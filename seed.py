"""Fictional demo data so the dashboard, escalation and insights have something to show."""
from __future__ import annotations

from datetime import timedelta

from . import config
from .db import Store, iso, sla_due

# (ward, location, category, title, summary_en, summary_ta, priority, status, hours_ago,
#  escalation_level, resolved_hours_ago, reports[(name, lang, text)])
SEED = [
    (4, "Kovil Street", "WATER_SUPPLY", "Low water pressure in taps – Kovil Street",
     "Taps on Kovil Street get only a thin flow in the morning supply hours for the past week.",
     "கோவில் தெருவில் காலை நேரத்தில் குழாயில் தண்ணீர் மிகக் குறைவாக வருகிறது.",
     "P3", "OPEN", 30, 0, None,
     [("Murugesan", "en", "Very low water pressure in Kovil Street taps in the morning."),
      ("Vasanthi", "ta", "கோவில் தெருவில் குழாயில் தண்ணீர் மெதுவாக வருகிறது.")]),
    (2, "Kamarajar Street", "DRAINAGE", "Drain overflowing onto road – Kamarajar Street",
     "The drain on Kamarajar Street is blocked and sewage is overflowing onto the road, causing smell and mosquitoes.",
     "காமராஜர் தெருவில் சாக்கடை அடைத்து கழிவுநீர் சாலையில் ஓடுகிறது.",
     "P2", "OPEN", 50, 0, None,
     [("Priya", "ta", "காமராஜர் தெருவில் சாக்கடை நிரம்பி சாலையில் ஓடுகிறது. துர்நாற்றம் தாங்க முடியவில்லை."),
      ("Kannan", "en", "Drain blocked and overflowing on Kamarajar Street, mosquitoes everywhere."),
      ("Ayesha", "tanglish", "Kamarajar street la sakkadai overflow aagudhu, road la thanni nikkudhu.")]),
    (1, "Bus Stand Road, near the bus stand", "ROADS", "Large potholes near the bus stand",
     "Deep potholes on Bus Stand Road near the bus stand; two-wheelers are skidding at night.",
     "பேருந்து நிலையம் அருகே சாலையில் பெரிய பள்ளங்கள்; இரவில் இருசக்கர வாகனங்கள் சறுக்குகின்றன.",
     "P2", "IN_PROGRESS", 80, 0, None,
     [("Ramesh", "en", "Huge potholes near the bus stand, two-wheelers are falling at night."),
      ("Divya", "en", "Bus Stand Road has big potholes, please fix before someone gets hurt."),
      ("Karthik", "tanglish", "Bus stand kitta road la periya kuzhi, night la bike slip aagudhu."),
      ("Gopal", "en", "Potholes near bus stand getting worse after rain.")]),
    (5, "Ambedkar Street", "STREET_LIGHTS", "Three street lights not working – Ambedkar Street",
     "Three street lights on Ambedkar Street have not worked for four days; the stretch is dark at night.",
     "அம்பேத்கர் தெருவில் மூன்று தெரு விளக்குகள் நான்கு நாட்களாக எரியவில்லை.",
     "P3", "OPEN", 20, 0, None,
     [("Saranya", "en", "3 street lights in Ambedkar Street not working for 4 days.")]),
    (6, "Canal Road", "AGRICULTURE", "Irrigation channel blocked with silt – Canal Road",
     "The irrigation channel along Canal Road is silted up; water is not reaching the paddy fields at the tail end.",
     "வாய்க்கால் சாலையில் பாசன கால்வாய் மண் அடைத்து வயல்களுக்கு தண்ணீர் செல்லவில்லை.",
     "P3", "OPEN", 100, 0, None,
     [("Arun", "en", "Irrigation channel near Canal Road is blocked with silt, water not reaching our fields."),
      ("Lakshmi", "ta", "வாய்க்காலில் மண் அடைத்துள்ளது, வயலுக்கு தண்ணீர் வரவில்லை.")]),
    (3, "Anna Nagar", "SANITATION", "Garbage not collected for a week – Anna Nagar",
     "Garbage has not been collected in Anna Nagar for a week; the bin near the corner is overflowing.",
     "அண்ணா நகரில் ஒரு வாரமாக குப்பை எடுக்கப்படவில்லை.",
     "P2", "ESCALATED", 110, 1, None,
     [("Meenakshi", "ta", "அண்ணா நகரில் ஒரு வாரமாக குப்பை எடுக்கவில்லை."),
      ("Joseph", "en", "Garbage not cleared in Anna Nagar for 7 days."),
      ("Selvi", "en", "Bin overflowing at Anna Nagar corner, dogs spreading waste."),
      ("Mohammed Ali", "en", "No garbage collection in Anna Nagar this week."),
      ("Kavya", "tanglish", "Anna nagar la kuppai eduka aal varala.")]),
    (1, "Gandhi Street", "PUBLIC_HEALTH", "Mosquito breeding in stagnant water – Gandhi Street",
     "Stagnant water in an empty plot on Gandhi Street is breeding mosquitoes; two children have fever.",
     "காந்தி தெருவில் தேங்கிய நீரில் கொசு பெருகுகிறது; இரண்டு குழந்தைகளுக்கு காய்ச்சல்.",
     "P2", "OPEN", 60, 0, None,
     [("Revathi", "en", "Stagnant water in Gandhi Street plot, lots of mosquitoes, two kids have fever."),
      ("Suresh", "ta", "காந்தி தெருவில் தண்ணீர் தேங்கி கொசு அதிகமாக உள்ளது.")]),
    (2, "School Street, near the school", "ROADS", "Speed breaker needed near school",
     "Parents request a speed breaker on School Street near the school gate.",
     "பள்ளி வாசல் அருகே வேகத்தடை அமைக்க பெற்றோர் கோரிக்கை.",
     "P4", "OPEN", 200, 0, None,
     [("Anitha", "en", "Please put a speed breaker near the school gate on School Street.")]),
    (6, "Thiruvalluvar Street", "WELFARE_SCHEMES", "Old age pension delayed – Thiruvalluvar Street",
     "An elderly resident of Thiruvalluvar Street has not received the old age pension for two months.",
     "திருவள்ளுவர் தெருவைச் சேர்ந்த முதியவருக்கு இரண்டு மாதமாக ஓய்வூதியம் வரவில்லை.",
     "P3", "OPEN", 40, 0, None,
     [("Palani", "en", "My father's old age pension not received for 2 months. Thiruvalluvar Street.")]),
    (5, "Market Road", "SANITATION", "Garbage dumped near market – Market Road",
     "Shops on Market Road are dumping waste next to the road.",
     "சந்தை சாலையில் கடைக்காரர்கள் குப்பையை சாலையோரம் கொட்டுகின்றனர்.",
     "P3", "RESOLVED", 150, 0, 20,
     [("Fathima", "en", "Garbage dumped near the market on Market Road.")]),
    (4, "Kulam Road", "DRAINAGE", "Sewage flowing into village pond – Kulam Road",
     "Sewage from the Kulam Road drain was flowing into the village pond.",
     "குளம் சாலை சாக்கடை நீர் ஊர் குளத்தில் கலக்கிறது.",
     "P1", "RESOLVED", 300, 0, 280,
     [("Ganesan", "ta", "குளம் சாலை சாக்கடை நீர் குளத்தில் கலக்கிறது."),
      ("Rani", "en", "Sewage going into the pond on Kulam Road, fish dying.")]),
    (3, "Water Tank Street", "WATER_SUPPLY", "Overhead tank cleaning – Water Tank Street",
     "Residents asked for the overhead water tank to be cleaned.",
     "தண்ணீர் தொட்டியை சுத்தம் செய்ய கோரிக்கை.",
     "P4", "RESOLVED", 400, 0, 330,
     [("Bala", "en", "Please clean the overhead tank on Water Tank Street.")]),
    (1, "Bazaar Street", "STREET_LIGHTS", "Street light flickering – Bazaar Street",
     "One street light on Bazaar Street keeps flickering.",
     "பஜார் தெருவில் ஒரு தெரு விளக்கு மின்னுகிறது.",
     "P4", "RESOLVED", 260, 0, 200,
     [("Kumar", "en", "Street light flickering on Bazaar Street.")]),
    (5, "Indira Colony", "WATER_SUPPLY", "Pipeline leak at Indira Colony entrance",
     "The drinking water pipeline is leaking at the Indira Colony entrance and water is wasted.",
     "இந்திரா காலனி நுழைவாயிலில் குடிநீர் குழாய் கசிகிறது.",
     "P3", "OPEN", 10, 0, None,
     [("Shanthi", "en", "Water pipeline leaking at Indira Colony entrance.")]),
    (2, "Post Office Road", "ELECTRICITY", "Frequent evening power cuts – Post Office Road",
     "Houses on Post Office Road face power cuts every evening; referred to the EB section office.",
     "தபால் நிலைய சாலையில் தினமும் மாலை மின்வெட்டு.",
     "P3", "OPEN", 70, 0, None,
     [("Raja", "en", "Power cut every evening on Post Office Road."),
      ("Uma", "ta", "தபால் நிலைய சாலையில் மாலையில் கரண்ட் போகிறது."),
      ("Imran", "en", "Voltage drop and power cuts daily, Post Office Road.")]),
    (6, "Rice Mill Road", "ROADS", "Road damaged by heavy trucks – Rice Mill Road",
     "Rice Mill Road surface is broken by heavy trucks going to the mill.",
     "அரிசி ஆலை சாலை லாரிகளால் சேதமடைந்துள்ளது.",
     "P3", "OPEN", 90, 0, None,
     [("Senthil Kumar", "en", "Rice Mill Road is fully damaged by lorries.")]),
    (4, "Mariamman Kovil Street", "SANITATION", "Public toilet not cleaned – Mariamman Kovil Street",
     "The community toilet on Mariamman Kovil Street has not been cleaned and has no water.",
     "மாரியம்மன் கோவில் தெரு பொது கழிப்பறை சுத்தம் செய்யப்படவில்லை.",
     "P2", "OPEN", 30, 0, None,
     [("Chitra", "ta", "மாரியம்மன் கோவில் தெரு கழிப்பறை சுத்தம் இல்லை, தண்ணீரும் இல்லை.")]),
    (3, "Periyar Nagar", "STREET_LIGHTS", "Street light pole damaged after storm – Periyar Nagar",
     "A street light pole in Periyar Nagar was bent in the storm.",
     "பெரியார் நகரில் புயலில் தெரு விளக்கு கம்பம் சேதமடைந்தது.",
     "P3", "RESOLVED", 500, 0, 450,
     [("Dinesh", "en", "Street light pole bent after the storm in Periyar Nagar.")]),
]


