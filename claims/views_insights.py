"""What the calls add up to.

The dispatcher board answers "what is happening now". This answers "how is the
agent doing", which is a different question and the one that tells you what to
change in the prompt.
"""

from datetime import timedelta

from django.db.models import Avg, Count, Q
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET

from .models import Claim, Conversation, IncidentType, Policyholder, VerificationAttempt

# What each rejected field means in words a person would use.
MISSING_LABELS = {
    "is_drivable": "Filed without asking if the car drives",
    "policy_number": "Filed without a policy number",
    "location": "Filed without a location",
    "incident_type": "Filed with an incident it could not categorise",
}


def percent(part, whole):
    return round(100 * part / whole) if whole else 0


@require_GET
def metrics(request):
    """Every number the insights page draws, in one request."""
    days = int(request.GET.get("days") or 30)
    since = timezone.now() - timedelta(days=days)

    calls = Conversation.objects.filter(started_at__gte=since)
    claims = Claim.objects.filter(created_at__gte=since)

    total_calls = calls.count()
    verified_calls = calls.filter(verified=True).count()
    with_claim = calls.filter(claims__isnull=False).distinct().count()
    total_claims = claims.count()

    finished = [c for c in calls if c.duration_seconds]
    durations = sorted(c.duration_seconds for c in finished)
    median = durations[len(durations) // 2] if durations else 0

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

    checks = VerificationAttempt.objects.filter(created_at__gte=since)
    checks_total = checks.count()
    checks_passed = checks.filter(passed=True).count()

    risk_bands = [
        ("Low", claims.filter(risk_score__lt=25).count()),
        ("Medium", claims.filter(risk_score__gte=25, risk_score__lt=50).count()),
        ("High", claims.filter(risk_score__gte=50, risk_score__lt=75).count()),
        ("Critical", claims.filter(risk_score__gte=75).count()),
    ]

    by_type = [
        {
            "key": value,
            "label": label,
            "count": claims.filter(incident_type=value).count(),
        }
        for value, label in IncidentType.choices
    ]

    by_channel = [
        {"key": value, "label": label, "count": calls.filter(channel=value).count()}
        for value, label in Conversation.Channel.choices
    ]

    return JsonResponse(
        {
            "window_days": days,
            "headline": {
                "calls": total_calls,
                "claims": total_claims,
                "verified_rate": percent(verified_calls, total_calls),
                "completion_rate": percent(with_claim, total_calls),
                "median_seconds": round(median),
                "recordings": calls.exclude(recording="").exclude(recording=None).count(),
            },
            # Where callers drop out, in order. Each stage is a subset of the one
            # before it, so the bars are honestly comparable.
            "funnel": [
                {"label": "Calls answered", "count": total_calls},
                {"label": "Identity verified", "count": verified_calls},
                {"label": "Claim filed", "count": with_claim},
                {"label": "Tow dispatched", "count": claims.filter(tow_required=True).count()},
            ],
            "risk_bands": [{"label": label, "count": count} for label, count in risk_bands],
            "incident_types": by_type,
            "channels": by_channel,
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
                "average_risk": round(
                    claims.aggregate(value=Avg("risk_score"))["value"] or 0
                ),
                "unverified_claims": claims.filter(
                    Q(conversation__isnull=True) | Q(conversation__verified=False)
                ).count(),
                "policyholders": Policyholder.objects.count(),
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
