# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
Run /apps/{slug}/run against each of the 5 functional SmartApps with
realistic inputs and a REAL LLM. Capture timeline + outputs.

Output:
    demo-data/results/run_report.json
    demo-data/results/run_report.md   (human-readable)
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import jwt as pyjwt
import requests

# Errors have to be findable in a wall of INFO. A failed catalogue crawl printed
# one actionable line and it scrolled past unread among two thousand others; the
# run then produced four confusing 422s that read like a different problem.
# Colour only when stderr is a terminal and NO_COLOR is unset, so a piped
# transcript stays plain text.
class _LevelColour(logging.Formatter):
    _C = {"ERROR": "\033[31m", "CRITICAL": "\033[31m", "WARNING": "\033[33m"}

    def format(self, record):
        line = super().format(record)
        colour = self._C.get(record.levelname)
        return f"{colour}{line}\033[0m" if colour else line


_FMT = "%(asctime)s %(levelname)s %(message)s"
_want_colour = sys.stderr.isatty() and not __import__("os").environ.get("NO_COLOR")
_handler = logging.StreamHandler()
_handler.setFormatter((_LevelColour if _want_colour else logging.Formatter)(_FMT))
logging.basicConfig(level=logging.INFO, handlers=[_handler])
log = logging.getLogger(__name__)


SMART_APP_URL = os.getenv("SMART_APP_SERVICE_URL", "http://localhost:9100").rstrip("/")
JWT_SECRET = os.getenv("JWT_SECRET", "test-only-not-for-prod")
TENANT_ID = "acme-cement"
RESULTS_DIR = Path(__file__).resolve().parent.parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)


def _mint() -> str:
    now = int(time.time())
    payload = {
        "sub": "demo-tester",
        "tenant_id": TENANT_ID,
        "org_id": TENANT_ID,
        "dept_ids": ["plant_ops", "quality", "sales_dispatch"],
        "roles": ["org_admin"],
        "scope": "smart-app-builder",
        "iat": now,
        "exp": now + 3600,
    }
    return pyjwt.encode(payload, JWT_SECRET, algorithm="HS256")


def _headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {_mint()}",
        "Content-Type": "application/json",
    }


# ─────────────────────────────────────────────────────────────────
# Test scenarios — one realistic input per app
# ─────────────────────────────────────────────────────────────────


SCENARIOS: List[Dict[str, Any]] = [
    {
        "app_name": "Quality Triage Co-pilot",
        "slug": "acme-cement-quality-triage",
        "action": "triage_batch",
        "inputs": {
            "batch_id": "BATCH-20260511-K1-01",
            "product_grade_claimed": "OPC_53",
            "blaine_cm2_per_g": 305,
            "strength_3day_mpa": 24.5,
            "strength_7day_mpa": 36.0,
            "strength_28day_mpa": 49.8,
            "so3_pct": 2.4,
            "mgo_pct": 1.2,
            "lsf": 0.92,
        },
        "expected": "Should HOLD or REWORK — 28-day < 53 MPa for OPC 53. Cite IS 269:2015 Cl 6.1",
    },
    {
        "app_name": "Dispatch Planner",
        "slug": "acme-cement-dispatch-planner",
        "action": "plan_dispatches",
        "inputs": {
            "as_of_date": "2026-05-11",
            "trucks_available": 25,
            "territory_filter": "Mumbai",
        },
        "expected": "Ranked dispatch sequence; some orders may be blocked on credit; reason codes",
    },
    {
        "app_name": "Customer 360",
        "slug": "acme-cement-customer-360",
        "action": "load_customer",
        "inputs": {
            "account_id": "ACC10042",
        },
        "expected": "Customer record + recent dispatches + ledger summary",
    },
    {
        "app_name": "Plant Daily Briefing",
        "slug": "acme-cement-plant-daily-briefing",
        "action": "summarise_last_24h",
        "inputs": {
            "horizon_days": 1,
        },
        "expected": "5-bullet briefing: PRODUCTION · QUALITY · DISPATCH · MAINTENANCE · SAFETY + action of the day",
    },
    {
        "app_name": "Maintenance Co-pilot",
        "slug": "acme-cement-maintenance-copilot",
        "action": "lookup_maintenance",
        "inputs": {
            "equipment_id": "K1",
            "question": "What's the refractory health, and when is next inspection due?",
        },
        "expected": "Last inspection date + refractory health assessment + recommended next action with cited SOP",
    },
]


