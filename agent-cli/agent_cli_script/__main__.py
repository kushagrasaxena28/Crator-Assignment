"""Interactive Claude Agent SDK CLI.

A backend session is established before the conversation starts; the agent then reaches the
permissions/audit backend only through Bash + curl (plus the one custom GenerateJWT tool), so
every action runs through the backend's permission and audit logic.
"""

import asyncio
import sys
from datetime import datetime

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

from .session import get_valid_token, get_backend_url
from .system_prompt import build_system_prompt
from .ticket_watcher import TicketWatcher
from .tools.generate_jwt import jwt_server

BACKEND_URL = get_backend_url()
SYSTEM_PROMPT = build_system_prompt(BACKEND_URL)

# ANSI styling for ticket-update notifications, named so the formatting below reads as
# intent ("bold + green") rather than bare escape codes.
_RESET = "\x1b[0m"
_BOLD = "\x1b[1m"
_DIM = "\x1b[2m"
_GREEN = "\x1b[32m"
_MAGENTA = "\x1b[35m"
_GREY = "\x1b[90m"


def _format_ticket_update(ticket_id: str, update: dict) -> str:
    """Render a ticket_watcher update as the text printed to the terminal."""
    short = ticket_id[:8]
    status = update.get("status")
    if status == "approved":
        return (
            f"\n{_BOLD}{_GREEN}● Ticket {short} was approved.{_RESET}\n"
            f"  {_DIM}Result: {update.get('result')}{_RESET}"
        )
    if status == "rejected":
        reason = update.get("reason") or "(none given)"
        return (
            f"\n{_BOLD}{_MAGENTA}● Ticket {short} was rejected.{_RESET}\n"
            f"  {_DIM}Reason: {reason}{_RESET}"
        )
    return f"\n{_BOLD}{_GREY}● Ticket {short} expired (24h passed with no decision).{_RESET}"


async def _process_turn(client: ClaudeSDKClient, ticket_watcher: TicketWatcher) -> None:
    """Stream one assistant turn to stdout: print text as it arrives and each tool's name as
    it's invoked. `receive_response()` always ends by yielding the turn's ResultMessage and
    then stopping; that message (like any non-StreamEvent) is simply skipped here, since
    everything worth showing has already been printed as StreamEvents by the time it arrives."""
    open_text_blocks: dict[int, str] = {}  # content-block index -> text accumulated so far

    async for message in client.receive_response():
        if type(message).__name__ != "StreamEvent":
            continue
        event = message.event or {}
        event_type = event.get("type")

        if event_type == "content_block_start":
            block = event["content_block"]
            if block["type"] == "text":
                open_text_blocks[event["index"]] = ""
                print("\nAgent: ", end="", flush=True)
            elif block["type"] == "tool_use":
                print(f"\n[tool_use] {block['name']}")

        elif event_type == "content_block_delta":
            index = event["index"]
            delta = event["delta"]
            if index in open_text_blocks and delta.get("type") == "text_delta":
                open_text_blocks[index] += delta["text"]
                print(delta["text"], end="", flush=True)

        elif event_type == "content_block_stop":
            index = event["index"]
            if index in open_text_blocks:
                print()
                # Scan the finished block for ticket ids now that its full text is known.
                ticket_watcher.scan(open_text_blocks.pop(index))

    print()  # blank line before the next "User:" prompt


async def _establish_session() -> None:
    """Fetch the first token so the CLI can report a ready-state before the user types
    anything. Exits the process if the backend can't be reached — there's no useful degraded
    mode without a session."""
    print("Establishing session...")
    try:
        cached = await get_valid_token()
    except Exception as err:  # noqa: BLE001 — any failure here means "can't proceed"
        print(f"Could not establish a session: {err}", file=sys.stderr)
        print(f"Is the backend running at {BACKEND_URL}?", file=sys.stderr)
        sys.exit(1)
    valid_until = datetime.fromtimestamp(cached.expires_at / 1000).strftime("%H:%M:%S")
    print(f"Session established. Token valid until {valid_until}.")


def _build_claude_options() -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        # Four built-in tools + our custom GenerateJWT tool (via the jwt MCP server).
        allowed_tools=["Read", "Write", "Edit", "Bash", "mcp__jwt__GenerateJWT"],
        mcp_servers={"jwt": jwt_server},
        # Local session hitting only localhost — skip interactive approval per Bash/curl call.
        permission_mode="bypassPermissions",
        # Stream text/tool-use deltas live instead of one message per full turn.
        include_partial_messages=True,
    )


async def main() -> None:
    await _establish_session()
    print('Type your request ("exit" to quit).\n')

    # Polls the backend directly (not via the model) so approvals/rejections reach the user
    # without them having to ask.
    ticket_watcher = TicketWatcher(
        lambda ticket_id, update: print(_format_ticket_update(ticket_id, update), flush=True)
    )
    ticket_watcher.start()

    async with ClaudeSDKClient(options=_build_claude_options()) as client:
        while True:
            try:
                line = await asyncio.to_thread(input, "User: ")
            except (EOFError, KeyboardInterrupt):
                break
            text = line.strip()
            if text in ("exit", "quit"):
                break
            if not text:
                continue
            await client.query(text)
            await _process_turn(client, ticket_watcher)

    ticket_watcher.stop()
    print("\nSession closed.")


def run() -> None:
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
