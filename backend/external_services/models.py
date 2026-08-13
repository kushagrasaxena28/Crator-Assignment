"""Database models for the permissions-and-audit layer.

There are two identities here, and keeping them distinct is the point of the design:

  * `User` — a human. Authenticates with a username and password, owns agents, and is the only
    identity allowed to approve a held action or change an agent's permissions.
  * `Agent` — a program acting on a user's behalf. Authenticates with its own secret, and can call
    actions but can never approve one.

An agent belongs to exactly one user; a user may own many agents. Every token this system issues
says which of the two it represents (see tokens.py), because an agent that could present itself as
its own owner could sign off on its own work — which would defeat the entire approval queue.

`User` subclasses Django's `AbstractUser` and is wired up as `AUTH_USER_MODEL`, so password
hashing, the configured password validators, `createsuperuser`, and the admin all work the way any
Django reviewer would expect. The only thing customised is the primary key. `Agent` deliberately
does *not* subclass it: an agent is not a person, has no password, and must never be able to log
into the admin.

The three enums are stored as their string values (matching the REST contract and the values the
CLI sends). The audit log is deliberately decoupled from both identities — see `AuditLog` below.
"""

from uuid import uuid7

from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models


class PermissionValue(models.TextChoices):
    ALWAYS_ALLOW = "always_allow", "Always allow"
    REQUIRES_APPROVAL = "requires_approval", "Requires approval"
    ALWAYS_DENY = "always_deny", "Always deny"


class TicketStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    EXPIRED = "expired", "Expired"


class AuditOutcome(models.TextChoices):
    EXECUTED = "executed", "Executed"
    DENIED = "denied", "Denied"
    PENDING_APPROVAL = "pending_approval", "Pending approval"
    REJECTED = "rejected", "Rejected"
    EXPIRED = "expired", "Expired"
    INVALID_PARAMS = "invalid_params", "Invalid params"
    # The action was permitted and attempted, but the effect itself raised. Recorded so an attempt
    # that reached an external service can never be invisible in the log.
    EXECUTION_FAILED = "execution_failed", "Execution failed"
    # A user changed an agent's permission overrides. The policy-change trail matters as much as
    # the action trail in a system built to answer "who allowed this?".
    PERMISSION_CHANGED = "permission_changed", "Permission changed"


class PolicyDefault(models.Model):
    """Singleton row holding the permission newly-discovered MCP tools are registered with.

    It lives in the database rather than in code because it is policy, not structure: an operator
    should be able to tighten the default for every future tool without a deploy. The built-in
    toolkits keep their own per-action defaults in `catalog.py` — those are part of what each
    toolkit *is*, and are what make the allow / approval / deny branches demonstrable."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    default_permission_for_new_actions = models.CharField(
        max_length=32, choices=PermissionValue.choices, default=PermissionValue.ALWAYS_ALLOW
    )
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def current(cls) -> "PolicyDefault":
        row = cls.objects.first()
        if row is None:
            row = cls.objects.create()
        return row

    def __str__(self) -> str:
        return f"new MCP tools default to {self.default_permission_for_new_actions}"


class User(AbstractUser):
    """A human operator. Owns agents, approves their held actions, and sets their permissions.

    Everything about authentication is inherited: `set_password`/`check_password`, the validators
    in `AUTH_PASSWORD_VALIDATORS`, `is_active`, and admin integration. The one override is the
    primary key, so users are identified the same way as everything else here (see the UUIDv7 note
    on `Agent`)."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)


