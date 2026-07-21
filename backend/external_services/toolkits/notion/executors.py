"""The `notion` toolkit's executors — each is a thin adapter from the registry's generic
(params: dict) -> dict shape onto `client.py`'s real Notion calls. Same `EXECUTORS` export
contract as every other toolkit package."""

from . import client


def _list_pages(params: dict) -> dict:
    return client.list_pages()


def _create_page(params: dict) -> dict:
    return client.create_page(params["title"], params["content"])


def _update_page(params: dict) -> dict:
    return client.update_page(params["id"], params.get("title"), params.get("content"))


def _delete_page(params: dict) -> dict:
    return client.archive_page(params["id"])


EXECUTORS = {
    "list_pages": _list_pages,
    "create_page": _create_page,
    "update_page": _update_page,
    "delete_page": _delete_page,
}
