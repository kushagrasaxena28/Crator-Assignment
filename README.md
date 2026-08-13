# External Services Permissions Layer for AI Agents

A permissions-and-audit layer between AI agents and the external services they call. Every action
goes through **one HTTP boundary**, is written to an **immutable audit log**, and sensitive actions
are held for **human approval**.

| Folder | What it is |
|---|---|
| [`backend/`](backend) | The permissions layer — Django + DRF REST API |
| [`agent-cli/`](agent-cli) | A Claude Agent SDK CLI that plays the *agent* |

---

# The feature: plug in any MCP server, from chat

The original submission shipped three toolkits hardcoded in Python (`items`, `github`, `notion`).
Adding a fourth meant writing a package and redeploying. **Now a user connects any MCP server by
asking the agent to, in plain language, and its tools become usable immediately.**

```
User:  add the github mcp server, my token is ghp_xxxxx
Agent: Added github — its tools are available now.

User:  what can I access?
Agent: items, mcp, and github (yours — nobody else can see it)

User:  list the open issues on my repo
Agent: [calls github.list_issues through the same permission + audit path as everything else]
```

`github` and `notion` were **deleted** from the codebase to prove the point: they come back through
this feature instead of being built in.

**Why chat, not a UI.** Configuring a server (name, URL, auth headers) is naturally a form, but
building one would mean adding the one interface the brief is explicitly *not* about — everything
else in this system is deliberately agent-driven, with no web frontend at all. Doing it through the
agent keeps that in the spirit of the assignment: registration is just another action the agent
calls, gated by the same permission and audit path as everything else, so the one new surface is
zero new surface.

## How I arrived at the design

Started from Notion's MCP integration docs and the spec itself, to see what a client actually has
to implement rather than assume. The one thing that shaped the whole design: a server's `tools/list`
returns `name` / `description` / `inputSchema` per tool, and `Action.input_schema` was already a
JSON Schema column being validated against on every call. So discovery is a *mapping* onto tables
that already existed, not a new subsystem:

```
MCP tools/list          →   this system's existing tables
  tool.name              →     Action.remote_name  (+ a URL-safe Action.slug)
  tool.description       →     Action.description
  tool.inputSchema       →     Action.input_schema   ← already validated on every call
```

The client also handles the two protocol eras and session handling MCP defines — not detailed here
since that's spec mechanics, not design. What actually decided whether a given real server could be
used at all was authentication — see below.

## What is handled, and what is not

| Case | Supported | Notes |
|---|---|---|
| **Serverless / remote HTTP servers** | ✅ | The only transport. Reached over the network like any API. |
| **No authentication** | ✅ | e.g. `https://mcp.deepwiki.com/mcp` — add the URL and it works. |
| **Static bearer token** | ✅ | Any server accepting `Authorization: Bearer <token>`. **GitHub works this way with a personal access token.** The header is stored and forwarded verbatim. |
| **Interactive OAuth 2.1** | ❌ detected, not performed | Notion's server is OAuth-only, which needs a browser and a callback URL — no surface for that in a chat-driven backend. The `WWW-Authenticate` header is parsed and the user is told plainly what the server wants instead of a silent failure. |
| **Local `stdio` servers** (`npx …`) | ❌ deliberately | Would mean the backend spawning an agent-chosen command — remote code execution, not a missing feature. |

## What happens when you add GitHub

One HTTP call from the agent. Everything below happens inside it:

```
Agent → POST /toolkits/mcp/actions/add_server/call/
        { "params": { "name": "github",
                      "url": "https://api.githubcopilot.com/mcp/",
                      "headers": { "Authorization": "Bearer ghp_xxx" } } }

  1. authenticate      the agent's JWT → which agent, and which user it acts for
  2. validate          params against add_server's own stored JSON Schema
  3. check permission  add_server is always_allow → proceed
  4. guard the URL     https? public IP? not a private/metadata address? → connect
  5. talk MCP          initialize → tools/list   (sending the Authorization header)
  6. write the catalog MCPServer row  +  Toolkit row  +  one Action row per tool
  7. audit             one row, with the bearer token redacted

← 200 { "toolkit": "github", "tool_count": <however many it exposes>, "tools": [...] }
```

