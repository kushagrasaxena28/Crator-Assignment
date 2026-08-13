"""In-memory store for the `items` mock toolkit — it stands in for an external service's own data.
State lives only in this process's memory and resets on restart; that's expected of a mock external
service, not a bug — a real `items` MCP server would own its data itself, and this database would
never be where it lived.
"""

from uuid import uuid7

_SAMPLE_ITEM_ID = "10000000-0000-0000-0000-000000000001"

_items: dict[str, dict] = {
    _SAMPLE_ITEM_ID: {
        "id": _SAMPLE_ITEM_ID,
        "name": "Sample Item",
        "content": "This item exists so read_item has something to fetch on the first demo run.",
    },
}


def get(item_id: str) -> dict | None:
    return _items.get(item_id)


def create(name: str, content: str) -> dict:
    item_id = str(uuid7())
    item = {"id": item_id, "name": name, "content": content}
    _items[item_id] = item
    return item


def update(item_id: str, name: str | None, content: str | None) -> dict | None:
    item = _items.get(item_id)
    if item is None:
        return None
    if name is not None:
        item["name"] = name
    if content is not None:
        item["content"] = content
    return item


def delete(item_id: str) -> bool:
    return _items.pop(item_id, None) is not None
