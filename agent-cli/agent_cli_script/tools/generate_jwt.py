"""The one custom LLM tool: a thin wrapper around session.py. The signing secret lives only on
the backend, so this process can't forge a token. Cheap to call — returns the cached token if
still valid, else fetches a new one.

Faithful Python port of tools/generateJwt.ts, exposed as an in-process MCP server; the model
sees it as `mcp__jwt__GenerateJWT`.
"""

from datetime import datetime, timezone

from claude_agent_sdk import tool, create_sdk_mcp_server

from ..session import get_valid_token, get_backend_url

_DESCRIPTION = (
    "Obtain (or refresh) a short-lived JWT for this agent from the backend's token endpoint. "
    "Safe and cheap to call any time: if a valid token is already cached for this session, it "
    "is returned immediately with no network call. Send the returned token as "
    "`Authorization: Bearer <token>` on every curl call to the backend (except the human "
    "reviewer's resolve/ endpoint, which this agent never calls). If any backend call ever "
    "returns HTTP 401, call this tool again with refresh:true to force a new token, then retry "
    "the call that failed — the session should never simply break on token expiry."
)


@tool("GenerateJWT", _DESCRIPTION, {"refresh": bool})
async def generate_jwt(args: dict) -> dict:
    try:
        refresh = bool(args.get("refresh"))
        cached = await get_valid_token(force_refresh=refresh)
        expires_iso = datetime.fromtimestamp(
            cached.expires_at / 1000, tz=timezone.utc
        ).isoformat()
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"Token: {cached.token}\n"
                        f"Expires: {expires_iso}\n"
                        f"BACKEND_URL: {get_backend_url()}"
                    ),
                }
            ]
        }
    except Exception as err:  # noqa: BLE001 — surface any failure back to the model as text
        return {"content": [{"type": "text", "text": str(err)}], "is_error": True}


# Exposed as an in-process MCP server; the model sees it as `mcp__jwt__GenerateJWT`.
jwt_server = create_sdk_mcp_server(name="jwt", version="1.0.0", tools=[generate_jwt])
