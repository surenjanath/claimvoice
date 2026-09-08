"""Sending things to the scene.

The risk engine raises the obvious one on ingest — a tow, when the caller says
the car cannot be driven. Everything else is a judgement call, so a dispatcher
makes it here.
"""

import json

from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET, require_POST

from .models import Claim, Dispatch
from .vendors import BOOK, pick_vendor


def raise_automatic_dispatches(claim, eta_minutes):
    """Rows the webhook already promised the caller, so the board matches Ivy."""
    existing = set(claim.dispatches.values_list("kind", flat=True))
    rows = []

    def add(kind, eta, notes, status=Dispatch.Status.REQUESTED):
        if kind in existing:
            return
        vendor = pick_vendor(kind, claim.location, claim.id)
        row = Dispatch.objects.create(
            claim=claim,
            kind=kind,
            vendor=vendor["name"],
            vendor_phone=vendor["phone"],
            eta_minutes=eta,
            status=status,
            raised_by="system",
            notes=notes,
        )
        existing.add(kind)
        rows.append(row)

    if claim.tow_required:
        add(
            Dispatch.Kind.TOW,
            eta_minutes,
            "Raised automatically: caller reported the vehicle is not drivable.",
            Dispatch.Status.EN_ROUTE,
        )
    if claim.incident_type == "theft":
        add(
            Dispatch.Kind.INVESTIGATOR,
            45,
            "Raised automatically: theft claim.",
        )
    if claim.injuries_reported:
        add(
            Dispatch.Kind.AMBULANCE,
            12,
            "Raised automatically: injuries reported.",
        )
    holder = claim.policyholder
    if claim.tow_required and holder and holder.rental_cover:
        add(
            Dispatch.Kind.RENTAL,
            40,
            "Raised automatically: policy includes rental cover.",
        )
    return rows


def raise_automatic_dispatch(claim, eta_minutes):
    """Back-compat: the tow row, or the first automatic row."""
    rows = raise_automatic_dispatches(claim, eta_minutes)
    return next((row for row in rows if row.kind == Dispatch.Kind.TOW), rows[0] if rows else None)


@require_GET
def dispatch_feed(request):
    """The board of everything currently on its way."""
    queryset = Dispatch.objects.select_related("claim", "claim__policyholder")
    status = request.GET.get("status")
    if status in Dispatch.Status.values:
        queryset = queryset.filter(status=status)
    if request.GET.get("open") == "1":
        queryset = queryset.exclude(
            status__in=[Dispatch.Status.ARRIVED, Dispatch.Status.CANCELLED]
        )

    rows = []
    for dispatch in queryset[:200]:
        data = dispatch.as_dict()
        claim = dispatch.claim
        data["claim_reference"] = f"CV-{claim.id:05d}"
        data["location"] = claim.location
        data["policy_number"] = claim.policy_number
        data["policyholder"] = (
            claim.policyholder.full_name if claim.policyholder else claim.caller_name
        )
        data["risk_score"] = claim.risk_score
        data["priority"] = claim.priority
        data["lat"] = claim.lat
        data["lng"] = claim.lng
        rows.append(data)
    return JsonResponse({"dispatches": rows, "kinds": _kind_options()})


def _kind_options():
    return [{"value": value, "label": label} for value, label in Dispatch.Kind.choices]


@require_GET
def vendor_feed(request):
    """The book a dispatcher picks from when they raise a row by hand."""
    kind = (request.GET.get("kind") or "").strip()
    rows = []
    kinds = [kind] if kind and kind in BOOK else list(BOOK)
    for item in kinds:
        for vendor in BOOK[item]:
            rows.append(
                {
                    "kind": item,
                    "name": vendor["name"],
                    "phone": vendor["phone"],
                }
            )
    return JsonResponse({"vendors": rows, "kinds": _kind_options()})


@require_POST
def create_dispatch(request, pk):
    """A dispatcher sending something themselves."""
    claim = get_object_or_404(Claim, pk=pk)
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "invalid json"}, status=400)

    kind = payload.get("kind")
    if kind not in Dispatch.Kind.values:
        return JsonResponse(
            {"error": "unknown kind", "kinds": Dispatch.Kind.values}, status=400
        )

    eta = payload.get("eta_minutes")
    try:
        eta = int(eta) if eta not in (None, "") else None
    except (TypeError, ValueError):
        return JsonResponse({"error": "eta_minutes must be a number"}, status=400)
    if eta is not None and not 0 <= eta <= 600:
        return JsonResponse({"error": "eta_minutes must be 0-600"}, status=400)

    vendor_name = str(payload.get("vendor") or "").strip()[:120]
    vendor_phone = str(payload.get("vendor_phone") or "").strip()[:24]
    if not vendor_name:
        picked = pick_vendor(kind, claim.location, claim.id)
        vendor_name = picked["name"]
        vendor_phone = vendor_phone or picked["phone"]

    dispatch = Dispatch.objects.create(
        claim=claim,
        kind=kind,
        vendor=vendor_name,
        vendor_phone=vendor_phone,
        eta_minutes=eta,
        notes=str(payload.get("notes") or "").strip()[:255],
        raised_by=str(payload.get("raised_by") or "Dispatcher").strip()[:60] or "Dispatcher",
        status=Dispatch.Status.REQUESTED,
    )

    # A claim someone has actively sent help to is no longer a new claim.
    if claim.status == "new":
        claim.status = "dispatched"
        claim.save(update_fields=["status"])

    return JsonResponse({"ok": True, "dispatch": dispatch.as_dict(), "claim": claim.as_dict()})


@require_POST
def dispatch_status(request, pk):
    dispatch = get_object_or_404(Dispatch, pk=pk)
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        payload = {}
    status = payload.get("status")
    if status not in Dispatch.Status.values:
        return JsonResponse({"error": "unknown status"}, status=400)
    dispatch.status = status
    dispatch.save(update_fields=["status", "updated_at"])
    return JsonResponse({"ok": True, "dispatch": dispatch.as_dict()})
