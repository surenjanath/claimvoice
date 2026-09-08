"""Best-effort pin for a free-text incident location.

Nominatim is free and needs no key. Failure is silent: the claim still files,
the map just omits that pin.
"""

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("claims")


def _candidates(location):
    query = (location or "").strip()
    if not query:
        return []
    seen = []
    for part in [query] + re.split(r",| near | in | just past ", query, flags=re.I):
        part = part.strip(" ,.")
        if part and part.lower() not in seen:
            seen.append(part.lower())
            yield part


def geocode(location):
    for query in _candidates(location):
        point = _lookup(query)
        if point:
            return point
    return None


def _lookup(query):
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
        {"q": query, "format": "json", "limit": 1}
    )
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "ClaimVoice/1.0 (hackathon FNOL demo)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as res:
            rows = json.loads(res.read().decode() or "[]")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        log.info("geocode skipped for %r: %s", query, exc)
        return None
    if not rows:
        return None
    try:
        return float(rows[0]["lat"]), float(rows[0]["lon"])
    except (KeyError, TypeError, ValueError):
        return None


def pin_claim(claim):
    if claim.lat is not None and claim.lng is not None:
        return claim
    from django.conf import settings

    if not getattr(settings, "GEOCODE_CLAIMS", True):
        return claim
    point = geocode(claim.location)
    if not point:
        return claim
    claim.lat, claim.lng = point
    claim.save(update_fields=["lat", "lng"])
    return claim
