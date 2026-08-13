# Permissions & Audit Layer — Django backend

The gateway between AI agents and external-service toolkits. Every agent action funnels through
**one HTTP boundary**, so on every single call the backend:

1. **authenticates** the caller's short-lived JWT (signature + expiry + token type + identity still active),
2. **validates** the params against the action's stored JSON schema,
3. **resolves** the agent's *effective* permission for that action,
4. **enforces** it — `always_allow` runs it now, `requires_approval` holds it for a human, `always_deny` refuses,
5. **writes an immutable audit row** for the outcome, whichever branch was taken.

**Stack:** Python 3.14 · Django · Django REST Framework · SQLite · PyJWT · jsonschema · requests.

> The feature work, the architecture rationale and the end-to-end flow are in the
> [top-level README](../README.md). This document is the backend reference, plus the list of
> defects fixed since the last commit.

---

## Fixes since the last commit

Found by reviewing the original submission and by exercising it. Each was reproduced before being
fixed and re-checked after.

### Correctness and security

**The audit log was readable by anyone.** `GET /audit/` was behind the agent JWT but not scoped to
the caller, so any agent could read every other agent's trail — including `params`, which is exactly
where secrets and personal data end up. It is now scoped: an agent sees only its own rows, a user
sees their agents'.

**One approval could execute an action many times.** `resolve/` checked `status != pending` and then
executed, with nothing in between — a check-then-act race. Five concurrent approvals of one ticket
produced five executions. The ticket row is now locked inside a transaction and re-read under the
lock, so exactly one caller wins and the rest get `409`.

> Worth calling out because it was invisible: adding `select_for_update()` alone did **not** fix
> this on SQLite. Django opens a *deferred* transaction, so concurrent writers each take a read lock
> and then deadlock trying to upgrade — SQLite gives up immediately with "database is locked", and
> the four losers got `500`s instead of `409`s. It needed `transaction_mode: "IMMEDIATE"`, a busy
> `timeout` and WAL in `DATABASES` before the lock did anything at all. On Postgres the row lock is
> real and none of this applies.

**Permission overrides landed on the wrong action.** The override endpoint resolved actions by slug
alone (`Action.objects.filter(slug__in=[…])`), but `Action.slug` is unique only *per toolkit*. With
two toolkits exposing the same action name the override silently attached to whichever row came back
last — while reporting success. Actions must now be addressed as `"<toolkit>.<action>"`; a bare slug
is a `400`.

**A revoked permission did not stop work already in flight.** Permission was resolved when the call
was made and never again, so a ticket created before a revocation still executed on approval — up to
24 hours later. `resolve/` now re-resolves under the lock and refuses if it has since become
`always_deny`.

**A failing action left no trace.** The executor ran before the audit row was written, so anything
that threw produced a `500` and *no record at all* — losing precisely the attempts most worth having
during an incident. Executors are now wrapped, and a failure writes an `execution_failed` row before
returning `502`.

**Policy changes were not audited.** Changing an agent's permissions wrote nothing. In a system
built to answer "who allowed this?", the policy-change trail matters as much as the action trail; it
now writes a `permission_changed` row naming the user who made it.

**Agent identity was not a credential.** `POST /auth/token/` issued a token to anyone who supplied an
agent's UUID — but a UUID appears in URLs, in every audit row and in config files. Token issuance is
now an exchange against a hashed per-agent secret.

### Robustness

| Fix | Was |
|---|---|
| `seed` prunes toolkits/actions no longer in the catalog | Deleting a toolkit from the code left its rows — and its callable actions — in the database forever |
| A JSON array request body returns `400` | `request.data.get(...)` raised `AttributeError` → `500` |
| Audit `agent_id` filter is case-insensitive | An uppercase UUID silently matched nothing |
| A contended database returns `503` with `Retry-After` | Surfaced as an opaque `500` |
| `seed` raises `CommandError` instead of `assert` | Assertions are stripped under `python -O`, letting a bad permission reach the database silently |
| Composite indexes on `(agent_id, -created_at)` and `(user_id, -created_at)` | Indexed the columns but not the ordering, though every read is newest-first |

### Configuration hardening

