"""Who the agent is talking to, and what was said.

Split out from models.py to keep the claim itself readable: this file is the
book of record the agent verifies callers against, plus the transcript of every
call for tuning the prompt later.
"""

from django.db import models
from django.urls import reverse
from django.utils import timezone


class Policyholder(models.Model):
    """A customer on the books. The agent verifies against this before it says
    anything about a policy."""

    class Coverage(models.TextChoices):
        LIABILITY = "liability", "Liability only"
        COLLISION = "collision", "Collision"
        COMPREHENSIVE = "comprehensive", "Comprehensive"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        LAPSED = "lapsed", "Lapsed"
        CANCELLED = "cancelled", "Cancelled"

    policy_number = models.CharField(max_length=32, unique=True)
    full_name = models.CharField(max_length=120)
    phone = models.CharField(max_length=24, help_text="E.164, e.g. +15551234567")
    email = models.EmailField(blank=True)
    address = models.CharField(max_length=200, blank=True)

    coverage = models.CharField(
        max_length=20, choices=Coverage.choices, default=Coverage.COMPREHENSIVE
    )
    deductible = models.PositiveIntegerField(default=500)
    roadside_assistance = models.BooleanField(default=True)
    rental_cover = models.BooleanField(default=False)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    renewal_date = models.DateField(null=True, blank=True)

    # [{"year": 2019, "make": "Toyota", "model": "Camry", "colour": "silver",
    #   "plate": "8FRT229"}]
    vehicles = models.JSONField(default=list, blank=True)
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["full_name"]

    def __str__(self):
        return f"{self.policy_number} · {self.full_name}"

    @property
    def first_name(self):
        return self.full_name.split(" ")[0]

    @property
    def phone_last4(self):
        digits = "".join(c for c in self.phone if c.isdigit())
        return digits[-4:] if len(digits) >= 4 else ""

    @property
    def phone_masked(self):
        """What a dispatcher may see: everything but the middle."""
        digits = "".join(c for c in self.phone if c.isdigit())
        return f"•••• •••{digits[-4:]}" if len(digits) >= 4 else "—"

    def vehicle_line(self):
        """One spoken line listing the cars on the policy."""
        described = [
            " ".join(
                str(part)
                for part in (v.get("year"), v.get("colour"), v.get("make"), v.get("model"))
                if part
            )
            for v in self.vehicles or []
        ]
        described = [d for d in described if d]
        if not described:
            return ""
        if len(described) == 1:
            return described[0]
        return ", ".join(described[:-1]) + " and " + described[-1]

    def as_dict(self, reveal=False, demo=False):
        """`reveal` adds the last four digits and the full contact details, and
        belongs to the desk alone. `demo` is the narrow version for the
        Talk-to-Ivy card: the digits a tester needs to read out, and nothing
        else — no full number, no address, no email, no notes."""
        data = {
            "id": self.id,
            "policy_number": self.policy_number,
            "full_name": self.full_name,
            "first_name": self.first_name,
            "phone_masked": self.phone_masked,
            "email": self.email,
            "address": self.address,
            "coverage": self.coverage,
            "coverage_label": self.get_coverage_display(),
            "deductible": self.deductible,
            "roadside_assistance": self.roadside_assistance,
            "rental_cover": self.rental_cover,
            "status": self.status,
            "status_label": self.get_status_display(),
            "renewal_date": self.renewal_date.isoformat() if self.renewal_date else None,
            "vehicles": self.vehicles,
            "vehicle_line": self.vehicle_line(),
            "notes": self.notes,
            "claims": getattr(self, "claim_count", None),
            "calls": getattr(self, "call_count", None),
        }
        if data["claims"] is None:
            data["claims"] = self.claims.count()
        if data["calls"] is None:
            data["calls"] = 0
        if reveal:
            data["phone_last4"] = self.phone_last4
            data["phone"] = self.phone
        elif demo:
            data["phone_last4"] = self.phone_last4
            for private in ("address", "email", "notes"):
                data[private] = ""
        return data


class VerificationAttempt(models.Model):
    """Every identity check, passed or failed.

    Kept so a caller guessing at last-four digits runs out of attempts, and so
    a dispatcher can see that it happened.
    """

    policy_number = models.CharField(max_length=32, db_index=True)
    phone_last4 = models.CharField(max_length=8)
    passed = models.BooleanField()
    policyholder = models.ForeignKey(
        Policyholder, null=True, blank=True, on_delete=models.SET_NULL
    )
    conversation = models.ForeignKey(
        "claims.Conversation",
        null=True,
        blank=True,
        related_name="verifications",
        on_delete=models.SET_NULL,
    )
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.policy_number} {'passed' if self.passed else 'failed'}"


