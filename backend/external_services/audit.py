"""The audit engine.

`write_audit_row` is the single path by which rows enter the append-only audit log — including the
writes inside the resolve/ and expiry transactions — so no call site duplicates the write or the
console-mirroring. Toolkit-agnostic: it records opaque toolkit/action slugs and never branches on
them.

Every row names both identities involved: the agent that made the attempt and the user it was
acting for. Both are stored as plain snapshot columns rather than foreign keys, so a row keeps
meaning even after either identity is deleted.
"""

from django.db import transaction
from django.utils import timezone

from .models import AuditLog, AuditOutcome, TicketStatus

_RESET = "\x1b[0m"
_DIM = "\x1b[2m"
_BOLD = "\x1b[1m"

_OUTCOME_STYLE = {
    "executed": ("\x1b[32m", "✓"),            # green
    "denied": ("\x1b[31m", "✗"),              # red
    "pending_approval": ("\x1b[33m", "⏳"),    # yellow
    "rejected": ("\x1b[35m", "✗"),            # magenta
    "expired": ("\x1b[90m", "⌛"),             # gray
    "invalid_params": ("\x1b[36m", "⚠"),      # cyan
    "execution_failed": ("\x1b[31m", "✗"),    # red
    "permission_changed": ("\x1b[34m", "⚙"),  # blue
}

_legend_printed = False

# Params are recorded verbatim, which is exactly where a credential would end up: plugging in an
# MCP server means passing its bearer token as an action parameter. Values under these keys are
# masked on the way into the log. Matching on the key rather than sniffing the value keeps it
# predictable — nothing is redacted by accident, and nothing that *is* a secret slips through
# because it didn't look like one.
_SENSITIVE_KEYS = frozenset({
    "headers", "env", "authorization", "token", "access_token", "refresh_token",
    "api_key", "apikey", "secret", "client_secret", "password", "passwd", "credentials",
})
_REDACTED = "***redacted***"


def _redact(value, *, inside_sensitive: bool = False):
    """Recursively mask sensitive values. Once inside a sensitive key everything below it is masked,
    so `{"headers": {"Authorization": "Bearer ..."}}` hides the token rather than just the word."""
    if isinstance(value, dict):
        return {
            key: _redact(item, inside_sensitive=inside_sensitive or key.lower() in _SENSITIVE_KEYS)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item, inside_sensitive=inside_sensitive) for item in value]
    return _REDACTED if inside_sensitive else value


def _print_legend_once() -> None:
    global _legend_printed
    if _legend_printed:
        return
    _legend_printed = True
    print(
        f"{_DIM}audit log legend:{_RESET} \x1b[32m✓ EXECUTED{_DIM}  \x1b[31m✗ DENIED{_DIM}  "
        f"\x1b[33m⏳ PENDING_APPROVAL{_DIM}  \x1b[35m✗ REJECTED{_DIM}  \x1b[90m⌛ EXPIRED{_DIM}  "
        f"\x1b[36m⚠ INVALID_PARAMS{_DIM}  \x1b[31m✗ EXECUTION_FAILED{_DIM}  "
        f"\x1b[34m⚙ PERMISSION_CHANGED{_RESET}",
        flush=True,
    )


def _log_to_console(row: AuditLog) -> None:
    """Pretty-print a just-written row to the backend's own terminal — a purely additive display
    side effect; `audit_log` remains the source of truth."""
    _print_legend_once()
    color, icon = _OUTCOME_STYLE.get(row.outcome, ("", "•"))
    time_str = timezone.localtime(row.created_at).strftime("%H:%M:%S")
    who = f"{row.agent_name}{_DIM}/{row.user_name}{_RESET}"
    action = f"{row.toolkit_slug}.{row.action_slug}"
    outcome_label = row.outcome.upper().ljust(18)

    line = (
        f"{_DIM}[{time_str}]{_RESET} {_BOLD}{who}{_RESET} {_DIM}→{_RESET} "
        f"{action.ljust(24)} {color}{icon} {outcome_label}{_RESET}"
    )
    if row.ticket_id:
        line += f" {_DIM}ticket={str(row.ticket_id)[:8]}{_RESET}"
    if row.message:
        line += f" {_DIM}({row.message}){_RESET}"
    print(line, flush=True)


def write_audit_row(*, agent_id, agent_name, user_id, user_name, toolkit_slug, action_slug,
                    params, outcome, ticket_id=None, message=None) -> AuditLog:
    row = AuditLog.objects.create(
        agent_id=str(agent_id),
        agent_name=agent_name,
        user_id=str(user_id),
        user_name=user_name,
        toolkit_slug=toolkit_slug,
        action_slug=action_slug,
        params=_redact(params),
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
            user_id=ticket.requested_by_user_id,
            user_name=ticket.requested_by_user.username,
            toolkit_slug=ticket.action.toolkit.slug,
            action_slug=ticket.action.slug,
            params=ticket.params,
            outcome=AuditOutcome.EXPIRED,
            ticket_id=ticket.id,
            message="Ticket expired before resolution.",
        )
    return ticket
