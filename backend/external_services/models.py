"""Database models for the permissions-and-audit layer.

The three enums are stored as their string values (matching the REST contract and the values the
CLI sends). The audit log is deliberately decoupled from `Agent` — see `AuditLog` below.
"""

import uuid

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


class Agent(models.Model):
    """An identity that calls the layer. Referenced everywhere by its UUID; `name` is for display
    and logging only and never participates in authorization."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.name} ({self.id})"


class Toolkit(models.Model):
    """One external-service surface (items, github, notion, ...)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=255)
    description = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.slug


class Action(models.Model):
    """One callable action on a toolkit, with its stored input/output JSON schemas and the
    default permission applied when an agent has no override."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    toolkit = models.ForeignKey(Toolkit, on_delete=models.CASCADE, related_name="actions")
    slug = models.SlugField()
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

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
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
    """A held `requires_approval` call, awaiting a human decision."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name="approval_tickets")
    action = models.ForeignKey(Action, on_delete=models.CASCADE, related_name="approval_tickets")
    params = models.JSONField()
    status = models.CharField(max_length=16, choices=TicketStatus.choices, default=TicketStatus.PENDING)
    result = models.JSONField(null=True, blank=True)
    rejection_reason = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    resolved_at = models.DateTimeField(null=True, blank=True)


class AuditLog(models.Model):
    """Append-only record of every attempt. Deliberately has NO foreign key to `Agent`: it stores
    `agent_id` / `agent_name` as a point-in-time snapshot so a row survives forever regardless of
    what later happens to the agent (deletion included). There is no update path anywhere."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent_id = models.CharField(max_length=36)
    agent_name = models.CharField(max_length=255)
    toolkit_slug = models.SlugField()
    action_slug = models.SlugField()
    params = models.JSONField()
    outcome = models.CharField(max_length=32, choices=AuditOutcome.choices)
    ticket_id = models.CharField(max_length=36, null=True, blank=True)
    message = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["agent_id"]),
            models.Index(fields=["toolkit_slug"]),
            models.Index(fields=["outcome"]),
        ]


# Deliberately no Item/Repo/Issue models here. This database holds only the permission system's
# own domain — the six models above. `items` and `github` are mock external services exactly like
# `notion` is a real one: each owns its own data in its toolkit package
# (external_services/toolkits/<slug>/store.py), not in this schema. That keeps this database from
# needing a migration every time a mock toolkit's shape changes, and means swapping a mock toolkit
# for a real MCP later never touches this file.


# --- Shared lookup helper ---

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
