"""Desk login. Voice, share, and AssemblyAI webhooks stay public.

Two ways in, and which one applies is decided by whether anybody has created a
dispatcher:

* **No accounts** — one shared password and a name typed into a box. This is
  the demo, and it is what every existing deployment has.
* **Accounts exist** — email and password, per person. The shared password
  stops working at that point, deliberately: an account system you can walk
  around is decoration, and the whole reason for accounts is that a claim
  closed by "Dispatcher" answers no question worth asking later.

Create the first one with `python manage.py create_dispatcher`.
"""

from urllib.parse import quote

from django.conf import settings
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods


PUBLIC_EXACT = {
    "/",
    "/login/",
    "/logout/",
    "/healthz/",
    "/api/tick/",
    "/api/token/",
    "/api/agent/",
    "/api/verify/",
    "/api/log-claim/",
    "/api/end-call/",
    "/api/vendor-eta/",
    "/api/request-human/",
    "/api/recordings/upload/",
    "/api/conversations/ingest/",
    "/api/conversations/session/",
}

# Everything a caller reaches is under one of these, and each is addressed by
# an unguessable token rather than a row id.
#
# /api/twilio/ is the carrier reporting on a call it is holding for us. It has
# no session and cannot get one, it arrives mid-call, and it is addressed by
# the handoff's token for exactly the reason the share links are.
PUBLIC_PREFIX = ("/c/", "/api/share/", "/api/twilio/", "/static/", "/media/")


def request_is_public(request):
    """What an unauthenticated request may reach.

    Three audiences: AssemblyAI posting tool calls, Twilio reporting on a leg
    of a call in progress, and a driver at the roadside opening the link they
    were texted. Nothing here is addressed by a row id — a public route keyed
    on a sequential id is an invitation to count through everyone else's
    claims.
    """
    path = request.path
    if path in PUBLIC_EXACT:
        return True
    if any(path.startswith(prefix) for prefix in PUBLIC_PREFIX):
        return True
    # One policy, by name, and only where this deployment has asked for demo
    # credentials. Never the book.
    if (
        path == "/api/policyholders/"
        and getattr(settings, "DEMO_CREDENTIALS", False)
        and (request.GET.get("policy") or "").strip()
    ):
        return True
    if path.startswith("/api/conversations/") and path.endswith("/recording/"):
        return True
    return False


def accounts_exist():
    from .models import Dispatcher

    return Dispatcher.objects.filter(is_active=True).exists()


def current_dispatcher(request):
    """The person at the desk, when there is one in particular."""
    from .models import Dispatcher

    identifier = request.session.get("desk_dispatcher")
    if not identifier:
        return None
    return Dispatcher.objects.filter(pk=identifier, is_active=True).first()


def desk_unlocked(request):
    if not getattr(settings, "DESK_AUTH", False):
        return True
    return bool(request.session.get("desk"))


class DeskAuthMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if desk_unlocked(request) or request_is_public(request):
            return self.get_response(request)
        if request.path.startswith("/api/"):
            return JsonResponse({"error": "login required"}, status=401)
        return redirect("/login/?next=" + quote(request.get_full_path()))


@require_http_methods(["GET", "POST"])
def login(request):
    if not getattr(settings, "DESK_AUTH", False):
        return redirect("/dashboard/")
    nxt = request.GET.get("next") or request.POST.get("next") or "/dashboard/"
    if not nxt.startswith("/"):
        nxt = "/dashboard/"
    if request.session.get("desk"):
        return redirect(nxt)

    by_account = accounts_exist()
    if request.method == "POST":
        if by_account:
            person = _authenticate(
                request.POST.get("email") or "", request.POST.get("password") or ""
            )
            if person:
                _start_session(request, person)
                return redirect(nxt)
            messages.error(request, "That email and password do not match.")
        else:
            expected = getattr(settings, "DESK_PASSWORD", "")
            if expected and (request.POST.get("password") or "") == expected:
                request.session["desk"] = True
                request.session["desk_dispatcher"] = None
                name = str(request.POST.get("name") or "").strip()[:40]
                request.session["desk_name"] = name or "Dispatcher"
                return redirect(nxt)
            messages.error(request, "That password is not right.")
    return render(request, "claims/login.html", {"next": nxt, "by_account": by_account})


def _authenticate(email, password):
    from .models import Dispatcher

    person = Dispatcher.objects.filter(
        email__iexact=email.strip(), is_active=True
    ).first()
    # check_password is run against a real hash either way, so a wrong email
    # and a wrong password take the same time to answer.
    if person and person.check_password(password):
        return person
    if not person:
        Dispatcher().check_password(password)
    return None


def _start_session(request, person):
    from .models_desk import record

    request.session["desk"] = True
    request.session["desk_dispatcher"] = person.id
    request.session["desk_name"] = person.name
    person.last_seen_at = timezone.now()
    person.save(update_fields=["last_seen_at"])
    record(person, "sign_in")


def logout(request):
    person = current_dispatcher(request)
    if person:
        from .models_desk import record

        record(person, "sign_out")
    request.session.pop("desk", None)
    request.session.pop("desk_name", None)
    request.session.pop("desk_dispatcher", None)
    return redirect("/login/")


def desk_context(request):
    person = current_dispatcher(request) if request.session.get("desk") else None
    return {
        "desk_auth": getattr(settings, "DESK_AUTH", False),
        "desk_ok": desk_unlocked(request),
        "desk_name": request.session.get("desk_name") or "Dispatcher",
        "desk_dispatcher": person,
        "desk_role": person.role if person else "",
    }
