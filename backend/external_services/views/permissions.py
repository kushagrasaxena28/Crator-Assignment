"""View and set an agent's per-action permission overrides.

Both methods share one URL; GET reads the sparse override set, PUT replaces it wholesale (an empty
list clears all overrides). Only overrides are stored — never a row per (agent, action) pair.

This endpoint is an administrative control, so it is guarded by the human reviewer's admin HTTP
Basic credentials (like resolve/) — NOT the agent JWT. Managing permissions with an agent's own
token would let an agent rewrite its own policy (e.g. flip its `delete_item` from `always_deny` to
`always_allow`), defeating the entire enforcement model. Only a human, never an agent, may change
who can do what.
"""

from django.db import transaction
from rest_framework import serializers
from rest_framework.decorators import api_view, authentication_classes
from rest_framework.response import Response

from ..authentication import AdminBasicAuthentication
from ..models import Action, PermissionOverride, PermissionValue, get_agent


class _OverrideItemSerializer(serializers.Serializer):
    action = serializers.CharField()
    permission = serializers.ChoiceField(choices=PermissionValue.choices)


class _OverridesBodySerializer(serializers.Serializer):
    overrides = _OverrideItemSerializer(many=True)


@api_view(["GET", "PUT"])
@authentication_classes([AdminBasicAuthentication])
def agent_permissions(request, agent_id):
    agent = get_agent(agent_id)
    if agent is None:
        return Response({"error": "agent_not_found", "message": "No such agent."}, status=404)

    if request.method == "GET":
        return _list_overrides(agent)
    return _replace_overrides(request, agent)


def _list_overrides(agent):
    overrides = PermissionOverride.objects.select_related("action").filter(agent=agent)
    return Response({
        "agent_id": str(agent.id),
        "overrides": [{"action": o.action.slug, "permission": o.permission} for o in overrides],
    })


def _replace_overrides(request, agent):
    body = _OverridesBodySerializer(data=request.data)
    if not body.is_valid():
        return Response(
            {"error": "invalid_body", "message": "overrides must be an array of {action, permission}."},
            status=400,
        )
    overrides = body.validated_data["overrides"]

    # Resolve action slugs up front so a bad slug 400s before any writes happen.
    actions_by_slug = {a.slug: a for a in Action.objects.filter(slug__in=[o["action"] for o in overrides])}
    unknown = [o["action"] for o in overrides if o["action"] not in actions_by_slug]
    if unknown:
        return Response(
            {"error": "unknown_action", "message": f"Unknown action slug(s): {', '.join(unknown)}"},
            status=400,
        )

    with transaction.atomic():
        PermissionOverride.objects.filter(agent=agent).delete()
        PermissionOverride.objects.bulk_create([
            PermissionOverride(agent=agent, action=actions_by_slug[o["action"]], permission=o["permission"])
            for o in overrides
        ])

    return Response({
        "agent_id": str(agent.id),
        "overrides": [{"action": o["action"], "permission": o["permission"]} for o in overrides],
    })
