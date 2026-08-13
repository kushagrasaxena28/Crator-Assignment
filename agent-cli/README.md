# Agent CLI — the Claude Agent SDK client

An interactive command-line agent, built on the **Claude Agent SDK**, that plays the *agent* in this
system. You talk to it in plain language; it discovers what toolkits and actions it can use and
calls them — always through the backend's HTTP boundary, so the backend's permission and audit logic
runs on **every single call**.

The agent has the SDK's standard built-in tools (**Read, Write, Edit, Bash**) plus exactly **one**
custom tool, **`GenerateJWT`**. It is deliberately given **no** direct `read_item` / `create_item` /
etc. tool — the only way it can touch an external service is `Bash` + `curl` against the backend.
That is the whole point: funnelling every call through one HTTP boundary is what lets the backend
enforce permissions and record an audit trail on each attempt.

**Stack:** Python 3.14 · Claude Agent SDK · PyJWT · httpx · python-dotenv. This folder is a plain
Python package (no Django, no `manage.py`) — it is the backend's *client*.

> The feature work and the end-to-end flow are in the [top-level README](../README.md). This
> document is the CLI reference, plus what changed since the last commit.

---

## Fixes since the last commit

**Configuration was loaded relative to the current directory.** `load_dotenv()` searched upward from
wherever the process happened to start, so running the CLI from anywhere other than `agent-cli/`
silently found no configuration and failed as if nothing were set. It now loads from a path anchored
to the module, so the behaviour is the same whichever directory you launch from.

**An unedited `.env` failed with an opaque `401`.** `cp .env.example .env` leaves `AGENT_SECRET` as
a placeholder, and the backend deliberately returns one generic `invalid_credentials` for every
failed exchange so that agent ids cannot be enumerated. The result was a message that told you
nothing. The CLI now recognises the placeholder before making any request and says exactly what to
replace and where. A wrong-but-real secret gets its own message, naming the most likely cause: a
re-seeded database issues a new one.

**An unedited `ANTHROPIC_API_KEY` broke a setup that would otherwise have worked.** `.env.example`
shipped the key line *uncommented* with a placeholder value. The SDK treats any set value as a
configured credential and stops falling back to your `claude` CLI login — so copying the example and
not editing it was the one configuration that could not work, while looking correct. The line now
ships commented out, with `claude auth status` documented as the check.

**Comments referred to files that do not exist.** Several modules described themselves as a
"Faithful Python port of session.ts" / "ticketWatcher.ts" — there is no TypeScript in this
repository. Removed.

**Broken links.** This README and the top-level one referred to `agent-cli-script/`; the directory
is `agent-cli/`.

---

## The credential boundary

This CLI authenticates as an **agent**, using an agent id and an agent secret. It does **not** hold
the owning user's password, and it must never be given one.

The reason is concrete rather than theoretical: this process runs a model with shell access, so
anything readable from here is readable by the agent. If a user password lived in `.env`, the agent
could read it, mint a **user** token, and approve its own held actions — defeating the entire
approval queue. An agent secret can only ever be exchanged for an agent token, and the backend
rejects an agent token at `resolve/`.

So approving a ticket is something *you* do, from your own terminal, with your own credentials.

---

## Prerequisites

- **Python 3.14+** (same floor as the backend). On macOS: `brew install python@3.14`.
- **Node.js** and the `claude` CLI on your `PATH` — the SDK drives Claude Code as a subprocess.
  Install with `npm install -g @anthropic-ai/claude-code`.
- The **backend running** (see [`../backend`](../backend)) and **already seeded** — the agent
  authenticates with a secret that does not exist until `python manage.py seed` has run.

## Setup

```bash
cd agent-cli
python3.14 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
```

Then edit `.env`:

| Key | Purpose |
|---|---|
| `BACKEND_URL` | Where the backend lives (default `http://localhost:8000`) |
| `AGENT_ID` | The seeded demo agent's UUID — the default already matches `seed`, so leave it |
| `AGENT_SECRET` | **You must paste this.** `seed` prints the exact `AGENT_SECRET="…"` line to copy |
| `ANTHROPIC_API_KEY` | **Only if** `claude auth status` says you are not logged in. Ships commented out; leave it that way if you are |

