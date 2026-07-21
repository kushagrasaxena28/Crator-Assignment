"""The `github` toolkit's actual side-effecting code — reads and writes its own in-memory store
(store.py), simulating a mock external service. Same `EXECUTORS` export contract as every other
toolkit package."""

from . import store


def _list_repos(params: dict) -> dict:
    return {"repos": store.list_repos()}


def _get_repo(params: dict) -> dict:
    repo = store.get_repo(params["id"])
    return {"found": repo is not None, "repo": repo}


def _create_issue(params: dict) -> dict:
    issue = store.create_issue(params["repoId"], params["title"], params["body"])
    return {"found": issue is not None, "issue": issue}


def _delete_repo(params: dict) -> dict:
    return {"found": store.delete_repo(params["id"])}


EXECUTORS = {
    "list_repos": _list_repos,
    "get_repo": _get_repo,
    "create_issue": _create_issue,
    "delete_repo": _delete_repo,
}
