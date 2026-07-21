"""The approval workflow: an agent polls its own ticket's status; a human reviewer resolves it.

`resolve/` uses admin HTTP Basic auth (not the agent JWT) — only a human, never the agent, can
approve or reject. On approval the action executes first, then the ticket and audit row are
written together in one transaction.
"""

from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import api_view, authentication_classes
from rest_framework.response import Response

from ..audit import resolve_expiry_if_needed, write_audit_row
from ..authentication import AdminBasicAuthentication
from ..toolkits import execute_action
from ..models import ApprovalTicket, AuditOutcome, TicketStatus


class _ResolveDecisionSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(choices=["approved", "rejected"])
    reason = serializers.CharField(required=False, allow_null=True, allow_blank=True)


def _get_ticket(ticket_id):
    return (
        ApprovalTicket.objects
        .select_related("action__toolkit", "agent")
        .filter(id=ticket_id)
        .first()
    )


@api_view(["GET"])
def approval_status(request, ticket_id):
    ticket = _get_ticket(ticket_id)
    if ticket is None:
        return Response({"error": "ticket_not_found", "message": "No such ticket."}, status=404)
    if ticket.agent_id != request.user.id:
        return Response({"error": "not_your_ticket", "message": "This ticket belongs to a different agent."}, status=403)

    ticket = resolve_expiry_if_needed(ticket)
    if ticket.status == TicketStatus.PENDING:
        return Response({"status": "pending"})
    if ticket.status == TicketStatus.APPROVED:
        return Response({"status": "approved", "result": ticket.result})
    if ticket.status == TicketStatus.REJECTED:
        return Response({"status": "rejected", "reason": ticket.rejection_reason})
    return Response({"status": "expired"})


@api_view(["PATCH"])
@authentication_classes([AdminBasicAuthentication])
def resolve_approval(request, ticket_id):
    ticket = _get_ticket(ticket_id)
    if ticket is None:
        return Response({"error": "ticket_not_found", "message": "No such ticket."}, status=404)

    ticket = resolve_expiry_if_needed(ticket)
    if ticket.status != TicketStatus.PENDING:
        return Response(
            {"status": ticket.status, "message": f"Ticket already {ticket.status}; cannot resolve again."},
            status=409,
        )

    body = _ResolveDecisionSerializer(data=request.data)
    if not body.is_valid():
        return Response(
            {"error": "invalid_body", "message": "decision must be 'approved' or 'rejected'; reason must be a string."},
            status=400,
        )
    decision = body.validated_data["decision"]
    reason = body.validated_data.get("reason")

    audit = dict(
        agent_id=ticket.agent_id,
        agent_name=ticket.agent.name,
        toolkit_slug=ticket.action.toolkit.slug,
        action_slug=ticket.action.slug,
        params=ticket.params,
        ticket_id=ticket.id,
    )

    if decision == "approved":
        result = execute_action(ticket.action.toolkit.slug, ticket.action.slug, ticket.params)
        with transaction.atomic():
            ticket.status = TicketStatus.APPROVED
            ticket.result = result
            ticket.resolved_at = timezone.now()
            ticket.save(update_fields=["status", "result", "resolved_at"])
            write_audit_row(**audit, outcome=AuditOutcome.EXECUTED, message="Approved and executed.")
        return Response({"status": "approved", "result": result})

    with transaction.atomic():
        ticket.status = TicketStatus.REJECTED
        ticket.rejection_reason = reason
        ticket.resolved_at = timezone.now()
        ticket.save(update_fields=["status", "rejection_reason", "resolved_at"])
        write_audit_row(**audit, outcome=AuditOutcome.REJECTED, message=reason)
    return Response({"status": "rejected", "reason": reason})
