# External Services Permissions Layer for AI Agents

A permissions-and-audit layer that sits between AI agents and the external services they call.
Every action an agent attempts is funnelled through **one HTTP boundary** where a per-action
permission policy is enforced, every attempt is written to an **immutable audit log**, and
sensitive actions are held for **human approval**.

The repository is two independently-runnable pieces:

| Folder | What it is | Stack |
|---|---|---|
| [`backend/`](backend) | The permissions layer itself — a REST API that validates a per-request JWT, resolves the effective permission for each `(agent, action)`, enforces it (`always_allow` / `requires_approval` / `always_deny`), writes an audit row for every attempt, and runs a human-in-the-loop approval queue. | Python · Django · Django REST Framework · SQLite · PyJWT |
| [`agent-cli-script/`](agent-cli-script) | A Claude Agent SDK CLI that plays the *agent*. It has exactly one custom tool, `GenerateJWT`; everything else it does to the outside world goes through `Bash` + `curl` against the backend, so the permission and audit logic runs on **every single call**. | Python · Claude Agent SDK · PyJWT · httpx |

Each folder has its own detailed README ([backend](backend/README.md) · [agent CLI](agent-cli-script/README.md)). This top-level README is the quick-start for running the two together and the map of how the whole thing fits the brief.

---

## How it maps to the brief

- **Granular permissions** — `resolve_effective_permission()` checks a sparse
  `PermissionOverride` row for `(agent, action)`, falling back to the action's default. A row is
  stored **only** when an agent's permission differs from the default (no row-per-pair).
- **Immutable audit trail** — `audit_log` is append-only (no update or delete path anywhere) and
  has **no foreign key to `agents`**, so deleting an agent never cascades to its history; identity
  is preserved via `agent_id` / `agent_name` snapshot columns. A single approval flow produces
  **two** audit rows (`pending_approval` at call time, then `executed`/`rejected`/`expired` at
  resolution) rather than one mutated row. The Django admin registers it read-only, so the UI
  can't add/edit/delete rows either.
- **Human-in-the-loop** — a `requires_approval` action creates a ticket (HTTP `202`), the agent
  polls `status/`, and a human resolves it via the Basic-auth `resolve/` endpoint. Only a human,
  never the agent, holds the admin credential.
- **JWT identity** — every agent request carries a short-lived HS256 token whose signature,
  expiry, and referenced-agent-still-active checks all run on the server; the signing secret lives
  only in the backend.

---

## Prerequisites

