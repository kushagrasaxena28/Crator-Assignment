"""agent-cli — the Claude Agent SDK client that plays the agent in this system.

Uses the SDK's standard built-in tools (Read, Write, Edit, Bash) plus a single custom LLM tool,
GenerateJWT. Every external-service action goes through the permissions backend over HTTP
(Bash + curl), never as a direct tool call. This package is a plain Python script — it has no
Django code of its own; it's the client that talks to whichever backend implements the
permissions API.
"""
