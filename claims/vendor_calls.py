"""Calling the tow operator on the caller's behalf.

Ivy takes the claim in ninety seconds. The part that actually gets a truck to
the roadside is the call afterwards — ring the operator, give them the location
and the vehicle, ask how long, tell the driver. That is what this does, and it
is the same loop when the truck is late: ring the next one.

Two things are deliberately not automatic:

* **Dialling a real number.** Every vendor in the book is in a range reserved
  for fiction, and even then a real call is only placed when the deployment has
  a registered phone number and has set OUTBOUND_CALLS. Otherwise the call is
  recorded as simulated: the same row, the same escalation, no phone ringing.
* **Emergency services.** Nothing here dials police or an ambulance. A machine
  that can summon them on its own reading of a situation is a machine that can
  send them to the wrong place, and a false dispatch is somebody else's
  emergency going unanswered. The dispatcher gets the numbers and makes that
  call themselves.
"""

import logging
import re

from django.conf import settings
from django.utils import timezone

from .agent_api import AgentApiError, api
from .models import Claim, Dispatch, VendorCall
from .vendors import rank_vendors

log = logging.getLogger("claims")

HIGHWAY = re.compile(
    r"\b(?:highway|interstate|freeway|motorway|expressway|turnpike|dual carriageway"
    r"|hard shoulder|i-\s?\d{1,3}|i\d{2,3}|a\d{1,3}\b|m\d{1,2}\b|route \d+|us-?\d+)",
    re.I,
)

# How far past a promised ETA to wait before treating the tow as late.
LATE_GRACE_MINUTES = 5
# A caller should not be rung, or texted, more often than this.
MIN_UPDATE_MINUTES = 4
# Stop after this many operators rather than working through the book forever.
MAX_ATTEMPTS = 3


def outbound_ready():
    """Whether a real call can be placed at all.

    Needs a number registered with AssemblyAI — the same import that
    `connect_phone` does — and an explicit opt-in, because placing calls is the
    one thing here that reaches outside the building.
    """
    if not getattr(settings, "OUTBOUND_CALLS", False):
        return False, "OUTBOUND_CALLS is off"
    number = getattr(settings, "OUTBOUND_FROM_NUMBER", "")
    if not number:
        return False, "no OUTBOUND_FROM_NUMBER"
    return True, number


def next_vendor(claim, kind="tow"):
    """The operator to try next, skipping any already tried on this claim."""
    tried = list(
        claim.vendor_calls.values_list("vendor_phone", flat=True).distinct()
    )
    tried = [phone for phone in tried if phone]
    at = (claim.lat, claim.lng) if claim.lat is not None and claim.lng is not None else None
    ranked = rank_vendors(kind, claim.location, claim.id, at=at, exclude=tried)
    return ranked[0] if ranked else None


def place_call(claim, purpose=VendorCall.Purpose.REQUEST, dispatch=None, kind="tow"):
    """Ring the next operator about this claim.

    Returns the VendorCall either way. A deployment without outbound calling
    still gets the row, the vendor choice and the escalation — everything but
    the dial.
    """
    # Only calls that went out to a *new* operator count against the cap.
    # Chasing the operator already assigned is not another attempt at finding
    # one, and counting it meant a single chase could exhaust the budget and
    # hand a driver to a dispatcher who had options left.
    attempts = claim.vendor_calls.exclude(purpose=VendorCall.Purpose.ETA_CHECK).count()
    if attempts >= MAX_ATTEMPTS:
        log.info("claim %s: %s operators tried, stopping", claim.id, attempts)
        return None

    vendor = next_vendor(claim, kind)
    if not vendor:
        log.warning("claim %s: no vendor left to try for %s", claim.id, kind)
        return None

    call = VendorCall.objects.create(
        claim=claim,
        dispatch=dispatch,
        vendor_name=vendor["name"],
        vendor_phone=vendor["phone"],
        distance_km=vendor.get("distance_km"),
        purpose=purpose,
    )

    ready, detail = outbound_ready()
    if not ready:
        call.status = VendorCall.Status.SIMULATED
        call.note = f"Not dialled: {detail}."
        call.save(update_fields=["status", "note"])
        log.info("claim %s: simulated call to %s (%s)", claim.id, vendor["name"], detail)
        return call

    agent_id = getattr(settings, "VENDOR_AGENT_ID", "")
    body = {
        "from_number": detail,
        "to_number": vendor["phone"],
        **({"agent_id": agent_id} if agent_id else {}),
    }
    try:
        created = api("/calls", method="POST", body=body)
    except AgentApiError as exc:
        call.status = VendorCall.Status.FAILED
        call.note = str(exc)[:255]
        call.save(update_fields=["status", "note"])
        log.error("claim %s: outbound call failed — %s", claim.id, exc)
        return call

    call.status = VendorCall.Status.DIALING
    call.provider_call_id = str(created.get("id") or created.get("call_id") or "")[:80]
    call.save(update_fields=["status", "provider_call_id"])
    log.info("claim %s: dialling %s (%s)", claim.id, vendor["name"], call.provider_call_id)
    return call


