"""The `notion` toolkit: a genuine live integration (see `client.py` in this package). Pure data —
the schemas here are our simplified page shapes, which `client.py` translates to/from Notion's
raw API shapes."""

_PAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "title": {"type": "string"},
        "url": {"type": "string"},
        "archived": {"type": "boolean"},
    },
    "required": ["id", "title"],
}

TOOLKIT = {
    "slug": "notion",
    "name": "Notion",
    "description": "A live Notion integration — real pages in a real workspace, not a local table.",
    "actions": [
        {
            "slug": "list_pages",
            "name": "List Pages",
            "description": "View all pages the integration can see.",
            "default_permission": "always_allow",
            "input_schema": {"type": "object", "properties": {}},
            "output_schema": {
                "type": "object",
                "properties": {"pages": {"type": "array", "items": _PAGE_SCHEMA}},
            },
        },
        {
            "slug": "create_page",
            "name": "Create Page",
            "description": "Create a new page.",
            "default_permission": "requires_approval",
            "input_schema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "minLength": 1},
                    "content": {"type": "string"},
                },
                "required": ["title", "content"],
            },
            "output_schema": {
                "type": "object",
                "properties": {"page": _PAGE_SCHEMA},
            },
        },
        {
            "slug": "update_page",
            "name": "Update Page",
            "description": "Update an existing page's title/content.",
            "default_permission": "requires_approval",
            "input_schema": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "minLength": 1},
                    "title": {"type": "string", "minLength": 1},
                    "content": {"type": "string"},
                },
                "required": ["id"],
            },
            "output_schema": {
                "type": "object",
                "properties": {
                    "found": {"type": "boolean"},
                    "page": {"anyOf": [_PAGE_SCHEMA, {"type": "null"}]},
                },
            },
        },
        {
            "slug": "delete_page",
            "name": "Delete Page",
            "description": "Archive (Notion's closest equivalent to delete) a page.",
            "default_permission": "always_deny",
            "input_schema": {
                "type": "object",
                "properties": {"id": {"type": "string", "minLength": 1}},
                "required": ["id"],
            },
            "output_schema": {
                "type": "object",
                "properties": {"found": {"type": "boolean"}},
            },
        },
    ],
}
