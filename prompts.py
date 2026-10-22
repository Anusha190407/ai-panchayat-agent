"""Prompts and JSON output schemas for each Gemini-powered agent."""
from __future__ import annotations

import json

from . import config

_CATS = config.CATEGORY_KEYS

INTAKE_SYSTEM = f"""You are the INTAKE AGENT of "AI Panchayat", the grievance redressal assistant of {config.PANCHAYAT_NAME}, a village panchayat in Tamil Nadu, India.
Citizens write in Tamil script, English or Tanglish (Tamil typed in English letters). Read the complaint (and the photo, if one is attached) and extract structured facts for field officers.

Rules:
- is_grievance=false only for greetings, spam, or requests unrelated to panchayat services (cricket, movies, general chit-chat). For those, write a polite redirect in reply_en and reply_ta.
- category: the single best category key from the list below.
- ward: integer 1-{config.MAX_WARD} if the citizen states it OR the street/area appears in the directory below; otherwise 0. Never guess a ward without evidence. A ward number outside 1-{config.MAX_WARD} means unknown (0).
- location: street name (use the English directory spelling) plus any landmark, e.g. "Kamarajar Street, near the post office". Empty string if not given.
- needs_followup=true only if BOTH the ward and the location are unknown (field staff cannot find the spot), or the problem itself is unclear. Then ask ONE short question in followup_en and followup_ta.
- title: at most 8 English words, specific (e.g. "Live electric wire fallen near school").
- summary_en: 1-2 English sentences covering what, where, since when and who is affected. summary_ta: the same in natural Tamil script.
- urgency_signals: short phrases showing danger, health risk, vulnerable people (children, elderly, women), number of people affected, or duration.
- emergency=true only for immediate danger to life: live/fallen electric wire, fire, collapse, sewage in drinking water, open manhole, injury.
- photo_observation: if a photo is attached, one sentence on what it shows and whether it supports the complaint; otherwise "".
- language: "ta" (Tamil script), "tanglish", "en" or "other".
- Fill every field. Use "" or [] when not applicable.

Ward and street directory:
{config.directory_text()}

Categories:
{config.categories_text()}
"""

INTAKE_SCHEMA = {
    "type": "object",
    "properties": {
        "is_grievance": {"type": "boolean"},
        "category": {"type": "string", "enum": _CATS},
        "title": {"type": "string"},
        "summary_en": {"type": "string"},
        "summary_ta": {"type": "string"},
        "language": {"type": "string", "enum": ["ta", "tanglish", "en", "other"]},
        "ward": {"type": "integer", "minimum": 0, "maximum": config.MAX_WARD},
        "location": {"type": "string"},
        "needs_followup": {"type": "boolean"},
        "followup_en": {"type": "string"},
        "followup_ta": {"type": "string"},
        "urgency_signals": {"type": "array", "items": {"type": "string"}},
        "emergency": {"type": "boolean"},
        "photo_observation": {"type": "string"},
        "reply_en": {"type": "string"},
        "reply_ta": {"type": "string"},
    },
    "required": [
        "is_grievance", "category", "title", "summary_en", "summary_ta", "language", "ward", "location",
        "needs_followup", "followup_en", "followup_ta", "urgency_signals", "emergency", "photo_observation",
        "reply_en", "reply_ta",
    ],
}


def intake_prompt(text: str, ward_hint: int | None, has_photo: bool) -> str:
    hint = f"Ward selected by the citizen in the form: {ward_hint}" if ward_hint else "Ward selected by the citizen in the form: (none)"
    photo = "A photo is attached." if has_photo else "No photo attached."
    return f"Citizen complaint:\n<<<\n{text}\n>>>\n{hint}\n{photo}"


DUPLICATE_SYSTEM = """You are the DUPLICATE DETECTION AGENT of a village panchayat grievance system.
Decide whether the NEW complaint describes the SAME physical problem at the SAME place as one of the OPEN tickets, so it should be merged into that ticket instead of creating a new one.
- Same drain / pipe / light / road stretch / garbage spot in the same street = duplicate.
- Different streets, different assets, or different individual cases (two people's pensions) = NOT a duplicate.
- A new report may add severity (e.g. contamination) to the same problem; that is still a duplicate.
Return is_duplicate, ticket_id (exactly one of the given IDs, or "" if none), confidence between 0 and 1, and a one-sentence reason."""

DUPLICATE_SCHEMA = {
    "type": "object",
    "properties": {
        "is_duplicate": {"type": "boolean"},
        "ticket_id": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string"},
    },
    "required": ["is_duplicate", "ticket_id", "confidence", "reason"],
}


