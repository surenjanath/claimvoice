"""Desk login. Voice, share, and AssemblyAI webhooks stay public."""

from urllib.parse import quote

from django.conf import settings
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods


PUBLIC_EXACT = {
    "/",
    "/login/",
    "/logout/",
    "/healthz/",
    "/api/token/",
    "/api/agent/",
    "/api/verify/",
    "/api/log-claim/",
    "/api/end-call/",
    "/api/request-human/",
    "/api/recordings/upload/",
    "/api/conversations/ingest/",
    "/api/conversations/session/",
}

# Everything a caller reaches is under one of these, and each is addressed by
# an unguessable token rather than a row id.
PUBLIC_PREFIX = ("/c/", "/api/share/", "/static/", "/media/")


def request_is_public(request):
    """What an unauthenticated request may reach.

    Two audiences: AssemblyAI posting tool calls, and a driver at the roadside
    opening the link they were texted. Nothing here is addressed by a row id —
    a public route keyed on a sequential id is an invitation to count through
    everyone else's claims.
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
    if request.method == "POST":
        offered = request.POST.get("password") or ""
        expected = getattr(settings, "DESK_PASSWORD", "")
        if expected and offered == expected:
            request.session["desk"] = True
            name = str(request.POST.get("name") or "").strip()[:40]
            request.session["desk_name"] = name or "Dispatcher"
            return redirect(nxt)
        messages.error(request, "That password is not right.")
    return render(request, "claims/login.html", {"next": nxt})


def logout(request):
    request.session.pop("desk", None)
    request.session.pop("desk_name", None)
    return redirect("/login/")


def desk_context(request):
    return {
        "desk_auth": getattr(settings, "DESK_AUTH", False),
        "desk_ok": desk_unlocked(request),
        "desk_name": request.session.get("desk_name") or "Dispatcher",
    }
