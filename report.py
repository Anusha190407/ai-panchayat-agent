"""Write a Markdown test report with real results.

    python -m tests.report            # unit tests + scenarios (offline engine and local Gemini-protocol mock)
    python -m tests.report --live     # scenarios against the real Gemini API (needs GEMINI_API_KEY)
"""
from __future__ import annotations

import io
import json
import os
import platform
import sys
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from panchayat import scenarios  # noqa: E402
from panchayat.llm import GeminiClient  # noqa: E402


def ist_now() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime("%d %b %Y, %I:%M %p IST")


def fmt_expect(exp: dict) -> str:
    parts = []
    for k, v in exp.items():
        if isinstance(v, list):
            v = " or ".join(str(x) for x in v)
        parts.append(f"{k}: {v}")
    return "; ".join(parts).replace("@kamarajar_drain", "open Kamarajar St drain ticket")


def fmt_actual(r: dict) -> str:
    return "; ".join(f"{c['field']}: {c['actual']}" for c in r["checks"]) or r.get("error", "")


def scenario_table(results: list[dict]) -> str:
    lines = ["| ID | Scenario | Citizen input | Expected | Actual | Result | Engine | Time |",
             "|---|---|---|---|---|---|---|---|"]
    by_id = {s["id"]: s for s in scenarios.SCENARIOS}
    for r in results:
        inp = r["input"].replace("|", "/")
        if len(inp) > 90:
            inp = inp[:87] + "…"
        lines.append(f"| {r['id']} | {r['name']} | {inp} | {fmt_expect(by_id[r['id']]['expect'])} | "
                     f"{fmt_actual(r)} | {'✅ Pass' if r['passed'] else '❌ Fail'} | {', '.join(r['engines'])} | {r['ms']} ms |")
    return "\n".join(lines)


def trace_block(r: dict) -> str:
    out = [f"**{r['id']}: {r['name']}**", "", "```"]
    for i, step in enumerate(r["trace"], 1):
        out.append(f"{i}. [{step.get('engine')}] {step.get('agent')}: {step.get('summary')}")
    out += ["```", ""]
    return "\n".join(out)


def run_unit_tests() -> tuple[int, int, str]:
    stream = io.StringIO()
    suite = unittest.defaultTestLoader.discover(os.path.join(ROOT, "tests"), top_level_dir=ROOT)
    result = unittest.TextTestRunner(stream=stream, verbosity=1).run(suite)
    return result.testsRun, len(result.failures) + len(result.errors), stream.getvalue().strip().splitlines()[-1]


def main() -> None:
    live = "--live" in sys.argv
    lines = [f"# AI Panchayat — test report", "", f"Generated {ist_now()} on Python {platform.python_version()}.", ""]
    if live:
        llm = GeminiClient()
        if not llm.api_key:
            sys.exit("Set GEMINI_API_KEY first.")
        results = list(scenarios.run_many(llm=llm, use_llm=True, pause=1.5))
        passed = sum(r["passed"] for r in results)
        lines += ["## Scenarios against the live Gemini API", "",
                  f"**{passed} of {len(results)} passed.** Model used: {llm.status()['model']}.", "",
                  scenario_table(results), "", "## Agent traces", ""]
        lines += [trace_block(r) for r in results if r["id"] in ("S3", "S4", "S9")]
        path = os.path.join(ROOT, "docs", "TEST_REPORT_LIVE.md")
    else:
        ran, failed, summary = run_unit_tests()
        offline = list(scenarios.run_many(use_llm=False))
        from tests.mock_gemini import API_KEY, MockGemini
        with MockGemini() as mock:
            mocked = list(scenarios.run_many(llm=GeminiClient(api_key=API_KEY, model="", base_url=mock.base_url), use_llm=True))
        lines += [
            "## 1. Automated test suite", "",
            f"`python -m unittest discover -s tests -t .` ran **{ran} tests, {failed} failures** ({summary}).", "",
            "Covers the offline engine (Tamil/English/Tanglish understanding, ward lookup, follow-up flow, duplicate merge, "
            "priority escalation, status updates, SLA escalation, insights), the Gemini client (request format, model "
            "fallback, output-format fallback, rate-limit cool-down, invalid key, bad JSON, photo input, safety guardrail) "
            "and every HTTP endpoint.", "",
            "## 2. Scenarios with the offline engine (no API key)", "",
            f"**{sum(r['passed'] for r in offline)} of {len(offline)} passed.**", "",
            scenario_table(offline), "",
            "## 3. Scenarios through the Gemini code path", "",
            "Same scenarios, with the HTTP calls answered by a local server that speaks the Gemini REST format "
            "(tests/mock_gemini.py). Its triage answer deliberately under-rates the live-wire case as P3 so the safety "
            "guardrail is exercised.", "",
            f"**{sum(r['passed'] for r in mocked)} of {len(mocked)} passed.**", "",
            scenario_table(mocked), "",
            "## 4. Sample agent traces (offline engine)", "",
        ]
        lines += [trace_block(r) for r in offline if r["id"] in ("S3", "S4", "S6", "S9")]
        lines += ["## 5. Live Gemini run", "",
                  "Run the same 11 scenarios against the real Gemini API from the deployed app's **Test lab** tab "
                  "(Download results gives a JSON file), or locally with `GEMINI_API_KEY=... python -m tests.report --live`, "
                  "which writes `docs/TEST_REPORT_LIVE.md`.", ""]
        path = os.path.join(ROOT, "docs", "TEST_REPORT.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    print(f"wrote {path}")
    if not live:
        print(json.dumps({"unit_tests": ran, "failed": failed}))


if __name__ == "__main__":
    main()
