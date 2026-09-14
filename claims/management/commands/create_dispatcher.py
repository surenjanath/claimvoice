"""Add somebody to the desk.

    python manage.py create_dispatcher
    python manage.py create_dispatcher --name "Priya R" --email priya@example.com \\
        --phone +15550142887 --shifts "mon-fri 08:00-18:00"

Creating the first dispatcher switches the login from one shared password to
per-person accounts. That is the point — but it also means everyone else needs
an account before they can get back in, so it says so out loud.

Shifts are optional and a rota with none in it means the desk is always open.
Write them as `mon-fri 08:00-18:00`, `sat 09:00-13:00`, `fri 22:00-06:00`
(that last one runs through the night), comma-separated.
"""

import getpass
import re

from django.core.management.base import BaseCommand, CommandError

from claims.models import Dispatcher, Shift

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
E164 = re.compile(r"\+[1-9]\d{6,14}")
WINDOW = re.compile(
    r"^(?P<days>[a-z]{3}(?:-[a-z]{3})?)\s+(?P<from>\d{1,2}:\d{2})-(?P<to>\d{1,2}:\d{2})$"
)


def parse_shifts(text):
    """`mon-fri 08:00-18:00, sat 09:00-13:00` into (weekday, start, end) rows."""
    rows = []
    for chunk in [c.strip().lower() for c in (text or "").split(",") if c.strip()]:
        match = WINDOW.match(chunk)
        if not match:
            raise CommandError(
                f"Could not read the shift {chunk!r}. Write it like 'mon-fri 08:00-18:00'."
            )
        days = match.group("days")
        if "-" in days:
            first, last = days.split("-", 1)
            if first not in DAYS or last not in DAYS:
                raise CommandError(f"Unknown day in {days!r}. Use mon, tue, … sun.")
            start, end = DAYS.index(first), DAYS.index(last)
            span = (
                list(range(start, end + 1))
                if start <= end
                else list(range(start, 7)) + list(range(0, end + 1))
            )
        else:
            if days not in DAYS:
                raise CommandError(f"Unknown day {days!r}. Use mon, tue, … sun.")
            span = [DAYS.index(days)]
        for weekday in span:
            rows.append((weekday, match.group("from"), match.group("to")))
    return rows


class Command(BaseCommand):
    help = "Create a dispatcher who can log in and be rung"

    def add_arguments(self, parser):
        parser.add_argument("--name")
        parser.add_argument("--email")
        parser.add_argument("--phone", default="", help="E.164, like +15551234567")
        parser.add_argument("--password", help="Prompted for when omitted")
        parser.add_argument("--role", default="dispatcher", choices=["dispatcher", "supervisor"])
        parser.add_argument("--order", type=int, default=100, help="Lower is rung first")
        parser.add_argument("--shifts", default="", help='e.g. "mon-fri 08:00-18:00"')

    def handle(self, *args, **options):
        first = not Dispatcher.objects.exists()

        name = options["name"] or input("Name: ").strip()
        email = (options["email"] or input("Email: ")).strip().lower()
        phone = (options["phone"] or "").strip()
        if not name or not email:
            raise CommandError("A name and an email are both needed.")
        if phone and not E164.fullmatch(phone):
            raise CommandError(
                f"Phone must be E.164, like +15551234567 (got {phone!r})"
            )
        if Dispatcher.objects.filter(email__iexact=email).exists():
            raise CommandError(f"{email} is already on the desk.")

        password = options["password"] or getpass.getpass("Password: ")
        if len(password) < 8:
            raise CommandError("Use at least eight characters.")

        shifts = parse_shifts(options["shifts"])

        person = Dispatcher(
            name=name,
            email=email,
            phone=phone,
            role=options["role"],
            order=options["order"],
        )
        person.set_password(password)
        person.save()
        for weekday, starts, ends in shifts:
            Shift.objects.create(
                dispatcher=person, weekday=weekday, starts=starts, ends=ends
            )

        self.stdout.write(self.style.SUCCESS(f"Created {person}"))
        if phone:
            self.stdout.write(f"Transfers will ring {phone}.")
        else:
            self.stdout.write(
                self.style.WARNING(
                    "No phone number, so this person is on the board but never rung. "
                    "Re-run with --phone to change that."
                )
            )
        if shifts:
            self.stdout.write(f"{len(shifts)} shift(s) written.")
        else:
            self.stdout.write(
                "No shifts, so this person is on call whenever the rota is empty."
            )
        if first:
            self.stdout.write(
                self.style.WARNING(
                    "\nThis was the first account, so the desk login is now per person. "
                    "DESK_PASSWORD no longer works — everyone needs an account."
                )
            )
