"""Offline analysis engine (no LLM).

Used in three ways:
1. As the fallback brain when Gemini is not configured, rate-limited or fails.
2. As a deterministic safety layer: its hazard rules are a priority *floor*
   that the LLM's decision can never go below (guardrail).
3. As a fast location resolver: streets in the ward directory map to wards.

Understands English, Tamil script and common Tanglish words.
"""
from __future__ import annotations

import re

from . import config

# --------------------------------------------------------------------------
# Language detection
# --------------------------------------------------------------------------
_TANGLISH = {
    "illa", "illai", "romba", "aagudhu", "aaguthu", "agudhu", "varala", "varalai", "varudhu", "edukkala",
    "edukala", "eduka", "kuppai", "thanni", "tanni", "sakkadai", "vilakku", "kudineer", "naaththam", "natham",
    "nikkudhu", "irukku", "iruku", "pannunga", "seyyunga", "vaaram", "naal", "theru", "pakkam", "kitta",
    "aachu", "odudhu", "oduthu", "adikuthu", "adikudhu", "eriyala", "eriyalai", "current", "la",
}


def detect_language(text: str) -> str:
    tamil = sum(1 for ch in text if "஀" <= ch <= "௿")
    latin = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    if tamil and tamil / max(1, tamil + latin) > 0.3:
        return "ta"
    words = set(re.findall(r"[a-z]+", text.lower()))
    if len(words & (_TANGLISH - {"current", "la"})) >= 1 or ({"la"} <= words and len(words & _TANGLISH) >= 1):
        return "tanglish"
    return "en"


# --------------------------------------------------------------------------
# Keyword tables
# --------------------------------------------------------------------------
def _compile(term: str) -> re.Pattern:
    if term.startswith("re:"):
        return re.compile(term[3:], re.IGNORECASE)
    if re.search(r"[஀-௿]", term):
        return re.compile(re.escape(term))  # Tamil words take suffixes; match as substring
    return re.compile(r"(?<![a-z0-9])" + re.escape(term.lower()) + r"(?![a-z0-9])")