Step 6 is the whole idea. GitHub's tools become **ordinary `Action` rows**, indistinguishable from
the built-in ones. Nothing downstream knows they came from a remote server:

```
Toolkit(slug="github", mcp_server=→MCPServer)
  ├─ Action(slug="list_issues",  remote_name="list_issues",  input_schema={…}, default_permission="always_allow")
  ├─ Action(slug="create_issue", remote_name="create_issue", …)
  └─ … one per tool
```

So the next time the agent lists toolkits, `github` is simply there. Calling `github.list_issues`
runs the same five steps as `items.read_item` — the only difference is step 5, where instead of a
local Python function the backend forwards the call to GitHub as MCP `tools/call`.

**Scoping:** that toolkit belongs to *your user*. Another user's agent cannot see it in the catalog,
and gets a `404` if it guesses the URL — which matters, because the server row holds your token.

**Unplugging:** `disable_server` hides the toolkit and makes its actions uncallable while deleting
nothing, so it can be switched back on. `remove_server` deletes it for good and is the only action
here that requires human approval, because deleting a toolkit cascades to its permission overrides
and pending tickets. Audit history survives either way.

---

# Architecture changes from the interview discussion

These came out of our conversation rather than the feature request. They were worth doing first,
because the feature could not be built cleanly on the old model — an MCP server has to *belong* to
somebody, and there was no "somebody" in the schema.

### 1. A real user identity, using Django's own auth

Previously `Agent` was the only identity, and approvals were gated by one shared admin password in
an env file. Now `User` is a Django `AbstractUser` wired up as `AUTH_USER_MODEL`, so password
hashing, the password validators and `createsuperuser` all work the normal way — and the account
that logs into the admin is the same account the API authenticates. One user owns many agents.

### 2. Two separate JWTs — user and agent

| | **User token** | **Agent token** |
|---|---|---|
| Who | a human | a program acting for that human |
| Gets one by | username + password | agent id + **agent secret** |
| Claim | `typ: "user"` | `typ: "agent"` |
| Can call tools | no | **yes** |
| Can approve / set permissions | **yes** | no |

Every token says which identity it is, and every endpoint says which it accepts. Presenting an
agent token to the approval endpoint is a `401` — **this is the check that makes the approval queue
mean anything.** An agent that could authenticate as its own owner could sign off on its own work.

The same reasoning is why the agent's `.env` holds an **agent secret and never the user's
password**: the agent has shell access, so anything in that file is readable by the model, and an
agent secret can only ever yield an agent token.

Agents also got a real credential. Previously anyone who knew an agent's UUID could mint a token for
it — but a UUID is an identifier, not a secret: it appears in URLs, in every audit row, and in
config files. Token issuance is now a credential exchange against a hashed `Agent.secret_hash`.

### 3. UUIDv7 instead of UUIDv4

Every generated id is now `uuid.uuid7()` — same 128 bits, same safety in URLs, but time-ordered.
Inserts append to the right edge of the index instead of scattering across the B-tree. `audit_log`
is where that matters: append-only, ever-growing, always read newest-first. (This is why the project
requires Python 3.14 — `uuid7()` entered the standard library there.)

---

# Schema: before and after

**Before** — six tables, one identity:

```
Agent ─┬─ PermissionOverride ─┐
       ├─ ApprovalTicket ─────┼─ Action ── Toolkit
       └─ (AuditLog: no FK, snapshots agent_id / agent_name)
```

**After** — nine tables. New in bold:

```
**User** ─┬─ Agent ─┬─ PermissionOverride ─┐
          │         └─ ApprovalTicket ─────┼─ Action ── Toolkit ──→ **MCPServer**
          └─ **MCPServer** (owns)                                   (NULL = built-in)

**PolicyDefault** — one row: the permission newly discovered MCP tools get
AuditLog — no FK to anything; snapshots agent_id/agent_name **+ user_id/user_name**
```