Never add the user's password to this file — see [the credential boundary](#the-credential-boundary).

```bash
python -m agent_cli_script
```

You should see `Session established. Token valid until …`. If instead it tells you `AGENT_SECRET`
is still the placeholder, you skipped the paste step above.

---

## Try it

### Plug in an MCP server

```
User: add an mcp server called deepwiki at https://mcp.deepwiki.com/mcp
User: what toolkits do I have now?
User: using deepwiki, what does the pallets/flask documentation cover?
```

The agent asks for whatever it still needs (a short name, the URL, auth headers if the server wants
them), registers it, and its tools are usable immediately — through the same permission and audit
path as everything else.

`mcp.deepwiki.com` needs no authentication, which makes it the easiest thing to try. Servers wanting
a static bearer token work too — give the agent `{"Authorization": "Bearer <token>"}` and it
forwards it (GitHub's MCP accepts a personal access token this way). Servers behind an interactive
**OAuth** flow, such as Notion's, cannot be added: that needs a browser and a callback URL. The
agent will tell you which case you have hit.

### The permission demo

```
User: read the sample item                 # always_allow     → runs immediately
User: create an item called "Meeting Notes" # requires_approval → ticket id, waits for a human
User: delete the sample item               # always_deny      → refused
```

Approve a pending ticket from a **separate terminal**, as yourself. The agent cannot — its token is
the wrong type and the backend rejects it:

```bash
BASE=http://localhost:8000
UTOK=$(curl -s -X POST $BASE/api/auth/user/token/ -H 'Content-Type: application/json' \
  -d '{"username":"demo","password":"<the password seed printed>"}' \
  | python -c 'import sys,json;print(json.load(sys.stdin)["token"])')

curl -X PATCH $BASE/api/external-services/approvals/<ticket_id>/resolve/ \
  -H "Authorization: Bearer $UTOK" -H "Content-Type: application/json" \
  -d '{"decision":"approved"}'
```

The moment you resolve it, the CLI's **background ticket watcher** notices on its own — it polls the
backend directly, independent of the conversation — and prints an unprompted notification within ~2
seconds, even if you are mid-typing:

```
● Ticket 81d8f17c was approved.
  Result: {"item": {"id": "...", "name": "Meeting Notes", ...}}
```

---

## How it works

```
agent_cli_script/
├── __main__.py         entry point: the REPL loop, live token streaming, orchestration
├── system_prompt.py    the agent's operating instructions
├── session.py          agent-token fetch + in-memory cache + transparent refresh
├── ticket_watcher.py   background poller for approval-ticket status changes
└── tools/
    └── generate_jwt.py the one custom tool exposed to the model (in-process MCP server)
```

- **The signing secret never lives here.** `GenerateJWT` does not sign anything — it exchanges the
  agent id and secret at `/api/auth/agent/token/` and caches the result. This process cannot forge a
  token, and cannot obtain one of a different type.
- **Long sessions survive token expiry.** The cache refreshes ~60s before the ~20-minute expiry, and
  any `401` triggers a forced refresh and retry.
- **The watcher is independent of the model.** Approvals reach you whether or not you ask the agent
  to check.
- **`bypassPermissions`** disables the SDK's *local* tool prompt only. It is safe because the agent
  only ever curls the backend; the real enforcement is server-side. That is the whole thesis: never
  trust the client.

---

## Notes

- The CLI **never** calls `resolve/` itself. The system prompt tells the agent to refuse "just
  approve it" requests — but note that **the prompt is not the control**: the authentication class
  is. A prompt is advisory; the `401` is enforcement.
- `PyJWT` is used only to read a token's `exp` claim when the backend's response omits `expires_at`.
  It never signs or verifies.
- The ticket watcher finds ticket ids by regex-scanning the model's text output. It works, but it is
  the weakest seam here: if the model paraphrases or truncates an id, the watcher misses it.
  Returning ticket ids structurally from the tool layer would be the cleaner design.
- `.env` is git-ignored.