| Fix | Was |
|---|---|
| `DEBUG` defaults to **False** | Defaulted to `True`, so a missing or misspelled env var served tracebacks and accepted any `Host` |
| `IsAuthenticated` in `DEFAULT_PERMISSION_CLASSES` | Empty — security rested entirely on every auth class remembering to raise rather than return `None` |
| `django.request` logs to the console at WARNING | Routed to a `NullHandler`, silencing every 4xx/5xx and making a real `500` nearly undiagnosable |
| Password comparison through Django's hashers | The admin password was compared with `!=`, which is not constant-time |

### Documentation

The top-level README's links pointed at `agent-cli-script/`; the directory is `agent-cli/`. Several
modules carried comments referring to a TypeScript version of files that do not exist in this
repository ("Faithful Python port of session.ts", "what Express did in middleware"). Both were
stale and have been removed.

---

## Setup

Requires **Python 3.14+** — `uuid.uuid7()` is in the standard library from 3.14 and this project
uses it directly. On macOS: `brew install python@3.14`.

```bash
cd backend
python3.14 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env               # then edit — see the table below
python manage.py migrate
python manage.py seed
python manage.py createsuperuser   # optional — to browse the admin
python manage.py runserver 8000
```

`seed` creates a demo **user** (`demo`) and a demo **agent** with the fixed id
`00000000-0000-0000-0000-000000000001`, and prints the user's password and the agent's secret
**once** — only their PBKDF2 hashes are stored. The agent secret is printed as the exact
`AGENT_SECRET="…"` line to paste into `agent-cli/.env`.

Re-running `seed` never rotates an existing credential (that would break a working `agent-cli/.env`
on an unrelated re-seed). Set `DEMO_USER_PASSWORD` / `DEMO_AGENT_SECRET` to choose or rotate them.

### Environment (`.env`, git-ignored)

| Key | Purpose |
|---|---|
| `DJANGO_SECRET_KEY` | Django's own secret. `python -c "import secrets; print(secrets.token_urlsafe(50))"` |
| `DJANGO_DEBUG` | `true` for local dev. **Defaults to false** when unset, so a typo fails safe |
| `JWT_SECRET` | HS256 signing key for every token — never leaves the backend. `openssl rand -hex 32` |
| `JWT_EXPIRES_IN_MINUTES` | Token lifetime (default `20`) |
| `DEMO_USER_PASSWORD` / `DEMO_AGENT_SECRET` | Optional, seed-time only |

User passwords and agent secrets are **not** configuration — they live in the database as hashes.

---

## API

| Method + Path | Auth | Purpose |
|---|---|---|
| `GET /health/` | none | Liveness |
| `POST /api/auth/user/token/` | none¹ | username + password → user token |
| `POST /api/auth/agent/token/` | none¹ | agent id + secret → agent token |
| `GET /api/external-services/toolkits/` | agent | Toolkits visible to this caller |
| `GET /…/toolkits/<tk>/actions/` | agent | Actions + the agent's **effective** permission |
| `GET /…/toolkits/<tk>/actions/<a>/schema/` | agent | An action's input/output JSON schema |
| `POST /…/toolkits/<tk>/actions/<a>/call/` | agent | Call → `200` / `202` / `403` / `400` / `502` |
| `GET /…/approvals/<ticket>/status/` | agent | Poll a ticket (owning agent only) |
| `PATCH /…/approvals/<ticket>/resolve/` | **user** | Approve/reject — only the user who raised it |
| `GET`, `PUT` `/…/agents/<id>/permissions/` | **user** | Read / replace an agent's sparse overrides |
| `GET /…/audit/` | either | Query the audit log, scoped to the caller |

¹ Unauthenticated to call — they *are* the authentication step. Both require a real credential and
return one generic `401` whatever went wrong, so neither can enumerate valid usernames or agent ids.
Neither is rate-limited; that remains the clearest production gap, flagged in `views/auth.py`.

Seeded permissions demonstrate all three branches: `read_item` is `always_allow`, `create_item` and
`update_item` are `requires_approval`, `delete_item` is `always_deny`.

Setting an override — a **user** control, and actions must be toolkit-qualified:

```bash
curl -s -X PUT $BASE/api/external-services/agents/$AGENT/permissions/ \
  -H "Authorization: Bearer $UTOK" -H 'Content-Type: application/json' \
  -d '{"overrides":[{"action":"items.read_item","permission":"always_deny"}]}'
# PUT is wholesale — send {"overrides":[]} to clear everything back to defaults.
```

