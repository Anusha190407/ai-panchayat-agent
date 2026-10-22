# AI Panchayat — test report

Generated 22 Oct 2026, 03:30 PM IST on Python 3.13.16.

## 1. Automated test suite

`python -m unittest discover -s tests -t .` ran **37 tests, 0 failures** (OK).

Covers the offline engine (Tamil/English/Tanglish understanding, ward lookup, follow-up flow, duplicate merge, priority escalation, status updates, SLA escalation, insights), the Gemini client (request format, model fallback, output-format fallback, rate-limit cool-down, invalid key, bad JSON, photo input, safety guardrail) and every HTTP endpoint.

## 2. Scenarios with the offline engine (no API key)

**11 of 11 passed.**

| ID | Scenario | Citizen input | Expected | Actual | Result | Engine | Time |
|---|---|---|---|---|---|---|---|
| S1 | English water complaint with ward number | No drinking water in Periyar Nagar, ward 3 for the last 3 days. The pipeline near the w… | outcome: created or merged; category: WATER_SUPPLY; ward: 3; priority: P1 or P2 | outcome: created; category: WATER_SUPPLY; ward: 3; priority: P2 | ✅ Pass | offline rules | 27 ms |
| S2 | Tamil street-light complaint, ward found from the street name | காமராஜர் தெருவில் தெரு விளக்கு ஒரு வாரமாக எரியவில்லை. இரவில் பெண்கள் நடக்க பயமாக உள்ளது. | outcome: created or merged; category: STREET_LIGHTS; ward: 2; language: ta | outcome: created; category: STREET_LIGHTS; ward: 2; language: ta | ✅ Pass | offline rules | 26 ms |
| S3 | Emergency: live wire on the road gets P1 and safety advice | Electric wire has fallen on the road near the school in Gandhi Street. Children walk he… | outcome: created or merged; category: ELECTRICITY or STREET_LIGHTS; priority: P1; emergency: True | outcome: created; category: ELECTRICITY; priority: P1; emergency: True | ✅ Pass | offline rules | 20 ms |
| S4 | Duplicate drainage report is merged into the open ticket | Drainage is overflowing on Kamarajar Street, dirty water entering houses. | outcome: merged; merged_into: open Kamarajar St drain ticket | outcome: merged; merged_into: AP-1002 | ✅ Pass | offline rules | 15 ms |
| S5 | Vague complaint makes the agent ask where | Water is not coming properly | outcome: needs_info | outcome: needs_info | ✅ Pass | offline rules | 1 ms |
| S6 | Follow-up answer completes the complaint | Water is not coming properly | first_outcome: needs_info; outcome: created or merged; category: WATER_SUPPLY; ward: 3 | first_outcome: needs_info; outcome: created; category: WATER_SUPPLY; ward: 3 | ✅ Pass | offline rules | 19 ms |
| S7 | Off-topic message is politely rejected | Hi, who will win the cricket match today? | outcome: rejected | outcome: rejected | ✅ Pass | offline rules | 1 ms |
| S8 | Tanglish garbage complaint | Market road la kuppai oru vaaram ah edukkala, romba naaththam adikuthu | outcome: created or merged; category: SANITATION; ward: 5 | outcome: created; category: SANITATION; ward: 5 | ✅ Pass | offline rules | 17 ms |
| S9 | Health risk: sewage in drinking water is P1 | Sewage water is mixing with drinking water pipe in Indira Colony. Many people have fever. | outcome: created or merged; category: WATER_SUPPLY or DRAINAGE or PUBLIC_HEALTH; priority: P1 | outcome: merged; category: WATER_SUPPLY; priority: P1 | ✅ Pass | offline rules | 26 ms |
| S10 | Individual pension case is never merged | My mother's old age pension has not been credited for 3 months. We live in Thiruvalluva… | outcome: created; category: WELFARE_SCHEMES; ward: 6 | outcome: created; category: WELFARE_SCHEMES; ward: 6 | ✅ Pass | offline rules | 17 ms |
| S11 | Escalation Agent escalates overdue tickets after 3 days | Fast-forward 72 h, run sweep | escalated_min: 1 | escalated: 6 | ✅ Pass | offline rules | 80 ms |

## 3. Scenarios through the Gemini code path

Same scenarios, with the HTTP calls answered by a local server that speaks the Gemini REST format (tests/mock_gemini.py). Its triage answer deliberately under-rates the live-wire case as P3 so the safety guardrail is exercised.

**11 of 11 passed.**

