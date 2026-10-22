"""Static configuration for the AI Panchayat demo.

All names, streets and officers below are fictional sample data for a demo
village panchayat. Replace them with a real panchayat's directory to go live.
"""
from __future__ import annotations

import os

APP_NAME = "AI Panchayat"
PANCHAYAT_NAME = os.getenv("PANCHAYAT_NAME", "Demo Village Panchayat")
PANCHAYAT_NAME_TA = os.getenv("PANCHAYAT_NAME_TA", "மாதிரி கிராம ஊராட்சி")

# ---------------------------------------------------------------------------
# Complaint categories
# ---------------------------------------------------------------------------
CATEGORIES: dict[str, dict] = {
    "WATER_SUPPLY": {
        "short": "Water",
        "en": "Drinking Water Supply",
        "ta": "குடிநீர் விநியோகம்",
        "icon": "💧",
        "hint": "no water, low pressure, pipeline leak, borewell/motor, overhead tank, contaminated drinking water",
    },
    "ROADS": {
        "short": "Roads",
        "en": "Roads & Potholes",
        "ta": "சாலைகள் & பள்ளங்கள்",
        "icon": "🛣️",
        "hint": "potholes, damaged road, speed breaker, culvert/bridge",
    },
    "DRAINAGE": {
        "short": "Drainage",
        "en": "Drainage & Sewage",
        "ta": "வடிகால் & கழிவுநீர்",
        "icon": "🌊",
        "hint": "blocked or overflowing drain, sewage on road, open manhole",
    },
    "STREET_LIGHTS": {
        "short": "Lights",
        "en": "Street Lights",
        "ta": "தெரு விளக்குகள்",
        "icon": "💡",
        "hint": "street light not working, flickering, damaged pole fitting",
    },
    "SANITATION": {
        "short": "Garbage",
        "en": "Garbage & Sanitation",
        "ta": "குப்பை & துப்புரவு",
        "icon": "🗑️",
        "hint": "garbage not collected, dumping, public toilet not cleaned",
    },
    "ELECTRICITY": {
        "short": "Power",
        "en": "Electricity & Power Lines",
        "ta": "மின்சாரம் & மின்கம்பிகள்",
        "icon": "⚡",
        "hint": "fallen/live wire, transformer, frequent power cuts (handled with the Electricity Board)",
    },
    "PUBLIC_HEALTH": {
        "short": "Health",
        "en": "Public Health",
        "ta": "பொது சுகாதாரம்",
        "icon": "🏥",
        "hint": "mosquito breeding, fever outbreak, stray dog bites, health hazards",
    },
    "AGRICULTURE": {
        "short": "Farming",
        "en": "Agriculture & Irrigation",
        "ta": "வேளாண்மை & பாசனம்",
        "icon": "🌾",
        "hint": "irrigation channel, crop damage, fertiliser/seed supply",
    },
    "WELFARE_SCHEMES": {
        "short": "Welfare",
        "en": "Welfare Schemes & Certificates",
        "ta": "நலத்திட்டங்கள் & சான்றிதழ்கள்",
        "icon": "📄",
        "hint": "pension, ration, housing, job card, certificates, house tax (individual cases)",
    },
    "OTHER": {
        "short": "Other",
        "en": "Other",
        "ta": "மற்றவை",
        "icon": "📌",
        "hint": "any other panchayat matter",
    },
}
CATEGORY_KEYS = list(CATEGORIES)

# Individual cases (one person's pension, one family's certificate) are never
# merged with other people's complaints.
INDIVIDUAL_CATEGORIES = {"WELFARE_SCHEMES"}

# Categories that often describe the same physical problem (a leaking pipe and
# sewage in drinking water may be one issue) and are compared when the street matches.
RELATED_CATEGORIES = [{"WATER_SUPPLY", "DRAINAGE", "PUBLIC_HEALTH"}, {"ELECTRICITY", "STREET_LIGHTS"}]

