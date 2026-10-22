"""Development Insights Agent: turns complaint patterns into a Gram Sabha brief."""
from __future__ import annotations

from collections import defaultdict

from . import config, offline, prompts
from .agents import OFFLINE, Ctx, _int, _str
from .db import parse_ts
from .llm import LLMError

WORK_LIBRARY = {
    "WATER_SUPPLY": ("Replace leaking pipeline sections and clean the overhead tank",
                     "Jal Jeevan Mission / Finance Commission tied grant (water)"),
    "DRAINAGE": ("Desilt and rebuild the blocked drain stretch with a proper outlet",
                 "Finance Commission tied grant (sanitation) / SBM-G liquid waste management"),
    "ROADS": ("Patch potholes and resurface the damaged stretch",
              "Panchayat funds / state rural roads scheme / MGNREGS rural connectivity"),
    "STREET_LIGHTS": ("Replace failed fittings with LED lights and repair wiring",
                      "Panchayat own funds / Finance Commission untied grant"),
    "SANITATION": ("Add bins and a fixed daily door-to-door collection route",
                   "Swachh Bharat Mission (Gramin) – solid waste management"),
    "ELECTRICITY": ("Joint inspection with the Electricity Board to fix lines and the transformer",
                    "Electricity Board maintenance (external)"),
    "PUBLIC_HEALTH": ("Fogging, source reduction and a fever screening camp",
                      "NHM / PHC coordination"),
    "AGRICULTURE": ("Desilt the irrigation channel before the next season",
                    "MGNREGS water-conservation works"),
    "WELFARE_SCHEMES": ("Hold a special camp for pension and certificate cases",
                        "Revenue / Social Welfare department camp"),
    "OTHER": ("Review pending general grievances in the Gram Sabha", "Panchayat own funds"),
}


def compute_stats(ctx: Ctx) -> dict:
    store = ctx.store
    tickets = store.list_tickets()
    now = store.now()
    by_cat = defaultdict(lambda: {"tickets": 0, "open": 0, "resolved": 0, "citizens": 0})
    by_ward = defaultdict(lambda: {"tickets": 0, "open": 0, "resolved": 0, "citizens": 0})
    matrix = defaultdict(lambda: defaultdict(int))
    spots = defaultdict(lambda: {"tickets": 0, "citizens": 0, "categories": set(), "ward": 0})
    overdue, resolution_hours = 0, []
    for t in tickets:
        is_open = t["status"] in config.OPEN_STATUSES
        for bucket in (by_cat[t["category"]], by_ward[t["ward"]]):
            bucket["tickets"] += 1
            bucket["citizens"] += t["report_count"]
            bucket["open" if is_open else "resolved"] += 1
        matrix[t["ward"]][t["category"]] += t["report_count"]
        street = next(iter(offline.street_names(t["location"])), "")
        if street:
            s = spots[street]
            s["tickets"] += 1
            s["citizens"] += t["report_count"]
            s["categories"].add(t["category"])
            s["ward"] = t["ward"]
        if is_open and parse_ts(t["sla_due_at"]) < now:
            overdue += 1
        if t["resolved_at"]:
            resolution_hours.append((parse_ts(t["resolved_at"]) - parse_ts(t["created_at"])).total_seconds() / 3600)
    hotspots = sorted(
        ({"street": k, "ward": v["ward"], "tickets": v["tickets"], "citizens": v["citizens"],
          "categories": sorted(v["categories"])} for k, v in spots.items() if v["tickets"] >= 2 or v["citizens"] >= 3),
        key=lambda h: (-h["citizens"], -h["tickets"]),
    )[:8]
    return {
        "total_tickets": len(tickets),
        "open": sum(1 for t in tickets if t["status"] in config.OPEN_STATUSES),
        "resolved": sum(1 for t in tickets if t["status"] == "RESOLVED"),
        "overdue": overdue,
        "critical_open": sum(1 for t in tickets if t["priority"] == "P1" and t["status"] in config.OPEN_STATUSES),
        "citizens_reached": sum(t["report_count"] for t in tickets),
        "avg_resolution_hours": round(sum(resolution_hours) / len(resolution_hours), 1) if resolution_hours else None,
        "by_category": {k: dict(v) for k, v in sorted(by_cat.items(), key=lambda kv: -kv[1]["citizens"])},
        "by_ward": {str(k): dict(v) for k, v in sorted(by_ward.items())},
        "ward_category_citizens": {str(w): dict(c) for w, c in sorted(matrix.items())},
        "hotspots": hotspots,
    }


