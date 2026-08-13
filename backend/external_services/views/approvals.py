"""The approval workflow: an agent polls its own ticket's status; the user it acts for resolves it.

`resolve/` authenticates with a **user** token, never an agent token — only a human, never the
agent, can approve or reject. The agent's system prompt also tells it not to try, but a prompt is
advisory; the authentication class is the actual control.
"""

from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import api_view, authentication_classes
from rest_framework.response import Response

from ..audit import resolve_expiry_if_needed, write_audit_row
from ..authentication import UserJWTAuthentication
from ..models import ApprovalTicket, AuditOutcome, PermissionValue, TicketStatus
from ..permissions import resolve_effective_permission
from ..toolkits import ExecutionContext, execute_action
from ..toolkits.errors import ExecutorError


class _ResolveDecisionSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(choices=["approved", "rejected"])
    reason = serializers.CharField(required=False, allow_null=True, allow_blank=True)


_RELATED = ("action__toolkit", "agent", "requested_by_user")


def _audit_fields(ticket) -> dict:
    return dict(
        agent_id=ticket.agent_id,
        agent_name=ticket.agent.name,
        user_id=ticket.requested_by_user_id,
        user_name=ticket.requested_by_user.username,
        toolkit_slug=ticket.action.toolkit.slug,
        action_slug=ticket.action.slug,
        params=ticket.params,
        ticket_id=ticket.id,
    )


@api_view(["GET"])
def approval_status(request, ticket_id):
    """Polled by the agent that created the ticket, so it can report the decision back to the user."""
    ticket = ApprovalTicket.objects.select_related(*_RELATED).filter(id=ticket_id).first()
    if ticket is None:
        return Response({"error": "ticket_not_found", "message": "No such ticket."}, status=404)
    if ticket.agent_id != request.user.id:
        return Response(
            {"error": "not_your_ticket", "message": "This ticket belongs to a different agent."}, status=403
        )

    ticket = resolve_expiry_if_needed(ticket)
    if ticket.status == TicketStatus.PENDING:
        return Response({"status": "pending"})
    if ticket.status == TicketStatus.APPROVED:
        return Response({"status": "approved", "result": ticket.result})
    if ticket.status == TicketStatus.REJECTED:
        return Response({"status": "rejected", "reason": ticket.rejection_reason})
    return Response({"status": "expired"})


@api_view(["PATCH"])
@authentication_classes([UserJWTAuthentication])
def resolve_approval(request, ticket_id):
    """Approve or reject a held action. Requires the token of the user the calling agent was
    acting for when the ticket was created."""
    body = _ResolveDecisionSerializer(data=request.data if isinstance(request.data, dict) else {})
    if not body.is_valid():
        return Response(
            {"error": "invalid_body", "message": "decision must be 'approved' or 'rejected'; reason must be a string."},
            status=400,
        )
    decision = body.validated_data["decision"]
    reason = body.validated_data.get("reason")

    # Everything from re-reading the ticket to writing its outcome happens under a row lock. Without
    # it this is a check-then-act race: several concurrent approvals of one ticket each read
    # `pending` and each execute the action, so a single human decision could fire the effect N
    # times — the worst possible failure for a system whose purpose is "a human signs off once".
    # `select_for_update` is a genuine row lock on Postgres; on SQLite it is a no-op and the
    # serialization comes instead from SQLite's database-wide write lock held by this transaction.
    with transaction.atomic():
        ticket = (
            ApprovalTicket.objects
            .select_for_update()
            .select_related(*_RELATED)
            .filter(id=ticket_id)
            .first()
        )
        if ticket is None:
            return Response({"error": "ticket_not_found", "message": "No such ticket."}, status=404)
        if ticket.requested_by_user_id != request.user.id:
            return Response(
                {"error": "not_your_ticket",
                 "message": "This ticket was raised for a different user."},
                status=403,
            )

        ticket = resolve_expiry_if_needed(ticket)
        if ticket.status != TicketStatus.PENDING:
            return Response(
                {"status": ticket.status, "message": f"Ticket already {ticket.status}; cannot resolve again."},
                status=409,
            )

        audit = _audit_fields(ticket)

        # Permission was resolved when the call was made, but a ticket can sit here for up to 24
        # hours and policy may have changed since. Re-resolve now, so revoking a permission also
        # stops the approvals already in flight rather than only the calls that come after it.
        if resolve_effective_permission(ticket.agent_id, ticket.action) == PermissionValue.ALWAYS_DENY:
            ticket.status = TicketStatus.REJECTED
            ticket.rejection_reason = "Permission was revoked while this ticket was pending."
            ticket.resolved_at = timezone.now()
            ticket.save(update_fields=["status", "rejection_reason", "resolved_at"])
            write_audit_row(**audit, outcome=AuditOutcome.DENIED,
                            message="Permission revoked while pending; not executed.")
            return Response(
                {"status": "denied",
                 "message": "This action is no longer permitted for this agent; the ticket was closed."},
                status=403,
            )

        if decision == "rejected":
            ticket.status = TicketStatus.REJECTED
            ticket.rejection_reason = reason
            ticket.resolved_at = timezone.now()
            ticket.save(update_fields=["status", "rejection_reason", "resolved_at"])
            write_audit_row(**audit, outcome=AuditOutcome.REJECTED, message=reason)
            return Response({"status": "rejected", "reason": reason})

        # Approved. The effect runs before the ticket is marked resolved, so a throwing effect
        # never leaves behind an `executed` row for something that did not happen.
        try:
            result = execute_action(
                ticket.action,
                ticket.params,
                ExecutionContext(agent=ticket.agent, user=ticket.requested_by_user),
            )
        except Exception as err:  # noqa: BLE001 — any executor failure is an auditable outcome
            # The ticket stays pending on purpose: the human's decision still stands, and the
            # action can be retried once whatever failed is fixed. A known failure mode of the
            # external service explains itself; anything unexpected keeps its detail in the log.
            write_audit_row(**audit, outcome=AuditOutcome.EXECUTION_FAILED, message=str(err))
            detail = str(err) if isinstance(err, ExecutorError) else "the action failed to execute"
            return Response(
                {"status": "execution_failed",
                 "message": f"Approved, but {detail}. The ticket is still pending."},
                status=502,
            )

        # Note the gap that cannot be closed with a transaction: the effect above may have already
        # happened in an external system, and if this commit fails the ticket stays pending and a
        # retry would run it twice. Closing that properly needs an idempotency key on the outbound
        # call or a transactional outbox — neither is worth it at this scale, but it is real.
        ticket.status = TicketStatus.APPROVED
        ticket.result = result
        ticket.resolved_at = timezone.now()
        ticket.save(update_fields=["status", "result", "resolved_at"])
        write_audit_row(**audit, outcome=AuditOutcome.EXECUTED, message="Approved and executed.")
        return Response({"status": "approved", "result": result})