def record_outcome(call, *, accepted, eta_minutes=None, reason=""):
    """What the operator said, and what follows from it."""
    call.outcome = (
        VendorCall.Outcome.ACCEPTED if accepted else VendorCall.Outcome.DECLINED
    )
    call.decline_reason = reason[:32]
    call.eta_minutes = eta_minutes if accepted else None
    call.status = call.status if call.simulated else VendorCall.Status.DONE
    call.completed_at = timezone.now()
    call.save(
        update_fields=["outcome", "decline_reason", "eta_minutes", "status", "completed_at"]
    )

    claim = call.claim
    if accepted:
        dispatch = call.dispatch or claim.dispatches.filter(
            kind=Dispatch.Kind.TOW
        ).order_by("-created_at").first()
        if dispatch:
            dispatch.vendor = call.vendor_name
            dispatch.vendor_phone = call.vendor_phone
            dispatch.eta_minutes = eta_minutes
            dispatch.status = Dispatch.Status.EN_ROUTE
            dispatch.save(
                update_fields=["vendor", "vendor_phone", "eta_minutes", "status", "updated_at"]
            )
        else:
            Dispatch.objects.create(
                claim=claim,
                kind=Dispatch.Kind.TOW,
                vendor=call.vendor_name,
                vendor_phone=call.vendor_phone,
                eta_minutes=eta_minutes,
                status=Dispatch.Status.EN_ROUTE,
                raised_by="agent",
                notes="Accepted by the operator over the phone.",
            )
        tell_the_caller(
            claim,
            f"{call.vendor_name} is on the way to {claim.location}"
            + (f", about {eta_minutes} minutes." if eta_minutes else "."),
        )
        return call

    # Declined: move down the list rather than leaving the driver waiting.
    log.info("claim %s: %s declined (%s)", claim.id, call.vendor_name, reason or "no reason")
    following = place_call(claim, purpose=VendorCall.Purpose.REASSIGN, dispatch=call.dispatch)
    if following:
        tell_the_caller(
            claim,
            f"The first operator could not take it, so we are arranging "
            f"{following.vendor_name} instead. We will confirm the time shortly.",
        )
    else:
        tell_the_caller(
            claim,
            "We are having trouble reaching a recovery operator for you. "
            "A dispatcher is picking this up now and will call you.",
        )
        claim.needs_human = True
        claim.handoff_reason = "no_tow_available"
        claim.save(update_fields=["needs_human", "handoff_reason"])
    return call


def tell_the_caller(claim, message):
    """Keep the driver in the loop, without becoming the reason their phone
    never stops buzzing."""
    from .models import ClaimNote
    from .notify import send_sms

    recent = claim.notes.filter(
        author="system",
        created_at__gte=timezone.now() - timezone.timedelta(minutes=MIN_UPDATE_MINUTES),
    ).exists()
    ClaimNote.objects.create(claim=claim, author="system", body=message[:400])
    if recent:
        log.info("claim %s: update noted but not texted (too soon)", claim.id)
        return {"sent": False, "skipped": "too_soon"}

    holder = claim.policyholder
    if not holder or not holder.phone:
        return {"sent": False, "skipped": "no_number"}
    return send_sms(holder.phone, f"ClaimVoice: {message}")


