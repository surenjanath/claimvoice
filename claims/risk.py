"""Lightweight underwriting pass that runs on every inbound claim.

No model, no service call: a transparent rule set that a dispatcher can read
off the dashboard and argue with. Each rule contributes points and a short
reason, and the reasons are what the UI shows.
"""

BASE_BY_INCIDENT = {
    "collision": 40,
    "theft": 35,
    "weather": 25,
}

# Words in the caller's own description that historically correlate with a
# severe loss. Matched case-insensitively against location + description.
SEVERITY_KEYWORDS = {
    "highway": (8, "Highway incident"),
    "freeway": (8, "Highway incident"),
    "interstate": (8, "Highway incident"),
    "motorway": (8, "Highway incident"),
    "intersection": (5, "Busy intersection"),
    "rollover": (15, "Rollover reported"),
    "flip": (15, "Rollover reported"),
    "fire": (18, "Fire reported"),
    "smoke": (10, "Smoke reported"),
    "airbag": (10, "Airbags deployed"),
    "flood": (10, "Flood water"),
    "water": (6, "Standing water"),
    "hail": (5, "Hail damage"),
    "tree": (6, "Fallen tree"),
    "multi": (8, "Multiple vehicles"),
    "truck": (6, "Heavy vehicle involved"),
    "night": (4, "Low light conditions"),
    "ice": (6, "Ice on road"),
    "snow": (5, "Snow conditions"),
}


# What the agent picked off the severity list, in points. This is a reported
# fact rather than a guess from keywords, so it outranks the scan below.
SEVERITY_POINTS = {
    "fire_or_smoke": (18, "Fire or smoke reported"),
    "rollover": (15, "Rollover reported"),
    "airbags_deployed": (10, "Airbags deployed"),
    "fluid_leak": (8, "Fluid leak"),
    "glass_or_dents": (2, "Glass or dent damage"),
    "none": (0, "No major damage"),
}


def score_claim(
    *,
    incident_type,
    is_drivable,
    location="",
    description="",
    injuries_reported=None,
    severity="",
):
    """Return (score 1-100, [reasons], tow_required).

    The score is clamped into 1..100 so an empty claim still ranks above
    nothing and a pile-up cannot run off the end of the scale.
    """
    factors = []
    score = BASE_BY_INCIDENT.get(incident_type, 30)
    factors.append(f"{incident_type.title()} baseline")

    if not is_drivable:
        score += 20
        factors.append("Vehicle not drivable")

    if injuries_reported is True:
        score += 30
        factors.append("Injuries reported")
    elif injuries_reported is False:
        score -= 5
        factors.append("No injuries reported")

    if severity in SEVERITY_POINTS:
        points, reason = SEVERITY_POINTS[severity]
        score += points
        factors.append(reason)

    haystack = f"{location} {description}".lower()
    seen = set(factors)
    for keyword, (points, reason) in SEVERITY_KEYWORDS.items():
        if keyword in haystack and reason not in seen:
            seen.add(reason)
            score += points
            factors.append(reason)

    # Theft has no vehicle to tow and no injuries to triage, so drivability
    # should not dominate its score.
    if incident_type == "theft" and not is_drivable:
        score -= 10

    score = max(1, min(100, score))
    tow_required = (not is_drivable) and incident_type != "theft"
    return score, factors, tow_required
