"""Django admin — the browsable table UI over every model.

The audit log is registered read-only, so its append-only, immutable nature is enforced in the UI
too: no adding, editing, or deleting rows through the admin. The agent's secret hash is excluded
from its form — it is not a plaintext secret, but an editable hash field invites someone to paste
a raw value into it, which would silently create an unusable credential.

`User` uses Django's own `UserAdmin`, so it gets the proper password-change widget (a hashed
round-trip, never a raw text field) rather than a generic model form.
"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import (
    Action, Agent, ApprovalTicket, AuditLog, MCPServer, PermissionOverride, PolicyDefault,
    Toolkit, User,
)


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ("username", "email", "id", "is_active", "is_staff", "date_joined")
    list_filter = ("is_active", "is_staff", "is_superuser")
    search_fields = ("username", "email", "id")


@admin.register(Agent)
class AgentAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "id", "is_active", "created_at")
    list_filter = ("is_active", "owner")
    search_fields = ("name", "id")
    exclude = ("secret_hash",)
    # An agent secret can only be issued, never read back — `seed` prints it once and stores the
    # hash. There is deliberately no way to recover one from this screen.


@admin.register(MCPServer)
class MCPServerAdmin(admin.ModelAdmin):
    list_display = ("slug", "owner", "url", "is_enabled", "last_discovered_at", "last_error")
    list_filter = ("is_enabled", "owner")
    search_fields = ("slug", "name", "url")
    # `headers` carries the server's bearer token. It goes in through add_server and is never read
    # back out — not by the API, and not here.
    exclude = ("headers",)


@admin.register(PolicyDefault)
class PolicyDefaultAdmin(admin.ModelAdmin):
    list_display = ("default_permission_for_new_actions", "updated_at")

    def has_add_permission(self, request):
        # Singleton: `PolicyDefault.current()` creates the one row.
        return not PolicyDefault.objects.exists()


@admin.register(Toolkit)
class ToolkitAdmin(admin.ModelAdmin):
    list_display = ("slug", "name", "mcp_server", "created_at")
    list_filter = ("mcp_server",)
    search_fields = ("slug", "name")


@admin.register(Action)
class ActionAdmin(admin.ModelAdmin):
    list_display = ("slug", "toolkit", "name", "default_permission")
    list_filter = ("toolkit", "default_permission")
    search_fields = ("slug", "name")


@admin.register(PermissionOverride)
class PermissionOverrideAdmin(admin.ModelAdmin):
    list_display = ("agent", "action", "permission", "updated_at")
    list_filter = ("permission",)


@admin.register(ApprovalTicket)
class ApprovalTicketAdmin(admin.ModelAdmin):
    list_display = ("id", "agent", "requested_by_user", "action", "status", "created_at", "expires_at", "resolved_at")
    list_filter = ("status",)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "agent_name", "user_name", "toolkit_slug", "action_slug", "outcome", "ticket_id")
    list_filter = ("outcome", "toolkit_slug")
    search_fields = ("agent_id", "agent_name", "user_id", "user_name", "action_slug")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
