"""The `items` toolkit: a minimal read/create/update/delete surface backed by a local table.

Pure data. Each action carries the JSON schemas that get stored on the `Action` row at seed
time; the stored `input_schema` is the single source of truth the call endpoint validates
against (there is no separate validator to drift from it).
"""

_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "name": {"type": "string"},
        "content": {"type": "string"},
    },
    "required": ["id", "name", "content"],
}

TOOLKIT = {
    "slug": "items",
    "name": "Items",
    "description": "A minimal read/create/update/delete toolkit for demoing the permissions layer.",
    "actions": [
        {
            "slug": "read_item",
            "name": "Read Item",
            "description": "Fetch a single item by id.",
            "default_permission": "always_allow",
            "input_schema": {
                "type": "object",
                "properties": {"id": {"type": "string", "minLength": 1}},
                "required": ["id"],
            },
            "output_schema": {
                "type": "object",
                "properties": {
                    "found": {"type": "boolean"},
                    "item": {"anyOf": [_ITEM_SCHEMA, {"type": "null"}]},
                },
            },
        },
        {
            "slug": "create_item",
            "name": "Create Item",
            "description": "Create a new item.",
            "default_permission": "requires_approval",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "minLength": 1},
                    "content": {"type": "string"},
                },
                "required": ["name", "content"],
            },
            "output_schema": {
                "type": "object",
                "properties": {"item": _ITEM_SCHEMA},
            },
        },
        {
            "slug": "update_item",
            "name": "Update Item",
            "description": "Update an existing item's fields.",
            "default_permission": "requires_approval",
            "input_schema": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "minLength": 1},
                    "name": {"type": "string", "minLength": 1},
                    "content": {"type": "string"},
                },
                "required": ["id"],
            },
            "output_schema": {
                "type": "object",
                "properties": {
                    "found": {"type": "boolean"},
                    "item": {"anyOf": [_ITEM_SCHEMA, {"type": "null"}]},
                },
            },
        },
        {
            "slug": "delete_item",
            "name": "Delete Item",
            "description": "Permanently delete an item.",
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
