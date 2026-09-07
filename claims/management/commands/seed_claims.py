"""Fill the dashboard with plausible traffic, for screenshots and demos.

    python manage.py seed_claims --count 12
    python manage.py seed_claims --clear
"""

import random
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from claims.models import (
    Claim,
    ClaimStatus,
    Conversation,
    Policyholder,
    Severity,
    VerificationAttempt,
)
from claims.risk import score_claim

# incident, location, drivable, injuries, severity, vehicle, description
SCENARIOS = [
    ("collision", "I-95 northbound near exit 12", False, True, Severity.AIRBAGS,
     "silver Toyota Camry", "Rear-ended at speed on the highway, airbags deployed."),
    ("collision", "Corner of Main and 4th, Springfield", True, False, Severity.COSMETIC,
     "blue Honda Civic", "Low speed fender bender at the intersection, bumper damage only."),
    ("weather", "Riverside Drive underpass", False, False, Severity.LEAK,
     "white Nissan Leaf", "Drove into standing flood water, engine cut out."),
    ("theft", "Level 3, Westfield parking garage", False, False, Severity.NONE,
     "black Audi A4", "Vehicle gone when the owner returned from work."),
    ("collision", "Route 9 near the Bellview overpass", False, True, Severity.ROLLOVER,
     "red Jeep Wrangler", "Multi car pile up in the rain, vehicle rolled onto its side."),
    ("weather", "Home driveway, 14 Oakwood Lane", True, False, Severity.COSMETIC,
     "grey Subaru Outback", "Hail storm overnight, dented roof and cracked windshield."),
    ("collision", "Parking lot of the Grandview Mall", True, False, Severity.COSMETIC,
     "green Mini Cooper", "Backed into a bollard, small dent in the tailgate."),
    ("theft", "Outside 88 Chapel Street", False, False, Severity.NONE,
     "2019 Ford F-150", "Catalytic converter cut out overnight, vandalism to the rear panel."),
    ("weather", "County Road 6, two miles past the bridge", False, False, Severity.COSMETIC,
     "beige Volvo V60", "Fallen tree across the road struck the hood in high wind."),
    ("collision", "Freeway on-ramp at Grand Avenue", False, True, Severity.FIRE,
     "black BMW 3 Series", "Side swiped by a truck merging, smoke coming from the engine bay."),
    ("collision", "Elm Street outside the school", True, False, Severity.NONE,
     "white Kia Soul", "Clipped a wing mirror pulling out, everyone fine."),
    ("weather", "Mountain Pass Road, ice on the surface", False, False, Severity.LEAK,
     "silver Mazda CX-5", "Slid on ice into the guardrail at night."),
]




def transcript_for(holder, claim, incident_words, drivable):
    """A plausible call for a seeded claim.

    Written from the same script the live agent follows, so the Conversations
    tab shows the shape of a real call before anyone has made one.
    """
    spaced = " ".join(holder.policy_number) if holder else "P V"
    last4 = " ".join(holder.phone_last4) if holder else "0 0 0 0"
    car = holder.vehicles[0].get("model") if holder and holder.vehicles else "car"
    name = holder.first_name if holder else "there"
    lines = [
        ("agent", "ClaimVoice claims, this is Ivy. First things first, are you somewhere safe right now?"),
        ("caller", "Yes, I'm off the road."),
        ("agent", "I am glad you are safe. Before we go any further, I need to verify your account. Can you give me your policy number?"),
        ("caller", f"My policy number is {spaced}."),
        ("agent", "Thank you. And the last four digits of the phone number on the policy?"),
        ("caller", f"The last four digits are {last4}."),
        ("tool", 'verify_policyholder({"policy_number":"%s","phone_last4":"••••"})'
            % (holder.policy_number if holder else "")),
        ("agent", f"Thank you {name}, I have your policy here. Can you tell me what happened?"),
        ("caller", incident_words),
        ("agent", "I am sorry to hear that. Where did this happen?"),
        ("caller", claim.location),
        ("agent", f"I have that. Is the {car} still driveable?"),
        ("caller", "Yes, it drives fine." if drivable else "No, it is not driveable."),
        ("tool", 'log_claim({"policy_number":"%s","incident_type":"%s","is_drivable":"%s"})'
            % (claim.policy_number, claim.incident_type, "yes" if drivable else "no")),
        ("agent", f"I have filed your claim, reference C V {' '.join(f'{claim.id:05d}')}. "
                  + ("A tow truck is on the way." if claim.tow_required else "An adjuster will call you.")),
    ]
    at = 6.0
    turns = []
    for role, text in lines:
        turns.append({"role": role, "text": text, "at": round(at, 1)})
        at += 5.5 + len(text) / 22
    return turns, at


