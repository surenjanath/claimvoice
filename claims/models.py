"""The system of record: one row per First Notice of Loss."""

import secrets

from django.db import models
from django.utils import timezone

from .models_desk import (  # noqa: F401  (re-exported: claims.models is the import surface)
    DeskAction,
    Dispatcher,
    Handoff,
    HandoffAttempt,
    Shift,
)
from .models_policy import (  # noqa: F401  (re-exported: claims.models is the import surface)
    Conversation,
    Dispatch,
    Policyholder,
    VendorCall,
    VerificationAttempt,
)


class IncidentType(models.TextChoices):
    COLLISION = "collision", "Collision"
    THEFT = "theft", "Theft"
    WEATHER = "weather", "Weather"


class Severity(models.TextChoices):
    """The worst thing the caller reported, picked by the agent from a list."""

    AIRBAGS = "airbags_deployed", "Airbags deployed"
    FIRE = "fire_or_smoke", "Fire or smoke"
    LEAK = "fluid_leak", "Fluid leak"
    ROLLOVER = "rollover", "Rollover"
    COSMETIC = "glass_or_dents", "Glass or dents"
    NONE = "none", "No major damage"


class ClaimStatus(models.TextChoices):
    NEW = "new", "New"
    DISPATCHED = "dispatched", "Dispatched"
    CLOSED = "closed", "Closed"


