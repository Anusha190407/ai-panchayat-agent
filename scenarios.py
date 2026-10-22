"""Built-in test scenarios. Used by the in-app Agent Test Lab and by pytest.

Each scenario runs on a fresh copy of the demo data in a temporary database,
so results are repeatable and the live demo data is never touched.
"""
from __future__ import annotations

import os
import tempfile
import time
import uuid

from . import config, escalation, seed
from .agents import Ctx, run_complaint
from .db import Store

SCENARIOS = [
    {
        "id": "S1", "name": "English water complaint with ward number",
        "input": {"text": "No drinking water in Periyar Nagar, ward 3 for the last 3 days. The pipeline near the water tank is leaking.", "name": "Selvi"},
        "expect": {"outcome": ["created", "merged"], "category": ["WATER_SUPPLY"], "ward": [3], "priority": ["P1", "P2"]},
    },
    {
        "id": "S2", "name": "Tamil street-light complaint, ward found from the street name",
        "input": {"text": "காமராஜர் தெருவில் தெரு விளக்கு ஒரு வாரமாக எரியவில்லை. இரவில் பெண்கள் நடக்க பயமாக உள்ளது.", "name": "Kavitha"},
        "expect": {"outcome": ["created", "merged"], "category": ["STREET_LIGHTS"], "ward": [2], "language": ["ta"]},
    },
    {
        "id": "S3", "name": "Emergency: live wire on the road gets P1 and safety advice",
        "input": {"text": "Electric wire has fallen on the road near the school in Gandhi Street. Children walk here, very dangerous!"},
        "expect": {"outcome": ["created", "merged"], "category": ["ELECTRICITY", "STREET_LIGHTS"], "priority": ["P1"], "emergency": [True]},
    },
    {
        "id": "S4", "name": "Duplicate drainage report is merged into the open ticket",
        "input": {"text": "Drainage is overflowing on Kamarajar Street, dirty water entering houses."},
        "expect": {"outcome": ["merged"], "merged_into": ["@kamarajar_drain"]},
    },
    {
        "id": "S5", "name": "Vague complaint makes the agent ask where",
        "input": {"text": "Water is not coming properly"},
        "expect": {"outcome": ["needs_info"]},
    },
    {
        "id": "S6", "name": "Follow-up answer completes the complaint",
        "input": {"text": "Water is not coming properly", "followup": "Anna Nagar, near the ration shop"},
        "expect": {"first_outcome": ["needs_info"], "outcome": ["created", "merged"], "category": ["WATER_SUPPLY"], "ward": [3]},
    },
    {
        "id": "S7", "name": "Off-topic message is politely rejected",
        "input": {"text": "Hi, who will win the cricket match today?"},
        "expect": {"outcome": ["rejected"]},
    },
    {
        "id": "S8", "name": "Tanglish garbage complaint",
        "input": {"text": "Market road la kuppai oru vaaram ah edukkala, romba naaththam adikuthu"},
        "expect": {"outcome": ["created", "merged"], "category": ["SANITATION"], "ward": [5]},
    },
    {
        "id": "S9", "name": "Health risk: sewage in drinking water is P1",
        "input": {"text": "Sewage water is mixing with drinking water pipe in Indira Colony. Many people have fever."},
        "expect": {"outcome": ["created", "merged"], "category": ["WATER_SUPPLY", "DRAINAGE", "PUBLIC_HEALTH"], "priority": ["P1"]},
    },
    {
        "id": "S10", "name": "Individual pension case is never merged",
        "input": {"text": "My mother's old age pension has not been credited for 3 months. We live in Thiruvalluvar Street."},
        "expect": {"outcome": ["created"], "category": ["WELFARE_SCHEMES"], "ward": [6]},
    },
    {
        "id": "S11", "name": "Escalation Agent escalates overdue tickets after 3 days",
        "type": "escalation", "fast_forward_hours": 72,
        "expect": {"escalated_min": 1},
    },
]


