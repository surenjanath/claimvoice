"""Identity checks and call records.

`verify_policyholder` is the first tool Ivy calls. Nothing about a policy is
said out loud until it passes, which is why the failure path here is careful
about what it gives away.
"""

import json
import logging
import os
import threading
from datetime import timedelta

from django.conf import settings
from django.http import FileResponse, Http404, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

from django.core.files.base import ContentFile

from .agent_api import AgentApiError, api
from .redact import blank_spans, redact_tool_calls, redact_turns
from .models import Conversation, Policyholder, VerificationAttempt

log = logging.getLogger("claims")

# A caller guessing four digits gets this many tries per policy per window
# before the line is closed to them.
MAX_ATTEMPTS = 4
ATTEMPT_WINDOW = timedelta(minutes=30)


def digits(value):
    return "".join(c for c in str(value or "") if c.isdigit())


# A tool call belongs to a call that is happening right now, so this is how
# long a call may plausibly still be running.
LIVE_CALL_WINDOW = timedelta(minutes=15)


def _conversation(payload, request, channel_default=Conversation.Channel.PHONE):
    """Find or start the call record this tool call belongs to.

    AssemblyAI posts its HTTP tools itself and passes no session id, so the
    webhook cannot name its own call. What it can do is look for the call that
    is currently open: clients register the session as soon as it is ready and
    mark it ended when they hang up, so an unended row started minutes ago is
    the call being spoken on right now.

    Matching on "any recent row without a session id" instead is what went
    wrong before — a leftover row from an earlier call is recent for a while
    after that call has ended, and claims filed later were glued onto it.
    """
    session_id = payload.get("session_id") or request.headers.get("X-Session-Id", "")
    if session_id:
        conversation, _ = Conversation.objects.get_or_create(
            session_id=session_id,
            defaults={
                "channel": request.headers.get("X-Claim-Source", channel_default)[:16],
                "agent_id": settings.ASSEMBLYAI_AGENT_ID,
            },
        )
        return conversation

    live = (
        Conversation.objects.filter(
            ended_at__isnull=True,
            started_at__gte=timezone.now() - LIVE_CALL_WINDOW,
        )
        .order_by("-started_at")
        .first()
    )
    if live:
        return live

    # Nothing is open: a phone call, whose client never registers a session.
    return Conversation.objects.create(
        channel=channel_default, agent_id=settings.ASSEMBLYAI_AGENT_ID
    )


