"""The desk itself: who is on it, who is waiting, and who did what.

Everything here exists because a real phone number is answering. Up to that
point "the dispatcher" is a shared password and a name typed into a box, which
is fine for a demo and indefensible the moment a caller asks to speak to a
person:

* **Dispatchers** are people with a password and a phone. A call handed to
  "the desk" has to ring somebody in particular, and a claim closed by
  "Dispatcher" answers no question worth asking six months later.
* **Shifts** say who that somebody is at four in the morning. Without them
  every handoff rings the same first person in the list forever.
* **Handoffs** are the queue. Ivy asking for a person is the start of a record,
  not the end of one: who it rang, in what order, who picked up, how long the
  caller waited, and what was done for them when nobody did.
* **Desk actions** are the audit trail. Who moved this claim, and when.
"""

import secrets

from django.contrib.auth.hashers import check_password, make_password
from django.db import models
from django.utils import timezone


class Dispatcher(models.Model):
    """A person who can be rung, and who can log in.

    The password is hashed with Django's configured hasher — the same one
    `auth.User` uses. This is not `auth.User` because a dispatcher is defined
    by a phone number that a live call can be sent to, and half of them will
    never open the admin.
    """

    class Role(models.TextChoices):
        DISPATCHER = "dispatcher", "Dispatcher"
        SUPERVISOR = "supervisor", "Supervisor"

    name = models.CharField(max_length=80)
    email = models.EmailField(unique=True)
    # E.164. Blank is allowed — a supervisor who only reads the board never
    # needs one — but a dispatcher without a number is never rung.
    phone = models.CharField(max_length=24, blank=True)
    password = models.CharField(max_length=128)
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.DISPATCHER)
    is_active = models.BooleanField(default=True)
    # Who gets rung first when more than one person is on call.
    order = models.PositiveSmallIntegerField(default=100)
    created_at = models.DateTimeField(default=timezone.now)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["order", "name"]

    def __str__(self):
        return f"{self.name} <{self.email}>"

    def set_password(self, raw):
        self.password = make_password(raw)

    def check_password(self, raw):
        return bool(raw) and check_password(raw, self.password)

    @property
    def reachable(self):
        return bool(self.is_active and self.phone)

    def as_dict(self, include_phone=False):
        data = {
            "id": self.id,
            "name": self.name,
            "email": self.email,
            "role": self.role,
            "role_label": self.get_role_display(),
            "is_active": self.is_active,
            "reachable": self.reachable,
            "order": self.order,
            "last_seen_at": self.last_seen_at.isoformat() if self.last_seen_at else None,
        }
        # A desk phone is a person's mobile more often than not, so it goes out
        # only where the page is about reaching them.
        if include_phone:
            data["phone"] = self.phone
        return data


class Shift(models.Model):
    """When a dispatcher is on call, weekly and recurring.

    Times are local to the deployment's TIME_ZONE, because that is how a rota
    is written and read. A shift that ends before it starts runs through
    midnight — 22:00 to 06:00 is the night shift, not an empty set.
    """

    class Weekday(models.IntegerChoices):
        MONDAY = 0, "Monday"
        TUESDAY = 1, "Tuesday"
        WEDNESDAY = 2, "Wednesday"
        THURSDAY = 3, "Thursday"
        FRIDAY = 4, "Friday"
        SATURDAY = 5, "Saturday"
        SUNDAY = 6, "Sunday"

    dispatcher = models.ForeignKey(
        Dispatcher, related_name="shifts", on_delete=models.CASCADE
    )
    weekday = models.IntegerField(choices=Weekday.choices)
    starts = models.TimeField()
    ends = models.TimeField()

    class Meta:
        ordering = ["weekday", "starts"]

    def __str__(self):
        return f"{self.get_weekday_display()} {self.starts:%H:%M}–{self.ends:%H:%M}"

    @property
    def overnight(self):
        return self.ends <= self.starts

    def covers(self, when):
        """Whether this shift is running at `when` (a local datetime).

        An overnight shift belongs to the day it started on, so Friday 22:00 to
        06:00 covers Saturday at 02:00 as well.
        """
        clock = when.time()
        if not self.overnight:
            return when.weekday() == self.weekday and self.starts <= clock < self.ends
        if when.weekday() == self.weekday and clock >= self.starts:
            return True
        yesterday = (when.weekday() - 1) % 7
        return yesterday == self.weekday and clock < self.ends

    def as_dict(self):
        return {
            "id": self.id,
            "dispatcher_id": self.dispatcher_id,
            "weekday": self.weekday,
            "weekday_label": self.get_weekday_display(),
            "starts": self.starts.strftime("%H:%M"),
            "ends": self.ends.strftime("%H:%M"),
            "overnight": self.overnight,
        }


