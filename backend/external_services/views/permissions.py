"""View and set an agent's per-action permission overrides.

Both methods share one URL; GET reads the sparse override set, PUT replaces it wholesale (an empty
list clears all overrides). Only overrides are stored — never a row per (agent, action) pair.

This is a human-only control, guarded by a **user** token and scoped to agents that user owns. An
agent must never be able to rewrite its own policy — flipping its own `delete_item` from
`always_deny` to `always_allow` would defeat the entire enforcement model — so an agent token is
not accepted here at all. The owning human, on the other hand, is exactly who should decide.

Actions are addressed as `"<toolkit>.<action>"`, never by a bare action slug: `Action.slug` is
unique only *per toolkit*, so a bare slug is ambiguous the moment two toolkits expose the same
action name, and a lookup on it can silently write the override to the wrong toolkit's action.
"""

from django.db import transaction
from django.db.models import Q
from rest_framework import serializers
from rest_framework.decorators import api_view, authentication_classes
from rest_framework.response import Response

from ..audit import write_audit_row
from ..authentication import UserJWTAuthentication
from ..models import Action, Agent, AuditOutcome, PermissionOverride, PermissionValue


class _OverrideItemSerializer(serializers.Serializer):
    """One override. The action may be given either fully qualified as `"items.read_item"`, or
    split across `toolkit` and `action` keys — both resolve to the same (toolkit, action) pair."""

    action = serializers.CharField()
    toolkit = serializers.CharField(required=False, allow_blank=False)
    permission = serializers.ChoiceField(choices=PermissionValue.choices)


class _OverridesBodySerializer(serializers.Serializer):
    overrides = _OverrideItemSerializer(many=True)


def _qualified_pair(item) -> tuple[str | None, str]:
    """Normalize one override entry to (toolkit_slug, action_slug). Returns a None toolkit when the
    entry is unqualified, which the caller rejects."""
    action = item["action"]
    toolkit = item.get("toolkit")
    if toolkit:
        return toolkit, action
    if "." in action:
        toolkit_slug, _, action_slug = action.partition(".")
        return (toolkit_slug or None), action_slug
    return None, action


@api_view(["GET", "PUT"])
@authentication_classes([UserJWTAuthentication])
def agent_permissions(request, agent_id):
    # Scoped to this user's agents. Another user's agent reads as "not found" rather than
    # "forbidden", so agent ids can't be probed for existence from outside their owner.
    agent = Agent.objects.filter(id=agent_id, owner=request.user).first()
    if agent is None:
        return Response({"error": "agent_not_found", "message": "No such agent."}, status=404)

    if request.method == "GET":
        return _list_overrides(agent)
    return _replace_overrides(request, agent)


def _list_overrides(agent):
    overrides = PermissionOverride.objects.select_related("action__toolkit").filter(agent=agent)
    return Response({
        "agent_id": str(agent.id),
        "overrides": [
            {"action": f"{o.action.toolkit.slug}.{o.action.slug}", "permission": o.permission}
            for o in overrides
        ],
    })


def _replace_overrides(request, agent):
    body = _OverridesBodySerializer(data=request.data if isinstance(request.data, dict) else {})
    if not body.is_valid():
        return Response(
            {"error": "invalid_body",
             "message": 'overrides must be an array of {action, permission}, where action is '
                        '"<toolkit>.<action>" (or paired with a separate "toolkit" key).'},
            status=400,
        )
    overrides = body.validated_data["overrides"]

    pairs = [_qualified_pair(item) for item in overrides]
    unqualified = [item["action"] for item, (toolkit, _) in zip(overrides, pairs) if toolkit is None]
    if unqualified:
        return Response(
            {"error": "unqualified_action",
             "message": f'Action(s) must be qualified as "<toolkit>.<action>": {", ".join(unqualified)}'},
            status=400,
        )

    # Resolve every (toolkit, action) pair in one query so a bad reference 400s before any writes.
    # The empty case is guarded because an empty Q() matches every row.
    actions_by_pair = {}
    if pairs:
        lookup = Q()
        for toolkit_slug, action_slug in pairs:
            lookup |= Q(toolkit__slug=toolkit_slug, slug=action_slug)
        actions_by_pair = {
            (a.toolkit.slug, a.slug): a
            for a in Action.objects.select_related("toolkit").filter(lookup)
        }

    unknown = [f"{tk}.{act}" for tk, act in pairs if (tk, act) not in actions_by_pair]
    if unknown:
        return Response(
            {"error": "unknown_action", "message": f"Unknown action(s): {', '.join(unknown)}"},
            status=400,
        )

    resolved = [{"action": f"{tk}.{act}", "permission": item["permission"]}
                for item, (tk, act) in zip(overrides, pairs)]

    with transaction.atomic():
        PermissionOverride.objects.filter(agent=agent).delete()
        PermissionOverride.objects.bulk_create([
            PermissionOverride(agent=agent, action=actions_by_pair[pair], permission=item["permission"])
            for item, pair in zip(overrides, pairs)
        ])
        # A policy change is as much a part of "what happened and who allowed it" as an action
        # attempt, so it goes in the same append-only log rather than a separate table.
        write_audit_row(
            agent_id=agent.id,
            agent_name=agent.name,
            user_id=request.user.id,
            user_name=request.user.username,
            toolkit_slug="system",
            action_slug="set_permissions",
            params={"overrides": resolved},
            outcome=AuditOutcome.PERMISSION_CHANGED,
            message=f"{len(resolved)} override(s) set by {request.user.username}.",
        )

    return Response({"agent_id": str(agent.id), "overrides": resolved})
