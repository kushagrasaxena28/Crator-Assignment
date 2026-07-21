"""The `github` toolkit: a repos/issues surface backed by local tables. Pure data — same shape
and conventions as `items`."""

_REPO_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "name": {"type": "string"},
        "owner": {"type": "string"},
        "description": {"type": "string"},
        "stars": {"type": "number"},
    },
    "required": ["id", "name", "owner", "description", "stars"],
}

_ISSUE_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "repoId": {"type": "string"},
        "title": {"type": "string"},
        "body": {"type": "string"},
    },
    "required": ["id", "repoId", "title", "body"],
}

TOOLKIT = {
    "slug": "github",
    "name": "GitHub",
    "description": "A minimal repos/issues toolkit modeled on the brief's own suggested MCP servers.",
    "actions": [
        {
            "slug": "list_repos",
            "name": "List Repos",
            "description": "List all repositories.",
            "default_permission": "always_allow",
            "input_schema": {"type": "object", "properties": {}},
            "output_schema": {
                "type": "object",
                "properties": {"repos": {"type": "array", "items": _REPO_SCHEMA}},
            },
        },
        {
            "slug": "get_repo",
            "name": "Get Repo",
            "description": "Fetch a single repository by id.",
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
                    "repo": {"anyOf": [_REPO_SCHEMA, {"type": "null"}]},
                },
            },
        },
        {
            "slug": "create_issue",
            "name": "Create Issue",
            "description": "Open a new issue on a repository.",
            "default_permission": "requires_approval",
            "input_schema": {
                "type": "object",
                "properties": {
                    "repoId": {"type": "string", "minLength": 1},
                    "title": {"type": "string", "minLength": 1},
                    "body": {"type": "string"},
                },
                "required": ["repoId", "title", "body"],
            },
            "output_schema": {
                "type": "object",
                "properties": {
                    "found": {"type": "boolean"},
                    "issue": {"anyOf": [_ISSUE_SCHEMA, {"type": "null"}]},
                },
            },
        },
        {
            "slug": "delete_repo",
            "name": "Delete Repo",
            "description": "Permanently delete a repository.",
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
