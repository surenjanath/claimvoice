"""End-to-end coverage of the paths a live demo depends on."""

import json
import re
from datetime import datetime, timedelta
from io import StringIO
from unittest import mock

from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from django.core.files.uploadedfile import SimpleUploadedFile

from . import handoff as handoff_module
from . import roster
from .models import (
    AgentProfile,
    AgentRevision,
    Claim,
    ClaimNote,
    ClaimPhoto,
    ClaimStatus,
    Conversation,
    DeskAction,
    Dispatch,
    Dispatcher,
    Handoff,
    HandoffAttempt,
    Policyholder,
    Shift,
    VendorCall,
    VerificationAttempt,
)
from .risk import score_claim
from .views import parse_claim, PayloadError


class RiskScoreTests(TestCase):
    def test_undrivable_highway_collision_with_injuries_is_critical(self):
        score, factors, tow = score_claim(
            incident_type="collision",
            is_drivable=False,
            location="I-95 northbound near exit 12",
            description="Airbags deployed, smoke from the engine",
            injuries_reported=True,
        )
        self.assertGreaterEqual(score, 75)
        self.assertTrue(tow)
        self.assertIn("Injuries reported", factors)

    def test_minor_drivable_bump_is_low(self):
        score, _, tow = score_claim(
            incident_type="collision",
            is_drivable=True,
            location="Mall parking lot",
            description="Small dent in the tailgate",
            injuries_reported=False,
        )
        self.assertLess(score, 50)
        self.assertFalse(tow)

    def test_theft_never_dispatches_a_tow(self):
        _, _, tow = score_claim(
            incident_type="theft", is_drivable=False, location="Garage level 3"
        )
        self.assertFalse(tow)

    def test_reported_severity_raises_the_score(self):
        base, _, _ = score_claim(
            incident_type="collision", is_drivable=True, location="Elm Street"
        )
        worse, factors, _ = score_claim(
            incident_type="collision",
            is_drivable=True,
            location="Elm Street",
            severity="fire_or_smoke",
        )
        self.assertGreater(worse, base)
        self.assertIn("Fire or smoke reported", factors)

    def test_score_stays_in_range(self):
        score, _, _ = score_claim(
            incident_type="collision",
            is_drivable=False,
            location="Interstate 5 multi vehicle intersection",
            description="rollover fire smoke airbag truck night ice snow flood water tree hail",
            injuries_reported=True,
        )
        self.assertLessEqual(score, 100)
        self.assertGreaterEqual(score, 1)


class PayloadParsingTests(TestCase):
    def test_accepts_the_documented_shape(self):
        fields = parse_claim(
            {
                "policy_number": "pv 482193",
                "incident_type": "collision",
                "location": "Main and 4th",
                "is_drivable": False,
            }
        )
        self.assertEqual(fields["policy_number"], "PV482193")
        self.assertFalse(fields["is_drivable"])
        self.assertIsNone(fields["injuries_reported"])

    def test_keeps_vehicle_and_a_known_severity(self):
        fields = parse_claim(
            {
                "policy_number": "PV1",
                "incident_type": "collision",
                "location": "Elm Street",
                "is_drivable": True,
                "vehicle": "blue Honda Civic",
                "severity": "airbags_deployed",
            }
        )
        self.assertEqual(fields["vehicle"], "blue Honda Civic")
        self.assertEqual(fields["severity"], "airbags_deployed")

    def test_drops_a_severity_outside_the_enum(self):
        fields = parse_claim(
            {
                "policy_number": "PV1",
                "incident_type": "collision",
                "location": "Elm Street",
                "is_drivable": True,
                "severity": "completely wrecked",
            }
        )
        self.assertEqual(fields["severity"], "")

    def test_writes_a_summary_when_the_agent_sends_none(self):
        # The agent cannot compose prose without breaking the tool call, so the
        # readable line has to be built here.
        fields = parse_claim(
            {
                "policy_number": "PV1",
                "incident_type": "collision",
                "location": "Elm Street",
                "is_drivable": False,
                "injuries_reported": False,
                "vehicle": "blue Honda Civic",
                "severity": "airbags_deployed",
            }
        )
        self.assertEqual(
            fields["description"],
            "Collision at Elm Street. Vehicle: blue Honda Civic. Airbags deployed. "
            "Not drivable. No injuries.",
        )

    def test_a_supplied_description_is_kept(self):
        fields = parse_claim(
            {
                "policy_number": "PV1",
                "incident_type": "theft",
                "location": "Garage",
                "is_drivable": False,
                "description": "Gone when they came back.",
            }
        )
        self.assertEqual(fields["description"], "Gone when they came back.")

    def test_accepts_wrapped_arguments_and_spoken_booleans(self):
        fields = parse_claim(
            {
                "arguments": {
                    "policy_number": "PV771004",
                    "incident_type": "hail storm damage",
                    "location": "Driveway",
                    "is_drivable": "yes",
                    "injuries_reported": "no",
                }
            }
        )
        self.assertEqual(fields["incident_type"], "weather")
        self.assertTrue(fields["is_drivable"])
        self.assertFalse(fields["injuries_reported"])

    def test_rejects_missing_fields(self):
        with self.assertRaises(PayloadError):
            parse_claim({"incident_type": "collision", "location": "x", "is_drivable": True})
        with self.assertRaises(PayloadError):
            parse_claim({"policy_number": "PV1", "location": "x", "is_drivable": True})

    def test_rejects_an_uncategorisable_incident(self):
        with self.assertRaises(PayloadError):
            parse_claim(
                {
                    "policy_number": "PV1",
                    "incident_type": "abducted by aliens",
                    "location": "x",
                    "is_drivable": True,
                }
            )


