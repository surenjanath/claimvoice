"""The book of vendors a dispatcher would actually call.

No Towbook account. Each kind has a small roster; location words pick a
region, otherwise the claim id rotates the list so the same filing always
gets the same truck.
"""

BOOK = {
    "tow": [
        {"name": "Southern Main Recovery", "phone": "+18685550110", "match": "chaguanas trinidad port of spain"},
        {"name": "I-95 Rapid Tow", "phone": "+15550119500", "match": "i-95 interstate 95 highway 95"},
        {"name": "Springfield Flatbed", "phone": "+15550143020", "match": "springfield elm oakwood"},
        {"name": "Regional Towing", "phone": "+15550148800", "match": ""},
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


def pick_vendor(kind, location="", claim_id=0):
    roster = BOOK.get(kind) or BOOK["tow"]
    blob = (location or "").lower()
    for vendor in roster:
        needles = [n for n in vendor["match"].split() if n]
        if needles and any(n in blob for n in needles):
            return vendor
    return roster[claim_id % len(roster)]