_RAW_KEYWORDS: dict[str, list[tuple[str, float]]] = {
    "WATER_SUPPLY": [
        ("drinking water", 3), ("no water", 3), ("water supply", 3), ("re:water\\s+(is\\s+)?not\\s+(coming|available|supplied)", 3),
        ("tap", 1), ("taps", 1), ("pipeline", 2), ("pipe", 1), ("borewell", 2), ("bore well", 2), ("motor", 1),
        ("overhead tank", 2), ("low pressure", 2), ("leak", 1), ("leaking", 1), ("water", 1),
        ("kudineer", 3), ("thanni", 2), ("tanni", 2), ("thaneer", 2),
        ("குடிநீர்", 3), ("தண்ணீர்", 2), ("குழாய்", 2), ("பைப்", 1), ("நீர் வரவில்லை", 3),
    ],
    "ROADS": [
        ("pothole", 3), ("potholes", 3), ("damaged road", 3), ("road damaged", 3), ("road is damaged", 3),
        ("bad road", 2), ("road", 1), ("tar road", 2), ("speed breaker", 2), ("bridge", 2), ("culvert", 2),
        ("kuzhi", 2), ("salai", 1), ("சாலை", 1), ("ரோடு", 1), ("குழி", 2), ("பள்ளம்", 2), ("பாலம்", 2),
    ],
    "DRAINAGE": [
        ("drain", 2), ("drains", 2), ("drainage", 3), ("sewage", 3), ("gutter", 2), ("overflow", 2),
        ("overflowing", 2), ("manhole", 2), ("blocked drain", 3), ("sakkadai", 3), ("saakadai", 3),
        ("kalivu neer", 3), ("சாக்கடை", 3), ("கழிவுநீர்", 3), ("கழிவு நீர்", 3), ("வடிகால்", 3),
    ],
    "STREET_LIGHTS": [
        ("street light", 3), ("street lights", 3), ("streetlight", 3), ("streetlights", 3), ("street lamp", 3),
        ("light not working", 3), ("lights not working", 3), ("lamp", 1), ("bulb", 1), ("dark", 1),
        ("theru vilakku", 3), ("vilakku", 2), ("தெரு விளக்கு", 3), ("விளக்கு", 2), ("இருட்டு", 1), ("எரியவில்லை", 1),
    ],
    "SANITATION": [
        ("garbage", 3), ("waste", 2), ("trash", 2), ("rubbish", 2), ("dump", 1), ("dumped", 2), ("dustbin", 2),
        ("not cleaned", 2), ("toilet", 2), ("sweeping", 2), ("kuppai", 3), ("குப்பை", 3), ("கழிப்பறை", 2),
    ],
    "ELECTRICITY": [
        ("electric wire", 3), ("live wire", 4), ("wire", 1), ("transformer", 3), ("power cut", 3),
        ("power cuts", 3), ("current", 1), ("electricity", 2), ("voltage", 2), ("shock", 2), ("eb", 2),
        ("electric pole", 2), ("மின்சாரம்", 2), ("மின் கம்பி", 3), ("மின்கம்பி", 3), ("கம்பி", 1),
        ("டிரான்ஸ்பார்மர்", 3), ("கரண்ட்", 2), ("மின்வெட்டு", 3),
    ],
    "PUBLIC_HEALTH": [
        ("mosquito", 3), ("mosquitoes", 3), ("dengue", 3), ("fever", 2), ("disease", 2), ("cholera", 3),
        ("diarrhoea", 3), ("diarrhea", 3), ("stray dog", 2), ("stray dogs", 2), ("dog bite", 3), ("health", 1),
        ("kosu", 3), ("கொசு", 3), ("காய்ச்சல்", 2), ("டெங்கு", 3), ("நோய்", 2), ("நாய்", 1),
    ],
    "AGRICULTURE": [
        ("crop", 2), ("crops", 2), ("farm", 1), ("farmer", 2), ("farmers", 2), ("irrigation", 3), ("paddy", 2),
        ("fertilizer", 2), ("fertiliser", 2), ("seeds", 2), ("pest", 2), ("field", 1), ("canal", 1),
        ("channel", 1), ("vivasayam", 3), ("விவசாயம்", 3), ("வயல்", 2), ("பயிர்", 2), ("பாசனம்", 3),
        ("உரம்", 2), ("விதை", 2), ("வாய்க்கால்", 1),
    ],
    "WELFARE_SCHEMES": [
        ("pension", 3), ("ration", 2), ("ration card", 3), ("scheme", 2), ("certificate", 3), ("house tax", 2),
        ("patta", 3), ("job card", 3), ("100 days", 3), ("mgnrega", 3), ("nrega", 3), ("housing", 2),
        ("subsidy", 2), ("ஓய்வூதியம்", 3), ("பென்ஷன்", 3), ("ரேஷன்", 2), ("சான்றிதழ்", 3), ("பட்டா", 3),
        ("வீட்டு வரி", 2), ("100 நாள்", 3),
    ],
}
KEYWORDS = {cat: [(_compile(t), w, t) for t, w in items] for cat, items in _RAW_KEYWORDS.items()}

# Hazard rules -> priority. (pattern, human-readable signal)
_P1 = [
    (r"\b(live|electric|power|current)\s+wire", "live/electric wire"),
    (r"\bwire\b.{0,40}\b(fallen|fell|fall|cut|snapped|hanging|broken|on the road)", "fallen wire"),
    (r"\b(fallen|fell|snapped)\b.{0,30}\bwire", "fallen wire"),
    (r"\bshock\b|electrocut", "electric shock risk"),
    (r"\bfire\b", "fire"),
    (r"\bcollaps", "structure collapse"),
    (r"\binjur|\bbleeding\b", "injury"),
    (r"sewage.{0,40}\b(mix|mixing|mixed|entering|enters|into|in)\b.{0,40}drinking", "sewage in drinking water"),
    (r"drinking water.{0,40}\b(sewage|contaminat|dirty|smell)", "contaminated drinking water"),
    (r"\bcontaminat", "contamination"),
    (r"\bopen manhole\b", "open manhole"),
    (r"\bdrown", "drowning risk"),
    (r"மின் ?கம்பி.{0,20}(அறுந்து|விழுந்து)", "fallen electric wire"),
    (r"மின்சாரம் தாக்", "electric shock risk"),
    (r"தீ விபத்து", "fire"),
    (r"(கழிவுநீர்|சாக்கடை).{0,30}(குடிநீர்|குழாய்).{0,20}கலந்", "sewage in drinking water"),
    (r"குடிநீரில்.{0,20}(கழிவுநீர்|சாக்கடை)", "sewage in drinking water"),
]
_P2 = [
    (r"\bno (drinking )?water\b|water\s+(is\s+)?not\s+(coming|available|supplied)|thanni varala|நீர் வரவில்லை|தண்ணீர் வரவில்லை", "drinking water disrupted"),
    (r"\b([2-9]|\d{2})\s*days\b|\bweek\b|\bvaaram\b|ஒரு வாரம|வாரமாக", "long pending (days)"),
    (r"entering (the )?houses?|into (the )?houses?|வீட்டுக்குள்|வீடுகளுக்குள்", "entering houses"),
    (r"\boverflow", "overflow"),
    (r"\bdengue\b|டெங்கு", "dengue risk"),
    (r"\bfever\b|காய்ச்சல்", "fever cases"),
    (r"\bdog bite", "dog bite"),
    (r"\baccident|\bdanger|\bunsafe\b|ஆபத்து", "safety risk"),
    (r"\bschool\b|\bhospital\b|பள்ளி|மருத்துவமனை", "near school/hospital"),
    (r"\bchildren\b|\belderly\b|\bwomen\b|\bladies\b|பெண்கள்|குழந்தைகள்|முதியோர்|பயம", "vulnerable people affected"),
]
_P4 = [
    (r"\bcertificate\b|சான்றிதழ்", "paperwork request"),
    (r"\bsuggest|\brequest\b|please (consider|add|provide)", "request / suggestion"),
    (r"\bspeed breaker\b", "new facility request"),
]
P1_RULES = [(re.compile(p, re.IGNORECASE), s) for p, s in _P1]
P2_RULES = [(re.compile(p, re.IGNORECASE), s) for p, s in _P2]
P4_RULES = [(re.compile(p, re.IGNORECASE), s) for p, s in _P4]

