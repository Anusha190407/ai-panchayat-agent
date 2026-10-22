"""Escalation Agent: watches every SLA deadline and acts on its own.

* Overdue tickets climb the escalation chain (Secretary, then President, then
  BDO), one level at a time: each authority gets 24 hours before it goes higher.
* Tickets that have used 75% of their time get a reminder to the officer.
* Gemini drafts the escalation notes (offline template if unavailable).
"""
from __future__ import annotations

from . import config, prompts
from .agents import OFFLINE, Ctx, _str, _two
from .db import iso, parse_ts
from .llm import LLMError

REMINDER_GAP_HOURS = 12


def _fmt_hours(h: float) -> str:
    if h >= 48:
        return f"{h / 24:.1f} days"
    return f"{h:.0f} hours"


def run_sweep(ctx: Ctx) -> dict:
    store = ctx.store
    now = store.now()
    tickets = store.open_tickets()
    breached, reminders = [], []
    for t in tickets:
        created, due = parse_ts(t["created_at"]), parse_ts(t["sla_due_at"])
        if now > due:
            overdue_h = (now - due).total_seconds() / 3600
            level = t["escalation_level"]
            last = parse_ts(t["escalated_at"]) if t.get("escalated_at") else None
            waited = last is None or (now - last).total_seconds() >= config.ESCALATION_STEP_HOURS * 3600
            if level < len(config.ESCALATION_CHAIN) and (level == 0 or waited):
                breached.append((t, level + 1, overdue_h))
        else:
            total = (due - created).total_seconds()
            used = (now - created).total_seconds() / total if total > 0 else 0
            if used >= 0.75:
                last = store.last_event_time(t["id"], "SLA reminder sent")
                if not last or (now - last).total_seconds() >= REMINDER_GAP_HOURS * 3600:
                    reminders.append((t, (due - now).total_seconds() / 3600))

    notes, engine, fallback, tokens = {}, OFFLINE, "", 0
    if breached and ctx.llm_on():
        items = [{
            "ticket_id": t["id"],
            "title": t["title"],
            "priority": t["priority"],
            "ward": t["ward"],
            "location": t["location"],
            "overdue": _fmt_hours(h),
            "citizens_reported": t["report_count"],
            "assigned_officer": config.officer_for(t["category"])["name"],
            "escalating_to": config.escalation_target(level, t["officer_id"])["role"],
        } for t, level, h in breached[:10]]
        try:
            res = ctx.llm.generate_json(system=prompts.ESCALATION_SYSTEM, prompt=prompts.escalation_prompt(items),
                                        schema=prompts.ESCALATION_SCHEMA)
            for n in res.data.get("notes", []) or []:
                if isinstance(n, dict) and _str(n.get("ticket_id")):
                    notes[_str(n["ticket_id"])] = (_str(n.get("note")), _str(n.get("action")))
            engine, tokens = res.model, res.tokens
        except LLMError as exc:
            fallback = str(exc)

    escalated = []
    for t, level, overdue_h in breached:
        officer = config.officer_for(t["category"])
        target = config.escalation_target(level, t["officer_id"])
        note, action = notes.get(t["id"], ("", ""))
        if not note:
            note = (f"{t['title']} (Ward {t['ward'] or '?'}) is {_fmt_hours(overdue_h)} past its deadline with "
                    f"{t['report_count']} citizen(s) waiting. {officer['name']}, {officer['role']}, has not closed it.")
        if not action:
            action = "Call the assigned officer today and fix a new completion date."
        store.update_ticket(t["id"], escalation_level=level, escalated_at=iso(now), status="ESCALATED", updated_at=iso(now))
        store.add_event(t["id"], "Escalation Agent", f"Escalated to level {level}",
                        f"{target['name']} ({target['role']}): {note} Action: {action}")
        store.add_notification(t["id"], "WhatsApp (simulated)", f"{target['name']} ({target['role']})",
                               f"🚨 Escalation L{level}: {note}\nRecommended action: {action}", "en")
        for r in store.reports_for(t["id"]):
            en = (f"Your complaint {t['id']} is taking longer than promised. It has been escalated to the "
                  f"{target['role']} for faster action.")
            ta = (f"உங்கள் புகார் {t['id']} தாமதமாகிறது. விரைவான நடவடிக்கைக்காக இது உயர் அலுவலர் "
                  f"{target['role_ta']} அவர்களிடம் அனுப்பப்பட்டுள்ளது.")
            store.add_notification(t["id"], "SMS (simulated)", r["citizen_name"] or "Citizen", _two(r["language"], en, ta),
                                   r["language"])
        escalated.append({
            "ticket_id": t["id"], "title": t["title"], "priority": t["priority"], "ward": t["ward"],
            "overdue": _fmt_hours(overdue_h), "level": level, "to": target, "note": note, "action": action,
            "citizens": t["report_count"],
        })

    reminded = []
    for t, hours_left in reminders:
        officer = config.officer_for(t["category"])
        msg = f"⏰ Reminder: {t['id']} “{t['title']}” is due in {_fmt_hours(max(0, hours_left))}. Please update the status."
        store.add_notification(t["id"], "WhatsApp (simulated)", f"{officer['name']} ({officer['role']})", msg, "en")
        store.add_event(t["id"], "Escalation Agent", "SLA reminder sent", msg)
        reminded.append({"ticket_id": t["id"], "title": t["title"], "due_in": _fmt_hours(max(0, hours_left)),
                         "officer": officer["name"]})

    summary = f"Checked {len(tickets)} open complaints: {len(escalated)} escalated, {len(reminded)} reminders sent"
    return {
        "ran_at": iso(now),
        "checked": len(tickets),
        "escalated": escalated,
        "reminders": reminded,
        "engine": engine,
        "tokens": tokens,
        "fallback": fallback,
        "summary": summary,
    }
