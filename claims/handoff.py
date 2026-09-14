"""Getting a caller to a person, on the call they are already on.

Ivy can take a claim. She cannot reassure somebody whose child is in the back
seat, and she should not try. `request_human` is the moment the machine steps
aside, and up to now that moment only lit a banner on a board — the caller kept
talking to Ivy and hoped.

This is the transfer. Twilio owns the PSTN leg (the number is on a SIP trunk
pointed at AssemblyAI), so the live call can be taken back and redirected with
one REST call. Ringing down the rota is then TwiML: dial the first dispatcher
with a timeout, and when Twilio reports no answer it asks us what to do next,
which is dial the second. That is the whole queue — no state machine of our
own, no polling, and it survives our process restarting mid-call.

What deliberately does not happen:

* **No transfer without an explicit opt-in.** LIVE_TRANSFERS, the same shape as
  OUTBOUND_CALLS. Otherwise the handoff is recorded, the board lights up, and a
  dispatcher rings the caller back — which is what happens today, except now
  there is a row that says so.
* **No silent dead end.** If the rota is empty, or everybody has been tried,
  the caller is told in words and the fallback number is texted. A caller left
  listening to nothing is worse than a caller told to expect a call back.
"""

import logging
from datetime import timedelta
from xml.sax.saxutils import escape, quoteattr

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from . import roster, telephony
from .models import AgentProfile, ClaimNote, Handoff, HandoffAttempt

log = logging.getLogger("claims")

# How long one dispatcher's phone rings before moving on.
RING_SECONDS = 20
# Slack on top of the ring window before a row still RINGING counts as stuck.
# Twilio's own POST to the callback is what moves it along; this only covers
# the POST never landing at all.
STALE_SLACK_SECONDS = 90
# How many people to try before falling back. A caller will not hold through
# five phones ringing in sequence, whatever the rota says.
MAX_ATTEMPTS = 3

HOLD_LINE = "Connecting you to a dispatcher now. Please stay on the line."
NO_ANSWER_LINE = (
    "I am sorry — every dispatcher is on another call. Your claim is on their "
    "board and somebody will call you back shortly. Goodbye."
)
AFTER_HOURS_LINE = (
    "The desk is closed right now. Your claim is filed and a dispatcher will "
    "call you back as soon as they are on."
)


def ring_seconds():
    return int(getattr(settings, "HANDOFF_RING_SECONDS", RING_SECONDS) or RING_SECONDS)


def max_attempts():
    return int(getattr(settings, "HANDOFF_MAX_ATTEMPTS", MAX_ATTEMPTS) or MAX_ATTEMPTS)


def transfer_ready():
    """Whether a live call can actually be redirected.

    Three things have to be true, and the reason is returned rather than a bare
    False because "why did it not transfer" is the first question asked.
    """
    if not getattr(settings, "LIVE_TRANSFERS", False):
        return False, "LIVE_TRANSFERS is off"
    if not telephony.configured():
        return False, "no Twilio credentials"
    if not AgentProfile.load().base_url:
        return False, "no public base URL to call back to"
    return True, ""


# --- opening one ------------------------------------------------------------


def open_handoff(conversation, reason="caller_request", claim=None):
    """Start a handoff for this call and set it going.

    Idempotent per call: a caller who asks twice, or an agent that fires the
    tool twice, is one person waiting, not two.
    """
    existing = (
        Handoff.objects.filter(conversation=conversation)
        .exclude(status__in=Handoff.CLOSED)
        .order_by("-created_at")
        .first()
    )
    if existing:
        log.info("conversation %s already has handoff %s", conversation.id, existing.id)
        return existing

    handoff = Handoff.objects.create(
        conversation=conversation,
        claim=claim or conversation.claim,
        reason=reason[:64],
        caller_number=conversation.caller_number or "",
    )
    return begin(handoff)


