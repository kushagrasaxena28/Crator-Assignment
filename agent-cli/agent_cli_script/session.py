"""In-memory JWT cache: fetches, caches, and refreshes the token so a long session survives
past its ~20 min expiry. Never persisted to disk.

Faithful Python port of session.ts. The signing secret lives only on the backend, so this
process can never forge a token — it only asks the backend's token endpoint for one.
"""

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
import asyncio

import httpx
import jwt  # PyJWT — used only to read a token's `exp` claim if the backend omits expires_at
from dotenv import load_dotenv

# Load .env before reading any of the values below, so importing this module anywhere
# (main, the JWT tool, the ticket watcher) sees the same configuration.
load_dotenv()

BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000")
AGENT_ID = os.environ.get("AGENT_ID")

# Refresh this far before actual expiry so a call started near the deadline doesn't get
# handed a token that expires mid-flight.
REFRESH_MARGIN_MS = 60_000


@dataclass
class CachedToken:
    token: str
    expires_at: int  # epoch ms


_cached: Optional[CachedToken] = None
# Serializes concurrent fetches so callers share a single in-flight request (the async
# equivalent of the `inFlight` promise in session.ts).
_lock = asyncio.Lock()


def _now_ms() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp() * 1000)


def _parse_expires_at(body: dict, token: str) -> int:
    """Prefer the backend's `expires_at`; fall back to the JWT's own `exp` claim (PyJWT)."""
    raw = body.get("expires_at")
    if raw:
        # Accept ISO-8601 with a trailing Z (Node's Date parses this; datetime needs +00:00).
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    # No expires_at from the backend — read `exp` out of the token itself. We only decode
    # the claims here (not verify the signature), so no shared secret is needed.
    claims = jwt.decode(token, options={"verify_signature": False})
    return int(claims["exp"]) * 1000


async def _fetch_new_token() -> CachedToken:
    if not AGENT_ID:
        raise RuntimeError("AGENT_ID env var is not set — cannot establish a session.")
    async with httpx.AsyncClient() as client:
        res = await client.post(
            f"{BACKEND_URL}/api/auth/token/",
            headers={"Content-Type": "application/json"},
            json={"agent_id": AGENT_ID},
        )
    try:
        body = res.json()
    except Exception:
        body = {}
    if res.status_code >= 400:
        raise RuntimeError(f"Token request failed: {res.status_code} {body}")
    return CachedToken(token=body["token"], expires_at=_parse_expires_at(body, body["token"]))


async def get_valid_token(force_refresh: bool = False) -> CachedToken:
    """Return a valid token: cached if still fresh, otherwise fetch a new one. Pass
    force_refresh=True after a 401. The lock ensures that if two callers race while the
    token is stale, only one of them actually hits the network — the second sees the
    freshly-fetched token once it acquires the lock and returns that instead of fetching
    again."""
    global _cached
    async with _lock:
        is_stale = _cached is None or _now_ms() > _cached.expires_at - REFRESH_MARGIN_MS
        if not force_refresh and not is_stale:
            return _cached  # type: ignore[return-value]
        _cached = await _fetch_new_token()
        return _cached


def get_backend_url() -> str:
    return BACKEND_URL
