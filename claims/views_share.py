"""The page a caller opens from the text: the claim, the tow, photos."""

import mimetypes

from django.contrib import messages
from django.http import FileResponse, Http404, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .geo import pin_claim
from .models import AgentRevision, AgentProfile, Claim, ClaimPhoto

MAX_PHOTO_BYTES = 4 * 1024 * 1024
PHOTO_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}

# The first bytes of each format we accept. Checked because content_type is
# whatever the client claims it is.
MAGIC = (
    b"\xff\xd8\xff",       # jpeg
    b"\x89PNG\r\n\x1a\n",  # png
    b"RIFF",                 # webp (RIFF....WEBP)
    b"GIF87a",
    b"GIF89a",
)


def _looks_like_an_image(upload):
    head = upload.read(12)
    upload.seek(0)
    if not head.startswith(MAGIC):
        return False
    if head.startswith(b"RIFF"):
        return head[8:12] == b"WEBP"
    return True


def _claim_from_token(token):
    """Resolve the caller's link.

    Only ever by token. Accepting a claim id here — or a CV- reference, which
    is one — would put every other claim one increment away from anybody
    holding a single link.
    """
    token = str(token or "").strip()
    if not token:
        raise Http404("no claim")
    return get_object_or_404(Claim, share_token=token)


def claim_share(request, token):
    claim = _claim_from_token(token)
    pin_claim(claim)
    return render(
        request,
        "claims/claim.html",
        {
            "claim": claim,
            "photos": claim.photos.all(),
            "reference": claim.reference,
        },
    )


@require_GET
def claim_live(request, token):
    claim = get_object_or_404(
        Claim.objects.prefetch_related("dispatches", "photos", "notes"),
        share_token=str(token or "").strip(),
    )
    return JsonResponse(
        {
            "id": claim.id,
            "status": claim.status,
            "status_label": claim.get_status_display(),
            "needs_human": claim.needs_human,
            "handoff_reason": claim.handoff_reason,
            "photo_count": len(claim.photos.all()),
            "sms_status": claim.sms_status,
            "email_status": claim.email_status,
            "assigned_to": claim.assigned_to,
            "risk_score": claim.risk_score,
            "risk_factors": claim.risk_factors,
            "severity_label": claim.get_severity_display() if claim.severity else "",
            "injuries_reported": claim.injuries_reported,
            "vehicle": claim.vehicle,
            "vehicle_mismatch": claim.vehicle_mismatch(),
            "coverage_label": (
                claim.policyholder.get_coverage_display()
                if claim.policyholder_id and claim.policyholder
                else ""
            ),
            "roadside_assistance": (
                claim.policyholder.roadside_assistance
                if claim.policyholder_id and claim.policyholder
                else None
            ),
            "dispatches": [d.as_dict() for d in claim.dispatches.all()],
            "notes": [n.as_dict() for n in claim.notes.all()[:20]],
        }
    )


@require_GET
def claim_photos(request, token):
    claim = _claim_from_token(token)
    return JsonResponse({"photos": [p.as_dict() for p in claim.photos.all()]})


@csrf_exempt
def upload_claim_photo(request, token):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    claim = _claim_from_token(token)
    upload = request.FILES.get("photo") or request.FILES.get("image")
    if not upload:
        return JsonResponse({"error": "no photo"}, status=400)
    if upload.size > MAX_PHOTO_BYTES:
        return JsonResponse({"error": "photo too large", "limit": MAX_PHOTO_BYTES}, status=413)
    # Declared type, then the bytes. A client that simply omits the header
    # would otherwise walk past the check entirely.
    kind = (getattr(upload, "content_type", "") or "").split(";")[0].strip()
    if kind not in PHOTO_TYPES or not _looks_like_an_image(upload):
        return JsonResponse({"error": "use a jpeg, png or webp"}, status=400)
    photo = ClaimPhoto.objects.create(
        claim=claim,
        image=upload,
        caption=str(request.POST.get("caption") or "")[:120],
    )
    accept = request.headers.get("Accept", "")
    if "application/json" in accept:
        return JsonResponse({"ok": True, "photo": photo.as_dict()})
    return redirect("claim-share", token=claim.share_token)


@require_GET
def claim_photo_file(request, token, pk):
    # Scoped to the claim the token names, so a photo id from one claim cannot
    # be read through another claim's link.
    photo = get_object_or_404(ClaimPhoto, pk=pk, claim__share_token=str(token or "").strip())
    if not photo.image:
        raise Http404("no photo")
    kind = mimetypes.guess_type(photo.image.name)[0] or "image/jpeg"
    return FileResponse(photo.image.open("rb"), content_type=kind)


@require_POST
def restore_revision(request, pk):
    profile = AgentProfile.load()
    revision = get_object_or_404(AgentRevision, pk=pk, profile=profile)
    profile.system_prompt = revision.system_prompt
    profile.greeting = revision.greeting
    if revision.voice_id:
        profile.voice_id = revision.voice_id
    profile.save(update_fields=["system_prompt", "greeting", "voice_id"])
    if request.headers.get("Accept", "").find("application/json") >= 0:
        return JsonResponse({"ok": True, "revision": revision.as_dict()})
    messages.success(request, "Restored that published prompt. Save & publish to push it live.")
    return redirect(reverse("settings"))
