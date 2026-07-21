# Permissions & Audit Layer — Django backend

The gateway that sits between AI agents and external-service toolkits. Every agent action funnels
through **one HTTP boundary**, so on every single call the backend:

1. **authenticates** the agent's short-lived JWT (signature + expiry + agent-still-active),
2. **validates** the params against the action's stored JSON schema,
3. **resolves** the agent's *effective* permission for that action,
4. **enforces** it — `always_allow` runs it now, `requires_approval` holds it for a human,
   `always_deny` refuses it, and
5. **writes an immutable audit row** for the outcome, whichever branch was taken.

**Stack:** Python · Django · Django REST Framework · Django ORM · SQLite · PyJWT · jsonschema ·
requests · python-dotenv.

---

## Setup

Requires **Python 3.11+**.

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env               # then edit — see the table below
python manage.py migrate           # create db.sqlite3 + apply the schema
python manage.py seed              # register the catalog + demo data (idempotent, safe to re-run)
python manage.py createsuperuser   # optional — lets you browse models in the Django admin
python manage.py runserver 8000
```

The server listens on `http://localhost:8000`. `seed` creates a demo agent with the fixed id
`00000000-0000-0000-0000-000000000001` — the id the CLI's `.env` already points at — and registers
the `items` / `github` / `notion` catalog. `items` and `github` bring their own pre-seeded sample
data (see [Mock toolkit storage](#mock-toolkit-storage-items--github) below); there's no
database-backed demo data to seed.

### Environment (`.env`, copied from `.env.example`, git-ignored)

| Key | Purpose |
|---|---|
| `DJANGO_SECRET_KEY` | Django's own secret. Generate: `python -c "import secrets; print(secrets.token_urlsafe(50))"` |
| `DJANGO_DEBUG` | `true` for local dev |
| `JWT_SECRET` | HS256 signing key for agent tokens — **never leaves the backend**. Generate: `openssl rand -hex 32` |
| `JWT_EXPIRES_IN_MINUTES` | Agent token lifetime (default `20`) |
| `ADMIN_USER` / `ADMIN_PASSWORD` | HTTP Basic credentials for the reviewer's `resolve/` endpoint. Generate a password: `openssl rand -hex 12` |
| `NOTION_API_KEY` / `NOTION_PARENT_PAGE_ID` | Only for the live `notion` toolkit. `items` / `github` work without them. |

No real secret value is ever committed — `.env` is git-ignored and every environment generates its
own.

---

## API

All endpoints require the agent JWT (`Authorization: Bearer <jwt>`) **except** `POST
/api/auth/token/` (unauthenticated) and the admin controls — `PATCH .../resolve/` and both
`/agents/<id>/permissions/` methods — which use admin HTTP Basic. Managing who can do what is a
human-only control: gating it with an agent's own token would let an agent rewrite its own policy.

| Method + Path | Auth | Purpose |
|---|---|---|
| `GET /health/` | none | Liveness check |
| `POST /api/auth/token/` | none | Issue a short-lived JWT for an existing, active agent |
| `GET /api/external-services/toolkits/` | JWT | List toolkits |
| `GET /api/external-services/toolkits/<tk>/actions/` | JWT | List a toolkit's actions with the agent's **effective** permission |
| `GET /api/external-services/toolkits/<tk>/actions/<a>/schema/` | JWT | Get an action's input/output JSON schema |
| `POST /api/external-services/toolkits/<tk>/actions/<a>/call/` | JWT | Call an action → `200` executed / `202` pending / `403` denied / `400` invalid params |
| `GET /api/external-services/approvals/<ticket>/status/` | JWT | Poll a ticket (owning agent only) |
| `PATCH /api/external-services/approvals/<ticket>/resolve/` | admin Basic | Approve or reject a ticket |
| `GET /api/external-services/agents/<id>/permissions/` | admin Basic | Read an agent's sparse overrides |
| `PUT /api/external-services/agents/<id>/permissions/` | admin Basic | Replace an agent's overrides wholesale (`[]` clears all) |
| `GET /api/external-services/audit/?agent_id=&toolkit=&outcome=` | JWT | Query the audit log, newest first |

The seeded permissions give all three branches to demo: `read_item` / `list_repos` / `list_pages`
are `always_allow`; `create_*` / `update_*` are `requires_approval`; every `delete_*` is
`always_deny`.

### Request/response walkthrough (curl)

```bash
BASE=http://localhost:8000
AGENT=00000000-0000-0000-0000-000000000001

# 1. Get a token
TOKEN=$(curl -s -X POST $BASE/api/auth/token/ \
  -H 'Content-Type: application/json' -d "{\"agent_id\":\"$AGENT\"}" \
  | python -c 'import sys,json;print(json.load(sys.stdin)["token"])')

# 2. ALLOW — read the sample item -> 200 {"status":"executed","result":{...}}
curl -s -H "Authorization: Bearer $TOKEN" \
  -X POST $BASE/api/external-services/toolkits/items/actions/read_item/call/ \
  -H 'Content-Type: application/json' \
  -d '{"params":{"id":"10000000-0000-0000-0000-000000000001"}}'

# 3. APPROVAL — create an item -> 202 {"status":"pending_approval","ticket_id":"..."}
curl -s -H "Authorization: Bearer $TOKEN" \
  -X POST $BASE/api/external-services/toolkits/items/actions/create_item/call/ \
  -H 'Content-Type: application/json' \
  -d '{"params":{"name":"Notes","content":"hello"}}'

# 4. Human resolves it (admin Basic auth, NOT the agent JWT)
curl -s -X PATCH $BASE/api/external-services/approvals/<ticket_id>/resolve/ \
  -u admin:<ADMIN_PASSWORD from .env> \
  -H 'Content-Type: application/json' -d '{"decision":"approved"}'

# 5. DENY — delete the sample item -> 403 {"status":"denied","message":"..."}
curl -s -H "Authorization: Bearer $TOKEN" \
  -X POST $BASE/api/external-services/toolkits/items/actions/delete_item/call/ \
  -H 'Content-Type: application/json' \
  -d '{"params":{"id":"10000000-0000-0000-0000-000000000001"}}'

# 6. AUDIT — the whole trail, newest first
curl -s -H "Authorization: Bearer $TOKEN" $BASE/api/external-services/audit/ | python -m json.tool
```

Setting a per-agent override (e.g. flipping `read_item` to `always_deny` for this agent) — this is
an **admin** control, so it uses admin Basic auth, not the agent JWT:

```bash
curl -s -u admin:<ADMIN_PASSWORD from .env> \
  -X PUT $BASE/api/external-services/agents/$AGENT/permissions/ \
  -H 'Content-Type: application/json' \
  -d '{"overrides":[{"action":"read_item","permission":"always_deny"}]}'
# PUT is wholesale — send {"overrides":[]} to clear every override back to defaults.
```

---

## The table UI (Django admin)

The brief's browsable "table UI" is the built-in Django admin at `http://localhost:8000/admin/`
(log in with the superuser you created above). Every model is browsable; **`AuditLog` is registered
read-only** — no add/change/delete through the UI — so its append-only, immutable nature holds in
the admin too.

You can also just watch the `runserver` terminal: every permission attempt prints there live,
coloured and labelled, the moment it happens (with a one-time legend at the top).

---

## Data model

Six models + three enums (`PermissionValue`, `TicketStatus`, `AuditOutcome`, stored as their
string values so they match the REST contract exactly). **This is deliberately the entire schema**
— the database holds only the permission system's own domain, nothing toolkit-specific. See
[Mock toolkit storage](#mock-toolkit-storage-items--github) below for where `items`/`github`'s data
actually lives.

| Model | Role |
|---|---|
| `Agent` | The calling identity. Referenced everywhere by UUID; `name` is display-only, never used for authorization. |
| `Toolkit` | One external-service surface (`items`, `github`, `notion`). |
| `Action` | One callable action on a toolkit, carrying its stored input/output JSON schemas and its `default_permission`. Slug is unique *per toolkit*. |
| `PermissionOverride` | **Sparse** per-agent override — a row exists only when an agent's permission for an action differs from that action's default. Unique per `(agent, action)`. |
| `ApprovalTicket` | A held `requires_approval` call awaiting a human decision, with a 24h expiry. |
| `AuditLog` | Append-only record of every attempt. **No FK to `Agent`** — see below. |

**Why `AuditLog` has no foreign key to `Agent`:** the audit trail must survive forever, regardless
of what later happens to an agent (deletion included). So it snapshots `agent_id` and `agent_name`
as plain string columns at write time. Deleting an agent never cascades to — or orphans — a single
audit row. There is no update path anywhere in the code, and the admin registration is read-only:
immutability is enforced structurally, not by convention.

### Mock toolkit storage (`items` / `github`)

`Item`, `Repo`, and `Issue` are **not** Django models. `items` and `github` are mock external
services — exactly like `notion` is a real one — so each owns its data inside its own toolkit
package, not in this app's schema:

```
toolkits/items/store.py    a {id: item} dict, pre-seeded with one sample item
toolkits/github/store.py   two {id: dict} dicts (repos, issues), pre-seeded with two sample repos
```

Plain module-level Python dicts, mutated directly by that toolkit's `executors.py` — `create`
generates a `uuid.uuid4()` id and inserts, `delete_repo` removes the repo's issues along with it
(a hand-written stand-in for the FK `on_delete=CASCADE` a real table would give for free). State
lives only in the running process and **resets on restart** — including Django's autoreloader
firing on a code change mid-demo. That's an intentional property of a mock external service, not a
limitation: a real `items` or `github` MCP would own its data itself, the same way Notion owns its
own pages, and this database should never need a migration just because a mock toolkit's shape
changes.

---

## Project layout

The request flow reads top-to-bottom without hopping across many files.

```
config/            Django project (settings, urls, wsgi/asgi)
external_services/
  models.py          6 models + 3 enums; AuditLog has NO FK to Agent (survives agent deletion)
  authentication.py  AgentJWTAuthentication + AdminBasicAuthentication (DRF auth classes)
  tokens.py          PyJWT sign/verify — the signing secret lives only here
  permissions.py     resolve_effective_permission() — completely toolkit-agnostic
  validation.py      validate_params() against the STORED input schema (jsonschema)
  audit.py           write_audit_row(), resolve_expiry_if_needed() + the live console mirror
  exceptions.py      DRF handler -> {error, message} bodies + clean JSON 500
  toolkits/          the ONLY toolkit-aware code — see below
    __init__.py         the toolkit registry: TOOLKITS list + execute_action() dispatch
    items/              catalog.py + executors.py + store.py (in-memory mock data)
    github/             catalog.py + executors.py + store.py (in-memory mock data)
    notion/             catalog.py + executors.py + client.py (the live Notion REST wrapper)
  views/             auth · toolkits · approvals · permissions · audit
  admin.py           all models browsable; AuditLog read-only
  management/commands/seed.py   catalog registration + demo agent (idempotent)
```

### Architecture: the pipeline is decoupled from toolkits

Authentication, permission resolution, the approval queue, and the audit log operate purely on
`Agent` / `Action` / `params` / effective-permission. **None of them knows what `github` or
`notion` is.** All toolkit-aware code lives under `toolkits/`, one self-contained package per
toolkit:

```
toolkits/<slug>/
  catalog.py     pure data — TOOLKIT = {slug, name, description, actions: [...]}, what `seed`
                 registers into the Toolkit/Action tables
  executors.py   the real side effects — EXECUTORS = {action_slug: function}
  store.py       (mock toolkits, e.g. items/, github/) — pre-seeded in-memory dicts; the
                 toolkit's OWN data, never a Django model
  client.py      (live toolkits, e.g. notion/) — a plain HTTP wrapper hitting the real API
```

Either way, `store.py` or `client.py` is private to that one toolkit package — nothing outside it,
and no other toolkit, ever imports it.

`toolkits/__init__.py` is the **one file** that wires a toolkit package into the system: it
collects every package's `TOOLKIT` into `TOOLKITS` (what `seed` reads) and every package's
`EXECUTORS` into one dispatch table keyed by `"<toolkit>.<action>"` (what `execute_action()`
looks up). Adding a fourth toolkit/MCP is:

1. create `toolkits/<slug>/{catalog.py, executors.py}` (+ `client.py` if it calls a real API),
2. add one import + one entry to `toolkits/__init__.py`'s `_MODULES` list,
3. re-run `python manage.py seed`.

Nothing in the permission / approval / audit / auth pipeline, or in any *other* toolkit's package,
changes.

---

## Notes & assumptions

- **Approval produces two audit rows.** Because the log is append-only, resolving a ticket
  *appends* a new row (`executed` / `rejected` / `expired`) rather than editing the original
  `pending_approval` row — the full history of a decision is reconstructable.
- **Ticket expiry is lazy.** A pending ticket past its 24h deadline flips to `expired` (and writes
  its audit row) the next time it's read (`status/`) or resolved (`resolve/`) — no background sweep,
  which keeps the demo a single process. `resolve_expiry_if_needed()` is shared by both endpoints so
  the rule can't drift between them.
- **On `always_allow`, the effect runs *before* the audit row is written**, so a throwing effect
  produces no misleading `executed` row; the DRF exception handler turns it into a clean JSON `500`.
- **A failed live Notion call is a clean `500` with no audit row** — an unexpected *system* failure
  isn't a permission-relevant outcome, so it isn't recorded as one. Permission-relevant outcomes
  (allow / deny / pending / approved / rejected / expired / invalid-params) always are.
- **Params are validated against the *stored* schema.** `call/` validates directly against
  `Action.input_schema` — the same JSON the schema endpoint serves — so there is no second
  hand-written validator that could drift. A malformed call is a `400 invalid_params` (and is
  audited).
- **`notion.delete_page` archives, not hard-deletes** — Notion's API has no permanent delete for a
  regular integration. It's `always_deny` by default regardless.
- **SQLite is intentional** for a zero-setup, single-file, reviewable local run. All data access is
  through the ORM, so moving to Postgres is a `DATABASES` change only.
- **`items`/`github` mock data resets on restart.** It lives in each toolkit's own in-memory
  `store.py`, not the database — deliberate, see [Mock toolkit storage](#mock-toolkit-storage-items--github)
  above. If you need mutations to survive a restart for a longer demo, that's the one file per
  toolkit you'd swap for a real datastore; nothing else in the app would need to change.
