"""The `mcp` toolkit: the one whose actions manage other toolkits.

Registering an MCP server is itself an action, called through the same
`POST /toolkits/mcp/actions/<action>/call/` boundary as everything else. That is the whole trick —
it means plugging in a new server automatically gets schema validation, permission enforcement, an
audit row and, where the permission calls for it, the human approval queue, without a single new
endpoint or line of auth code. The plugin system is registered as a plugin.

`add_server` accepts either a flat `{name, url, headers}` object or the `mcpServers` map that
Claude Desktop, Claude Code, Cursor and VS Code all use, so a user can paste the config they
already have.
"""

_SERVER_ENTRY = {
    "type": "object",
    "properties": {
        "url": {
            "type": "string",
            "minLength": 1,
            "description": "The server's Streamable HTTP endpoint, e.g. https://mcp.example.com/mcp",
        },
        "type": {
            "type": "string",
            "enum": ["http"],
            "description": "Transport. Only 'http' is supported; stdio servers cannot be reached "
                           "from a hosted backend.",
        },
        "headers": {
            "type": "object",
            "additionalProperties": {"type": "string"},
            "description": "Auth headers, e.g. {\"Authorization\": \"Bearer ...\"}. Stored on the "
                           "server record and redacted from the audit log.",
        },
    },
    "required": ["url"],
}

_SERVER_SUMMARY = {
    "type": "object",
    "properties": {
        "slug": {"type": "string"},
        "name": {"type": "string"},
        "url": {"type": "string"},
        "enabled": {"type": "boolean"},
        "tool_count": {"type": "integer"},
        "last_discovered_at": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "last_error": {"type": "string"},
    },
}

TOOLKIT = {
    "slug": "mcp",
    "name": "MCP Servers",
    "description": (
        "Plug your own MCP servers into this system. Adding one discovers its tools and makes them "
        "available to you as a new toolkit; disabling one takes them away again."
    ),
    "actions": [
        {
            "slug": "add_server",
            "name": "Add MCP Server",
            "description": (
                "Register an MCP server and discover its tools. Give it a short name, the server's "
                "https URL, and auth headers if the server needs them. The discovered tools become "
                "a new toolkit visible only to you."
            ),
            "default_permission": "always_allow",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "minLength": 1,
                        "description": "Short name for the server, e.g. 'stripe'. Becomes the toolkit slug.",
                    },
                    "url": {"type": "string", "minLength": 1},
                    "headers": _SERVER_ENTRY["properties"]["headers"],
                    "mcpServers": {
                        "type": "object",
                        "additionalProperties": _SERVER_ENTRY,
                        "description": "Alternative to name/url: the standard mcpServers config "
                                       "object. Exactly one entry.",
                    },
                },
            },
            "output_schema": {
                "type": "object",
                "properties": {
                    "server": _SERVER_SUMMARY,
                    "toolkit": {"type": "string"},
                    "tools": {"type": "array", "items": {"type": "string"}},
                    "tool_count": {"type": "integer"},
                },
            },
        },
        {
            "slug": "list_servers",
            "name": "List MCP Servers",
            "description": "List the MCP servers you have registered, with their tool counts and status.",
            "default_permission": "always_allow",
            "input_schema": {"type": "object", "properties": {}},
            "output_schema": {
                "type": "object",
                "properties": {"servers": {"type": "array", "items": _SERVER_SUMMARY}},
            },
        },
        {
            "slug": "refresh_server",
            "name": "Refresh MCP Server",
            "description": "Re-run discovery against a registered server and update its tool list.",
            "default_permission": "always_allow",
            "input_schema": {
                "type": "object",
                "properties": {"name": {"type": "string", "minLength": 1}},
                "required": ["name"],
            },
            "output_schema": {
                "type": "object",
                "properties": {
                    "toolkit": {"type": "string"},
                    "tools": {"type": "array", "items": {"type": "string"}},
                    "pruned": {"type": "array", "items": {"type": "string"}},
                    "tool_count": {"type": "integer"},
                },
            },
        },
        {
            "slug": "disable_server",
            "name": "Disable MCP Server",
            "description": (
                "Unplug a server: its tools stop appearing in the catalog and stop being callable. "
                "Nothing is deleted, so it can be re-enabled at any time."
            ),
            "default_permission": "always_allow",
            "input_schema": {
                "type": "object",
                "properties": {"name": {"type": "string", "minLength": 1}},
                "required": ["name"],
            },
            "output_schema": {"type": "object", "properties": {"server": _SERVER_SUMMARY}},
        },
        {
            "slug": "enable_server",
            "name": "Enable MCP Server",
            "description": "Plug a previously disabled server back in.",
            "default_permission": "always_allow",
            "input_schema": {
                "type": "object",
                "properties": {"name": {"type": "string", "minLength": 1}},
                "required": ["name"],
            },
            "output_schema": {"type": "object", "properties": {"server": _SERVER_SUMMARY}},
        },
        {
            "slug": "remove_server",
            "name": "Remove MCP Server",
            "description": (
                "Permanently delete a registered server and its toolkit. Prefer disable_server "
                "unless you really want the configuration gone."
            ),
            # The only gated action here, and deliberately so: deleting a toolkit cascades to its
            # actions and from there to permission overrides and approval tickets. `disable_server`
            # covers the ordinary unplug with no data loss, so the destructive path can afford to
            # wait for a human. Audit rows survive either way — they have no FK to any of this.
            "default_permission": "requires_approval",
            "input_schema": {
                "type": "object",
                "properties": {"name": {"type": "string", "minLength": 1}},
                "required": ["name"],
            },
            "output_schema": {
                "type": "object",
                "properties": {"removed": {"type": "string"}, "actions_removed": {"type": "integer"}},
            },
        },
    ],
}
