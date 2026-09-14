"""The queue, as the board and as Twilio see it.

Two audiences with nothing in common. The board reads JSON behind the desk
login and presses Take this call. Twilio posts form fields from the public
internet mid-call and expects TwiML back within a few seconds — no login, no
session, and every millisecond is a caller listening to silence.
"""

import json
import logging

from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from . import handoff as handoffs
from . import roster
from .desk import current_dispatcher
from .models import Conversation, DeskAction, Handoff, HandoffAttempt
from .models_desk import record

log = logging.getLogger("claims")


@require_GET
def handoff_feed(request):
    """Everyone waiting, and who is on to take them."""
    queryset = (
        Handoff.objects.select_related("conversation", "conversation__policyholder", "claim", "dispatcher")
        .prefetch_related("attempts", "attempts__dispatcher")
    )
    if request.GET.get("open") == "1":
        queryset = queryset.exclude(status__in=Handoff.CLOSED)
    return JsonResponse(
        {
            "handoffs": [h.as_dict() for h in queryset[:100]],
            "roster": roster.summary(),
            "transfers": dict(zip(("live", "why"), handoffs.transfer_ready())),
        }
    )


@require_POST
def take_handoff(request, pk):
    """A dispatcher claims a waiting caller.

    Transfers to *their* number rather than ringing down the rota: they have
    pressed the button, so they are the answer to who is taking it.
    """
    handoff = get_object_or_404(Handoff, pk=pk)
    me = current_dispatcher(request)
    if not me:
        return JsonResponse({"error": "sign in as a dispatcher to take a call"}, status=403)
    if not me.phone:
        return JsonResponse(
            {"error": "no phone number on your account to transfer to"}, status=400
        )
    if not handoff.open:
        return JsonResponse({"error": "that call is already closed"}, status=409)

    handoff.dispatcher = me
    handoff.save(update_fields=["dispatcher"])
    record(me, "take_call", f"handoff:{handoff.id}", handoff.reason)

    ready, why = handoffs.transfer_ready()
    call_sid = handoff.provider_call_id or handoffs.find_live_call(handoff.caller_number)
    if not ready or not call_sid:
        # Nothing to redirect: mark it theirs and let them ring back.
        handoff.simulated = True
        handoff.note = f"Assigned to {me.name}; not transferred ({why or 'no live call'})."
        handoff.save(update_fields=["simulated", "note"])
        HandoffAttempt.objects.create(
            handoff=handoff,
            dispatcher=me,
            to_number=me.phone,
            outcome=HandoffAttempt.Outcome.SIMULATED,
            detail=(why or "no live call")[:255],
        )
        return JsonResponse({"ok": True, "transferred": False, "handoff": handoff.as_dict()})

    handoff.provider_call_id = call_sid
    handoff.save(update_fields=["provider_call_id"])
    attempt = HandoffAttempt.objects.create(
        handoff=handoff, dispatcher=me, to_number=me.phone, detail="taken from the board"
    )
    try:
        from . import telephony

        telephony.account(
            f"/Calls/{call_sid}.json",
            {"Twiml": handoffs.dial_twiml(handoff, me.phone, first=True)},
        )
    except Exception as exc:  # noqa: BLE001 — reported to the dispatcher as-is
        attempt.outcome = HandoffAttempt.Outcome.FAILED
        attempt.detail = str(exc)[:255]
        attempt.save(update_fields=["outcome", "detail"])
        return JsonResponse({"error": str(exc)[:200]}, status=502)

    handoff.status = Handoff.Status.RINGING
    handoff.save(update_fields=["status"])
    return JsonResponse({"ok": True, "transferred": True, "handoff": handoff.as_dict()})


@require_POST
def close_handoff(request, pk):
    """Dealt with by hand — the dispatcher rang them back themselves."""
    handoff = get_object_or_404(Handoff, pk=pk)
    me = current_dispatcher(request)
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        payload = {}
    note = str(payload.get("note") or "Closed on the board.")[:255]
    handoffs.close(handoff, note=note)
    record(me, "close_call", f"handoff:{handoff.id}", note)
    return JsonResponse({"ok": True, "handoff": handoff.as_dict()})


@csrf_exempt
def dial_status(request, token):
    """Twilio reporting how a leg went, addressed by the handoff's token.

    Answers TwiML, always. An error here is a caller holding a silent line, so
    anything unexpected ends the call politely rather than 500ing at Twilio.
    """
    if request.method != "POST":
        return HttpResponse(status=405)
    handoff = Handoff.objects.filter(token=token).first()
    if not handoff:
        log.warning("dial status for an unknown handoff token")
        return _twiml(handoffs.goodbye_twiml("Thank you for calling. Goodbye."))

    status = (request.POST.get("DialCallStatus") or "").strip().lower()
    log.info("handoff %s: dial status %s", handoff.id, status or "(none)")
    try:
        twiml = handoffs.on_dial_status(handoff, status)
    except Exception:  # noqa: BLE001 — never leave the caller on a dead line
        log.exception("handoff %s: could not act on dial status", handoff.id)
        twiml = handoffs.goodbye_twiml(handoffs.NO_ANSWER_LINE)
    # An empty string means ring_next already redirected the live call itself,
    # and Twilio is being told there is nothing more to do with this leg.
    return _twiml(twiml or '<?xml version="1.0" encoding="UTF-8"?><Response/>')


def _twiml(body):
    return HttpResponse(body, content_type="text/xml; charset=utf-8")


@require_GET
def roster_feed(request):
    from .models import Dispatcher

    people = Dispatcher.objects.prefetch_related("shifts")
    return JsonResponse(
        {
            "dispatchers": [
                {**p.as_dict(include_phone=True), "shifts": [s.as_dict() for s in p.shifts.all()]}
                for p in people
            ],
            "now": roster.summary(),
        }
    )


@require_GET
def desk_log(request):
    """The audit trail, newest first."""
    rows = DeskAction.objects.select_related("dispatcher")[:200]
    return JsonResponse({"actions": [a.as_dict() for a in rows]})


def open_for_conversation(conversation_id, reason="caller_request"):
    """Used by the tool webhook, which has already authenticated itself."""
    conversation = Conversation.objects.filter(pk=conversation_id).first()
    if not conversation:
        return None
    return handoffs.open_handoff(conversation, reason=reason)
