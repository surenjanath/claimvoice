"""HTTP surface: the voice client, the tool webhook, and the dispatcher board."""

import json
import logging
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.db.models import Avg, Count, Q
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .agent_api import AgentApiError, api, mint_token, publish_agent, redacted_agent
from .forms import AgentProfileForm
from .models import (
    AgentProfile,
    Claim,
    ClaimStatus,
    Conversation,
    IncidentType,
    Policyholder,
    Severity,
)
from .views_identity import _conversation
from .risk import score_claim

log = logging.getLogger("claims")

# How long the same policy and incident counts as the same claim.
DUPLICATE_WINDOW = timedelta(minutes=10)

# The agent answers this one from an enum, so "yes"/"no" arrive as strings.
NOT_ASKED_WORDS = {"not_asked", "not asked", "unknown", "unsure", "none", ""}
TRUE_WORDS = {"true", "yes", "y", "1", "drivable", "affirmative", "correct"}
FALSE_WORDS = {"false", "no", "n", "0", "not drivable", "undrivable", "negative"}

# What the caller actually says, mapped onto the three categories the schema
# allows. The agent normally picks the enum itself; this catches the times it
# passes the caller's own word through.
INCIDENT_SYNONYMS = {
    "collision": "collision",
    "crash": "collision",
    "accident": "collision",
    "wreck": "collision",
    "rear-end": "collision",
    "rear end": "collision",
    "fender bender": "collision",
    "hit and run": "collision",
    "theft": "theft",
    "stolen": "theft",
    "stolen vehicle": "theft",
    "burglary": "theft",
    "break-in": "theft",
    "break in": "theft",
    "vandalism": "theft",
    "weather": "weather",
    "storm": "weather",
    "hail": "weather",
    "flood": "weather",
    "flooding": "weather",
    "hurricane": "weather",
    "tornado": "weather",
    "wind": "weather",
    "snow": "weather",
    "ice": "weather",
}


class PayloadError(ValueError):
    """A tool call we cannot turn into a claim.

    Carries the field at fault so the reply can tell the agent what to ask for
    rather than making it parse an error string.
    """

    def __init__(self, message, field=""):
        super().__init__(message)
        self.field = field


# What to ask the caller for, per field. The agent reads `instructions`, not
# the field name — "is_drivable" spoken down a phone line helps nobody.
ASK_FOR = {
    "policy_number": "their policy number",
    "incident_type": "what kind of incident this was",
    "location": "where the incident happened",
    "is_drivable": "whether the vehicle can still be driven — ask them straight out",
}


# --- payload handling ------------------------------------------------------


def coerce_bool(value, field):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in TRUE_WORDS:
            return True
        if text in FALSE_WORDS:
            return False
    raise PayloadError(f"{field} must be true or false, got {value!r}", field)


def coerce_incident(value):
    if not isinstance(value, str):
        raise PayloadError("incident_type is required", "incident_type")
    text = value.strip().lower()
    if text in IncidentType.values:
        return text
    if text in INCIDENT_SYNONYMS:
        return INCIDENT_SYNONYMS[text]
    for word, mapped in INCIDENT_SYNONYMS.items():
        if word in text:
            return mapped
    raise PayloadError(
        f"incident_type must be one of collision, theft or weather, got {value!r}",
        "incident_type",
    )


def unwrap(payload):
    """Find the tool arguments however the caller nested them.

    AssemblyAI posts the arguments as a flat JSON object. Test harnesses and
    the browser fallback wrap them, so both shapes are accepted.
    """
    if not isinstance(payload, dict):
        raise PayloadError("expected a JSON object")
    for key in ("arguments", "parameters", "input", "data", "claim"):
        inner = payload.get(key)
        if isinstance(inner, dict):
            return inner
    return payload