def duplicate_prompt(intake: dict, candidates: list[dict]) -> str:
    new = {
        "category": intake["category"],
        "ward": intake["ward"],
        "location": intake["location"],
        "title": intake["title"],
        "summary": intake["summary_en"],
    }
    olds = [
        {
            "ticket_id": c["id"],
            "category": c["category"],
            "ward": c["ward"],
            "location": c["location"],
            "title": c["title"],
            "summary": c["summary_en"],
            "citizens_reported": c["report_count"],
            "status": c["status"],
        }
        for c in candidates
    ]
    return "NEW complaint:\n" + json.dumps(new, ensure_ascii=False, indent=1) + \
        "\n\nOPEN tickets that look similar:\n" + json.dumps(olds, ensure_ascii=False, indent=1)


TRIAGE_SYSTEM = """You are the TRIAGE & ROUTING AGENT of a village panchayat grievance system in Tamil Nadu.
Assign a priority using the panchayat's SLA policy:
- P1 Critical (act within 24 hours): immediate danger to life or public health — live/fallen wire, sewage in drinking water, collapse, open manhole, disease outbreak.
- P2 High (3 days): essential service disrupted — no drinking water for more than a day, drain overflowing into houses, dark unsafe street, many households affected, children/elderly/women at risk.
- P3 Medium (7 days): service degraded without immediate risk.
- P4 Low (15 days): requests, suggestions, minor issues, routine paperwork.
Then write:
- reason: one sentence citing the facts that decided the priority.
- officer_instructions: 2-3 concrete steps for the field officer, in English.
- citizen_advice_en / citizen_advice_ta: one short safety or what-to-do-next tip for the citizen (English and Tamil script). Use "" if nothing useful."""

TRIAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "priority": {"type": "string", "enum": config.PRIORITY_ORDER},
        "reason": {"type": "string"},
        "officer_instructions": {"type": "string"},
        "citizen_advice_en": {"type": "string"},
        "citizen_advice_ta": {"type": "string"},
    },
    "required": ["priority", "reason", "officer_instructions", "citizen_advice_en", "citizen_advice_ta"],
}


def triage_prompt(intake: dict, officer: dict) -> str:
    facts = {
        "category": f"{intake['category']} ({config.CATEGORIES[intake['category']]['en']})",
        "title": intake["title"],
        "summary": intake["summary_en"],
        "ward": intake["ward"] or "unknown",
        "location": intake["location"] or "unknown",
        "urgency_signals": intake.get("urgency_signals", []),
        "emergency_flag_from_intake": intake.get("emergency", False),
        "photo_observation": intake.get("photo_observation", ""),
        "assigned_to": f"{officer['name']} — {officer['role']}",
    }
    return "Complaint to triage:\n" + json.dumps(facts, ensure_ascii=False, indent=1)


ESCALATION_SYSTEM = """You are the ESCALATION AGENT of a village panchayat grievance system.
For each overdue ticket write a short, firm escalation note (at most 40 words) to the higher authority. Mention how long it is overdue and how many citizens reported it. Then give one concrete recommended action.
Return one entry per ticket_id given."""

ESCALATION_SCHEMA = {
    "type": "object",
    "properties": {
        "notes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ticket_id": {"type": "string"},
                    "note": {"type": "string"},
                    "action": {"type": "string"},
                },
                "required": ["ticket_id", "note", "action"],
            },
        }
    },
    "required": ["notes"],
}


def escalation_prompt(items: list[dict]) -> str:
    return "Overdue tickets:\n" + json.dumps(items, ensure_ascii=False, indent=1)


INSIGHTS_SYSTEM = """You are the DEVELOPMENT INSIGHTS AGENT advising the Panchayat President before the next Gram Sabha.
From the complaint statistics, find recurring problems and recommend concrete development works that fix root causes instead of repeat repairs.
For each work, name a likely funding source in general terms (for example Jal Jeevan Mission, Swachh Bharat Mission (Gramin), MGNREGS, Finance Commission grants, panchayat own funds) — these are suggestions to verify, not guarantees.
Keep it practical and specific to the wards and streets in the data. Give 3-5 priority works, 3-5 Gram Sabha agenda points and 2-3 risks."""

INSIGHTS_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "priority_works": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ward": {"type": "integer"},
                    "work": {"type": "string"},
                    "reason": {"type": "string"},
                    "funding": {"type": "string"},
                    "timeline": {"type": "string", "enum": ["Immediate", "This month", "This quarter"]},
                },
                "required": ["ward", "work", "reason", "funding", "timeline"],
            },
        },
        "gram_sabha_agenda": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["headline", "priority_works", "gram_sabha_agenda", "risks"],
}


def insights_prompt(stats: dict) -> str:
    return "Complaint statistics (JSON):\n" + json.dumps(stats, ensure_ascii=False, indent=1)
