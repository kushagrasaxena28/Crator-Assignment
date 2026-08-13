"""Turn what an MCP server advertises into rows in the catalog.

This is the runtime equivalent of `manage.py seed`: same upsert-and-prune shape, except the source
is a live `tools/list` response instead of a `catalog.py` file. Once the rows exist, nothing
downstream can tell the difference — permission resolution, validation, the approval queue and the
audit log all operate on `Action` and never learn where it came from.
"""

import re
from datetime import datetime, timezone

from django.db import transaction

from ...models import Action, PolicyDefault, Toolkit
from . import client

# What Django's SlugField and the <slug:> URL converter accept.
_SLUG_SAFE = re.compile(r"[^a-z0-9_-]+")


def slugify_tool_name(name: str) -> str:
    """MCP allows tool names a URL segment does not — dots especially (`admin.tools.list`). Map to
    something routable; the exact name is preserved separately in `Action.remote_name`."""
    slug = _SLUG_SAFE.sub("_", name.strip().lower()).strip("_")
    return slug or "tool"


def _unique_slugs(tools: list[dict]) -> dict[str, str]:
    """Map each tool's real name to a slug that is unique within this toolkit. Sanitising can
    collide (`a.b` and `a-b` both become `a_b`), so later duplicates get a numeric suffix."""
    taken: set[str] = set()
    mapping: dict[str, str] = {}
    for tool in tools:
        base = slugify_tool_name(tool["name"])
        slug, n = base, 2
        while slug in taken:
            slug, n = f"{base}_{n}", n + 1
        taken.add(slug)
        mapping[tool["name"]] = slug
    return mapping


def _schema_or_empty(value) -> dict:
    """`inputSchema` is required by the spec but servers are not always well behaved, and
    `outputSchema` is optional. `validate_params` treats `{}` as "anything goes"."""
    return value if isinstance(value, dict) else {}


@transaction.atomic
def sync_catalog(server, tools: list[dict], protocol_version: str) -> dict:
    """Make the catalog match the tool list the server just advertised.

    The network call happens before this opens its transaction (see `discover`), so a server that
    is unreachable or misbehaving never leaves a half-built toolkit behind — and no database write
    is held open across an external HTTP request."""
    toolkit, _ = Toolkit.objects.update_or_create(
        mcp_server=server,
        defaults={
            "slug": server.slug,
            "name": server.name,
            "description": f"Tools discovered from the MCP server at {server.url}.",
        },
    )

    default_permission = PolicyDefault.current().default_permission_for_new_actions
    slugs = _unique_slugs(tools)

    for tool in tools:
        Action.objects.update_or_create(
            toolkit=toolkit,
            slug=slugs[tool["name"]],
            defaults={
                "remote_name": tool["name"],
                "name": tool.get("title") or tool["name"],
                "description": tool.get("description") or "",
                "input_schema": _schema_or_empty(tool.get("inputSchema")),
                "output_schema": _schema_or_empty(tool.get("outputSchema")),
                # Newly discovered tools take the database-configured default. Note the
                # consequence: a server that adds a tool later has it become callable on the next
                # refresh without a fresh human decision.
                "default_permission": default_permission,
            },
        )

    # Drop actions the server no longer advertises, so an unplugged tool stops being callable.
    stale = toolkit.actions.exclude(slug__in=set(slugs.values()))
    pruned = [a.slug for a in stale]
    stale.delete()

    server.protocol_version = protocol_version
    server.last_error = ""
    server.last_discovered_at = datetime.now(tz=timezone.utc)
    server.save(update_fields=["protocol_version", "last_error", "last_discovered_at", "updated_at"])

    return {
        "toolkit": toolkit.slug,
        "tools": sorted(slugs.values()),
        "tool_count": len(tools),
        "pruned": sorted(pruned),
        "protocol_version": protocol_version,
    }


def discover(server) -> dict:
    """Fetch the server's tool list, then sync it into the catalog. The one entry point callers use."""
    try:
        tools, protocol_version = client.list_tools(server)
    except client.MCPError as err:
        # Record why it failed so `list_servers` can show the user, then let it propagate: the call
        # view turns it into an `execution_failed` audit row and a 502.
        server.last_error = str(err)[:1000]
        server.save(update_fields=["last_error", "updated_at"])
        raise

    return sync_catalog(server, tools, protocol_version)