def parse_claim(payload):
    """Validate a tool payload into the fields the model needs."""
    data = unwrap(payload)

    policy = str(data.get("policy_number") or "").strip().upper().replace(" ", "")
    if not policy:
        raise PayloadError("policy_number is required", "policy_number")

    location = str(data.get("location") or "").strip()
    if not location:
        raise PayloadError("location is required", "location")

    if "is_drivable" not in data and "drivable" in data:
        data["is_drivable"] = data["drivable"]
    drivable = data.get("is_drivable")
    if drivable is None or str(drivable).strip().lower() in NOT_ASKED_WORDS:
        # The agent is allowed to admit it has not asked. That is the whole
        # point of the enum: a guessed boolean dispatches a tow nobody ordered.
        raise PayloadError("is_drivable is required", "is_drivable")
    data["is_drivable"] = drivable

    injuries = data.get("injuries_reported", data.get("injuries"))
    severity = str(data.get("severity") or "").strip().lower()
    fields = {
        "policy_number": policy[:64],
        "incident_type": coerce_incident(data.get("incident_type")),
        "location": location[:255],
        "is_drivable": coerce_bool(data["is_drivable"], "is_drivable"),
        "injuries_reported": (
            None if injuries in (None, "") else coerce_bool(injuries, "injuries_reported")
        ),
        "caller_name": str(data.get("caller_name") or "").strip()[:120],
        "vehicle": str(data.get("vehicle") or "").strip()[:120],
        "severity": severity if severity in Severity.values else "",
    }
    # The agent cannot compose prose without breaking the tool call, so the
    # readable line is assembled here from the parts it did send. A description
    # supplied directly (a test, a manual post) is kept as it is.
    fields["description"] = str(data.get("description") or "").strip() or summarise(fields)
    return fields


def summarise(fields):
    """One line for the dashboard, built from the extracted facts."""
    verb = {
        "collision": "Collision",
        "theft": "Theft",
        "weather": "Weather damage",
    }[fields["incident_type"]]
    parts = [f"{verb} at {fields['location']}"]
    if fields["vehicle"]:
        parts.append(f"Vehicle: {fields['vehicle']}")
    if fields["severity"] and fields["severity"] != "none":
        parts.append(Severity(fields["severity"]).label)
    parts.append("Not drivable" if not fields["is_drivable"] else "Still drivable")
    if fields["injuries_reported"] is True:
        parts.append("Injuries reported")
    elif fields["injuries_reported"] is False:
        parts.append("No injuries")
    return ". ".join(parts) + "."


def dispatch_message(claim):
    """The sentence the agent reads back to close the loop.

    Derived from the claim id rather than drawn fresh, so replaying a duplicate
    tool call quotes the same arrival time the caller was given the first time.
    """
    reference = f"CV-{claim.id:05d}"
    if claim.tow_required:
        eta = 18 + (claim.id * 7) % 18
        action = (
            f"A tow truck is being dispatched to {claim.location} "
            f"with an estimated arrival of {eta} minutes"
        )
    elif claim.incident_type == "theft":
        action = (
            "A theft investigator has been assigned and will call you within the hour"
        )
    else:
        action = (
            f"No tow is needed, so an adjuster will contact you about {claim.location} "
            "within one business day"
        )
    return f"Claim {reference} logged. {action}."


# --- pages -----------------------------------------------------------------


def voice(request):
    """The page judges talk to."""
    profile = AgentProfile.load()
    return render(
        request,
        "claims/voice.html",
        {
            "profile": profile,
            "agent_id": profile.agent_id,
            "has_key": bool(settings.ASSEMBLYAI_API_KEY),
            # Without a webhook URL the tool is not published at all, so the
            # page says so rather than letting someone talk into a dead end.
            "tool_live": bool(profile.webhook_url),
        },
    )


def dashboard(request):
    return render(request, "claims/dashboard.html", {})


# --- api -------------------------------------------------------------------


@require_GET
def token(request):
    """Mint a 60-second session token so the API key stays on the server."""
    try:
        return JsonResponse(mint_token())
    except AgentApiError as exc:
        log.error("Token request failed: %s", exc)
        return JsonResponse({"error": str(exc)}, status=502)


@require_GET
def agent(request):
    """The published agent as AssemblyAI stored it, with secrets stripped."""
    profile = AgentProfile.load()
    if not profile.agent_id:
        return JsonResponse(
            {"error": "No agent published yet. Open /settings/ and publish."},
            status=404,
        )
    try:
        return JsonResponse(redacted_agent(api(f"/agents/{profile.agent_id}")))
    except AgentApiError as exc:
        log.error("Agent fetch failed: %s", exc)
        return JsonResponse({"error": str(exc)}, status=502)