class Claim(models.Model):
    """A claim as the voice agent extracted it, plus what we decided about it.

    Everything above `risk_score` comes off the wire from AssemblyAI; the score,
    the priority band and the dispatch decision are computed here on ingest.
    """

    policy_number = models.CharField(max_length=64)
    incident_type = models.CharField(max_length=16, choices=IncidentType.choices)
    location = models.CharField(max_length=255)
    is_drivable = models.BooleanField()

    # Optional context. The agent volunteers these when the caller mentions
    # them; none of them gate the tool call.
    caller_name = models.CharField(max_length=120, blank=True)
    injuries_reported = models.BooleanField(null=True, blank=True)
    vehicle = models.CharField(max_length=120, blank=True)
    severity = models.CharField(max_length=32, blank=True, choices=Severity.choices)
    # Written on ingest from the parts above. The agent cannot compose it: a
    # free-text field in the tool schema stops the API firing the tool at all.
    description = models.TextField(blank=True)

    risk_score = models.PositiveSmallIntegerField(default=0)
    risk_factors = models.JSONField(default=list, blank=True)
    tow_required = models.BooleanField(default=False)
    status = models.CharField(
        max_length=16, choices=ClaimStatus.choices, default=ClaimStatus.NEW
    )

    # Kept for debugging a live demo: the exact JSON AssemblyAI sent.
    # Who it was filed for and the call it came from, when we know.
    policyholder = models.ForeignKey(
        "claims.Policyholder",
        null=True,
        blank=True,
        related_name="claims",
        on_delete=models.SET_NULL,
    )
    conversation = models.ForeignKey(
        "claims.Conversation",
        null=True,
        blank=True,
        related_name="claims",
        on_delete=models.SET_NULL,
    )

    raw_payload = models.JSONField(default=dict, blank=True)
    source = models.CharField(max_length=32, default="voice_agent")
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    # The share link is public — it goes out by text to a driver at the
    # roadside, who cannot be asked to log in. So the identifier in it has to
    # be the secret: a sequential id makes every other claim readable by
    # counting. Generated on save, never reused.
    share_token = models.CharField(max_length=43, unique=True, blank=True, db_index=True)

    lat = models.FloatField(null=True, blank=True)
    lng = models.FloatField(null=True, blank=True)
    needs_human = models.BooleanField(default=False)
    handoff_reason = models.CharField(max_length=64, blank=True)
    sms_status = models.CharField(max_length=16, blank=True)
    sms_sent_at = models.DateTimeField(null=True, blank=True)
    email_status = models.CharField(max_length=16, blank=True)
    assigned_to = models.CharField(max_length=80, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["-created_at"])]

    def __str__(self):
        return f"{self.policy_number} · {self.get_incident_type_display()}"

    @property
    def priority(self):
        if self.risk_score >= 75:
            return "critical"
        if self.risk_score >= 50:
            return "high"
        if self.risk_score >= 25:
            return "medium"
        return "low"

    def as_dict(self):
        """Shape the dashboard polls for."""
        return {
            "id": self.id,
            "policy_number": self.policy_number,
            "incident_type": self.incident_type,
            "incident_label": self.get_incident_type_display(),
            "location": self.location,
            "is_drivable": self.is_drivable,
            "caller_name": self.caller_name,
            "injuries_reported": self.injuries_reported,
            "vehicle": self.vehicle,
            "severity": self.severity,
            "severity_label": self.get_severity_display() if self.severity else "",
            "description": self.description,
            "risk_score": self.risk_score,
            "risk_factors": self.risk_factors,
            "priority": self.priority,
            "tow_required": self.tow_required,
            "status": self.status,
            "status_label": self.get_status_display(),
            "source": self.source,
            "created_at": self.created_at.isoformat(),
            "policyholder_id": self.policyholder_id,
            "policyholder_name": self.policyholder.full_name if self.policyholder else "",
            "conversation_id": self.conversation_id,
            "verified": bool(self.conversation and self.conversation.verified),
            "dispatches": [d.as_dict() for d in self.dispatches.all()],
            # Sliced in Python, not in the query: .all()[:20] would go back to
            # the database even when the rows are already prefetched.
            "vendor_calls": [v.as_dict() for v in list(self.vendor_calls.all())[:20]],
            "lat": self.lat,
            "lng": self.lng,
            "needs_human": self.needs_human,
            "handoff_reason": self.handoff_reason,
            "flags": self.flags(),
            "photo_count": len(self.photos.all()),
            "share_path": self.share_path,
            "sms_status": self.sms_status,
            "sms_sent_at": self.sms_sent_at.isoformat() if self.sms_sent_at else None,
            "email_status": self.email_status,
            "notified": self.sms_status == "sent" or self.email_status == "sent",
            "assigned_to": self.assigned_to,
            "notes": [n.as_dict() for n in self.notes.all()[:12]],
            "coverage": self.policyholder.coverage if self.policyholder_id and self.policyholder else "",
            "coverage_label": (
                self.policyholder.get_coverage_display()
                if self.policyholder_id and self.policyholder
                else ""
            ),
            "deductible": self.policyholder.deductible if self.policyholder_id and self.policyholder else None,
            "roadside_assistance": (
                self.policyholder.roadside_assistance
                if self.policyholder_id and self.policyholder
                else None
            ),
            "rental_cover": (
                self.policyholder.rental_cover if self.policyholder_id and self.policyholder else None
            ),
            "policy_status": self.policyholder.status if self.policyholder_id and self.policyholder else "",
            "vehicle_on_file": self.policyholder.vehicle_line() if self.policyholder_id and self.policyholder else "",
            "vehicle_mismatch": self.vehicle_mismatch(),
        }

    def vehicle_mismatch(self):
        said = (self.vehicle or "").lower()
        if not said or not self.policyholder_id or not self.policyholder:
            return False
        book = " ".join(
            " ".join(str(part.get(key) or "") for key in ("year", "colour", "make", "model", "plate"))
            for part in (self.policyholder.vehicles or [])
        ).lower()
        if not book.strip():
            return False
        tokens = [token for token in said.replace(",", " ").split() if len(token) > 2]
        return bool(tokens) and not any(token in book for token in tokens)

    def flags(self):
        marks = []
        verified = bool(self.conversation and self.conversation.verified)
        if self.assigned_to:
            marks.append("owned")
        if self.needs_human:
            marks.append("handoff")
        if not verified:
            marks.append("unverified")
        if self.policyholder_id and self.policyholder and self.policyholder.status != "active":
            marks.append("lapsed")
        if self.risk_score >= 75 and not verified:
            marks.append("fraud watch")
        if self.vehicle_mismatch():
            marks.append("vehicle mismatch")
        return marks

    @property
    def reference(self):
        return f"CV-{self.id:05d}"

    @property
    def share_path(self):
        """Where the caller's copy of this claim lives."""
        return f"/c/{self.share_token}/" if self.share_token else ""

    def save(self, *args, **kwargs):
        if not self.share_token:
            self.share_token = secrets.token_urlsafe(24)
        super().save(*args, **kwargs)


