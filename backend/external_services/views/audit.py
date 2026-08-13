"""Query the audit log, newest first.

Readable with either token, but never unscoped: an agent sees only its own attempts, and a user
sees everything done by every agent they own. The log records `params` verbatim, which is exactly
where secrets and personal data end up, so serving one caller another's rows would leak far more
than a list of action names.
"""

from rest_framework.decorators import api_view, authentication_classes
from rest_framework.response import Response

from ..authentication import AgentOrUserJWTAuthentication
from ..models import Agent, AuditLog

# Without a cap this returns the entire table in one response, which stops being viable the moment
# the log is any real size.
_DEFAULT_LIMIT = 100
_MAX_LIMIT = 500


def _int_param(request, name, default):
    raw = request.query_params.get(name)
    if raw is None or raw == "":
        return default, None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None, f"{name} must be an integer."
    if value < 0:
        return None, f"{name} must not be negative."
    return value, None


@api_view(["GET"])
@authentication_classes([AgentOrUserJWTAuthentication])
def query_audit(request):
    caller = request.user
    filters = {}

    # The scope is set from the authenticated identity, never from a query parameter, so there is
    # no combination of parameters that widens it.
    if isinstance(caller, Agent):
        filters["agent_id"] = str(caller.id)
    else:
        filters["user_id"] = str(caller.id)
        # A user may narrow to one of their own agents. Matched case-insensitively because a UUID
        # written in uppercase is the same id, and an exact match would silently return nothing.
        if request.query_params.get("agent_id"):
            filters["agent_id__iexact"] = request.query_params["agent_id"]

    if request.query_params.get("toolkit"):
        filters["toolkit_slug"] = request.query_params["toolkit"]
    if request.query_params.get("outcome"):
        filters["outcome"] = request.query_params["outcome"]

    limit, error = _int_param(request, "limit", _DEFAULT_LIMIT)
    if error:
        return Response({"error": "invalid_request", "message": error}, status=400)
    offset, error = _int_param(request, "offset", 0)
    if error:
        return Response({"error": "invalid_request", "message": error}, status=400)
    limit = min(limit, _MAX_LIMIT)

    query = AuditLog.objects.filter(**filters).order_by("-created_at")
    total = query.count()
    logs = query[offset:offset + limit]

    return Response({
        "total": total,
        "limit": limit,
        "offset": offset,
        "logs": [
            {
                "id": str(log.id),
                "agent_id": log.agent_id,
                "agent_name": log.agent_name,
                "user_id": log.user_id,
                "user_name": log.user_name,
                "toolkit": log.toolkit_slug,
                "action": log.action_slug,
                "params": log.params,
                "outcome": log.outcome,
                "ticket_id": log.ticket_id,
                "message": log.message,
                "created_at": log.created_at.isoformat(),
            }
            for log in logs
        ],
    })