def begin(handoff):
    """Ring the first person, or explain why nobody is being rung."""
    if roster.after_hours():
        return out_of_hours(handoff)

    queue = roster.escalation()
    if not queue and not roster.fallback_number():
        return nobody_answered(handoff, "nobody on the rota has a number on file")

    ready, why = transfer_ready()
    if not ready or not handoff.caller_number:
        # Recorded, boarded, not dialled. The caller is still with Ivy.
        detail = why or "the call did not arrive over the phone"
        handoff.simulated = True
        handoff.status = Handoff.Status.WAITING
        handoff.note = f"Not transferred: {detail}."
        handoff.save(update_fields=["simulated", "status", "note"])
        HandoffAttempt.objects.create(
            handoff=handoff,
            dispatcher=queue[0] if queue else None,
            to_number=queue[0].phone if queue else "",
            outcome=HandoffAttempt.Outcome.SIMULATED,
            detail=detail[:255],
        )
        note_on_claim(handoff, f"Caller asked for a person. {detail.capitalize()}.")
        log.info("handoff %s: not dialled (%s)", handoff.id, detail)
        return handoff

    return ring_next(handoff)


# --- ringing down the rota --------------------------------------------------


def already_tried(handoff):
    return [a.dispatcher_id for a in handoff.attempts.all() if a.dispatcher_id]


def ring_next(handoff):
    """Take hold of the live call and point it at the next dispatcher."""
    tried = already_tried(handoff)
    if len(tried) >= max_attempts():
        return nobody_answered(handoff, f"{len(tried)} dispatchers tried")

    queue = roster.escalation(exclude=tried)
    target = queue[0] if queue else None
    number = target.phone if target else roster.fallback_number()
    if not number:
        return nobody_answered(handoff, "nobody left to try")

    call_sid = handoff.provider_call_id or find_live_call(handoff.caller_number)
    if not call_sid:
        handoff.simulated = True
        handoff.note = "The call was no longer live when the transfer was tried."
        handoff.status = Handoff.Status.ABANDONED
        handoff.ended_at = timezone.now()
        handoff.save(update_fields=["simulated", "note", "status", "ended_at"])
        log.info("handoff %s: no live call for %s", handoff.id, handoff.caller_number)
        return handoff

    attempt = HandoffAttempt.objects.create(
        handoff=handoff,
        dispatcher=target,
        to_number=number,
        outcome=HandoffAttempt.Outcome.RINGING,
        detail="" if target else "fallback number",
    )
    try:
        telephony.account(
            f"/Calls/{call_sid}.json",
            {"Twiml": dial_twiml(handoff, number, first=not tried)},
        )
    except Exception as exc:  # noqa: BLE001 — the caller must hear something, not a 500
        attempt.outcome = HandoffAttempt.Outcome.FAILED
        attempt.detail = str(exc)[:255]
        attempt.save(update_fields=["outcome", "detail"])
        handoff.status = Handoff.Status.FAILED
        handoff.note = str(exc)[:255]
        handoff.ended_at = timezone.now()
        handoff.save(update_fields=["status", "note", "ended_at"])
        log.error("handoff %s: transfer failed — %s", handoff.id, exc)
        return handoff

    handoff.provider_call_id = call_sid
    handoff.status = Handoff.Status.RINGING
    handoff.dispatcher = target
    handoff.save(update_fields=["provider_call_id", "status", "dispatcher"])
    log.info(
        "handoff %s: ringing %s on %s", handoff.id, target.name if target else "fallback", number
    )
    return handoff


def find_live_call(caller_number):
    """The in-progress leg from this caller, as Twilio knows it.

    AssemblyAI holds the media, so we never saw the SID: it is looked up by the
    number that is on the call right now.
    """
    if not caller_number or not telephony.configured():
        return ""
    try:
        found = telephony.account(
            f"/Calls.json?From={caller_number}&Status=in-progress&PageSize=1"
        )
    except telephony.TelephonyError as exc:
        log.warning("could not look up the live call for %s — %s", caller_number, exc)
        return ""
    calls = found.get("calls") or []
    return calls[0].get("sid", "") if calls else ""


