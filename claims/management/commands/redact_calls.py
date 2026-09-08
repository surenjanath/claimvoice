"""Scrub verification digits out of calls already on disk.

    python manage.py redact_calls --dry-run
    python manage.py redact_calls

New calls are redacted as they arrive. This is for everything recorded before
that, and for the case where a recording landed before its transcript did, so
the spans were not known yet.

It rewrites in place and keeps no copy, because a copy of the thing you just
removed is not a redaction.
"""

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand

from claims.models import Conversation
from claims.redact import blank_spans, redact_tool_calls, redact_turns


class Command(BaseCommand):
    help = "Remove spoken verification digits from stored transcripts and recordings"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without touching anything.",
        )
        parser.add_argument(
            "--audio-only",
            action="store_true",
            help="Re-blank recordings from spans already recorded.",
        )

    def handle(self, *args, **options):
        dry = options["dry_run"]
        scrubbed_text = scrubbed_audio = 0

        for call in Conversation.objects.all().iterator():
            known = (
                call.policyholder.phone_last4
                if call.policyholder_id and call.policyholder
                else ""
            )
            fields = []

            if not options["audio_only"] and call.turns:
                cleaned, spans = redact_turns(call.turns, known_last4=known)
                if cleaned != call.turns:
                    scrubbed_text += 1
                    self.stdout.write(
                        f"  call {call.id}: {len(spans)} spoken answer(s) masked"
                    )
                    if not dry:
                        call.turns = cleaned
                        fields.append("turns")
                # Spans are worth keeping even when the text was already clean:
                # the audio still needs them.
                if spans and spans != (call.redactions or []):
                    if not dry:
                        call.redactions = spans
                        fields.append("redactions")
                    elif not cleaned != call.turns:
                        self.stdout.write(f"  call {call.id}: {len(spans)} window(s) to blank")

            tools = redact_tool_calls(call.tool_calls)
            if tools != call.tool_calls and not dry:
                call.tool_calls = tools
                fields.append("tool_calls")

            spans = call.redactions if dry else (call.redactions or [])
            if call.recording and spans:
                if dry:
                    scrubbed_audio += 1
                else:
                    try:
                        raw = call.recording.open("rb").read()
                    finally:
                        call.recording.close()
                    cleaned_audio = blank_spans(raw, spans)
                    if cleaned_audio != raw:
                        name = call.recording.name.split("/")[-1]
                        call.recording.delete(save=False)
                        call.recording.save(name, ContentFile(cleaned_audio), save=False)
                        call.recording_bytes = len(cleaned_audio)
                        fields += ["recording", "recording_bytes"]
                        scrubbed_audio += 1
                        self.stdout.write(f"  call {call.id}: audio blanked")

            if fields and not dry:
                call.save(update_fields=list(dict.fromkeys(fields)))

        verb = "would scrub" if dry else "scrubbed"
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb} {scrubbed_text} transcript(s) and {scrubbed_audio} recording(s)"
            )
        )