def overdue_calls():
    """Tows that promised a time and have run past it."""
    now = timezone.now()
    late = []
    for dispatch in Dispatch.objects.filter(
        kind=Dispatch.Kind.TOW, status=Dispatch.Status.EN_ROUTE, eta_minutes__isnull=False
    ).select_related("claim"):
        due = dispatch.updated_at + timezone.timedelta(
            minutes=dispatch.eta_minutes + LATE_GRACE_MINUTES
        )
        if now > due:
            late.append((dispatch, round((now - due).total_seconds() / 60)))
    return late


def chase(dispatch, minutes_over):
    """A tow that is late: ask the operator, then look for a closer one."""
    claim = dispatch.claim
    log.info("claim %s: tow %s minutes late", claim.id, minutes_over)

    already_chased = claim.vendor_calls.filter(purpose=VendorCall.Purpose.ETA_CHECK).exists()
    if not already_chased:
        call = VendorCall.objects.create(
            claim=claim,
            dispatch=dispatch,
            vendor_name=dispatch.vendor or "the operator",
            vendor_phone=dispatch.vendor_phone,
            purpose=VendorCall.Purpose.ETA_CHECK,
            status=VendorCall.Status.SIMULATED
            if not outbound_ready()[0]
            else VendorCall.Status.QUEUED,
            note=f"Tow {minutes_over} minutes past the promised time.",
        )
        tell_the_caller(
            claim,
            f"Your tow is running about {minutes_over} minutes late. "
            "We are checking with the operator now.",
        )
        return call

    # Chased once already and still nothing: find someone closer.
    following = place_call(claim, purpose=VendorCall.Purpose.REASSIGN, dispatch=dispatch)
    if following:
        dispatch.status = Dispatch.Status.CANCELLED
        dispatch.notes = (dispatch.notes + " Reassigned: ran late.").strip()[:255]
        dispatch.save(update_fields=["status", "notes", "updated_at"])
        tell_the_caller(
            claim,
            f"We have moved your recovery to {following.vendor_name}"
            + (
                f", about {following.distance_km} km away."
                if following.distance_km
                else "."
            ),
        )
        return following

    claim.needs_human = True
    claim.handoff_reason = "tow_overdue"
    claim.save(update_fields=["needs_human", "handoff_reason"])
    tell_the_caller(
        claim,
        "Your recovery is taking longer than it should. A dispatcher is taking "
        "over and will call you.",
    )
    return None


def emergency_options(claim):
    """Numbers a dispatcher may want when a roadside wait has gone badly.

    Returned for a person to read and decide on. Nothing here is dialled
    automatically: sending police or an ambulance on a machine's reading of a
    situation risks a false dispatch, and a false dispatch is somebody else's
    emergency going unanswered.
    """
    reasons = []
    if claim.injuries_reported:
        reasons.append("injuries were reported on the call")
    if not claim.is_drivable and claim.severity in {"fire_or_smoke", "rollover"}:
        reasons.append(f"the caller described {claim.get_severity_display().lower()}")
    blob = f"{claim.location} {claim.description}".lower()
    # People say "I-95", not "interstate 95", and a car stopped on one is the
    # case this whole panel exists for.
    if HIGHWAY.search(blob):
        reasons.append("the vehicle is stopped on a highway")
    if claim.needs_human:
        reasons.append(f"the claim was handed off ({claim.handoff_reason or 'no reason'})")

    return {
        "suggest": bool(reasons),
        "reasons": reasons,
        "contacts": [
            {"name": "Emergency services", "phone": "911", "note": "Injury or immediate danger"},
            {
                "name": "Highway patrol, non-emergency",
                "phone": "+15550110311",
                "note": "Vehicle obstructing a live lane",
            },
        ],
        "dial_automatically": False,
    }