@csrf_exempt
def verify_policyholder(request):
    """The tool Ivy calls before she discusses anything.

    Answers 200 whatever happens: a body the model cannot parse leaves the
    caller listening to silence. `verified` carries the real result, `message`
    is safe to say out loud, and `instructions` is for the model only.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    secret = settings.CLAIM_WEBHOOK_SECRET
    if secret and request.headers.get("X-Claim-Secret") != secret:
        return JsonResponse({"verified": False, "error": "forbidden"}, status=403)

    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        payload = {}

    policy_number = str(payload.get("policy_number") or "").strip().upper().replace(" ", "")
    last4 = digits(payload.get("phone_last4"))[-4:]
    conversation = _conversation(payload, request)

    log.info("verify_policyholder: %s / %s", policy_number, "*" * len(last4))

    if not policy_number or not last4:
        return JsonResponse(
            {
                "verified": False,
                "message": "I still need a couple of details to pull up the policy.",
                "instructions": (
                    "Ask the caller for their policy number and the last four digits of "
                    "the phone number on the policy, then call verify_policyholder again."
                ),
            }
        )

    failures = VerificationAttempt.objects.filter(
        policy_number=policy_number,
        passed=False,
        created_at__gte=timezone.now() - ATTEMPT_WINDOW,
    ).count()
    if failures >= MAX_ATTEMPTS:
        log.warning("Verification locked out for %s", policy_number)
        return JsonResponse(
            {
                "verified": False,
                "locked": True,
                "message": (
                    "I am not able to verify this policy over the phone right now. "
                    "Please call us on 1-800-555-0142 and we will help you there."
                ),
                "instructions": (
                    "Too many failed checks. Do not try to verify again, do not take "
                    "claim details, and do not say anything about the policy. Give the "
                    "callback number and close the call politely."
                ),
            }
        )

    holder = Policyholder.objects.filter(policy_number=policy_number).first()
    passed = bool(holder and holder.phone_last4 and holder.phone_last4 == last4)

    VerificationAttempt.objects.create(
        policy_number=policy_number,
        phone_last4=last4,
        passed=passed,
        policyholder=holder if passed else None,
        conversation=conversation,
    )

    if not passed:
        remaining = max(0, MAX_ATTEMPTS - failures - 1)
        # Deliberately identical whether the policy exists or not: confirming
        # that a policy number is real is itself a disclosure.
        return JsonResponse(
            {
                "verified": False,
                "attempts_remaining": remaining,
                "message": "That does not match what I have on file.",
                "instructions": (
                    "The check failed. Do not say whether the policy number exists, do "
                    "not reveal any name or detail. Ask the caller to repeat the policy "
                    "number and the last four digits of the phone number on the policy, "
                    "then call verify_policyholder again."
                    if remaining
                    else "This was the last attempt. Give the callback number 1-800-555-0142 "
                    "and close the call politely."
                ),
            }
        )

    conversation.policyholder = holder
    conversation.verified = True
    conversation.save(update_fields=["policyholder", "verified"])

    vehicles = holder.vehicle_line()
    notes = []
    if holder.status != Policyholder.Status.ACTIVE:
        notes.append(
            f"This policy is {holder.get_status_display().lower()}. Tell the caller their "
            "cover is not active, take no claim, and give them 1-800-555-0142 to sort it out."
        )
    if not holder.roadside_assistance:
        notes.append(
            "This policy has no roadside assistance, so a tow is not covered. If the car "
            "cannot be driven, say a tow can be arranged but will be charged to them."
        )

    log.info("Verified %s as %s", policy_number, holder.full_name)
    return JsonResponse(
        {
            "verified": True,
            "policy_status": holder.status,
            "first_name": holder.first_name,
            "full_name": holder.full_name,
            "coverage": holder.get_coverage_display(),
            "deductible": holder.deductible,
            "roadside_assistance": holder.roadside_assistance,
            "rental_cover": holder.rental_cover,
            "vehicles": vehicles,
            "message": f"Thank you {holder.first_name}, I have your policy here.",
            "instructions": (
                f"Verified. Greet them by first name ({holder.first_name}). The policy covers "
                f"{vehicles or 'their vehicle'} with {holder.get_coverage_display().lower()} "
                f"cover and a {holder.deductible} dollar deductible. Use these details to ask "
                "targeted questions — name the car rather than asking what they drive. "
                + (" ".join(notes) if notes else "Now take the incident details.")
            ),
        }
    )


# --- call records ----------------------------------------------------------


@csrf_exempt
def conversation_ingest(request):
    """The page posts the transcript here, during and after a call.

    Upserted on session id, so a mid-call post and the final one land on the
    same row and the dispatcher sees the call while it is still happening.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "invalid json"}, status=400)

    session_id = str(payload.get("session_id") or "").strip()[:80]
    if not session_id:
        return JsonResponse({"error": "session_id is required"}, status=400)

    channel = payload.get("channel") or Conversation.Channel.BROWSER
    conversation, created = Conversation.objects.get_or_create(
        session_id=session_id,
        defaults={
            "channel": channel if channel in Conversation.Channel.values else "browser",
            "agent_id": payload.get("agent_id", "")[:64],
        },
    )

    turns = payload.get("turns")
    if isinstance(turns, list):
        # The caller speaks the verification digits out loud, so they arrive in
        # the transcript. They are the secret the identity check rests on and
        # never get stored: masked here, and the windows they were spoken in
        # are kept so the recording can be blanked to match.
        known = (
            conversation.policyholder.phone_last4
            if conversation.policyholder_id and conversation.policyholder
            else ""
        )
        cleaned, spans = redact_turns(turns[:400], known_last4=known)
        conversation.turns = cleaned
        if spans:
            conversation.redactions = spans
    tool_calls = payload.get("tool_calls")
    if isinstance(tool_calls, list):
        conversation.tool_calls = redact_tool_calls(tool_calls[:50])
    if payload.get("ended"):
        conversation.ended_at = timezone.now()
        conversation.close_reason = str(payload.get("close_reason") or "")[:64]
        conversation.duration_seconds = float(payload.get("duration_seconds") or 0)

    conversation.save()
    # AssemblyAI posts the tool webhooks itself and passes no session id, so
    # those land on a row of their own. Fold them into this one, which is the
    # same call seen from the other side.
    merged = _absorb_orphans(conversation)
    _link_orphan_claims(conversation)
    return JsonResponse(
        {"ok": True, "id": conversation.id, "created": created, "merged": merged}
    )


