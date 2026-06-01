#!/usr/bin/env python3
"""
batch_experiments_from_prompts.py
─────────────────────────────────
웹 데모와 동일한 흐름을 로컬에서 재현:

  자연어 프롬프트
       ↓
  extract_setup_context()   ←── 1 LLM call
       ↓
  setup (company / industry / revenue / rd_budget / horizon / strategic_direction)
       ↓
  main.py 의 explicit CLI args 로 주입 (--scenario-id 사용 X)
       ↓
  outputs/<분야>/<기업>/<전략>/ 에 산출물 저장

9 scenarios × 2 strategies = 18 runs.

사용:
  cd orchestration_agent
  ../.venv/bin/python scripts/batch_experiments_from_prompts.py --dry-run
  ../.venv/bin/python scripts/batch_experiments_from_prompts.py --only samsung --strategy 기술선도
  ../.venv/bin/python scripts/batch_experiments_from_prompts.py             # 전체 18개
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

from interactive.session import extract_setup_context  # noqa: E402

# ─────────────────────────────────────────────────────────────
# 9 시나리오 프롬프트 (웹 데모에 사람이 직접 타이핑하던 그 텍스트)
# ─────────────────────────────────────────────────────────────

SCENARIOS = {
    "hyundai_mobis_autonomous_2026_2030": {
        "sector": "자율주행 플랫폼",
        "company": "Hyundai Mobis",
        "prompt": """Company: Hyundai Mobis
Industry: Autonomous Driving Platform / ADAS / Automotive Electronics
Annual Revenue: ~$43.7B
R&D Budget Ratio: ~4.3%
Annual R&D Budget: ~$1.88B
Total 5-year R&D Budget: ~$5.55B
Planning Horizon: 2026-2030 (5 years)""",
    },
    "hl_mando_autonomous_2026_2030": {
        "sector": "자율주행 플랫폼",
        "company": "HL Mando",
        "prompt": """Company: HL Mando
Industry: ADAS / Autonomous Driving / Chassis Control / Steer-by-Wire
Annual Revenue: ~$6.8B
R&D Budget Ratio: ~7.1%
Annual R&D Budget: ~$478M
Total 5-year R&D Budget: ~$1.48B
Planning Horizon: 2026-2030 (5 years)""",
    },
    "stradvision_autonomous_2026_2030": {
        "sector": "자율주행 플랫폼",
        "company": "STRADVISION",
        "prompt": """Company: STRADVISION
Industry: AI Vision Perception / ADAS Perception Software / Computer Vision
Annual Revenue: ~$13M
R&D Budget Ratio: ~66.3%
Annual R&D Budget: ~$8.6M
Total 5-year R&D Budget: ~$43M
Planning Horizon: 2026-2030 (5 years)""",
    },
    "lg_chem_battery_2026_2030": {
        "sector": "이차전지 양극재",
        "company": "LG Chem",
        "prompt": """Company: LG Chem
Industry: Cathode Materials / Advanced Materials / Battery Materials
Annual Revenue: ~$32.8B
R&D Budget Ratio: ~3.4%
Annual R&D Budget: ~$1.10B
Total 5-year R&D Budget: ~$3.63B
Planning Horizon: 2026-2030 (5 years)""",
    },
    "ecopro_bm_battery_2026_2030": {
        "sector": "이차전지 양극재",
        "company": "EcoPro BM",
        "prompt": """Company: EcoPro BM
Industry: High-Nickel Cathode Materials / Secondary Battery Materials
Annual Revenue: ~$1.81B
R&D Budget Ratio: ~2.3%
Annual R&D Budget: ~$41.6M
Total 5-year R&D Budget: ~$153M
Planning Horizon: 2026-2030 (5 years)""",
    },
    "cosmo_amt_battery_2026_2030": {
        "sector": "이차전지 양극재",
        "company": "Cosmo AM&T",
        "prompt": """Company: Cosmo AM&T
Industry: Cathode Materials / Functional Materials
Annual Revenue: ~$326M
R&D Budget Ratio: ~1.2%
Annual R&D Budget: ~$3.9M
Total 5-year R&D Budget: ~$11.7M
Planning Horizon: 2026-2030 (5 years)""",
    },
    "naver_ai_llm_2026_2030": {
        "sector": "AI LLM",
        "company": "NAVER",
        "prompt": """Company: NAVER
