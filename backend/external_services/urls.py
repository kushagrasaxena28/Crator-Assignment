"""All API routes in one place, mounted under /api/ by the project URLconf. Trailing slashes are
the Django default and match exactly what the agent CLI calls.
"""

from django.urls import path

from .views import approvals, audit, auth, permissions, toolkits

urlpatterns = [
    # Token issuance — one credential exchange per identity.
    path("auth/user/token/", auth.issue_user_access_token),
    path("auth/agent/token/", auth.issue_agent_access_token),

    # Catalog discovery + calling actions (agent token)
    path("external-services/toolkits/", toolkits.list_toolkits),
    path("external-services/toolkits/<slug:toolkit_slug>/actions/", toolkits.list_actions),
    path("external-services/toolkits/<slug:toolkit_slug>/actions/<slug:action_slug>/schema/", toolkits.action_schema),
    path("external-services/toolkits/<slug:toolkit_slug>/actions/<slug:action_slug>/call/", toolkits.call_action),

    # Approval workflow — the agent may poll its own ticket; only its owning user may resolve it.
    path("external-services/approvals/<uuid:ticket_id>/status/", approvals.approval_status),
    path("external-services/approvals/<uuid:ticket_id>/resolve/", approvals.resolve_approval),

    # Per-agent permission overrides, GET reads and PUT replaces (user token)
    path("external-services/agents/<uuid:agent_id>/permissions/", permissions.agent_permissions),

    # Audit log — readable with either token, scoped to whoever is asking.
    path("external-services/audit/", audit.query_audit),
]
