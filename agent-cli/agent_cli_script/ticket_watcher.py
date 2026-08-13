"""Background watcher, independent of the model: polls the backend on its own timer so an
approval/rejection shows up without the user having to ask.
"""

import asyncio
import re
from typing import Callable, Optional

import httpx

from .session import get_valid_token, get_backend_url

POLL_INTERVAL_S = 2.0  # same cadence as the model's own "wait for it" loop

# Loose UUID shape (no version/variant check) to also match this project's non-standard seed
# ids. Every match gets a one-time status check; non-tickets just come back 404.
UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)


async def _fetch_status(ticket_id: str) -> Optional[dict]:
    backend_url = get_backend_url()
    cached = await get_valid_token()
    async with httpx.AsyncClient() as client:
        res = await client.get(
            f"{backend_url}/api/external-services/approvals/{ticket_id}/status/",
            headers={"Authorization": f"Bearer {cached.token}"},
        )
    if res.status_code >= 400:
        return None  # 404 or other error — not a watchable ticket
    return res.json()


class TicketWatcher:
    def __init__(self, on_update: Callable[[str, dict], None]):
        self._seen_ids: set[str] = set()
        self._watching: set[str] = set()
        self._task: Optional[asyncio.Task] = None
        self._on_update = on_update

    def scan(self, text: str) -> None:
        """Feed it each completed assistant text block. New UUID-shaped substrings get a
        one-time check; ones that turn out to be real, currently-pending tickets join the
        poll set."""
        for raw in UUID_RE.findall(text):
            ticket_id = raw.lower()
            if ticket_id in self._seen_ids:
                continue
            self._seen_ids.add(ticket_id)
            asyncio.create_task(self._check_and_maybe_watch(ticket_id))

    async def _check_and_maybe_watch(self, ticket_id: str) -> None:
        try:
            status = await _fetch_status(ticket_id)
            if status and status.get("status") == "pending":
                self._watching.add(ticket_id)
        except Exception:
            # Backend unreachable, or transient failure on an id that might not even be a
            # ticket — not worth surfacing an error for.
            pass

    def start(self) -> None:
        if self._task:
            return
        self._task = asyncio.create_task(self._run())

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None

    async def _run(self) -> None:
        try:
            while True:
                await self._tick()
                await asyncio.sleep(POLL_INTERVAL_S)
        except asyncio.CancelledError:
            pass

    async def _tick(self) -> None:
        for ticket_id in list(self._watching):
            try:
                status = await _fetch_status(ticket_id)
            except Exception:
                continue  # transient hiccup — stays in the watch set, retried next tick
            if not status or status.get("status") == "pending":
                continue  # no change yet

            self._watching.discard(ticket_id)
            state = status.get("status")
            if state == "approved":
                self._on_update(ticket_id, {"status": "approved", "result": status.get("result")})
            elif state == "rejected":
                self._on_update(ticket_id, {"status": "rejected", "reason": status.get("reason")})
            elif state == "expired":
                self._on_update(ticket_id, {"status": "expired"})