def _resolve(expected: list, store: Store) -> list:
    out = []
    for v in expected:
        if v == "@kamarajar_drain":
            match = [t["id"] for t in store.list_tickets(category="DRAINAGE") if "Kamarajar" in t["location"]]
            out.extend(match or ["(missing seed ticket)"])
        else:
            out.append(v)
    return out


def _check(field: str, expected: list, actual) -> dict:
    return {"field": field, "expected": expected, "actual": actual, "ok": actual in expected}


def run_one(sc: dict, llm=None, use_llm: bool = True, workdir: str | None = None) -> dict:
    path = os.path.join(workdir or tempfile.gettempdir(), f"ai-panchayat-scenario-{uuid.uuid4().hex}.db")
    store = Store(path)
    seed.seed(store)
    ctx = Ctx(store=store, llm=llm, use_llm=use_llm)
    started = time.perf_counter()
    checks, trace, engines, outcome, error = [], [], set(), None, ""
    try:
        exp = sc["expect"]
        if sc.get("type") == "escalation":
            store.set_clock_offset(sc.get("fast_forward_hours", 72))
            res = escalation.run_sweep(ctx)
            n = len(res["escalated"])
            checks.append({"field": "escalated", "expected": f">= {exp['escalated_min']}", "actual": n,
                           "ok": n >= exp["escalated_min"]})
            outcome = res["summary"]
            engines.add(res["engine"])
            trace = [{"agent": "Escalation Agent", "icon": "⏰", "summary": res["summary"], "engine": res["engine"],
                      "details": {"escalated": [e["ticket_id"] for e in res["escalated"]]}}]
        else:
            inp = sc["input"]
            res = run_complaint(ctx, inp["text"], name=inp.get("name", ""))
            trace = list(res["trace"])
            engines.update(res["engines"])
            if "followup" in inp:
                checks.append(_check("first_outcome", exp["first_outcome"], res["outcome"]))
                res = run_complaint(ctx, inp["followup"], followup_context=inp["text"], name=inp.get("name", ""))
                trace += res["trace"]
                engines.update(res["engines"])
            outcome = res["outcome"]
            ticket = res.get("ticket") or {}
            actual = {
                "outcome": res["outcome"],
                "category": ticket.get("category", (res.get("intake") or {}).get("category")),
                "ward": ticket.get("ward", (res.get("intake") or {}).get("ward")),
                "priority": ticket.get("priority"),
                "language": (res.get("intake") or {}).get("language"),
                "emergency": bool(ticket.get("emergency")) if ticket else bool((res.get("intake") or {}).get("emergency")),
                "merged_into": ticket.get("id") if res["outcome"] == "merged" else None,
            }
            for field in ("outcome", "category", "ward", "priority", "language", "emergency", "merged_into"):
                if field in exp:
                    checks.append(_check(field, _resolve(exp[field], store), actual[field]))
    except Exception as exc:  # a crash is a failed test, not a crashed test lab
        error = f"{exc.__class__.__name__}: {exc}"
    finally:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(path + suffix)
            except OSError:
                pass
    return {
        "id": sc["id"],
        "name": sc["name"],
        "input": (sc.get("input") or {}).get("text", f"Fast-forward {sc.get('fast_forward_hours', 0)} h, run sweep"),
        "passed": bool(checks) and all(c["ok"] for c in checks) and not error,
        "checks": checks,
        "outcome": outcome,
        "engines": sorted(e for e in engines if e),
        "error": error,
        "ms": round((time.perf_counter() - started) * 1000),
        "trace": trace,
    }


def scenario_list() -> list[dict]:
    return [{"id": s["id"], "name": s["name"], "input": (s.get("input") or {}).get("text", ""),
             "expect": s["expect"]} for s in SCENARIOS]


def run_many(ids: list[str] | None = None, llm=None, use_llm: bool = True, pause: float = 0.0):
    chosen = [s for s in SCENARIOS if not ids or s["id"] in ids]
    for i, sc in enumerate(chosen):
        if i and pause:
            time.sleep(pause)
        yield run_one(sc, llm=llm, use_llm=use_llm)


__all__ = ["SCENARIOS", "run_one", "run_many", "scenario_list", "config"]
