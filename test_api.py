"""HTTP API tests using Flask's test client (no server, no network)."""
from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("DB_PATH", os.path.join(tempfile.mkdtemp(), "import.db"))
os.environ.pop("GEMINI_API_KEY", None)
os.environ.pop("GOOGLE_API_KEY", None)

import app as app_module  # noqa: E402
from panchayat.llm import GeminiClient  # noqa: E402
from tests.mock_gemini import API_KEY, MockGemini  # noqa: E402


def ndjson(resp) -> list[dict]:
    return [json.loads(line) for line in resp.get_data(as_text=True).splitlines() if line.strip()]


class ApiTests(unittest.TestCase):
    def setUp(self):
        db = os.path.join(tempfile.mkdtemp(), "api.db")
        self.app = app_module.create_app(db_path=db, llm=GeminiClient(api_key=""), rate_limits=False)
        self.client = self.app.test_client()

    def test_pages_and_health(self):
        self.assertEqual(self.client.get("/healthz").json, {"ok": True})
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Tell us the problem", page.get_data(as_text=True))
        js = self.client.get("/static/app.js")
        self.assertEqual(js.status_code, 200)
        js.close()

    def test_status_reports_offline_mode(self):
        s = self.client.get("/api/status").json
        self.assertEqual(s["llm"]["mode"], "offline")
        self.assertEqual(s["counts"]["total"], 18)

    def test_stream_creates_ticket_and_reports_steps(self):
        resp = self.client.post("/api/complaints/stream", json={"text": "Garbage dumped on Rice Mill Road for a week", "name": "Arun"})
        self.assertEqual(resp.mimetype, "application/x-ndjson")
        events = ndjson(resp)
        kinds = [e["type"] for e in events]
        self.assertEqual(kinds[0], "start")
        self.assertIn("step", kinds)
        result = events[-1]["result"]
        self.assertEqual(result["outcome"], "created")
        tid = result["ticket"]["id"]
        detail = self.client.get(f"/api/tickets/{tid}").json
        self.assertEqual(detail["reports"][0]["citizen_name"], "Arun")
        self.assertEqual(len(detail["notifications"]), 2)

    def test_plain_json_endpoint_and_followup(self):
        first = self.client.post("/api/complaints", json={"text": "Street light not working"}).json
        self.assertEqual(first["outcome"], "needs_info")
        second = self.client.post("/api/complaints", json={"text": "School Street", "followup_context": "Street light not working"}).json
        self.assertEqual(second["outcome"], "created")
        self.assertEqual(second["ticket"]["ward"], 2)

    def test_validation_errors(self):
        self.assertEqual(self.client.post("/api/complaints", json={"text": "  "}).status_code, 400)
        bad_mime = {"text": "pothole on school street", "image": {"mime": "text/html", "data": "aGk="}}
        r = self.client.post("/api/complaints", json=bad_mime)
        self.assertEqual(r.status_code, 400)
        self.assertIn("JPEG", r.json["error"])
        bad_b64 = {"text": "pothole on school street", "image": {"mime": "image/png", "data": "!!!"}}
        self.assertEqual(self.client.post("/api/complaints", json=bad_b64).status_code, 400)
        self.assertEqual(self.client.get("/api/tickets/AP-9999").status_code, 404)

    def test_photo_accepted(self):
        img = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 64).decode()
        r = self.client.post("/api/complaints", json={"text": "pothole on school street", "image": {"mime": "image/png", "data": img}})
        self.assertEqual(r.status_code, 200)
        tid = r.json["ticket"]["id"]
        reports = self.client.get(f"/api/tickets/{tid}").json["reports"]
        self.assertEqual(reports[-1]["has_photo"], 1)  # newest report (may be merged into an older ticket)

    def test_filters_and_phone_masking(self):
        self.client.post("/api/complaints", json={"text": "Bus stand road pothole", "phone": "9876543210"})
        open_items = self.client.get("/api/tickets?status=OPEN_ANY&ward=1").json["tickets"]
        self.assertTrue(all(t["ward"] == 1 and t["status"] != "RESOLVED" for t in open_items))
        tid = next(t["id"] for t in open_items if t["location"].startswith("Bus Stand Road") and t["report_count"] > 4)
        reports = self.client.get(f"/api/tickets/{tid}").json["reports"]
        self.assertTrue(any(r["phone"].endswith("3210") and "98765" not in r["phone"] for r in reports))

    def test_officer_status_flow(self):
        r = self.client.post("/api/tickets/AP-1002/status", json={"status": "IN_PROGRESS", "actor": "P. Ravi"})
        self.assertEqual(r.json["ticket"]["status"], "IN_PROGRESS")
        r = self.client.post("/api/tickets/AP-1002/status", json={"status": "RESOLVED", "note": "Drain cleared"})
        self.assertEqual(r.json["ticket"]["status"], "RESOLVED")
        self.assertEqual(r.json["ticket"]["resolution_note"], "Drain cleared")
        self.assertEqual(self.client.post("/api/tickets/AP-1002/status", json={"status": "DONE"}).status_code, 400)

    def test_clock_and_escalation(self):
        self.assertEqual(self.client.post("/api/clock", json={"advance_hours": 72}).json["offset_hours"], 72)
        sweep = self.client.post("/api/escalation/sweep").json
        self.assertGreaterEqual(len(sweep["escalated"]), 1)
        self.assertTrue(self.client.get("/api/escalation/log").json["events"])
        self.assertEqual(self.client.post("/api/clock", json={"reset": True}).json["offset_hours"], 0)
        self.assertEqual(self.client.post("/api/clock", json={"advance_hours": "x"}).status_code, 400)

    def test_insights(self):
        stats = self.client.get("/api/insights").json
        self.assertEqual(stats["total_tickets"], 18)
        brief = self.client.post("/api/insights/brief").json
        self.assertTrue(brief["brief"]["priority_works"])

    def test_scenarios_run_stream(self):
        self.assertEqual(len(self.client.get("/api/scenarios").json["scenarios"]), 11)
        events = ndjson(self.client.post("/api/scenarios/run", json={"ids": ["S4", "S7"], "mode": "offline"}))
        results = [e["result"] for e in events if e["type"] == "result"]
        self.assertEqual([r["id"] for r in results], ["S4", "S7"])
        self.assertTrue(all(r["passed"] for r in results))

    def test_graph_and_reset(self):
        g = self.client.get("/api/graph").json
        self.assertIn("flowchart TD", g["mermaid"])
        self.assertEqual(len(g["graph"]["nodes"]), 9)
        self.client.post("/api/complaints", json={"text": "Pothole on Rice Mill Road"})
        self.assertTrue(self.client.post("/api/admin/reset").json["ok"])
        self.assertEqual(self.client.get("/api/status").json["counts"]["total"], 18)

    def test_rate_limit(self):
        db = os.path.join(tempfile.mkdtemp(), "rl.db")
        client = app_module.create_app(db_path=db, llm=GeminiClient(api_key=""), rate_limits=True).test_client()
        codes = [client.post("/api/admin/reset").status_code for _ in range(6)]
        self.assertEqual(codes[:5], [200] * 5)
        self.assertEqual(codes[5], 429)

    def test_end_to_end_with_gemini_mock(self):
        with MockGemini() as mock:
            db = os.path.join(tempfile.mkdtemp(), "llm.db")
            llm = GeminiClient(api_key=API_KEY, model="", base_url=mock.base_url)
            client = app_module.create_app(db_path=db, llm=llm, rate_limits=False).test_client()
            events = ndjson(client.post("/api/complaints/stream", json={"text": "Electric wire has fallen near the school in Gandhi Street"}))
            result = events[-1]["result"]
            self.assertEqual(result["ticket"]["priority"], "P1")
            self.assertIn("gemini-3.5-flash-lite", result["engines"])
            self.assertEqual(client.get("/api/status").json["llm"]["mode"], "gemini")


if __name__ == "__main__":
    unittest.main()