- **Python 3.11+** (tested on 3.11/3.12; `str | None` type hints require ≥ 3.10)
- An **Anthropic API key** ([console.anthropic.com/settings/keys](https://console.anthropic.com/settings/keys))
  — required to run the agent CLI live. The backend on its own can be exercised entirely with
  `curl`, with no Anthropic account at all.
- *(Optional)* a **Notion integration secret** — only if you want the `notion` toolkit to hit a
  real workspace. `items` and `github` need nothing external.

---

## Quick start (two terminals)

There are only two secrets a fresh checkout can't generate for itself, because both are tied to
real accounts: the **Anthropic API key**, and (optional) the **Notion secret**. Everything else —
the JWT signing secret, the admin password, the Django secret key — you generate locally on the
spot with `openssl` / `python`, and each `.env.example` says exactly how.

### Terminal 1 — backend

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
#   then edit .env — generate real values for JWT_SECRET, ADMIN_PASSWORD, DJANGO_SECRET_KEY
#   (the file tells you the exact openssl/python one-liners). Notion keys are optional.
python manage.py migrate            # create db.sqlite3 + apply the (permission-system-only) schema
python manage.py seed               # demo agent + items/github/notion catalog
python manage.py runserver 8000     # serve on http://localhost:8000  — keep this running
```

Every permission attempt prints live in this terminal, coloured and formatted, the moment it
happens — no separate query needed:

```
[00:19:07] demo-agent → items.create_item        ⏳ PENDING_APPROVAL  ticket=81d8f17c
[00:19:48] demo-agent → items.create_item        ✓ EXECUTED          ticket=81d8f17c (Approved and executed.)
```

### Terminal 2 — agent CLI

```bash
cd agent-cli
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
#   then edit .env — paste a real ANTHROPIC_API_KEY. AGENT_ID already matches the seed.
python -m agent_cli_script
```

Then talk to it:

```
User: what can I access?
User: read the sample item
User: create an item called "Meeting Notes" with content "Discuss Q3 roadmap"
User: delete the sample item          # 403 — always_deny
User: exit
```

`create_item` / `update_item` return `202` with a `ticket_id`. Resolve it from a **third**
terminal as the human reviewer:

```bash
curl -X PATCH http://localhost:8000/api/external-services/approvals/<ticket_id>/resolve/ \
  -u admin:<ADMIN_PASSWORD from backend/.env> \
  -H "Content-Type: application/json" \
  -d '{"decision":"approved"}'
# or: -d '{"decision":"rejected","reason":"not needed"}'
```

The moment you resolve it the CLI notices on its own (it polls the backend directly, independent
of the conversation) and prints an unprompted notification within ~2 seconds, even if you're
mid-typing.

---

## The end-to-end demo (allow + approval + deny + audit)

This one sequence exercises all four required paths:

1. **Allow** — `read the sample item` → `200`, runs immediately, one `executed` audit row.
2. **Approval** — `create an item …` → `202` + ticket → resolve it `approved` → the CLI prints
   the result. Two audit rows: `pending_approval`, then `executed`.
3. **Deny** — `delete the sample item` → `403`, one `denied` audit row (nothing is deleted).
4. **Audit** — view the whole trail, newest first:
   ```bash
   TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/token/ \
     -H 'Content-Type: application/json' \
     -d '{"agent_id":"00000000-0000-0000-0000-000000000001"}' | python -c 'import sys,json;print(json.load(sys.stdin)["token"])')
   curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/external-services/audit/ | python -m json.tool
   ```
   Or browse it in the **Django admin** (the assignment's "table UI") — see the backend README.

---

## API surface (at a glance)

All under `/api/external-services` (except token issuance). All require
`Authorization: Bearer <jwt>` **except** `PATCH .../resolve/` and the `/agents/:id/permissions/`
endpoints, which use admin HTTP Basic auth (managing permissions is a human-only control).

| Method & path | Auth | Purpose |
|---|---|---|
| `POST /api/auth/token/` | none¹ | Issue a short-lived JWT for an existing, active agent |
| `GET /toolkits/` | JWT | List toolkits |
| `GET /toolkits/:toolkit/actions/` | JWT | List actions + the caller's **effective** permission for each |
| `GET /toolkits/:toolkit/actions/:action/schema/` | JWT | Get an action's input/output JSON schema |
| `POST /toolkits/:toolkit/actions/:action/call/` | JWT | Attempt an action → `200` / `202` / `403` |
| `GET /approvals/:ticketId/status/` | JWT | Poll a pending ticket (own tickets only) |
| `PATCH /approvals/:ticketId/resolve/` | Admin Basic | Human approves/rejects a ticket |
| `GET /agents/:agentId/permissions/` | Admin Basic | View an agent's sparse permission overrides |
| `PUT /agents/:agentId/permissions/` | Admin Basic | Replace an agent's overrides (`[]` clears all) |
| `GET /audit/?agent_id=&toolkit=&outcome=` | JWT | Query the audit log |

¹ Unauthenticated to call, but only issues a token for an agent that already exists and is active
— the agent is provisioned out-of-band by the seed command, never self-registered.

Full per-endpoint request/response shapes are in [`backend/README.md`](backend/README.md).

---

## The catalog: three code-defined toolkits

Each exercises all three permission branches. **None of the three has any toolkit-specific data
in the database** — `items` and `github` are mock external services, backed by pre-seeded
in-memory stores inside their own toolkit package (mutations genuinely persist for the life of the
process — nothing is a canned response, it just resets on restart, like a real external service's
data would never live in *your* database anyway); `notion` is a genuine **live** integration
against the real Notion API. The backend's own database holds only the permission system itself
(`Agent`/`Toolkit`/`Action`/`PermissionOverride`/`ApprovalTicket`/`AuditLog`) — see
[Mock toolkit storage](backend/README.md#mock-toolkit-storage-items--github) in the backend README.

| Toolkit | `always_allow` | `requires_approval` | `always_deny` | Backing |
|---|---|---|---|---|
| **items** | `read_item` | `create_item`, `update_item` | `delete_item` | in-memory (`toolkits/items/store.py`) |
| **github** | `list_repos`, `get_repo` | `create_issue` | `delete_repo` | in-memory (`toolkits/github/store.py`) |
| **notion** | `list_pages` | `create_page`, `update_page` | `delete_page` | **live** Notion REST API |

`notion` requires `NOTION_API_KEY` + `NOTION_PARENT_PAGE_ID` in `backend/.env`. Without
them, only the `notion.*` actions fail — and only *at call time*, with a clear error — while
`items`/`github` keep working regardless. Each toolkit is a self-contained package under
`backend/external_services/toolkits/<slug>/`; adding a fourth toolkit/MCP is: a new
`toolkits/<slug>/{catalog.py, executors.py}`, one entry in `toolkits/__init__.py`, and a re-run of
`seed`. Nothing in the permission / approval / audit / auth pipeline, or in any other toolkit's
package, changes shape.

---

## Assumptions & deliberate scope decisions

These are the judgment calls made where the brief left room, called out so a reviewer doesn't have
to reverse-engineer them:

- **Stack.** The brief specifies Python + Django ORM; this is a straight Django + DRF
  implementation of exactly that. SQLite is the store (zero-setup, single file); swapping in
  Postgres is a `DATABASES` change only, since all data access is through the ORM.
- **The database holds only the permission system, not toolkit data.** `items` and `github` are
  mock external services, not local tables — they pre-seed and mutate an in-memory store inside
  their own toolkit package, the same way `notion` reaches a real external API instead of a local
  table. This keeps the schema exactly as large as what's actually being evaluated (the
  permission/audit model), and means a mock toolkit can be swapped for a real MCP later without
  ever touching a Django migration. The one tradeoff: mock data resets when the process restarts —
  intentional for a mock service, documented in the backend README.
- **The "table UI".** The brief's browsable admin is the built-in **Django admin** at
  `/admin/` — `AuditLog` is registered read-only there to reinforce its append-only nature. (Create
  a superuser with `python manage.py createsuperuser` to log in; it's separate from the agent/admin
  auth used by the API.)
- **Agents are provisioned, not self-registered.** `POST /auth/token/` issues a token only for an
  agent the seed command already created. There is no public sign-up — an agent identity is an
  operator concern, and self-registration would undermine the whole trust model.
- **Approval tickets expire after 24h.** A pending ticket past its deadline flips to `expired` (and
  writes an audit row) lazily, the next time it's read or resolved — no background cron needed for
  a demo-scale system.
- **`notion.delete_page` archives rather than hard-deletes.** Notion's API has no permanent delete
  for a regular integration; archiving (the trash icon) is the closest equivalent. It's
  `always_deny` by default regardless, matching the "never delete" posture.
- **Params are validated against the *stored* JSON schema** (the same `Action.input_schema` served
  by the schema endpoint), so there's no second hand-written validator that could drift. A
  malformed call is a `400` with `invalid_params`, and is audited like any other outcome.
- **JWT lifetime is 20 minutes** (configurable). The CLI refreshes transparently, so a long session
  survives token expiry without breaking.
- **`bypassPermissions` in the CLI** is safe here because the agent only ever curls `localhost` —
  the *real* permission enforcement is the backend's, not the SDK's local tool-approval prompt.
- **Not in scope** (matching the brief's "not evaluated" list): a web frontend, deployment/CI,
  performance tuning, and complex concurrency. SQLite + Django's dev server are intentional for a
  reviewable, single-command local run.

---

## Repository layout

```
backend/
  config/settings.py            # one SQLite db, DRF, env-driven secrets
  config/urls.py                # health, admin, /api/
  external_services/
    models.py                   # ONLY the permission system: Agent, Toolkit, Action,
                                #   PermissionOverride, ApprovalTicket, AuditLog (no agent FK)
    toolkits/                   # the ONLY toolkit-aware code — one package per toolkit
      __init__.py                 #   the registry: TOOLKITS list + execute_action() dispatch
      items/, github/             #   catalog.py + executors.py + store.py (in-memory mock data)
      notion/                     #   catalog.py + executors.py + client.py (live Notion REST)
    management/commands/seed.py # idempotent catalog + demo-agent registration
    permissions.py              # effective-permission resolution (toolkit-agnostic)
    validation.py                # JSON-schema validation against the stored input schema
    authentication.py           # AgentJWTAuthentication + AdminBasicAuthentication
    tokens.py                   # PyJWT sign/verify (secret stays here)
    audit.py                    # append-only write path + live console mirror
    views/                      # toolkits, approvals, auth, permissions, audit
agent-cli-script/
  agent_cli_script/
    __main__.py                 # interactive session loop + streaming output
    session.py                  # JWT cache + transparent refresh
    system_prompt.py            # the agent's operating instructions
    ticket_watcher.py           # background poller -> unprompted approve/reject notifications
    tools/generate_jwt.py       # the one custom LLM tool (in-process MCP server)
```