class WebhookTests(TestCase):
    url = "/api/log-claim/"

    def post(self, payload, **extra):
        return self.client.post(
            self.url, data=json.dumps(payload), content_type="application/json", **extra
        )

    def test_logs_a_claim_and_returns_a_line_to_read_back(self):
        response = self.post(
            {
                "policy_number": "PV482193",
                "incident_type": "collision",
                "location": "I-95 northbound near exit 12",
                "is_drivable": False,
                "injuries_reported": False,
                "description": "Rear-ended on the highway",
            }
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        claim = Claim.objects.get()
        self.assertEqual(body["claim_reference"], f"CV-{claim.id:05d}")
        self.assertIn("tow truck", body["message"].lower())
        self.assertIn(claim.location, body["message"])
        self.assertEqual(claim.status, ClaimStatus.DISPATCHED)
        self.assertGreater(claim.risk_score, 0)
        self.assertEqual(claim.raw_payload["policy_number"], "PV482193")

    def test_drivable_claim_is_not_towed(self):
        body = self.post(
            {
                "policy_number": "PV100200",
                "incident_type": "collision",
                "location": "Elm Street",
                "is_drivable": True,
            }
        ).json()
        self.assertIn("no tow is needed", body["message"].lower())
        self.assertEqual(Claim.objects.get().status, ClaimStatus.NEW)

    def test_theft_gets_an_investigator_not_a_tow(self):
        body = self.post(
            {
                "policy_number": "PV300400",
                "incident_type": "theft",
                "location": "Westfield garage",
                "is_drivable": False,
            }
        ).json()
        self.assertIn("investigator", body["message"].lower())
        kinds = set(Dispatch.objects.values_list("kind", flat=True))
        self.assertIn(Dispatch.Kind.INVESTIGATOR, kinds)
        self.assertNotIn(Dispatch.Kind.TOW, kinds)

    def test_a_repeat_tool_call_replays_the_first_claim(self):
        payload = {
            "policy_number": "PV482193",
            "incident_type": "collision",
            "location": "I-95 northbound near exit 12",
            "is_drivable": False,
        }
        first = self.post(payload).json()
        second = self.post(payload).json()
        self.assertEqual(Claim.objects.count(), 1)
        self.assertTrue(second["duplicate"])
        self.assertEqual(second["claim_reference"], first["claim_reference"])
        # Same claim, same tow ETA: the caller is told one arrival time.
        self.assertEqual(second["message"], first["message"])

    def test_a_seeded_fixture_does_not_swallow_a_real_claim(self):
        payload = {
            "policy_number": "PV482193",
            "incident_type": "collision",
            "location": "I-95 northbound",
            "is_drivable": False,
        }
        Claim.objects.create(
            policy_number="PV482193",
            incident_type="collision",
            location="Somewhere else",
            is_drivable=True,
            source="seed",
        )
        body = self.post(payload).json()
        self.assertNotIn("duplicate", body)
        self.assertEqual(Claim.objects.exclude(source="seed").count(), 1)
        self.assertEqual(body["claim"]["location"], "I-95 northbound")

    def test_a_different_incident_on_the_same_policy_is_a_new_claim(self):
        base = {
            "policy_number": "PV482193",
            "location": "I-95 northbound",
            "is_drivable": False,
        }
        self.post({**base, "incident_type": "collision"})
        self.post({**base, "incident_type": "theft"})
        self.assertEqual(Claim.objects.count(), 2)

    def test_an_old_claim_does_not_swallow_a_new_one(self):
        payload = {
            "policy_number": "PV482193",
            "incident_type": "collision",
            "location": "I-95 northbound",
            "is_drivable": False,
        }
        self.post(payload)
        Claim.objects.update(created_at=timezone.now() - timedelta(hours=2))
        self.post(payload)
        self.assertEqual(Claim.objects.count(), 2)

    def test_bad_payload_answers_200_with_something_speakable(self):
        response = self.post({"incident_type": "collision"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["ok"])
        self.assertIn("policy_number", body["error"])
        self.assertEqual(body["missing"], "policy_number")
        # What Ivy says must not contain a field name or an error string.
        self.assertNotIn("policy_number", body["message"])
        self.assertIn("their policy number", body["instructions"])
        self.assertEqual(Claim.objects.count(), 0)

    def test_not_asked_is_rejected_and_sends_the_agent_back_to_ask(self):
        body = self.post(
            {
                "policy_number": "PV482193",
                "incident_type": "collision",
                "location": "I-95",
                "is_drivable": "not_asked",
            }
        ).json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["missing"], "is_drivable")
        self.assertIn("ask them straight out", body["instructions"])
        self.assertEqual(Claim.objects.count(), 0)

    def test_the_enum_answers_map_onto_the_boolean(self):
        for answer, expected in (("yes", True), ("no", False)):
            Claim.objects.all().delete()
            self.post(
                {
                    "policy_number": f"PV00{expected}",
                    "incident_type": "collision",
                    "location": "I-95",
                    "is_drivable": answer,
                }
            )
            self.assertEqual(Claim.objects.get().is_drivable, expected)

    def test_a_null_drivable_is_rejected_with_the_question_to_ask(self):
        # The agent sometimes files without asking; nulling the field through
        # is how a claim gets a tow decision nobody made.
        body = self.post(
            {
                "policy_number": "PV482193",
                "incident_type": "collision",
                "location": "I-95",
                "is_drivable": None,
            }
        ).json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["missing"], "is_drivable")
        self.assertIn("whether the vehicle can still be driven", body["instructions"])
        self.assertEqual(Claim.objects.count(), 0)

    def test_malformed_json_does_not_500(self):
        response = self.client.post(
            self.url, data="not json", content_type="application/json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["ok"])

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_secret_is_enforced_when_configured(self):
        with self.settings(CLAIM_WEBHOOK_SECRET="s3cret"):
            payload = {
                "policy_number": "PV900100",
                "incident_type": "weather",
                "location": "Driveway",
                "is_drivable": True,
            }
            self.assertEqual(self.post(payload).status_code, 403)
            ok = self.post(payload, headers={"x-claim-secret": "s3cret"})
            self.assertEqual(ok.status_code, 200)


class FeedTests(TestCase):
    def make(self, **kwargs):
        defaults = {
            "policy_number": "PV000001",
            "incident_type": "collision",
            "location": "Somewhere",
            "is_drivable": True,
            "risk_score": 30,
        }
        return Claim.objects.create(**{**defaults, **kwargs})

    def test_feed_returns_claims_and_stats(self):
        self.make()
        self.make(risk_score=90, tow_required=True, is_drivable=False)
        body = self.client.get(reverse("claim-feed")).json()
        self.assertEqual(len(body["claims"]), 2)
        self.assertEqual(body["stats"]["total"], 2)
        self.assertEqual(body["stats"]["critical"], 1)
        self.assertEqual(body["stats"]["tows"], 1)
        self.assertEqual(body["stats"]["avg_risk"], 60)
        self.assertFalse(body["incremental"])

    def test_today_excludes_yesterdays_claims(self):
        self.make()
        old = self.make(policy_number="PV000099")
        Claim.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=2))
        body = self.client.get(reverse("claim-feed")).json()
        self.assertEqual(body["stats"]["today"], 1)
        self.assertEqual(body["stats"]["total"], 2)

    def test_since_returns_only_newer_claims(self):
        first = self.make()
        second = self.make(policy_number="PV000002")
        body = self.client.get(reverse("claim-feed"), {"since": first.id}).json()
        self.assertTrue(body["incremental"])
        self.assertEqual([c["id"] for c in body["claims"]], [second.id])
        self.assertEqual(body["latest_id"], second.id)

    def test_status_can_be_advanced(self):
        claim = self.make()
        response = self.client.post(
            reverse("claim-status", args=[claim.id]),
            data=json.dumps({"status": "closed"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        claim.refresh_from_db()
        self.assertEqual(claim.status, ClaimStatus.CLOSED)

    def test_unknown_status_is_rejected(self):
        claim = self.make()
        response = self.client.post(
            reverse("claim-status", args=[claim.id]),
            data=json.dumps({"status": "on fire"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)


class PageTests(TestCase):
    def test_voice_page_renders(self):
        response = self.client.get(reverse("voice"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Start call")
        self.assertContains(response, "File a first notice of loss")
        self.assertContains(response, "PV482193")
        self.assertContains(response, 'id="intake"')
        self.assertContains(response, 'id="call-recap"')

    def test_dashboard_renders(self):
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dispatcher board")
        self.assertContains(response, 'data-view="dispatches"')
        self.assertContains(response, "/api/dispatches/")

    def test_health(self):
        self.assertTrue(self.client.get(reverse("health")).json()["ok"])


class AgentProfileTests(TestCase):
    def profile(self, **kwargs):
        profile = AgentProfile.load()
        for key, value in kwargs.items():
            setattr(profile, key, value)
        profile.save()
        return profile

    def test_seeds_itself_from_agent_json(self):
        profile = AgentProfile.load()
        self.assertEqual(profile.name, "ClaimVoice FNOL Intake")
        self.assertIn("Ivy", profile.system_prompt)
        self.assertEqual(AgentProfile.objects.count(), 1)
        # A second load reuses the row rather than seeding another.
        AgentProfile.load()
        self.assertEqual(AgentProfile.objects.count(), 1)

    def test_public_url_publishes_every_tool_at_its_endpoint(self):
        profile = self.profile(public_base_url="https://claimvoice.example.com")
        urls = {
            tool["name"]: tool["http"]["url"] for tool in profile.to_agent_config()["tools"]
        }
        self.assertEqual(
            urls,
            {
                "verify_policyholder": "https://claimvoice.example.com/api/verify/",
                "log_claim": "https://claimvoice.example.com/api/log-claim/",
                "end_call": "https://claimvoice.example.com/api/end-call/",
                "request_human": "https://claimvoice.example.com/api/request-human/",
            },
        )

    def test_no_public_url_publishes_no_tools(self):
        # The API stores http tools only: a client-side one comes back with
        # http null and never fires, so shipping it would be a silent trap.
        profile = self.profile(public_base_url="")
        self.assertEqual(profile.to_agent_config()["tools"], [])

    def test_secret_is_attached_to_every_tool(self):
        profile = self.profile(public_base_url="https://x.example.com")
        with self.settings(CLAIM_WEBHOOK_SECRET="s3cret"):
            for tool in profile.to_agent_config()["tools"]:
                self.assertEqual(
                    tool["http"]["headers"],
                    [{"name": "X-Claim-Secret", "value": "s3cret"}],
                    tool["name"],
                )

    def test_tool_schema_asks_the_model_to_compose_nothing(self):
        """A property the model must write prose for stops the API firing the
        tool at all — silently. Every property must be copied or chosen."""
        authoring = re.compile(
            r"your own words|one sentence|in a sentence|summar|describe what happened|write ",
            re.I,
        )
        profile = self.profile(public_base_url="https://x.example.com")
        for tool in profile.to_agent_config()["tools"]:
            for name, schema in tool["parameters"]["properties"].items():
                if schema["type"] != "string" or "enum" in schema:
                    continue  # numbers, booleans and picked-from-a-list are safe
                self.assertIsNone(
                    authoring.search(schema["description"]),
                    f"{tool['name']}.{name} asks the model to compose text, "
                    "which kills the tool call",
                )

    def test_settings_page_renders_and_saves(self):
        response = self.client.get(reverse("settings"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Agent configuration")

        profile = AgentProfile.load()
        form = {
            "name": profile.name,
            "voice_id": "michael",
            "greeting": "Hello from the test.",
            "system_prompt": profile.system_prompt,
            "volume": 90,
            "vad_threshold": 0.5,
            "min_silence": 800,
            "max_silence": 2400,
            "interrupt_response": "on",
            "execution_mode": "hold",
            "timeout_seconds": 15,
            "public_base_url": "https://x.example.com",
            "keyterms_text": "policy number\ncollision",
            "action": "save",
        }
        response = self.client.post(reverse("settings"), form)
        self.assertEqual(response.status_code, 302)
        profile.refresh_from_db()
        self.assertEqual(profile.voice_id, "michael")
        self.assertEqual(profile.keyterms, ["policy number", "collision"])

    def test_settings_page_uses_side_nav_and_voice_cards(self):
        profile = AgentProfile.load()
        profile.public_base_url = "http://testserver"
        profile.published_digest = profile.config_digest()
        profile.agent_id = "agt_test"
        profile.save()

        response = self.client.get(reverse("settings"), SERVER_NAME="testserver")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-pane=\"identity\"")
        self.assertContains(response, "data-pane=\"prompt\"")
        self.assertContains(response, "Empathetic roadside")
        self.assertContains(response, "voice-card")
        self.assertContains(response, "Webhook reachable")
        self.assertContains(response, "Draft matches live")

    def test_saved_but_not_published_shows_on_settings(self):
        profile = AgentProfile.load()
        profile.agent_id = "agt_test"
        profile.published_digest = "not-the-current-digest"
        profile.save()
        response = self.client.get(reverse("settings"))
        self.assertContains(response, "Saved, not published")

    def test_invalid_silence_opens_listening_pane(self):
        profile = AgentProfile.load()
        response = self.client.post(
            reverse("settings"),
            {
                "name": profile.name,
                "voice_id": "anna",
                "greeting": "Hi.",
                "system_prompt": "x",
                "volume": 100,
                "vad_threshold": 0.5,
                "min_silence": 3000,
                "max_silence": 1000,
                "execution_mode": "hold",
                "timeout_seconds": 20,
                "public_base_url": "",
                "keyterms_text": "",
                "action": "save",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-error-pane="listening"')

    def test_min_silence_must_be_under_max(self):
        profile = AgentProfile.load()
        response = self.client.post(
            reverse("settings"),
            {
                "name": profile.name,
                "voice_id": "anna",
                "greeting": "Hi.",
                "system_prompt": "x",
                "volume": 100,
                "vad_threshold": 0.5,
                "min_silence": 3000,
                "max_silence": 1000,
                "execution_mode": "hold",
                "timeout_seconds": 20,
                "public_base_url": "",
                "keyterms_text": "",
                "action": "save",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "longer than min silence")


class VerificationTests(TestCase):
    url = "/api/verify/"

    def setUp(self):
        self.holder = Policyholder.objects.create(
            policy_number="PV482193",
            full_name="Dana Whitfield",
            phone="+15550142887",
            coverage="comprehensive",
            deductible=500,
            vehicles=[
                {"year": 2019, "make": "Toyota", "model": "Camry", "colour": "silver"}
            ],
        )

    def post(self, payload, **extra):
        return self.client.post(
            self.url, data=json.dumps(payload), content_type="application/json", **extra
        )

    def test_matching_last_four_verifies_and_hands_over_the_details(self):
        body = self.post(
            {"policy_number": "pv 482193", "phone_last4": "2887"}
        ).json()
        self.assertTrue(body["verified"])
        self.assertEqual(body["first_name"], "Dana")
        self.assertIn("Camry", body["vehicles"])
        self.assertIn("Dana", body["message"])
        self.assertTrue(VerificationAttempt.objects.get().passed)

    def test_it_binds_the_caller_to_the_conversation(self):
        self.post({"policy_number": "PV482193", "phone_last4": "2887"})
        conversation = Conversation.objects.get()
        self.assertTrue(conversation.verified)
        self.assertEqual(conversation.policyholder, self.holder)

    def test_a_wrong_last_four_reveals_nothing(self):
        body = self.post({"policy_number": "PV482193", "phone_last4": "0000"}).json()
        self.assertFalse(body["verified"])
        # Not the name, not the vehicle, not even that the policy is real.
        blob = json.dumps(body)
        self.assertNotIn("Dana", blob)
        self.assertNotIn("Camry", blob)
        self.assertFalse(VerificationAttempt.objects.get().passed)

    def test_an_unknown_policy_answers_exactly_like_a_wrong_digit(self):
        unknown = self.post({"policy_number": "PV000000", "phone_last4": "1234"}).json()
        wrong = self.post({"policy_number": "PV482193", "phone_last4": "1234"}).json()
        self.assertEqual(unknown["message"], wrong["message"])
        self.assertEqual(unknown["instructions"], wrong["instructions"])

    def test_guessing_runs_out_of_attempts(self):
        for _ in range(4):
            self.post({"policy_number": "PV482193", "phone_last4": "0000"})
        body = self.post({"policy_number": "PV482193", "phone_last4": "2887"}).json()
        # Even the right answer is refused once the window is spent.
        self.assertFalse(body["verified"])
        self.assertTrue(body["locked"])
        self.assertIn("1-800-555-0142", body["message"])

    def test_lockout_expires(self):
        for _ in range(4):
            self.post({"policy_number": "PV482193", "phone_last4": "0000"})
        VerificationAttempt.objects.update(
            created_at=timezone.now() - timedelta(hours=2)
        )
        body = self.post({"policy_number": "PV482193", "phone_last4": "2887"}).json()
        self.assertTrue(body["verified"])

    def test_missing_digits_ask_for_them(self):
        body = self.post({"policy_number": "PV482193"}).json()
        self.assertFalse(body["verified"])
        self.assertIn("last four digits", body["instructions"])

    def test_a_lapsed_policy_verifies_but_warns_the_agent(self):
        self.holder.status = "lapsed"
        self.holder.save()
        body = self.post({"policy_number": "PV482193", "phone_last4": "2887"}).json()
        self.assertTrue(body["verified"])
        self.assertIn("lapsed", body["instructions"])

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)


class ClaimLinkingTests(TestCase):
    def setUp(self):
        self.holder = Policyholder.objects.create(
            policy_number="PV482193",
            full_name="Dana Whitfield",
            phone="+15550142887",
            roadside_assistance=True,
        )

    def file(self, **overrides):
        payload = {
            "policy_number": "PV482193",
            "incident_type": "collision",
            "location": "I-95 northbound",
            "is_drivable": False,
            **overrides,
        }
        return self.client.post(
            "/api/log-claim/",
            data=json.dumps(payload),
            content_type="application/json",
        ).json()

    def test_a_claim_finds_its_policyholder(self):
        self.file()
        claim = Claim.objects.get()
        self.assertEqual(claim.policyholder, self.holder)
        self.assertEqual(claim.caller_name, "Dana Whitfield")
        self.assertIsNotNone(claim.conversation)

    def test_a_claim_for_an_unknown_policy_is_still_filed(self):
        # A driver at the roadside is not turned away because the book has no
        # matching row; the dispatcher sees it was unverified instead.
        body = self.file(policy_number="PV999999")
        self.assertTrue(body["ok"])
        claim = Claim.objects.get()
        self.assertIsNone(claim.policyholder)
        self.assertFalse(claim.as_dict()["verified"])

    def test_no_roadside_cover_changes_what_is_read_back(self):
        self.holder.roadside_assistance = False
        self.holder.save()
        body = self.file()
        self.assertIn("charged to the policyholder", body["message"])

    def test_the_tool_call_is_recorded_on_the_conversation(self):
        self.file()
        conversation = Conversation.objects.get()
        self.assertEqual(conversation.tool_calls[0]["name"], "log_claim")


class ConversationTests(TestCase):
    def ingest(self, **payload):
        return self.client.post(
            reverse("conversation-ingest"),
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_a_transcript_is_stored_and_updated_in_place(self):
        self.ingest(
            session_id="sess_abc",
            channel="browser",
            turns=[{"role": "agent", "text": "Are you safe?", "at": 1.0}],
        )
        self.ingest(
            session_id="sess_abc",
            channel="browser",
            turns=[
                {"role": "agent", "text": "Are you safe?", "at": 1.0},
                {"role": "caller", "text": "Yes.", "at": 4.2},
            ],
            ended=True,
            close_reason="client_end",
            duration_seconds=42.5,
        )
        conversation = Conversation.objects.get()
        self.assertEqual(len(conversation.turns), 2)
        self.assertEqual(conversation.close_reason, "client_end")
        self.assertEqual(conversation.duration_seconds, 42.5)
        self.assertIsNotNone(conversation.ended_at)

    def test_a_tool_call_row_is_folded_into_the_transcript(self):
        # AssemblyAI posts the webhook with no session id, so the tool call and
        # the transcript arrive as two rows for one call.
        Policyholder.objects.create(
            policy_number="PV482193", full_name="Dana Whitfield", phone="+15550142887"
        )
        self.client.post(
            "/api/verify/",
            data=json.dumps({"policy_number": "PV482193", "phone_last4": "2887"}),
            content_type="application/json",
        )
        self.assertEqual(Conversation.objects.count(), 1)

        self.ingest(
            session_id="sess_merge",
            channel="browser",
            turns=[{"role": "caller", "text": "Hello.", "at": 1.0}],
            duration_seconds=60,
        )
        self.assertEqual(Conversation.objects.count(), 1)
        conversation = Conversation.objects.get()
        self.assertEqual(conversation.session_id, "sess_merge")
        self.assertTrue(conversation.verified)
        self.assertEqual(conversation.policyholder.full_name, "Dana Whitfield")
        self.assertEqual(conversation.verifications.count(), 1)

    def test_a_tool_call_joins_the_call_that_is_open(self):
        """A webhook carries no session id, so it finds its call by that call
        being the one still running."""
        Policyholder.objects.create(
            policy_number="PV482193", full_name="Dana Whitfield", phone="+15550142887"
        )
        # An earlier call, already hung up.
        self.ingest(
            session_id="sess_old",
            turns=[{"role": "caller", "text": "Earlier call.", "at": 1.0}],
            ended=True,
            duration_seconds=60,
        )
        # The call happening now.
        self.ingest(session_id="sess_live", turns=[])

        self.client.post(
            "/api/verify/",
            data=json.dumps({"policy_number": "PV482193", "phone_last4": "2887"}),
            content_type="application/json",
        )
        live = Conversation.objects.get(session_id="sess_live")
        old = Conversation.objects.get(session_id="sess_old")
        self.assertTrue(live.verified)
        self.assertFalse(old.verified)
        self.assertEqual(Conversation.objects.count(), 2)

    def test_a_claim_lands_on_the_open_call_not_a_stale_row(self):
        Policyholder.objects.create(
            policy_number="PV482193", full_name="Dana Whitfield", phone="+15550142887"
        )
        self.ingest(
            session_id="sess_yesterday",
            turns=[],
            ended=True,
            duration_seconds=90,
        )
        self.ingest(session_id="sess_now", turns=[])
        self.client.post(
            "/api/log-claim/",
            data=json.dumps(
                {
                    "policy_number": "PV482193",
                    "incident_type": "collision",
                    "location": "I-95",
                    "is_drivable": "no",
                }
            ),
            content_type="application/json",
        )
        claim = Claim.objects.get()
        self.assertEqual(claim.conversation.session_id, "sess_now")

    def test_a_session_id_is_required(self):
        self.assertEqual(self.ingest(turns=[]).status_code, 400)

    def test_the_detail_view_returns_the_turns(self):
        self.ingest(
            session_id="sess_xyz",
            turns=[{"role": "caller", "text": "I was rear ended.", "at": 3.0}],
        )
        conversation = Conversation.objects.get()
        body = self.client.get(
            reverse("conversation-detail", args=[conversation.id])
        ).json()
        self.assertEqual(body["turns"][0]["text"], "I was rear ended.")
        self.assertEqual(body["turn_count"], 1)

    def test_the_list_summarises_without_the_transcript(self):
        self.ingest(
            session_id="sess_1",
            turns=[{"role": "caller", "text": "Someone hit me.", "at": 2.0}],
        )
        body = self.client.get(reverse("conversation-feed")).json()
        row = body["conversations"][0]
        self.assertEqual(row["summary"], "Someone hit me.")
        self.assertNotIn("turns", row)

    def test_lookup_by_session_reports_the_verified_caller(self):
        holder = Policyholder.objects.create(
            policy_number="PV482193", full_name="Dana Whitfield", phone="+15550142887"
        )
        self.client.post(
            "/api/verify/",
            data=json.dumps(
                {
                    "policy_number": "PV482193",
                    "phone_last4": "2887",
                    "session_id": "sess_live",
                }
            ),
            content_type="application/json",
        )
        body = self.client.get(
            reverse("conversation-session"), {"session": "sess_live"}
        ).json()
        self.assertTrue(body["verified"])
        self.assertEqual(body["policyholder"]["full_name"], holder.full_name)

    def test_an_unknown_session_is_not_verified(self):
        body = self.client.get(
            reverse("conversation-session"), {"session": "nope"}
        ).json()
        self.assertFalse(body["verified"])
        self.assertIsNone(body["policyholder"])


class DirectoryTests(TestCase):
    def setUp(self):
        Policyholder.objects.create(
            policy_number="PV482193", full_name="Dana Whitfield", phone="+15550142887"
        )

    def test_the_directory_shows_the_digits_a_tester_needs(self):
        body = self.client.get(reverse("policyholder-feed")).json()
        self.assertEqual(body["policyholders"][0]["phone_last4"], "2887")

    def test_search_narrows_the_book(self):
        Policyholder.objects.create(
            policy_number="PV771004", full_name="Marcus Oyelaran", phone="+15550118432"
        )
        body = self.client.get(reverse("policyholder-feed"), {"q": "marcus"}).json()
        self.assertEqual(len(body["policyholders"]), 1)

    def test_the_page_renders(self):
        response = self.client.get(reverse("directory"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Policy directory")
        self.assertContains(response, "On the books")
        self.assertContains(response, "Liability only")

    def test_the_feed_includes_book_totals(self):
        body = self.client.get(reverse("policyholder-feed")).json()
        self.assertEqual(body["total"], 1)
        self.assertIn("calls", body["policyholders"][0])


class InsightsMetricsTests(TestCase):
    def test_metrics_includes_the_detailed_series(self):
        holder = Policyholder.objects.create(
            policy_number="PV482193", full_name="Dana Whitfield", phone="+15550142887"
        )
        call = Conversation.objects.create(
            session_id="sess_insight",
            verified=True,
            policyholder=holder,
            duration_seconds=95,
            end_reason="claim_filed",
            channel="browser",
            tool_calls=[{"name": "log_claim", "rejected": True, "missing": "is_drivable"}],
        )
        Claim.objects.create(
            policy_number="PV482193",
            incident_type="collision",
            location="I-95 northbound",
            is_drivable=False,
            conversation=call,
            risk_score=60,
            tow_required=True,
        )

        body = self.client.get(reverse("metrics"), {"days": "30"}).json()
        self.assertEqual(body["headline"]["calls"], 1)
        self.assertEqual(body["headline"]["p90_seconds"], 95)
        self.assertEqual(body["headline"]["tows"], 1)
        self.assertEqual(sum(row["count"] for row in body["durations"]), 1)
        self.assertEqual(len(body["weekdays"]), 7)
        self.assertEqual(body["locations"][0]["label"], "I-95 northbound")
        self.assertTrue(body["notable"])
        self.assertIn("Needed a second filing", body["notable"][0]["flags"])
        self.assertEqual(body["funnel"][1]["from_previous"], 100)

    def test_metrics_reports_who_answered_the_phone(self):
        priya = Dispatcher.objects.create(
            name="Priya", email="priya@example.com", phone="+15550142001", order=1
        )
        call = Conversation.objects.create(session_id="sess_handoff_metrics")
        handoff = Handoff.objects.create(conversation=call, caller_number="+15550190000")
        HandoffAttempt.objects.create(
            handoff=handoff,
            dispatcher=priya,
            outcome=HandoffAttempt.Outcome.NO_ANSWER,
        )
        answered_at = timezone.now()
        HandoffAttempt.objects.create(
            handoff=handoff,
            dispatcher=priya,
            outcome=HandoffAttempt.Outcome.ANSWERED,
            created_at=answered_at - timedelta(seconds=12),
        )
        handoff.status = Handoff.Status.DONE
        handoff.connected_at = answered_at
        handoff.save(update_fields=["status", "connected_at"])

        body = self.client.get(reverse("metrics"), {"days": "30"}).json()
        stats = body["dispatchers"]
        self.assertEqual(stats["handoffs"], 1)
        self.assertEqual(stats["answered"], 1)
        person = stats["people"][0]
        self.assertEqual(person["name"], "Priya")
        self.assertEqual(person["rung"], 2)
        self.assertEqual(person["answered"], 1)
        self.assertEqual(person["answer_rate"], 50)
        self.assertEqual(person["avg_answer_seconds"], 12)

    def test_export_honours_the_days_window(self):
        Conversation.objects.create(session_id="sess_old", duration_seconds=10)
        Conversation.objects.filter(session_id="sess_old").update(
            started_at=timezone.now() - timedelta(days=40)
        )
        Conversation.objects.create(session_id="sess_new", duration_seconds=10)
        response = self.client.get(reverse("export-calls"), {"days": "7"})
        self.assertEqual(response.status_code, 200)
        lines = [line for line in b"".join(response.streaming_content).decode().splitlines() if line]
        self.assertEqual(len(lines), 1)


class PageWiringTests(TestCase):
    """Every URL a page hands its JavaScript has to resolve.

    The dispatcher board keeps its routes in one `window.CLAIMVOICE` block with
    `__ID__` and `__TOKEN__` where the row goes. Nothing checks those against
    the URL conf, so moving a route — as the photo endpoints moved to being
    addressed by share token — leaves a page quietly 404ing on a panel nobody
    opens in a smoke test.
    """

    def setUp(self):
        self.claim = Claim.objects.create(
            policy_number="PV482193",
            incident_type="collision",
            location="I-95 northbound",
            is_drivable=False,
        )

    def test_every_route_the_pages_hand_their_javascript_resolves(self):
        from django.urls import Resolver404, resolve

        for page in ("dashboard", "directory", "insights", "voice"):
            html = self.client.get(reverse(page)).content.decode()
            block = re.search(r"window\.CLAIMVOICE = \{(.*?)\n  \};", html, re.S)
            self.assertIsNotNone(block, f"{page} has no CLAIMVOICE block")
            urls = re.findall(r'(\w+): "(/[^"]*)"', block.group(1))
            self.assertTrue(urls, f"{page} declares no routes")
            for name, url in urls:
                path = url.replace("__ID__", str(self.claim.id)).replace(
                    "__TOKEN__", self.claim.share_token
                )
                try:
                    resolve(path)
                except Resolver404:
                    self.fail(f"{page}: {name} points at {url}, which no route matches")

    def test_the_board_reaches_photos_through_the_claims_share_token(self):
        """The desk and the caller at the roadside go through one route."""
        html = self.client.get(reverse("dashboard")).content.decode()
        self.assertIn('photosUrl: "/api/share/__TOKEN__/photos/"', html)
        self.assertNotIn("/api/claims/__ID__/photos/", html)
        listing = self.client.get(f"/api/share/{self.claim.share_token}/photos/")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()["photos"], [])


class PulseTests(TestCase):
    """The heartbeat every open page polls in place of its own feed."""

    def setUp(self):
        self.claim = Claim.objects.create(
            policy_number="PV482193",
            incident_type="collision",
            location="I-95 northbound",
            is_drivable=False,
        )

    def test_counters_describe_what_is_stored(self):
        body = self.client.get(reverse("pulse")).json()
        self.assertEqual(body["claims"]["n"], 1)
        self.assertEqual(body["claims"]["last"], self.claim.id)
        self.assertEqual(body["claims"]["handoffs"], 0)
        self.assertEqual(body["notes"], {"n": 0, "last": 0})

    def test_a_note_moves_only_its_own_counter(self):
        """What the board's incremental fetch rests on: a note changes a claim
        row it already holds, and the claim counters cannot show that."""
        before = self.client.get(reverse("pulse")).json()
        ClaimNote.objects.create(claim=self.claim, body="Caller called back")
        after = self.client.get(reverse("pulse")).json()
        self.assertEqual(before["claims"], after["claims"])
        self.assertNotEqual(before["notes"], after["notes"])

    def test_a_handoff_moves_the_claim_counters(self):
        before = self.client.get(reverse("pulse")).json()
        self.claim.needs_human = True
        self.claim.save(update_fields=["needs_human"])
        after = self.client.get(reverse("pulse")).json()
        self.assertNotEqual(before["claims"], after["claims"])
        self.assertEqual(after["claims"]["handoffs"], 1)

    def test_the_cost_does_not_grow_with_the_rows(self):
        """It is polled by every open tab, so it has to cost the same whatever
        is on the board. The number of aggregates is allowed to change; what it
        must not do is depend on how much there is to count."""
        with CaptureQueriesContext(connection) as quiet:
            self.client.get(reverse("pulse"))
        for index in range(20):
            claim = Claim.objects.create(
                policy_number=f"PV00{index}",
                incident_type="collision",
                location="I-95",
                is_drivable=True,
            )
            ClaimNote.objects.create(claim=claim, body="note")
        with CaptureQueriesContext(connection) as busy:
            self.client.get(reverse("pulse"))
        self.assertEqual(len(busy), len(quiet))

    def test_the_counters_are_behind_the_desk_login(self):
        with self.settings(DESK_AUTH=True, DESK_PASSWORD="secret"):
            self.assertEqual(self.client.get(reverse("pulse")).status_code, 401)


class WebhookStatusTests(TestCase):
    def test_empty_url_is_unset(self):
        from .webhook_status import probe_webhook

        self.assertEqual(probe_webhook("", "testserver")["state"], "unset")

    def test_same_host_is_reachable_without_a_network_hop(self):
        from .webhook_status import probe_webhook

        self.assertEqual(
            probe_webhook("http://testserver", "testserver")["state"], "ok"
        )

    def test_invalid_url_is_bad(self):
        from .webhook_status import probe_webhook

        self.assertEqual(probe_webhook("not-a-url", "testserver")["state"], "bad")


class NotifyTests(TestCase):
    def setUp(self):
        self.holder = Policyholder.objects.create(
            policy_number="PV482193",
            full_name="Dana Whitfield",
            phone="+15550142887",
        )
        self.claim = Claim.objects.create(
            policy_number="PV482193",
            incident_type="collision",
            location="I-95 northbound",
            is_drivable=False,
            policyholder=self.holder,
            risk_score=60,
            tow_required=True,
        )

    def test_the_text_names_the_claim_and_the_tow(self):
        from .notify import claim_sms_body

        body = claim_sms_body(self.claim)
        self.assertIn("CV-00001", body)
        self.assertIn("I-95 northbound", body)
        self.assertIn("tow", body.lower())

    def test_no_credentials_skips_the_send(self):
        from .notify import notify_claim

        result = notify_claim(self.claim)
        self.assertFalse(result["sent"])
        self.assertEqual(result["skipped"], "unset")

    def test_a_successful_send_is_spoken_on_the_claim(self):
        from unittest.mock import patch

        with patch.dict(
            "os.environ",
            {
                "TWILIO_ACCOUNT_SID": "ACtest",
                "TWILIO_AUTH_TOKEN": "token",
                "TWILIO_PHONE_NUMBER": "+15550001111",
            },
        ), patch("claims.notify.send_sms", return_value={"sent": True, "sid": "SM1"}):
            body = self.client.post(
                "/api/log-claim/",
                data=json.dumps(
                    {
                        "policy_number": "PV482193",
                        "incident_type": "weather",
                        "location": "Driveway",
                        "is_drivable": True,
                    }
                ),
                content_type="application/json",
            ).json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["notified"])
        self.assertIn("texted", body["message"])


class DispatchFeedTests(TestCase):
    def test_the_feed_lists_open_tows(self):
        claim = Claim.objects.create(
            policy_number="PV482193",
            incident_type="collision",
            location="I-95",
            is_drivable=False,
            tow_required=True,
            risk_score=60,
        )
        Dispatch.objects.create(
            claim=claim,
            kind=Dispatch.Kind.TOW,
            vendor="Regional Towing (auto)",
            eta_minutes=22,
            status=Dispatch.Status.EN_ROUTE,
            raised_by="system",
        )
        body = self.client.get(reverse("dispatch-feed"), {"open": "1"}).json()
        self.assertEqual(len(body["dispatches"]), 1)
        self.assertEqual(body["dispatches"][0]["claim_reference"], f"CV-{claim.id:05d}")
        self.assertEqual(body["dispatches"][0]["location"], "I-95")


class ShareAndHandoffTests(TestCase):
    def setUp(self):
        self.holder = Policyholder.objects.create(
            policy_number="PV482193",
            full_name="Dana Whitfield",
            phone="+15550142887",
            status="active",
        )
        self.claim = Claim.objects.create(
            policy_number="PV482193",
            incident_type="collision",
            location="Southern Main Road, Chaguanas",
            is_drivable=False,
            tow_required=True,
            risk_score=80,
            policyholder=self.holder,
        )

    def test_the_share_page_opens_from_its_token(self):
        response = self.client.get(reverse("claim-share", args=[self.claim.share_token]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.claim.reference)

    def test_the_share_page_cannot_be_reached_by_counting(self):
        """The link is public and goes to a driver who cannot log in, so the
        identifier in it has to be the secret. An id or a CV- reference must
        not resolve, or one link would open every other claim."""
        for guess in (str(self.claim.id), self.claim.reference, f"cv-{self.claim.id}", "1"):
            response = self.client.get(f"/c/{guess}/")
            self.assertEqual(response.status_code, 404, guess)

    def test_a_photo_cannot_be_posted_to_a_claim_by_id(self):
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
        response = self.client.post(
            f"/api/share/{self.claim.id}/photos/upload/",
            {"photo": SimpleUploadedFile("x.png", png, content_type="image/png")},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(ClaimPhoto.objects.count(), 0)

    def test_a_photo_that_is_not_an_image_is_refused(self):
        response = self.client.post(
            reverse("claim-photo-upload", args=[self.claim.share_token]),
            {"photo": SimpleUploadedFile("x.png", b"#!/bin/sh\nrm -rf /", content_type="image/png")},
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(ClaimPhoto.objects.count(), 0)

    def test_one_claims_photo_is_not_readable_through_another_link(self):
        other = Claim.objects.create(
            policy_number="PV999999",
            incident_type="theft",
            location="Elsewhere",
            is_drivable=True,
        )
        png = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
            b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        self.client.post(
            reverse("claim-photo-upload", args=[self.claim.share_token]),
            {"photo": SimpleUploadedFile("scene.png", png, content_type="image/png")},
            HTTP_ACCEPT="application/json",
        )
        photo = ClaimPhoto.objects.get()
        mine = self.client.get(
            reverse("claim-photo-file", args=[self.claim.share_token, photo.id])
        )
        self.assertEqual(mine.status_code, 200)
        theirs = self.client.get(
            reverse("claim-photo-file", args=[other.share_token, photo.id])
        )
        self.assertEqual(theirs.status_code, 404)

    def test_a_photo_attaches_to_the_claim(self):
        png = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
            b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        response = self.client.post(
            reverse("claim-photo-upload", args=[self.claim.share_token]),
            {"photo": SimpleUploadedFile("scene.png", png, content_type="image/png"), "caption": "Bumper"},
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertEqual(ClaimPhoto.objects.filter(claim=self.claim).count(), 1)
        photo = ClaimPhoto.objects.get()
        file_res = self.client.get(
            reverse("claim-photo-file", args=[self.claim.share_token, photo.id])
        )
        self.assertEqual(file_res.status_code, 200)

    def test_ivy_can_request_a_human(self):
        conversation = Conversation.objects.create(session_id="sess-handoff")
        self.claim.conversation = conversation
        self.claim.save(update_fields=["conversation"])
        body = self.client.post(
            reverse("request-human"),
            data=json.dumps({"session_id": "sess-handoff", "reason": "cannot_verify"}),
            content_type="application/json",
        ).json()
        self.assertTrue(body["ok"])
        conversation.refresh_from_db()
        self.claim.refresh_from_db()
        self.assertTrue(conversation.needs_human)
        self.assertTrue(self.claim.needs_human)
        self.assertEqual(self.claim.handoff_reason, "cannot_verify")
        self.assertIn("handoff", self.claim.flags())

    def test_the_dispatcher_can_flag_a_handoff(self):
        body = self.client.post(
            reverse("claim-handoff", args=[self.claim.id]),
            data=json.dumps({"reason": "dispatcher"}),
            content_type="application/json",
        ).json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["claim"]["needs_human"])

    def test_a_publish_snapshot_can_be_restored(self):
        profile = AgentProfile.load()
        profile.system_prompt = "Live prompt"
        profile.greeting = "Hi, this is Ivy."
        profile.voice_id = "anna"
        profile.save()
        revision = profile.snapshot_revision()
        profile.system_prompt = "Draft that went wrong"
        profile.greeting = "Changed greeting"
        profile.save()
        response = self.client.post(
            reverse("restore-revision", args=[revision.id]),
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 200)
        profile.refresh_from_db()
        self.assertEqual(profile.system_prompt, "Live prompt")
        self.assertEqual(profile.greeting, "Hi, this is Ivy.")
        self.assertEqual(AgentRevision.objects.count(), 1)

    def test_the_sms_includes_the_share_link_when_a_public_url_exists(self):
        from unittest.mock import patch

        from .notify import claim_sms_body

        with patch.dict("os.environ", {"PUBLIC_BASE_URL": "https://claimvoice.example.com"}):
            body = claim_sms_body(self.claim)
        self.assertIn(f"https://claimvoice.example.com/c/{self.claim.share_token}/", body)

    def test_geocode_pins_a_claim_when_nominatim_answers(self):
        from unittest.mock import patch

        from .geo import pin_claim

        with patch("claims.geo.geocode", return_value=(10.5, -61.4)):
            with self.settings(GEOCODE_CLAIMS=True):
                pin_claim(self.claim)
        self.claim.refresh_from_db()
        self.assertEqual(self.claim.lat, 10.5)
        self.assertEqual(self.claim.lng, -61.4)

    def test_geocode_retries_the_place_name_when_the_full_line_misses(self):
        from unittest.mock import patch

        from .geo import geocode

        def lookup(query):
            return (10.5, -61.4) if query == "Chaguanas" else None

        with patch("claims.geo._lookup", side_effect=lookup):
            self.assertEqual(geocode("Southern Main Road in Chaguanas"), (10.5, -61.4))


class AutoDispatchAndSmsTests(TestCase):
    def test_injuries_raise_a_medical_dispatch(self):
        body = self.client.post(
            "/api/log-claim/",
            data=json.dumps(
                {
                    "policy_number": "PV482193",
                    "incident_type": "collision",
                    "location": "Main Street",
                    "is_drivable": True,
                    "injuries_reported": True,
                }
            ),
            content_type="application/json",
        ).json()
        self.assertTrue(body["ok"])
        self.assertTrue(
            Dispatch.objects.filter(kind=Dispatch.Kind.AMBULANCE).exists()
        )

    def test_rental_cover_raises_a_replacement_vehicle(self):
        Policyholder.objects.create(
            policy_number="PV482193",
            full_name="Dana Whitfield",
            phone="+15550142887",
            rental_cover=True,
        )
        self.client.post(
            "/api/log-claim/",
            data=json.dumps(
                {
                    "policy_number": "PV482193",
                    "incident_type": "collision",
                    "location": "I-95",
                    "is_drivable": False,
                }
            ),
            content_type="application/json",
        )
        kinds = set(Dispatch.objects.values_list("kind", flat=True))
        self.assertIn(Dispatch.Kind.TOW, kinds)
        self.assertIn(Dispatch.Kind.RENTAL, kinds)

    def test_a_skipped_text_is_recorded_on_the_claim(self):
        claim = Claim.objects.create(
            policy_number="PV1",
            incident_type="collision",
            location="Elm",
            is_drivable=True,
        )
        from .notify import notify_claim

        result = notify_claim(claim)
        claim.refresh_from_db()
        self.assertFalse(result["sent"])
        self.assertEqual(claim.sms_status, "unset")

    def test_resend_updates_the_claim(self):
        from unittest.mock import patch

        holder = Policyholder.objects.create(
            policy_number="PV482193",
            full_name="Dana Whitfield",
            phone="+15550142887",
        )
        claim = Claim.objects.create(
            policy_number="PV482193",
            incident_type="collision",
            location="Elm",
            is_drivable=True,
            policyholder=holder,
        )
        with patch(
            "claims.notify.send_sms", return_value={"sent": True, "sid": "SM9"}
        ), patch.dict(
            "os.environ",
            {
                "TWILIO_ACCOUNT_SID": "ACtest",
                "TWILIO_AUTH_TOKEN": "token",
                "TWILIO_PHONE_NUMBER": "+15550001111",
            },
        ):
            body = self.client.post(reverse("claim-notify", args=[claim.id])).json()
        self.assertTrue(body["sent"])
        claim.refresh_from_db()
        self.assertEqual(claim.sms_status, "sent")
        self.assertIsNotNone(claim.sms_sent_at)

    def test_the_live_feed_lists_dispatches(self):
        claim = Claim.objects.create(
            policy_number="PV1",
            incident_type="theft",
            location="Garage",
            is_drivable=True,
        )
        Dispatch.objects.create(
            claim=claim,
            kind=Dispatch.Kind.INVESTIGATOR,
            status=Dispatch.Status.REQUESTED,
        )
        body = self.client.get(reverse("claim-live", args=[claim.share_token])).json()
        self.assertEqual(len(body["dispatches"]), 1)
        self.assertEqual(body["dispatches"][0]["kind"], "investigator")

    def test_session_lookup_counts_remaining_attempts(self):
        Conversation.objects.create(session_id="sess-tries")
        body = self.client.get(
            reverse("conversation-session"), {"session": "sess-tries"}
        ).json()
        self.assertFalse(body["verified"])
        self.assertEqual(body["attempts_remaining"], 4)
        self.assertFalse(body["locked"])


class DeskReadyTests(TestCase):
    def setUp(self):
        self.claim = Claim.objects.create(
            policy_number="PV482193",
            incident_type="collision",
            location="Elm Street",
            is_drivable=True,
        )

    def test_a_dispatcher_can_take_and_release_a_claim(self):
        taken = self.client.post(
            reverse("claim-assign", args=[self.claim.id]),
            data=json.dumps({"assigned_to": "Alex"}),
            content_type="application/json",
        ).json()
        self.assertEqual(taken["claim"]["assigned_to"], "Alex")
        released = self.client.post(
            reverse("claim-assign", args=[self.claim.id]),
            data=json.dumps({"release": True}),
            content_type="application/json",
        ).json()
        self.assertEqual(released["claim"]["assigned_to"], "")

    def test_caller_and_dispatcher_can_leave_notes(self):
        from .models import ClaimNote

        body = self.client.post(
            reverse("claim-note", args=[self.claim.id]),
            data=json.dumps({"body": "On scene, waiting.", "author": "caller"}),
            content_type="application/json",
            HTTP_ACCEPT="application/json",
        ).json()
        self.assertTrue(body["ok"])
        self.assertEqual(ClaimNote.objects.get().author, "caller")
        live = self.client.get(reverse("claim-live", args=[self.claim.share_token])).json()
        self.assertEqual(live["notes"][0]["body"], "On scene, waiting.")


class VendorBookTests(TestCase):
    def test_location_picks_a_named_truck(self):
        from .vendors import pick_vendor

        trinidad = pick_vendor("tow", "Southern Main Road, Chaguanas", 1)
        self.assertEqual(trinidad["name"], "Southern Main Recovery")
        highway = pick_vendor("tow", "I-95 northbound", 1)
        self.assertEqual(highway["name"], "I-95 Rapid Tow")

    def test_automatic_dispatch_uses_the_book(self):
        body = self.client.post(
            "/api/log-claim/",
            data=json.dumps(
                {
                    "policy_number": "PV482193",
                    "incident_type": "collision",
                    "location": "Southern Main Road, Chaguanas",
                    "is_drivable": False,
                }
            ),
            content_type="application/json",
        ).json()
        self.assertTrue(body["ok"])
        row = Dispatch.objects.get(kind=Dispatch.Kind.TOW)
        self.assertEqual(row.vendor, "Southern Main Recovery")
        self.assertTrue(row.vendor_phone)


class NotifyEmailTests(TestCase):
    def test_email_sends_when_the_backend_is_in_memory(self):
        from django.core import mail

        from .notify import notify_claim

        holder = Policyholder.objects.create(
            policy_number="PV482193",
            full_name="Dana Whitfield",
            phone="+15550142887",
            email="dana.whitfield@example.com",
        )
        claim = Claim.objects.create(
            policy_number="PV482193",
            incident_type="collision",
            location="Elm",
            is_drivable=True,
            policyholder=holder,
        )
        with self.settings(
            EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
            EMAIL_HOST="",
        ):
            result = notify_claim(claim)
        self.assertTrue(result["email"]["sent"])
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(claim.reference, mail.outbox[0].subject)
        claim.refresh_from_db()
        self.assertEqual(claim.email_status, "sent")


class DeskAuthTests(TestCase):
    def test_the_desk_is_open_when_auth_is_off(self):
        self.assertEqual(self.client.get(reverse("directory")).status_code, 200)
        self.assertEqual(self.client.get(reverse("policyholder-feed")).status_code, 200)

    def test_login_locks_the_directory_and_keeps_voice_public(self):
        with self.settings(DESK_AUTH=True, DESK_PASSWORD="secret"):
            self.assertEqual(self.client.get(reverse("voice")).status_code, 200)
            self.assertEqual(self.client.get(reverse("directory")).status_code, 302)
            self.assertEqual(self.client.get(reverse("policyholder-feed")).status_code, 401)
            # The last four is what the agent verifies identity against, so a
            # locked desk does not hand it to an anonymous caller — not even
            # for one policy asked for by name.
            one = self.client.get(reverse("policyholder-feed"), {"policy": "PV482193"})
            self.assertEqual(one.status_code, 401)
            locked = self.client.post("/login/", {"password": "nope"})
            self.assertEqual(locked.status_code, 200)
            self.client.post("/login/", {"password": "secret", "next": "/directory/"})
            self.assertEqual(self.client.get(reverse("directory")).status_code, 200)
            self.assertEqual(self.client.get(reverse("policyholder-feed")).status_code, 200)


class DemoCredentialTests(TestCase):
    """The Talk-to-Ivy card can show a tester which digits to read out. That is
    the verification secret, so it is opt-in per deployment and never the book."""

    def setUp(self):
        Policyholder.objects.create(
            policy_number="PV482193",
            full_name="Dana Whitfield",
            phone="+15550142887",
            address="218 Larkspur Lane",
            email="dana@example.com",
        )

    def test_off_by_default_the_secret_stays_at_the_desk(self):
        with self.settings(DESK_AUTH=True, DESK_PASSWORD="s"):
            response = self.client.get(reverse("policyholder-feed"), {"policy": "PV482193"})
            self.assertEqual(response.status_code, 401)

    def test_demo_mode_shows_the_digits_but_not_the_rest(self):
        with self.settings(DESK_AUTH=True, DESK_PASSWORD="s", DEMO_CREDENTIALS=True):
            body = self.client.get(
                reverse("policyholder-feed"), {"policy": "PV482193"}
            ).json()
            holder = body["policyholders"][0]
            self.assertEqual(holder["phone_last4"], "2887")
            # The digits to read out, and nothing more.
            self.assertNotIn("phone", holder)
            self.assertEqual(holder["address"], "")
            self.assertEqual(holder["email"], "")

    def test_demo_mode_still_refuses_the_whole_book(self):
        with self.settings(DESK_AUTH=True, DESK_PASSWORD="s", DEMO_CREDENTIALS=True):
            self.assertEqual(
                self.client.get(reverse("policyholder-feed")).status_code, 401
            )


class SyncCallsTests(TestCase):
    def test_the_agent_page_can_sync_sessions(self):
        from datetime import datetime, timezone as dt_timezone
        from unittest.mock import patch

        profile = AgentProfile.load()
        session = {
            "id": "sess-phone-1",
            "agent_id": profile.agent_id,
            "created_at": "2026-09-07T12:00:00Z",
            "ended_at": "2026-09-07T12:04:00Z",
            "duration_seconds": 240,
            "public_close_reason": "user_hangup",
        }
        with patch("claims.sync.api", return_value={"sessions": [session]}):
            body = self.client.post(reverse("sync-calls")).json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["created"], 1)
        call = Conversation.objects.get(session_id="sess-phone-1")
        self.assertEqual(call.channel, Conversation.Channel.PHONE)
        self.assertEqual(call.duration_seconds, 240)
        self.assertEqual(call.close_reason, "user_hangup")
        self.assertEqual(call.ended_at, datetime(2026, 9, 7, 12, 4, tzinfo=dt_timezone.utc))


class NextWaveTests(TestCase):
    def test_the_feed_can_filter_by_policy(self):
        Claim.objects.create(
            policy_number="PV482193",
            incident_type="collision",
            location="Elm",
            is_drivable=True,
        )
        Claim.objects.create(
            policy_number="PV771004",
            incident_type="theft",
            location="Garage",
            is_drivable=True,
        )
        body = self.client.get(reverse("claim-feed"), {"policy": "PV482193"}).json()
        self.assertEqual(len(body["claims"]), 1)
        self.assertEqual(body["claims"][0]["policy_number"], "PV482193")

    def test_the_vendor_book_is_listed(self):
        body = self.client.get(reverse("vendor-feed")).json()
        names = {row["name"] for row in body["vendors"]}
        self.assertIn("Southern Main Recovery", names)
        self.assertIn("Metro EMS", names)
        tows = self.client.get(reverse("vendor-feed"), {"kind": "tow"}).json()
        self.assertTrue(all(row["kind"] == "tow" for row in tows["vendors"]))

    def test_live_feed_includes_notify_and_handoff(self):
        claim = Claim.objects.create(
            policy_number="PV1",
            incident_type="collision",
            location="Elm",
            is_drivable=False,
            tow_required=True,
            needs_human=True,
            handoff_reason="cannot_verify",
            email_status="sent",
            assigned_to="Alex",
        )
        body = self.client.get(reverse("claim-live", args=[claim.share_token])).json()
        self.assertTrue(body["needs_human"])
        self.assertEqual(body["handoff_reason"], "cannot_verify")
        self.assertEqual(body["email_status"], "sent")
        self.assertEqual(body["assigned_to"], "Alex")

    def test_an_open_tow_counts_down(self):
        claim = Claim.objects.create(
            policy_number="PV1",
            incident_type="collision",
            location="I-95",
            is_drivable=False,
            tow_required=True,
        )
        row = Dispatch.objects.create(
            claim=claim,
            kind=Dispatch.Kind.TOW,
            eta_minutes=20,
            status=Dispatch.Status.EN_ROUTE,
        )
        self.assertIsNotNone(row.remaining_minutes)
        self.assertLessEqual(row.remaining_minutes, 20)

    def test_login_stores_the_dispatcher_name(self):
        with self.settings(DESK_AUTH=True, DESK_PASSWORD="secret"):
            self.client.post(
                "/login/",
                {"password": "secret", "name": "Alex", "next": "/dashboard/"},
            )
            session = self.client.session
            self.assertTrue(session.get("desk"))
            self.assertEqual(session.get("desk_name"), "Alex")


class WaveThreeTests(TestCase):
    def test_a_wrong_vehicle_is_flagged(self):
        holder = Policyholder.objects.create(
            policy_number="PV660271",
            full_name="Tom Alvarez",
            phone="+15550127765",
            coverage="liability",
            roadside_assistance=False,
            vehicles=[{"year": 2017, "make": "Ford", "model": "F-150", "colour": "black"}],
        )
        claim = Claim.objects.create(
            policy_number="PV660271",
            incident_type="collision",
            location="Grand",
            is_drivable=False,
            vehicle="red Jeep Wrangler",
            policyholder=holder,
        )
        data = claim.as_dict()
        self.assertTrue(data["vehicle_mismatch"])
        self.assertIn("vehicle mismatch", data["flags"])
        self.assertEqual(data["coverage"], "liability")
        self.assertFalse(data["roadside_assistance"])

    def test_a_matching_vehicle_is_not_flagged(self):
        holder = Policyholder.objects.create(
            policy_number="PV482193",
            full_name="Dana Whitfield",
            phone="+15550142887",
            vehicles=[{"year": 2019, "make": "Toyota", "model": "Camry", "colour": "silver"}],
        )
        claim = Claim.objects.create(
            policy_number="PV482193",
            incident_type="collision",
            location="I-95",
            is_drivable=False,
            vehicle="silver Toyota Camry",
            policyholder=holder,
        )
        self.assertFalse(claim.vehicle_mismatch())

    def test_inject_demo_files_a_scored_claim(self):
        Policyholder.objects.create(
            policy_number="PV482193",
            full_name="Dana Whitfield",
            phone="+15550142887",
            roadside_assistance=True,
            vehicles=[{"year": 2019, "make": "Toyota", "model": "Camry", "colour": "silver"}],
        )
        body = self.client.post(reverse("demo-claim")).json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["claim"]["tow_required"])
        self.assertEqual(body["claim"]["source"], "demo")
        self.assertTrue(Dispatch.objects.filter(claim_id=body["claim"]["id"]).exists())

    def test_the_share_live_feed_includes_risk(self):
        claim = Claim.objects.create(
            policy_number="PV1",
            incident_type="collision",
            location="Elm",
            is_drivable=False,
            risk_score=70,
            risk_factors=["Not drivable", "Highway"],
        )
        body = self.client.get(reverse("claim-live", args=[claim.share_token])).json()
        self.assertEqual(body["risk_score"], 70)
        self.assertIn("Highway", body["risk_factors"])


class RedactionTests(TestCase):
    """The caller says the verification digits out loud. They are the secret the
    identity check rests on, so they are not kept — in text or in audio."""

    def setUp(self):
        self.holder = Policyholder.objects.create(
            policy_number="PV482193", full_name="Dana Whitfield", phone="+15550142887"
        )

    def turns(self):
        return [
            {"role": "agent", "text": "What is your policy number?", "at": 20.0},
            {"role": "caller", "text": "My policy number is P V 4 8 2 1 9 3.", "at": 21.0},
            {"role": "agent", "text": "And the last four digits of the phone number?", "at": 34.0},
            {"role": "caller", "text": "The last four digits are 2 8 8 7.", "at": 40.0},
            {"role": "caller", "text": "I'm on interstate 95 near exit 12.", "at": 55.0},
        ]

    def test_the_spoken_answer_is_masked_and_nothing_else_is(self):
        from claims.redact import redact_turns

        cleaned, spans = redact_turns(self.turns(), known_last4="2887")
        answer = cleaned[3]
        self.assertNotIn("2887", answer["text"])
        self.assertNotIn("2 8 8 7", answer["text"])
        self.assertIn("••••", answer["text"])
        self.assertTrue(answer["redacted"])
        # The policy number is an identifier the agent reads back anyway, and
        # the location is the claim. Neither is the secret.
        self.assertIn("4 8 2 1 9 3", cleaned[1]["text"])
        self.assertIn("interstate 95 near exit 12", cleaned[4]["text"])
        self.assertEqual(len(spans), 1)

    def test_the_span_covers_the_moment_it_was_said(self):
        from claims.redact import redact_turns

        _, spans = redact_turns(self.turns(), known_last4="2887")
        self.assertLess(spans[0]["start"], 40.0)
        self.assertGreater(spans[0]["end"], 40.0)

    def test_ingest_never_stores_the_digits(self):
        conversation = Conversation.objects.create(
            session_id="sess_red", policyholder=self.holder, verified=True
        )
        self.client.post(
            reverse("conversation-ingest"),
            data=json.dumps(
                {
                    "session_id": "sess_red",
                    "turns": self.turns(),
                    "tool_calls": [
                        {
                            "name": "verify_policyholder",
                            "arguments": {"policy_number": "PV482193", "phone_last4": "2887"},
                        }
                    ],
                }
            ),
            content_type="application/json",
        )
        conversation.refresh_from_db()
        blob = json.dumps(conversation.turns) + json.dumps(conversation.tool_calls)
        self.assertNotIn("2887", blob)
        self.assertNotIn("2 8 8 7", blob)
        self.assertEqual(len(conversation.redactions), 1)

    def test_a_tool_call_arriving_unmasked_is_masked(self):
        from claims.redact import redact_tool_calls

        out = redact_tool_calls(
            [{"name": "verify_policyholder", "arguments": {"phone_last4": "2887"}}]
        )
        self.assertEqual(out[0]["arguments"]["phone_last4"], "••••")

    def test_the_recording_is_blanked_on_the_caller_channel_only(self):
        import array
        import io
        import wave

        from claims.redact import blank_spans

        rate = 24000
        seconds = 6
        tone = array.array("h", [12000] * (rate * seconds * 2))  # both channels loud
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as sink:
            sink.setnchannels(2)
            sink.setsampwidth(2)
            sink.setframerate(rate)
            sink.writeframes(tone.tobytes())

        out = blank_spans(buffer.getvalue(), [{"start": 2.0, "end": 4.0}])
        with wave.open(io.BytesIO(out), "rb") as source:
            frames = array.array("h", source.readframes(source.getnframes()))
        left, right = frames[0::2], frames[1::2]

        inside = slice(int(2.2 * rate), int(3.8 * rate))
        self.assertEqual(max(abs(v) for v in left[inside]), 0)
        # Ivy's side is untouched: you can still hear her ask the question.
        self.assertEqual(max(abs(v) for v in right[inside]), 12000)
        # And the rest of the caller's channel survives.
        self.assertEqual(max(abs(v) for v in left[int(4.5 * rate) :]), 12000)

    def test_blanking_is_a_no_op_without_spans(self):
        from claims.redact import blank_spans

        self.assertEqual(blank_spans(b"not a wav", []), b"not a wav")


class VendorCallTests(TestCase):
    """Ivy takes the claim; this is the call that actually gets a truck moving."""

    def setUp(self):
        self.holder = Policyholder.objects.create(
            policy_number="PV482193", full_name="Dana Whitfield", phone="+15550142887"
        )
        self.claim = Claim.objects.create(
            policy_number="PV482193",
            incident_type="collision",
            location="I-95 northbound near exit 12",
            is_drivable=False,
            tow_required=True,
            policyholder=self.holder,
            lat=39.95,
            lng=-75.16,
        )

    def test_the_nearest_operator_is_called_first(self):
        from claims.vendor_calls import place_call

        call = place_call(self.claim)
        self.assertEqual(call.vendor_name, "I-95 Rapid Tow")
        self.assertLess(call.distance_km, 10)

    def test_without_outbound_the_call_is_recorded_not_dialled(self):
        from claims.vendor_calls import place_call

        call = place_call(self.claim)
        self.assertTrue(call.simulated)
        self.assertIn("OUTBOUND_CALLS is off", call.note)

    def test_accepting_sets_the_dispatch_and_tells_the_caller(self):
        from claims.vendor_calls import place_call, record_outcome

        call = place_call(self.claim)
        record_outcome(call, accepted=True, eta_minutes=25)

        dispatch = self.claim.dispatches.get()
        self.assertEqual(dispatch.vendor, "I-95 Rapid Tow")
        self.assertEqual(dispatch.eta_minutes, 25)
        self.assertEqual(dispatch.status, Dispatch.Status.EN_ROUTE)
        note = self.claim.notes.order_by("-created_at").first()
        self.assertIn("I-95 Rapid Tow", note.body)
        self.assertIn("25 minutes", note.body)

    def test_declining_moves_down_the_list_without_redialling(self):
        from claims.vendor_calls import place_call, record_outcome

        first = place_call(self.claim)
        record_outcome(first, accepted=False, reason="busy")

        second = self.claim.vendor_calls.order_by("-created_at").first()
        self.assertNotEqual(second.vendor_phone, first.vendor_phone)
        self.assertEqual(second.purpose, VendorCall.Purpose.REASSIGN)
        self.assertIn(second.vendor_name, self.claim.notes.first().body)

    def test_running_out_of_operators_hands_over_to_a_person(self):
        from claims.vendor_calls import place_call, record_outcome

        for _ in range(5):
            call = place_call(self.claim)
            if not call:
                break
            record_outcome(call, accepted=False, reason="busy")

        self.claim.refresh_from_db()
        self.assertTrue(self.claim.needs_human)
        self.assertEqual(self.claim.handoff_reason, "no_tow_available")

    def test_a_late_tow_is_chased_then_reassigned(self):
        from claims.vendor_calls import chase, overdue_calls, place_call, record_outcome

        call = place_call(self.claim)
        record_outcome(call, accepted=True, eta_minutes=25)
        dispatch = self.claim.dispatches.get()
        Dispatch.objects.filter(pk=dispatch.pk).update(
            updated_at=timezone.now() - timedelta(minutes=45)
        )
        dispatch.refresh_from_db()

        self.assertTrue(any(d.pk == dispatch.pk for d, _ in overdue_calls()))

        chased = chase(dispatch, 15)
        self.assertEqual(chased.purpose, VendorCall.Purpose.ETA_CHECK)
        self.assertEqual(chased.vendor_name, "I-95 Rapid Tow")

        self.claim.notes.update(created_at=timezone.now() - timedelta(minutes=30))
        moved = chase(dispatch, 25)
        self.assertEqual(moved.purpose, VendorCall.Purpose.REASSIGN)
        self.assertNotEqual(moved.vendor_name, "I-95 Rapid Tow")
        dispatch.refresh_from_db()
        self.assertEqual(dispatch.status, Dispatch.Status.CANCELLED)

    def test_chasing_does_not_use_up_an_escalation_attempt(self):
        """Ringing the operator already assigned is not another attempt at
        finding one, and counting it stranded callers who still had options."""
        from claims.vendor_calls import chase, place_call, record_outcome

        call = place_call(self.claim)
        record_outcome(call, accepted=True, eta_minutes=20)
        dispatch = self.claim.dispatches.get()
        Dispatch.objects.filter(pk=dispatch.pk).update(
            updated_at=timezone.now() - timedelta(minutes=60)
        )
        dispatch.refresh_from_db()
        for _ in range(3):
            chase(dispatch, 30)
            self.claim.notes.update(created_at=timezone.now() - timedelta(minutes=30))
        self.claim.refresh_from_db()
        self.assertFalse(self.claim.needs_human)

    def test_the_webhook_records_what_the_operator_said(self):
        from claims.vendor_calls import place_call

        place_call(self.claim)
        body = self.client.post(
            reverse("vendor-eta"),
            data=json.dumps({"accepted": "yes", "eta_minutes": 18}),
            content_type="application/json",
        ).json()
        self.assertTrue(body["ok"])
        self.assertIn("18 minutes", body["message"])
        self.assertEqual(self.claim.dispatches.get().eta_minutes, 18)

    def test_a_nonsense_eta_is_dropped_rather_than_promised(self):
        from claims.vendor_calls import place_call

        place_call(self.claim)
        self.client.post(
            reverse("vendor-eta"),
            data=json.dumps({"accepted": "yes", "eta_minutes": 99999}),
            content_type="application/json",
        )
        self.assertIsNone(self.claim.vendor_calls.first().eta_minutes)

    def test_the_caller_is_not_texted_twice_in_a_minute(self):
        from claims.vendor_calls import tell_the_caller

        tell_the_caller(self.claim, "First update.")
        second = tell_the_caller(self.claim, "Second update.")
        self.assertEqual(second["skipped"], "too_soon")
        # Still written down, just not sent.
        self.assertEqual(self.claim.notes.count(), 2)

    def test_emergency_services_are_offered_never_dialled(self):
        from claims.vendor_calls import emergency_options

        self.claim.injuries_reported = True
        self.claim.save(update_fields=["injuries_reported"])
        options = emergency_options(self.claim)
        self.assertTrue(options["suggest"])
        self.assertFalse(options["dial_automatically"])
        self.assertIn("injuries were reported on the call", options["reasons"])
        self.assertIn("highway", " ".join(options["reasons"]))

    def test_every_vendor_number_is_in_the_fiction_range(self):
        """Nothing in the book may ring a real recovery operator, because a
        wrong turn here dispatches a real truck to a place nobody crashed."""
        import re

        from claims.vendors import BOOK

        for kind, roster in BOOK.items():
            for vendor in roster:
                digits = re.sub(r"\D", "", vendor["phone"])
                self.assertTrue(
                    digits.startswith("1555") or digits.startswith("555"),
                    f"{kind}/{vendor['name']} is not a fiction-range number: {vendor['phone']}",
                )


class TelephonyTests(TestCase):
    """The Twilio client itself, not the callers who mock it away.

    Every other test in this file patches `claims.telephony.account` or
    `.configured` and never touches this module's own request-building — so a
    broken auth header or a mishandled error body would pass the whole suite
    and only show up against the real API, mid-transfer, with a caller on
    the line. This is the one place that talks to `urllib` directly.
    """

    def _fake_urlopen(self, body=b'{"sid": "CA1"}'):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = body
        return response

    def test_not_configured_without_both_credentials(self):
        from claims import telephony

        with mock.patch.dict("os.environ", {"TWILIO_ACCOUNT_SID": "AC1"}, clear=True):
            self.assertFalse(telephony.configured())
        with mock.patch.dict(
            "os.environ",
            {"TWILIO_ACCOUNT_SID": "AC1", "TWILIO_AUTH_TOKEN": "tok"},
            clear=True,
        ):
            self.assertTrue(telephony.configured())

    def test_a_get_carries_basic_auth_and_no_body(self):
        import base64

        from claims import telephony

        with mock.patch.dict(
            "os.environ",
            {"TWILIO_ACCOUNT_SID": "AC1", "TWILIO_AUTH_TOKEN": "tok"},
        ), mock.patch(
            "claims.telephony.request.urlopen", return_value=self._fake_urlopen()
        ) as urlopen:
            result = telephony.call("https://api.twilio.com/2010-04-01/Calls.json")

        self.assertEqual(result, {"sid": "CA1"})
        req = urlopen.call_args[0][0]
        self.assertEqual(req.get_method(), "GET")
        self.assertIsNone(req.data)
        expected = "Basic " + base64.b64encode(b"AC1:tok").decode()
        self.assertEqual(req.get_header("Authorization"), expected)

    def test_a_form_posts_urlencoded_with_the_right_content_type(self):
        from claims import telephony

        with mock.patch.dict(
            "os.environ", {"TWILIO_ACCOUNT_SID": "AC1", "TWILIO_AUTH_TOKEN": "tok"}
        ), mock.patch(
            "claims.telephony.request.urlopen", return_value=self._fake_urlopen()
        ) as urlopen:
            telephony.call(
                "https://api.twilio.com/2010-04-01/Calls/CA1.json",
                form={"Twiml": "<Response/>"},
            )

        req = urlopen.call_args[0][0]
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.data, b"Twiml=%3CResponse%2F%3E")
        self.assertEqual(req.get_header("Content-type"), "application/x-www-form-urlencoded")

    def test_an_empty_body_is_an_empty_dict_not_a_parse_error(self):
        from claims import telephony

        with mock.patch.dict(
            "os.environ", {"TWILIO_ACCOUNT_SID": "AC1", "TWILIO_AUTH_TOKEN": "tok"}
        ), mock.patch(
            "claims.telephony.request.urlopen", return_value=self._fake_urlopen(body=b"")
        ):
            self.assertEqual(telephony.call("https://api.twilio.com/x"), {})

    def test_an_http_error_becomes_a_telephony_error_with_the_host_stripped(self):
        from io import BytesIO
        from urllib.error import HTTPError

        from claims import telephony

        error = HTTPError(
            "https://api.twilio.com/2010-04-01/Calls/CA1.json",
            404,
            "Not Found",
            {},
            BytesIO(b'{"message": "no such call"}'),
        )
        with mock.patch.dict(
            "os.environ", {"TWILIO_ACCOUNT_SID": "AC1", "TWILIO_AUTH_TOKEN": "tok"}
        ), mock.patch("claims.telephony.request.urlopen", side_effect=error):
            with self.assertRaises(telephony.TelephonyError) as caught:
                telephony.call("https://api.twilio.com/2010-04-01/Calls/CA1.json")

        exc = caught.exception
        self.assertEqual(exc.status, 404)
        self.assertIn("no such call", exc.body)
        # The host is stripped — a Twilio error ends up in a log line, and a
        # log line is not the place for even a well-known API host.
        self.assertNotIn("api.twilio.com", str(exc))
        self.assertIn("/2010-04-01/Calls/CA1.json", str(exc))

    def test_a_network_failure_becomes_a_telephony_error_with_status_zero(self):
        from urllib.error import URLError

        from claims import telephony

        with mock.patch.dict(
            "os.environ", {"TWILIO_ACCOUNT_SID": "AC1", "TWILIO_AUTH_TOKEN": "tok"}
        ), mock.patch(
            "claims.telephony.request.urlopen",
            side_effect=URLError("Connection refused"),
        ):
            with self.assertRaises(telephony.TelephonyError) as caught:
                telephony.call("https://api.twilio.com/x")
        self.assertEqual(caught.exception.status, 0)

    def test_account_prefixes_the_path_with_this_accounts_sid(self):
        from claims import telephony

        with mock.patch.dict(
            "os.environ", {"TWILIO_ACCOUNT_SID": "AC1", "TWILIO_AUTH_TOKEN": "tok"}
        ), mock.patch(
            "claims.telephony.request.urlopen", return_value=self._fake_urlopen()
        ) as urlopen:
            telephony.account("/Calls.json")
        req = urlopen.call_args[0][0]
        self.assertEqual(
            req.full_url, "https://api.twilio.com/2010-04-01/Accounts/AC1/Calls.json"
        )


class RosterTests(TestCase):
    """Who is on the desk, which is the question a transfer starts with."""

    def setUp(self):
        self.day = Dispatcher.objects.create(
            name="Priya", email="priya@example.com", phone="+15550142887", order=1
        )
        self.night = Dispatcher.objects.create(
            name="Marcus", email="marcus@example.com", phone="+15550142888", order=2
        )

    def test_an_empty_rota_means_the_desk_is_always_open(self):
        """A deployment that never wrote a rota must not silently go dark."""
        self.assertFalse(roster.after_hours())
        self.assertEqual(len(roster.escalation()), 2)

    def test_shifts_govern_once_somebody_writes_one(self):
        Shift.objects.create(
            dispatcher=self.day, weekday=0, starts="09:00", ends="17:00"
        )
        monday_noon = self._local(2026, 9, 7, 12, 0)  # a Monday
        monday_night = self._local(2026, 9, 7, 23, 0)
        self.assertEqual([p.id for p in roster.escalation(monday_noon)], [self.day.id])
        self.assertTrue(roster.after_hours(monday_night))

    def test_a_night_shift_covers_the_small_hours_of_the_next_day(self):
        """22:00 to 06:00 is a shift, not an empty set."""
        Shift.objects.create(
            dispatcher=self.night, weekday=4, starts="22:00", ends="06:00"
        )
        friday_late = self._local(2026, 9, 11, 23, 30)
        saturday_early = self._local(2026, 9, 12, 2, 0)
        saturday_noon = self._local(2026, 9, 12, 12, 0)
        self.assertEqual([p.id for p in roster.escalation(friday_late)], [self.night.id])
        self.assertEqual([p.id for p in roster.escalation(saturday_early)], [self.night.id])
        self.assertTrue(roster.after_hours(saturday_noon))

    def test_somebody_with_no_number_is_on_the_board_but_never_rung(self):
        """A silent leg in the escalation is worse than not being in it."""
        watcher = Dispatcher.objects.create(name="Ana", email="ana@example.com", phone="")
        self.assertIn(watcher.id, [p.id for p in roster.rostered()])
        self.assertNotIn(watcher.id, [p.id for p in roster.escalation()])

    def test_the_escalation_skips_whoever_has_already_been_tried(self):
        rest = roster.escalation(exclude=[self.day.id])
        self.assertEqual([p.id for p in rest], [self.night.id])

    def _local(self, *args):
        return timezone.make_aware(datetime(*args), timezone.get_current_timezone())


class DispatcherAccountTests(TestCase):
    """The shared password is a demo. A desk taking real calls has people."""

    def test_the_shared_password_still_works_with_nobody_on_the_desk(self):
        with self.settings(DESK_AUTH=True, DESK_PASSWORD="secret"):
            self.client.post("/login/", {"password": "secret"})
            self.assertEqual(self.client.get(reverse("dashboard")).status_code, 200)

    def test_creating_the_first_account_switches_the_login_over(self):
        person = Dispatcher(name="Priya", email="priya@example.com", phone="+15550142887")
        person.set_password("longenough")
        person.save()
        with self.settings(DESK_AUTH=True, DESK_PASSWORD="secret"):
            # The shared password is no longer a way in.
            self.client.post("/login/", {"password": "secret"})
            self.assertEqual(self.client.get(reverse("dashboard")).status_code, 302)
            # The account is.
            self.client.post(
                "/login/", {"email": "priya@example.com", "password": "longenough"}
            )
            self.assertEqual(self.client.get(reverse("dashboard")).status_code, 200)

    def test_a_password_is_never_stored_as_typed(self):
        person = Dispatcher(name="Priya", email="p@example.com")
        person.set_password("longenough")
        person.save()
        self.assertNotIn("longenough", person.password)
        self.assertTrue(person.check_password("longenough"))
        self.assertFalse(person.check_password("something else"))

    def test_signing_in_is_recorded_against_the_person(self):
        person = Dispatcher(name="Priya", email="priya@example.com")
        person.set_password("longenough")
        person.save()
        with self.settings(DESK_AUTH=True):
            self.client.post(
                "/login/", {"email": "priya@example.com", "password": "longenough"}
            )
        action = DeskAction.objects.filter(action="sign_in").first()
        self.assertIsNotNone(action)
        self.assertEqual(action.dispatcher_id, person.id)
        self.assertEqual(action.who, "Priya")

    def test_the_command_refuses_a_number_that_is_not_e164(self):
        from django.core.management import call_command

        with self.assertRaises(CommandError):
            call_command(
                "create_dispatcher",
                name="Priya",
                email="priya@example.com",
                phone="555-0142",
                password="longenough",
            )

    def test_the_command_writes_the_shifts_it_is_given(self):
        from django.core.management import call_command

        call_command(
            "create_dispatcher",
            name="Priya",
            email="priya@example.com",
            phone="+15550142887",
            password="longenough",
            shifts="mon-fri 08:00-18:00, sat 09:00-13:00",
            stdout=StringIO(),
        )
        person = Dispatcher.objects.get(email="priya@example.com")
        self.assertEqual(person.shifts.count(), 6)
        self.assertEqual(
            sorted(person.shifts.values_list("weekday", flat=True)), [0, 1, 2, 3, 4, 5]
        )


class HandoffTests(TestCase):
    """Ivy stepping aside, and what the caller hears while she does."""

    def setUp(self):
        self.holder = Policyholder.objects.create(
            policy_number="PV482193", full_name="Dana Whitfield", phone="+15550142887"
        )
        self.call = Conversation.objects.create(
            session_id="sess_handoff",
            channel=Conversation.Channel.PHONE,
            caller_number="+15550190000",
            policyholder=self.holder,
            verified=True,
        )
        self.claim = Claim.objects.create(
            policy_number="PV482193",
            incident_type="collision",
            location="I-95 northbound",
            is_drivable=False,
            conversation=self.call,
            policyholder=self.holder,
        )
        self.priya = Dispatcher.objects.create(
            name="Priya", email="priya@example.com", phone="+15550142001", order=1
        )
        self.marcus = Dispatcher.objects.create(
            name="Marcus", email="marcus@example.com", phone="+15550142002", order=2
        )

    def _ask_for_a_person(self):
        return self.client.post(
            reverse("request-human"),
            data=json.dumps({"session_id": "sess_handoff", "reason": "caller_request"}),
            content_type="application/json",
        )

    # --- without live transfers ---------------------------------------------

    def test_asking_for_a_person_opens_a_handoff_and_says_something_true(self):
        """Transfers are off, so the caller must not be told to hold for one."""
        body = self._ask_for_a_person().json()
        self.assertTrue(body["ok"])
        self.assertFalse(body["transferring"])
        self.assertIn("call you", body["message"])
        handoff = Handoff.objects.get()
        self.assertTrue(handoff.simulated)
        self.assertIn("LIVE_TRANSFERS is off", handoff.note)
        self.assertTrue(Claim.objects.get(pk=self.claim.pk).needs_human)

    def test_asking_twice_is_one_person_waiting(self):
        self._ask_for_a_person()
        self._ask_for_a_person()
        self.assertEqual(Handoff.objects.count(), 1)

    def test_out_of_hours_is_told_to_the_caller_in_words(self):
        Shift.objects.create(
            dispatcher=self.priya, weekday=0, starts="09:00", ends="09:30"
        )
        with mock.patch("claims.roster.rostered", return_value=[]):
            body = self._ask_for_a_person().json()
        handoff = Handoff.objects.get()
        self.assertEqual(handoff.status, Handoff.Status.AFTER_HOURS)
        self.assertIn("closed", body["message"])
        self.assertTrue(
            self.claim.notes.filter(body__icontains="out of hours").exists()
        )

    def test_transfer_ready_names_each_reason_it_is_not(self):
        from claims import handoff as handoff_lib

        with self.settings(LIVE_TRANSFERS=False):
            self.assertEqual(handoff_lib.transfer_ready(), (False, "LIVE_TRANSFERS is off"))
        with self.settings(LIVE_TRANSFERS=True), mock.patch(
            "claims.telephony.configured", return_value=False
        ):
            self.assertEqual(
                handoff_lib.transfer_ready(), (False, "no Twilio credentials")
            )
        with self.settings(LIVE_TRANSFERS=True, PUBLIC_BASE_URL=""), mock.patch(
            "claims.telephony.configured", return_value=True
        ):
            AgentProfile.objects.update_or_create(defaults={"public_base_url": ""})
            self.assertEqual(
                handoff_lib.transfer_ready(),
                (False, "no public base URL to call back to"),
            )

    def test_an_empty_rota_with_no_fallback_is_not_a_silent_dead_end(self):
        """The one case the whole feature exists to prevent: nobody rostered,
        nobody to fall back to, and the caller must still be told something
        true rather than the call just quietly going nowhere."""
        Dispatcher.objects.all().update(phone="")
        with self.settings(HANDOFF_FALLBACK_NUMBER=""):
            body = self._ask_for_a_person().json()
        handoff = Handoff.objects.get()
        self.assertEqual(handoff.status, Handoff.Status.NO_ANSWER)
        self.assertIn("call you back", body["message"])
        self.assertTrue(
            self.claim.notes.filter(body__icontains="Nobody took the transfer").exists()
        )

    def test_the_fallback_number_is_actually_texted_when_one_is_set(self):
        with self.settings(HANDOFF_FALLBACK_NUMBER="+15550199999"), mock.patch(
            "claims.notify.send_sms", return_value={"sent": True, "sid": "SM1"}
        ) as send_sms, mock.patch("claims.roster.rostered", return_value=[]):
            self._ask_for_a_person()
        send_sms.assert_called_once()
        to_number, body = send_sms.call_args[0]
        self.assertEqual(to_number, "+15550199999")
        self.assertIn(f"CV-{self.claim.id:05d}", body)
        self.assertIn(self.call.caller_number, body)

    def test_note_on_claim_is_a_noop_without_a_linked_claim(self):
        from claims import handoff as handoff_lib

        call = Conversation.objects.create(session_id="sess_no_claim")
        handoff = Handoff.objects.create(conversation=call)
        self.assertIsNone(handoff_lib.note_on_claim(handoff, "hello"))

    # --- with live transfers -------------------------------------------------

    def _live(self):
        return self.settings(
            LIVE_TRANSFERS=True,
            OUTBOUND_FROM_NUMBER="+15550100000",
            HANDOFF_RING_SECONDS=20,
        )

    def _twilio(self, sid="CA123"):
        """Stand in for the carrier: one live call, and a record of redirects."""
        sent = []

        def account(path, form=None, method=None):
            if path.startswith("/Calls.json"):
                return {"calls": [{"sid": sid}] if sid else []}
            sent.append((path, form))
            return {"sid": sid}

        return sent, mock.patch("claims.telephony.account", side_effect=account)

    def test_a_live_call_is_taken_hold_of_and_pointed_at_the_first_dispatcher(self):
        sent, patched = self._twilio()
        with self._live(), mock.patch("claims.telephony.configured", return_value=True), patched:
            AgentProfile.objects.update_or_create(
                defaults={"public_base_url": "https://claimvoice.example.com"}
            )
            body = self._ask_for_a_person().json()

        self.assertTrue(body["transferring"])
        handoff = Handoff.objects.get()
        self.assertEqual(handoff.status, Handoff.Status.RINGING)
        self.assertEqual(handoff.dispatcher_id, self.priya.id)
        self.assertEqual(handoff.provider_call_id, "CA123")

        path, form = sent[0]
        self.assertEqual(path, "/Calls/CA123.json")
        self.assertIn("<Number>+15550142001</Number>", form["Twiml"])
        self.assertIn('timeout="20"', form["Twiml"])
        # The callback has to be reachable from Twilio and addressed by token.
        self.assertIn(f"/api/twilio/dial/{handoff.token}/", form["Twiml"])

    def test_a_failed_lookup_for_the_live_call_does_not_blow_up_the_handoff(self):
        """Twilio erroring on the `/Calls.json` lookup is not the same as
        finding no live call — both end up not dialled, but only one of them
        should ever be silent about why."""
        from claims import telephony

        def broken_account(path, form=None, method=None):
            raise telephony.TelephonyError("/Calls.json", 429, "rate limited")

        with self._live(), mock.patch("claims.telephony.configured", return_value=True), mock.patch(
            "claims.telephony.account", side_effect=broken_account
        ):
            AgentProfile.objects.update_or_create(
                defaults={"public_base_url": "https://claimvoice.example.com"}
            )
            body = self._ask_for_a_person().json()

        self.assertTrue(body["ok"])
        self.assertFalse(body["transferring"])
        handoff = Handoff.objects.get()
        self.assertEqual(handoff.status, Handoff.Status.ABANDONED)

    def test_an_unexpected_failure_while_ringing_tells_the_caller_rather_than_500ing(self):
        """Not every way of talking to Twilio going wrong shows up as our own
        TelephonyError — a malformed response, a bug in the TwiML we build,
        anything. None of it may reach the caller as a raw 500."""

        def account(path, form=None, method=None):
            if path.startswith("/Calls.json"):
                return {"calls": [{"sid": "CA123"}]}
            raise RuntimeError("something unexpected")

        with self._live(), mock.patch("claims.telephony.configured", return_value=True), mock.patch(
            "claims.telephony.account", side_effect=account
        ):
            AgentProfile.objects.update_or_create(
                defaults={"public_base_url": "https://claimvoice.example.com"}
            )
            body = self._ask_for_a_person().json()

        self.assertTrue(body["ok"])
        self.assertFalse(body["transferring"])
        handoff = Handoff.objects.get()
        self.assertEqual(handoff.status, Handoff.Status.FAILED)

    def test_nobody_answering_rings_the_next_person(self):
        """The overflow, which is the whole reason this is a queue."""
        sent, patched = self._twilio()
        with self._live(), mock.patch("claims.telephony.configured", return_value=True), patched:
            AgentProfile.objects.update_or_create(
                defaults={"public_base_url": "https://claimvoice.example.com"}
            )
            self._ask_for_a_person()
            handoff = Handoff.objects.get()
            answer = self.client.post(
                reverse("handoff-dial-status", args=[handoff.token]),
                {"DialCallStatus": "no-answer"},
            )

        self.assertEqual(answer.status_code, 200)
        handoff.refresh_from_db()
        self.assertEqual(handoff.dispatcher_id, self.marcus.id)
        self.assertEqual(handoff.status, Handoff.Status.RINGING)
        self.assertIn("<Number>+15550142002</Number>", sent[-1][1]["Twiml"])
        outcomes = list(handoff.attempts.values_list("outcome", flat=True))
        self.assertEqual(outcomes, ["no_answer", "ringing"])

    def test_somebody_answering_closes_it_and_says_so_on_the_claim(self):
        sent, patched = self._twilio()
        with self._live(), mock.patch("claims.telephony.configured", return_value=True), patched:
            AgentProfile.objects.update_or_create(
                defaults={"public_base_url": "https://claimvoice.example.com"}
            )
            self._ask_for_a_person()
            handoff = Handoff.objects.get()
            self.client.post(
                reverse("handoff-dial-status", args=[handoff.token]),
                {"DialCallStatus": "completed"},
            )

        handoff.refresh_from_db()
        self.assertEqual(handoff.status, Handoff.Status.DONE)
        self.assertIsNotNone(handoff.connected_at)
        self.assertTrue(self.claim.notes.filter(body__icontains="put through to Priya").exists())
        # Somebody actually spoke to them — the board must stop asking for one.
        self.assertFalse(Claim.objects.get(pk=self.claim.pk).needs_human)
        self.assertFalse(Conversation.objects.get(pk=self.call.pk).needs_human)

    def test_running_out_of_people_tells_the_caller_rather_than_holding_them(self):
        sent, patched = self._twilio()
        with self._live(), mock.patch("claims.telephony.configured", return_value=True), patched:
            AgentProfile.objects.update_or_create(
                defaults={"public_base_url": "https://claimvoice.example.com"}
            )
            self._ask_for_a_person()
            handoff = Handoff.objects.get()
            for _ in range(3):
                last = self.client.post(
                    reverse("handoff-dial-status", args=[handoff.token]),
                    {"DialCallStatus": "no-answer"},
                )

        handoff.refresh_from_db()
        self.assertEqual(handoff.status, Handoff.Status.NO_ANSWER)
        self.assertIn("call you back", last.content.decode())
        self.assertIn("<Hangup/>", last.content.decode())
        # Nobody actually spoke to them — still owed one, not silently dropped.
        self.assertTrue(Claim.objects.get(pk=self.claim.pk).needs_human)

    def test_closing_a_handoff_by_hand_clears_needs_human(self):
        """A dispatcher pressing Close is them saying it is dealt with."""
        self._ask_for_a_person()
        handoff = Handoff.objects.get()
        self.assertTrue(Claim.objects.get(pk=self.claim.pk).needs_human)

        self.client.post(
            reverse("handoff-close", args=[handoff.id]),
            data=json.dumps({"note": "Called them back myself."}),
            content_type="application/json",
        )
        self.assertFalse(Claim.objects.get(pk=self.claim.pk).needs_human)

    def test_a_caller_who_hung_up_is_not_reported_as_transferred(self):
        sent, patched = self._twilio(sid="")
        with self._live(), mock.patch("claims.telephony.configured", return_value=True), patched:
            AgentProfile.objects.update_or_create(
                defaults={"public_base_url": "https://claimvoice.example.com"}
            )
            self._ask_for_a_person()
        handoff = Handoff.objects.get()
        self.assertEqual(handoff.status, Handoff.Status.ABANDONED)

    # --- the routes ----------------------------------------------------------

    def test_the_carrier_callback_is_public_and_the_queue_is_not(self):
        """Twilio has no session and cannot get one; the queue is desk-only."""
        self._ask_for_a_person()
        handoff = Handoff.objects.get()
        with self.settings(DESK_AUTH=True, DESK_PASSWORD="secret"):
            self.assertEqual(self.client.get(reverse("handoff-feed")).status_code, 401)
            self.assertEqual(
                self.client.post(
                    reverse("handoff-dial-status", args=[handoff.token]),
                    {"DialCallStatus": "completed"},
                ).status_code,
                200,
            )

    def test_an_unknown_token_still_answers_the_caller(self):
        """A 500 at Twilio is a person listening to silence."""
        answer = self.client.post(
            reverse("handoff-dial-status", args=["nope"]), {"DialCallStatus": "completed"}
        )
        self.assertEqual(answer.status_code, 200)
        self.assertIn("<Hangup/>", answer.content.decode())

    def test_taking_a_call_needs_a_dispatcher_with_a_number(self):
        self._ask_for_a_person()
        handoff = Handoff.objects.get()
        # Signed in with the shared password: nobody in particular.
        self.assertEqual(
            self.client.post(reverse("handoff-take", args=[handoff.id])).status_code, 403
        )

    def test_taking_a_call_assigns_it_and_is_recorded(self):
        self._ask_for_a_person()
        handoff = Handoff.objects.get()
        self.priya.set_password("longenough")
        self.priya.save()
        with self.settings(DESK_AUTH=True):
            self.client.post(
                "/login/", {"email": "priya@example.com", "password": "longenough"}
            )
            body = self.client.post(reverse("handoff-take", args=[handoff.id])).json()
        self.assertTrue(body["ok"])
        self.assertFalse(body["transferred"])
        handoff.refresh_from_db()
        self.assertEqual(handoff.dispatcher_id, self.priya.id)
        self.assertTrue(DeskAction.objects.filter(action="take_call").exists())

    def _login_as_priya(self):
        self.priya.set_password("longenough")
        self.priya.save()
        self.client.post("/login/", {"email": "priya@example.com", "password": "longenough"})

    def test_taking_a_call_with_live_transfers_on_actually_redirects_it(self):
        """The success path `test_taking_a_call_assigns_it_and_is_recorded`
        does not reach: LIVE_TRANSFERS on, a live call to take hold of."""
        self._ask_for_a_person()
        handoff = Handoff.objects.get()
        sent, patched = self._twilio()
        with self._live(), mock.patch("claims.telephony.configured", return_value=True), patched:
            AgentProfile.objects.update_or_create(
                defaults={"public_base_url": "https://claimvoice.example.com"}
            )
            with self.settings(DESK_AUTH=True):
                self._login_as_priya()
                body = self.client.post(reverse("handoff-take", args=[handoff.id])).json()

        self.assertTrue(body["ok"])
        self.assertTrue(body["transferred"])
        handoff.refresh_from_db()
        self.assertEqual(handoff.status, Handoff.Status.RINGING)
        self.assertEqual(handoff.provider_call_id, "CA123")
        path, form = sent[-1]
        self.assertEqual(path, "/Calls/CA123.json")
        self.assertIn("<Number>+15550142001</Number>", form["Twiml"])

    def test_taking_a_call_reports_a_telephony_failure_as_502(self):
        self._ask_for_a_person()
        handoff = Handoff.objects.get()
        with self._live(), mock.patch(
            "claims.telephony.configured", return_value=True
        ), mock.patch(
            "claims.telephony.account",
            side_effect=[{"calls": [{"sid": "CA1"}]}, RuntimeError("Twilio is down")],
        ):
            with self.settings(DESK_AUTH=True):
                self._login_as_priya()
                response = self.client.post(reverse("handoff-take", args=[handoff.id]))

        self.assertEqual(response.status_code, 502)
        attempt = HandoffAttempt.objects.filter(handoff=handoff).latest("created_at")
        self.assertEqual(attempt.outcome, HandoffAttempt.Outcome.FAILED)
        self.assertIn("Twilio is down", attempt.detail)

    def test_dial_status_only_answers_post(self):
        handoff = Handoff.objects.create(conversation=self.call, caller_number="+15550190000")
        response = self.client.get(reverse("handoff-dial-status", args=[handoff.token]))
        self.assertEqual(response.status_code, 405)

    def test_dial_status_never_leaves_the_caller_on_a_dead_line(self):
        """Whatever goes wrong acting on it, Twilio still gets TwiML back —
        never a 500, which is a caller holding a silent line."""
        handoff = Handoff.objects.create(conversation=self.call, caller_number="+15550190000")
        with mock.patch("claims.handoff.on_dial_status", side_effect=RuntimeError("boom")):
            response = self.client.post(
                reverse("handoff-dial-status", args=[handoff.token]),
                {"DialCallStatus": "completed"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"<Hangup/>", response.content)
        self.assertIn(b"Response", response.content)

    def test_roster_feed_lists_dispatchers_with_their_shifts(self):
        Shift.objects.create(dispatcher=self.priya, weekday=0, starts="08:00", ends="18:00")
        body = self.client.get(reverse("roster-feed")).json()
        names = {row["name"] for row in body["dispatchers"]}
        self.assertEqual(names, {"Priya", "Marcus"})
        priya_row = next(row for row in body["dispatchers"] if row["name"] == "Priya")
        self.assertEqual(priya_row["phone"], "+15550142001")
        self.assertEqual(len(priya_row["shifts"]), 1)

    def test_desk_log_lists_recorded_actions_newest_first(self):
        from claims.models_desk import record

        record(self.priya, "sign_in")
        record(self.marcus, "sign_in")
        body = self.client.get(reverse("desk-log")).json()
        actions = [row["who"] for row in body["actions"]]
        self.assertEqual(actions[:2], ["Marcus", "Priya"])

    def test_the_queue_feed_carries_the_cover_the_board_shows(self):
        self._ask_for_a_person()
        body = self.client.get(reverse("handoff-feed")).json()
        self.assertEqual(len(body["handoffs"]), 1)
        self.assertFalse(body["roster"]["after_hours"])
        self.assertFalse(body["transfers"]["live"])
        self.assertEqual(body["transfers"]["why"], "LIVE_TRANSFERS is off")

    def test_the_queue_feed_carries_the_ring_window_so_the_board_can_flag_it(self):
        with self.settings(HANDOFF_RING_SECONDS=15):
            body = self.client.get(reverse("handoff-feed")).json()
        self.assertEqual(body["ring_seconds"], 15)

    # --- when the dial-status callback never arrives -------------------------

    def _stuck_ringing(self):
        sent, patched = self._twilio()
        with self._live(), mock.patch("claims.telephony.configured", return_value=True), patched:
            AgentProfile.objects.update_or_create(
                defaults={"public_base_url": "https://claimvoice.example.com"}
            )
            self._ask_for_a_person()
        handoff = Handoff.objects.get()
        self.assertEqual(handoff.status, Handoff.Status.RINGING)
        return handoff

    def test_a_row_still_ringing_well_past_the_window_is_not_stale_yet(self):
        handoff = self._stuck_ringing()
        self.assertEqual(handoff_module.stale_ringing(), [])

    def test_a_row_stuck_ringing_past_the_window_is_found_and_closed(self):
        handoff = self._stuck_ringing()
        HandoffAttempt.objects.filter(handoff=handoff).update(
            created_at=timezone.now() - timedelta(seconds=200)
        )
        stale = handoff_module.stale_ringing()
        self.assertEqual([h.id for h in stale], [handoff.id])

        handoff_module.reap_stale(handoff)
        handoff.refresh_from_db()
        self.assertEqual(handoff.status, Handoff.Status.NO_ANSWER)
        self.assertIn("no dial status", handoff.note)
        last_attempt = handoff.attempts.order_by("-created_at").first()
        self.assertEqual(last_attempt.outcome, HandoffAttempt.Outcome.FAILED)
        self.assertTrue(
            self.claim.notes.filter(body__icontains="Nobody took the transfer").exists()
        )
        # Reaped once — a second pass must not find it again.
        self.assertEqual(handoff_module.stale_ringing(), [])


class DuplicateClaimTests(TestCase):
    """One incident is one claim. Two drivers is two."""

    def setUp(self):
        self.holder = Policyholder.objects.create(
            policy_number="PV700100", full_name="Fleet Ltd", phone="+15550142887"
        )

    def _file(self, session_id, caller_number):
        Conversation.objects.create(
            session_id=session_id,
            channel=Conversation.Channel.PHONE,
            caller_number=caller_number,
        )
        return self.client.post(
            reverse("log-claim"),
            data=json.dumps(
                {
                    "session_id": session_id,
                    "policy_number": "PV700100",
                    "incident_type": "collision",
                    "location": "I-95 northbound",
                    "is_drivable": False,
                }
            ),
            content_type="application/json",
        ).json()

    def test_the_same_caller_filing_twice_is_replayed(self):
        first = self._file("sess_a", "+15550190001")
        second = self._file("sess_b", "+15550190001")
        self.assertNotIn("duplicate", first)
        self.assertTrue(second.get("duplicate"))
        self.assertEqual(first["claim_reference"], second["claim_reference"])
        self.assertEqual(Claim.objects.count(), 1)

    def test_two_drivers_on_one_fleet_policy_are_two_claims(self):
        """The bug this narrowing exists for: the second driver must not be
        read back a reference to somebody else's tow truck."""
        first = self._file("sess_a", "+15550190001")
        second = self._file("sess_b", "+15550190002")
        self.assertFalse(second.get("duplicate"))
        self.assertNotEqual(first["claim_reference"], second["claim_reference"])
        self.assertEqual(Claim.objects.count(), 2)

    def test_one_call_firing_the_tool_twice_is_still_one_claim(self):
        self._file("sess_a", "+15550190001")
        again = self.client.post(
            reverse("log-claim"),
            data=json.dumps(
                {
                    "session_id": "sess_a",
                    "policy_number": "PV700100",
                    "incident_type": "collision",
                    "location": "I-95 northbound",
                    "is_drivable": False,
                }
            ),
            content_type="application/json",
        ).json()
        self.assertTrue(again.get("duplicate"))
        self.assertEqual(Claim.objects.count(), 1)


class TickTests(TestCase):
    """The free-plan substitute for a worker dyno: one HTTP call runs a pass
    of everything that otherwise needs a scheduler of its own."""

    def test_get_and_post_are_both_allowed(self):
        self.assertEqual(self.client.get(reverse("tick")).status_code, 200)
        self.assertEqual(self.client.post(reverse("tick")).status_code, 200)

    def test_other_methods_are_rejected(self):
        self.assertEqual(self.client.delete(reverse("tick")).status_code, 405)

    def test_secret_is_enforced_when_configured(self):
        with self.settings(CLAIM_WEBHOOK_SECRET="s3cret"):
            self.assertEqual(self.client.get(reverse("tick")).status_code, 403)
            ok = self.client.get(reverse("tick"), headers={"x-claim-secret": "s3cret"})
            self.assertEqual(ok.status_code, 200)

    def test_nothing_due_is_a_quiet_pass(self):
        body = self.client.get(reverse("tick")).json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["dispatches_chased"], 0)
        self.assertEqual(body["handoffs_closed"], 0)

    def test_a_late_tow_is_chased_in_one_pass(self):
        from claims.vendor_calls import place_call, record_outcome

        holder = Policyholder.objects.create(
            policy_number="PV482193", full_name="Dana Whitfield", phone="+15550142887"
        )
        claim = Claim.objects.create(
            policy_number="PV482193",
            incident_type="collision",
            location="I-95 northbound near exit 12",
            is_drivable=False,
            tow_required=True,
            policyholder=holder,
            lat=39.95,
            lng=-75.16,
        )
        call = place_call(claim)
        record_outcome(call, accepted=True, eta_minutes=25)
        Dispatch.objects.filter(claim=claim).update(
            updated_at=timezone.now() - timedelta(minutes=45)
        )

        body = self.client.get(reverse("tick")).json()
        self.assertEqual(body["dispatches_chased"], 1)
        self.assertTrue(
            claim.vendor_calls.filter(purpose=VendorCall.Purpose.ETA_CHECK).exists()
        )

    def test_a_handoff_stuck_ringing_is_closed_in_one_pass(self):
        conversation = Conversation.objects.create(session_id="sess-tick")
        handoff = Handoff.objects.create(
            conversation=conversation, status=Handoff.Status.RINGING
        )
        Handoff.objects.filter(pk=handoff.pk).update(
            created_at=timezone.now() - timedelta(seconds=200)
        )

        body = self.client.get(reverse("tick")).json()
        self.assertEqual(body["handoffs_closed"], 1)
        handoff.refresh_from_db()
        self.assertEqual(handoff.status, Handoff.Status.NO_ANSWER)

    def test_a_bad_sync_pass_does_not_block_the_others(self):
        with mock.patch(
            "claims.sync.sync_sessions", side_effect=RuntimeError("assemblyai is down")
        ):
            body = self.client.get(reverse("tick")).json()
        self.assertTrue(body["ok"])
        self.assertIsNone(body["sessions_synced"])
        self.assertEqual(body["dispatches_chased"], 0)
