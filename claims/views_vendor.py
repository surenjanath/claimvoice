"""The vendor side: the tool Rae calls, and the dispatcher's controls."""

import json
import logging

from django.conf import settings
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .models import Claim, VendorCall
from .vendor_calls import (
    emergency_options,
    next_vendor,
    outbound_ready,
    place_call,
    record_outcome,
)

log = logging.getLogger("claims")

TRUE = {"yes", "true", "y", "1", "accepted", "accept"}


def _live_call():
    """The vendor call this webhook belongs to.

    An outbound call carries no claim reference of its own, so it is matched
    the same way the inbound tool webhooks are: the one that is currently in
    flight. Only one vendor call is placed at a time per claim, and they last a
    minute, so the window is small.
    """
    return (
        VendorCall.objects.filter(
            # SIMULATED belongs here too. With outbound calling off — the
            # default — every call is simulated, and leaving it out meant the
            # outcome could never be recorded on the path most deployments
            # actually run.
            status__in=[
                VendorCall.Status.DIALING,
                VendorCall.Status.IN_PROGRESS,
                VendorCall.Status.QUEUED,
                VendorCall.Status.SIMULATED,
            ],
            outcome="",
            created_at__gte=timezone.now() - timezone.timedelta(minutes=15),
        )
        .order_by("-created_at")
        .first()
    )


@csrf_exempt
def record_eta(request):
    """What the operator said. Answers 200 so Rae always has something to say."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    secret = settings.CLAIM_WEBHOOK_SECRET
    if secret and request.headers.get("X-Claim-Secret") != secret:
        return JsonResponse({"ok": False, "error": "forbidden"}, status=403)

    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        payload = {}

    call = _live_call()
    if not call:
        log.warning("record_eta with no call in flight: %s", payload)
        return JsonResponse(
            {
                "ok": False,
                "message": "Thank you, that is all I needed.",
                "instructions": "Nothing to record. Thank them and call end_call.",
            }
        )

    accepted = str(payload.get("accepted") or "").strip().lower() in TRUE
    eta = payload.get("eta_minutes")
    try:
        eta = int(float(eta)) if eta not in (None, "") else None
    except (TypeError, ValueError):
        eta = None
    if eta is not None and not 0 < eta <= 600:
        eta = None

    record_outcome(
        call,
        accepted=accepted,
        eta_minutes=eta,
        reason=str(payload.get("reason") or "").strip().lower(),
    )
    log.info(
        "record_eta: %s %s%s",
        call.vendor_name,
        "accepted" if accepted else "declined",
        f", {eta} min" if eta else "",
    )

    spoken = (
        f"Thank you, {eta} minutes noted." if accepted and eta
        else "Thank you, noted." if accepted
        else "Understood, thank you for your time."
    )
    return JsonResponse(
        {
            "ok": True,
            "message": spoken,
            "instructions": "Say that line and then call end_call. Do not ask anything else.",
        }
    )


# --- the dispatcher's controls ---------------------------------------------


@require_POST
def call_vendor(request, pk):
    """Ring the next operator about this claim, from the board."""
    claim = get_object_or_404(Claim, pk=pk)
    call = place_call(claim, dispatch=claim.dispatches.order_by("-created_at").first())
    if not call:
        return JsonResponse(
            {"error": "no operator left to try", "attempts": claim.vendor_calls.count()},
            status=409,
        )
    return JsonResponse({"ok": True, "call": call.as_dict(), "claim": claim.as_dict()})


@require_GET
def vendor_calls(request, pk):
    claim = get_object_or_404(Claim, pk=pk)
    ready, detail = outbound_ready()
    upcoming = next_vendor(claim)
    return JsonResponse(
        {
            "calls": [c.as_dict() for c in claim.vendor_calls.all()[:20]],
            "outbound_ready": ready,
            "outbound_detail": detail if not ready else "",
            "next_vendor": upcoming["name"] if upcoming else "",
            "next_distance_km": (upcoming or {}).get("distance_km"),
            "emergency": emergency_options(claim),
        }
    )