TITLE_BASE = {
    "WATER_SUPPLY": "Drinking water problem",
    "ROADS": "Damaged road",
    "DRAINAGE": "Drainage blocked",
    "STREET_LIGHTS": "Street light not working",
    "SANITATION": "Garbage not cleared",
    "ELECTRICITY": "Electricity problem",
    "PUBLIC_HEALTH": "Public health risk",
    "AGRICULTURE": "Agriculture / irrigation issue",
    "WELFARE_SCHEMES": "Welfare scheme issue",
    "OTHER": "General grievance",
}
_TITLE_RULES = [
    ("WATER_SUPPLY", r"contaminat|sewage|dirty water|smell|கழிவுநீர்|சாக்கடை", "Contaminated drinking water"),
    ("WATER_SUPPLY", r"no (drinking )?water|not coming|not supplied|varala|வரவில்லை", "No drinking water supply"),
    ("WATER_SUPPLY", r"leak|burst|broken pipe|உடைந்|கசிவு", "Drinking water pipeline leak"),
    ("WATER_SUPPLY", r"pressure", "Low water pressure"),
    ("ELECTRICITY", r"wire|கம்பி", "Fallen / live electric wire"),
    ("ELECTRICITY", r"power cut|மின்வெட்டு|no current|current illa", "Frequent power cuts"),
    ("ELECTRICITY", r"transformer|டிரான்ஸ்பார்மர்", "Transformer problem"),
    ("DRAINAGE", r"manhole", "Open / damaged manhole"),
    ("DRAINAGE", r"overflow|நிரம்பி|entering|odudhu|ஓடுகிறது", "Drain overflowing"),
    ("ROADS", r"pothole|குழி|பள்ளம்|kuzhi", "Potholes on road"),
    ("ROADS", r"speed breaker", "Speed breaker request"),
    ("SANITATION", r"toilet|கழிப்பறை", "Public toilet not cleaned"),
    ("PUBLIC_HEALTH", r"mosquito|கொசு|kosu|dengue|டெங்கு", "Mosquito breeding / dengue risk"),
    ("PUBLIC_HEALTH", r"\bdog|நாய்", "Stray dog menace"),
    ("PUBLIC_HEALTH", r"fever|காய்ச்சல்", "Fever cases reported"),
    ("AGRICULTURE", r"canal|channel|வாய்க்கால்|silt", "Irrigation channel blocked"),
    ("WELFARE_SCHEMES", r"pension|ஓய்வூதியம்|பென்ஷன்", "Pension not received"),
    ("WELFARE_SCHEMES", r"certificate|சான்றிதழ்", "Certificate request pending"),
    ("WELFARE_SCHEMES", r"ration|ரேஷன்", "Ration card issue"),
]
TITLE_RULES = [(c, re.compile(p, re.IGNORECASE), t) for c, p, t in _TITLE_RULES]

