"""Query the audit log, newest first. All three filters (agent_id, toolkit, outcome) are optional."""

from rest_framework.decorators import api_view
from rest_framework.response import Response

from ..models import AuditLog


@api_view(["GET"])
def query_audit(request):
    filters = {}
    if request.query_params.get("agent_id"):
        filters["agent_id"] = request.query_params["agent_id"]
    if request.query_params.get("toolkit"):
        filters["toolkit_slug"] = request.query_params["toolkit"]
    if request.query_params.get("outcome"):
        filters["outcome"] = request.query_params["outcome"]

    logs = AuditLog.objects.filter(**filters).order_by("-created_at")
    return Response({"logs": [
        {
            "id": str(log.id),
            "agent_id": log.agent_id,
            "agent_name": log.agent_name,
            "toolkit": log.toolkit_slug,
            "action": log.action_slug,
            "params": log.params,
            "outcome": log.outcome,
            "ticket_id": log.ticket_id,
            "message": log.message,
            "created_at": log.created_at.isoformat(),
        }
        for log in logs
    ]})