@csrf_exempt
def log_claim(request):
    """The webhook AssemblyAI's log_claim tool posts to.

    Scores the claim, saves it, and answers with a line the agent reads back to
    the caller. Errors answer 200 with a `message` too: a 4xx body the model
    cannot parse leaves the caller in silence, and the message is what it
    speaks either way. The `ok` flag and `error` field carry the real status.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    secret = settings.CLAIM_WEBHOOK_SECRET
    if secret and request.headers.get("X-Claim-Secret") != secret:
        log.warning("Rejected tool call with a bad secret")
        return JsonResponse({"ok": False, "error": "forbidden"}, status=403)

    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse(
            {
                "ok": False,
                "error": "invalid json",
                "message": "I could not file that claim. Please try again.",
            },
            status=200,
        )

    log.info("log_claim payload: %s", payload)

    try:
        fields = parse_claim(payload)
    except PayloadError as exc:
        log.warning("Rejected tool call: %s", exc)
        ask = ASK_FOR.get(exc.field, "the missing detail")
        # Kept on the call so the insights page can say which question the
        # agent keeps skipping. This is the signal for tuning the prompt.
        rejected_on = _conversation(
            payload if isinstance(payload, dict) else {}, request
        )
        rejected_on.tool_calls = (rejected_on.tool_calls or []) + [
            {
                "name": "log_claim",
                "rejected": True,
                "missing": exc.field,
                "arguments": unwrap(payload) if isinstance(payload, dict) else {},
            }
        ]
        rejected_on.save(update_fields=["tool_calls"])
        return JsonResponse(
            {
                "ok": False,
                "error": str(exc),
                "missing": exc.field,
                # Two audiences in one object: `message` is safe to say out
                # loud, `instructions` is for the agent and never spoken.
                "message": "I just need to check one more thing before I can file this.",
                "instructions": (
                    f"The claim was not filed. Ask the caller {ask}, wait for their "
                    "answer, then call log_claim again with everything you already have."
                ),
            },
            status=200,
        )

    # A caller who repeats themselves, or an agent that files twice for one
    # incident, must not become two tow trucks. Inside the window the same
    # policy and incident is treated as the same claim, and the original
    # reference is read back.
    recent = (
        Claim.objects.filter(
            policy_number=fields["policy_number"],
            incident_type=fields["incident_type"],
            created_at__gte=timezone.now() - DUPLICATE_WINDOW,
        )
        # Demo fixtures are not filings. Without this, seeding the board and
        # then making a call means the caller is read back a reference from a
        # claim that was never theirs, and no claim is filed at all.
        .exclude(source="seed")
        .order_by("-created_at")
        .first()
    )
    if recent:
        log.info("Duplicate tool call for %s, replaying claim %s", recent.policy_number, recent.id)
        return JsonResponse(
            {
                "ok": True,
                "duplicate": True,
                "message": dispatch_message(recent),
                "claim_reference": f"CV-{recent.id:05d}",
                "risk_score": recent.risk_score,
                "priority": recent.priority,
                "tow_required": recent.tow_required,
                "claim": recent.as_dict(),
            }
        )

    score, factors, tow_required = score_claim(
        incident_type=fields["incident_type"],
        is_drivable=fields["is_drivable"],
        location=fields["location"],
        description=fields["description"],
        injuries_reported=fields["injuries_reported"],
        severity=fields["severity"],
    )

    # Tie the claim to the book and to the call it came from. An unverified
    # caller still gets a claim — the dispatcher sees it was unverified rather
    # than the driver being turned away at the roadside.
    holder = Policyholder.objects.filter(
        policy_number=fields["policy_number"]
    ).first()
    conversation = _conversation(
        payload if isinstance(payload, dict) else {},
        request,
        channel_default=Conversation.Channel.PHONE,
    )
    if holder and not conversation.policyholder_id:
        conversation.policyholder = holder
        conversation.save(update_fields=["policyholder"])
    if not fields["caller_name"] and holder:
        fields["caller_name"] = holder.full_name

    claim = Claim.objects.create(
        **fields,
        policyholder=holder,
        conversation=conversation,
        risk_score=score,
        risk_factors=factors,
        tow_required=tow_required,
        status=ClaimStatus.DISPATCHED if tow_required else ClaimStatus.NEW,
        raw_payload=payload if isinstance(payload, dict) else {},
        source=request.headers.get("X-Claim-Source", "voice_agent")[:32],
    )
    conversation.tool_calls = (conversation.tool_calls or []) + [
        {"name": "log_claim", "arguments": unwrap(payload), "claim_id": claim.id}
    ]
    conversation.save(update_fields=["tool_calls"])

    message = dispatch_message(claim)
    if holder and claim.tow_required and not holder.roadside_assistance:
        message = (
            f"Claim CV-{claim.id:05d} logged. This policy does not include roadside "
            f"assistance, so a tow to {claim.location} can be arranged but it will be "
            "charged to the policyholder."
        )
    log.info("Claim %s logged, risk %s", claim.id, claim.risk_score)
    return JsonResponse(
        {
            "ok": True,
            "message": message,
            "claim_reference": f"CV-{claim.id:05d}",
            "risk_score": claim.risk_score,
            "priority": claim.priority,
            "tow_required": claim.tow_required,
            "claim": claim.as_dict(),
        }
    )


@require_GET
def claim_feed(request):
    """Everything the dashboard needs in one poll.

    `?since=<id>` returns only claims newer than that id, so the common poll is
    an empty list and the page only re-renders when something arrives.
    """
    queryset = Claim.objects.all()
    stats = queryset.aggregate(
        total=Count("id"),
        critical=Count("id", filter=Q(risk_score__gte=75)),
        tows=Count("id", filter=Q(tow_required=True)),
        avg_risk=Avg("risk_score"),
    )
    stats["avg_risk"] = round(stats["avg_risk"] or 0)

    since = request.GET.get("since")
    incremental = False
    if since and since.isdigit():
        incremental = True
        queryset = queryset.filter(id__gt=int(since))

    claims = [claim.as_dict() for claim in queryset[:200]]
    return JsonResponse(
        {
            "claims": claims,
            "stats": stats,
            "incremental": incremental,
            "latest_id": Claim.objects.order_by("-id").values_list("id", flat=True).first() or 0,
        }
    )


@require_POST
def claim_status(request, pk):
    """Dispatcher moves a claim along: new -> dispatched -> closed."""
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        payload = {}
    status = payload.get("status")
    if status not in ClaimStatus.values:
        return JsonResponse({"error": "unknown status"}, status=400)
    updated = Claim.objects.filter(pk=pk).update(status=status)
    if not updated:
        return JsonResponse({"error": "not found"}, status=404)
    return JsonResponse({"ok": True, "claim": Claim.objects.get(pk=pk).as_dict()})


@require_GET
def health(request):
    profile = AgentProfile.load()
    return JsonResponse(
        {
            "ok": True,
            "agent_id": profile.agent_id or None,
            "voice": profile.voice_id,
            "webhook_public": bool(profile.webhook_url),
            "webhook_url": profile.webhook_url,
            "published_at": profile.published_at.isoformat() if profile.published_at else None,
            "claims": Claim.objects.count(),
        }
    )


# --- settings --------------------------------------------------------------


def agent_settings(request):
    """Tune the agent and push it to AssemblyAI without a redeploy."""
    profile = AgentProfile.load()

    if request.method == "POST":
        action = request.POST.get("action", "publish")
        if action == "reset":
            AgentProfile.seed(profile)
            messages.success(request, "Reset to the defaults in agent.json.")
            return redirect(reverse("settings"))

        form = AgentProfileForm(request.POST, instance=profile)
        if form.is_valid():
            profile = form.save()
            if action == "save":
                messages.success(request, "Saved. Publish to push it to AssemblyAI.")
                return redirect(reverse("settings"))
            try:
                agent_id, created = publish_agent(
                    profile.to_agent_config(), profile.agent_id
                )
            except AgentApiError as exc:
                messages.error(request, f"AssemblyAI rejected the agent: {exc}")
            else:
                profile.agent_id = agent_id
                profile.published_at = timezone.now()
                profile.save(update_fields=["agent_id", "published_at"])
                verb = "Created" if created else "Updated"
                if profile.webhook_url:
                    messages.success(
                        request,
                        f"{verb} the agent. log_claim posts to {profile.webhook_url}.",
                    )
                else:
                    messages.warning(
                        request,
                        f"{verb} the agent, but with no tools: set a public base URL "
                        "so AssemblyAI has somewhere to post log_claim.",
                    )
                return redirect(reverse("settings"))
    else:
        form = AgentProfileForm(instance=profile)
        # Nothing configured and nothing derived: offer the address this page
        # was reached on, which on a hosted deploy is exactly right.
        if not profile.public_base_url:
            form.initial["public_base_url"] = (
                f"{request.scheme}://{request.get_host()}"
            )

    return render(
        request,
        "claims/settings.html",
        {
            "form": form,
            "profile": profile,
            "config": json.dumps(profile.to_agent_config(), indent=2),
            "has_key": bool(settings.ASSEMBLYAI_API_KEY),
        },
    )