| ID | Scenario | Citizen input | Expected | Actual | Result | Engine | Time |
|---|---|---|---|---|---|---|---|
| S1 | English water complaint with ward number | No drinking water in Periyar Nagar, ward 3 for the last 3 days. The pipeline near the w… | outcome: created or merged; category: WATER_SUPPLY; ward: 3; priority: P1 or P2 | outcome: created; category: WATER_SUPPLY; ward: 3; priority: P2 | ✅ Pass | gemini-3.5-flash-lite | 32 ms |
| S2 | Tamil street-light complaint, ward found from the street name | காமராஜர் தெருவில் தெரு விளக்கு ஒரு வாரமாக எரியவில்லை. இரவில் பெண்கள் நடக்க பயமாக உள்ளது. | outcome: created or merged; category: STREET_LIGHTS; ward: 2; language: ta | outcome: created; category: STREET_LIGHTS; ward: 2; language: ta | ✅ Pass | gemini-3.5-flash-lite | 29 ms |
| S3 | Emergency: live wire on the road gets P1 and safety advice | Electric wire has fallen on the road near the school in Gandhi Street. Children walk he… | outcome: created or merged; category: ELECTRICITY or STREET_LIGHTS; priority: P1; emergency: True | outcome: created; category: ELECTRICITY; priority: P1; emergency: True | ✅ Pass | gemini-3.5-flash-lite | 31 ms |
| S4 | Duplicate drainage report is merged into the open ticket | Drainage is overflowing on Kamarajar Street, dirty water entering houses. | outcome: merged; merged_into: open Kamarajar St drain ticket | outcome: merged; merged_into: AP-1002 | ✅ Pass | gemini-3.5-flash-lite | 33 ms |
| S5 | Vague complaint makes the agent ask where | Water is not coming properly | outcome: needs_info | outcome: needs_info | ✅ Pass | gemini-3.5-flash-lite | 10 ms |
| S6 | Follow-up answer completes the complaint | Water is not coming properly | first_outcome: needs_info; outcome: created or merged; category: WATER_SUPPLY; ward: 3 | first_outcome: needs_info; outcome: created; category: WATER_SUPPLY; ward: 3 | ✅ Pass | gemini-3.5-flash-lite | 34 ms |
| S7 | Off-topic message is politely rejected | Hi, who will win the cricket match today? | outcome: rejected | outcome: rejected | ✅ Pass | gemini-3.5-flash-lite | 5 ms |
| S8 | Tanglish garbage complaint | Market road la kuppai oru vaaram ah edukkala, romba naaththam adikuthu | outcome: created or merged; category: SANITATION; ward: 5 | outcome: created; category: SANITATION; ward: 5 | ✅ Pass | gemini-3.5-flash-lite | 37 ms |
| S9 | Health risk: sewage in drinking water is P1 | Sewage water is mixing with drinking water pipe in Indira Colony. Many people have fever. | outcome: created or merged; category: WATER_SUPPLY or DRAINAGE or PUBLIC_HEALTH; priority: P1 | outcome: merged; category: WATER_SUPPLY; priority: P1 | ✅ Pass | gemini-3.5-flash-lite | 29 ms |
| S10 | Individual pension case is never merged | My mother's old age pension has not been credited for 3 months. We live in Thiruvalluva… | outcome: created; category: WELFARE_SCHEMES; ward: 6 | outcome: created; category: WELFARE_SCHEMES; ward: 6 | ✅ Pass | gemini-3.5-flash-lite | 26 ms |
| S11 | Escalation Agent escalates overdue tickets after 3 days | Fast-forward 72 h, run sweep | escalated_min: 1 | escalated: 6 | ✅ Pass | gemini-3.5-flash-lite | 102 ms |

## 4. Sample agent traces (offline engine)

**S3: Emergency: live wire on the road gets P1 and safety advice**

```
1. [offline rules] Intake Agent: ⚡ Electricity & Power Lines in Ward 1, Gandhi Street, near the school. Written in English.
2. [tool] Duplicate search: Checked 13 open complaints; none match this one.
3. [offline rules] Triage & Routing Agent: P1 Critical: act within 24 hours. Assigned to EB Section Office, Electricity Board liaison (external).
4. [tool] Create ticket: Registered ticket AP-1019.
5. [tool] Notification Agent: Sent a bilingual SMS to the citizen and a work order to EB Section Office.
```

**S4: Duplicate drainage report is merged into the open ticket**

```
1. [offline rules] Intake Agent: 🌊 Drainage & Sewage in Ward 2, Kamarajar Street. Written in English.
2. [tool] Duplicate search: Checked 13 open complaints and found 1 similar (closest: AP-1002, similarity 0.91).
3. [offline rules] Duplicate Agent: Same problem as AP-1002. Same street and category as AP-1002.
4. [tool] Merge: Added this citizen to AP-1002; 4 citizens have now reported it.
5. [tool] Notification Agent: Sent a bilingual SMS to the citizen and a work order to P. Ravi.
```

**S6: Follow-up answer completes the complaint**

```
1. [offline rules] Intake Agent: Understood a drinking water supply problem but not where it is, so I'll ask the citizen.
2. [tool] Follow-up: Asked the citizen where the problem is.
3. [offline rules] Intake Agent: 💧 Drinking Water Supply in Ward 3, Anna Nagar, near the ration shop. Written in English.
4. [tool] Duplicate search: Checked 13 open complaints; none match this one.
5. [offline rules] Triage & Routing Agent: P2 High: act within 3 days. Assigned to K. Murugan, Pump Operator (Water Supply).
6. [tool] Create ticket: Registered ticket AP-1019.
7. [tool] Notification Agent: Sent a bilingual SMS to the citizen and a work order to K. Murugan.
```

**S9: Health risk: sewage in drinking water is P1**

```
1. [offline rules] Intake Agent: 💧 Drinking Water Supply in Ward 5, Indira Colony. Written in English.
2. [tool] Duplicate search: Checked 13 open complaints and found 1 similar (closest: AP-1014, similarity 0.9).
3. [offline rules] Duplicate Agent: Same problem as AP-1014. Same street and category as AP-1014.
4. [tool] Merge: Added this citizen to AP-1014; 2 citizens have now reported it. Priority raised from P3 to P1.
5. [tool] Notification Agent: Sent a bilingual SMS to the citizen and a work order to K. Murugan.
```

## 5. Live Gemini run

Run the same 11 scenarios against the real Gemini API from the deployed app's **Test lab** tab (Download results gives a JSON file), or locally with `GEMINI_API_KEY=... python -m tests.report --live`, which writes `docs/TEST_REPORT_LIVE.md`.
