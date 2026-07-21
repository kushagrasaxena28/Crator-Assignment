"""Catalog discovery (list toolkits, list actions, get a schema) and the call/ endpoint where the
whole pipeline converges: validate → resolve permission → deny / execute / queue → audit.
"""

from datetime import timedelta

from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response

from ..audit import write_audit_row
from ..toolkits import execute_action
from ..models import ApprovalTicket, AuditOutcome, PermissionValue, TicketStatus, Toolkit
from ..permissions import effective_permission_map, resolve_effective_permission
from ..validation import validate_params


def _lookup(toolkit_slug, action_slug):
    """Resolve (toolkit, action) by slug. Returns (None, None) if the toolkit is missing, or
    (toolkit, None) if the toolkit exists but the action doesn't — so callers can 404 precisely."""
    toolkit = Toolkit.objects.filter(slug=toolkit_slug).first()
    if toolkit is None:
        return None, None
    action = toolkit.actions.filter(slug=action_slug).first()
    return toolkit, action


@api_view(["GET"])
def list_toolkits(request):
    toolkits = Toolkit.objects.all()
    return Response({
        "toolkits": [{"slug": t.slug, "name": t.name, "description": t.description} for t in toolkits]
    })


@api_view(["GET"])
def list_actions(request, toolkit_slug):
    toolkit = Toolkit.objects.filter(slug=toolkit_slug).first()
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
    toolkit, action = _lookup(toolkit_slug, action_slug)
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
    toolkit, action = _lookup(toolkit_slug, action_slug)
    if toolkit is None:
        return Response({"error": "toolkit_not_found", "message": "No such toolkit."}, status=404)
    if action is None:
        return Response({"error": "action_not_found", "message": "No such action."}, status=404)

    agent = request.user
    params = request.data.get("params")
    if params is None:
        params = {}

    # Validate against the stored schema first — a malformed call is always 400, and is audited
    # like every other outcome, before any permission decision is reached.
    errors = validate_params(action.input_schema, params)
    if errors is not None:
        write_audit_row(agent_id=agent.id, agent_name=agent.name, toolkit_slug=toolkit.slug,
                        action_slug=action.slug, params=params, outcome=AuditOutcome.INVALID_PARAMS)
        return Response({"status": "invalid_params", "errors": errors}, status=400)

    permission = resolve_effective_permission(agent.id, action)
    audit = dict(agent_id=agent.id, agent_name=agent.name, toolkit_slug=toolkit.slug,
                 action_slug=action.slug, params=params)

    if permission == PermissionValue.ALWAYS_DENY:
        write_audit_row(**audit, outcome=AuditOutcome.DENIED)
        return Response({"status": "denied", "message": "This action is not permitted for this agent."}, status=403)

    if permission == PermissionValue.ALWAYS_ALLOW:
        result = execute_action(toolkit.slug, action.slug, params)  # runs first; no audit row if it throws
        write_audit_row(**audit, outcome=AuditOutcome.EXECUTED)
        return Response({"status": "executed", "result": result}, status=200)

    # requires_approval
    ticket = ApprovalTicket.objects.create(
        agent=agent, action=action, params=params, status=TicketStatus.PENDING,
        expires_at=timezone.now() + timedelta(hours=24),
    )
    write_audit_row(**audit, outcome=AuditOutcome.PENDING_APPROVAL, ticket_id=ticket.id)
    return Response(
        {"status": "pending_approval", "ticket_id": str(ticket.id), "message": "Awaiting human approval."},
        status=202,
    )
