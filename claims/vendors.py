"""The book of vendors a dispatcher would actually call.

No Towbook account. Each kind has a small roster; a depot coordinate picks the
nearest one when the claim has a pin, location words pick a region when it does
not, and otherwise the claim id rotates the list so the same filing always gets
the same truck.

Every number here is in a range reserved for fiction, so nothing in this file
can ring a real recovery operator even if outbound dialling is switched on.
"""

import math

BOOK = {
    "tow": [
        {"name": "Southern Main Recovery", "phone": "+15550110110",
         "match": "chaguanas trinidad port of spain", "at": (10.5155, -61.4108)},
        {"name": "I-95 Rapid Tow", "phone": "+15550119500",
         "match": "i-95 interstate 95 highway 95", "at": (39.9042, -75.1580)},
        {"name": "Springfield Flatbed", "phone": "+15550143020",
         "match": "springfield elm oakwood", "at": (42.1015, -72.5898)},
        {"name": "Harbor Point Recovery", "phone": "+15550143055",
         "match": "harbor dock pier waterfront", "at": (40.7128, -74.0060)},
        {"name": "Regional Towing", "phone": "+15550148800", "match": "", "at": None},
    ],
    "investigator": [
        {"name": "Northshore SIU", "phone": "+15550147711", "match": "theft garage westfield"},
        {"name": "Harbor Special Investigations", "phone": "+15550147722", "match": ""},
    ],
    "ambulance": [
        {"name": "Metro EMS", "phone": "+1555011911", "match": ""},
    ],
    "rental": [
        {"name": "Enterprise Replacement", "phone": "+15550143210", "match": ""},
    ],
    "adjuster": [
        {"name": "Field Adjusting Co.", "phone": "+15550145000", "match": ""},
    ],
    "locksmith": [
        {"name": "Night Key Locksmith", "phone": "+15550146000", "match": ""},
    ],
}


def distance_km(a, b):
    """Great-circle distance. Good enough to rank depots against each other."""
    if not a or not b:
        return None
    lat1, lon1 = a
    lat2, lon2 = b
    radius = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    h = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    return round(2 * radius * math.asin(math.sqrt(h)), 1)


def rank_vendors(kind, location="", claim_id=0, at=None, exclude=()):
    """The roster in the order worth calling, nearest first where we can tell.

    `exclude` carries the phone numbers already tried on this claim, so an
    escalation moves down the list instead of redialling the truck that just
    said no.
    """
    roster = [v for v in (BOOK.get(kind) or BOOK["tow"]) if v["phone"] not in set(exclude)]
    if not roster:
        return []
    blob = (location or "").lower()

    def sort_key(vendor):
        gap = distance_km(at, vendor.get("at")) if at else None
        matched = any(n in blob for n in vendor["match"].split() if n)
        # A named match still wins when there is no pin to measure against.
        return (
            0 if gap is not None else 1,
            gap if gap is not None else (0 if matched else 1),
            roster.index(vendor),
        )

    ranked = sorted(roster, key=sort_key)
    if at is None and not any(
        any(n in blob for n in v["match"].split() if n) for v in ranked
    ):
        # Nothing to go on: rotate, so one claim always gets the same truck.
        ranked = ranked[claim_id % len(ranked) :] + ranked[: claim_id % len(ranked)]
    return [dict(v, distance_km=distance_km(at, v.get("at")) if at else None) for v in ranked]


def pick_vendor(kind, location="", claim_id=0, at=None, exclude=()):
    ranked = rank_vendors(kind, location, claim_id, at=at, exclude=exclude)
    return ranked[0] if ranked else None
