"""Close out a transfer whose outcome never came back.

    python manage.py watch_handoffs            # one pass
    python manage.py watch_handoffs --loop 30  # keep watching

Twilio always POSTs to the dial-status webhook when a <Dial> leg ends. If that
POST never lands — the app was down, the request failed — a caller stays
"ringing" on the board long after the call itself is over, with no dispatcher
any the wiser. One pass finds anything stuck past its ring window, closes it
as no-answer, and lets the fallback number know, same as any other overflow.
"""

import time

from django.core.management.base import BaseCommand

from claims import handoff as handoffs


class Command(BaseCommand):
    help = "Close out handoffs whose dial-status callback never arrived"

    def add_arguments(self, parser):
        parser.add_argument(
            "--loop",
            type=int,
            default=0,
            metavar="SECONDS",
            help="Keep watching, checking this often.",
        )

    def handle(self, *args, **options):
        while True:
            self.pass_once()
            if not options["loop"]:
                return
            time.sleep(options["loop"])

    def pass_once(self):
        stale = handoffs.stale_ringing()
        if not stale:
            self.stdout.write("Nothing stuck.")
            return
        for handoff in stale:
            handoffs.reap_stale(handoff)
            self.stdout.write(
                self.style.WARNING(f"  handoff {handoff.id}: no dial status arrived, closed")
            )