def _rule_brief(stats: dict) -> dict:
    pairs = []
    for ward, cats in stats["ward_category_citizens"].items():
        for cat, n in cats.items():
            pairs.append((n, int(ward), cat))
    pairs.sort(reverse=True)
    works = []
    for n, ward, cat in pairs[:4]:
        work, funding = WORK_LIBRARY.get(cat, WORK_LIBRARY["OTHER"])
        works.append({
            "ward": ward,
            "work": work,
            "reason": f"{n} citizen reports about {config.CATEGORIES[cat]['en'].lower()} in Ward {ward}.",
            "funding": funding,
            "timeline": "Immediate" if n >= 5 else ("This month" if n >= 3 else "This quarter"),
        })
    top = pairs[0] if pairs else None
    headline = (f"Ward {top[1]} {config.CATEGORIES[top[2]]['en'].lower()} is the biggest recurring problem "
                f"({top[0]} citizen reports)." if top else "Not enough complaints yet for a brief.")
    agenda = [f"Review {stats['overdue']} overdue complaints and fix new deadlines."] if stats["overdue"] else []
    agenda += [f"Approve: {w['work']} (Ward {w['ward']})." for w in works[:3]]
    agenda.append("Share the complaint dashboard with ward members every month.")
    risks = []
    cats = stats["by_category"]
    if "DRAINAGE" in cats or "PUBLIC_HEALTH" in cats:
        risks.append("Blocked drains and stagnant water raise dengue risk during the monsoon.")
    if "WATER_SUPPLY" in cats:
        risks.append("Pipeline leaks can let sewage into drinking water — test water quality in affected streets.")
    if stats["critical_open"]:
        risks.append(f"{stats['critical_open']} critical complaint(s) still open.")
    return {"headline": headline, "priority_works": works, "gram_sabha_agenda": agenda[:5], "risks": risks[:3]}


def development_brief(ctx: Ctx) -> dict:
    stats = compute_stats(ctx)
    brief, engine, fallback, tokens = None, OFFLINE, "", 0
    if ctx.llm_on() and stats["total_tickets"]:
        compact = {k: stats[k] for k in ("total_tickets", "open", "resolved", "overdue", "critical_open",
                                         "citizens_reached", "avg_resolution_hours", "by_category",
                                         "ward_category_citizens", "hotspots")}
        try:
            res = ctx.llm.generate_json(system=prompts.INSIGHTS_SYSTEM, prompt=prompts.insights_prompt(compact),
                                        schema=prompts.INSIGHTS_SCHEMA)
            d = res.data
            works = []
            for w in d.get("priority_works", []) or []:
                if not isinstance(w, dict) or not _str(w.get("work")):
                    continue
                timeline = _str(w.get("timeline"))
                works.append({
                    "ward": _int(w.get("ward")),
                    "work": _str(w.get("work")),
                    "reason": _str(w.get("reason")),
                    "funding": _str(w.get("funding")),
                    "timeline": timeline if timeline in ("Immediate", "This month", "This quarter") else "This month",
                })
            brief = {
                "headline": _str(d.get("headline")),
                "priority_works": works[:6],
                "gram_sabha_agenda": [_str(a) for a in d.get("gram_sabha_agenda", []) or [] if _str(a)][:6],
                "risks": [_str(r) for r in d.get("risks", []) or [] if _str(r)][:4],
            }
            if not brief["headline"] or not brief["priority_works"]:
                brief = None
                fallback = "Gemini brief was incomplete"
            else:
                engine, tokens = res.model, res.tokens
        except LLMError as exc:
            fallback = str(exc)
    if brief is None:
        brief = _rule_brief(stats)
    return {"brief": brief, "stats": stats, "engine": engine, "tokens": tokens, "fallback": fallback,
            "note": "Funding sources are suggestions — verify eligibility with the BDO office."}
