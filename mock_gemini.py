"""A local stand-in for the Gemini REST API, used by the tests.

It speaks the same wire format (ListModels, generateContent, error bodies) so the
real HTTP client code is exercised end to end without a network or API key.
Answers are produced from the offline analyzer so the pipeline gets realistic JSON.
"""
from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from panchayat import offline

API_KEY = "test-key"


class MockGemini:
    def __init__(self):
        self.models = ["gemini-3.5-flash-lite", "gemini-3.6-flash", "gemini-3.8-flash", "gemini-2.5-flash",
                       "gemini-3.8-flash-tts", "text-embedding-004"]
        self.behaviour: dict[str, str] = {}      # model -> "ok" | "404" | "429-zero" | "429" | "500" | "badjson"
        self.reject_fields: set[str] = set()     # e.g. {"responseFormat", "thinkingConfig"}
        self.triage_override: str | None = "P3-if-wire"  # simulate a model under-prioritising a hazard
        self.requests: list[dict] = []
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.port = self._server.server_address[1]
        self.base_url = f"http://127.0.0.1:{self.port}/v1beta"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()

    # ------------------------------------------------------------- answers
    def answer(self, system: str, prompt: str) -> dict:
        if "INTAKE AGENT" in system:
            m = re.search(r"<<<\n(.*)\n>>>", prompt, re.S)
            text = m.group(1) if m else prompt
            r = offline.analyze(text)
            return {
                "is_grievance": r["is_grievance"], "category": r["category"], "title": r["title"][:60],
                "summary_en": "[mock] " + r["summary_en"], "summary_ta": r["summary_ta"],
                "language": r["language"], "ward": r["ward"], "location": r["location"],
                "needs_followup": r["needs_followup"], "followup_en": "Which street is this in?",
                "followup_ta": "இது எந்த தெருவில் உள்ளது?", "urgency_signals": r["urgency_signals"],
                "emergency": r["emergency"], "photo_observation": "The photo shows the problem." if "A photo is attached" in prompt else "",
                "reply_en": "" if r["is_grievance"] else "I can only help with panchayat problems.",
                "reply_ta": "" if r["is_grievance"] else "பஞ்சாயத்து பிரச்சனைகளுக்கு மட்டுமே உதவ முடியும்.",
            }
        if "DUPLICATE DETECTION AGENT" in system:
            olds = json.loads(prompt.split("OPEN tickets that look similar:\n", 1)[1])
            new = json.loads(prompt.split("NEW complaint:\n", 1)[1].split("\n\nOPEN tickets", 1)[0])
            new_streets = offline.street_names(new["location"])
            for o in olds:
                if new_streets & offline.street_names(o["location"]):
                    return {"is_duplicate": True, "ticket_id": o["ticket_id"], "confidence": 0.9,
                            "reason": "Same street and same problem."}
            return {"is_duplicate": False, "ticket_id": "", "confidence": 0.2, "reason": "Different place."}
        if "TRIAGE & ROUTING AGENT" in system:
            facts = json.loads(prompt.split("Complaint to triage:\n", 1)[1])
            r = offline.analyze(facts["summary"] + " " + " ".join(facts.get("urgency_signals", [])))
            p = r["priority"]
            if self.triage_override == "P3-if-wire" and "wire" in prompt.lower():
                p = "P3"  # deliberately wrong so the safety guardrail has to step in
            return {"priority": p, "reason": "[mock] " + r["priority_reason"],
                    "officer_instructions": r["officer_instructions"],
                    "citizen_advice_en": r["advice_en"], "citizen_advice_ta": r["advice_ta"]}
        if "ESCALATION AGENT" in system:
            items = json.loads(prompt.split("Overdue tickets:\n", 1)[1])
            return {"notes": [{"ticket_id": i["ticket_id"], "note": f"[mock] {i['ticket_id']} overdue by {i['overdue']}.",
                               "action": "Visit the site today."} for i in items]}
        if "DEVELOPMENT INSIGHTS AGENT" in system:
            return {"headline": "[mock] Drainage in Ward 2 needs a permanent fix.",
                    "priority_works": [{"ward": 2, "work": "Rebuild Kamarajar Street drain", "reason": "Repeated overflow",
                                        "funding": "Finance Commission tied grant", "timeline": "This month"}],
                    "gram_sabha_agenda": ["Approve drain work"], "risks": ["Dengue risk"]}
        return {}

    # ------------------------------------------------------------- server
    def _handler(self):
        mock = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, code: int, payload: dict):
                body = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _error(self, code: int, status: str, message: str, retry: str | None = None):
                err = {"code": code, "message": message, "status": status}
                if retry:
                    err["details"] = [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry}]
                self._send(code, {"error": err})

            def do_GET(self):
                if self.headers.get("x-goog-api-key") != API_KEY:
                    return self._error(400, "INVALID_ARGUMENT", "API key not valid. Please pass a valid API key.")
                if self.path.startswith("/v1beta/models"):
                    return self._send(200, {"models": [
                        {"name": f"models/{m}", "supportedGenerationMethods": ["generateContent", "countTokens"]}
                        for m in mock.models]})
                self._error(404, "NOT_FOUND", "not found")

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                m = re.match(r"/v1beta/models/([^:]+):generateContent", self.path)
                model = m.group(1) if m else ""
                mock.requests.append({"model": model, "body": body, "headers": dict(self.headers)})
                if self.headers.get("x-goog-api-key") != API_KEY:
                    return self._error(400, "INVALID_ARGUMENT", "API key not valid. Please pass a valid API key.")
                gen = body.get("generationConfig", {})
                for field in mock.reject_fields:
                    if field in gen:
                        return self._error(400, "INVALID_ARGUMENT",
                                           f'Invalid JSON payload received. Unknown name "{field}" at \'generation_config\': Cannot find field.')
                behaviour = mock.behaviour.get(model, "ok")
                if model not in mock.models or behaviour == "404":
                    return self._error(404, "NOT_FOUND", f"models/{model} is not found for API version v1beta.")
                if behaviour == "429-zero":
                    return self._error(429, "RESOURCE_EXHAUSTED",
                                       "Quota exceeded for metric: generate_content_free_tier_requests, limit: 0", "0s")
                if behaviour == "429":
                    return self._error(429, "RESOURCE_EXHAUSTED",
                                       "Quota exceeded for metric: generate_content_free_tier_requests, limit: 10", "0.01s")
                if behaviour == "500":
                    return self._error(500, "INTERNAL", "Internal error")
                system = "".join(p.get("text", "") for p in body.get("systemInstruction", {}).get("parts", []))
                parts = body["contents"][0]["parts"]
                prompt = parts[0]["text"]
                data = mock.answer(system, prompt)
                text = "not json at all" if behaviour == "badjson" else json.dumps(data, ensure_ascii=False)
                self._send(200, {
                    "candidates": [{"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": "STOP", "index": 0}],
                    "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 50, "totalTokenCount": 150},
                    "modelVersion": model,
                })

        return Handler
