"""The complaint-handling agents and the graph that connects them.

Flow:  Intake → (follow-up | reject | duplicate search) → Duplicate check →
       (merge into existing ticket | Triage & routing → create ticket) → Notify
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from . import config, offline, prompts, textsim
from .db import Store, iso, parse_ts, sla_due
from .graph import END, AgentGraph
from .llm import GeminiClient, LLMError

OFFLINE = "offline rules"
TOOL = "tool"
LANGUAGE_NAMES = {"ta": "Tamil", "en": "English", "tanglish": "Tanglish", "other": "another language"}
STATUS_ACTIONS = {"IN_PROGRESS": "Work started", "RESOLVED": "Marked resolved", "OPEN": "Reopened", "ESCALATED": "Marked escalated"}


@dataclass
class Ctx:
    store: Store
    llm: GeminiClient | None = None
    use_llm: bool = True

    def llm_on(self) -> bool:
        return bool(self.use_llm and self.llm is not None and self.llm.available())


def _entry(summary: str, engine: str, *, details: dict | None = None, ms: int | None = None,
           tokens: int = 0, fallback: str = "") -> dict:
    e = {"summary": summary, "engine": engine, "details": details or {}, "tokens": tokens}
    if ms is not None:
        e["ms"] = ms
    if fallback:
        e["fallback"] = fallback
    return e


def _str(v, default: str = "") -> str:
    return v.strip() if isinstance(v, str) else default


def _bool(v, default: bool = False) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("true", "yes", "1")
    return default


def _int(v, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


# =============================================================== Intake agent
def _normalize_intake(raw: dict, rule: dict, ward_hint: int | None) -> dict:
    cat = _str(raw.get("category")).upper()
    if cat not in config.CATEGORIES:
        cat = rule["category"]
    ward = _int(raw.get("ward"))
    if ward not in config.WARDS:
        ward = 0
    location = _str(raw.get("location"))
    # Directory lookups are exact: trust them when the model left gaps.
    if not location and rule["location"]:
        location = rule["location"]
    if not ward:
        ward = offline.street_ward(location) or rule["ward"] or (ward_hint if ward_hint in config.WARDS else 0)
    is_grievance = _bool(raw.get("is_grievance"), True)
    if not is_grievance and sum(rule["category_scores"].values()) >= 3:
        is_grievance = True  # guardrail against wrongly rejecting a clear complaint
    signals = raw.get("urgency_signals") if isinstance(raw.get("urgency_signals"), list) else []
    signals = [_str(s) for s in signals if _str(s)][:6]
    emergency = _bool(raw.get("emergency")) or rule["emergency"]
    title = _str(raw.get("title")) or rule["title"]
    needs = (not ward and not location) or (_bool(raw.get("needs_followup")) and cat == "OTHER")
    return {
        "is_grievance": is_grievance,
        "category": cat,
        "title": title[:90],
        "summary_en": _str(raw.get("summary_en")) or rule["summary_en"],
        "summary_ta": _str(raw.get("summary_ta")) or rule["summary_ta"],
        "language": _str(raw.get("language")) if _str(raw.get("language")) in ("ta", "tanglish", "en", "other") else rule["language"],
        "ward": ward,
        "location": location,
        "needs_followup": bool(is_grievance and needs),
        "followup_en": _str(raw.get("followup_en")) or offline.FOLLOWUP_EN,
        "followup_ta": _str(raw.get("followup_ta")) or offline.FOLLOWUP_TA,
        "urgency_signals": signals or rule["urgency_signals"],
        "emergency": emergency,
        "photo_observation": _str(raw.get("photo_observation")),
        "reply_en": _str(raw.get("reply_en")),
        "reply_ta": _str(raw.get("reply_ta")),
    }


def _rule_intake(rule: dict) -> dict:
    return {
        "is_grievance": rule["is_grievance"],
        "category": rule["category"],
        "title": rule["title"],
        "summary_en": rule["summary_en"],
        "summary_ta": rule["summary_ta"],
        "language": rule["language"],
        "ward": rule["ward"],
        "location": rule["location"],
        "needs_followup": rule["needs_followup"],
        "followup_en": rule["followup_en"],
        "followup_ta": rule["followup_ta"],
        "urgency_signals": rule["urgency_signals"],
        "emergency": rule["emergency"],
        "photo_observation": "",
        "reply_en": "",
        "reply_ta": "",
    }


def intake_node(state: dict, ctx: Ctx) -> dict:
    text = state["text"]
    ward_hint = state.get("ward_hint")
    images = state.get("images") or []
    rule = offline.analyze(text, ward_hint=ward_hint)
    intake, engine, tokens, ms, fallback = None, OFFLINE, 0, None, ""
    if ctx.llm_on():
        try:
            res = ctx.llm.generate_json(
                system=prompts.INTAKE_SYSTEM,
                prompt=prompts.intake_prompt(text, ward_hint, bool(images)),
                schema=prompts.INTAKE_SCHEMA,
                images=images,
            )
            intake = _normalize_intake(res.data, rule, ward_hint)
            engine, tokens, ms = res.model, res.tokens, res.ms
        except LLMError as exc:
            fallback = str(exc)
    elif ctx.use_llm and ctx.llm is not None and ctx.llm.api_key:
        fallback = ctx.llm.status()["note"]
    if intake is None:
        intake = _rule_intake(rule)
    cat = config.CATEGORIES[intake["category"]]
    lang = LANGUAGE_NAMES.get(intake["language"], "another language")
    if not intake["is_grievance"]:
        summary = "Not a panchayat problem, so it gets a polite reply."
    elif intake["needs_followup"]:
        summary = f"Understood a {cat['en'].lower()} problem but not where it is, so I'll ask the citizen."
    else:
        ward = f"Ward {intake['ward']}" if intake["ward"] else "ward not known"
        where = intake["location"] or "exact spot to be confirmed"
        summary = f"{cat['icon']} {cat['en']} in {ward}, {where}. Written in {lang}."
    details = {
        "title": intake["title"],
        "category": intake["category"],
        "ward": intake["ward"],
        "location": intake["location"],
        "urgency_signals": intake["urgency_signals"],
        "emergency": intake["emergency"],
    }
    if intake["photo_observation"]:
        details["photo"] = intake["photo_observation"]
    return {
        "intake": intake,
        "rule": rule,
        "trace": [_entry(summary, engine, details=details, ms=ms, tokens=tokens, fallback=fallback)],
    }


def route_after_intake(state: dict) -> str:
    i = state["intake"]
    if not i["is_grievance"]:
        return "reject"
    if i["needs_followup"]:
        return "ask"
    if i["category"] in config.INDIVIDUAL_CATEGORIES:
        return "individual"
    return "search"


# ===================================================== Duplicate search tool
def _related(a: str, b: str) -> bool:
    return a == b or any(a in group and b in group for group in config.RELATED_CATEGORIES)


def find_similar_open_tickets(store: Store, intake: dict, limit: int = 3) -> tuple[list[dict], int]:
    tickets = store.open_tickets()
    new_streets = offline.street_names(intake["location"])
    new_text = f"{intake['title']} {intake['summary_en']} {intake['location']}"
    scored = []
    for t in tickets:
        if intake["ward"] and t["ward"] and t["ward"] != intake["ward"]:
            continue
        same_street = bool(new_streets & offline.street_names(t["location"]))
        if t["category"] != intake["category"] and not (same_street and _related(t["category"], intake["category"])):
            continue
        score = textsim.similarity(new_text, f"{t['title']} {t['summary_en']} {t['location']}")
        if same_street:
            score = min(1.0, score + 0.35)
        if score >= 0.2:
            scored.append({**t, "similarity": round(score, 2), "same_street": same_street})
    scored.sort(key=lambda t: -t["similarity"])
    return scored[:limit], len(tickets)


def search_node(state: dict, ctx: Ctx) -> dict:
    cands, total = find_similar_open_tickets(ctx.store, state["intake"])
    if cands:
        best = cands[0]
        summary = (f"Checked {total} open complaints and found {len(cands)} similar "
                   f"(closest: {best['id']}, similarity {best['similarity']}).")
    else:
        summary = f"Checked {total} open complaints; none match this one."
    details = {"candidates": [{"id": c["id"], "title": c["title"], "score": c["similarity"]} for c in cands]}
    return {"candidates": cands, "trace": [_entry(summary, TOOL, details=details)]}


def route_after_search(state: dict) -> str:
    return "verify" if state.get("candidates") else "new"


# ========================================================== Duplicate agent
def verify_node(state: dict, ctx: Ctx) -> dict:
    intake, cands = state["intake"], state["candidates"]
    ids = {c["id"] for c in cands}
    decision, engine, tokens, ms, fallback = None, OFFLINE, 0, None, ""
    if ctx.llm_on():
        try:
            res = ctx.llm.generate_json(system=prompts.DUPLICATE_SYSTEM,
                                        prompt=prompts.duplicate_prompt(intake, cands),
                                        schema=prompts.DUPLICATE_SCHEMA)
            tid = _str(res.data.get("ticket_id"))
            dup = _bool(res.data.get("is_duplicate")) and tid in ids
            try:
                conf = float(res.data.get("confidence", 0.0))
            except (TypeError, ValueError):
                conf = 0.0
            decision = {"is_duplicate": dup, "ticket_id": tid if dup else "",
                        "confidence": round(max(0.0, min(1.0, conf)), 2),
                        "reason": _str(res.data.get("reason")) or "Model decision."}
            engine, tokens, ms = res.model, res.tokens, res.ms
        except LLMError as exc:
            fallback = str(exc)
    if decision is None:
        best = cands[0]
        dup = (best["same_street"] and best["category"] == intake["category"]) or best["similarity"] >= 0.55
        decision = {
            "is_duplicate": dup,
            "ticket_id": best["id"] if dup else "",
            "confidence": best["similarity"],
            "reason": (f"Same street and category as {best['id']}." if best["same_street"] else
                       f"Text similarity {best['similarity']} with {best['id']}.") if dup
            else "Nearby tickets are about a different problem or place.",
        }
    summary = (f"Same problem as {decision['ticket_id']}. {decision['reason']}" if decision["is_duplicate"]
               else f"This is a new problem. {decision['reason']}")
    return {"duplicate": decision,
            "trace": [_entry(summary, engine, details=decision, ms=ms, tokens=tokens, fallback=fallback)]}


def route_after_verify(state: dict) -> str:
    return "merge" if state["duplicate"]["is_duplicate"] else "new"


# ================================================================ Merge tool
def merge_node(state: dict, ctx: Ctx) -> dict:
    with ctx.store.lock:  # read-modify-write of report_count / priority
        return _merge(state, ctx)


def _merge(state: dict, ctx: Ctx) -> dict:
    store, intake, rule = ctx.store, state["intake"], state["rule"]
    tid = state["duplicate"]["ticket_id"]
    t = store.get_ticket(tid)
    now = store.now()
    count = t["report_count"] + 1
    new_p, reasons = t["priority"], []
    if count in (3, 6) and new_p != "P1":
        new_p = config.bump_priority(new_p)
        reasons.append(f"{count} citizens have now reported it")
    severity = "P1" if (intake["emergency"] or rule["priority"] == "P1") else rule["priority"]
    if config.higher_priority(new_p, severity) != new_p:
        new_p = severity
        reasons.append("the new report shows a more serious problem" +
                       (f" ({', '.join(intake['urgency_signals'][:2])})" if intake["urgency_signals"] else ""))
    updates = {"report_count": count, "updated_at": iso(now)}
    if intake["emergency"]:
        updates["emergency"] = True
    if new_p != t["priority"]:
        created = parse_ts(t["created_at"])
        due = min(parse_ts(t["sla_due_at"]), sla_due(created, new_p))
        updates.update(priority=new_p, sla_due_at=iso(due),
                       priority_reason=f"Raised to {new_p}: " + "; ".join(reasons) + ".")
    store.update_ticket(tid, **updates)
    store.add_report(tid, state["text"], citizen_name=state.get("name", ""), phone=state.get("phone", ""),
                     language=intake["language"], has_photo=bool(state.get("images")),
                     photo_observation=intake.get("photo_observation", ""))
    store.add_event(tid, "Duplicate Agent", "Duplicate report merged",
                    f"Now reported by {count} citizens. {state['duplicate']['reason']}")
    if new_p != t["priority"]:
        store.add_event(tid, "Triage Agent", "Priority raised", f"From {t['priority']} to {new_p}: " + "; ".join(reasons))
    summary = f"Added this citizen to {tid}; {count} citizens have now reported it."
    if new_p != t["priority"]:
        summary += f" Priority raised from {t['priority']} to {new_p}."
    return {
        "ticket_id": tid,
        "outcome": "merged",
        "priority_change": {"from": t["priority"], "to": new_p, "reasons": reasons} if new_p != t["priority"] else None,
        "trace": [_entry(summary, TOOL, details={"ticket_id": tid, "report_count": count, "priority": new_p})],
    }


# ==================================================== Triage & routing agent
def triage_node(state: dict, ctx: Ctx) -> dict:
    intake, rule = state["intake"], state["rule"]
    officer = config.officer_for(intake["category"])
    tri, engine, tokens, ms, fallback = None, OFFLINE, 0, None, ""
    if ctx.llm_on():
        try:
            res = ctx.llm.generate_json(system=prompts.TRIAGE_SYSTEM,
                                        prompt=prompts.triage_prompt(intake, officer),
                                        schema=prompts.TRIAGE_SCHEMA)
            p = _str(res.data.get("priority")).upper()
            tri = {
                "priority": p if p in config.PRIORITIES else rule["priority"],
                "reason": _str(res.data.get("reason")) or rule["priority_reason"],
                "officer_instructions": _str(res.data.get("officer_instructions")) or rule["officer_instructions"],
                "advice_en": _str(res.data.get("citizen_advice_en")),
                "advice_ta": _str(res.data.get("citizen_advice_ta")),
            }
            engine, tokens, ms = res.model, res.tokens, res.ms
        except LLMError as exc:
            fallback = str(exc)
    if tri is None:
        tri = {
            "priority": rule["priority"],
            "reason": rule["priority_reason"],
            "officer_instructions": rule["officer_instructions"],
            "advice_en": rule["advice_en"],
            "advice_ta": rule["advice_ta"],
        }
    # Safety guardrail: deterministic hazard rules set a floor the model cannot go below.
    guardrail = ""
    floor = "P1" if (intake["emergency"] or rule["priority"] == "P1") else None
    if floor and config.higher_priority(tri["priority"], floor) != tri["priority"]:
        guardrail = (f"Safety guardrail raised the priority from {tri['priority']} to {floor} because of: "
                     f"{', '.join(rule['urgency_signals'][:2]) or 'emergency'}.")
        tri["priority"] = floor
    if intake["emergency"] and not tri["advice_en"]:
        tri["advice_en"], tri["advice_ta"] = rule["advice_en"], rule["advice_ta"]
    tri["guardrail"] = guardrail
    tri["officer"] = officer
    tri["sla_hours"] = config.PRIORITIES[tri["priority"]]["sla_hours"]
    p = config.PRIORITIES[tri["priority"]]
    summary = f"{tri['priority']} {p['label']}: act within {p['sla_en']}. Assigned to {officer['name']}, {officer['role']}."
    entries = [_entry(summary, engine, details={"reason": tri["reason"], "officer": officer["name"]},
                      ms=ms, tokens=tokens, fallback=fallback)]
    if guardrail:
        entries.append(_entry(guardrail, "guardrail", details={"floor": floor}, ms=0))
    return {"triage": tri, "trace": entries}


# ======================================================== Create ticket tool
def create_node(state: dict, ctx: Ctx) -> dict:
    store, intake, tri = ctx.store, state["intake"], state["triage"]
    now = store.now()
    engines = sorted({e["engine"] for e in state["trace"] if e["engine"] not in (TOOL, "guardrail")})
    created = store.create_ticket({
        "created_at": iso(now),
        "updated_at": iso(now),
        "status": "OPEN",
        "category": intake["category"],
        "title": intake["title"],
        "summary_en": intake["summary_en"],
        "summary_ta": intake["summary_ta"],
        "ward": intake["ward"],
        "location": intake["location"],
        "priority": tri["priority"],
        "priority_reason": tri["reason"],
        "officer_id": tri["officer"]["id"],
        "officer_instructions": tri["officer_instructions"],
        "sla_due_at": iso(now + timedelta(hours=tri["sla_hours"])),
        "language": intake["language"],
        "emergency": intake["emergency"],
        "engine": ", ".join(engines),
    })
    tid = created["id"]
    store.add_report(tid, state["text"], citizen_name=state.get("name", ""), phone=state.get("phone", ""),
                     language=intake["language"], has_photo=bool(state.get("images")),
                     photo_observation=intake.get("photo_observation", ""))
    store.add_event(tid, "Intake Agent", "Complaint registered", intake["summary_en"])
    store.add_event(tid, "Triage Agent", f"Priority {tri['priority']}", tri["reason"])
    if tri["guardrail"]:
        store.add_event(tid, "Safety guardrail", "Priority enforced", tri["guardrail"])
    store.add_event(tid, "Routing Agent", "Assigned", f"{tri['officer']['name']} — {tri['officer']['role']}")
    return {"ticket_id": tid, "outcome": "created",
            "trace": [_entry(f"Registered ticket {tid}.", TOOL, details={"ticket_id": tid})]}


# ======================================================= Notification agent
def _two(lang: str, en: str, ta: str) -> str:
    parts = [ta, en] if lang == "ta" else [en, ta]
    return "\n\n".join(p for p in parts if p)


EMERGENCY_EN = "⚠️ If anyone is in immediate danger, call 112 now and keep people away from the spot."
EMERGENCY_TA = "⚠️ உடனடி ஆபத்து இருந்தால் உடனே 112 அழைக்கவும்; அந்த இடத்திற்கு யாரும் செல்ல வேண்டாம்."


def citizen_ack(state: dict, t: dict) -> str:
    intake, tri = state["intake"], state.get("triage") or {}
    officer = config.officer_for(t["category"])
    p = config.PRIORITIES[t["priority"]]
    name = state.get("name") or ""
    hello_en = f"Vanakkam{(' ' + name) if name else ''}!"
    hello_ta = f"வணக்கம்{(' ' + name) if name else ''}!"
    if state.get("outcome") == "merged":
        en = (f"{hello_en} This problem was already reported. Your complaint is linked to ticket {t['id']} — "
              f"{t['report_count']} citizens have reported it, so it is handled with {t['priority']} {p['label']} priority "
              f"(action within {p['sla_en']}). Officer: {officer['name']}, {officer['role']}.")
        ta = (f"{hello_ta} இந்த பிரச்சனை ஏற்கனவே பதிவாகியுள்ளது. உங்கள் புகார் {t['id']} என்ற புகாருடன் இணைக்கப்பட்டது — "
              f"இதுவரை {t['report_count']} பேர் புகார் அளித்துள்ளனர். முன்னுரிமை: {p['ta']} ({p['sla_ta']} நடவடிக்கை). "
              f"பொறுப்பு அலுவலர்: {officer['name']}, {officer['role_ta']}.")
    else:
        en = (f"{hello_en} Your complaint is registered.\nTicket: {t['id']}\nIssue: {t['title']}\n"
              f"Priority: {t['priority']} {p['label']} — action within {p['sla_en']}\n"
              f"Officer: {officer['name']}, {officer['role']}")
        ta = (f"{hello_ta} உங்கள் புகார் பதிவு செய்யப்பட்டது.\nபுகார் எண்: {t['id']}\nவிவரம்: {t['summary_ta']}\n"
              f"முன்னுரிமை: {p['ta']} — {p['sla_ta']} நடவடிக்கை\nபொறுப்பு அலுவலர்: {officer['name']}, {officer['role_ta']}")
        if tri.get("advice_en"):
            en += f"\nTip: {tri['advice_en']}"
        if tri.get("advice_ta"):
            ta += f"\nகுறிப்பு: {tri['advice_ta']}"
    if intake.get("emergency") or t.get("emergency"):
        en += "\n" + EMERGENCY_EN
        ta += "\n" + EMERGENCY_TA
    return _two(intake.get("language", "en"), en, ta)


def officer_message(state: dict, t: dict) -> str:
    due = parse_ts(t["sla_due_at"]) + timedelta(hours=5, minutes=30)
    due_txt = due.strftime("%d %b %Y, %I:%M %p IST")
    if state.get("outcome") == "merged":
        msg = f"🔁 {t['id']}: one more citizen reported this (total {t['report_count']})."
        change = state.get("priority_change")
        if change:
            msg += f" Priority raised from {change['from']} to {change['to']}: {'; '.join(change['reasons'])}. New deadline: {due_txt}."
        return msg
    tri = state["triage"]
    return (f"🛠 New work order {t['id']} [{t['priority']}] — {t['title']}\n"
            f"Where: Ward {t['ward'] or '?'}{', ' + t['location'] if t['location'] else ''}\n"
            f"What: {t['summary_en']}\n"
            f"Do: {tri['officer_instructions']}\n"
            f"Resolve by: {due_txt}"
            + ("\n⚠️ EMERGENCY — attend immediately." if t["emergency"] else ""))


def notify_node(state: dict, ctx: Ctx) -> dict:
    store = ctx.store
    t = store.get_ticket(state["ticket_id"])
    officer = config.officer_for(t["category"])
    citizen = citizen_ack(state, t)
    work = officer_message(state, t)
    store.add_notification(t["id"], "SMS (simulated)", state.get("name") or "Citizen", citizen, state["intake"]["language"])
    store.add_notification(t["id"], "WhatsApp (simulated)", f"{officer['name']} ({officer['role']})", work, "en")
    store.add_event(t["id"], "Notification Agent", "Citizen and officer notified", "Bilingual SMS + officer work order")
    return {
        "ticket": t,
        "messages": {"citizen": citizen, "officer": work},
        "trace": [_entry(f"Sent a bilingual SMS to the citizen and a work order to {officer['name']}.", TOOL)],
    }


# ======================================================= Reply-only nodes
def ask_node(state: dict, ctx: Ctx) -> dict:
    i = state["intake"]
    q = {"en": i["followup_en"], "ta": i["followup_ta"]}
    return {"outcome": "needs_info", "question": q,
            "messages": {"citizen": _two(i["language"], q["en"], q["ta"])},
            "trace": [_entry("Asked the citizen where the problem is.", TOOL, details=q)]}


REJECT_EN = ("Vanakkam! I am the AI Panchayat assistant. I can register village problems such as drinking water, roads, "
             "drainage, street lights, garbage, health risks, farming and welfare schemes. Please describe the problem and "
             "where it is (street or ward).")
REJECT_TA = ("வணக்கம்! நான் AI பஞ்சாயத்து உதவியாளர். குடிநீர், சாலை, வடிகால், தெரு விளக்கு, குப்பை, சுகாதாரம், விவசாயம், "
             "நலத்திட்டங்கள் போன்ற பிரச்சனைகளை பதிவு செய்ய உதவுவேன். பிரச்சனையையும் அது உள்ள இடத்தையும் (தெரு அல்லது வார்டு) கூறுங்கள்.")


def reject_node(state: dict, ctx: Ctx) -> dict:
    i = state["intake"]
    en, ta = i.get("reply_en") or REJECT_EN, i.get("reply_ta") or REJECT_TA
    return {"outcome": "rejected", "messages": {"citizen": _two(i["language"], en, ta)},
            "trace": [_entry("Replied politely; nothing was registered.", TOOL)]}


# ================================================================== Graph
def build_complaint_graph() -> AgentGraph:
    g = AgentGraph("complaint_pipeline")
    g.add_node("intake", intake_node, agent="Intake Agent", icon="🧾",
               description="Understands Tamil/English/Tanglish text and photos; extracts category, location, urgency.")
    g.add_node("ask_followup", ask_node, agent="Follow-up", icon="❓", kind="tool",
               description="Asks the citizen one question when the location is missing.")
    g.add_node("reject", reject_node, agent="Polite reply", icon="🙏", kind="tool",
               description="Handles greetings and off-topic messages without creating tickets.")
    g.add_node("search", search_node, agent="Duplicate search", icon="🔎", kind="tool",
               description="Finds similar open tickets in the same ward (text similarity + street match).")
    g.add_node("verify", verify_node, agent="Duplicate Agent", icon="🧠",
               description="Decides if the complaint is the same physical problem as an open ticket.")
    g.add_node("merge", merge_node, agent="Merge", icon="🔁", kind="tool",
               description="Adds the citizen to the existing ticket and raises priority when more people report it.")
    g.add_node("triage", triage_node, agent="Triage & Routing Agent", icon="⚖️",
               description="Sets priority under the SLA policy, writes officer steps and citizen advice; safety guardrail.")
    g.add_node("create", create_node, agent="Create ticket", icon="🎫", kind="tool",
               description="Creates the ticket, SLA deadline and audit trail.")
    g.add_node("notify", notify_node, agent="Notification Agent", icon="📣", kind="tool",
               description="Bilingual SMS to the citizen and a work order to the officer.")
    g.add_router("intake", route_after_intake,
                 {"reject": "reject", "ask": "ask_followup", "individual": "triage", "search": "search"})
    g.add_router("search", route_after_search, {"verify": "verify", "new": "triage"})
    g.add_router("verify", route_after_verify, {"merge": "merge", "new": "triage"})
    g.add_edge("merge", "notify")
    g.add_edge("triage", "create")
    g.add_edge("create", "notify")
    g.add_edge("notify", END)
    g.add_edge("ask_followup", END)
    g.add_edge("reject", END)
    return g


COMPLAINT_GRAPH = build_complaint_graph()

MAX_TEXT = 2000


def initial_state(text: str, *, name: str = "", phone: str = "", ward_hint=None, images=None,
                  followup_context: str = "") -> dict:
    text = (text or "").strip()[:MAX_TEXT]
    if followup_context:
        text = f"{followup_context.strip()[:MAX_TEXT]}\nAdditional details from citizen: {text}"
    try:
        ward_hint = int(ward_hint) if ward_hint not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        ward_hint = None
    return {
        "text": text,
        "name": (name or "").strip()[:60],
        "phone": (phone or "").strip()[:20],
        "ward_hint": ward_hint if ward_hint in config.WARDS else None,
        "images": images or [],
    }


def result_from_state(state: dict) -> dict:
    trace = state.get("trace", [])
    engines = [e["engine"] for e in trace if e["engine"] not in (TOOL, "guardrail")]
    return {
        "outcome": state.get("outcome"),
        "ticket": state.get("ticket"),
        "intake": state.get("intake"),
        "triage": {k: v for k, v in (state.get("triage") or {}).items() if k != "officer"} or None,
        "duplicate": state.get("duplicate"),
        "priority_change": state.get("priority_change"),
        "question": state.get("question"),
        "messages": state.get("messages", {}),
        "trace": trace,
        "path": state.get("path", []),
        "engines": sorted(set(engines)),
        "llm_calls": sum(1 for e in engines if e != OFFLINE),
        "fallbacks": [e["fallback"] for e in trace if e.get("fallback")],
        "total_ms": sum(e.get("ms", 0) or 0 for e in trace),
    }


def run_complaint(ctx: Ctx, text: str, **kwargs) -> dict:
    state = initial_state(text, **kwargs)
    COMPLAINT_GRAPH.run(state, ctx)
    return result_from_state(state)


# ============================================================ Officer actions
def update_status(ctx: Ctx, ticket_id: str, status: str, note: str = "", actor: str = "Officer") -> dict:
    store = ctx.store
    t = store.get_ticket(ticket_id)
    if not t:
        raise KeyError(ticket_id)
    if status not in config.STATUSES:
        raise ValueError("Unknown status")
    now = store.now()
    fields = {"status": status, "updated_at": iso(now)}
    if status == "RESOLVED":
        fields.update(resolved_at=iso(now), resolution_note=note[:500])
    store.update_ticket(ticket_id, **fields)
    store.add_event(ticket_id, actor, STATUS_ACTIONS.get(status, f"Status changed to {config.STATUSES[status]}"), note[:500])
    if status in ("RESOLVED", "IN_PROGRESS"):
        reports = store.reports_for(ticket_id)
        p_en = "is resolved" if status == "RESOLVED" else "is now being worked on"
        p_ta = "சரி செய்யப்பட்டது" if status == "RESOLVED" else "மீது பணி தொடங்கியுள்ளது"
        en = f"Update on {ticket_id} ({t['title']}): the problem {p_en}."
        if status == "RESOLVED":
            en += f" Note: {note}" if note else ""
            en += " If it is not fixed, reply to reopen. Please rate the service 1–5."
        ta = f"புகார் {ticket_id} பற்றிய தகவல்: பிரச்சனை {p_ta}."
        if status == "RESOLVED":
            ta += " சரியாகவில்லை என்றால் பதில் அனுப்பி மீண்டும் திறக்கலாம். சேவையை 1–5 வரை மதிப்பிடுங்கள்."
        for r in reports:
            store.add_notification(ticket_id, "SMS (simulated)", r["citizen_name"] or "Citizen",
                                   _two(r["language"], en, ta), r["language"])
        store.add_event(ticket_id, "Notification Agent", "Citizens updated",
                        f"Bilingual update sent to {len(reports)} citizen(s)")
    return store.get_ticket(ticket_id)
