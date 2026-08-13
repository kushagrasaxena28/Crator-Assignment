"""DRF authentication classes — one per identity, plus one that serves either.

`AgentJWTAuthentication` guards the agent-facing endpoints (the catalog and calling actions);
`UserJWTAuthentication` guards the human-only controls (resolving a ticket, setting permissions).
Each accepts only its own token type, so an agent token presented to an approval endpoint is a
401 rather than a privilege escalation.

All of them always raise on failure and never return `None`. That distinction is load-bearing in
DRF: returning `None` means "not my credential type, try the next authenticator", and if every
authenticator returns `None` the request reaches the view unauthenticated. Raising fails closed.
They also define `authenticate_header`, without which DRF downgrades the 401 to a 403.

That fail-closed behaviour is also why `AgentOrUserJWTAuthentication` exists as a single class
rather than as a list of two: DRF re-raises the first authenticator's failure instead of moving on
to the next, so two raising classes can never be chained.
"""

import jwt
from rest_framework import authentication, exceptions

from .models import get_agent, get_user
from .tokens import TOKEN_TYPE_AGENT, TOKEN_TYPE_USER, decode_any_token, decode_token


def _bearer_token(request) -> str:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise exceptions.AuthenticationFailed(
            "Authorization: Bearer <jwt> header is required.", code="missing_token"
        )
    return header[len("Bearer "):]


# Deliberately one message for every failure mode — bad signature, expiry, and wrong token type all
# look identical from outside, so a caller can't probe for which one they hit.
def _invalid_token() -> exceptions.AuthenticationFailed:
    return exceptions.AuthenticationFailed(
        "Token is malformed, expired, of the wrong type, or has a bad signature.",
        code="invalid_token",
    )


def _decode(token: str, expected_type: str) -> dict:
    try:
        return decode_token(token, expected_type=expected_type)
    except jwt.PyJWTError:
        raise _invalid_token()


def _load_agent(claims: dict):
    """Resolve an agent from verified claims, checking that it — and the user it acts for — are
    still active. Both checks run on every request, so disabling either takes effect immediately,
    even while a previously-issued token is still within its lifetime."""
    agent_id = claims.get("agent_id")
    if not agent_id:
        raise exceptions.AuthenticationFailed("Token payload is missing agent_id.", code="invalid_token")

    agent = get_agent(agent_id)
    if agent is None or not agent.is_active:
        raise exceptions.AuthenticationFailed(
            "Agent no longer exists or is inactive.", code="agent_inactive_or_missing"
        )
    if not agent.owner.is_active:
        raise exceptions.AuthenticationFailed(
            "The user this agent acts for is inactive.", code="owner_inactive"
        )
    return agent


def _load_user(claims: dict):
    user_id = claims.get("user_id")
    if not user_id:
        raise exceptions.AuthenticationFailed("Token payload is missing user_id.", code="invalid_token")

    user = get_user(user_id)
    if user is None or not user.is_active:
        raise exceptions.AuthenticationFailed(
            "User no longer exists or is inactive.", code="user_inactive_or_missing"
        )
    return user


class AgentJWTAuthentication(authentication.BaseAuthentication):
    """Accepts only an agent token. Guards the catalog and the call/ endpoint."""

    def authenticate(self, request):
        token = _bearer_token(request)
        claims = _decode(token, TOKEN_TYPE_AGENT)
        return (_load_agent(claims), claims)

    def authenticate_header(self, request):
        return "Bearer"


class UserJWTAuthentication(authentication.BaseAuthentication):
    """Accepts only a user token. This is the credential behind every control an agent must never
    hold: approving or rejecting a held action, and changing an agent's permissions."""

    def authenticate(self, request):
        token = _bearer_token(request)
        claims = _decode(token, TOKEN_TYPE_USER)
        return (_load_user(claims), claims)

    def authenticate_header(self, request):
        return "Bearer"


class AgentOrUserJWTAuthentication(authentication.BaseAuthentication):
    """Accepts either token type, for endpoints that serve both but answer differently depending on
    who is asking (the audit log). The view is responsible for scoping its results to whichever
    identity `request.user` turns out to be."""

    def authenticate(self, request):
        token = _bearer_token(request)
        try:
            claims = decode_any_token(token)
        except jwt.PyJWTError:
            raise _invalid_token()

        token_type = claims.get("typ")
        if token_type == TOKEN_TYPE_AGENT:
            return (_load_agent(claims), claims)
        if token_type == TOKEN_TYPE_USER:
            return (_load_user(claims), claims)
        raise _invalid_token()

    def authenticate_header(self, request):
        return "Bearer"
