# Agent CLI — the Claude Agent SDK client

An interactive command-line agent, built on the **Claude Agent SDK**, that plays the *agent* in
this system. You talk to it in plain language; it discovers what toolkits and actions it can use
and calls them — always through the backend's HTTP boundary, so the backend's permission and audit
logic runs on **every single call**.

The agent has the SDK's standard built-in tools (**Read, Write, Edit, Bash**) plus exactly **one**
custom tool, **`GenerateJWT`**. It is deliberately given **no** direct `read_item` / `create_item`
/ etc. tool — the only way it can touch an external service is `Bash` + `curl` against the backend.
That is the whole point: funnelling every call through one HTTP boundary is what lets the backend
enforce permissions and record an audit trail on each attempt.

**Stack:** Python · Claude Agent SDK · PyJWT · httpx · python-dotenv. This folder is a plain Python
package (no Django, no `manage.py`) — it is the backend's *client* and works the same regardless of
how the backend is implemented.

---

## Prerequisites

- **Python 3.11+**
- **Node.js** and the `claude` CLI on your `PATH` — the Claude Agent SDK drives Claude Code as a
  subprocess. Install it with `npm install -g @anthropic-ai/claude-code` if you don't already have
  it.
- The **backend running** (see [`../backend-django`](../backend-django)) at
  `http://localhost:8000` — or wherever `BACKEND_URL` points — seeded with the demo agent whose
  UUID is in `AGENT_ID`.

---

## Setup

```bash
cd agent-cli-script
python3 -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env                # then edit .env
```

`.env` values:

| Key | Purpose |
|---|---|
| `BACKEND_URL` | Where the backend lives (default `http://localhost:8000`). |
| `AGENT_ID` | The seeded demo agent's UUID. The default already matches what `python manage.py seed` creates, so it works unmodified. |
| `ANTHROPIC_API_KEY` | **Required** on any machine that doesn't already have a logged-in `claude` CLI (assume you need it). Get one at <https://console.anthropic.com/settings/keys>. The SDK's subprocess inherits this process's environment, so once it's here nothing else needs wiring. |

> If you're relying on an existing `claude` login instead of a key, leave the `ANTHROPIC_API_KEY`
> line **commented out** — an uncommented-but-empty value is not the same as unset.

---

## Run

With the backend running:

```bash
python -m agent_cli_script
```

You'll see:

```
Establishing session...
Session established. Token valid until 14:32:10.
Type your request ("exit" to quit).

User:
```

A backend session (a JWT) is established *before* the first prompt, so the agent is ready to act on
your very first message. The conversation keeps its history for as long as the process runs. Type
`exit` / `quit` / Ctrl-D to leave.

### Try the full permission demo

```
User: what can I access?
User: read the sample item
User: create an item called "Meeting Notes" with content "Discuss Q3 roadmap"
User: delete the sample item
```

You'll see all three permission outcomes surfaced in plain language:

- **always_allow** → runs immediately (`read_item`)
- **requires_approval** → returns a ticket id and waits for a human (`create_item`)
- **always_deny** → refused (`delete_item`)

Approve or reject a pending ticket from a **separate terminal** — the reviewer's endpoint uses
admin credentials, not the agent's JWT (the agent can never resolve its own tickets):

```bash
curl -X PATCH http://localhost:8000/api/external-services/approvals/<ticket_id>/resolve/ \
  -u admin:<ADMIN_PASSWORD from backend-django/.env> \
  -H "Content-Type: application/json" \
  -d '{"decision":"approved"}'
# or reject: -d '{"decision":"rejected","reason":"not needed"}'
```

The moment you resolve it, the CLI's **background ticket watcher** notices on its own — it polls
the backend directly, independent of the conversation — and prints an unprompted notification
within ~2 seconds, even if you're mid-typing:

```
● Ticket 81d8f17c was approved.
  Result: {"item": {"id": "...", "name": "Meeting Notes", ...}}
```

You can then ask, in the same session, `check on that ticket`, and the agent will report the real
outcome from the backend.

---

## How it works

```
agent_cli_script/
├── __main__.py         entry point: the REPL loop, live token streaming, orchestration
├── system_prompt.py    the agent's operating instructions (kept separate so __main__ stays short)
├── session.py          JWT fetch + in-memory cache + transparent refresh
├── ticket_watcher.py   background poller for approval-ticket status changes
└── tools/
    └── generate_jwt.py  the one custom tool exposed to the model (in-process MCP server)
```

Each module has one job, and the seams are clean: `session.py` doesn't know about the CLI or the
tools; `__main__.py` doesn't know *how* tokens are fetched, only that `get_valid_token()` returns
one.

A few design points worth calling out:

- **The signing secret never lives here.** `GenerateJWT` doesn't sign anything — it asks the
  backend's `/api/auth/token/` endpoint for a token and caches it. This process *cannot* forge a
  token; it can only request one for an agent the backend already knows.
- **Long sessions survive token expiry.** The cache refreshes ~60s before the ~20-minute expiry,
  and any `401` triggers a forced refresh + retry, so a token expiring mid-conversation never ends
  the session.
- **The watcher is independent of the model.** Approvals reach you whether or not you ask the agent
  to check — the polling loop runs on its own asyncio task.
- **`bypassPermissions`** is used for the SDK's *local* tool-approval prompt only. It's safe here
  because the agent only ever curls `localhost`; the real permission enforcement is the backend's,
  not the SDK's.

---

## Notes & assumptions

- The CLI **never** calls the `resolve/` endpoint itself — resolving a ticket is a human action, by
  design. The system prompt instructs the agent to refuse "just approve it" requests and explain
  that a human with admin access must do it.
- `PyJWT` is used only as a fallback to read a token's `exp` claim when the backend's token response
  omits `expires_at`; it never signs or verifies (the secret is backend-only).
- `.env` is git-ignored — anything you put there is never committed.