Industry: Large Language Model / Foundation Model / AI Platform
Annual Revenue: ~$8.6B
R&D Budget Ratio: ~25.8%
Annual R&D Budget: ~$2.22B
Total 5-year R&D Budget: ~$6.81B
Planning Horizon: 2026-2030 (5 years)""",
    },
    "saltlux_ai_llm_2026_2030": {
        "sector": "AI LLM",
        "company": "Saltlux",
        "prompt": """Company: Saltlux
Industry: Conversational AI / LLM / Knowledge Graph
Annual Revenue: ~$29.7M
R&D Budget Ratio: ~27.3%
Annual R&D Budget: ~$8.1M
Total 5-year R&D Budget: ~$27M
Planning Horizon: 2026-2030 (5 years)""",
    },
    "upstage_ai_llm_2026_2030": {
        "sector": "AI LLM",
        "company": "Upstage",
        "prompt": """Company: Upstage
Industry: Foundation Model / Document AI / LLM
Annual Revenue: ~$17.7M
R&D Budget Ratio: ~60.1%
Annual R&D Budget: ~$14.9M
Total 5-year R&D Budget: ~$54M
Planning Horizon: 2026-2030 (5 years)""",
    },
}

# ─────────────────────────────────────────────────────────────
# 2 전략 — 프롬프트에 한 줄 추가됨 (web demo 흐름과 동일)
# ─────────────────────────────────────────────────────────────

STRATEGIES = {
    "기술선도": {
        "label": "기술 선도 (Technology Leadership)",
        "prompt_line": (
            "전략: 기술 선도 (Technology Leadership) — "
            "선도 기술 확보, 공격적 R&D 투자, 장기 해자 구축, 프론티어 기술 선점"
        ),
    },
    "시장이익최대": {
        "label": "시장 이익 최대 (Market Profit Maximization)",
        "prompt_line": (
            "전략: 시장 이익 최대 (Market Profit Maximization) — "
            "단기 상용화, 원가 우위, 검증된 기술 확대, 매출/마진 성장"
        ),
    },
}

ARTIFACTS = [
    "tech_candidates.json",
    "planned_roadmap.json",
    "investment_strategy.json",
    "orchestrator_report.json",
]


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def total_budget_from_prompt(prompt: str, fallback_annual_rd: float) -> int:
    """'Total 5-year R&D Budget: ~$116.2B' → 116_200_000_000."""
    m = re.search(
        r"Total\s*5-year\s*R&D\s*Budget[:\s~$]*([\d.]+)\s*([BMK]?)",
        prompt, re.IGNORECASE,
    )
    if m:
        val = float(m.group(1))
        mult = {"B": 1e9, "M": 1e6, "K": 1e3, "": 1e9}[m.group(2).upper()]
        return int(val * mult)
    if fallback_annual_rd:
        return int(fallback_annual_rd * 5)
    return 0


# 전역 patent_map 모드 — main() 에서 CLI flag 로 세팅
PATENT_MAP_MODE = "on"   # "on" or "off"


def patent_map_folder() -> str:
    return "특허맵_On" if PATENT_MAP_MODE == "on" else "특허맵_Off"


def target_dir(scenario_id: str, strategy_key: str) -> Path:
    s = SCENARIOS[scenario_id]
    return OUTPUTS_DIR / patent_map_folder() / s["sector"] / s["company"] / strategy_key


def is_completed(scenario_id: str, strategy_key: str) -> bool:
    return (target_dir(scenario_id, strategy_key) / "orchestrator_report.json").exists()


def build_full_prompt(scenario_id: str, strategy_key: str) -> str:
    return SCENARIOS[scenario_id]["prompt"] + "\n" + STRATEGIES[strategy_key]["prompt_line"]


# ─────────────────────────────────────────────────────────────
# Single run
# ─────────────────────────────────────────────────────────────

def run_one(scenario_id: str, strategy_key: str, *,
            dry_run: bool = False,
            verbose_extract: bool = False) -> dict:
    s = SCENARIOS[scenario_id]
    full_prompt = build_full_prompt(scenario_id, strategy_key)
    tgt = target_dir(scenario_id, strategy_key)
    tgt.mkdir(parents=True, exist_ok=True)

    # 1) 추출 (웹 데모와 동일)
    print(f"  ┌─ extract_setup_context()")
    setup = extract_setup_context(full_prompt)
    print(f"  │   company_name      = {setup.get('company_name')!r}")
    print(f"  │   industry/domain   = {setup.get('industry')!r}")
    print(f"  │   annual_revenue    = {setup.get('annual_revenue'):,.0f} USD")
    print(f"  │   annual_rd_budget  = {setup.get('annual_rd_budget'):,.0f} USD")
    print(f"  │   planning_horizon  = {setup.get('planning_horizon')!r}")
    print(f"  │   reference_year    = {setup.get('reference_year')}")
    print(f"  │   strategic_direction:")
    for b in setup.get("strategic_direction") or []:
        print(f"  │     • {b}")

    total_budget = total_budget_from_prompt(full_prompt, setup.get("annual_rd_budget", 0))

    # 추출된 시나리오를 평가용 로그 파일로 보존
    extracted_log = {
        "scenario_id": scenario_id,
        "strategy_key": strategy_key,
        "strategy_label": STRATEGIES[strategy_key]["label"],
        "prompt": full_prompt,
        "setup": setup,
        "total_budget_usd": total_budget,
    }
    (tgt / "extracted_setup.json").write_text(
        json.dumps(extracted_log, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # company_name 은 폴더와 일관되도록 시나리오 정의값 우선
    company_name = s["company"]

    # priorities: 추출된 strategic_direction 을 그대로 사용 (web demo 와 동일)
    priorities = " | ".join(setup.get("strategic_direction") or [])
    if not priorities:
        priorities = STRATEGIES[strategy_key]["label"]

    prefix = f"batch_{scenario_id}__{strategy_key}__"
    env = os.environ.copy()

    cmd = [
        sys.executable, "main.py",
        "--domain", setup["domain"],
        "--year", str(int(setup["reference_year"])),
        "--categories", ",".join(setup.get("category_hints") or []),
        "--industry", setup["industry"],
        "--company-name", company_name,
        "--company-profile", full_prompt,
        "--time-horizon", setup.get("planning_horizon") or "2026-2030",
        "--budget", str(float(total_budget)),
        "--priorities", priorities,
        "--out-prefix", prefix,
        "--use-patent-map" if PATENT_MAP_MODE == "on" else "--no-use-patent-map",
    ]
    print(f"  ├─ subprocess:")
    for arg in cmd:
        if arg.startswith("--"):
            print(f"  │     {arg}", end="")
        elif arg == sys.executable or arg == "main.py":
            print(f"  │   {arg}")
        else:
            disp = arg if len(arg) < 90 else arg[:87] + "..."
            print(f" {disp}")

    if dry_run:
        print(f"  └─ (dry-run) — 실행 안 함\n")
        return {"ok": True, "dry_run": True, "tgt": str(tgt)}

    log_path = tgt / "stdout.log"
    t0 = time.time()
    with log_path.open("w", encoding="utf-8") as logf:
        logf.write(f"# {' '.join(cmd)}\n")
        logf.write(f"# strategy: {STRATEGIES[strategy_key]['label']}\n")
        logf.write(f"# prompt:\n{full_prompt}\n\n")
        logf.flush()
        proc = subprocess.run(
            cmd, cwd=str(ORCH_DIR), env=env,
            stdout=logf, stderr=subprocess.STDOUT, text=True,
        )
    dt = time.time() - t0

    # 산출물 이동
    moved = []
    for fn in ARTIFACTS:
        src = OUTPUTS_DIR / f"{prefix}{fn}"
        if src.exists():
            shutil.copy2(src, tgt / fn)
            moved.append(fn)
    for src in OUTPUTS_DIR.glob(f"{prefix}iter*_*.json"):
        shutil.copy2(src, tgt / src.name[len(prefix):])

    # Markdown + HTML 보고서 생성 (web session 과 동일한 render)
    report_json_path = tgt / "orchestrator_report.json"
    tc_path = tgt / "tech_candidates.json"
    rd_path = tgt / "planned_roadmap.json"
    is_path = tgt / "investment_strategy.json"
    if report_json_path.exists():
        try:
            from report_export import generate_markdown_report, generate_html_report

            def _load(p: Path) -> dict:
                try:
                    return json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    return {}

            rep = _load(report_json_path)
            tc = _load(tc_path)
            rd = _load(rd_path)
            ist = _load(is_path)
            report_dict = {
                "problem_frame": rep.get("problem_frame"),
                "active_agents": rep.get("active_agents"),
                "iteration": rep.get("iteration"),
                "review": rep.get("review"),
                "review_history": rep.get("review_history") or [],
                "artifact_paths": rep.get("artifact_paths"),
                "tech_candidates": tc.get("tech_candidates") or [],
                "planned_roadmap": rd.get("planned_roadmap") or [],
                "investment_strategy": ist.get("investment_strategy") or [],
            }
            (tgt / "orchestrator_report.md").write_text(
                generate_markdown_report(report_dict), encoding="utf-8")
            (tgt / "orchestrator_report.html").write_text(
                generate_html_report(report_dict), encoding="utf-8")
            moved.extend(["orchestrator_report.md", "orchestrator_report.html"])
        except Exception as e:
            print(f"  │   ⚠️ MD/HTML 생성 실패: {e}")

    ok = proc.returncode == 0 and "orchestrator_report.json" in moved
    status = "✅" if ok else "❌"
    expected_total = len(ARTIFACTS) + 2  # +md, +html
    print(f"  └─ {status}  {dt:.0f}s, return={proc.returncode}, "
          f"artifacts={len(moved)}/{expected_total} → {tgt}\n")
    return {"ok": ok, "dt": dt, "rc": proc.returncode, "tgt": str(tgt),
            "moved": moved, "log": str(log_path)}


# ─────────────────────────────────────────────────────────────
# Driver
# ─────────────────────────────────────────────────────────────

def main():
    global PATENT_MAP_MODE
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="추출 + 명령만 출력 (서브프로세스 실행 안 함)")
    ap.add_argument("--only", nargs="+", default=None,
                    help="시나리오 ID 부분일치 필터")
    ap.add_argument("--strategy", choices=list(STRATEGIES.keys()), default=None,
                    help="한 전략만 실행")
    ap.add_argument("--patent-map", choices=["on", "off"], default="on",
                    help="patent map 사용 여부. on=특허맵_On/ 폴더에 저장, off=특허맵_Off/")
    ap.add_argument("--force", action="store_true",
                    help="완료된 실험도 재실행")
    args = ap.parse_args()
    PATENT_MAP_MODE = args.patent_map

    runs = []
    for sid in SCENARIOS.keys():
        if args.only and not any(k in sid for k in args.only):
            continue
        for skey in STRATEGIES.keys():
            if args.strategy and skey != args.strategy:
                continue
            runs.append((sid, skey))

    print(f"════════════════════════════════════════════════════════════")
    print(f"  Prompt-driven Batch — 총 {len(runs)} runs")
    print(f"  dry_run={args.dry_run}  force={args.force}")
    print(f"════════════════════════════════════════════════════════════")

    done, fail, skipped = 0, 0, 0
    total = len(runs)
    for idx, (sid, skey) in enumerate(runs, start=1):
        s = SCENARIOS[sid]
        print(f"\n[{idx}/{total}] [{s['sector']}] {s['company']} × {skey}  ({STRATEGIES[skey]['label']})")
        if is_completed(sid, skey) and not args.force:
            print(f"  ⏭️  skip — 이미 완료: {target_dir(sid, skey)}")
            skipped += 1
            continue
        res = run_one(sid, skey, dry_run=args.dry_run)
        if res.get("ok"):
            done += 1
        else:
            fail += 1

    print(f"\n════════════════════════════════════════════════════════════")
    print(f"  완료: {done}  실패: {fail}  스킵: {skipped}  (총 {total})")
    print(f"════════════════════════════════════════════════════════════")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