# ---------------------------------------------------------------------------
# Priorities and service-level agreements (SLA)
# ---------------------------------------------------------------------------
PRIORITIES: dict[str, dict] = {
    "P1": {"label": "Critical", "ta": "மிக அவசரம்", "sla_hours": 24, "sla_en": "24 hours", "sla_ta": "24 மணி நேரத்திற்குள்"},
    "P2": {"label": "High", "ta": "அவசரம்", "sla_hours": 72, "sla_en": "3 days", "sla_ta": "3 நாட்களுக்குள்"},
    "P3": {"label": "Medium", "ta": "சாதாரணம்", "sla_hours": 168, "sla_en": "7 days", "sla_ta": "7 நாட்களுக்குள்"},
    "P4": {"label": "Low", "ta": "குறைந்த முன்னுரிமை", "sla_hours": 360, "sla_en": "15 days", "sla_ta": "15 நாட்களுக்குள்"},
}
PRIORITY_ORDER = ["P1", "P2", "P3", "P4"]  # most urgent first


def higher_priority(a: str | None, b: str | None) -> str | None:
    """Return the more urgent of two priorities (P1 beats P2...)."""
    vals = [p for p in (a, b) if p in PRIORITIES]
    if not vals:
        return None
    return min(vals, key=PRIORITY_ORDER.index)


def bump_priority(p: str) -> str:
    idx = PRIORITY_ORDER.index(p) if p in PRIORITY_ORDER else 2
    return PRIORITY_ORDER[max(0, idx - 1)]


STATUSES = {
    "OPEN": "Open",
    "IN_PROGRESS": "In progress",
    "ESCALATED": "Escalated",
    "RESOLVED": "Resolved",
}
OPEN_STATUSES = ("OPEN", "IN_PROGRESS", "ESCALATED")

# ---------------------------------------------------------------------------
# Ward and street directory (used by the agents to locate complaints)
# ---------------------------------------------------------------------------
WARDS: dict[int, list[dict]] = {
    1: [
        {"en": "Gandhi Street", "ta": "காந்தி தெரு", "aliases": ["gandhi street", "gandhi st", "gandhi theru"]},
        {"en": "Bazaar Street", "ta": "பஜார் தெரு", "aliases": ["bazaar street", "bazar street", "bazaar st", "bazaar"]},
        {"en": "Bus Stand Road", "ta": "பேருந்து நிலைய சாலை", "aliases": ["bus stand road", "bus stand", "busstand"]},
    ],
    2: [
        {"en": "Kamarajar Street", "ta": "காமராஜர் தெரு", "aliases": ["kamarajar street", "kamarajar st", "kamaraj street", "kamarajar"]},
        {"en": "School Street", "ta": "பள்ளி தெரு", "aliases": ["school street", "school st", "palli theru"]},
        {"en": "Post Office Road", "ta": "தபால் நிலைய சாலை", "aliases": ["post office road", "post office", "po road"]},
    ],
    3: [
        {"en": "Periyar Nagar", "ta": "பெரியார் நகர்", "aliases": ["periyar nagar", "periyar ngr"]},
        {"en": "Anna Nagar", "ta": "அண்ணா நகர்", "aliases": ["anna nagar", "anna ngr"]},
        {"en": "Water Tank Street", "ta": "தண்ணீர் தொட்டி தெரு", "aliases": ["water tank street", "tank street", "thotti theru"]},
    ],
    4: [
        {"en": "Kovil Street", "ta": "கோவில் தெரு", "aliases": ["kovil street", "kovil st", "temple street"]},
        {"en": "Mariamman Kovil Street", "ta": "மாரியம்மன் கோவில் தெரு", "aliases": ["mariamman kovil street", "mariamman kovil", "mariamman temple"]},
        {"en": "Kulam Road", "ta": "குளம் சாலை", "aliases": ["kulam road", "pond road"]},
    ],
    5: [
        {"en": "Indira Colony", "ta": "இந்திரா காலனி", "aliases": ["indira colony"]},
        {"en": "Ambedkar Street", "ta": "அம்பேத்கர் தெரு", "aliases": ["ambedkar street", "ambedkar st", "ambedkar"]},
        {"en": "Market Road", "ta": "சந்தை சாலை", "aliases": ["market road", "market rd", "santhai road"]},
    ],
    6: [
        {"en": "Thiruvalluvar Street", "ta": "திருவள்ளுவர் தெரு", "aliases": ["thiruvalluvar street", "thiruvalluvar", "valluvar street"]},
        {"en": "Rice Mill Road", "ta": "அரிசி ஆலை சாலை", "aliases": ["rice mill road", "rice mill"]},
        {"en": "Canal Road", "ta": "வாய்க்கால் சாலை", "aliases": ["canal road", "vaikkal road", "vaikal road"]},
    ],
}
WARD_NUMBERS = sorted(WARDS)
MAX_WARD = max(WARD_NUMBERS)

