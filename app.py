"""AI Panchayat — agentic grievance redressal platform (Flask web app + JSON API).

Run locally:   python app.py            → http://localhost:7860
Production:    gunicorn app:app -b 0.0.0.0:7860 --workers 1 --threads 8 --timeout 120
Set GEMINI_API_KEY to use Gemini; without it the agents run on the offline engine.
"""
from __future__ import annotations

import base64
import binascii
import json
import os
import tempfile
import threading
import time
from collections import defaultdict, deque

from flask import Flask, Response, abort, jsonify, render_template, request, stream_with_context
from werkzeug.exceptions import HTTPException

from panchayat import agents, config, escalation, insights, scenarios, seed
from panchayat.agents import COMPLAINT_GRAPH, Ctx
from panchayat.db import Store, iso, parse_ts
from panchayat.llm import GeminiClient

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_IMAGE_BYTES = 4 * 1024 * 1024


def _load_dotenv(path: str) -> None:
    """Minimal .env support for local runs (never overrides real environment variables)."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _db_path() -> str:
    candidates = [os.getenv("DB_PATH") or os.path.join(BASE_DIR, "data", "panchayat.db"),
                  os.path.join(tempfile.gettempdir(), "ai-panchayat.db")]
    for path in candidates:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8"):
                pass
            return path
        except OSError:
            continue
    raise RuntimeError("No writable location for the database")


class RateLimiter:
    """Per-IP sliding-window limits so a public demo can't burn the Gemini quota."""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.hits: dict[tuple, deque] = defaultdict(deque)
        self.lock = threading.Lock()

    def check(self, bucket: str, limit: int, window: int) -> None:
        if not self.enabled:
            return
        ip = (request.headers.get("X-Forwarded-For") or request.remote_addr or "?").split(",")[0].strip()
        now = time.time()
        with self.lock:
            q = self.hits[(bucket, ip)]
            while q and now - q[0] > window:
                q.popleft()
            if len(q) >= limit:
                abort(429, description="Too many requests from your connection — please wait a few minutes and try again.")
            q.append(now)


def _mask_phone(phone: str) -> str:
    digits = "".join(ch for ch in phone or "" if ch.isdigit())
    return f"••••••{digits[-4:]}" if len(digits) >= 4 else ""


def _ticket_view(t: dict, now) -> dict:
    due = parse_ts(t["sla_due_at"])
    is_open = t["status"] in config.OPEN_STATUSES
    officer = config.officer_for(t["category"])
    return {
        **t,
        "emergency": bool(t["emergency"]),
        "category_label": config.CATEGORIES[t["category"]]["en"],
        "category_icon": config.CATEGORIES[t["category"]]["icon"],
        "priority_label": config.PRIORITIES[t["priority"]]["label"],
        "status_label": config.STATUSES.get(t["status"], t["status"]),
        "officer": officer,
        "overdue": bool(is_open and due < now),
        "due_in_hours": round((due - now).total_seconds() / 3600, 1) if is_open else None,
    }


