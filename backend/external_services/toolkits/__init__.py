"""Toolkit registry — the ONE file to touch when wiring in a new code-defined toolkit.

Each toolkit is a self-contained package under toolkits/<slug>/ with two required modules:
  catalog.py    — pure data: the toolkit's slug, name, description, and its actions' schemas and
                  default permissions, as `TOOLKIT = {...}`. This is what `seed` registers into
                  the Toolkit/Action tables.
  executors.py  — the actual side-effecting code, one function per action, exported as
                  `EXECUTORS = {action_slug: fn(params, context)}`.

A live external service may also have its own client.py — a plain HTTP wrapper that executors.py
calls into. That's private to that one toolkit package.

To add a new code-defined toolkit:
  1. Create toolkits/<slug>/{catalog.py, executors.py} (+ client.py if it's a live integration).
  2. Add one import + one entry to `_MODULES` below.
  3. Run `python manage.py seed`.

Everything else — permissions.py, audit.py, authentication.py, validation.py, every view — stays
untouched. They only ever operate on generic Action/params and never know a toolkit slug exists;
this file (plus execute_action's dispatch) is the sole place that bridges "which toolkit" to
"which code actually runs."

**Most toolkits no longer arrive this way.** A user can plug in their own MCP server at runtime
through the `mcp` toolkit's own actions, which discovers that server's tools and writes them into
the same Toolkit/Action tables. Those toolkits have no package here at all: `execute_action` routes
them straight to the remote server. `_MODULES` is now just the built-ins — `items` as a mock
external service, and `mcp` as the toolkit that manages the others.
"""

from dataclasses import dataclass

from .items import catalog as items_catalog
from .items import executors as items_executors
from .mcp import catalog as mcp_catalog
from .mcp import client as mcp_client
from .mcp import executors as mcp_executors

_MODULES = [
    (items_catalog, items_executors),
    (mcp_catalog, mcp_executors),
]

TOOLKITS = [catalog.TOOLKIT for catalog, _executors in _MODULES]

_EXECUTORS = {
    f"{catalog.TOOLKIT['slug']}.{action_slug}": fn
    for catalog, executors in _MODULES
    for action_slug, fn in executors.EXECUTORS.items()
}


@dataclass(frozen=True)
class ExecutionContext:
    """Who an action is running for.

    Executors that reach an external service ignore this; the `mcp` toolkit needs it, because
    "which servers exist" is a per-user question. Passing it explicitly keeps executors free of
    any dependency on the request or on DRF."""

    agent: object
    user: object


def execute_action(action, params: dict, context: ExecutionContext) -> dict:
    """Run one action: either a code-defined executor, or a proxied call to a user's MCP server.

    The MCP branch is checked **first**, deliberately. Built-in dispatch is keyed by
    "<toolkit>.<action>", and toolkit slugs are only unique per owner for user-registered servers —
    so resolving built-ins first would let someone shadow a built-in action by naming their server
    after it. Routing on the toolkit's own `mcp_server` link removes the ambiguity entirely.
    (`add_server` also refuses built-in names, so this is belt and braces.)"""
    server = getattr(action.toolkit, "mcp_server", None)
    if server is not None:
        return mcp_client.call_tool(server, action.remote_name or action.slug, params)

    executor = _EXECUTORS.get(f"{action.toolkit.slug}.{action.slug}")
    if executor is None:
        raise ValueError(
            f'No executor registered for action "{action.toolkit.slug}.{action.slug}"'
        )
    return executor(params, context)
