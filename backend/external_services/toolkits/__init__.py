"""Toolkit registry — the ONE file to touch when wiring in a new toolkit/MCP.

Each toolkit is a self-contained package under toolkits/<slug>/ with two required modules:
  catalog.py    — pure data: the toolkit's slug, name, description, and its actions' schemas and
                  default permissions, as `TOOLKIT = {...}`. This is what `seed` registers into
                  the Toolkit/Action tables.
  executors.py  — the actual side-effecting code, one function per action, exported as
                  `EXECUTORS = {action_slug: function}`.

A live external API (like notion/) may also have its own client.py — a plain HTTP wrapper that
executors.py calls into. That's private to that one toolkit package; nothing outside it needs to
know the client exists.

To add a new toolkit/MCP:
  1. Create toolkits/<slug>/{catalog.py, executors.py} (+ client.py if it's a live integration).
  2. Add one import + one entry to `_MODULES` below.
  3. Run `python manage.py seed`.

That's it — permissions.py, audit.py, authentication.py, validation.py, and every view stay
untouched. They only ever operate on generic Action/params and never know a toolkit slug exists;
this file (plus execute_action's dispatch) is the sole place that bridges "which toolkit" to
"which code actually runs."
"""

from .github import catalog as github_catalog
from .github import executors as github_executors
from .items import catalog as items_catalog
from .items import executors as items_executors
from .notion import catalog as notion_catalog
from .notion import executors as notion_executors

_MODULES = [
    (items_catalog, items_executors),
    (github_catalog, github_executors),
    (notion_catalog, notion_executors),
]

TOOLKITS = [catalog.TOOLKIT for catalog, _executors in _MODULES]

_EXECUTORS = {
    f"{catalog.TOOLKIT['slug']}.{action_slug}": fn
    for catalog, executors in _MODULES
    for action_slug, fn in executors.EXECUTORS.items()
}


def execute_action(toolkit_slug: str, action_slug: str, params: dict) -> dict:
    executor = _EXECUTORS.get(f"{toolkit_slug}.{action_slug}")
    if executor is None:
        raise ValueError(f'No executor registered for action "{toolkit_slug}.{action_slug}"')
    return executor(params)
