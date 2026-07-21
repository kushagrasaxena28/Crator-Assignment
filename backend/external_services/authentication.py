"""DRF authentication classes — the idiomatic Django home for what Express did in middleware.

`AgentJWTAuthentication` guards the agent-facing endpoints; `AdminBasicAuthentication` guards the
human reviewer's resolve/ endpoint. Both always raise on failure (never return None) so a missing
credential is a clean 401, and both define `authenticate_header` so DRF returns 401 rather than 403.
"""

import base64

import jwt
from django.conf import settings
from rest_framework import authentication, exceptions

from .models import get_agent
from .tokens import decode_token


class AgentJWTAuthentication(authentication.BaseAuthentication):
    """Verify the Bearer JWT (signature + expiry) and that the agent still exists and is active.
    The active check runs on every request, so an agent disabled mid-session is rejected
    immediately even with an otherwise-valid token."""

    def authenticate(self, request):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            raise exceptions.AuthenticationFailed(
                "Authorization: Bearer <jwt> header is required.", code="missing_token"
            )
        token = header[len("Bearer "):]

        try:
            payload = decode_token(token)
        except jwt.PyJWTError:
            raise exceptions.AuthenticationFailed(
                "Token is malformed, expired, or has a bad signature.", code="invalid_token"
            )

        agent_id = payload.get("agent_id")
        if not agent_id:
            raise exceptions.AuthenticationFailed(
                "Token payload is missing agent_id.", code="invalid_token"
            )

        agent = get_agent(agent_id)
        if agent is None or not agent.is_active:
            raise exceptions.AuthenticationFailed(
                "Agent no longer exists or is inactive.", code="agent_inactive_or_missing"
            )
        return (agent, token)

    def authenticate_header(self, request):
        return "Bearer"


class AdminBasicAuthentication(authentication.BaseAuthentication):
    """HTTP Basic Auth against ADMIN_USER / ADMIN_PASSWORD. Callable directly with
    `curl -u admin:password`, no session cookie needed. The resolve/ view never reads
    `request.user` — the admin's authority is the HTTP Basic credential itself, not an
    agent identity — so the authenticated principal is just the string "admin"."""

    def authenticate(self, request):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            raise exceptions.AuthenticationFailed("HTTP Basic Auth required.", code="missing_credentials")

        try:
            decoded = base64.b64decode(header[len("Basic "):]).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            raise exceptions.AuthenticationFailed("Bad admin username or password.", code="invalid_credentials")

        username, _, password = decoded.partition(":")
        if username != settings.ADMIN_USER or password != settings.ADMIN_PASSWORD:
            raise exceptions.AuthenticationFailed("Bad admin username or password.", code="invalid_credentials")

        return ("admin", None)

    def authenticate_header(self, request):
        return "Basic"
