"""In-memory agent-token cache: fetches, caches, and refreshes the token so a long session survives
past its ~20 min expiry. Never persisted to disk.

The backend holds the signing secret, so this process can never forge a token — it exchanges the
agent's id and secret for one. Note what is deliberately *not* here: the owning user's password.
This process runs an agent with shell access, so anything readable from here is readable by the
model; keeping user credentials out means the agent cannot mint a user token and approve its own
held actions.
"""

import asyncio
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
import jwt  # PyJWT — used only to read a token's `exp` claim if the backend omits expires_at
from dotenv import load_dotenv

# Load .env before reading any of the values below, so importing this module anywhere
# (main, the JWT tool, the ticket watcher) sees the same configuration. The path is explicit
# rather than left to dotenv's cwd-upward search, so the CLI behaves the same whichever
# directory it's launched from.
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_ENV_PATH)

BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000")
AGENT_ID = os.environ.get("AGENT_ID")
AGENT_SECRET = os.environ.get("AGENT_SECRET")

# The literal shipped in .env.example. Copying that file and forgetting to paste the real secret
# is the single most likely setup mistake, and the backend can only answer it with a generic 401
# (it deliberately doesn't distinguish "wrong secret" from "no such agent"), so we catch it here
# where we can actually say what went wrong.
_SECRET_PLACEHOLDER = "paste-the-agent-secret-printed-by-seed"

# Refresh this far before actual expiry so a call started near the deadline doesn't get
# handed a token that expires mid-flight.
REFRESH_MARGIN_MS = 60_000


@dataclass
class CachedToken:
    token: str
    expires_at: int  # epoch ms


_cached: Optional[CachedToken] = None
# Serializes concurrent fetches so callers share a single in-flight request.
_lock = asyncio.Lock()


def _now_ms() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp() * 1000)


def _parse_expires_at(body: dict, token: str) -> int:
    """Prefer the backend's `expires_at`; fall back to the JWT's own `exp` claim (PyJWT)."""
    raw = body.get("expires_at")
    if raw:
        # Accept ISO-8601 with a trailing Z (datetime needs +00:00).
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    # No expires_at from the backend — read `exp` out of the token itself. We only decode
    # the claims here (not verify the signature), so no shared secret is needed.
    claims = jwt.decode(token, options={"verify_signature": False})
    return int(claims["exp"]) * 1000


def _check_config() -> None:
    """Fail with an actionable message before spending a round trip on a request that can't work."""
    missing = [name for name, value in (("AGENT_ID", AGENT_ID), ("AGENT_SECRET", AGENT_SECRET)) if not value]
    if missing:
        raise RuntimeError(
            f"{' and '.join(missing)} not set in {_ENV_PATH}.\n"
            "  Run `python manage.py seed` in the backend — it prints the agent secret once — "
            "and copy it into .env as AGENT_SECRET."
        )
    if AGENT_SECRET == _SECRET_PLACEHOLDER:
        raise RuntimeError(
            f"AGENT_SECRET in {_ENV_PATH} is still the placeholder from .env.example.\n"
            "  Replace it with the real secret that `python manage.py seed` printed. If you no "
            "longer have it, re-run seed with DEMO_AGENT_SECRET set to a value of your choosing."
        )


async def _fetch_new_token() -> CachedToken:
    _check_config()
    async with httpx.AsyncClient() as client:
        res = await client.post(
            f"{BACKEND_URL}/api/auth/agent/token/",
            headers={"Content-Type": "application/json"},
            json={"agent_id": AGENT_ID, "agent_secret": AGENT_SECRET},
        )
    try:
        body = res.json()
    except Exception:
        body = {}
    if res.status_code == 401:
        # The backend answers every failed exchange identically on purpose, so that a caller can't
        # enumerate agent ids. That means the useful diagnosis has to happen on this side.
        raise RuntimeError(
            f"The backend rejected this agent's credentials (401).\n"
            f"  AGENT_ID     {AGENT_ID}\n"
            f"  AGENT_SECRET set in {_ENV_PATH}, but not accepted.\n"
            "  Most likely the secret in .env doesn't match the database — re-running "
            "`python manage.py migrate`/`seed` on a fresh database issues a new one.\n"
            "  Fix: re-run seed with DEMO_AGENT_SECRET=<value> and put the same value in .env."
        )
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
