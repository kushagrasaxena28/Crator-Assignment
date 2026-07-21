"""Register the toolkit/action catalog and seed the demo agent.

Idempotent — safe to re-run; it syncs the database to match each toolkit's `catalog.py` under
`external_services/toolkits/`. This is the registration layer: once these rows exist, the rest of
the system operates purely on Toolkit / Action / schema / permission and never cares where the
definitions came from.

There is no demo *data* to seed here beyond the agent — `items` and `github` are mock external
services, like `notion` is a real one, so each pre-seeds its own sample data inside its own
package (external_services/toolkits/<slug>/store.py), not through this command.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from external_services.models import Action, Agent, PermissionValue, Toolkit
from external_services.toolkits import TOOLKITS

# Fixed so re-seeding always reproduces the same id — the agent CLI's AGENT_ID never needs to
# change between runs.
DEMO_AGENT_ID = "00000000-0000-0000-0000-000000000001"


class Command(BaseCommand):
    help = "Register the toolkit/action catalog and seed the demo agent (idempotent)."

    @transaction.atomic
    def handle(self, *args, **options):
        agent, _ = Agent.objects.update_or_create(
            id=DEMO_AGENT_ID, defaults={"name": "demo-agent", "is_active": True}
        )
        self._register_catalog()

        self.stdout.write(self.style.SUCCESS("Seeded:"))
        self.stdout.write(f"  demo agent id: {agent.id}")
        self.stdout.write("  items/github mock data: pre-seeded in their own toolkit packages")

    def _register_catalog(self):
        for toolkit_def in TOOLKITS:
            toolkit, _ = Toolkit.objects.update_or_create(
                slug=toolkit_def["slug"],
                defaults={"name": toolkit_def["name"], "description": toolkit_def["description"]},
            )
            for action in toolkit_def["actions"]:
                assert action["default_permission"] in PermissionValue.values, (
                    f"Bad default_permission for {toolkit_def['slug']}.{action['slug']}"
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
