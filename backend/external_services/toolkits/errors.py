"""The one exception type executors raise when the caller can do something about the failure.

The distinction matters at the API boundary. An agent that asks to plug in a server and gets back
"the action failed" has nothing to tell the user and nothing to retry differently; one that gets
"you already have a server called demo" can act. So a failure that names a *caller* problem — a bad
name, a URL this service won't fetch, an unreachable server — has its message returned, while
anything unexpected stays a generic 500-class response with the detail confined to the audit log.

Lives in its own module rather than in `toolkits/__init__.py` so that `client.py` can raise it
without importing the package that imports `client`.
"""


class ExecutorError(Exception):
    """A failure whose message is safe and useful to return to the caller."""


class InvalidRequest(ExecutorError):
    """The caller's input was wrong and could be corrected on a retry — surfaces as a 400."""