| Change | Why |
|---|---|
| **+ `User`** | Someone has to own agents and MCP servers, and approve tickets |
| **+ `MCPServer`** | A plugged-in server's URL, headers, enabled flag. Kept separate from `Toolkit` because the catalog is rebuilt on every re-discovery while the connection and its credentials persist — and credentials do not belong in the table the catalog endpoints serve |
| **+ `PolicyDefault`** | One row: the default permission for newly-discovered MCP tools, DB-configurable instead of hardcoded |
| `Agent` **+ `owner`, `+ secret_hash`** | Ownership, and a real credential instead of a bare UUID |
| `Toolkit` **+ `mcp_server`** | `NULL` = built-in, visible to everyone; set = discovered, visible only to its owner |
| `Action` **+ `remote_name`** | MCP tool names may contain dots (`admin.tools.list`), which a URL segment cannot. `slug` is the sanitized routing name; `remote_name` is what gets sent back to the server |
| `ApprovalTicket` **+ `requested_by_user`** | Only the user who raised a ticket may approve it. Stored rather than derived, so re-assigning an agent cannot hand a stranger authority over work already in flight |
| `AuditLog` **+ `user_id`, `user_name`** | Every row now names both identities. Still no foreign keys — snapshots survive deletion of either |

**When rows are written:** `MCPServer` on `add_server`, updated on enable/disable/refresh.
`Toolkit` and `Action` on `add_server` and `refresh_server` — upserted, with tools the server no
longer advertises pruned. `ApprovalTicket` when a `requires_approval` action is called, updated once
when resolved. `AuditLog` on **every** attempt, and never updated — only appended.

---

# Running it

Requires **Python 3.14+** (`brew install python@3.14`), and either a logged-in `claude` CLI
(check with `claude auth status`) or an Anthropic API key.

### Terminal 1 — backend

```bash
cd backend
python3.14 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
#   edit .env — generate DJANGO_SECRET_KEY and JWT_SECRET (the file gives the one-liners)
python manage.py migrate
python manage.py seed
python manage.py runserver 8000
```

**`seed` prints the demo user's password and the agent's secret once** — only hashes are stored. It
prints the agent secret as the exact line to paste:

```
agent secret:  dYrSRJd4yfwwbYrJILs37s2aMWLetpE_
  → paste this exact line into agent-cli/.env, replacing the placeholder:
      AGENT_SECRET="dYrSRJd4yfwwbYrJILs37s2aMWLetpE_"
```

### Terminal 2 — agent CLI

**Run `seed` first** — the agent authenticates with a secret that does not exist until it has run.

```bash
cd agent-cli
python3.14 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Now edit `agent-cli/.env` and replace the placeholder with the line seed printed:

```bash
AGENT_ID="00000000-0000-0000-0000-000000000001"   # already matches the seed
AGENT_SECRET="<the value seed printed>"           # ← you must paste this
# ANTHROPIC_API_KEY="..."                         # only if `claude auth status` says not logged in
```

Never put the user's password in this file. Then:

```bash
python -m agent_cli_script
```

### Try it

```
User: add an mcp server called deepwiki at https://mcp.deepwiki.com/mcp   # no auth needed
User: what toolkits do I have now?
User: using deepwiki, what does the pallets/flask documentation cover?

User: read the sample item          # always_allow     → runs immediately
User: create an item called "Notes" # requires_approval → 202 + ticket
User: delete the sample item        # always_deny      → 403
```

To approve a held ticket, from a **third** terminal — as yourself, with credentials the agent does
not have:

```bash
BASE=http://localhost:8000
UTOK=$(curl -s -X POST $BASE/api/auth/user/token/ -H 'Content-Type: application/json' \
  -d '{"username":"demo","password":"<the password seed printed>"}' \
  | python -c 'import sys,json;print(json.load(sys.stdin)["token"])')

curl -X PATCH $BASE/api/external-services/approvals/<ticket_id>/resolve/ \
  -H "Authorization: Bearer $UTOK" -H 'Content-Type: application/json' \
  -d '{"decision":"approved"}'