def _absorb_orphans(conversation):
    """Merge sessionless rows raised by tool calls during this same call.

    Matched on time: a tool call belongs to the call that was running when it
    fired, and calls are minutes long, so the window is the call itself plus a
    little slack on each side.
    """
    from .models import Claim

    span = timedelta(seconds=(conversation.duration_seconds or 0) + 120)
    orphans = Conversation.objects.filter(
        session_id__isnull=True,
        started_at__gte=conversation.started_at - span,
        started_at__lte=timezone.now(),
    ).exclude(pk=conversation.pk)

    merged = 0
    for orphan in orphans:
        conversation.tool_calls = (conversation.tool_calls or []) + (orphan.tool_calls or [])
        if orphan.policyholder_id and not conversation.policyholder_id:
            conversation.policyholder_id = orphan.policyholder_id
        conversation.verified = conversation.verified or orphan.verified
        if orphan.caller_number and not conversation.caller_number:
            conversation.caller_number = orphan.caller_number
        Claim.objects.filter(conversation=orphan).update(conversation=conversation)
        orphan.verifications.update(conversation=conversation)
        orphan.delete()
        merged += 1

    if merged:
        conversation.save(
            update_fields=["tool_calls", "policyholder", "verified", "caller_number"]
        )
    return merged


def _link_orphan_claims(conversation):
    """Attach claims filed during this call but posted by AssemblyAI, which
    knows nothing about the session id."""
    if not conversation.policyholder_id:
        return
    from .models import Claim

    Claim.objects.filter(
        conversation__isnull=True,
        policyholder=conversation.policyholder,
        created_at__gte=conversation.started_at - timedelta(minutes=1),
    ).update(conversation=conversation)


@require_GET
def conversation_feed(request):
    queryset = Conversation.objects.select_related("policyholder").prefetch_related(
        "claims", "claims__policyholder"
    )
    if request.GET.get("verified") == "1":
        queryset = queryset.filter(verified=True)
    rows = list(queryset[:100])
    _attach_claim_counts([c.policyholder for c in rows if c.policyholder_id])
    return JsonResponse({"conversations": [c.as_dict() for c in rows]})


def _attach_claim_counts(holders):
    """One grouped count for the whole page instead of one per row."""
    from django.db.models import Count

    from .models import Claim

    ids = {h.id for h in holders}
    if not ids:
        return
    counts = dict(
        Claim.objects.filter(policyholder_id__in=ids)
        .values_list("policyholder_id")
        .annotate(total=Count("id"))
    )
    for holder in holders:
        holder.claim_count = counts.get(holder.id, 0)


@csrf_exempt
def conversation_recording(request, pk):
    """Store or serve the audio of a call.

    POST is a multipart upload from whoever held the session — the browser page
    and the simulator both mix the two directions into one stereo WAV, the
    caller on the left and Ivy on the right. GET streams it back for the
    dispatcher's player.
    """
    conversation = get_object_or_404(Conversation, pk=pk)

    if request.method == "GET":
        if not conversation.recording:
            raise Http404("no recording")
        return FileResponse(
            conversation.recording.open("rb"),
            content_type="audio/wav",
            filename=f"claimvoice-call-{conversation.id}.wav",
        )

    if request.method != "POST":
        return HttpResponseNotAllowed(["GET", "POST"])

    upload = request.FILES.get("audio")
    if not upload:
        return JsonResponse({"error": "no audio file"}, status=400)
    if upload.size > settings.MAX_RECORDING_BYTES:
        log.warning("Recording for %s rejected: %s bytes", conversation.id, upload.size)
        return JsonResponse(
            {"error": "recording too large", "limit": settings.MAX_RECORDING_BYTES},
            status=413,
        )

    # Blank the windows where the caller read out their verification digits.
    # The transcript post lands before this one, so the spans are already known;
    # if it has not arrived yet the audio is stored unredacted and the
    # `redact_calls` command sweeps it up.
    raw = upload.read()
    upload.seek(0)
    cleaned = blank_spans(raw, conversation.redactions or [])
    stored = ContentFile(cleaned)

    # A re-upload replaces the old file rather than leaving it orphaned.
    if conversation.recording:
        conversation.recording.delete(save=False)
    conversation.recording.save(
        f"call-{conversation.id}-{conversation.started_at:%Y%m%d-%H%M%S}.wav",
        stored,
        save=False,
    )
    conversation.recording_bytes = stored.size
    conversation.save(update_fields=["recording", "recording_bytes"])
    log.info(
        "Recording stored for call %s (%s bytes, %s redacted windows)",
        conversation.id,
        stored.size,
        len(conversation.redactions or []),
    )
    return JsonResponse(
        {
            "ok": True,
            "bytes": stored.size,
            "redacted_windows": len(conversation.redactions or []),
            "url": conversation.as_dict()["recording_url"],
        }
    )