# ---------------------------------------------------------------------------
# Officers (routing table) and escalation chain
# ---------------------------------------------------------------------------
_SECRETARY = {
    "id": "OFF-SC",
    "name": "N. Saravanan",
    "role": "Panchayat Secretary",
    "role_ta": "ஊராட்சி செயலர்",
}
OFFICERS: dict[str, dict] = {
    "WATER_SUPPLY": {"id": "OFF-WS", "name": "K. Murugan", "role": "Pump Operator (Water Supply)", "role_ta": "குடிநீர் பம்ப் இயக்குநர்"},
    "ROADS": {"id": "OFF-RD", "name": "S. Lakshmi", "role": "Overseer, Panchayat Union (Roads)", "role_ta": "ஊராட்சி ஒன்றிய மேற்பார்வையாளர்"},
    "DRAINAGE": {"id": "OFF-DR", "name": "P. Ravi", "role": "Sanitation Supervisor (Drainage)", "role_ta": "துப்புரவு மேற்பார்வையாளர்"},
    "STREET_LIGHTS": {"id": "OFF-SL", "name": "M. Senthil", "role": "Panchayat Electrician (Street Lights)", "role_ta": "ஊராட்சி மின் பணியாளர்"},
    "SANITATION": {"id": "OFF-SN", "name": "R. Kavitha", "role": "Sanitation Coordinator (Garbage)", "role_ta": "தூய்மை பணி ஒருங்கிணைப்பாளர்"},
    "ELECTRICITY": {"id": "OFF-EB", "name": "EB Section Office", "role": "Electricity Board liaison (external)", "role_ta": "மின்வாரிய பிரிவு அலுவலகம்", "external": True},
    "PUBLIC_HEALTH": {"id": "OFF-PH", "name": "A. Meena", "role": "Village Health Nurse", "role_ta": "கிராம சுகாதார செவிலியர்"},
    "AGRICULTURE": {"id": "OFF-AG", "name": "V. Kumar", "role": "Assistant Agricultural Officer", "role_ta": "உதவி வேளாண்மை அலுவலர்"},
    "WELFARE_SCHEMES": dict(_SECRETARY, role="Panchayat Secretary (Schemes & Certificates)"),
    "OTHER": dict(_SECRETARY),
}

ESCALATION_CHAIN = [
    {"level": 1, "id": "OFF-SC", "name": "N. Saravanan", "role": "Panchayat Secretary", "role_ta": "ஊராட்சி செயலர்"},
    {"level": 2, "id": "ESC-PR", "name": "R. Selvi", "role": "Panchayat President", "role_ta": "ஊராட்சி மன்றத் தலைவர்"},
    {"level": 3, "id": "ESC-BDO", "name": "Block Development Officer", "role": "BDO (Village Panchayats)", "role_ta": "வட்டார வளர்ச்சி அலுவலர்"},
]
ESCALATION_STEP_HOURS = 24  # one level higher for every further 24 h overdue


def officer_for(category: str) -> dict:
    return OFFICERS.get(category, OFFICERS["OTHER"])


def escalation_target(level: int, assigned_officer_id: str) -> dict:
    """Authority for an escalation level, skipping the assigned officer themself."""
    chain = [c for c in ESCALATION_CHAIN if c["id"] != assigned_officer_id]
    level = max(1, min(level, len(chain)))
    return chain[level - 1]


def directory_text() -> str:
    """Ward/street directory as plain text for LLM prompts."""
    lines = []
    for ward, streets in WARDS.items():
        names = ", ".join(f"{s['en']} ({s['ta']})" for s in streets)
        lines.append(f"Ward {ward}: {names}")
    return "\n".join(lines)


def categories_text() -> str:
    return "\n".join(f"- {k}: {v['en']} — {v['hint']}" for k, v in CATEGORIES.items())


def public_config() -> dict:
    """Configuration the browser UI needs."""
    return {
        "panchayat": {"name": PANCHAYAT_NAME, "name_ta": PANCHAYAT_NAME_TA},
        "categories": CATEGORIES,
        "priorities": PRIORITIES,
        "statuses": STATUSES,
        "wards": {str(w): [s["en"] for s in streets] for w, streets in WARDS.items()},
        "officers": OFFICERS,
        "escalation_chain": ESCALATION_CHAIN,
    }
