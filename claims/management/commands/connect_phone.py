"""Put Ivy on a real phone number.

    python manage.py connect_phone

Twilio hands inbound calls to AssemblyAI over SIP, so there is no media server,
no audio bridge and no webhook of our own in the path. This points a Twilio SIP
trunk at AssemblyAI, gives it your number, registers the number, and binds the
published agent to it.

Every step checks before it creates, so re-running is safe.

Needs in .env:
    TWILIO_ACCOUNT_SID=AC...
    TWILIO_AUTH_TOKEN=...
    TWILIO_PHONE_NUMBER=+15551234567          a number you already own, E.164
    TWILIO_TRUNK_DOMAIN=claimvoice.pstn.twilio.com   you choose the first part
"""

import base64
import json
import os
import re
import urllib.parse
import uuid
from urllib import error, request

from django.core.management.base import BaseCommand, CommandError

from claims.agent_api import AgentApiError, api
from claims.models import AgentProfile

TRUNKING = "https://trunking.twilio.com/v1/Trunks"
# Where Twilio sends the call. A fixed AssemblyAI address, not a setting.
SIP_URL = "sip:sip.assemblyai.com"


def twilio(url, form=None):
    """Twilio's REST API is form-encoded with basic auth, so this needs no SDK."""
    sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
    token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    auth = base64.b64encode(f"{sid}:{token}".encode()).decode()
    data = urllib.parse.urlencode(form).encode() if form else None
    req = request.Request(url, data=data, method="POST" if form else "GET")
    req.add_header("Authorization", f"Basic {auth}")
    if form:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with request.urlopen(req, timeout=30) as res:
            body = res.read().decode()
    except error.HTTPError as exc:
        detail = exc.read().decode()
        label = re.sub(r"https://[^/]+", "", url).split("?")[0]
        raise CommandError(
            f"Twilio {'POST' if form else 'GET'} {label} failed ({exc.code}): {detail}"
        ) from exc
    return json.loads(body) if body else {}