def dial_twiml(handoff, number, first=True):
    """Dial one dispatcher, and come back here when that leg ends."""
    action = callback_url(handoff)
    caller_id = getattr(settings, "OUTBOUND_FROM_NUMBER", "")
    line = HOLD_LINE if first else "Still trying. Please hold."
    dial_attrs = (
        f"action={quoteattr(action)} method=\"POST\" timeout=\"{ring_seconds()}\""
        + (f" callerId={quoteattr(caller_id)}" if caller_id else "")
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f"<Say>{escape(line)}</Say>"
        f"<Dial {dial_attrs}><Number>{escape(number)}</Number></Dial>"
        "</Response>"
    )


def goodbye_twiml(line):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Response><Say>{escape(line)}</Say><Hangup/></Response>"
    )


def callback_url(handoff):
    """Where Twilio reports how that leg went.

    Addressed by the handoff's token: the route is public by necessity, and a
    public route keyed on a row id is an invitation to walk the queue.
    """
    base = AgentProfile.load().base_url.rstrip("/")
    return base + reverse("handoff-dial-status", args=[handoff.token])


# --- what Twilio tells us afterwards ----------------------------------------


def on_dial_status(handoff, dial_status):
    """One leg finished. Either somebody spoke to the caller, or ring the next.

    Returns the TwiML for whatever should happen to the caller now.
    """
    attempt = handoff.attempts.order_by("-created_at").first()
    answered = dial_status in {"completed", "answered"}

    if attempt:
        attempt.outcome = (
            HandoffAttempt.Outcome.ANSWERED if answered else _outcome_for(dial_status)
        )
        attempt.detail = dial_status[:255]
        attempt.save(update_fields=["outcome", "detail"])

    if answered:
        now = timezone.now()
        handoff.status = Handoff.Status.DONE
        handoff.connected_at = handoff.connected_at or now
        handoff.ended_at = now
        handoff.save(update_fields=["status", "connected_at", "ended_at"])
        who = handoff.dispatcher.name if handoff.dispatcher else "a dispatcher"
        note_on_claim(handoff, f"Caller was put through to {who}.")
        clear_needs_human(handoff)
        log.info("handoff %s: connected to %s", handoff.id, who)
        # The dispatcher hung up, so the caller's leg ends too.
        return goodbye_twiml("Thank you for calling. Goodbye.")

    log.info("handoff %s: %s, trying the next", handoff.id, dial_status)
    handoff = ring_next(handoff)
    if handoff.status == Handoff.Status.RINGING:
        # ring_next already redirected the live call; nothing to return.
        return ""
    return goodbye_twiml(NO_ANSWER_LINE)


def _outcome_for(dial_status):
    return {
        "busy": HandoffAttempt.Outcome.BUSY,
        "no-answer": HandoffAttempt.Outcome.NO_ANSWER,
        "failed": HandoffAttempt.Outcome.FAILED,
        "canceled": HandoffAttempt.Outcome.NO_ANSWER,
    }.get(dial_status, HandoffAttempt.Outcome.NO_ANSWER)


# --- the ways it ends badly -------------------------------------------------


def out_of_hours(handoff):
    handoff.status = Handoff.Status.AFTER_HOURS
    handoff.note = "Nobody was rostered."
    handoff.ended_at = timezone.now()
    handoff.save(update_fields=["status", "note", "ended_at"])
    note_on_claim(handoff, "Caller asked for a person out of hours. Owed a call back.")
    tell_the_fallback(handoff, "out of hours")
    log.info("handoff %s: out of hours", handoff.id)
    return handoff


