"""Register the toolkit/action catalog and seed the demo user + agent.

Idempotent — safe to re-run; it syncs the database to match each toolkit's `catalog.py` under
`external_services/toolkits/`. This is the registration layer: once these rows exist, the rest of
the system operates purely on Toolkit / Action / schema / permission and never cares where the
definitions came from.

Syncing means pruning as well as upserting. Without a prune, deleting a toolkit from the code
leaves its rows — and therefore its callable actions — alive in the database forever.

There is no demo *data* to seed here beyond the identities: `items` is a mock external service, so
it pre-seeds its own sample data inside its own package
(external_services/toolkits/<slug>/store.py), not through this command.
"""

import os
import secrets

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from external_services.models import Action, Agent, PermissionValue, PolicyDefault, Toolkit, User
from external_services.toolkits import TOOLKITS

# Fixed so re-seeding always reproduces the same id — the agent CLI's AGENT_ID never needs to
# change between runs.
DEMO_AGENT_ID = "00000000-0000-0000-0000-000000000001"
DEMO_USERNAME = "demo"


class Command(BaseCommand):
    help = "Register the toolkit/action catalog and seed the demo user and agent (idempotent)."

    @transaction.atomic
    def handle(self, *args, **options):
        user, user_password = self._seed_user()
        agent, agent_secret = self._seed_agent(user)
        policy = PolicyDefault.current()
        self._register_catalog()
        self._prune_catalog()

        self.stdout.write(self.style.SUCCESS("\nSeeded:"))
        self.stdout.write(f"  user:     {user.username} ({user.id})")
        self.stdout.write(f"  agent:    {agent.name} ({agent.id})")
        self.stdout.write(f"  policy:   {policy}")
        self.stdout.write("  items mock data: pre-seeded in its own toolkit package")

        # Credentials are shown once, at the moment they are set, because only their hashes are
        # stored — there is no way to print them again on a later run.
        if user_password or agent_secret:
            self.stdout.write(self.style.WARNING("\nCredentials (shown once — copy them now):"))
            if user_password:
                self.stdout.write(f"  user password: {user_password}")
                self.stdout.write(self.style.HTTP_INFO(
                    "    → yours, for approving tickets. NEVER put this in agent-cli/.env."))
            if agent_secret:
                # Printed as the literal line to paste, because the alternative — copy the value,
                # open the file, find the key, replace the placeholder — is where this setup
                # usually goes wrong. A copied-but-unedited .env fails with a placeholder secret.
                self.stdout.write(f"  agent secret:  {agent_secret}")
                self.stdout.write(self.style.HTTP_INFO(
                    "    → paste this exact line into agent-cli/.env, replacing the placeholder:"))
                self.stdout.write(self.style.SUCCESS(f'        AGENT_SECRET="{agent_secret}"'))
        else:
            self.stdout.write(
                "\nExisting credentials left unchanged. Set DEMO_USER_PASSWORD / DEMO_AGENT_SECRET "
                "and re-run to rotate them."
            )

    def _seed_user(self):
        """Create the demo user, or leave an existing one untouched unless DEMO_USER_PASSWORD asks
        for a rotation. Returns (user, password_to_display_or_None)."""
        override = os.environ.get("DEMO_USER_PASSWORD")
        user = User.objects.filter(username=DEMO_USERNAME).first()

        if user is None:
            password = override or secrets.token_urlsafe(18)
            user = User(username=DEMO_USERNAME, first_name="Demo", is_active=True)
            user.set_password(password)
            user.save()
            return user, password

        if override:
            user.set_password(override)
            user.is_active = True
            user.save(update_fields=["password", "is_active"])
            return user, override

        return user, None

    def _seed_agent(self, user):
        """Same contract as _seed_user: never silently rotate a secret that the CLI's .env already
        holds, because that would break a working checkout on an unrelated re-seed."""
        override = os.environ.get("DEMO_AGENT_SECRET")
        agent = Agent.objects.filter(id=DEMO_AGENT_ID).first()

        if agent is None:
            agent_secret = override or secrets.token_urlsafe(24)
            agent = Agent(id=DEMO_AGENT_ID, owner=user, name="demo-agent", is_active=True)
            agent.set_secret(agent_secret)
            agent.save()
            return agent, agent_secret

        agent.owner = user
        agent.name = "demo-agent"
        agent.is_active = True
        if override:
            agent.set_secret(override)
            agent.save()
            return agent, override

        agent.save(update_fields=["owner", "name", "is_active"])
        return agent, None

    def _register_catalog(self):
        for toolkit_def in TOOLKITS:
            toolkit, _ = Toolkit.objects.update_or_create(
                slug=toolkit_def["slug"],
                mcp_server=None,  # built-in; never matches a user's plugged-in server of the same name
                defaults={"name": toolkit_def["name"], "description": toolkit_def["description"]},
            )
            for action in toolkit_def["actions"]:
                # A CommandError, not an assert: assertions are stripped under `python -O`, which
                # would let a typo'd permission reach the database silently.
                if action["default_permission"] not in PermissionValue.values:
                    raise CommandError(
                        f"Bad default_permission {action['default_permission']!r} for "
                        f"{toolkit_def['slug']}.{action['slug']}"
                    )
                Action.objects.update_or_create(
                    toolkit=toolkit,
                    slug=action["slug"],
                    defaults={
                        "name": action["name"],
                        "description": action["description"],
                        "default_permission": action["default_permission"],
                        "input_schema": action["input_schema"],
                        "output_schema": action["output_schema"],
                    },
                )

    def _prune_catalog(self):
        """Delete rows for built-in toolkits and actions that are no longer in the code catalog.

        Scoped to `mcp_server__isnull=True` — toolkits discovered from a user's own MCP server are
        not in `TOOLKITS` and never will be, so an unscoped prune would delete every plugged-in
        server the moment anyone re-ran seed. Those are kept in sync by their own
        `refresh_server` action instead.

        Deleting an action cascades to its permission overrides and approval tickets — but not to a
        single audit row, because AuditLog has no foreign key to anything. History outlives the
        catalog it describes, which is the whole reason that model was built the way it was."""
        catalog_slugs = {t["slug"] for t in TOOLKITS}

        stale_toolkits = Toolkit.objects.filter(mcp_server__isnull=True).exclude(slug__in=catalog_slugs)
        for toolkit in stale_toolkits:
            self.stdout.write(self.style.WARNING(f"  pruning toolkit no longer in catalog: {toolkit.slug}"))
        stale_toolkits.delete()

        for toolkit_def in TOOLKITS:
            toolkit = Toolkit.objects.filter(slug=toolkit_def["slug"], mcp_server__isnull=True).first()
            if toolkit is None:
                continue
            action_slugs = {a["slug"] for a in toolkit_def["actions"]}
            stale_actions = toolkit.actions.exclude(slug__in=action_slugs)
            for action in stale_actions:
                self.stdout.write(
                    self.style.WARNING(f"  pruning action no longer in catalog: {toolkit.slug}.{action.slug}")
                )
            stale_actions.delete()
