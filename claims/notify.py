"""A text or email after the claim files.

Uses the same Twilio account as `connect_phone`. Missing credentials, a missing
number, or a send error must never block the claim itself.
"""

import base64
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

log = logging.getLogger("claims")


def claim_share_url(claim):
    base = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
    if not base:
        from .models import AgentProfile

        base = AgentProfile.load().base_url
    if not base:
        return ""
    return f"{base}{claim.share_path}" if claim.share_path else ""


def claim_sms_body(claim):
    reference = f"CV-{claim.id:05d}"
    link = claim_share_url(claim)
    extra = f" Photos and status: {link}" if link else ""
    if claim.tow_required:
        eta = 18 + (claim.id * 7) % 18
        return (
            f"ClaimVoice: {reference} is filed. A tow is on the way to {claim.location}, "
            f"about {eta} minutes.{extra} Keep this text."
        )
    return (
        f"ClaimVoice: {reference} is filed for {claim.location}. "
        f"An adjuster will be in touch.{extra} Keep this text."
    )


def send_sms(to, body):
    sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
    token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    source = os.environ.get("TWILIO_PHONE_NUMBER", "")
    if not (sid and token and source):
        return {"sent": False, "skipped": "unset"}
    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
    auth = base64.b64encode(f"{sid}:{token}".encode()).decode()
    payload = urllib.parse.urlencode({"To": to, "From": source, "Body": body}).encode()
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Authorization", f"Basic {auth}")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=8) as res:
            data = json.loads(res.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()[:240]
        log.warning("SMS to %s failed (%s): %s", to, exc.code, detail)
        return {"sent": False, "error": f"{exc.code}"}
    except OSError as exc:
        log.warning("SMS to %s failed: %s", to, exc)
        return {"sent": False, "error": "network"}
    return {"sent": True, "sid": data.get("sid", "")}


def send_claim_email(claim):
    holder = claim.policyholder if claim.policyholder_id else None
    to = (holder.email if holder else "") or ""
    if not to:
        return {"sent": False, "skipped": "no_email"}
    backend = getattr(settings, "EMAIL_BACKEND", "")
    host = getattr(settings, "EMAIL_HOST", "") or ""
    if "smtp" in backend and not host:
        return {"sent": False, "skipped": "unset"}
    if (
        not host
        and "locmem" not in backend
        and "console" not in backend
        and "dummy" not in backend
        and "inmemory" not in backend
    ):
        return {"sent": False, "skipped": "unset"}
    try:
        send_mail(
            subject=f"ClaimVoice {claim.reference} filed",
            message=claim_sms_body(claim),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[to],
            fail_silently=False,
        )
    except Exception as exc:
        log.warning("Email to %s failed: %s", to, exc)
        return {"sent": False, "error": "send"}
    return {"sent": True}


def _status_from(result):
    if result.get("sent"):
        return "sent"
    if result.get("error"):
        return "failed"
    return result.get("skipped") or "skipped"


def _stamp(claim, sms, email):
    fields = ["sms_status", "sms_sent_at", "email_status"]
    claim.sms_status = _status_from(sms)
    if sms.get("sent"):
        claim.sms_sent_at = timezone.now()
    claim.email_status = _status_from(email)
    claim.save(update_fields=fields)
    return sms, email


def notify_claim(claim):
    sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
    token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    source = os.environ.get("TWILIO_PHONE_NUMBER", "")
    if not (sid and token and source):
        sms = {"sent": False, "skipped": "unset"}
    else:
        to = ""
        if claim.policyholder_id and claim.policyholder:
            to = claim.policyholder.phone
        if not to:
            sms = {"sent": False, "skipped": "no_number"}
        else:
            sms = send_sms(to, claim_sms_body(claim))
    email = send_claim_email(claim)
    _stamp(claim, sms, email)
    out = dict(sms)
    out["email"] = email
    return out