def seed(store: Store) -> None:
    now = store.now()
    for i, row in enumerate(SEED):
        (ward, location, cat, title, s_en, s_ta, prio, status, hours_ago, esc, resolved_ago, reports) = row
        tid = f"AP-{1001 + i}"
        created = now - timedelta(hours=hours_ago)
        officer = config.officer_for(cat)
        resolved_at = iso(now - timedelta(hours=resolved_ago)) if resolved_ago is not None else None
        store.insert_ticket({
            "id": tid,
            "created_at": iso(created),
            "updated_at": resolved_at or iso(created + timedelta(minutes=5)),
            "status": status,
            "category": cat,
            "title": title,
            "summary_en": s_en,
            "summary_ta": s_ta,
            "ward": ward,
            "location": location,
            "priority": prio,
            "priority_reason": "Seed data",
            "officer_id": officer["id"],
            "officer_instructions": "",
            "sla_due_at": iso(sla_due(created, prio)),
            "escalation_level": esc,
            "escalated_at": iso(sla_due(created, prio) + timedelta(minutes=10)) if esc else None,
            "report_count": len(reports),
            "language": reports[0][1],
            "emergency": prio == "P1",
            "engine": "demo data",
            "resolution_note": "Work completed and verified by ward member." if resolved_at else "",
            "resolved_at": resolved_at,
        })
        for j, (name, lang, text) in enumerate(reports):
            store.add_report(tid, text, created_at=iso(created + timedelta(hours=j * 3)), citizen_name=name, language=lang)
        store.add_event(tid, "Intake Agent", "Complaint registered", s_en, ts=iso(created))
        store.add_event(tid, "Routing Agent", "Assigned", f"{officer['name']} — {officer['role']}",
                        ts=iso(created + timedelta(minutes=1)))
        if len(reports) > 1:
            store.add_event(tid, "Duplicate Agent", "Duplicate reports merged",
                            f"{len(reports) - 1} more citizen(s) reported the same problem",
                            ts=iso(created + timedelta(hours=3 * (len(reports) - 1))))
        if status == "IN_PROGRESS":
            store.add_event(tid, officer["name"], "Work started", "Team assigned",
                            ts=iso(created + timedelta(hours=20)))
        if esc:
            target = config.escalation_target(esc, officer["id"])
            store.add_event(tid, "Escalation Agent", f"Escalated to level {esc}", f"{target['name']} ({target['role']})",
                            ts=iso(sla_due(created, prio) + timedelta(minutes=10)))
        if resolved_at:
            store.add_event(tid, officer["name"], "Marked resolved", "Work completed and verified by ward member.",
                            ts=resolved_at)