@csrf_exempt
def recording_by_session(request):
    """Upload keyed on the AssemblyAI session id, which is what a client knows.

    The client never learns our conversation id, so this resolves it.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    session_id = (request.POST.get("session_id") or "").strip()
    conversation = Conversation.objects.filter(session_id=session_id).first()
    if not conversation:
        return JsonResponse({"error": "unknown session"}, status=404)
    return conversation_recording(request, conversation.pk)


@require_GET
def conversation_by_session(request):
    """What the voice page polls after a verification, to learn who it is
    talking to without ever handling the digits itself."""
    session_id = (request.GET.get("session") or "").strip()
    conversation = (
        Conversation.objects.select_related("policyholder")
        .filter(session_id=session_id)
        .first()
        if session_id
        else None
    )
    if not conversation:
        return JsonResponse({"verified": False, "policyholder": None})
    failed = conversation.verifications.filter(passed=False).count()
    remaining = 0 if conversation.verified else max(0, MAX_ATTEMPTS - failed)
    return JsonResponse(
        {
            "verified": conversation.verified,
            "policyholder": conversation.policyholder.as_dict()
            if conversation.policyholder
            else None,
            "attempts_remaining": remaining,
            "locked": (not conversation.verified) and failed >= MAX_ATTEMPTS,
        }
    )


@require_GET
def conversation_detail(request, pk):
    conversation = get_object_or_404(
        Conversation.objects.select_related("policyholder"), pk=pk
    )
    return JsonResponse(conversation.as_dict(include_turns=True))


@require_GET
def policyholder_feed(request):
    from django.db.models import Count, Q

    queryset = Policyholder.objects.annotate(
        claim_count=Count("claims", distinct=True),
        call_count=Count("conversations", distinct=True),
    )
    policy = (request.GET.get("policy") or "").strip().upper()
    if policy:
        queryset = queryset.filter(policy_number__iexact=policy)
    search = (request.GET.get("q") or "").strip()
    if search:
        queryset = queryset.filter(
            Q(full_name__icontains=search)
            | Q(policy_number__icontains=search)
            | Q(email__icontains=search)
            | Q(address__icontains=search)
            | Q(notes__icontains=search)
            | Q(phone__icontains=search)
        )
    # The last four is the shared secret the agent verifies against, so the
    # desk sees it and nobody else does — unless this deployment has opted into
    # demo credentials, and then only for one policy asked for by name.
    from .desk import desk_unlocked

    reveal = desk_unlocked(request)
    demo = bool(policy) and settings.DEMO_CREDENTIALS
    holders = list(queryset[:200])
    return JsonResponse(
        {
            "policyholders": [p.as_dict(reveal=reveal, demo=demo) for p in holders],
            "total": queryset.count(),
            "revealed": reveal or demo,
        }
    )


def directory(request):
    """A page for the dispatcher, not the agent: who is on the books."""
    return render(request, "claims/directory.html", {})


# --- hanging up ------------------------------------------------------------

# How long the closing line needs before the line is cut from this side. The
# client normally hangs up first, cleanly, as soon as the goodbye finishes
# playing; this is the backstop for a caller on a phone, where there is no
# client of ours in the call at all.
HANGUP_GRACE_SECONDS = float(os.environ.get("HANGUP_GRACE_SECONDS", 14))


def _end_session_soon(session_id, seconds=HANGUP_GRACE_SECONDS):
    """Cut the line after the goodbye has had time to play.

    A background timer rather than a task queue: there is exactly one of these
    per call, it holds no state, and losing it on a restart costs nothing —
    the session ends by itself when the caller hangs up.
    """

    def cut():
        target = session_id
        if not target:
            # A phone call never told us its session id, so find the one that
            # is still running on our agent and end that.
            target = _live_session_id()
        if not target:
            log.warning("end_call: no session to hang up")
            return
        try:
            api(f"/sessions/{target}", method="DELETE")
            log.info("end_call: hung up %s", target)
        except AgentApiError as exc:
            # Already gone is the normal case: the client got there first.
            if exc.status not in (404, 409):
                log.warning("end_call: could not hang up %s (%s)", target, exc)

    threading.Timer(seconds, cut).start()


def _live_session_id():
    """The session currently running on our agent, if there is one."""
    try:
        listing = api("/sessions")
    except AgentApiError as exc:
        log.warning("end_call: could not list sessions (%s)", exc)
        return None
    for session in listing.get("sessions") or []:
        if session.get("status") == "completed":
            continue
        if settings.ASSEMBLYAI_AGENT_ID and session.get("agent_id") != settings.ASSEMBLYAI_AGENT_ID:
            continue
        return session.get("id")
    return None


@csrf_exempt
def end_call(request):
    """The tool Ivy calls to hang up.

    The API has no native hangup, so this is it: the call is closed out here and
    the session is deleted a beat later, once the closing line has played. The
    browser and the simulator hang up themselves the moment the goodbye
    finishes, which is faster and cleaner; this covers the phone, where nothing
    of ours is in the call.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    secret = settings.CLAIM_WEBHOOK_SECRET
    if secret and request.headers.get("X-Claim-Secret") != secret:
        return JsonResponse({"ok": False, "error": "forbidden"}, status=403)

    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        payload = {}

    reason = str(payload.get("reason") or "").strip().lower()[:64]
    conversation = _conversation(payload, request)
    conversation.end_reason = reason or "agent_ended"
    if not conversation.ended_at:
        conversation.ended_at = timezone.now()
    conversation.tool_calls = (conversation.tool_calls or []) + [
        {"name": "end_call", "arguments": {"reason": reason}}
    ]
    conversation.save(update_fields=["end_reason", "ended_at", "tool_calls"])

    log.info("end_call: %s on call %s", reason or "no reason", conversation.id)
    _end_session_soon(conversation.session_id)

    return JsonResponse(
        {
            "ok": True,
            "message": "",
            "instructions": (
                "The call is being disconnected now. Say nothing further and do not "
                "ask another question."
            ),
        }
    )