class Conversation(models.Model):
    """One call, turn by turn.

    The transcript is posted by whatever is holding the session — the browser
    page has every word, so it sends them. A phone call has no browser, so that
    record is built from the tool calls and topped up from the sessions API.
    """

    class Channel(models.TextChoices):
        BROWSER = "browser", "Browser"
        PHONE = "phone", "Phone"
        SIMULATOR = "simulator", "Simulator"

    session_id = models.CharField(max_length=80, unique=True, null=True, blank=True)
    agent_id = models.CharField(max_length=64, blank=True)
    channel = models.CharField(
        max_length=16, choices=Channel.choices, default=Channel.BROWSER
    )
    caller_number = models.CharField(max_length=24, blank=True)

    policyholder = models.ForeignKey(
        Policyholder,
        null=True,
        blank=True,
        related_name="conversations",
        on_delete=models.SET_NULL,
    )
    verified = models.BooleanField(default=False)

    # [{"role": "agent|caller|tool|system", "text": "...", "at": 12.4}]
    turns = models.JSONField(default=list, blank=True)
    tool_calls = models.JSONField(default=list, blank=True)

    # The audio of the call, uploaded by whoever held it. Stereo: the caller on
    # the left, Ivy on the right, so you can hear who talked over whom.
    recording = models.FileField(upload_to="recordings/", blank=True, null=True)
    recording_bytes = models.PositiveIntegerField(default=0)

    started_at = models.DateTimeField(default=timezone.now, db_index=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.FloatField(default=0)
    # How the transport closed (client_end, session_ended) versus why the agent
    # chose to hang up. They answer different questions and the second is the
    # interesting one, so a client reporting the first must not erase it.
    close_reason = models.CharField(max_length=64, blank=True)
    end_reason = models.CharField(max_length=64, blank=True)
    needs_human = models.BooleanField(default=False)
    handoff_reason = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        who = self.policyholder.full_name if self.policyholder else "unidentified"
        return f"{self.started_at:%H:%M} · {who}"

    @property
    def claim(self):
        return self.claims.first()

    def summary_line(self):
        """What the list row says when there is no transcript to quote."""
        if self.turns:
            first = next((t for t in self.turns if t.get("role") == "caller"), None)
            if first:
                return first.get("text", "")[:120]
        if self.tool_calls:
            return ", ".join(sorted({c.get("name", "") for c in self.tool_calls}))
        return ""

    def as_dict(self, include_turns=False):
        claim = self.claim
        data = {
            "id": self.id,
            "session_id": self.session_id,
            "channel": self.channel,
            "channel_label": self.get_channel_display(),
            "caller_number": self.caller_number,
            "verified": self.verified,
            "policyholder": self.policyholder.as_dict() if self.policyholder else None,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "duration_seconds": round(self.duration_seconds, 1),
            "close_reason": self.close_reason,
            "end_reason": self.end_reason,
            "turn_count": len(self.turns or []),
            "recording_url": (
                reverse("conversation-recording", args=[self.id]) if self.recording else None
            ),
            "recording_bytes": self.recording_bytes,
            "tool_count": len(self.tool_calls or []),
            "summary": self.summary_line(),
            "claim_id": claim.id if claim else None,
            "claim_reference": f"CV-{claim.id:05d}" if claim else None,
            "needs_human": self.needs_human,
            "handoff_reason": self.handoff_reason,
        }
        if include_turns:
            data["turns"] = self.turns or []
            data["tool_calls"] = self.tool_calls or []
            data["verifications"] = [
                {
                    "policy_number": v.policy_number,
                    "passed": v.passed,
                    "at": v.created_at.isoformat(),
                }
                for v in self.verifications.all()
            ]
        return data


class Dispatch(models.Model):
    """Something sent to the scene.

    The risk engine raises one automatically when a claim needs a tow. A
    dispatcher raises the rest by hand — a second truck, an adjuster, a
    locksmith — which is why `raised_by` distinguishes them.
    """

    class Kind(models.TextChoices):
        TOW = "tow", "Tow truck"
        ADJUSTER = "adjuster", "Adjuster"
        INVESTIGATOR = "investigator", "Theft investigator"
        LOCKSMITH = "locksmith", "Locksmith"
        RENTAL = "rental", "Replacement vehicle"
        AMBULANCE = "ambulance", "Medical"

    class Status(models.TextChoices):
        REQUESTED = "requested", "Requested"
        EN_ROUTE = "en_route", "En route"
        ARRIVED = "arrived", "Arrived"
        CANCELLED = "cancelled", "Cancelled"

    claim = models.ForeignKey(
        "claims.Claim", related_name="dispatches", on_delete=models.CASCADE
    )
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.TOW)
    vendor = models.CharField(max_length=120, blank=True)
    vendor_phone = models.CharField(max_length=24, blank=True)
    eta_minutes = models.PositiveSmallIntegerField(null=True, blank=True)
    notes = models.CharField(max_length=255, blank=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.REQUESTED
    )
    # "system" when the risk engine raised it on ingest, otherwise the
    # dispatcher who did.
    raised_by = models.CharField(max_length=60, default="system")
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "dispatches"

    def __str__(self):
        return f"{self.get_kind_display()} for CV-{self.claim_id:05d}"

    @property
    def automatic(self):
        return self.raised_by == "system"

    @property
    def remaining_minutes(self):
        if self.eta_minutes is None:
            return None
        if self.status == self.Status.ARRIVED:
            return 0
        if self.status == self.Status.CANCELLED:
            return None
        elapsed = (timezone.now() - self.created_at).total_seconds() / 60
        return max(0, int(round(self.eta_minutes - elapsed)))

    def as_dict(self):
        return {
            "id": self.id,
            "claim_id": self.claim_id,
            "kind": self.kind,
            "kind_label": self.get_kind_display(),
            "vendor": self.vendor,
            "vendor_phone": self.vendor_phone,
            "eta_minutes": self.eta_minutes,
            "remaining_minutes": self.remaining_minutes,
            "notes": self.notes,
            "status": self.status,
            "status_label": self.get_status_display(),
            "raised_by": self.raised_by,
            "automatic": self.automatic,
            "created_at": self.created_at.isoformat(),
        }