def run_one(scenario: Dict[str, Any]) -> Dict[str, Any]:
    log.info("─" * 70)
    log.info("App: %s  (slug=%s)", scenario["app_name"], scenario["slug"])
    log.info("Action: %s", scenario["action"])
    log.info("Inputs: %s", json.dumps(scenario["inputs"], indent=2))
    log.info("Expected: %s", scenario["expected"])

    body = {
        "action": scenario["action"],
        "inputs": scenario["inputs"],
        "correlation_id": f"demo-{scenario['slug']}-{int(time.time())}",
    }
    t0 = time.perf_counter()
    try:
        r = requests.post(
            f"{SMART_APP_URL}/apps/{scenario['slug']}/run",
            json=body, headers=_headers(), timeout=600,
        )
    except requests.RequestException as exc:
        return {
            **scenario,
            "ok": False,
            "elapsed_s": time.perf_counter() - t0,
            "error": str(exc),
        }
    elapsed = time.perf_counter() - t0

    result = {
        **scenario,
        "http_status": r.status_code,
        "elapsed_s": round(elapsed, 2),
    }
    if r.status_code != 200:
        result["ok"] = False
        result["error_body"] = r.text[:2000]
        log.error("FAILED  http=%d  %.1fs  body=%s", r.status_code, elapsed, r.text[:300])
        return result

    body_out = r.json()
    result["ok"] = True
    result["status"] = body_out.get("status")
    result["correlation_id"] = body_out.get("correlation_id")
    result["outputs"] = body_out.get("outputs", {})
    result["timeline"] = body_out.get("timeline", [])
    result["error"] = body_out.get("error")

    log.info("PASSED  status=%s  elapsed=%.1fs  timeline_steps=%d",
             body_out.get("status"), elapsed, len(body_out.get("timeline", [])))
    # Print a 200-char preview of the output
    out_preview = json.dumps(body_out.get("outputs"), ensure_ascii=False)[:400]
    log.info("Outputs preview: %s", out_preview)
    return result


def write_report(results: List[Dict[str, Any]]) -> None:
    # JSON
    json_path = RESULTS_DIR / "run_report.json"
    json_path.write_text(json.dumps(results, indent=2, ensure_ascii=False, default=str),
                         encoding="utf-8")

    # Markdown
    md_lines: List[str] = []
    md_lines.append(f"# ACME Cement — Demo run report")
    md_lines.append(f"")
    md_lines.append(f"_Generated {datetime.now().isoformat()}_  ·  LLM: `{os.getenv('LLM_LARGE_MODEL', 'deepseek/deepseek-v4-flash')}`")
    md_lines.append(f"")
    md_lines.append(f"## Summary")
    md_lines.append(f"")
    passed = sum(1 for r in results if r.get("ok"))
    total = len(results)
    md_lines.append(f"- **Passed: {passed} / {total}**")
    if passed < total:
        md_lines.append(f"- Failed: {total - passed}")
    md_lines.append(f"")

    md_lines.append(f"| # | App | Action | Status | Elapsed (s) | Timeline steps |")
    md_lines.append(f"|---|---|---|---|---|---|")
    for i, r in enumerate(results, 1):
        status_emoji = "✓" if r.get("ok") else "✗"
        md_lines.append(
            f"| {i} | {r['app_name']} | `{r['action']}` | "
            f"{status_emoji} {r.get('status') or r.get('http_status', 'err')} | "
            f"{r.get('elapsed_s', '?')} | {len(r.get('timeline', []))} |"
        )
    md_lines.append("")

    for r in results:
        md_lines.append(f"## {r['app_name']}")
        md_lines.append("")
        md_lines.append(f"- **slug**: `{r['slug']}`")
        md_lines.append(f"- **action**: `{r['action']}`")
        md_lines.append(f"- **inputs**:")
        md_lines.append("  ```json")
        md_lines.append("  " + json.dumps(r["inputs"], indent=2, ensure_ascii=False).replace("\n", "\n  "))
        md_lines.append("  ```")
        md_lines.append(f"- **expected**: {r['expected']}")
        md_lines.append(f"- **HTTP status**: `{r.get('http_status', 'n/a')}`")
        md_lines.append(f"- **status**: `{r.get('status') or 'n/a'}`")
        md_lines.append(f"- **elapsed**: {r.get('elapsed_s')}s")
        md_lines.append(f"- **correlation_id**: `{r.get('correlation_id')}`")
        if r.get("ok"):
            md_lines.append(f"- **outputs**:")
            md_lines.append("  ```json")
            preview = json.dumps(r.get("outputs", {}), indent=2, ensure_ascii=False)
            md_lines.append("  " + preview.replace("\n", "\n  "))
            md_lines.append("  ```")
            timeline = r.get("timeline") or []
            if timeline:
                md_lines.append(f"- **timeline ({len(timeline)} steps)**:")
                for step in timeline:
                    if isinstance(step, dict):
                        step_name = step.get("step") or step.get("type") or step.get("kind") or "step"
                        md_lines.append(f"  - `{step_name}` — {step.get('status', '')} {step.get('duration_ms', '')}")
                    else:
                        md_lines.append(f"  - {step}")
        else:
            md_lines.append(f"- **error**:")
            md_lines.append("  ```")
            md_lines.append("  " + str(r.get("error_body") or r.get("error") or "unknown error"))
            md_lines.append("  ```")
        md_lines.append("")

    md_path = RESULTS_DIR / "run_report.md"
    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    log.info("Wrote: %s", json_path)
    log.info("Wrote: %s", md_path)


def main() -> int:
    # Health check
    try:
        h = requests.get(f"{SMART_APP_URL}/health", timeout=5).json()
        log.info("smart-app-service: %s", h)
    except Exception as exc:  # noqa: BLE001
        log.error("smart-app-service unreachable: %s", exc)
        return 2

    results = [run_one(s) for s in SCENARIOS]
    log.info("─" * 70)
    log.info("FINAL: %d/%d passed", sum(1 for r in results if r.get("ok")), len(results))
    write_report(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