class Agent(models.Model):
    """An identity that calls the layer, owned by exactly one user. Referenced everywhere by its
    UUID; `name` is for display and logging only and never participates in authorization.

    Every generated id in this schema is a **UUIDv7** rather than the more familiar v4. Both are
    128-bit and safe to expose in URLs and logs, but v7 carries a 48-bit millisecond timestamp in
    its high bits, so ids created close together sort close together — inserts append to the right
    edge of the index instead of scattering across the B-tree and splitting pages. `AuditLog` is
    where that matters most: append-only, ever-growing, always read newest-first. The tradeoff is
    that a v7 id reveals its creation time, which every model here already exposes via `created_at`.

    `secret_hash` makes the agent's identity an actual credential. Before this existed, anyone who
    learned an agent's UUID could mint a token for it — but a UUID is an identifier, not a secret:
    it appears in URLs, in audit rows, and in the CLI's configuration. Token issuance is now a
    client-credentials exchange (agent id + agent secret), and the secret is stored only as a hash.
    """

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="agents")
    name = models.CharField(max_length=255)
    secret_hash = models.CharField(max_length=128)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # DRF's IsAuthenticated checks this attribute. An Agent is not a Django user, so unlike `User`
    # it doesn't inherit the contract and has to state it.
    is_authenticated = True

    def set_secret(self, raw_secret: str) -> None:
        self.secret_hash = make_password(raw_secret)

    def check_secret(self, raw_secret: str) -> bool:
        return check_password(raw_secret, self.secret_hash)

    def __str__(self) -> str:
        return f"{self.name} ({self.id})"


class MCPServer(models.Model):
    """A custom MCP server a user has plugged in, and the connection details needed to reach it.

    Kept separate from `Toolkit` rather than folded into it as extra columns, because the two have
    different lifecycles: the catalog (Toolkit + Actions) is torn down and rebuilt on every
    re-discovery, while the connection and its credentials persist across those rebuilds. It also
    keeps `headers` — which carries the server's bearer token — out of the table the public catalog
    endpoints serve.

    `is_enabled` is the plug-out switch: flipping it hides the toolkit from the catalog and makes
    its actions uncallable without deleting anything, so permission overrides, approval tickets and
    audit history all survive being unplugged."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="mcp_servers")
    name = models.CharField(max_length=255)
    slug = models.SlugField()
    url = models.URLField(max_length=500)
    # Auth headers for the remote server, e.g. {"Authorization": "Bearer ..."}. Redacted before
    # anything reaches the audit log (see audit.py) and never returned by any endpoint.
    headers = models.JSONField(default=dict, blank=True)
    protocol_version = models.CharField(max_length=64, blank=True)
    is_enabled = models.BooleanField(default=True)
    last_error = models.TextField(blank=True)
    last_discovered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["owner", "slug"], name="unique_mcp_server_per_owner"),
        ]

    def __str__(self) -> str:
        return f"{self.slug} ({self.owner.username})"


class Toolkit(models.Model):
    """One external-service surface. Either built in (`mcp_server` is NULL, visible to everyone) or
    discovered from a user's own MCP server (visible only to that user's agents)."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    mcp_server = models.OneToOneField(
        MCPServer, on_delete=models.CASCADE, related_name="toolkit", null=True, blank=True
    )
    slug = models.SlugField()
    name = models.CharField(max_length=255)
    description = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            # Built-in slugs stay globally unique. User-registered ones only need to be unique per
            # owner, which MCPServer's own (owner, slug) constraint already guarantees — so two
            # different users may each plug in a server called "stripe".
            models.UniqueConstraint(
                fields=["slug"],
                condition=models.Q(mcp_server__isnull=True),
                name="unique_builtin_toolkit_slug",
            ),
        ]

    def __str__(self) -> str:
        return self.slug


