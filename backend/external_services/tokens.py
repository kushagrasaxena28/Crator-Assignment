"""JWT signing and verification (PyJWT). The signing secret lives only here, on the backend.

This system issues two kinds of token, and they are not interchangeable:

  * a **user** token, proving a human is present — the only thing that can approve a held action
    or change an agent's permissions;
  * an **agent** token, proving a program is calling on that user's behalf.

Every token therefore carries a `typ` claim, and every verification states which type it expects.
That check is not a formality: without it, an agent holding a valid agent token could present it to
the approval endpoint and sign off on its own work, which is precisely the thing the approval queue
exists to prevent. A token says *who is calling*; it never says what they are allowed to do — that
is resolved from the database on every request, so a revoked permission takes effect immediately
rather than lingering until the token expires.
"""

from datetime import timedelta

import jwt
from django.conf import settings
from django.utils import timezone

_ALGORITHM = "HS256"

TOKEN_TYPE_USER = "user"
TOKEN_TYPE_AGENT = "agent"


def _secret() -> str:
    if not settings.JWT_SECRET:
        raise RuntimeError("JWT_SECRET is not set — cannot sign or verify tokens. See .env.example.")
    return settings.JWT_SECRET


def _issue(payload: dict) -> tuple[str, str]:
    expires_at = timezone.now() + timedelta(minutes=settings.JWT_EXPIRES_IN_MINUTES)
    token = jwt.encode({**payload, "exp": expires_at}, _secret(), algorithm=_ALGORITHM)
    return token, expires_at.isoformat()


def issue_user_token(user) -> tuple[str, str]:
    """Sign a short-lived JWT identifying a human user. Returns (token, expires_at_iso)."""
    return _issue({
        "typ": TOKEN_TYPE_USER,
        "user_id": str(user.id),
        "username": user.username,
    })


def issue_agent_token(agent) -> tuple[str, str]:
    """Sign a short-lived JWT identifying an agent. Returns (token, expires_at_iso).

    Note what is absent: the agent's owner. The owning user is read from the database on each
    request instead, so re-assigning or deactivating an owner takes effect on the very next call
    rather than being frozen into a token for its whole lifetime."""
    return _issue({
        "typ": TOKEN_TYPE_AGENT,
        "agent_id": str(agent.id),
        "agent_name": agent.name,
    })


def decode_any_token(token: str) -> dict:
    """Verify signature and expiry, then return the claims **without** checking the token type.

    Only for the handful of endpoints that legitimately serve either identity (the audit log reads
    differently for a user than for an agent, but both may read it). Anything that is meant for one
    identity must use `decode_token` with an explicit `expected_type`.

    `algorithms` is passed explicitly (PyJWT requires it) to prevent algorithm-confusion attacks:
    if the library trusted the token's own `alg` header, an attacker could set `alg: none` and
    forge an unsigned token."""
    return jwt.decode(token, _secret(), algorithms=[_ALGORITHM])


def decode_token(token: str, *, expected_type: str) -> dict:
    """Verify signature, expiry, and token type, then return the claims.

    Raises jwt.PyJWTError on any problem, including a type mismatch."""
    claims = decode_any_token(token)
    if claims.get("typ") != expected_type:
        raise jwt.InvalidTokenError(
            f"Expected a {expected_type} token, got {claims.get('typ') or 'an untyped token'}."
        )
    return claims
