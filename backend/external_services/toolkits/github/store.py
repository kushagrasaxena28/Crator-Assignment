"""In-memory store for the `github` mock toolkit — simulates GitHub's own data, the same way
`notion/client.py` talks to Notion's real API. State lives only in this process's memory and
resets on restart; that's expected of a mock external service, not a bug.

`delete_repo` removes the repo's issues along with it — a plain-Python stand-in for the FK
`on_delete=CASCADE` a real relational table would give for free.
"""

import uuid

_REPO_1_ID = "20000000-0000-0000-0000-000000000001"
_REPO_2_ID = "20000000-0000-0000-0000-000000000002"

_repos: dict[str, dict] = {
    _REPO_1_ID: {
        "id": _REPO_1_ID,
        "name": "permissions-layer",
        "owner": "crator",
        "description": "The permissions and audit layer this assignment builds.",
        "stars": 12,
    },
    _REPO_2_ID: {
        "id": _REPO_2_ID,
        "name": "docs-site",
        "owner": "crator",
        "description": "Public documentation site.",
        "stars": 3,
    },
}

_issues: dict[str, dict] = {}


def list_repos() -> list[dict]:
    return list(_repos.values())


def get_repo(repo_id: str) -> dict | None:
    return _repos.get(repo_id)


def create_issue(repo_id: str, title: str, body: str) -> dict | None:
    if repo_id not in _repos:
        return None
    issue_id = str(uuid.uuid4())
    issue = {"id": issue_id, "repoId": repo_id, "title": title, "body": body}
    _issues[issue_id] = issue
    return issue


def delete_repo(repo_id: str) -> bool:
    if repo_id not in _repos:
        return False
    del _repos[repo_id]
    for issue_id in [i for i, issue in _issues.items() if issue["repoId"] == repo_id]:
        del _issues[issue_id]
    return True
