"""Reconcile the call log with AssemblyAI's own record of the sessions.

    python manage.py sync_calls
    python manage.py sync_calls --limit 100

The browser posts a transcript; a phone call has no browser to post one. This
fills the gap from the sessions API, which knows every call happened, how long
it lasted and how it ended — just not what was said. Rows the page already
wrote are topped up rather than replaced.
"""

from django.core.management.base import BaseCommand, CommandError

from claims.agent_api import AgentApiError
from claims.sync import sync_sessions


class Command(BaseCommand):
    help = "Pull session metadata from AssemblyAI into the conversation log"

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=50)
        parser.add_argument(
            "--prune",
            action="store_true",
            help="Delete sessionless call rows that never gathered anything.",
        )

    def handle(self, *args, **options):
        try:
            result = sync_sessions(limit=options["limit"], prune=options["prune"])
        except AgentApiError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"{result['sessions']} sessions · {result['created']} new call records · "
                f"{result['updated']} topped up"
            )
        )
        if options["prune"]:
            self.stdout.write(f"Pruned {result['pruned']} empty call records")
