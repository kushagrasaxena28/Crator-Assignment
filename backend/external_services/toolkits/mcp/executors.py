"""The `mcp` toolkit's side effects: registering, refreshing, unplugging and removing MCP servers.

Every function here is scoped to `context.user` — a user can only ever see and change their own
servers, and the toolkits those servers produce are visible only to that user's agents.

These are the one place in `toolkits/` that touches this app's own models. That is the nature of
the toolkit: its external service *is* the catalog.
"""

import re

from django.db import transaction

from ...models import MCPServer, Toolkit
from ..errors import InvalidRequest
from . import client, discovery

_SLUG_SAFE = re.compile(r"[^a-z0-9_-]+")


def _slugify_name(name: str) -> str:
    slug = _SLUG_SAFE.sub("-", name.strip().lower()).strip("-")
    if not slug:
        raise InvalidRequest("Server name must contain at least one letter or digit.")
    return slug


def _builtin_slugs() -> set[str]:
    """Slugs of the code-defined toolkits, which a user-registered server must not shadow."""
    from .. import TOOLKITS  # imported lazily to avoid a circular import at module load

    return {toolkit["slug"] for toolkit in TOOLKITS}


def _summary(server: MCPServer) -> dict:
    toolkit = Toolkit.objects.filter(mcp_server=server).first()
    return {
        "slug": server.slug,
        "name": server.name,
        "url": server.url,
        "enabled": server.is_enabled,
        "tool_count": toolkit.actions.count() if toolkit else 0,
        "last_discovered_at": server.last_discovered_at.isoformat() if server.last_discovered_at else None,
        "last_error": server.last_error,
        # Deliberately absent: `headers`. Credentials go in and are never read back out.
    }


def _get_own_server(context, name: str) -> MCPServer:
    slug = _slugify_name(name)
    server = MCPServer.objects.filter(owner=context.user, slug=slug).first()
    if server is None:
        raise InvalidRequest(f'You have no MCP server called "{name}".')
    return server


def _normalize_config(params: dict) -> tuple[str, str, dict]:
    """Accept either a flat {name, url, headers} or the standard mcpServers map. Returns
    (name, url, headers)."""
    servers = params.get("mcpServers")
    if servers:
        if len(servers) != 1:
            raise InvalidRequest("Provide exactly one server in mcpServers; add the others separately.")
        name, entry = next(iter(servers.items()))
        transport = (entry.get("type") or "http").lower()
        if transport != "http":
            raise InvalidRequest(
                f'Transport "{transport}" is not supported. This backend can only reach MCP servers '
                "over HTTP — a stdio server would have to run as a subprocess of the backend itself."
            )
        return name, entry.get("url") or "", entry.get("headers") or {}

    return params.get("name") or "", params.get("url") or "", params.get("headers") or {}


def _add_server(params: dict, context) -> dict:
    name, url, headers = _normalize_config(params)
    if not name or not url:
        raise InvalidRequest(
            "Both a name and a url are required — either as {name, url} or as an mcpServers entry."
        )

    slug = _slugify_name(name)
    if slug in _builtin_slugs():
        raise InvalidRequest(f'"{slug}" is a built-in toolkit name; choose a different name.')
    if MCPServer.objects.filter(owner=context.user, slug=slug).exists():
        raise InvalidRequest(f'You already have a server called "{slug}". Use refresh_server to re-sync it.')

    # Reject an unreachable-by-policy URL before creating anything, so a rejected registration
    # leaves no trace beyond its audit row.
    client.validate_url(url)

    server = MCPServer.objects.create(
        owner=context.user, name=name, slug=slug, url=url, headers=headers or {}
    )
    try:
        result = discovery.discover(server)
    except Exception:
        # Discovery is what makes a server useful; a server we cannot talk to should not linger as
        # a half-registered row for the user to trip over later.
        server.delete()
        raise

    return {
        "server": _summary(server),
        "toolkit": result["toolkit"],
        "tools": result["tools"],
        "tool_count": result["tool_count"],
    }


def _list_servers(params: dict, context) -> dict:
    servers = MCPServer.objects.filter(owner=context.user).order_by("slug")
    return {"servers": [_summary(server) for server in servers]}


def _refresh_server(params: dict, context) -> dict:
    server = _get_own_server(context, params["name"])
    result = discovery.discover(server)
    return {
        "toolkit": result["toolkit"],
        "tools": result["tools"],
        "pruned": result["pruned"],
        "tool_count": result["tool_count"],
    }


def _set_enabled(context, name: str, enabled: bool) -> dict:
    server = _get_own_server(context, name)
    server.is_enabled = enabled
    server.save(update_fields=["is_enabled", "updated_at"])
    return {"server": _summary(server)}


def _disable_server(params: dict, context) -> dict:
    return _set_enabled(context, params["name"], False)


def _enable_server(params: dict, context) -> dict:
    return _set_enabled(context, params["name"], True)


def _remove_server(params: dict, context) -> dict:
    server = _get_own_server(context, params["name"])
    toolkit = Toolkit.objects.filter(mcp_server=server).first()
    action_count = toolkit.actions.count() if toolkit else 0
    slug = server.slug

    with transaction.atomic():
        # Cascades to the toolkit, its actions, and any permission overrides or approval tickets
        # pointing at them. Audit rows are untouched — they hold no foreign key to any of this,
        # which is exactly the property that makes history survive its own catalog.
        server.delete()

    return {"removed": slug, "actions_removed": action_count}


EXECUTORS = {
    "add_server": _add_server,
    "list_servers": _list_servers,
    "refresh_server": _refresh_server,
    "disable_server": _disable_server,
    "enable_server": _enable_server,
    "remove_server": _remove_server,
}