class AgentProfile(models.Model):
    """The live agent configuration, editable from /settings/.

    agent.json is the seed: the first request copies it in here, and from then
    on this row is the source of truth that gets published to AssemblyAI. That
    keeps the file as a readable, committable default while letting the voice,
    prompt and turn taking be tuned without a redeploy.
    """

    VOICES = [
        ("anna", "Anna — British"),
        ("charles", "Charles — British"),
        ("paul", "Paul — British"),
        ("vera", "Vera — British"),
        ("alba", "Alba — American"),
        ("eve", "Eve — American"),
        ("george", "George — American"),
        ("jane", "Jane — American"),
        ("jean", "Jean — American"),
        ("mary", "Mary — American"),
        ("michael", "Michael — American"),
        ("giovanni", "Giovanni — Italian"),
        ("lola", "Lola — Spanish"),
        ("juergen", "Juergen — German"),
        ("rafael", "Rafael — Portuguese"),
        ("estelle", "Estelle — French"),
    ]
    EXECUTION_MODES = [
        ("hold", "Hold — Ivy waits quietly while the claim is filed"),
        ("interactive", "Interactive — Ivy keeps talking while it runs"),
    ]

    name = models.CharField(max_length=120, default="ClaimVoice FNOL Intake")
    system_prompt = models.TextField()
    greeting = models.CharField(max_length=500)
    voice_id = models.CharField(max_length=32, choices=VOICES, default="anna")
    volume = models.PositiveSmallIntegerField(default=100)

    keyterms = models.JSONField(default=list, blank=True)
    vad_threshold = models.FloatField(default=0.45)
    min_silence = models.PositiveIntegerField(default=700)
    max_silence = models.PositiveIntegerField(default=2600)
    interrupt_response = models.BooleanField(default=True)

    execution_mode = models.CharField(
        max_length=16, choices=EXECUTION_MODES, default="hold"
    )
    timeout_seconds = models.PositiveSmallIntegerField(default=20)

    # Where AssemblyAI posts log_claim. Without it the tool cannot be published
    # at all: the API stores http tools only, and drops client-side ones.
    public_base_url = models.URLField(blank=True)

    agent_id = models.CharField(max_length=64, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    # SHA-256 of to_agent_config() at last successful publish. Compared on
    # the settings page so "saved but not published" is a real state, not a guess.
    published_digest = models.CharField(max_length=64, blank=True)

    class Meta:
        verbose_name = "agent profile"

    def __str__(self):
        return f"{self.name} ({self.voice_id})"

    @classmethod
    def load(cls):
        """The single profile row, seeded from agent.json the first time."""
        from django.conf import settings as django_settings

        profile = cls.objects.first()
        if not profile:
            return cls.seed()
        # A fresh deploy knows its own hostname only at runtime, so adopt it
        # rather than making someone paste it in. An address already chosen by
        # hand is left alone.
        if not profile.public_base_url and django_settings.PUBLIC_BASE_URL:
            profile.public_base_url = django_settings.PUBLIC_BASE_URL
            profile.save(update_fields=["public_base_url"])
        return profile

    @classmethod
    def seed(cls, profile=None):
        """(Re)fill a profile from agent.json and the environment."""
        import json
        from pathlib import Path

        from django.conf import settings as django_settings

        raw = json.loads((Path(django_settings.BASE_DIR) / "agent.json").read_text())
        tool = next(t for t in raw.get("tools", []) if t["name"] == "log_claim")
        turn = (raw.get("input") or {}).get("turn_detection") or {}

        profile = profile or cls()
        profile.name = raw.get("name", profile.name)
        profile.system_prompt = raw.get("system_prompt", "")
        profile.greeting = raw.get("greeting", "")
        profile.voice_id = (raw.get("voice") or {}).get("voice_id", "anna")
        profile.keyterms = (raw.get("input") or {}).get("keyterms", [])
        profile.vad_threshold = turn.get("vad_threshold", 0.45)
        profile.min_silence = turn.get("min_silence", 700)
        profile.max_silence = turn.get("max_silence", 2600)
        profile.interrupt_response = turn.get("interrupt_response", True)
        profile.execution_mode = tool.get("execution_mode", "hold")
        profile.timeout_seconds = tool.get("timeout_seconds", 20)
        if not profile.public_base_url:
            profile.public_base_url = django_settings.PUBLIC_BASE_URL
        if not profile.agent_id:
            profile.agent_id = django_settings.ASSEMBLYAI_AGENT_ID
        profile.save()
        return profile

    # Which endpoint each tool in agent.json posts to. A tool named here is
    # published; anything else in the file is ignored, so the map is the list
    # of tools that actually exist.
    TOOL_ENDPOINTS = {
        "verify_policyholder": "/api/verify/",
        "log_claim": "/api/log-claim/",
        "end_call": "/api/end-call/",
        "request_human": "/api/request-human/",
    }

    @property
    def base_url(self):
        return (self.public_base_url or "").rstrip("/")

    @property
    def webhook_url(self):
        return f"{self.base_url}/api/log-claim/" if self.base_url else ""

    @property
    def tool_urls(self):
        if not self.base_url:
            return {}
        return {
            name: self.base_url + path for name, path in self.TOOL_ENDPOINTS.items()
        }

    def to_agent_config(self):
        """The request body for POST/PUT /v1/agents.

        The tool schemas live in agent.json — they are the contract with the
        backend, not knobs — so they are read from there and only the tunable
        parts are overlaid.
        """
        import json
        from pathlib import Path

        from django.conf import settings as django_settings

        raw = json.loads((Path(django_settings.BASE_DIR) / "agent.json").read_text())

        # The API stores http tools; one without an http block comes back with
        # http null, invisible to the model. So tools ship only when we have
        # somewhere for AssemblyAI to post them.
        tools = []
        for tool in raw.get("tools", []):
            url = self.tool_urls.get(tool["name"])
            if not url:
                continue
            # The settings page describes one knob — "while log_claim runs" —
            # so it steers that tool only. The others keep the mode their
            # schema chose: end_call must not hold the line silent while it
            # runs, since holding is the opposite of hanging up.
            if tool["name"] == "log_claim":
                tool["execution_mode"] = self.execution_mode
                tool["timeout_seconds"] = self.timeout_seconds
            tool["http"] = {"url": url, "http_method": "POST"}
            if django_settings.CLAIM_WEBHOOK_SECRET:
                tool["http"]["headers"] = [
                    {
                        "name": "X-Claim-Secret",
                        "value": django_settings.CLAIM_WEBHOOK_SECRET,
                    }
                ]
            tools.append(tool)

        return {
            "name": self.name,
            "system_prompt": self.system_prompt,
            "greeting": self.greeting,
            "voice": {"voice_id": self.voice_id},
            "input": {
                "keyterms": self.keyterms,
                "turn_detection": {
                    "vad_threshold": self.vad_threshold,
                    "min_silence": self.min_silence,
                    "max_silence": self.max_silence,
                    "interrupt_response": self.interrupt_response,
                },
            },
            "output": {"voice": self.voice_id, "volume": self.volume},
            "tools": tools,
        }

    def config_digest(self):
        """Stable fingerprint of the payload we last sent, or would send now."""
        import hashlib
        import json

        blob = json.dumps(self.to_agent_config(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()

    def unpublished_changes(self):
        """True when a prior publish exists and the saved row no longer matches it."""
        if not self.published_digest:
            return False
        return self.config_digest() != self.published_digest

    def snapshot_revision(self):
        """Keep what we just published so a later draft can be rolled back."""
        return AgentRevision.objects.create(
            profile=self,
            system_prompt=self.system_prompt,
            greeting=self.greeting,
            voice_id=self.voice_id,
            digest=self.published_digest or self.config_digest(),
        )


class ClaimNote(models.Model):
    """A line on the claim — dispatcher judgement or a message from the caller."""

    claim = models.ForeignKey(Claim, related_name="notes", on_delete=models.CASCADE)
    body = models.CharField(max_length=400)
    author = models.CharField(max_length=32, default="dispatcher")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["created_at"]

    def as_dict(self):
        return {
            "id": self.id,
            "body": self.body,
            "author": self.author,
            "created_at": self.created_at.isoformat(),
        }


class ClaimPhoto(models.Model):
    """A picture the caller or dispatcher attached to a filed claim."""

    claim = models.ForeignKey(Claim, related_name="photos", on_delete=models.CASCADE)
    image = models.FileField(upload_to="claim-photos/")
    caption = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["created_at"]

    def as_dict(self):
        return {
            "id": self.id,
            # Scoped to the claim's token: a photo id on its own is not a key.
            "url": f"/api/share/{self.claim.share_token}/photos/{self.id}/",
            "caption": self.caption,
            "created_at": self.created_at.isoformat(),
        }


class AgentRevision(models.Model):
    """One published prompt, so Settings can restore yesterday's Ivy."""

    profile = models.ForeignKey(
        AgentProfile, related_name="revisions", on_delete=models.CASCADE
    )
    system_prompt = models.TextField()
    greeting = models.CharField(max_length=500, blank=True)
    voice_id = models.CharField(max_length=32, blank=True)
    digest = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def as_dict(self):
        return {
            "id": self.id,
            "voice_id": self.voice_id,
            "digest": self.digest,
            "created_at": self.created_at.isoformat(),
            "prompt_preview": (self.system_prompt or "")[:160],
        }
