"""A minimal MCP client speaking JSON-RPC 2.0 over the Streamable HTTP transport.

Only what this system actually needs: `tools/list` to discover a server's catalog, and `tools/call`
to invoke one. Hand-written against the spec rather than pulling in an SDK — the surface is two
methods, and the registration layer already stores exactly the shape `tools/list` returns, so
`Tool.inputSchema` maps onto `Action.input_schema` one-to-one.

Talking to servers that exist in the wild forced four things that the spec alone does not make
obvious:

  * **Two protocol eras.** Revision 2026-07-28 and later are stateless — every request carries its
    version, identity and capabilities in `_meta`. Everything earlier opens with an `initialize`
    handshake. We probe with `initialize` first, because that is what essentially every deployed
    server still speaks, and fall forward to the stateless form only if `initialize` is unknown.
  * **Sessions.** A server may answer `initialize` with an `Mcp-Session-Id` header and then expect
    it on every later request. Servers that do not issue one are stateless and need nothing.
  * **Responses are often SSE.** `Content-Type: text/event-stream` with the JSON in `data:` lines,
    which may be split across several of them.
  * **Errors are not always JSON-RPC.** A server rejecting an unauthenticated request answers with
    an HTTP status and whatever body it likes — Notion sends `{"error": "invalid_token"}` where
    `error` is a *string*, GitHub sends `text/plain`. Neither is a JSON-RPC error object, and
    assuming otherwise is how this client used to crash.
"""

import ipaddress
import json
import re
import socket
from urllib.parse import urljoin, urlparse

import requests
from django.conf import settings

from ..errors import ExecutorError

# The protocol split into its two eras. Which era a version belongs to decides how requests are
# *shaped*, not just what version string they carry.
MODERN_PROTOCOL_VERSIONS = ["2026-07-28"]
LEGACY_PROTOCOL_VERSIONS = ["2025-11-25", "2025-06-18", "2025-03-26"]
SUPPORTED_PROTOCOL_VERSIONS = MODERN_PROTOCOL_VERSIONS + LEGACY_PROTOCOL_VERSIONS

# What we ask a legacy server for. Servers negotiate down and tell us what they picked.
_PREFERRED_LEGACY = "2025-06-18"

_CLIENT_INFO = {"name": "crator-permissions-layer", "version": "1.0.0"}
_META_PREFIX = "io.modelcontextprotocol"

# A hung server must never pin a worker thread indefinitely: (connect, read).
_TIMEOUT = (5, 30)
_MAX_REDIRECTS = 3
_MAX_PAGES = 50

_UNSUPPORTED_PROTOCOL_VERSION = -32022
_METHOD_NOT_FOUND = -32601


class MCPError(ExecutorError):
    """Any failure reaching or understanding an MCP server. Surfaces as an `execution_failed`
    audit row and a 502 carrying this message, so the agent can tell the user what went wrong."""


class MCPAuthRequired(MCPError):
    """The server refused us for lack of credentials. Separated out because the fix is a specific
    action the user has to take, not something to retry."""


# --- SSRF protection -------------------------------------------------------------------------
#
# Load-bearing, not defence in depth: registering a server is `always_allow`, so the agent chooses
# the URL and no human sees it before the backend connects.

def _is_forbidden_ip(ip: str) -> bool:
    address = ipaddress.ip_address(ip)
    return (
        address.is_private or address.is_loopback or address.is_link_local
        or address.is_reserved or address.is_multicast or address.is_unspecified
    )


def _loopback_allowed() -> bool:
    """Local development needs to reach a stub server on 127.0.0.1. Gated on DEBUG so the exception
    cannot exist in a deployed environment."""
    return bool(settings.DEBUG)


def validate_url(url: str) -> str:
    """Reject a URL before any connection is attempted. Returns the URL if it is safe to fetch."""
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        raise MCPError(f"URL must be http or https, got {parsed.scheme or 'no scheme'!r}.")
    if not parsed.hostname:
        raise MCPError("URL has no host.")
    if parsed.scheme == "http" and not _loopback_allowed():
        raise MCPError("MCP server URLs must use https.")

    try:
        # Resolve every address the name maps to — a host resolving to both a public and a private
        # address must not slip through on the strength of the public one.
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as err:
        raise MCPError(f"Could not resolve host {parsed.hostname!r}: {err}")

    for ip in {info[4][0] for info in infos}:
        if _is_forbidden_ip(ip):
            if _loopback_allowed() and ipaddress.ip_address(ip).is_loopback:
                continue
            raise MCPError(
                f"Refusing to connect to {parsed.hostname} ({ip}): private, loopback and "
                "link-local addresses are not reachable from this service."
            )
    return url


