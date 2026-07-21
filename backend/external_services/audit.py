"""The audit engine.

`write_audit_row` is the single path by which rows enter the append-only audit log — including the
writes inside the resolve/ and expiry transactions — so no call site duplicates the write or the
console-mirroring. Toolkit-agnostic: it records opaque toolkit/action slugs and never branches on
them.
"""

from django.db import transaction
from django.utils import timezone

from .models import AuditLog, AuditOutcome, TicketStatus

_RESET = "\x1b[0m"
_DIM = "\x1b[2m"
_BOLD = "\x1b[1m"

_OUTCOME_STYLE = {
    "executed": ("\x1b[32m", "✓"),          # green
    "denied": ("\x1b[31m", "✗"),            # red
    "pending_approval": ("\x1b[33m", "⏳"),  # yellow
    "rejected": ("\x1b[35m", "✗"),          # magenta
    "expired": ("\x1b[90m", "⌛"),           # gray
    "invalid_params": ("\x1b[36m", "⚠"),    # cyan
}

_legend_printed = False


def _print_legend_once() -> None:
    global _legend_printed
    if _legend_printed:
        return
    _legend_printed = True
    print(
        f"{_DIM}audit log legend:{_RESET} \x1b[32m✓ EXECUTED{_DIM}  \x1b[31m✗ DENIED{_DIM}  "
        f"\x1b[33m⏳ PENDING_APPROVAL{_DIM}  \x1b[35m✗ REJECTED{_DIM}  \x1b[90m⌛ EXPIRED{_DIM}  "
        f"\x1b[36m⚠ INVALID_PARAMS{_RESET}",
        flush=True,
    )


def _log_to_console(row: AuditLog) -> None:
    """Pretty-print a just-written row to the backend's own terminal — a purely additive display
    side effect; `audit_log` remains the source of truth."""
    _print_legend_once()
    color, icon = _OUTCOME_STYLE.get(row.outcome, ("", "•"))
    time_str = timezone.localtime(row.created_at).strftime("%H:%M:%S")
    action = f"{row.toolkit_slug}.{row.action_slug}"
    outcome_label = row.outcome.upper().ljust(16)

    line = (
        f"{_DIM}[{time_str}]{_RESET} {_BOLD}{row.agent_name}{_RESET} {_DIM}→{_RESET} "
        f"{action.ljust(24)} {color}{icon} {outcome_label}{_RESET}"
    )
    if row.ticket_id:
        line += f" {_DIM}ticket={str(row.ticket_id)[:8]}{_RESET}"
    if row.message:
        line += f" {_DIM}({row.message}){_RESET}"
    print(line, flush=True)


def write_audit_row(*, agent_id, agent_name, toolkit_slug, action_slug, params, outcome,
                    ticket_id=None, message=None) -> AuditLog:
    row = AuditLog.objects.create(
        agent_id=str(agent_id),
        agent_name=agent_name,
        toolkit_slug=toolkit_slug,
        action_slug=action_slug,
        params=params,
        outcome=outcome,
        ticket_id=str(ticket_id) if ticket_id is not None else None,
        message=message,
    )
    _log_to_console(row)
    return row


def resolve_expiry_if_needed(ticket):
    """Flip a still-pending ticket to `expired` if its deadline has passed, appending an audit row.
    Idempotent — a no-op once the ticket is no longer pending. Shared by the status/ and resolve/
    endpoints so the expiry rule can't drift between them."""
    if ticket.status != TicketStatus.PENDING or timezone.now() <= ticket.expires_at:
        return ticket

    with transaction.atomic():
        ticket.status = TicketStatus.EXPIRED
        ticket.save(update_fields=["status"])
        write_audit_row(
            agent_id=ticket.agent_id,
            agent_name=ticket.agent.name,
            toolkit_slug=ticket.action.toolkit.slug,
            action_slug=ticket.action.slug,
            params=ticket.params,
            outcome=AuditOutcome.EXPIRED,
            ticket_id=ticket.id,
            message="Ticket expired before resolution.",
        )
    return ticket