---

## Data model

Nine models. `User` is Django's `AbstractUser` with a UUIDv7 primary key, set as `AUTH_USER_MODEL`.

| Model | Role |
|---|---|
| `User` | A human. Owns agents, approves held actions, sets permissions |
| `Agent` | A program acting for exactly one user; authenticates with a hashed secret |
| `Toolkit` | One external-service surface. `mcp_server` NULL = built-in; set = discovered, owner-scoped |
| `Action` | One callable action, with its stored JSON schemas and `default_permission` |
| `MCPServer` | A user's plugged-in server: URL, headers, enabled flag, last discovery/error |
| `PermissionOverride` | **Sparse** per-agent override — a row exists only when it differs from the default |
| `ApprovalTicket` | A held call, with a 24h expiry and the user who raised it |
| `AuditLog` | Append-only record of every attempt. **No FK to anything** |
| `PolicyDefault` | Singleton: the permission newly discovered tools are registered with |

**Why `AuditLog` has no foreign keys:** the trail must survive whatever later happens to an agent, a
user or a toolkit. It snapshots `agent_id` / `agent_name` / `user_id` / `user_name` at write time, so
deleting any of them cascades to nothing. Snapshotting also records each name *as it was at the time
of the call*, which is the correct audit semantic — a JOIN would show today's name on a two-year-old
event.

**Why `ApprovalTicket.requested_by_user` is stored, not derived** through `agent.owner`:
re-assigning an agent must never hand a stranger authority over work already in flight.

### SQLite configuration

`transaction_mode: "IMMEDIATE"`, a busy `timeout` and WAL journaling in `DATABASES` are not
incidental tuning — without them the row lock in `resolve/` does nothing. See the concurrency fix
above.

---

## Project layout

```
config/            Django project (settings, urls, wsgi/asgi)
external_services/
  models.py          9 models + 3 enums; AuditLog has NO FK to any identity
  authentication.py  Agent / User / either-identity JWT authentication classes
  tokens.py          PyJWT sign/verify + the typ claim — the signing secret lives only here
  permissions.py     resolve_effective_permission() — completely toolkit-agnostic
  validation.py      validate_params() against the STORED input schema
  audit.py           write_audit_row() + credential redaction + the live console mirror
  exceptions.py      {error, message} bodies, 503 on a contended DB, clean JSON 500
  toolkits/          the ONLY toolkit-aware code
    __init__.py         registry: TOOLKITS list + execute_action() dispatch
    errors.py           ExecutorError / InvalidRequest — failures safe to return to the caller
    items/              a mock external service (catalog + executors + in-memory store)
    mcp/                the toolkit that registers other toolkits, and its protocol client
  views/             auth · toolkits · approvals · permissions · audit
  admin.py           all models browsable; AuditLog read-only; credential hashes hidden
  management/commands/seed.py   catalog sync (upsert + prune) + demo identities
```

Authentication, permission resolution, the approval queue and the audit log operate purely on
`Agent` / `User` / `Action` / `params`. **None of them contains the string "mcp" anywhere** — every
toolkit-aware line lives under `toolkits/`.

---

## Notes

- **Approval produces two audit rows.** The log is append-only, so resolving a ticket *appends*
  (`executed` / `rejected` / `execution_failed` / `expired`) rather than editing the original
  `pending_approval` row — the full history of a decision stays reconstructable.
- **Ticket expiry is lazy.** A pending ticket past its 24h deadline flips to `expired` next time it
  is read or resolved. `resolve_expiry_if_needed()` is shared by both endpoints so the rule cannot
  drift between them.
- **Params are validated against the stored schema** — the same JSON the schema endpoint serves — so
  there is no second hand-written validator to drift from it.
- **Credentials are redacted** from audit rows by key (`headers`, `authorization`, `token`,
  `secret`, …) before anything is written.
- **SQLite is intentional** for a zero-setup local run. All access is through the ORM, so moving to
  Postgres is a `DATABASES` change — and there `select_for_update()` becomes a genuine row lock.
- **Still open:** no rate limiting on the token endpoints; audit immutability is convention plus a
  read-only admin rather than enforced at the database role level; `params` is stored verbatim on
  `ApprovalTicket` (never served over the API, but visible in the admin).