OFFICER_STEPS = {
    "WATER_SUPPLY": "Inspect the pipeline and valves for the area, check the overhead tank level and pump, restore supply and update the citizen.",
    "ROADS": "Inspect the stretch, barricade dangerous potholes today and schedule patch work.",
    "DRAINAGE": "Clear the blockage with the sanitation team, remove silt and disinfect the spot.",
    "STREET_LIGHTS": "Check the fitting, bulb and wiring; replace with an LED fitting if it has failed.",
    "SANITATION": "Clear the garbage today and fix a regular collection schedule for the street.",
    "ELECTRICITY": "Inform the EB section office immediately, cordon off the spot and confirm the line is isolated.",
    "PUBLIC_HEALTH": "Visit the area, arrange fogging and source reduction, and refer fever cases to the PHC.",
    "AGRICULTURE": "Inspect the channel and plan desilting with MGNREGS workers before the next irrigation turn.",
    "WELFARE_SCHEMES": "Verify the citizen's records with the department and update the status within the SLA.",
    "OTHER": "Review the grievance and assign it to the right section.",
}
CONTAMINATION_STEPS = (
    "Stop supply on the affected line, flush and chlorinate it, find the sewage entry point, "
    "and alert the Village Health Nurse."
)
ADVICE = {
    "WATER_SUPPLY": ("Please boil drinking water until supply is normal.", "குடிநீர் சீராகும் வரை காய்ச்சிய நீரை குடிக்கவும்."),
    "ROADS": ("Please ride slowly on this stretch, especially at night.", "இந்த பகுதியில், குறிப்பாக இரவில், மெதுவாக வாகனம் ஓட்டவும்."),
    "DRAINAGE": ("Keep children away from the overflow and stagnant water.", "கழிவுநீர் தேங்கிய இடத்திலிருந்து குழந்தைகளை விலக்கி வைக்கவும்."),
    "STREET_LIGHTS": ("Avoid walking alone on the dark stretch until it is fixed.", "சரி செய்யும் வரை இருட்டான பகுதியில் தனியாக நடப்பதைத் தவிர்க்கவும்."),
    "SANITATION": ("Please hand over segregated waste to the collection worker.", "குப்பையை பிரித்து துப்புரவு பணியாளரிடம் ஒப்படைக்கவும்."),
    "ELECTRICITY": ("Stay away from the wire and keep children away.",
                    "கம்பியின் அருகே செல்ல வேண்டாம்; குழந்தைகளை விலக்கி வைக்கவும்."),
    "PUBLIC_HEALTH": ("Empty stored water every week; visit the PHC if you have fever.",
                      "தேங்கிய நீரை வாரம் ஒருமுறை அகற்றவும்; காய்ச்சல் இருந்தால் ஆரம்ப சுகாதார நிலையத்தை அணுகவும்."),
    "AGRICULTURE": ("", ""),
    "WELFARE_SCHEMES": ("Keep your Aadhaar and bank passbook ready for verification.",
                        "சரிபார்ப்புக்கு ஆதார் அட்டை மற்றும் வங்கி புத்தகத்தை தயாராக வைக்கவும்."),
    "OTHER": ("", ""),
}
CONTAMINATION_ADVICE = (
    "Do not drink tap water now; use boiled or packaged water.",
    "இப்போது குழாய் நீரை குடிக்க வேண்டாம்; காய்ச்சிய அல்லது பாட்டில் நீரை பயன்படுத்தவும்.",
)

FOLLOWUP_EN = "Where is this problem? Please tell me your ward number (1–%d) or the street name / a nearby landmark." % config.MAX_WARD
FOLLOWUP_TA = "இந்த பிரச்சனை எங்கு உள்ளது? வார்டு எண் (1–%d) அல்லது தெரு பெயர் / அருகிலுள்ள அடையாளத்தைச் சொல்லுங்கள்." % config.MAX_WARD

_GREETINGS = {"hi", "hello", "hey", "hai", "vanakkam", "test", "testing", "thanks", "thank", "you", "ok", "okay",
              "good", "morning", "evening", "night", "sir", "madam", "வணக்கம்", "நன்றி"}
_OFF_TOPIC = re.compile(r"\b(cricket|match|movie|film|song|weather forecast|joke|recipe|ipl|stock|bitcoin|game|homework)\b", re.I)

