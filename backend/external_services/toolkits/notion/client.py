"""Thin wrapper around Notion's real REST API — the one toolkit that genuinely reaches an
external service. Credentials are read lazily (only when a notion.* action actually runs), so an
unconfigured Notion never stops items/github from working; it just fails that one call cleanly.
"""

from django.conf import settings

import requests

_API_BASE = "https://api.notion.com/v1"
# Pinned rather than configurable — Notion API versions are stable, dated snapshots.
_VERSION = "2022-06-28"


def _headers() -> dict:
    if not settings.NOTION_API_KEY:
        raise RuntimeError("NOTION_API_KEY is not set — cannot reach Notion. See .env.example.")
    return {
        "Authorization": f"Bearer {settings.NOTION_API_KEY}",
        "Notion-Version": _VERSION,
        "Content-Type": "application/json",
    }


def _extract_title(page: dict) -> str:
    """A page's title lives under whichever property has type "title" (the key varies — "title"
    on a plain page, often "Name" on a database row), so we find it by type, not by a fixed key."""
    properties = page.get("properties") or {}
    for value in properties.values():
        if value.get("type") == "title":
            return "".join(part.get("plain_text", "") for part in value.get("title", []))
    return "(untitled)"


def _to_page_shape(page: dict) -> dict:
    return {
        "id": page["id"],
        "title": _extract_title(page),
        "url": page.get("url"),
        "archived": page.get("archived"),
    }


def _get_page_raw(page_id: str):
    response = requests.get(f"{_API_BASE}/pages/{page_id}", headers=_headers())
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()


def list_pages() -> dict:
    response = requests.post(
        f"{_API_BASE}/search",
        headers=_headers(),
        json={"filter": {"value": "page", "property": "object"}},
    )
    response.raise_for_status()
    results = response.json().get("results", [])
    return {"pages": [_to_page_shape(page) for page in results]}


def create_page(title: str, content: str) -> dict:
    parent_page_id = settings.NOTION_PARENT_PAGE_ID
    if not parent_page_id:
        raise RuntimeError("NOTION_PARENT_PAGE_ID is not set — cannot create a page. See .env.example.")

    children = (
        [{
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": content}}]},
        }]
        if content
        else []
    )
    response = requests.post(
        f"{_API_BASE}/pages",
        headers=_headers(),
        json={
            "parent": {"page_id": parent_page_id},
            "properties": {"title": {"title": [{"text": {"content": title}}]}},
            "children": children,
        },
    )
    response.raise_for_status()
    return {"page": _to_page_shape(response.json())}


def update_page(page_id: str, title=None, content=None) -> dict:
    """`content`, if given, is appended as a new paragraph block (not a full-content replace) —
    enough to prove the page genuinely mutates without diffing blocks."""
    if _get_page_raw(page_id) is None:
        return {"found": False, "page": None}

    if title is not None:
        response = requests.patch(
            f"{_API_BASE}/pages/{page_id}",
            headers=_headers(),
            json={"properties": {"title": {"title": [{"text": {"content": title}}]}}},
        )
        response.raise_for_status()
    if content is not None:
        response = requests.patch(
            f"{_API_BASE}/blocks/{page_id}/children",
            headers=_headers(),
            json={"children": [{
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": content}}]},
            }]},
        )
        response.raise_for_status()

    return {"found": True, "page": _to_page_shape(_get_page_raw(page_id))}


def archive_page(page_id: str) -> dict:
    """Notion's API has no permanent delete for pages; archiving (the trash icon) is the closest
    an integration can do."""
    if _get_page_raw(page_id) is None:
        return {"found": False}
    response = requests.patch(
        f"{_API_BASE}/pages/{page_id}",
        headers=_headers(),
        json={"archived": True},
    )
    response.raise_for_status()
    return {"found": True}