# --- Transport -------------------------------------------------------------------------------

def _post(url: str, payload: dict, headers: dict, protocol_version: str,
          session_id: str | None = None) -> requests.Response:
    """One JSON-RPC POST, following redirects by hand so each hop is re-validated. `requests` would
    otherwise happily follow a public URL's 302 into the private network the guard just excluded."""
    current = validate_url(url)
    outgoing = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": protocol_version,
        **headers,
    }
    if session_id:
        outgoing["Mcp-Session-Id"] = session_id

    for _ in range(_MAX_REDIRECTS + 1):
        response = requests.post(current, json=payload, headers=outgoing,
                                 timeout=_TIMEOUT, allow_redirects=False)
        if response.status_code in (301, 302, 303, 307, 308) and "location" in response.headers:
            current = validate_url(urljoin(current, response.headers["location"]))
            continue
        return response
    raise MCPError(f"Too many redirects from {url}.")


def _describe_auth_challenge(response: requests.Response, url: str) -> str:
    """Turn a 401 into something the user can act on.

    Most hosted MCP servers (Notion, GitHub, Linear) sit behind OAuth 2.1 and advertise it with a
    `WWW-Authenticate` header pointing at their protected-resource metadata. That is not something
    this backend can complete on its own — it needs a browser and a human — so the useful thing is
    to say exactly that, and to name the one alternative that does work here."""
    challenge = response.headers.get("WWW-Authenticate", "")
    metadata = re.search(r'resource_metadata="([^"]+)"', challenge)
    hint = f" Its authorization metadata is at {metadata.group(1)}." if metadata else ""
    return (
        f"{url} refused the connection with HTTP 401: it requires authentication.{hint} "
        "This server most likely uses OAuth, which has to be completed in a browser and cannot be "
        "done from here. If you can obtain an access token (for GitHub, a personal access token "
        "works), re-add the server with "
        '`headers: {"Authorization": "Bearer <token>"}`.'
    )


def _parse_body(response: requests.Response, url: str) -> dict:
    """Get the JSON-RPC message out of a response, whatever shape it arrived in.

    Handles both a plain JSON body and an SSE stream, where the payload lives in `data:` lines that
    may be split across several. Raises rather than returning something that isn't a JSON-RPC
    message, so callers never have to guess what they were handed."""
    if response.status_code == 401:
        raise MCPAuthRequired(_describe_auth_challenge(response, url))

    content_type = (response.headers.get("Content-Type") or "").lower()
    text = response.text

    if "text/event-stream" in content_type:
        # Per the SSE spec a message's data is the concatenation of its `data:` lines.
        data_lines = [line[len("data:"):].strip()
                      for line in text.splitlines() if line.startswith("data:")]
        if not data_lines:
            raise MCPError(f"{url} returned an event stream with no data.")
        text = "\n".join(data_lines)

    try:
        body = json.loads(text)
    except ValueError:
        detail = (text or "").strip()[:200] or "(empty body)"
        if response.status_code >= 400:
            raise MCPError(f"{url} returned HTTP {response.status_code}: {detail}")
        raise MCPError(f"{url} returned a non-JSON response: {detail}")

    if not isinstance(body, dict):
        raise MCPError(f"{url} returned {type(body).__name__} where a JSON-RPC object was expected.")
    return body


def _result_or_raise(body: dict, response: requests.Response, method: str, url: str) -> dict:
    """Pull `result` out of a JSON-RPC response, converting every error shape into an MCPError.

    `error` is only a JSON-RPC error object when the server is actually speaking JSON-RPC. A server
    rejecting the request at the HTTP layer may put a bare string there instead — which is exactly
    what used to crash this client with "'str' object has no attribute 'get'"."""
    error = body.get("error")

    if isinstance(error, dict):
        code = error.get("code")
        if code == _UNSUPPORTED_PROTOCOL_VERSION:
            raise _VersionMismatch((error.get("data") or {}).get("supported") or [])
        if code == _METHOD_NOT_FOUND:
            raise _MethodNotFound(str(error.get("message") or method))
        raise MCPError(f"{method} failed: [{code}] {error.get('message')}")

    if error:
        # Not a JSON-RPC error object — a plain error envelope. Report it as-is.
        description = body.get("error_description") or body.get("message") or ""
        raise MCPError(f"{method} failed: {error}{f' — {description}' if description else ''}")

    if response.status_code >= 400:
        raise MCPError(f"{method} failed: HTTP {response.status_code} from {url}")

    result = body.get("result")
    if not isinstance(result, dict):
        raise MCPError(f"{method} returned no usable result from {url}.")
    return result


