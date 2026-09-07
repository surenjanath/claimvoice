"""End-to-end coverage of the paths a live demo depends on."""

import json
import re
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    AgentProfile,
    Claim,
    ClaimStatus,
    Conversation,
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

    def test_dashboard_renders(self):
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dispatcher board")

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
