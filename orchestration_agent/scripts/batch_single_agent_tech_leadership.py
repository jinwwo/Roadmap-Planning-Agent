#!/usr/bin/env python3
"""
batch_single_agent_tech_leadership.py
─────────────────────────────────────
Run the 9 predefined scenarios with the Technology Leadership strategy in
single-agent baseline mode.

Outputs are stored with the same sector/company/strategy shape as the
multi-agent batch results, but under:

  outputs/Tool_Single_Agent/특허맵_On/<sector>/<company>/기술선도/

Usage:
  cd orchestration_agent
  python scripts/batch_single_agent_tech_leadership.py --dry-run
  python scripts/batch_single_agent_tech_leadership.py
  python scripts/batch_single_agent_tech_leadership.py --only naver upstage
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ORCH_DIR = HERE.parent
OUTPUTS_DIR = ORCH_DIR / "outputs"
sys.path.insert(0, str(ORCH_DIR))

from report_export import generate_html_report, generate_markdown_report  # noqa: E402
from scripts.batch_experiments_from_prompts import (  # noqa: E402
    ARTIFACTS,
    SCENARIOS,
    STRATEGIES,
    build_full_prompt,
    total_budget_from_prompt,
)


DEFAULT_STRATEGY = "기술선도"
DEFAULT_CATEGORIES = ["Equipment", "Material", "Process", "Architecture", "Packaging"]


def _parse_money(text: str) -> float:
    match = re.search(r"~?\$?([\d.]+)\s*([BMK]?)", text or "", re.IGNORECASE)
    if not match:
        return 0.0
    value = float(match.group(1))
    unit = match.group(2).upper()
    multiplier = {"B": 1e9, "M": 1e6, "K": 1e3, "": 1.0}[unit]
    return value * multiplier


def _parse_ratio(text: str) -> float:
    match = re.search(r"([\d.]+)\s*%", text or "")
    return float(match.group(1)) / 100.0 if match else 0.0


def _line_value(prompt: str, label: str) -> str:
    pattern = rf"^{re.escape(label)}\s*:\s*(.+)$"
    match = re.search(pattern, prompt, flags=re.IGNORECASE | re.MULTILINE)
    return match.group(1).strip() if match else ""


def _fallback_setup(scenario_id: str, full_prompt: str) -> dict:
    scenario = SCENARIOS[scenario_id]
    industry = _line_value(full_prompt, "Industry") or scenario["sector"]
    horizon = _line_value(full_prompt, "Planning Horizon") or "2026-2030 (5 years)"
    year_match = re.search(r"20\d{2}\s*[-–]\s*(20\d{2})", horizon)
    reference_year = int(year_match.group(1)) if year_match else 2030
    return {
        "company_name": scenario["company"],
        "industry": industry,
        "annual_revenue": _parse_money(_line_value(full_prompt, "Annual Revenue")),
        "rd_budget_ratio": _parse_ratio(_line_value(full_prompt, "R&D Budget Ratio")),
        "annual_rd_budget": _parse_money(_line_value(full_prompt, "Annual R&D Budget")),
        "planning_horizon": horizon,
        "objective": "선도 기술 확보와 장기 기술 해자 구축을 중심으로 2026-2030 기술 로드맵을 수립한다.",
        "strategic_direction": [
            "선도 기술 확보 및 핵심 기술 리더십 강화",
            "공격적 R&D 투자를 통한 프론티어 기술 선점",
            "장기 기술 해자와 플랫폼/생태계 확장 기반 구축",
        ],
        "domain": industry,
        "reference_year": reference_year,
        "category_hints": DEFAULT_CATEGORIES,
    }


def _existing_multi_setup_path(scenario_id: str, strategy_key: str) -> Path:
    scenario = SCENARIOS[scenario_id]
    return (
        OUTPUTS_DIR
        / "특허맵_On"
        / scenario["sector"]
        / scenario["company"]
        / strategy_key
        / "extracted_setup.json"
    )


def load_setup(scenario_id: str, strategy_key: str, full_prompt: str) -> tuple[dict, str]:
    existing = _existing_multi_setup_path(scenario_id, strategy_key)
    if existing.exists():
        try:
            data = json.loads(existing.read_text(encoding="utf-8"))
            setup = data.get("setup")
            if isinstance(setup, dict) and setup.get("domain"):
                return setup, str(existing)
        except Exception:
            pass
    return _fallback_setup(scenario_id, full_prompt), "deterministic_fallback"


def patent_map_folder(mode: str) -> str:
    return "특허맵_On" if mode == "on" else "특허맵_Off"


def target_dir(
    scenario_id: str,
    *,
    strategy_key: str,
    patent_map: str,
    output_root: str,
) -> Path:
    scenario = SCENARIOS[scenario_id]
    parts = [OUTPUTS_DIR]
    if output_root:
        parts.append(output_root)
    parts.extend([
        patent_map_folder(patent_map),
        scenario["sector"],
        scenario["company"],
        strategy_key,
    ])
    return Path(*parts)


def is_completed(
    scenario_id: str,
    *,
    strategy_key: str,
    patent_map: str,
    output_root: str,
) -> bool:
    return (
        target_dir(
            scenario_id,
            strategy_key=strategy_key,
            patent_map=patent_map,
            output_root=output_root,
        )
        / "orchestrator_report.json"
    ).exists()


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_rendered_reports(target: Path) -> list[str]:
    report_json = _load_json(target / "orchestrator_report.json")
    tech = _load_json(target / "tech_candidates.json")
    roadmap = _load_json(target / "planned_roadmap.json")
    strategy = _load_json(target / "investment_strategy.json")
    if not report_json:
        return []

    report_dict = {
        "problem_frame": report_json.get("problem_frame"),
        "active_agents": report_json.get("active_agents"),
        "iteration": report_json.get("iteration"),
        "review": report_json.get("review"),
        "review_history": report_json.get("review_history") or [],
        "artifact_paths": report_json.get("artifact_paths"),
        "tech_candidates": tech.get("tech_candidates") or [],
        "planned_roadmap": roadmap.get("planned_roadmap") or [],
        "investment_strategy": strategy.get("investment_strategy") or [],
    }
    (target / "orchestrator_report.md").write_text(
        generate_markdown_report(report_dict),
        encoding="utf-8",
    )
    (target / "orchestrator_report.html").write_text(
        generate_html_report(report_dict),
        encoding="utf-8",
    )
    return ["orchestrator_report.md", "orchestrator_report.html"]


def run_one(
    scenario_id: str,
    *,
    patent_map: str,
    output_root: str,
    run_mode: str,
    dry_run: bool = False,
) -> dict:
    strategy_key = DEFAULT_STRATEGY
    scenario = SCENARIOS[scenario_id]
    full_prompt = build_full_prompt(scenario_id, strategy_key)
    target = target_dir(
        scenario_id,
        strategy_key=strategy_key,
        patent_map=patent_map,
        output_root=output_root,
    )
    target.mkdir(parents=True, exist_ok=True)

    print("  ┌─ load_setup()")
    setup, setup_source = load_setup(scenario_id, strategy_key, full_prompt)
    print(f"  │   company_name      = {setup.get('company_name')!r}")
    print(f"  │   industry/domain   = {setup.get('industry')!r}")
    print(f"  │   reference_year    = {setup.get('reference_year')}")
    print(f"  │   setup_source      = {setup_source}")
    print("  │   strategic_direction:")
    for item in setup.get("strategic_direction") or []:
        print(f"  │     • {item}")

    total_budget = total_budget_from_prompt(
        full_prompt,
        setup.get("annual_rd_budget", 0),
    )
    extracted = {
        "run_mode": run_mode,
        "scenario_id": scenario_id,
        "strategy_key": strategy_key,
        "strategy_label": STRATEGIES[strategy_key]["label"],
        "patent_map": patent_map,
        "setup_source": setup_source,
        "prompt": full_prompt,
        "setup": setup,
        "total_budget_usd": total_budget,
    }
    (target / "extracted_setup.json").write_text(
        json.dumps(extracted, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    priorities = " | ".join(setup.get("strategic_direction") or [])
    if not priorities:
        priorities = STRATEGIES[strategy_key]["label"]

    prefix_mode = "tool_single" if run_mode == "tool-single" else "single"
    prefix = f"{prefix_mode}_{scenario_id}__{strategy_key}__"
    cmd = [
        sys.executable,
        "main.py",
        "--run-mode",
        run_mode,
        "--domain",
        setup["domain"],
        "--year",
        str(int(setup["reference_year"])),
        "--categories",
        ",".join(setup.get("category_hints") or []),
        "--industry",
        setup["industry"],
        "--company-name",
        scenario["company"],
        "--company-profile",
        full_prompt,
        "--time-horizon",
        setup.get("planning_horizon") or "2026-2030",
        "--budget",
        str(float(total_budget)),
        "--priorities",
        priorities,
        "--out-prefix",
        prefix,
        "--use-patent-map" if patent_map == "on" else "--no-use-patent-map",
    ]

    print("  ├─ subprocess:")
    print("  │   " + " ".join(cmd[:4]) + " ...")
    print(f"  │   run_mode={run_mode!r}")
    print(f"  │   out_prefix={prefix!r}")
    print(f"  │   target={target}")
    if dry_run:
        print("  └─ (dry-run) — 실행 안 함\n")
        return {"ok": True, "dry_run": True, "target": str(target)}

    env = os.environ.copy()
    log_path = target / "stdout.log"
    started = time.time()
    with log_path.open("w", encoding="utf-8") as logf:
        logf.write(f"# {' '.join(cmd)}\n")
        logf.write(f"# run_mode: {run_mode}\n")
        logf.write(f"# strategy: {STRATEGIES[strategy_key]['label']}\n")
        logf.write(f"# patent_map: {patent_map}\n")
        logf.write(f"# prompt:\n{full_prompt}\n\n")
        logf.flush()
        proc = subprocess.run(
            cmd,
            cwd=str(ORCH_DIR),
            env=env,
            stdout=logf,
            stderr=subprocess.STDOUT,
            text=True,
        )
    elapsed = time.time() - started

    moved = []
    for filename in ARTIFACTS:
        source = OUTPUTS_DIR / f"{prefix}{filename}"
        if source.exists():
            shutil.copy2(source, target / filename)
            moved.append(filename)

    try:
        moved.extend(_write_rendered_reports(target))
    except Exception as exc:
        print(f"  │   ⚠️ MD/HTML 생성 실패: {exc}")

    ok = proc.returncode == 0 and "orchestrator_report.json" in moved
    status = "✅" if ok else "❌"
    print(
        f"  └─ {status} {elapsed:.0f}s, return={proc.returncode}, "
        f"artifacts={len(moved)} → {target}\n"
    )
    return {
        "ok": ok,
        "rc": proc.returncode,
        "dt": elapsed,
        "target": str(target),
        "moved": moved,
        "log": str(log_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run 9 Technology Leadership scenarios in single-agent mode.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--only",
        nargs="+",
        default=None,
        help="Scenario id partial-match filter, e.g. naver upstage battery",
    )
    parser.add_argument(
        "--patent-map",
        choices=["on", "off"],
        default="on",
        help="Single-agent patent-map context setting.",
    )
    parser.add_argument(
        "--run-mode",
        choices=["single", "tool-single"],
        default="tool-single",
        help="single=pure LLM baseline, tool-single=patent/market tools + one LLM.",
    )
    parser.add_argument(
        "--output-root",
        default="",
        help="Subfolder under orchestration_agent/outputs. Default: Tool_Single_Agent for tool-single, Single_Agent for single.",
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    output_root = args.output_root or ("Tool_Single_Agent" if args.run_mode == "tool-single" else "Single_Agent")

    runs = []
    for scenario_id in SCENARIOS.keys():
        if args.only and not any(token in scenario_id for token in args.only):
            continue
        runs.append(scenario_id)

    print("════════════════════════════════════════════════════════════")
    print(f"  Single-Agent Batch — Technology Leadership only")
    print(f"  runs={len(runs)}  run_mode={args.run_mode}  patent_map={args.patent_map}  dry_run={args.dry_run}  force={args.force}")
    print(f"  output_root={output_root!r}")
    print("════════════════════════════════════════════════════════════")

    done = failed = skipped = 0
    for idx, scenario_id in enumerate(runs, 1):
        scenario = SCENARIOS[scenario_id]
        print(f"\n[{idx}/{len(runs)}] [{scenario['sector']}] {scenario['company']} × {DEFAULT_STRATEGY}")
        if (
            is_completed(
                scenario_id,
                strategy_key=DEFAULT_STRATEGY,
                patent_map=args.patent_map,
                output_root=output_root,
            )
            and not args.force
        ):
            print(
                "  ⏭️  skip — 이미 완료: "
                f"{target_dir(scenario_id, strategy_key=DEFAULT_STRATEGY, patent_map=args.patent_map, output_root=output_root)}"
            )
            skipped += 1
            continue
        result = run_one(
            scenario_id,
            patent_map=args.patent_map,
            output_root=output_root,
            run_mode=args.run_mode,
            dry_run=args.dry_run,
        )
        if result.get("ok"):
            done += 1
        else:
            failed += 1

    print("\n════════════════════════════════════════════════════════════")
    print(f"  완료: {done}  실패: {failed}  스킵: {skipped}  (총 {len(runs)})")
    print("════════════════════════════════════════════════════════════")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
