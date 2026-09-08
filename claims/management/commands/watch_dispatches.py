"""Notice when a tow is late, and do something about it.

    python manage.py watch_dispatches            # one pass
    python manage.py watch_dispatches --loop 60  # keep watching

One pass per run, so a cron entry or a Render cron job is enough. `--loop`
keeps it in the foreground for a demo, where nobody wants to wait for cron.

A pass chases the operator the first time a tow runs over, and reassigns to a
closer one the second time. The caller is told at each step, but not more often
than the update window allows — a driver at the roadside does not need their
phone buzzing every thirty seconds.
"""

import time

from django.core.management.base import BaseCommand

from claims.vendor_calls import chase, outbound_ready, overdue_calls


class Command(BaseCommand):
    help = "Chase and reassign tows that have run past their promised time"

    def add_arguments(self, parser):
        parser.add_argument(
            "--loop",
            type=int,
            default=0,
            metavar="SECONDS",
            help="Keep watching, checking this often.",
        )
        parser.add_argument(
            "--dry-run", action="store_true", help="Report without acting."
        )

    def handle(self, *args, **options):
        ready, detail = outbound_ready()
        if not ready:
            self.stdout.write(
                self.style.WARNING(
                    f"Outbound calling is off ({detail}) — chases are recorded and the "
                    "caller is still updated, but no operator is dialled."
                )
            )
        while True:
            self.pass_once(options["dry_run"])
            if not options["loop"]:
                return
            time.sleep(options["loop"])

    def pass_once(self, dry):
        late = overdue_calls()
        if not late:
            self.stdout.write("Nothing overdue.")
            return
        for dispatch, minutes in late:
            claim = dispatch.claim
            line = (
                f"CV-{claim.id:05d} · {dispatch.vendor or 'operator'} · "
                f"{minutes} min past the promised {dispatch.eta_minutes} min"
            )
            if dry:
                self.stdout.write(f"  would chase {line}")
                continue
            result = chase(dispatch, minutes)
            action = (
                f"reassigned to {result.vendor_name}"
                if result and result.purpose == "reassign"
                else "chased the operator"
                if result
                else "handed to a dispatcher"
            )
            self.stdout.write(self.style.SUCCESS(f"  {line} — {action}"))