class _VersionMismatch(Exception):
    """Internal: the server rejected our protocol version and told us what it does support."""

    def __init__(self, offered: list[str]):
        self.offered = offered


class _MethodNotFound(Exception):
    """Internal: the server does not implement this method — for `initialize`, that means it is a
    modern server with no handshake."""


class _Session:
    """How to talk to one server: which era, which version, and any session id it handed us."""

    def __init__(self, modern: bool, version: str, session_id: str | None = None):
        self.modern = modern
        self.version = version
        self.session_id = session_id


def _meta(version: str) -> dict:
    return {
        f"{_META_PREFIX}/protocolVersion": version,
        f"{_META_PREFIX}/clientInfo": _CLIENT_INFO,
        f"{_META_PREFIX}/clientCapabilities": {"tools": {}},
    }


def _rpc(server, session: _Session, method: str, params: dict | None = None) -> dict:
    body_params = dict(params or {})
    if session.modern:
        body_params["_meta"] = _meta(session.version)

    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": body_params}
    try:
        response = _post(server.url, payload, server.headers or {}, session.version, session.session_id)
    except requests.RequestException as err:
        raise MCPError(f"Could not reach {server.url}: {err}")

    body = _parse_body(response, server.url)
    return _result_or_raise(body, response, method, server.url)


def _open_session(server) -> _Session:
    """Work out how to talk to this server.

    `initialize` first: nearly every deployed server is still handshake-era, and it is also the only
    way to be handed a session id. A server that answers "method not found" is a modern one, and we
    switch to the stateless per-request form."""
    payload = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": _PREFERRED_LEGACY,
            "capabilities": {"tools": {}},
            "clientInfo": _CLIENT_INFO,
        },
    }
    try:
        response = _post(server.url, payload, server.headers or {}, _PREFERRED_LEGACY)
    except requests.RequestException as err:
        raise MCPError(f"Could not reach {server.url}: {err}")

    try:
        body = _parse_body(response, server.url)
        result = _result_or_raise(body, response, "initialize", server.url)
    except (_MethodNotFound, _VersionMismatch):
        return _Session(modern=True, version=MODERN_PROTOCOL_VERSIONS[0])

    version = result.get("protocolVersion") or _PREFERRED_LEGACY
    session_id = response.headers.get("Mcp-Session-Id") or response.headers.get("mcp-session-id")
    session = _Session(modern=False, version=version, session_id=session_id)

    # The spec's post-initialize notification. Servers that ignore it are fine; a failure here must
    # not sink an otherwise working connection.
    try:
        _post(server.url, {"jsonrpc": "2.0", "method": "notifications/initialized"},
              server.headers or {}, version, session_id)
    except requests.RequestException:
        pass
    return session


# --- Public API ------------------------------------------------------------------------------

def list_tools(server) -> tuple[list[dict], str]:
    """Discover every tool a server exposes, following pagination. Returns (tools, version)."""
    session = _open_session(server)
    tools: list[dict] = []
    params: dict = {}

    for _ in range(_MAX_PAGES):
        try:
            result = _rpc(server, session, "tools/list", params)
        except _MethodNotFound:
            raise MCPError(f"{server.url} does not expose any tools (tools/list is not implemented).")
        except _VersionMismatch as mismatch:
            raise MCPError(
                f"No mutually supported MCP protocol version. Server offers {mismatch.offered}, "
                f"this client speaks {SUPPORTED_PROTOCOL_VERSIONS}."
            )
        tools.extend(result.get("tools") or [])
        cursor = result.get("nextCursor")
        if not cursor:
            return tools, session.version
        params = {"cursor": cursor}

    raise MCPError("Server returned more pages of tools than this client will follow.")


def call_tool(server, tool_name: str, arguments: dict) -> dict:
    """Invoke one tool and return its result.

    Note what is *not* treated as a failure: `isError: true`. In MCP that is a successful protocol
    exchange carrying a tool-level problem the model is expected to read and correct for, so it is
    returned as an ordinary result and audited `executed`. Only a JSON-RPC `error` means the call
    itself did not happen."""
    session = _open_session(server)
    try:
        result = _rpc(server, session, "tools/call", {"name": tool_name, "arguments": arguments})
    except _MethodNotFound:
        raise MCPError(f'{server.url} rejected "{tool_name}": no such tool.')
    except _VersionMismatch:
        raise MCPError(f"{server.url} rejected this client's protocol version.")
    return {
        "content": result.get("content") or [],
        "structuredContent": result.get("structuredContent"),
        "isError": bool(result.get("isError")),
    }
