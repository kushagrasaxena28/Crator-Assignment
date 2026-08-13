"""Permission resolution — completely toolkit-agnostic. Given an agent and an action, it answers
one question: what is this agent's effective permission for this action? A per-agent override row
wins; otherwise the action's default applies. It never learns that any particular toolkit exists.
"""

from .models import PermissionOverride


def resolve_effective_permission(agent_id, action) -> str:
    override = PermissionOverride.objects.filter(agent_id=agent_id, action=action).first()
    return override.permission if override else action.default_permission


def effective_permission_map(agent_id, actions) -> dict:
    """Batched form for listing a toolkit's actions: {action_id: effective_permission}. One query
    for all overrides instead of one lookup per action."""
    overrides = PermissionOverride.objects.filter(agent_id=agent_id, action__in=actions)
    permission_by_action = {o.action_id: o.permission for o in overrides}
    return {action.id: permission_by_action.get(action.id, action.default_permission) for action in actions}