# --------------------------------------------------------------------------
# Location helpers
# --------------------------------------------------------------------------
_ALIASES: list[tuple[re.Pattern, dict, int]] = []
for _ward, _streets in config.WARDS.items():
    for _s in _streets:
        for _alias in set(_s["aliases"] + [_s["en"].lower()]):
            _ALIASES.append((_compile(_alias), _s, _ward))
        _ALIASES.append((re.compile(re.escape(_s["ta"])), _s, _ward))
_ALIASES.sort(key=lambda item: -len(item[0].pattern))

_WARD_RES = [
    re.compile(r"\bward\s*(?:no\.?|number|num|#)?\s*[:\-]?\s*(\d{1,2})\b", re.I),
    re.compile(r"\b(\d{1,2})\s*(?:st|nd|rd|th)?\s*ward\b", re.I),
    re.compile(r"வார்டு\s*(?:எண்)?\s*[:\-]?\s*(\d{1,2})"),
    re.compile(r"(\d{1,2})\s*(?:-?\s*வது)?\s*வார்டு"),
]
_LANDMARK_EN = re.compile(
    r"\b(near|opposite|opp\.?|behind|beside|next to|in front of|close to)\s+((?:the\s+)?[a-z0-9][a-z0-9'\- ]{1,40}?)"
    r"(?=\s*(?:[,.;!?\n]|$)|\s+(?:and|but|for|since|from|is|are|was|has|have|which|where|so|because|it|its|in|at|on|with|of|to)\b)",
    re.I,
)
_LANDMARK_TA = re.compile(r"([஀-௿]+(?:\s+[஀-௿]+)?)\s+(அருகில்|அருகே|எதிரில்|பின்புறம்|பக்கத்தில்)")


def find_streets(text: str) -> list[dict]:
    """Known streets mentioned in the text, in order of appearance."""
    low = text.lower()
    taken = [False] * len(low)
    found = []
    for pattern, street, ward in _ALIASES:
        for m in pattern.finditer(low):
            if any(taken[m.start():m.end()]):
                continue
            for i in range(m.start(), m.end()):
                taken[i] = True
            found.append({"street": street, "ward": ward, "start": m.start(), "end": m.end()})
    found.sort(key=lambda f: f["start"])
    unique, seen = [], set()
    for f in found:
        if f["street"]["en"] not in seen:
            seen.add(f["street"]["en"])
            unique.append(f)
    return unique


def find_ward(text: str) -> tuple[int | None, int | None]:
    """(valid ward, invalid ward number mentioned)."""
    for rx in _WARD_RES:
        m = rx.search(text)
        if m:
            n = int(m.group(1))
            return (n, None) if n in config.WARDS else (None, n)
    return None, None


def find_landmark(text: str) -> str:
    m = _LANDMARK_EN.search(text)
    if m:
        phrase = f"{m.group(1).lower()} {m.group(2).strip()}"
        # don't treat a street name itself as the landmark
        if not any(p.search(m.group(2).lower()) for p, _, _ in _ALIASES):
            return phrase
    m = _LANDMARK_TA.search(text)
    if m:
        return f"{m.group(1)} {m.group(2)}"
    return ""


def street_ward(location: str) -> int | None:
    found = find_streets(location or "")
    return found[0]["ward"] if found else None


def street_names(location: str) -> set[str]:
    return {f["street"]["en"] for f in find_streets(location or "")}


# --------------------------------------------------------------------------
# Main analysis
# --------------------------------------------------------------------------
def _strip_spans(text: str, spans: list[tuple[int, int]]) -> str:
    out, last = [], 0
    for start, end in sorted(spans):
        out.append(text[last:start])
        out.append(" ")
        last = end
    out.append(text[last:])
    return "".join(out)


def classify(text: str) -> tuple[str, dict[str, float]]:
    low = text.lower()
    scores = {}
    for cat, rules in KEYWORDS.items():
        s = sum(w * len(p.findall(low)) for p, w, _ in rules)
        if s:
            scores[cat] = s
    if not scores:
        return "OTHER", {}
    best = max(scores, key=lambda c: (scores[c], -config.CATEGORY_KEYS.index(c)))
    return best, dict(sorted(scores.items(), key=lambda kv: -kv[1])[:3])


