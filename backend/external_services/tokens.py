"""JWT signing and verification (PyJWT). The signing secret lives only here, on the backend."""

from datetime import timedelta

import jwt
from django.conf import settings
from django.utils import timezone

_ALGORITHM = "HS256"


def _secret() -> str:
    if not settings.JWT_SECRET:
        raise RuntimeError("JWT_SECRET is not set — cannot sign or verify tokens. See .env.example.")
    return settings.JWT_SECRET


def issue_token(agent) -> tuple[str, str]:
    """Sign a short-lived JWT carrying the agent's id and name. Returns (token, expires_at_iso)."""
    expires_at = timezone.now() + timedelta(minutes=settings.JWT_EXPIRES_IN_MINUTES)
    payload = {"agent_id": str(agent.id), "agent_name": agent.name, "exp": expires_at}
    token = jwt.encode(payload, _secret(), algorithm=_ALGORITHM)
    return token, expires_at.isoformat()


def decode_token(token: str) -> dict:
    """Verify signature + expiry and return the claims. Raises jwt.PyJWTError on any problem."""
    return jwt.decode(token, _secret(), algorithms=[_ALGORITHM])
