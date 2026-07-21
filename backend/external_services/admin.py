"""Django admin — the browsable UI the brief asks for (the Prisma Studio equivalent).

The audit log is registered read-only, so its append-only, immutable nature is enforced in the UI
too: no adding, editing, or deleting rows through the admin.
"""

from django.contrib import admin

from .models import Action, Agent, ApprovalTicket, AuditLog, PermissionOverride, Toolkit


@admin.register(Agent)
class AgentAdmin(admin.ModelAdmin):
    list_display = ("name", "id", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("name", "id")


@admin.register(Toolkit)
class ToolkitAdmin(admin.ModelAdmin):
    list_display = ("slug", "name", "created_at")
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
    list_display = ("id", "agent", "action", "status", "created_at", "expires_at", "resolved_at")
    list_filter = ("status",)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "agent_name", "toolkit_slug", "action_slug", "outcome", "ticket_id")
    list_filter = ("outcome", "toolkit_slug")
    search_fields = ("agent_id", "agent_name", "action_slug")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