def priority_rules(text: str) -> tuple[str, str, list[str]]:
    """(priority, reason, signals) from hazard rules alone."""
    p1 = [s for rx, s in P1_RULES if rx.search(text)]
    if p1:
        return "P1", f"Danger to life or health detected: {', '.join(dict.fromkeys(p1))}.", list(dict.fromkeys(p1))
    p2 = [s for rx, s in P2_RULES if rx.search(text)]
    if p2:
        return "P2", f"Essential service disrupted or vulnerable people affected: {', '.join(dict.fromkeys(p2))}.", list(dict.fromkeys(p2))
    p4 = [s for rx, s in P4_RULES if rx.search(text)]
    if p4:
        return "P4", f"Low urgency: {', '.join(dict.fromkeys(p4))}.", list(dict.fromkeys(p4))
    return "P3", "Service problem without immediate risk.", []


def _title(category: str, text: str) -> str:
    for cat, rx, title in TITLE_RULES:
        if cat == category and rx.search(text):
            return title
    return TITLE_BASE.get(category, "General grievance")


def _trim(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def analyze(text: str, ward_hint: int | None = None) -> dict:
    text = (text or "").strip()
    lang = detect_language(text)

    streets = find_streets(text)
    ward, invalid_ward = find_ward(text)
    landmark = find_landmark(text)
    street = streets[0]["street"] if streets else None
    if ward is None and street is not None:
        ward = streets[0]["ward"]
    if ward is None and ward_hint in config.WARDS:
        ward = ward_hint

    # classify on the text without street names (e.g. "Canal Road", "Water Tank Street")
    clean = _strip_spans(text, [(f["start"], f["end"]) for f in streets])
    land_m = _LANDMARK_EN.search(clean)
    if land_m:
        main = clean[: land_m.start()] + " " + clean[land_m.end():]
        cat, scores = classify(main)
        land_cat, land_scores = classify(land_m.group(0))
        for c, s in land_scores.items():  # landmark words count only a little
            scores[c] = scores.get(c, 0) + 0.3 * s
        if scores:
            cat = max(scores, key=lambda c: (scores[c], -config.CATEGORY_KEYS.index(c)))
    else:
        cat, scores = classify(clean)

    words = re.findall(r"[a-z]+|[஀-௿]+", text.lower())
    greeting_only = bool(words) and all(w in _GREETINGS for w in words)
    off_topic = bool(_OFF_TOPIC.search(text))
    is_grievance = bool(scores) or (len(words) > 5 and not off_topic and not greeting_only)
    if not text:
        is_grievance = False

    priority, reason, signals = priority_rules(text)
    if cat == "WELFARE_SCHEMES" and priority == "P2" and not any("water" in s or "danger" in s for s in signals):
        priority, reason = "P3", "Individual welfare case without immediate risk."
    title = _title(cat, clean)
    contamination = cat == "WATER_SUPPLY" and title == "Contaminated drinking water"

    location = ", ".join(x for x in [street["en"] if street else "", landmark] if x)
    location_ta = street["ta"] if street else (landmark if lang == "ta" else "")
    full_title = f"{title} – {street['en']}" if street else title

    if lang == "ta":
        summary_en = f"{full_title}{' (Ward %d)' % ward if ward else ''}. Reported in Tamil."
        summary_ta = _trim(text, 180)
    else:
        where = f" at {location}" if location else (f" in Ward {ward}" if ward else "")
        summary_en = f"{title}{where}. Citizen says: “{_trim(text, 160)}”"
        place = location_ta or (f"வார்டு {ward}" if ward else "உங்கள் பகுதி")
        summary_ta = f"{place} பகுதியில் {config.CATEGORIES[cat]['ta']} தொடர்பான புகார்."

    advice_en, advice_ta = CONTAMINATION_ADVICE if contamination else ADVICE.get(cat, ("", ""))
    return {
        "language": lang,
        "category": cat,
        "category_scores": scores,
        "title": full_title,
        "summary_en": summary_en,
        "summary_ta": summary_ta,
        "ward": ward or 0,
        "invalid_ward": invalid_ward,
        "street": street["en"] if street else "",
        "street_ta": street["ta"] if street else "",
        "landmark": landmark,
        "location": location,
        "priority": priority,
        "priority_reason": reason,
        "urgency_signals": signals,
        "emergency": priority == "P1",
        "is_grievance": is_grievance,
        "needs_followup": is_grievance and not ward and not location,
        "followup_en": FOLLOWUP_EN,
        "followup_ta": FOLLOWUP_TA,
        "officer_instructions": CONTAMINATION_STEPS if contamination else OFFICER_STEPS.get(cat, OFFICER_STEPS["OTHER"]),
        "advice_en": advice_en,
        "advice_ta": advice_ta,
    }