class Handoff(models.Model):
    """One caller waiting for a person, and everything tried on their behalf.

    A handoff is opened the moment Ivy calls `request_human` — or a dispatcher
    presses Take this call — and closed when somebody has spoken to them or it
    is clear nobody will. The row is the queue: the board reads it, the
    escalation writes it, and afterwards it is the only record of how long
    somebody held.
    """

    class Status(models.TextChoices):
        WAITING = "waiting", "Waiting"
        RINGING = "ringing", "Ringing"
        CONNECTED = "connected", "Connected"
        DONE = "done", "Done"
        NO_ANSWER = "no_answer", "Nobody answered"
        AFTER_HOURS = "after_hours", "Out of hours"
        ABANDONED = "abandoned", "Caller hung up"
        FAILED = "failed", "Transfer failed"

    # Statuses where nothing further is going to happen on its own.
    CLOSED = {Status.DONE, Status.NO_ANSWER, Status.ABANDONED, Status.FAILED}

    conversation = models.ForeignKey(
        "claims.Conversation", related_name="handoffs", on_delete=models.CASCADE
    )
    claim = models.ForeignKey(
        "claims.Claim",
        null=True,
        blank=True,
        related_name="handoffs",
        on_delete=models.SET_NULL,
    )
    reason = models.CharField(max_length=64, blank=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.WAITING
    )
    # Twilio posts the outcome of each leg back to us, and that route is public
    # by necessity. It is addressed by this, never by the row id, for the same
    # reason the caller's share link is.
    token = models.CharField(max_length=43, unique=True, blank=True, db_index=True)
    caller_number = models.CharField(max_length=24, blank=True)
    # The live PSTN leg, as Twilio knows it. Empty when the call arrived in a
    # browser, which cannot be transferred anywhere.
    provider_call_id = models.CharField(max_length=64, blank=True)
    dispatcher = models.ForeignKey(
        Dispatcher,
        null=True,
        blank=True,
        related_name="handoffs",
        on_delete=models.SET_NULL,
    )
    simulated = models.BooleanField(default=False)
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    connected_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "-created_at"])]

    def __str__(self):
        return f"handoff {self.id} · {self.get_status_display()}"

    def save(self, *args, **kwargs):
        if not self.token:
            self.token = secrets.token_urlsafe(24)
        super().save(*args, **kwargs)

    @property
    def open(self):
        return self.status not in self.CLOSED

    @property
    def waited_seconds(self):
        """How long the caller held before somebody spoke to them, or so far."""
        end = self.connected_at or self.ended_at or timezone.now()
        return max(0, round((end - self.created_at).total_seconds()))

    def as_dict(self):
        claim = self.claim
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "claim_id": claim.id if claim else None,
            "claim_reference": f"CV-{claim.id:05d}" if claim else None,
            "reason": self.reason,
            "status": self.status,
            "status_label": self.get_status_display(),
            "caller_number": self.caller_number,
            "caller_name": (
                self.conversation.policyholder.full_name
                if self.conversation and self.conversation.policyholder
                else ""
            ),
            "dispatcher": self.dispatcher.as_dict() if self.dispatcher else None,
            "simulated": self.simulated,
            "note": self.note,
            "open": self.open,
            "waited_seconds": self.waited_seconds,
            "created_at": self.created_at.isoformat(),
            "connected_at": self.connected_at.isoformat() if self.connected_at else None,
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "attempts": [a.as_dict() for a in self.attempts.all()],
        }


class HandoffAttempt(models.Model):
    """One person rung, and what came of it.

    Overflow is only legible afterwards if each leg is its own row: "rang Priya
    for twenty seconds, no answer, then rang Marcus" is the thing a supervisor
    actually asks about.
    """

    class Outcome(models.TextChoices):
        RINGING = "ringing", "Ringing"
        ANSWERED = "answered", "Answered"
        NO_ANSWER = "no_answer", "No answer"
        BUSY = "busy", "Busy"
        FAILED = "failed", "Failed"
        SIMULATED = "simulated", "Simulated"

    handoff = models.ForeignKey(
        Handoff, related_name="attempts", on_delete=models.CASCADE
    )
    dispatcher = models.ForeignKey(
        Dispatcher, null=True, blank=True, related_name="attempts", on_delete=models.SET_NULL
    )
    to_number = models.CharField(max_length=24, blank=True)
    outcome = models.CharField(
        max_length=16, choices=Outcome.choices, default=Outcome.RINGING
    )
    detail = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["created_at"]

    def as_dict(self):
        return {
            "id": self.id,
            "dispatcher_id": self.dispatcher_id,
            "dispatcher_name": self.dispatcher.name if self.dispatcher else "",
            "outcome": self.outcome,
            "outcome_label": self.get_outcome_display(),
            "detail": self.detail,
            "at": self.created_at.isoformat(),
        }


class DeskAction(models.Model):
    """Who did what, so the board can answer for itself.

    Written where a dispatcher changes something a caller would notice. Not a
    general event log: an audit trail that records everything records nothing,
    because nobody reads it.
    """

    dispatcher = models.ForeignKey(
        Dispatcher,
        null=True,
        blank=True,
        related_name="actions",
        on_delete=models.SET_NULL,
    )
    # Kept as text as well as a foreign key: the record has to survive the
    # person leaving and their row being deleted.
    who = models.CharField(max_length=80, blank=True)
    action = models.CharField(max_length=40)
    subject = models.CharField(max_length=40, blank=True)
    detail = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.who or 'system'} {self.action} {self.subject}"

    def as_dict(self):
        return {
            "id": self.id,
            "who": self.who,
            "action": self.action,
            "subject": self.subject,
            "detail": self.detail,
            "at": self.created_at.isoformat(),
        }


def record(dispatcher, action, subject="", detail=""):
    """Note a desk action. Never the reason a request fails."""
    return DeskAction.objects.create(
        dispatcher=dispatcher if isinstance(dispatcher, Dispatcher) else None,
        who=getattr(dispatcher, "name", "") or (dispatcher if isinstance(dispatcher, str) else ""),
        action=action[:40],
        subject=str(subject)[:40],
        detail=str(detail)[:255],
    )
