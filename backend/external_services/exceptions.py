"""Custom DRF exception handler.

Keeps error bodies in the `{error, message}` shape the rest of the API uses, and turns any
unexpected (non-DRF) exception — e.g. a failed live Notion call — into a clean JSON 500 instead
of an HTML error page. Unexpected errors are reported to the console as a single styled line
(matching the audit console's look), not a multi-line traceback, so backend output stays readable.
"""

from datetime import datetime

from rest_framework.response import Response
from rest_framework.views import exception_handler

_RESET = "\x1b[0m"
_DIM = "\x1b[2m"
_RED = "\x1b[31m"


def _log_unexpected(context, exc) -> None:
    request = context.get("request")
    where = f"{request.method} {request.path}" if request is not None else context.get("view")
    time_str = datetime.now().strftime("%H:%M:%S")
    print(
        f"{_DIM}[{time_str}]{_RESET} {_RED}✗ ERROR{_RESET} {where} "
        f"{_DIM}—{_RESET} {_RED}{exc}{_RESET}",
        flush=True,
    )


def api_exception_handler(exc, context):
    response = exception_handler(exc, context)

    if response is None:
        # A non-DRF exception (e.g. a live Notion call failing). Report one clean line — no
        # traceback — and return a generic JSON 500. No audit row is written for these, since an
        # unexpected system failure isn't a permission-relevant outcome.
        _log_unexpected(context, exc)
        return Response({"error": "internal_error", "message": "Something went wrong."}, status=500)

    # Reshape DRF's default {"detail": ...} into our {error, message} convention.
    if isinstance(response.data, dict) and "detail" in response.data:
        codes = exc.get_codes() if hasattr(exc, "get_codes") else None
        code = codes if isinstance(codes, str) else "error"
        response.data = {"error": code, "message": str(response.data["detail"])}
    return response
