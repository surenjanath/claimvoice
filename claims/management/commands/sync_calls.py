"""Reconcile the call log with AssemblyAI's own record of the sessions.

    python manage.py sync_calls
    python manage.py sync_calls --limit 100

The browser posts a transcript; a phone call has no browser to post one. This
fills the gap from the sessions API, which knows every call happened, how long
it lasted and how it ended — just not what was said. Rows the page already
wrote are topped up rather than replaced.
"""

from datetime import datetime, timezone as dt_timezone

from django.core.management.base import BaseCommand, CommandError

from claims.agent_api import AgentApiError, api
from claims.models import AgentProfile, Conversation


def parse_time(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


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
        profile = AgentProfile.load()
        try:
            listing = api("/sessions")
        except AgentApiError as exc:
            raise CommandError(str(exc)) from exc

        sessions = (listing.get("sessions") or [])[: options["limit"]]
        created = updated = 0

        for session in sessions:
            if profile.agent_id and session.get("agent_id") != profile.agent_id:
                continue
            started = parse_time(session.get("created_at"))
            conversation, is_new = Conversation.objects.get_or_create(
                session_id=session["id"],
                defaults={
                    # A session we never saw from a browser was a phone call or
                    # a client that died before it posted anything.
                    "channel": Conversation.Channel.PHONE,
                    "agent_id": session.get("agent_id", ""),
                    "started_at": started or datetime.now(dt_timezone.utc),
                },
            )
            fields = []
            duration = session.get("duration_seconds") or 0
            if duration and not conversation.duration_seconds:
                conversation.duration_seconds = duration
                fields.append("duration_seconds")
            reason = session.get("public_close_reason") or ""
            if reason and not conversation.close_reason:
                conversation.close_reason = reason[:64]
                fields.append("close_reason")
            ended = parse_time(session.get("ended_at"))
            if ended and not conversation.ended_at:
                conversation.ended_at = ended
                fields.append("ended_at")
            if fields:
                conversation.save(update_fields=fields)
            created += is_new
            updated += bool(fields) and not is_new

        self.stdout.write(
            self.style.SUCCESS(
                f"{len(sessions)} sessions · {created} new call records · {updated} topped up"
            )
        )

        if options["prune"]:
            # A call with no words, no tools, no claim and no audio holds
            # nothing a dispatcher could act on. Most are sessions this command
            # itself created for calls that connected and went nowhere.
            removed = 0
            for conversation in Conversation.objects.filter(claims__isnull=True):
                if conversation.turns or conversation.tool_calls or conversation.recording:
                    continue
                conversation.delete()
                removed += 1
            self.stdout.write(f"Pruned {removed} empty call records")