def create_app(db_path: str | None = None, llm: GeminiClient | None = None, rate_limits: bool = True) -> Flask:
    _load_dotenv(os.path.join(BASE_DIR, ".env"))
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
    app.json.ensure_ascii = False
    store = Store(db_path or _db_path())
    if store.count_tickets() == 0:
        seed.seed(store)
    llm = llm if llm is not None else GeminiClient()
    ctx = Ctx(store=store, llm=llm)
    limiter = RateLimiter(enabled=rate_limits)
    app.extensions["panchayat"] = {"store": store, "llm": llm, "ctx": ctx}

    def ndjson(events) -> Response:
        def gen():
            try:
                for ev in events:
                    yield json.dumps(ev, ensure_ascii=False, default=str) + "\n"
            except Exception:  # keep the stream well-formed; details go to the server log
                app.logger.exception("Streaming request failed")
                yield json.dumps({"type": "error", "message": "The agent hit an unexpected error. Please try again."}) + "\n"
        return Response(stream_with_context(gen()), mimetype="application/x-ndjson",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    def complaint_input() -> dict:
        data = request.get_json(silent=True) or {}
        text = str(data.get("text") or "").strip()
        if not text:
            abort(400, description="Please describe the problem.")
        if len(text) > agents.MAX_TEXT:
            abort(400, description=f"Please keep the complaint under {agents.MAX_TEXT} characters.")
        images = []
        image = data.get("image")
        if image:
            mime = str((image or {}).get("mime", "")).lower()
            b64 = str((image or {}).get("data", ""))
            if mime not in ALLOWED_IMAGE_TYPES:
                abort(400, description="Photo must be a JPEG, PNG or WebP image.")
            try:
                raw = base64.b64decode(b64, validate=True)
            except (binascii.Error, ValueError):
                abort(400, description="Photo data is not valid base64.")
            if len(raw) > MAX_IMAGE_BYTES:
                abort(400, description="Photo is too large (max 4 MB).")
            images.append((mime, b64))
        return {
            "text": text,
            "name": str(data.get("name") or ""),
            "phone": str(data.get("phone") or ""),
            "ward_hint": data.get("ward"),
            "images": images,
            "followup_context": str(data.get("followup_context") or ""),
        }

    # ---------------------------------------------------------------- pages
    @app.get("/")
    def index():
        return render_template("index.html", panchayat=config.PANCHAYAT_NAME, panchayat_ta=config.PANCHAYAT_NAME_TA)

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    # ------------------------------------------------------------- status
    @app.get("/api/status")
    def status():
        tickets = store.list_tickets()
        now = store.now()
        return {
            "llm": llm.status(),
            "panchayat": {"name": config.PANCHAYAT_NAME, "name_ta": config.PANCHAYAT_NAME_TA},
            "clock": {"offset_hours": store.clock_offset_hours(), "now": iso(now)},
            "counts": {
                "total": len(tickets),
                "open": sum(1 for t in tickets if t["status"] in config.OPEN_STATUSES),
                "critical": sum(1 for t in tickets if t["priority"] == "P1" and t["status"] in config.OPEN_STATUSES),
                "overdue": sum(1 for t in tickets if t["status"] in config.OPEN_STATUSES and parse_ts(t["sla_due_at"]) < now),
                "resolved": sum(1 for t in tickets if t["status"] == "RESOLVED"),
                "citizens": sum(t["report_count"] for t in tickets),
            },
        }

    @app.get("/api/config")
    def public_config():
        return config.public_config()

    @app.get("/api/graph")
    def graph():
        return {"graph": COMPLAINT_GRAPH.describe(), "mermaid": COMPLAINT_GRAPH.to_mermaid()}

    # --------------------------------------------------------- complaints
    @app.post("/api/complaints")
    def complaint():
        limiter.check("complaint", 30, 600)
        inp = complaint_input()
        return jsonify(agents.run_complaint(ctx, inp.pop("text"), **inp))

    @app.post("/api/complaints/stream")
    def complaint_stream():
        limiter.check("complaint", 30, 600)
        inp = complaint_input()
        state = agents.initial_state(inp.pop("text"), **inp)

        def events():
            yield from COMPLAINT_GRAPH.stream(state, ctx)
            yield {"type": "result", "result": agents.result_from_state(state)}
        return ndjson(events())

    # ------------------------------------------------------------ tickets
    @app.get("/api/tickets")
    def tickets():
        a = request.args
        items = store.list_tickets(ward=a.get("ward") or None, category=a.get("category") or None,
                                   status=a.get("status") or None, priority=a.get("priority") or None,
                                   q=(a.get("q") or "").strip() or None)
        now = store.now()
        return {"tickets": [_ticket_view(t, now) for t in items], "now": iso(now)}

    @app.get("/api/tickets/<tid>")
    def ticket(tid: str):
        t = store.get_ticket(tid.strip().upper())
        if not t:
            abort(404, description="No ticket with that number.")
        reports = [{**r, "phone": _mask_phone(r["phone"])} for r in store.reports_for(t["id"])]
        return {
            "ticket": _ticket_view(t, store.now()),
            "reports": reports,
            "events": store.events_for(t["id"]),
            "notifications": store.notifications_for(t["id"]),
        }

    @app.post("/api/tickets/<tid>/status")
    def ticket_status(tid: str):
        limiter.check("officer", 60, 600)
        data = request.get_json(silent=True) or {}
        status_value = str(data.get("status") or "").upper()
        if status_value not in config.STATUSES:
            abort(400, description="Unknown status.")
        try:
            agents.update_status(ctx, tid.strip().upper(), status_value, str(data.get("note") or "")[:500],
                                 actor=str(data.get("actor") or "Officer")[:60])
        except KeyError:
            abort(404, description="No ticket with that number.")
        return ticket(tid)

    # ------------------------------------------------- escalation & clock
    @app.post("/api/clock")
    def clock():
        data = request.get_json(silent=True) or {}
        if data.get("reset"):
            store.set_clock_offset(0)
        else:
            try:
                hours = float(data.get("advance_hours", 0))
            except (TypeError, ValueError):
                abort(400, description="advance_hours must be a number.")
            hours = max(0.0, min(hours, 240.0))
            store.set_clock_offset(min(720.0, store.clock_offset_hours() + hours))
        return {"offset_hours": store.clock_offset_hours(), "now": iso(store.now())}

    @app.post("/api/escalation/sweep")
    def sweep():
        limiter.check("sweep", 20, 600)
        return escalation.run_sweep(ctx)

    @app.get("/api/escalation/log")
    def escalation_log():
        return {"events": store.recent_events(limit=40, actor_like="Escalation")}

    # ------------------------------------------------------------ insights
    @app.get("/api/insights")
    def insight_stats():
        return insights.compute_stats(ctx)

    @app.post("/api/insights/brief")
    def insight_brief():
        limiter.check("brief", 10, 600)
        return insights.development_brief(ctx)

    # ------------------------------------------------------------ test lab
    @app.get("/api/scenarios")
    def scenario_list():
        return {"scenarios": scenarios.scenario_list()}

    @app.post("/api/scenarios/run")
    def scenario_run():
        limiter.check("tests", 6, 600)
        data = request.get_json(silent=True) or {}
        ids = [str(i) for i in data.get("ids") or []] or None
        use_llm = data.get("mode", "auto") != "offline"
        pause = 1.0 if (use_llm and llm.available()) else 0.0

        def events():
            yield {"type": "start", "engine": llm.status()["mode"] if use_llm else "offline"}
            for result in scenarios.run_many(ids, llm=llm, use_llm=use_llm, pause=pause):
                yield {"type": "result", "result": result}
            yield {"type": "done"}
        return ndjson(events())

    # --------------------------------------------------------------- admin
    @app.post("/api/admin/reset")
    def reset():
        limiter.check("reset", 5, 600)
        store.wipe()
        seed.seed(store)
        return {"ok": True}

    @app.errorhandler(HTTPException)
    def http_error(exc: HTTPException):
        if request.path.startswith("/api/"):
            return jsonify({"error": exc.description}), exc.code
        return exc

    return app


app = create_app()

if __name__ == "__main__":
    port = int(os.getenv("PORT", "7860"))
    app.run(host="0.0.0.0", port=port, threaded=True, debug=False)
