"""What the calls add up to.

The dispatcher board answers "what is happening now". This answers "how is the
agent doing", which is a different question and the one that tells you what to
change in the prompt.
"""

from collections import Counter
from datetime import timedelta

from django.db.models import Avg, Count, Q
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET

from .models import (
    Claim,
    ClaimStatus,
    Conversation,
    Dispatch,
    IncidentType,
    Policyholder,
    VerificationAttempt,
)

# Why a call ended, in words. The agent picks the key from an enum.
END_REASONS = {
    "claim_filed": "Claim filed",
    "caller_said_goodbye": "Caller said goodbye",
    "could_not_verify": "Could not verify",
    "policy_not_active": "Policy not active",
    "wrong_number": "Wrong number",
    "referred_to_emergency_services": "Sent to emergency services",
    "caller_will_call_back": "Caller will call back",
    "agent_ended": "Agent ended, no reason given",
}

# What each rejected field means in words a person would use.
MISSING_LABELS = {
    "is_drivable": "Filed without asking if the car drives",
    "policy_number": "Filed without a policy number",
    "location": "Filed without a location",
    "incident_type": "Filed with an incident it could not categorise",
}


def percent(part, whole):
    return round(100 * part / whole) if whole else 0


def quantile(sorted_values, q):
    if not sorted_values:
        return 0
    index = int(round((len(sorted_values) - 1) * q))
    return sorted_values[index]


DURATION_BUCKETS = (
    (60, "Under 1 min"),
    (120, "1–2 min"),
    (240, "2–4 min"),
    (480, "4–8 min"),
    (None, "Over 8 min"),
)
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _headline(calls, claims):
    total_calls = calls.count()
    verified_calls = calls.filter(verified=True).count()
    with_claim = calls.filter(claims__isnull=False).distinct().count()
    durations = sorted(c.duration_seconds for c in calls if c.duration_seconds)
    return {
        "calls": total_calls,
        "claims": claims.count(),
        "verified": verified_calls,
        "with_claim": with_claim,
        "verified_rate": percent(verified_calls, total_calls),
        "completion_rate": percent(with_claim, total_calls),
        "median_seconds": round(quantile(durations, 0.5)),
        "p90_seconds": round(quantile(durations, 0.9)),
        "recordings": calls.exclude(recording="").exclude(recording=None).count(),
        "tows": claims.filter(tow_required=True).count(),
    }