```

The CLI notices within ~2 seconds and tells you, unprompted.

---

# The flow, end to end

### When you start the agent CLI

```
python -m agent_cli_script
   │
   ├─ loads agent-cli/.env  →  AGENT_ID + AGENT_SECRET
   │
   ├─ POST /api/auth/agent/token/  { agent_id, agent_secret }
   │     backend: look up Agent → check_secret() against the stored hash
   │              → sign a JWT { typ:"agent", agent_id, exp: +20 min }
   │     ← { token, expires_at }
   │     cached in memory, refreshed ~60s before expiry.  "Session established."
   │
   ├─ starts a background ticket watcher (polls approvals independently of the conversation)
   └─ starts the Claude Agent SDK client, with Bash + one custom tool, GenerateJWT
```

The agent has **no direct tool** for any toolkit action. The only way it can touch the outside world
is `Bash` + `curl` against the backend — which is what guarantees the permission and audit logic
runs on every single attempt.

### When the agent calls an action

```
curl POST /api/external-services/toolkits/<toolkit>/actions/<action>/call/
     Authorization: Bearer <agent JWT>
     { "params": { … } }
   │
 urls.py ─────────────► routes to views/toolkits.py :: call_action
   │
 authentication.py ───► decode JWT, require typ=="agent", load Agent,
   │                    check the agent AND its owner are still active      → 401 if not
   │
 views/toolkits.py ───► _visible_toolkits(agent): built-ins + this user's enabled MCP servers
   │                    look up the Action inside that scope                → 404 if not visible
   │
 validation.py ───────► params vs the stored Action.input_schema            → 400 + audit row
   │
 permissions.py ──────► PermissionOverride for (agent, action)? else Action.default_permission
   │
   ├── always_deny ──────► audit(denied)                                    → 403
   │
   ├── requires_approval ► create ApprovalTicket(requested_by_user=owner)
   │                       audit(pending_approval)                          → 202 + ticket_id
   │
   └── always_allow ─────► toolkits/__init__.py :: execute_action
                             │
                             ├─ toolkit.mcp_server set? → forward as MCP tools/call
                             │                            to the remote server
                             └─ else                    → run the local Python executor
                             │
                           audit(executed)  or  audit(execution_failed)     → 200 / 502
```

Every branch ends in **a response and an audit row**. The row names the agent, the user it acted
for, the toolkit, the action, the params (credentials redacted) and the outcome — and is never
updated afterwards. A real one, from a live call:

```
agent_id     = 00000000-0000-0000-0000-000000000001     toolkit_slug = deepwiki
agent_name   = demo-agent                               action_slug  = read_wiki_structure
user_id      = 019ff816-ba0f-700d-b71b-c0f90a0f459b     outcome      = executed
user_name    = demo
```

### When a human approves a held ticket

```
PATCH /approvals/<id>/resolve/   Authorization: Bearer <USER JWT>
   │
 authentication.py ──► require typ=="user"                    ← an agent token is rejected here
 views/approvals.py ─► lock the ticket row inside a transaction
                       is this the user who raised it?        → 403 if not
                       is it still pending?                   → 409 if not
                       re-check the permission now            → 403 if revoked while it waited
                       execute → update the ticket → audit(executed)
```

The permission is re-checked at approval time because a ticket can sit for 24 hours, and revoking a
permission should stop work already in flight — not only future calls.

---

# Notes

- **The database holds only the permission system.** `items` is a mock external service that keeps
  its data in its own package; MCP toolkits keep theirs on the remote server. No toolkit's data
  lives in this schema.
- **The audit log is append-only** and has no foreign keys to any identity, so deleting a user, an
  agent or a whole toolkit never destroys history.
- **The "table UI"** is the Django admin at `/admin/`, with `AuditLog` registered read-only.
- **Known gaps, deliberately:** no rate limiting on the token endpoints; audit immutability is
  enforced by convention and the admin rather than at the database role level; adding an MCP server
  is ungated, so an agent can extend its own capabilities — what holds that line is the URL guard,
  per-user scoping, full auditing, and instant `disable_server`.

Per-folder detail, including what changed since the last commit, is in
[`backend/README.md`](backend/README.md) and [`agent-cli/README.md`](agent-cli/README.md).