class Command(BaseCommand):
    help = "Attach the published agent to a Twilio phone number over SIP"

    def add_arguments(self, parser):
        parser.add_argument("--number", default=None, help="Override TWILIO_PHONE_NUMBER")
        parser.add_argument("--detach", action="store_true", help="Unbind the agent from the number")
        parser.add_argument(
            "--check",
            action="store_true",
            help="Check credentials, numbers and webhook reachability, then stop.",
        )

    def handle(self, *args, **options):
        if options["check"]:
            return self.preflight()

        for name in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN"):
            if not os.environ.get(name):
                raise CommandError(f"{name} is not set. Add it to .env.")

        number = options["number"] or os.environ.get("TWILIO_PHONE_NUMBER", "")
        trunk_domain = os.environ.get("TWILIO_TRUNK_DOMAIN", "")
        if not re.fullmatch(r"\+[1-9]\d{6,14}", number):
            raise CommandError(
                f"TWILIO_PHONE_NUMBER must be E.164, like +15551234567 (got {number!r})"
            )
        if not trunk_domain.endswith(".pstn.twilio.com"):
            raise CommandError(
                "TWILIO_TRUNK_DOMAIN must end in .pstn.twilio.com, "
                f"like claimvoice.pstn.twilio.com (got {trunk_domain!r})"
            )

        profile = AgentProfile.load()
        if not profile.agent_id:
            raise CommandError("No agent published yet. Run: python manage.py publish_agent")
        if not profile.webhook_url:
            raise CommandError(
                "No public base URL. A phone call has no browser to answer a tool, so "
                "log_claim must be an HTTP tool AssemblyAI can reach. Publish with "
                "--public-url first."
            )

        encoded = urllib.parse.quote(number, safe="")

        if options["detach"]:
            api(f"/phone-numbers/{encoded}/agent", method="PUT", body={"agent_id": None})
            self.stdout.write(self.style.SUCCESS(f"Detached the agent from {number}"))
            return

        account = os.environ["TWILIO_ACCOUNT_SID"]
        core = f"https://api.twilio.com/2010-04-01/Accounts/{account}"

        # 1. The number has to be one you already bought.
        owned = twilio(f"{core}/IncomingPhoneNumbers.json?PhoneNumber={encoded}")
        incoming = (owned.get("incoming_phone_numbers") or [None])[0]
        if not incoming:
            raise CommandError(
                f"{number} is not on this Twilio account. Buy it in the console first."
            )

        # 2. The trunk, matched by the domain you picked.
        trunks = twilio(TRUNKING)
        trunk = next(
            (t for t in trunks.get("trunks", []) if t["domain_name"] == trunk_domain), None
        )
        if trunk:
            self.stdout.write(f"Trunk: {trunk['sid']} (existing)")
        else:
            trunk = twilio(
                TRUNKING,
                {"FriendlyName": "ClaimVoice agent", "DomainName": trunk_domain},
            )
            self.stdout.write(f"Trunk: {trunk['sid']} (created)")

        # 3. Origination sends inbound calls to AssemblyAI.
        origination = twilio(f"{TRUNKING}/{trunk['sid']}/OriginationUrls")
        if any(u["sip_url"] == SIP_URL for u in origination.get("origination_urls", [])):
            self.stdout.write(f"Origination: already routed to {SIP_URL}")
        else:
            twilio(
                f"{TRUNKING}/{trunk['sid']}/OriginationUrls",
                {
                    "FriendlyName": "AssemblyAI SIP",
                    "SipUrl": SIP_URL,
                    "Priority": 1,
                    "Weight": 1,
                    "Enabled": "true",
                },
            )
            self.stdout.write(f"Origination: routed to {SIP_URL}")

        # 4. Hand the number to the trunk. From here the trunk owns it, and any
        # Voice webhook set on the number itself stops applying.
        if incoming.get("trunk_sid") == trunk["sid"]:
            self.stdout.write(f"Number: {number} already on this trunk")
        elif incoming.get("trunk_sid"):
            raise CommandError(
                f"{number} is attached to a different trunk ({incoming['trunk_sid']}). "
                "Detach it in the Twilio console, then re-run."
            )
        else:
            twilio(
                f"{TRUNKING}/{trunk['sid']}/PhoneNumbers",
                {"PhoneNumberSid": incoming["sid"]},
            )
            self.stdout.write(f"Number: {number} attached to trunk")

        # 5. Register the number with AssemblyAI, unless it already knows it.
        try:
            api(f"/phone-numbers/{encoded}")
            self.stdout.write(f"Registered: {number} already known to AssemblyAI")
        except AgentApiError as exc:
            if exc.status != 404:
                raise CommandError(str(exc)) from exc
            api(
                "/phone-numbers/import",
                method="POST",
                body={"phone_number": number, "termination_uri": trunk_domain},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            self.stdout.write(f"Registered: {number} imported")

        # 6. Bind the agent to the number, then read it back.
        api(
            f"/phone-numbers/{encoded}/agent",
            method="PUT",
            body={"agent_id": profile.agent_id},
        )
        confirmed = api(f"/phone-numbers/{encoded}")

        self.stdout.write(
            self.style.SUCCESS(
                f"Attached: agent {confirmed.get('agent_id', profile.agent_id)} answers {number}"
            )
        )
        self.stdout.write(f"\nCall {number}. Tools post to {profile.base_url}.")
        self.stdout.write(
            "A phone call has no browser, so no transcript is recorded — the call still "
            "shows on the board, built from its tool calls."
        )

    # --- preflight ---------------------------------------------------------

    def preflight(self):
        """Everything that has to be true before a number can answer.

        Run this first: each failure here becomes a confusing Twilio or SIP
        error later, usually as a call that connects and then goes silent.
        """
        ok = True

        def line(good, label, detail=""):
            mark = self.style.SUCCESS("  ok  ") if good else self.style.ERROR(" fail ")
            self.stdout.write(f"{mark} {label}" + (f"\n       {detail}" if detail else ""))
            return good

        self.stdout.write("Twilio credentials")
        sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
        token = os.environ.get("TWILIO_AUTH_TOKEN", "")
        if not sid or not token:
            ok = line(False, "TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN", "Not set in .env.")
        elif not sid.startswith("AC"):
            ok = line(False, "TWILIO_ACCOUNT_SID", f"Should start with AC, got {sid[:4]}…")
        else:
            try:
                account = twilio(f"https://api.twilio.com/2010-04-01/Accounts/{sid}.json")
                line(True, f"Signed in as \"{account.get('friendly_name', sid)}\"",
                     f"status: {account.get('status', 'unknown')}")
                # A trial account can hold a trunk but will only accept inbound
                # calls from caller IDs it has verified, so a number nobody else
                # can dial looks identical to a broken deployment.
                if str(account.get("type", "")).lower() == "trial":
                    self.stdout.write(
                        self.style.WARNING(
                            "  warn  This is a trial account. Inbound calls are refused\n"
                            "        unless the caller's number is verified on the account,\n"
                            "        so nobody else can ring this number until you upgrade."
                        )
                    )
            except CommandError as exc:
                ok = line(False, "Credentials rejected by Twilio", str(exc)[:160])

        self.stdout.write("\nPhone number")
        number = os.environ.get("TWILIO_PHONE_NUMBER", "")
        if not re.fullmatch(r"\+[1-9]\d{6,14}", number or ""):
            ok = line(False, "TWILIO_PHONE_NUMBER", "Must be E.164, like +15551234567.")
        elif sid and token:
            try:
                owned = twilio(
                    f"https://api.twilio.com/2010-04-01/Accounts/{sid}"
                    f"/IncomingPhoneNumbers.json?PhoneNumber={urllib.parse.quote(number, safe='')}"
                )
                found = (owned.get("incoming_phone_numbers") or [None])[0]
                if not found:
                    ok = line(False, f"{number} is not on this account",
                              "Buy it in the Twilio console first.")
                elif found.get("trunk_sid"):
                    line(True, f"{number} is on this account",
                         f"already attached to trunk {found['trunk_sid']}")
                else:
                    line(True, f"{number} is on this account", "not yet attached to a trunk")
            except CommandError as exc:
                ok = line(False, "Could not list numbers", str(exc)[:160])

        self.stdout.write("\nSIP trunk domain")
        domain = os.environ.get("TWILIO_TRUNK_DOMAIN", "")
        if not domain:
            ok = line(False, "TWILIO_TRUNK_DOMAIN", "Not set. Pick one ending .pstn.twilio.com.")
        elif not domain.endswith(".pstn.twilio.com"):
            ok = line(False, "TWILIO_TRUNK_DOMAIN", f"Must end .pstn.twilio.com (got {domain}).")
        else:
            line(True, domain)

        self.stdout.write("\nAgent and webhooks")
        profile = AgentProfile.load()
        if not profile.agent_id:
            ok = line(False, "No agent published", "Run: python manage.py publish_agent")
        else:
            line(True, f"Agent {profile.agent_id}", f"voice: {profile.voice_id}")

        if not profile.base_url:
            ok = line(
                False,
                "No public base URL",
                "A phone call has no browser to answer a tool, so the webhooks must be "
                "reachable from the internet. Publish with --public-url.",
            )
        else:
            reachable = False
            try:
                with request.urlopen(profile.base_url + "/healthz/", timeout=8) as res:
                    reachable = res.status == 200
            except Exception:
                reachable = False
            ok = line(reachable, f"Webhooks at {profile.base_url}",
                      "" if reachable else "That URL did not answer. Is the tunnel or deploy up?") and ok
            if "trycloudflare.com" in profile.base_url:
                self.stdout.write(
                    self.style.WARNING(
                        "  warn  That is a quick tunnel, and its hostname dies with the\n"
                        "        process. A phone number pointed at it stops filing claims\n"
                        "        the moment it goes down. Deploy before you hand the number out."
                    )
                )

        self.stdout.write("")
        if ok:
            self.stdout.write(self.style.SUCCESS("Ready. Run: python manage.py connect_phone"))
        else:
            self.stdout.write(self.style.ERROR("Not ready yet — fix the failures above."))
