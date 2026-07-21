"""The `items` toolkit's actual side-effecting code — reads and writes its own in-memory store
(store.py), simulating a mock external service.

`EXECUTORS` is the contract every toolkit package exports: a {action_slug: function} dict that
`toolkits/__init__.py` folds into the registry under this toolkit's slug.
"""

from . import store


def _read_item(params: dict) -> dict:
    item = store.get(params["id"])
    return {"found": item is not None, "item": item}


def _create_item(params: dict) -> dict:
    return {"item": store.create(params["name"], params["content"])}


def _update_item(params: dict) -> dict:
    item = store.update(params["id"], params.get("name"), params.get("content"))
    return {"found": item is not None, "item": item}


def _delete_item(params: dict) -> dict:
    return {"found": store.delete(params["id"])}


EXECUTORS = {
    "read_item": _read_item,
    "create_item": _create_item,
    "update_item": _update_item,
    "delete_item": _delete_item,
}
