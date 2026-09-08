"""End-to-end coverage of the paths a live demo depends on."""

import json
import re
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from django.core.files.uploadedfile import SimpleUploadedFile

from .models import (
    AgentProfile,
    AgentRevision,
    Claim,
    ClaimPhoto,
    ClaimStatus,
    Conversation,
    Dispatch,
    Policyholder,
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
