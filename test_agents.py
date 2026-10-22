"""Agent pipeline and Gemini client tests.

Run:  python -m unittest discover -s tests -v      (or: pytest)
No API key or internet needed — Gemini is replaced by a local mock server
that speaks the real REST wire format.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from panchayat import agents, escalation, insights, offline, scenarios, seed  # noqa: E402
from panchayat.agents import Ctx  # noqa: E402
from panchayat.db import Store  # noqa: E402
from panchayat.llm import GeminiClient, LLMUnavailable, order_models  # noqa: E402
from tests.mock_gemini import API_KEY, MockGemini  # noqa: E402


def fresh_store() -> Store:
    path = os.path.join(tempfile.mkdtemp(), "test.db")
    store = Store(path)
    seed.seed(store)
    return store


class OfflineEngineTests(unittest.TestCase):
    def test_all_scenarios_pass_offline(self):
        results = list(scenarios.run_many(use_llm=False))
        failed = [(r["id"], r["checks"], r["error"]) for r in results if not r["passed"]]
        self.assertEqual(failed, [])
        self.assertEqual(len(results), len(scenarios.SCENARIOS))

    def test_language_detection(self):
        self.assertEqual(offline.detect_language("தெரு விளக்கு எரியவில்லை"), "ta")
        self.assertEqual(offline.detect_language("Market road la kuppai edukkala"), "tanglish")
        self.assertEqual(offline.detect_language("The drain is blocked"), "en")

    def test_street_maps_to_ward_in_tamil_and_english(self):
        self.assertEqual(offline.analyze("காமராஜர் தெருவில் சாக்கடை")["ward"], 2)
        self.assertEqual(offline.analyze("pothole on rice mill road")["ward"], 6)

    def test_street_names_do_not_bias_category(self):
        # "Canal Road" and "Water Tank Street" are places, not problems.
        self.assertEqual(offline.analyze("Street light not working on Canal Road")["category"], "STREET_LIGHTS")
        self.assertEqual(offline.analyze("Garbage dumped in Water Tank Street")["category"], "SANITATION")

    def test_invalid_ward_triggers_followup(self):
        r = offline.analyze("Ward 9 la street light eriyala")
        self.assertTrue(r["needs_followup"])
        self.assertEqual(r["ward"], 0)

    def test_followup_flow_reuses_context(self):
        ctx = Ctx(store=fresh_store(), use_llm=False)
        first = agents.run_complaint(ctx, "Street light not working")
        self.assertEqual(first["outcome"], "needs_info")
        second = agents.run_complaint(ctx, "Bazaar Street", followup_context="Street light not working")
        self.assertEqual(second["outcome"], "created")
        self.assertEqual(second["ticket"]["ward"], 1)

    def test_merge_raises_priority_for_hazard(self):
        ctx = Ctx(store=fresh_store(), use_llm=False)
        res = agents.run_complaint(ctx, "Sewage water is mixing with drinking water pipe in Indira Colony.")
        self.assertEqual(res["outcome"], "merged")
        self.assertEqual(res["ticket"]["priority"], "P1")
        self.assertEqual(res["priority_change"]["from"], "P3")

    def test_status_update_notifies_all_reporters(self):
        store = fresh_store()
        ctx = Ctx(store=store, use_llm=False)
        tid = next(t["id"] for t in store.list_tickets() if t["report_count"] >= 3 and t["status"] == "OPEN")
        before = len(store.notifications_for(tid))
        agents.update_status(ctx, tid, "RESOLVED", "Drain cleared")
        after = store.notifications_for(tid)
        self.assertEqual(len(after) - before, store.get_ticket(tid)["report_count"])
        self.assertEqual(store.get_ticket(tid)["status"], "RESOLVED")

    def test_escalation_levels_and_no_double_escalation(self):
        store = fresh_store()
        ctx = Ctx(store=store, use_llm=False)
        store.set_clock_offset(72)
        first = escalation.run_sweep(ctx)
        self.assertGreaterEqual(len(first["escalated"]), 1)
        second = escalation.run_sweep(ctx)
        self.assertEqual(second["escalated"], [])  # same clock → nothing new to escalate

    def test_insights_rule_brief(self):
        ctx = Ctx(store=fresh_store(), use_llm=False)
        out = insights.development_brief(ctx)
        self.assertTrue(out["brief"]["headline"])
        self.assertGreaterEqual(len(out["brief"]["priority_works"]), 1)
        self.assertEqual(out["engine"], agents.OFFLINE)


class GeminiClientTests(unittest.TestCase):
    def setUp(self):
        self.mock = MockGemini().__enter__()

    def tearDown(self):
        self.mock.__exit__(None, None, None)

    def client(self, **kw) -> GeminiClient:
        kw.setdefault("api_key", API_KEY)
        kw.setdefault("model", "")
        return GeminiClient(base_url=self.mock.base_url, max_retry_wait=0.1, cooldown_seconds=30, **kw)

    def test_model_ordering_prefers_newest_lite_then_flash(self):
        names = ["gemini-2.5-flash", "gemini-3.1-flash-lite", "gemini-3.5-flash-lite", "gemini-3.8-flash",
                 "gemini-3.6-flash", "gemini-3.8-flash-tts", "gemini-flash-latest", "gemini-4-argon"]
        self.assertEqual(order_models(names)[:3], ["gemini-3.5-flash-lite", "gemini-3.8-flash", "gemini-flash-latest"])
        self.assertNotIn("gemini-3.8-flash-tts", order_models(names))

    def test_request_wire_format(self):
        llm = self.client()
        res = llm.generate_json(system="X INTAKE AGENT", prompt="Citizen complaint:\n<<<\nNo water in Anna Nagar\n>>>",
                                schema={"type": "object"}, images=[("image/jpeg", "aGVsbG8=")])
        self.assertEqual(res.model, "gemini-3.5-flash-lite")
        req = self.mock.requests[-1]
        self.assertEqual(req["headers"].get("x-goog-api-key"), API_KEY)
        body = req["body"]
        self.assertIn("systemInstruction", body)
        self.assertEqual(body["generationConfig"]["responseFormat"]["text"]["mimeType"], "APPLICATION_JSON")
        self.assertEqual(body["contents"][0]["parts"][1]["inlineData"]["mimeType"], "image/jpeg")
        self.assertEqual(res.tokens, 150)

    def test_falls_back_to_older_output_format(self):
        self.mock.reject_fields = {"responseFormat"}
        llm = self.client()
        llm.generate_json(system="INTAKE AGENT", prompt="<<<\nhi\n>>>", schema={"type": "object"})
        body = self.mock.requests[-1]["body"]
        self.assertIn("responseJsonSchema", body["generationConfig"])
        n = len(self.mock.requests)
        llm.generate_json(system="INTAKE AGENT", prompt="<<<\nhi\n>>>", schema={"type": "object"})
        self.assertEqual(len(self.mock.requests), n + 1)  # remembered: no extra failing request

    def test_drops_thinking_config_if_rejected(self):
        self.mock.reject_fields = {"thinkingConfig"}
        llm = self.client()
        llm.generate_json(system="INTAKE AGENT", prompt="<<<\nhi\n>>>", schema={"type": "object"})
        self.assertNotIn("thinkingConfig", self.mock.requests[-1]["body"]["generationConfig"])
        self.assertIn("responseFormat", self.mock.requests[-1]["body"]["generationConfig"])

    def test_skips_models_without_quota_or_missing(self):
        self.mock.behaviour = {"gemini-3.5-flash-lite": "429-zero", "gemini-3.8-flash": "404"}
        llm = self.client()
        res = llm.generate_json(system="INTAKE AGENT", prompt="<<<\nhi\n>>>", schema={"type": "object"})
        self.assertEqual(res.model, "gemini-3.6-flash")
        self.assertIn("gemini-3.5-flash-lite", llm._dead)

    def test_forced_model_is_tried_first(self):
        llm = self.client(model="gemini-3.8-flash")
        res = llm.generate_json(system="INTAKE AGENT", prompt="<<<\nhi\n>>>", schema={"type": "object"})
        self.assertEqual(res.model, "gemini-3.8-flash")

    def test_rate_limit_cools_down_and_pipeline_falls_back(self):
        self.mock.behaviour = {m: "429" for m in self.mock.models}
        llm = self.client()
        with self.assertRaises(LLMUnavailable):
            llm.generate_json(system="INTAKE AGENT", prompt="<<<\nhi\n>>>", schema={"type": "object"})
        self.assertEqual(llm.status()["mode"], "cooldown")
        ctx = Ctx(store=fresh_store(), llm=llm)
        res = agents.run_complaint(ctx, "Drainage overflowing on Kamarajar Street")
        self.assertEqual(res["outcome"], "merged")
        self.assertEqual(res["engines"], [agents.OFFLINE])
        self.assertTrue(res["fallbacks"])

    def test_invalid_key_is_reported(self):
        llm = self.client(api_key="wrong-key")
        with self.assertRaises(LLMUnavailable):
            llm.generate_json(system="INTAKE AGENT", prompt="<<<\nhi\n>>>", schema={"type": "object"})
        self.assertIn("not valid", llm.status()["note"])
        self.assertFalse(llm.available())

    def test_bad_json_falls_back_for_that_step(self):
        self.mock.behaviour = {m: "badjson" for m in self.mock.models}
        ctx = Ctx(store=fresh_store(), llm=self.client())
        res = agents.run_complaint(ctx, "Street light not working on Ambedkar Street")
        self.assertIn(res["outcome"], ("created", "merged"))
        self.assertTrue(any("JSON" in f for f in res["fallbacks"]))

    def test_all_scenarios_pass_with_gemini_mock(self):
        llm = self.client()
        results = list(scenarios.run_many(llm=llm, use_llm=True))
        failed = [(r["id"], r["checks"], r["error"]) for r in results if not r["passed"]]
        self.assertEqual(failed, [])
        complaint_runs = [r for r in results if r["id"] != "S11"]
        self.assertTrue(all("gemini-3.5-flash-lite" in r["engines"] for r in complaint_runs))

    def test_safety_guardrail_overrides_model(self):
        ctx = Ctx(store=fresh_store(), llm=self.client())  # mock triage answers P3 for wires
        res = agents.run_complaint(ctx, "Electric wire has fallen on the road near the school in Gandhi Street.")
        self.assertEqual(res["ticket"]["priority"], "P1")
        self.assertTrue(res["triage"]["guardrail"])
        self.assertTrue(any(e["engine"] == "guardrail" for e in res["trace"]))

    def test_photo_is_sent_and_observed(self):
        ctx = Ctx(store=fresh_store(), llm=self.client())
        res = agents.run_complaint(ctx, "Garbage dumped on Rice Mill Road", images=[("image/png", "aGVsbG8=")])
        self.assertEqual(res["intake"]["photo_observation"], "The photo shows the problem.")

    def test_escalation_and_insights_use_gemini(self):
        store = fresh_store()
        ctx = Ctx(store=store, llm=self.client())
        store.set_clock_offset(72)
        sweep = escalation.run_sweep(ctx)
        self.assertEqual(sweep["engine"], "gemini-3.5-flash-lite")
        self.assertTrue(all(e["note"].startswith("[mock]") for e in sweep["escalated"]))
        brief = insights.development_brief(ctx)
        self.assertEqual(brief["engine"], "gemini-3.5-flash-lite")


if __name__ == "__main__":
    unittest.main()