class Action(models.Model):
    """One callable action on a toolkit, with its stored input/output JSON schemas and the
    default permission applied when an agent has no override.

    `remote_name` exists because MCP tool names are legal in shapes a URL segment is not: the spec
    allows dots (`admin.tools.list`), which Django's `<slug:>` converter and `SlugField` both
    reject. So `slug` is a sanitized, URL-safe form used for routing, and `remote_name` keeps the
    exact name to send back in `tools/call`. Blank for built-in actions, where the two are the
    same."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    toolkit = models.ForeignKey(Toolkit, on_delete=models.CASCADE, related_name="actions")
    slug = models.SlugField()
    remote_name = models.CharField(max_length=255, blank=True)
    name = models.CharField(max_length=255)
    description = models.TextField()
    input_schema = models.JSONField()
    output_schema = models.JSONField()
    default_permission = models.CharField(max_length=32, choices=PermissionValue.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            # slug is unique per toolkit, not globally.
            models.UniqueConstraint(fields=["toolkit", "slug"], name="unique_action_per_toolkit"),
        ]

    def __str__(self) -> str:
        return f"{self.toolkit.slug}.{self.slug}"


class PermissionOverride(models.Model):
    """Sparse per-agent override of an action's default permission. A row exists ONLY when an
    agent's effective permission for an action differs from that action's default."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name="permission_overrides")
    action = models.ForeignKey(Action, on_delete=models.CASCADE, related_name="permission_overrides")
    permission = models.CharField(max_length=32, choices=PermissionValue.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["agent", "action"], name="unique_override_per_agent_action"),
        ]


class ApprovalTicket(models.Model):
    """A held `requires_approval` call, awaiting a human decision.

    `requested_by_user` records which user the calling agent was acting for at the moment of the
    call, and only that user may resolve the ticket. It is stored explicitly rather than derived
    through `agent.owner` so that re-assigning an agent to a different owner can never hand a
    stranger the authority to approve work that is already in flight."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name="approval_tickets")
    requested_by_user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="approval_tickets")
    action = models.ForeignKey(Action, on_delete=models.CASCADE, related_name="approval_tickets")
    params = models.JSONField()
    status = models.CharField(max_length=16, choices=TicketStatus.choices, default=TicketStatus.PENDING)
    result = models.JSONField(null=True, blank=True)
    rejection_reason = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    resolved_at = models.DateTimeField(null=True, blank=True)


class AuditLog(models.Model):
    """Append-only record of every attempt. Deliberately has NO foreign key to `Agent` or `User`:
    it stores their ids and names as a point-in-time snapshot so a row survives forever regardless
    of what later happens to either identity (deletion included). There is no update path anywhere.

    Deleting a user cascades to their agents, but not to a single row of their history."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    agent_id = models.CharField(max_length=36)
    agent_name = models.CharField(max_length=255)
    user_id = models.CharField(max_length=36)
    user_name = models.CharField(max_length=255)
    toolkit_slug = models.SlugField()
    action_slug = models.SlugField()
    params = models.JSONField()
    outcome = models.CharField(max_length=32, choices=AuditOutcome.choices)
    ticket_id = models.CharField(max_length=36, null=True, blank=True)
    message = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["toolkit_slug"]),
            models.Index(fields=["outcome"]),
            # Every read of this table is newest-first and almost always filtered by one identity,
            # so the composite indexes match the actual access pattern rather than just the columns.
            models.Index(fields=["agent_id", "-created_at"]),
            models.Index(fields=["user_id", "-created_at"]),
        ]


# Deliberately no Item model here. This database holds only the permission system's own domain —
# the seven models above. `items` is a mock external service: it owns its own data in its toolkit
# package (external_services/toolkits/<slug>/store.py), not in this schema. That keeps this database
# from needing a migration every time a mock toolkit's shape changes, and means swapping a mock
# toolkit for a real MCP server later never touches this file.


# --- Shared lookup helpers ---

def find_by_id(model, id_value):
    """Fetch a row by primary key, treating a malformed id as 'not found' rather than an error.
    Ids often arrive from an untrusted source (a JWT claim, a request body, an agent-supplied
    param) and a bad one should read as a miss, never crash."""
    try:
        return model.objects.filter(id=id_value).first()
    except (ValueError, ValidationError):
        return None


def get_agent(agent_id) -> "Agent | None":
    return find_by_id(Agent, agent_id)


def get_user(user_id) -> "User | None":
    return find_by_id(User, user_id)