@csrf_exempt
def request_human(request):
    """Ivy asks for a person. The board lights up; the call stays open."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    secret = settings.CLAIM_WEBHOOK_SECRET
    if secret and request.headers.get("X-Claim-Secret") != secret:
        return JsonResponse({"ok": False, "error": "forbidden"}, status=403)
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        payload = {}
    reason = str(payload.get("reason") or "caller_request").strip()[:64]
    conversation = _conversation(payload, request)
    conversation.needs_human = True
    conversation.handoff_reason = reason
    conversation.tool_calls = (conversation.tool_calls or []) + [
        {"name": "request_human", "arguments": {"reason": reason}}
    ]
    conversation.save(update_fields=["needs_human", "handoff_reason", "tool_calls"])
    claim = conversation.claim
    if claim:
        claim.needs_human = True
        claim.handoff_reason = reason
        claim.save(update_fields=["needs_human", "handoff_reason"])

    # The flag lights the board. This puts the caller through.
    from .handoff import AFTER_HOURS_LINE, HOLD_LINE, open_handoff
    from .models import Handoff

    handoff = open_handoff(conversation, reason=reason, claim=claim)
    spoken = {
        Handoff.Status.RINGING: HOLD_LINE,
        Handoff.Status.AFTER_HOURS: AFTER_HOURS_LINE,
    }.get(
        handoff.status,
        "A dispatcher has your claim and will call you straight back.",
    )
    return JsonResponse(
        {
            "ok": True,
            # What Ivy says next. She is still holding the call until the
            # transfer takes, so this has to be true either way.
            "message": spoken,
            "handoff_reason": reason,
            "handoff_id": handoff.id,
            "handoff_status": handoff.status,
            "transferring": handoff.status == Handoff.Status.RINGING,
        }
    )