class Command(BaseCommand):
    help = "Create demo claims so the dashboard has something to show"

    def add_arguments(self, parser):
        parser.add_argument("--count", type=int, default=8)
        parser.add_argument("--clear", action="store_true", help="Delete claims first")

    def handle(self, *args, **options):
        if options["clear"]:
            deleted, _ = Claim.objects.all().delete()
            self.stdout.write(f"Deleted {deleted} claims")

        # Claims belong to people on the books, so the board, the directory and
        # the agent all agree about who exists. Without a book, the claims are
        # still made — they just show as unverified, which is honest.
        book = list(Policyholder.objects.all())
        if not book:
            self.stdout.write(
                self.style.WARNING(
                    "No policyholders yet — run `manage.py seed_policies` first for "
                    "claims that match the book."
                )
            )

        now = timezone.now()
        made = 0
        for index in range(options["count"]):
            incident, location, drivable, injuries, severity, vehicle, description = (
                SCENARIOS[index % len(SCENARIOS)]
            )
            score, factors, tow = score_claim(
                incident_type=incident,
                is_drivable=drivable,
                location=location,
                description=description,
                injuries_reported=injuries,
                severity=severity,
            )
            holder = book[index % len(book)] if book else None
            # A seeded claim uses the policyholder's own car when they have one,
            # so the row reads the way a real filed claim does.
            if holder and holder.vehicles:
                first = holder.vehicles[0]
                vehicle = " ".join(
                    str(part)
                    for part in (
                        first.get("year"),
                        first.get("colour"),
                        first.get("make"),
                        first.get("model"),
                    )
                    if part
                )

            claim = Claim.objects.create(
                policy_number=holder.policy_number if holder else f"PV{random.randint(100000, 999999)}",
                policyholder=holder,
                incident_type=incident,
                location=location,
                is_drivable=drivable,
                caller_name=holder.full_name if holder else "",
                injuries_reported=injuries,
                vehicle=vehicle,
                severity=severity,
                description=description,
                risk_score=score,
                risk_factors=factors,
                tow_required=tow,
                status=random.choice(
                    [ClaimStatus.NEW, ClaimStatus.DISPATCHED, ClaimStatus.CLOSED]
                )
                if index > 2
                else (ClaimStatus.DISPATCHED if tow else ClaimStatus.NEW),
                source="seed",
                created_at=now - timedelta(minutes=7 * index + random.randint(0, 5)),
            )
            # Most calls verify; leaving one that did not shows the dispatcher
            # what an unverified claim looks like on the board.
            verified = index % 5 != 3
            started = claim.created_at - timedelta(seconds=110)
            conversation = Conversation.objects.create(
                session_id=f"seed_{claim.id}",
                channel=Conversation.Channel.PHONE if index % 3 == 0 else Conversation.Channel.BROWSER,
                caller_number=holder.phone if (holder and index % 3 == 0) else "",
                policyholder=holder if verified else None,
                verified=verified,
                started_at=started,
                ended_at=claim.created_at,
                close_reason="client_end",
            )
            turns, length = transcript_for(holder, claim, description, drivable)
            if not verified:
                turns = turns[:7] + [
                    {"role": "agent", "text": "That does not match what I have on file. Could you repeat the policy number?", "at": 44.0},
                    {"role": "caller", "text": "Let me find the paperwork, hold on.", "at": 52.0},
                ]
            conversation.turns = turns
            conversation.tool_calls = [
                {"name": t["text"].split("(")[0], "arguments": {}, "at": t["at"]}
                for t in turns
                if t["role"] == "tool"
            ]
            conversation.duration_seconds = round(length, 1)
            conversation.save()
            VerificationAttempt.objects.create(
                policy_number=claim.policy_number,
                phone_last4=holder.phone_last4 if holder else "0000",
                passed=verified,
                policyholder=holder if verified else None,
                conversation=conversation,
                created_at=started,
            )
            claim.conversation = conversation
            claim.save(update_fields=["conversation"])
            made += 1

        self.stdout.write(
            self.style.SUCCESS(f"Created {made} demo claims, each with the call it came from")
        )