def nobody_answered(handoff, why):
    handoff.status = Handoff.Status.NO_ANSWER
    handoff.note = why[:255]
    handoff.ended_at = timezone.now()
    handoff.save(update_fields=["status", "note", "ended_at"])
    note_on_claim(handoff, f"Nobody took the transfer ({why}). Owed a call back.")
    tell_the_fallback(handoff, why)
    log.warning("handoff %s: nobody answered (%s)", handoff.id, why)
    return handoff


def tell_the_fallback(handoff, why):
    """Text whoever is carrying the escalation phone.

    A queue that overflows into nothing is not a queue.
    """
    number = roster.fallback_number()
    if not number:
        return {"sent": False, "skipped": "no fallback number"}
    from .notify import send_sms

    claim = handoff.claim
    reference = f"CV-{claim.id:05d}" if claim else f"call {handoff.conversation_id}"
    caller = handoff.caller_number or "an unknown number"
    return send_sms(
        number,
        f"ClaimVoice: {reference} is waiting for a person ({why}). Caller {caller}.",
    )


def note_on_claim(handoff, message):
    if not handoff.claim_id:
        return None
    return ClaimNote.objects.create(
        claim=handoff.claim, author="system", body=message[:400]
    )


def clear_needs_human(handoff):
    """The person part is done — stop asking the board for one.

    Nothing else ever turns this flag back off: `request_human` and a stalled
    tow escalation are the only places that set it, so a claim answered
    correctly and never closed here would sit flagged "owed a person" forever,
    long after somebody actually spoke to the caller.
    """
    claim = handoff.claim
    if claim and claim.needs_human:
        claim.needs_human = False
        claim.save(update_fields=["needs_human"])
    conversation = handoff.conversation
    if conversation and conversation.needs_human:
        conversation.needs_human = False
        conversation.save(update_fields=["needs_human"])


def close(handoff, status=Handoff.Status.DONE, note=""):
    """A dispatcher marking it dealt with, by hand or by call.

    Pressing Close is the dispatcher saying so themselves — the claim comes
    off the "owed a person" list whatever the status, because a human just
    made that judgment call.
    """
    handoff.status = status
    handoff.ended_at = handoff.ended_at or timezone.now()
    if note:
        handoff.note = note[:255]
    handoff.save(update_fields=["status", "ended_at", "note"])
    clear_needs_human(handoff)
    return handoff


# --- when the callback itself never arrives ---------------------------------


def stale_ringing():
    """Rows stuck RINGING well past when Twilio should have reported back.

    Twilio always POSTs to `action` when a <Dial> leg ends — answered, busy, no
    answer, or the ring timeout hit — so a row still RINGING this long after
    its last attempt means that POST never landed: the app was down, the
    request failed, or nothing was really being dialled. Left alone the caller
    stays "ringing" on the board forever, `waited_seconds` climbing long after
    the call itself is over.
    """
    cutoff = timezone.now() - timedelta(seconds=ring_seconds() + STALE_SLACK_SECONDS)
    return [
        handoff
        for handoff in Handoff.objects.filter(status=Handoff.Status.RINGING)
        if (_last_attempt_at(handoff) or handoff.created_at) < cutoff
    ]


def _last_attempt_at(handoff):
    attempt = handoff.attempts.order_by("-created_at").first()
    return attempt.created_at if attempt else None


def reap_stale(handoff):
    """Close out a handoff whose dial-status callback never arrived.

    Nothing here claims to know whether the dispatcher actually answered — it
    did not hear back either way, and saying "no answer" is the honest gap
    between "nobody knows" and leaving the caller shown as ringing forever.
    """
    attempt = handoff.attempts.order_by("-created_at").first()
    if attempt and attempt.outcome == HandoffAttempt.Outcome.RINGING:
        attempt.outcome = HandoffAttempt.Outcome.FAILED
        attempt.detail = "no dial status received"
        attempt.save(update_fields=["outcome", "detail"])
    return nobody_answered(handoff, "no dial status arrived before the ring window closed")
