"""Catalog discovery (list toolkits, list actions, get a schema) and the call/ endpoint where the
whole pipeline converges: validate → resolve permission → deny / execute / queue → audit.
"""

from datetime import timedelta

from django.db.models import Q
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response

from ..audit import write_audit_row
from ..models import ApprovalTicket, AuditOutcome, PermissionValue, TicketStatus, Toolkit
from ..permissions import effective_permission_map, resolve_effective_permission
from ..toolkits import ExecutionContext, execute_action
from ..toolkits.errors import ExecutorError, InvalidRequest
from ..validation import validate_params


def _visible_toolkits(agent):
    """The toolkits this agent may see and call: the built-in ones, plus the enabled MCP servers
    belonging to the user it acts for.

    Every catalog and call path goes through here. Without it an agent could list — and invoke —
    another user's registered servers, which would mean using someone else's credentials against
    someone else's account. A disabled server drops out of this queryset, which is what makes
    `disable_server` a real unplug rather than a cosmetic flag."""
    return Toolkit.objects.filter(
        Q(mcp_server__isnull=True)
        | Q(mcp_server__owner=agent.owner_id, mcp_server__is_enabled=True)
    )


def _lookup(agent, toolkit_slug, action_slug):
    """Resolve (toolkit, action) by slug within what this agent can see. Returns (None, None) if the
    toolkit is missing, or (toolkit, None) if the toolkit exists but the action doesn't — so callers
    can 404 precisely. Another user's toolkit reads as "not found", never "forbidden"."""
    toolkit = _visible_toolkits(agent).filter(slug=toolkit_slug).first()
    if toolkit is None:
        return None, None
    action = toolkit.actions.filter(slug=action_slug).first()
    return toolkit, action


@api_view(["GET"])
def list_toolkits(request):
    toolkits = _visible_toolkits(request.user).select_related("mcp_server")
    return Response({
        "toolkits": [
            {
                "slug": t.slug,
                "name": t.name,
                "description": t.description,
                # Tells the agent (and the user) which toolkits came from their own plugged-in
                # servers versus which are built in.
                "source": "mcp" if t.mcp_server_id else "builtin",
            }
            for t in toolkits
        ]
    })


@api_view(["GET"])
def list_actions(request, toolkit_slug):
    toolkit = _visible_toolkits(request.user).filter(slug=toolkit_slug).first()
    if toolkit is None:
        return Response({"error": "toolkit_not_found", "message": "No such toolkit."}, status=404)

    actions = list(toolkit.actions.all())
    permissions = effective_permission_map(request.user.id, actions)
    return Response({
        "toolkit": toolkit.slug,
        "actions": [
            {"slug": a.slug, "name": a.name, "description": a.description, "permission": permissions[a.id]}
            for a in actions
        ],
    })


@api_view(["GET"])
def action_schema(request, toolkit_slug, action_slug):
    toolkit, action = _lookup(request.user, toolkit_slug, action_slug)
    if toolkit is None:
        return Response({"error": "toolkit_not_found", "message": "No such toolkit."}, status=404)
    if action is None:
        return Response({"error": "action_not_found", "message": "No such action."}, status=404)

    return Response({
        "toolkit": toolkit.slug,
        "action": action.slug,
        "input_schema": action.input_schema,
        "output_schema": action.output_schema,
    })


@api_view(["POST"])
def call_action(request, toolkit_slug, action_slug):
    toolkit, action = _lookup(request.user, toolkit_slug, action_slug)
    if toolkit is None:
        return Response({"error": "toolkit_not_found", "message": "No such toolkit."}, status=404)
    if action is None:
        return Response({"error": "action_not_found", "message": "No such action."}, status=404)

    # A JSON body that isn't an object (an array, a bare string) has no `params` to read, and
    # reaching for one would raise rather than producing a clean 400.
    if not isinstance(request.data, dict):
        return Response(
            {"error": "invalid_request", "message": "Body must be a JSON object with a `params` object."},
            status=400,
        )

    agent = request.user
    params = request.data.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return Response(
            {"error": "invalid_request", "message": "`params` must be a JSON object."}, status=400
        )

    # Every audit row names both the agent and the user it acts for. The owner is read from the
    # database rather than the token, so it always reflects current ownership.
    owner = agent.owner
    audit = dict(agent_id=agent.id, agent_name=agent.name, user_id=owner.id, user_name=owner.username,
                 toolkit_slug=toolkit.slug, action_slug=action.slug, params=params)

    # Validate against the stored schema first — a malformed call is always 400, and is audited
    # like every other outcome, before any permission decision is reached.
    errors = validate_params(action.input_schema, params)
    if errors is not None:
        write_audit_row(**audit, outcome=AuditOutcome.INVALID_PARAMS)
        return Response({"status": "invalid_params", "errors": errors}, status=400)

    permission = resolve_effective_permission(agent.id, action)

    if permission == PermissionValue.ALWAYS_DENY:
        write_audit_row(**audit, outcome=AuditOutcome.DENIED)
        return Response({"status": "denied", "message": "This action is not permitted for this agent."}, status=403)

    if permission == PermissionValue.ALWAYS_ALLOW:
        # The effect runs before the audit row is written so a throwing effect never leaves an
        # `executed` row behind — but a failure is still an outcome worth recording, because an
        # attempt that reached an external service is exactly the one you want to find later.
        try:
            result = execute_action(action, params, ExecutionContext(agent=agent, user=owner))
        except InvalidRequest as err:
            # The caller got something wrong and could fix it on a retry — say what, so the agent
            # can relay it instead of reporting an opaque failure.
            write_audit_row(**audit, outcome=AuditOutcome.EXECUTION_FAILED, message=str(err))
            return Response({"status": "invalid_request", "message": str(err)}, status=400)
        except ExecutorError as err:
            # A known failure mode of the external service (unreachable, refused, protocol error).
            # Safe to surface verbatim, and the agent needs it to explain itself.
            write_audit_row(**audit, outcome=AuditOutcome.EXECUTION_FAILED, message=str(err))
            return Response({"status": "execution_failed", "message": str(err)}, status=502)
        except Exception as err:  # noqa: BLE001 — anything unexpected is still an auditable outcome
            # Genuinely unexpected: record the detail, but don't hand internals to the caller.
            write_audit_row(**audit, outcome=AuditOutcome.EXECUTION_FAILED, message=str(err))
            return Response(
                {"status": "execution_failed", "message": "The action was permitted but failed to execute."},
                status=502,
            )
        write_audit_row(**audit, outcome=AuditOutcome.EXECUTED)
        return Response({"status": "executed", "result": result}, status=200)

    # requires_approval
    ticket = ApprovalTicket.objects.create(
        agent=agent, requested_by_user=owner, action=action, params=params,
        status=TicketStatus.PENDING, expires_at=timezone.now() + timedelta(hours=24),
    )
    write_audit_row(**audit, outcome=AuditOutcome.PENDING_APPROVAL, ticket_id=ticket.id)
    return Response(
        {"status": "pending_approval", "ticket_id": str(ticket.id), "message": "Awaiting human approval."},
        status=202,
    )
