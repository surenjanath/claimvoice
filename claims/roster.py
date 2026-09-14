"""Who is on the desk right now, and in what order to ring them.

A rota is the difference between a handoff and a queue. Without one, every
caller who asks for a person rings the same first name in the list, at four in
the morning as readily as at noon, and there is no answer to "what happens when
nobody picks up".

Two rules worth stating because they are the ones that surprise people:

* **An empty rota means the desk is always open.** A deployment that has never
  written a shift should not discover at 2am that it has silently gone out of
  hours. Shifts govern only once somebody writes one.
* **Being on call is not the same as being reachable.** Somebody with no phone
  number on file is on the board but never rung — they would be a silent leg in
  the escalation, which is worse than not being in it.
"""

from django.conf import settings
from django.utils import timezone

from .models import Dispatcher, Shift


def local_now(at=None):
    """The rota is written in the deployment's own clock, so read it there."""
    return timezone.localtime(at or timezone.now())


def rostered(at=None):
    """Dispatchers whose shift covers `at`, in the order they should be rung."""
    when = local_now(at)
    people = list(Dispatcher.objects.filter(is_active=True).prefetch_related("shifts"))
    if not Shift.objects.exists():
        # Nobody has written a rota. Everyone active is on.
        return people
    return [
        person
        for person in people
        if any(shift.covers(when) for shift in person.shifts.all())
    ]


def escalation(at=None, exclude=()):
    """The order to try, longest-idle first among equals.

    `order` is the deliberate part — a supervisor puts the senior person last —
    and `exclude` is who has already been tried on this handoff.
    """
    skip = {getattr(p, "id", p) for p in exclude}
    return [
        person
        for person in rostered(at)
        if person.reachable and person.id not in skip
    ]


def after_hours(at=None):
    """True when nobody at all is rostered, whatever the clock says.

    Deliberately not "outside 9 to 5": the rota is the authority. A desk with a
    night shift is not out of hours at midnight, and a desk whose whole rota
    called in sick is out of hours at noon.
    """
    return not rostered(at)


def fallback_number():
    """One number that is rung when the rota is empty or exhausted.

    A supervisor's mobile, usually. Not a dispatcher row, because the point of
    it is to work when the dispatcher rows have not.
    """
    return getattr(settings, "HANDOFF_FALLBACK_NUMBER", "")


def summary(at=None):
    """What the board says about cover, in words a person would use."""
    when = local_now(at)
    on = rostered(when)
    return {
        "at": when.isoformat(),
        "after_hours": not on,
        "rota_written": Shift.objects.exists(),
        "on_call": [p.as_dict() for p in on],
        "reachable": [p.as_dict() for p in on if p.reachable],
        "fallback": bool(fallback_number()),
    }