@require_GET
def metrics(request):
    """Every number the insights page draws, in one request."""
    days = int(request.GET.get("days") or 30)
    now = timezone.now()
    since = now - timedelta(days=days)
    previous_since = since - timedelta(days=days)

    calls = (
        Conversation.objects.filter(started_at__gte=since)
        .select_related("policyholder")
        .prefetch_related("claims")
    )
    claims = Claim.objects.filter(created_at__gte=since)
    previous = _headline(
        Conversation.objects.filter(started_at__gte=previous_since, started_at__lt=since),
        Claim.objects.filter(created_at__gte=previous_since, created_at__lt=since),
    )
    head = _headline(calls, claims)
    total_calls = head["calls"]
    verified_calls = head["verified"]
    with_claim = head["with_claim"]
    total_claims = head["claims"]
    durations = sorted(c.duration_seconds for c in calls if c.duration_seconds)

    # --- where the agent needed a second attempt ---------------------------
    # Every rejection the webhook recorded, grouped by the field it was missing.
    rejections = {}
    calls_with_rejection = 0
    for call in calls:
        seen = False
        for entry in call.tool_calls or []:
            if entry.get("rejected"):
                field = entry.get("missing", "unknown")
                rejections[field] = rejections.get(field, 0) + 1
                seen = True
        calls_with_rejection += bool(seen)

    checks = VerificationAttempt.objects.filter(created_at__gte=since).aggregate(
        total=Count("id"), passed=Count("id", filter=Q(passed=True))
    )
    checks_total, checks_passed = checks["total"], checks["passed"]

    # One pass over claims for the bands and the incident mix, instead of a
    # query per bar. This endpoint is polled, so the difference is per-visitor
    # per-refresh, not once.
    band_counts = claims.aggregate(
        low=Count("id", filter=Q(risk_score__lt=25)),
        medium=Count("id", filter=Q(risk_score__gte=25, risk_score__lt=50)),
        high=Count("id", filter=Q(risk_score__gte=50, risk_score__lt=75)),
        critical=Count("id", filter=Q(risk_score__gte=75)),
        **{
            f"type_{value}": Count("id", filter=Q(incident_type=value))
            for value, _ in IncidentType.choices
        },
    )
    risk_bands = [
        ("Low", band_counts["low"]),
        ("Medium", band_counts["medium"]),
        ("High", band_counts["high"]),
        ("Critical", band_counts["critical"]),
    ]
    by_type = [
        {"key": value, "label": label, "count": band_counts[f"type_{value}"]}
        for value, label in IncidentType.choices
    ]

    channel_counts = calls.aggregate(
        **{
            f"c_{value}": Count("id", filter=Q(channel=value))
            for value, _ in Conversation.Channel.choices
        }
    )
    by_channel = [
        {"key": value, "label": label, "count": channel_counts[f"c_{value}"]}
        for value, label in Conversation.Channel.choices
    ]

    # --- one bucket per day ------------------------------------------------
    # Bucketed in Python rather than SQL: it is a few hundred rows at this
    # scale, and it keeps the same code working on SQLite and Postgres, which
    # disagree about date truncation and time zones.
    bucket_days = min(days, 90)
    start = (timezone.now() - timedelta(days=bucket_days - 1)).date()
    buckets = {
        start + timedelta(days=offset): {"calls": 0, "claims": 0, "verified": 0, "seconds": []}
        for offset in range(bucket_days)
    }
    for call in calls:
        day = timezone.localtime(call.started_at).date()
        if day in buckets:
            buckets[day]["calls"] += 1
            buckets[day]["verified"] += bool(call.verified)
            if call.duration_seconds:
                buckets[day]["seconds"].append(call.duration_seconds)
    for claim in claims:
        day = timezone.localtime(claim.created_at).date()
        if day in buckets:
            buckets[day]["claims"] += 1

    timeseries = [
        {
            "date": day.isoformat(),
            "calls": row["calls"],
            "claims": row["claims"],
            "verified": row["verified"],
            "median_seconds": round(
                sorted(row["seconds"])[len(row["seconds"]) // 2] if row["seconds"] else 0
            ),
        }
        for day, row in sorted(buckets.items())
    ]

    # --- when the phone rings ---------------------------------------------
    hours = [0] * 24
    weekdays = [0] * 7
    duration_counts = [0] * len(DURATION_BUCKETS)
    notable = []
    for call in calls:
        local = timezone.localtime(call.started_at)
        hours[local.hour] += 1
        weekdays[local.weekday()] += 1
        if call.duration_seconds:
            placed = False
            for index, (limit, _) in enumerate(DURATION_BUCKETS):
                if limit is None or call.duration_seconds < limit:
                    duration_counts[index] += 1
                    placed = True
                    break
            if not placed:
                duration_counts[-1] += 1

        reasons = []
        if any(entry.get("rejected") for entry in (call.tool_calls or [])):
            reasons.append("Needed a second filing")
        claim = call.claims.all()[0] if call.claims.all() else None
        if claim and not call.verified:
            reasons.append("Claim without a verified caller")
        if call.end_reason == "could_not_verify":
            reasons.append("Could not verify")
        if call.duration_seconds and durations and call.duration_seconds >= max(head["p90_seconds"], 240):
            reasons.append("Long call")
        if reasons:
            notable.append(
                {
                    "id": call.id,
                    "when": call.started_at.isoformat(),
                    "who": call.policyholder.full_name if call.policyholder_id else "Unidentified",
                    "channel": call.get_channel_display(),
                    "duration": round(call.duration_seconds),
                    "verified": call.verified,
                    "end_reason": END_REASONS.get(
                        call.end_reason, call.end_reason.replace("_", " ").capitalize()
                    )
                    if call.end_reason
                    else "Still open / no reason",
                    "claim_id": claim.id if claim else None,
                    "flags": reasons,
                }
            )
    notable = sorted(notable, key=lambda row: row["when"], reverse=True)[:12]

    ended = {}
    for call in calls.exclude(end_reason=""):
        ended[call.end_reason] = ended.get(call.end_reason, 0) + 1
    agent_ended = sum(ended.values())

    places = Counter()
    for claim in claims.exclude(location=""):
        places[claim.location.strip()] += 1

    dispatches = Dispatch.objects.filter(created_at__gte=since)
    kind_counts = dispatches.aggregate(
        total=Count("id"),
        open=Count(
            "id",
            filter=~Q(status__in=[Dispatch.Status.ARRIVED, Dispatch.Status.CANCELLED]),
        ),
        manual=Count("id", filter=~Q(raised_by="system")),
        **{
            f"k_{value}": Count("id", filter=Q(kind=value))
            for value, _ in Dispatch.Kind.choices
        },
    )
    by_kind = [
        {"key": value, "label": label, "count": kind_counts[f"k_{value}"]}
        for value, label in Dispatch.Kind.choices
    ]

    def delta(key):
        return head[key] - previous[key]

    return JsonResponse(
        {
            "window_days": days,
            "generated_at": now.isoformat(),
            "previous": previous,
            "deltas": {
                "calls": delta("calls"),
                "claims": delta("claims"),
                "verified_rate": head["verified_rate"] - previous["verified_rate"],
                "completion_rate": head["completion_rate"] - previous["completion_rate"],
                "median_seconds": delta("median_seconds"),
            },
            "timeseries": timeseries,
            "hours": [{"hour": hour, "count": count} for hour, count in enumerate(hours)],
            "weekdays": [
                {"label": WEEKDAYS[index], "count": weekdays[index]} for index in range(7)
            ],
            "durations": [
                {"label": label, "count": duration_counts[index]}
                for index, (_, label) in enumerate(DURATION_BUCKETS)
            ],
            "locations": [
                {"label": name, "count": count}
                for name, count in places.most_common(8)
            ],
            "drivable": [
                {"label": "Still drivable", "count": claims.filter(is_drivable=True).count()},
                {"label": "Needs a tow", "count": claims.filter(is_drivable=False).count()},
            ],
            "injuries": [
                {"label": "Injuries reported", "count": claims.filter(injuries_reported=True).count()},
                {"label": "No injuries", "count": claims.filter(injuries_reported=False).count()},
                {
                    "label": "Not asked / unknown",
                    "count": claims.filter(injuries_reported__isnull=True).count(),
                },
            ],
            "claim_status": [
                {
                    "key": value,
                    "label": label,
                    "count": claims.filter(status=value).count(),
                }
                for value, label in ClaimStatus.choices
            ],
            "end_reasons": sorted(
                (
                    {
                        "key": key,
                        "label": END_REASONS.get(key, key.replace("_", " ").capitalize()),
                        "count": count,
                    }
                    for key, count in ended.items()
                ),
                key=lambda row: -row["count"],
            ),
            "dispatch": {
                "total": kind_counts["total"],
                "open": kind_counts["open"],
                "manual": kind_counts["manual"],
                "by_kind": [row for row in by_kind if row["count"]],
            },
            "headline": {
                **head,
                "dropoff_after_verify": max(0, verified_calls - with_claim),
            },
            # Where callers drop out, in order. Each stage is a subset of the one
            # before it, so the bars are honestly comparable.
            "funnel": [
                {"label": "Calls answered", "count": total_calls, "from_previous": None},
                {
                    "label": "Identity verified",
                    "count": verified_calls,
                    "from_previous": percent(verified_calls, total_calls),
                },
                {
                    "label": "Claim filed",
                    "count": with_claim,
                    "from_previous": percent(with_claim, verified_calls),
                },
                {
                    "label": "Tow dispatched",
                    "count": claims.filter(tow_required=True).count(),
                    "from_previous": percent(
                        claims.filter(tow_required=True).count(), with_claim
                    ),
                },
            ],
            "risk_bands": [{"label": label, "count": count} for label, count in risk_bands],
            "incident_types": by_type,
            "channels": by_channel,
            "notable": notable,
            "quality": {
                "calls_with_rejection": calls_with_rejection,
                "rejection_rate": percent(calls_with_rejection, total_calls),
                "rejections": sorted(
                    (
                        {
                            "field": field,
                            "label": MISSING_LABELS.get(field, field),
                            "count": count,
                        }
                        for field, count in rejections.items()
                    ),
                    key=lambda row: -row["count"],
                ),
                "verification_checks": checks_total,
                "verification_pass_rate": percent(checks_passed, checks_total),
                "verification_failed": checks_total - checks_passed,
                "average_risk": round(
                    claims.aggregate(value=Avg("risk_score"))["value"] or 0
                ),
                "unverified_claims": claims.filter(
                    Q(conversation__isnull=True) | Q(conversation__verified=False)
                ).count(),
                "policyholders": Policyholder.objects.count(),
                "agent_ended_calls": agent_ended,
                "agent_ended_rate": percent(agent_ended, total_calls),
                "median_turns": round(
                    quantile(sorted(len(c.turns or []) for c in calls), 0.5)
                ),
            },
        }
    )


def insights(request):
    return render(request, "claims/insights.html", {})


@require_GET
def export_calls(request):
    """The call log as JSONL, one call per line.

    Shaped for fine-tuning and eval sets rather than for a spreadsheet: each
    line carries the transcript, what the tools were called with, and how the
    call actually turned out.
    """
    from django.http import StreamingHttpResponse
    import json

    def lines():
        queryset = Conversation.objects.select_related("policyholder").order_by("started_at")
        days = request.GET.get("days")
        if days and days.isdigit():
            queryset = queryset.filter(
                started_at__gte=timezone.now() - timedelta(days=int(days))
            )
        if request.GET.get("verified") == "1":
            queryset = queryset.filter(verified=True)
        for call in queryset.iterator():
            claim = call.claim
            yield json.dumps(
                {
                    "id": call.id,
                    "started_at": call.started_at.isoformat(),
                    "channel": call.channel,
                    "duration_seconds": round(call.duration_seconds, 1),
                    "verified": call.verified,
                    "turns": call.turns or [],
                    "tool_calls": call.tool_calls or [],
                    "outcome": {
                        "claim_filed": bool(claim),
                        "reference": f"CV-{claim.id:05d}" if claim else None,
                        "incident_type": claim.incident_type if claim else None,
                        "risk_score": claim.risk_score if claim else None,
                        "tow_required": claim.tow_required if claim else None,
                    },
                    "has_recording": bool(call.recording),
                },
                ensure_ascii=False,
            ) + "\n"

    response = StreamingHttpResponse(lines(), content_type="application/x-ndjson")
    stamp = timezone.now().strftime("%Y%m%d-%H%M")
    response["Content-Disposition"] = f'attachment; filename="claimvoice-calls-{stamp}.jsonl"'
    return response


@require_GET
def pulse(request):
    """A cheap heartbeat every page can poll.

    The alternative — every page refetching its own full feed on a timer — costs
    tens of kilobytes and a handful of joins per tick, per open tab, forever,
    and almost every tick returns exactly what the page already had. This
    returns counters instead: a few COUNTs and MAXes, a few hundred bytes. A
    page compares the ones it cares about with what it saw last time and only
    goes back for real data when one of them moves.
    """
    from django.db.models import Max

    from .models import ClaimNote, ClaimPhoto, Handoff, HandoffAttempt, VendorCall

    def mark(model, extra=None):
        row = model.objects.aggregate(n=Count("id"), last=Max("id"), **(extra or {}))
        return {"n": row["n"], "last": row["last"] or 0, **{
            k: v for k, v in row.items() if k not in ("n", "last")
        }}

    return JsonResponse(
        {
            "claims": mark(Claim, {"handoffs": Count("id", filter=Q(needs_human=True))}),
            "conversations": mark(Conversation),
            "dispatches": mark(Dispatch, {"touched": Max("updated_at")}),
            # The queue moves without any row being added — somebody answers,
            # somebody gives up — so the open count rides along with the ids.
            "handoffs": {
                **mark(
                    Handoff,
                    {
                        "waiting": Count("id", filter=~Q(status__in=list(Handoff.CLOSED))),
                        "touched": Max("ended_at"),
                    },
                ),
                # Ringing the next person down the rota adds no handoff and
                # changes no status, and it is the thing a watching board most
                # wants to see move.
                "tried": HandoffAttempt.objects.count(),
            },
            "vendor_calls": mark(VendorCall),
            "notes": mark(ClaimNote),
            "photos": mark(ClaimPhoto),
            "policyholders": {"n": Policyholder.objects.count()},
            "now": timezone.now().isoformat(),
        }
    )
