"""Token issuance — the endpoint GenerateJWT calls to obtain a short-lived agent token."""

from rest_framework.decorators import api_view, authentication_classes
from rest_framework.response import Response

from ..models import get_agent
from ..tokens import issue_token


@api_view(["POST"])
@authentication_classes([])  # unauthenticated to call
def issue_agent_token(request):
    """POST /api/auth/token/ — issues a token only for an agent that already exists and is active.
    The signing secret never leaves the backend."""
    agent_id = request.data.get("agent_id")
    if not isinstance(agent_id, str) or not agent_id:
        return Response({"error": "invalid_request", "message": "agent_id is required."}, status=400)

    agent = get_agent(agent_id)
    if agent is None:
        return Response({"error": "agent_not_found", "message": "No agent with that id."}, status=404)
    if not agent.is_active:
        return Response({"error": "agent_inactive", "message": "Agent is disabled."}, status=403)

    token, expires_at = issue_token(agent)
    return Response({"token": token, "expires_at": expires_at})
