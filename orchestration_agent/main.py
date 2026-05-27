"""
main.py
────────
Orchestration Agent 진입점.

사용 예:
  # 세 Agent 모두 ON (기본)
  python main.py

  # Investment Strategist 만 OFF (Agent 3 제외)
  python main.py --agent "1 2"

  # Tech Analysis 만 ON
  python main.py --agent "1"

  # 예산 / 목표 override
  python main.py --budget 3000000000 \
                 --objective "2nm 로드맵 최적화"

  # 비교 실험용 prefix (outputs/ 내 파일 덮어쓰기 방지)
  python main.py --agent "1 2 3" --out-prefix "full_"
  python main.py --agent "1 2"   --out-prefix "noA3_"
  python main.py --agent "1"     --out-prefix "onlyA1_"

결과물 (outputs/ 하위):
  <prefix>tech_candidates.json       : Agent 1 결과
  <prefix>planned_roadmap.json       : Agent 2 결과
  <prefix>investment_strategy.json   : Agent 3 결과
  <prefix>orchestrator_report.json   : Orchestrator 최종 TRM 보고서
"""

import argparse
import json
import os

from config import (
    validate_config,
    DEFAULT_INDUSTRY,
    DEFAULT_COMPANY_TYPE,
    DEFAULT_TIME_HORIZON,
    DEFAULT_TOTAL_BUDGET,
    DEFAULT_OBJECTIVE,
    DEFAULT_PRIORITIES,
    DEFAULT_FUTURE_TREND_SUMMARY,
    OUTPUTS_DIR,
    FILE_ORCHESTRATOR_REPORT,
)
from pipeline import run_orchestration, run_single_agent_orchestration
from scenario_loader import load_scenario


# ──────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────

def _parse_agent_flag(raw: str) -> list:
    """
    '--agent "1 2 3"'  →  ["1", "2", "3"]
    '--agent 1,3'      →  ["1", "3"]
    '--agent 2'        →  ["2"]
    """
    tokens = [t.strip() for t in raw.replace(",", " ").split() if t.strip()]
    valid = [t for t in tokens if t in ("1", "2", "3")]
    if not valid:
        raise argparse.ArgumentTypeError(
            f"--agent 값이 올바르지 않습니다: {raw!r} (예: '1 2 3')"
        )
    return valid


def parse_args():
    p = argparse.ArgumentParser(
        description="Technology Roadmap Orchestration Agent",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    # 분석 도메인 입력
    p.add_argument(
        "--scenario-id",
        dest="scenario_id",
        type=str,
        default="",
        help="orchestration_agent/scenarios/<id>.json 에서 구조화된 데모 시나리오 로드",
    )
    p.add_argument(
        "--run-mode",
        dest="run_mode",
        type=str,
        default="multi",
        choices=["multi", "single"],
        help="multi=기존 multi-agent 파이프라인, single=단일 LLM baseline",
    )
    p.add_argument(
        "--domain", type=str,
        default="AI / Semiconductor / GPU",
        help="Technology Analysis 대상 도메인",
    )
    p.add_argument("--year", type=int, default=2030, help="기준 연도")
    p.add_argument(
        "--categories", type=str,
        default="Architecture,Packaging,Process,Equipment",
        help="카테고리 힌트 (콤마 구분)",
    )
    # 활성 Agent
    p.add_argument(
        "--agent", type=_parse_agent_flag, default="1 2 3",
        help='활성화할 Agent 번호 (예: "1 2 3", "1 3", "2")',
    )
    # Problem Frame override
    p.add_argument("--industry", type=str, default=DEFAULT_INDUSTRY)
    p.add_argument("--company-type", dest="company_type", type=str, default=DEFAULT_COMPANY_TYPE)
    p.add_argument(
        "--company-name",
        dest="company_name",
        type=str,
        default="NVIDIA",
        help="Patent Agent 가 중심 actor 로 사용할 우리 기업명",
    )
    p.add_argument(
        "--company-profile",
        dest="company_profile",
        type=str,
        default=(
            "Company Scenario: NVIDIA. Industry: AI / Semiconductor / GPU. "
            "Annual Revenue: ~60B USD. R&D Budget Ratio: ~20%. "
            "Annual R&D Budget: ~12B USD. Planning Horizon: 2026-2030 (5 years). "
            "Strategic Direction: Maintain leadership in AI hardware (GPU); "
            "expand AI infrastructure and platform ecosystem; strengthen end-to-end "
            "AI stack (hardware + software)."
        ),
        help="Patent Agent 가 관련 기업/기술 후보를 해석할 때 사용할 우리 기업 정보",
    )
    p.add_argument(
        "--related-companies",
        dest="related_companies",
        type=str,
        default="",
        help="관련 기업명 직접 지정 (콤마 구분). 비우면 LLM/fallback 으로 탐색",
    )
    p.add_argument("--time-horizon", dest="time_horizon", type=str, default=DEFAULT_TIME_HORIZON)
    p.add_argument("--budget", type=float, default=DEFAULT_TOTAL_BUDGET, help="총 예산 (USD)")
    p.add_argument("--objective", type=str, default=DEFAULT_OBJECTIVE)
    p.add_argument(
        "--priorities", type=str,
        default="|".join(DEFAULT_PRIORITIES),
        help="전략 우선순위 (파이프 `|` 구분)",
    )
    p.add_argument(
        "--future-trend", dest="future_trend", type=str,
        default=DEFAULT_FUTURE_TREND_SUMMARY,
        help="Orchestrator Review 에 전달할 미래 기술 동향 요약",
    )
    # Agent 3 의 stage 집계 모드
    p.add_argument(
        "--stage-mode", dest="stage_mode", type=str, default="phase",
        choices=["phase", "horizon"],
        help="Investment Strategist 의 stage 집계 방식",
    )
    p.add_argument(
        "--patent-method", dest="patent_method", type=str, default="C_company_portfolio",
        choices=["A_current", "B_lee2009", "C_company_portfolio"],
        help="Technology Analyst Patent Agent prompt/method variant",
    )
    p.add_argument(
        "--use-patent-map",
        dest="use_patent_map",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Market Agent가 Patent Agent actor similarity map을 사용할지 여부. 미지정 시 .env USE_PATENT_MAP 값을 사용",
    )
    # 결과 저장 경로 prefix (비교 실험 시 파일 덮어쓰기 방지용)
    p.add_argument(
        "--out-prefix", dest="out_prefix", type=str, default="",
        help="outputs/ 내 파일명 앞에 붙일 prefix (예: 'abl_noA3_')",
    )
    return p.parse_args()


# ──────────────────────────────────────────────────────────────
# Entry
# ──────────────────────────────────────────────────────────────

def main():
    args = parse_args()

    # 환경/폴더 검증 (sibling 3개 폴더 존재 확인 포함)
    validate_config()

    scenario = load_scenario(args.scenario_id) if args.scenario_id else None
    if scenario:
        args.domain = scenario.get("domain") or args.domain
        args.year = int(scenario.get("reference_year") or args.year)
        args.categories = ",".join(scenario.get("category_hints") or []) or args.categories
        args.industry = scenario.get("industry") or args.industry
        args.company_type = scenario.get("company_type") or args.company_type
        args.company_name = scenario.get("company_name") or args.company_name
        args.company_profile = scenario.get("company_profile") or args.company_profile
        args.related_companies = ",".join(scenario.get("related_companies") or []) or args.related_companies
        args.time_horizon = scenario.get("time_horizon") or args.time_horizon
        args.budget = float(scenario.get("total_budget") or args.budget)
        args.objective = scenario.get("objective") or args.objective
        args.priorities = "|".join(scenario.get("strategic_priorities") or []) or args.priorities

    active = args.agent  # list[str]
    category_hints = [c.strip() for c in args.categories.split(",") if c.strip()]
    priorities = [p.strip() for p in args.priorities.split("|") if p.strip()]
    related_companies = [
        c.strip() for c in args.related_companies.split(",") if c.strip()
    ] or None

    print("\n" + "=" * 70)
    print("  Technology Roadmap Orchestration Agent")
    print("=" * 70)
    print(f"  domain         : {args.domain}")
    print(f"  reference_year : {args.year}")
    print(f"  active_agents  : {active}")
    print(f"  run_mode       : {args.run_mode}")
    print(f"  budget         : {args.budget:,.0f} USD")
    print(f"  stage_mode     : {args.stage_mode}")
    print(f"  patent_method  : {args.patent_method}")
    print(f"  use_patent_map : {args.use_patent_map if args.use_patent_map is not None else 'env/default'}")
    print(f"  scenario_id    : {args.scenario_id or '-'}")
    print(f"  company_name   : {args.company_name}")
    print(f"  out_prefix     : {args.out_prefix!r}")
    print("=" * 70)

    # 파이프라인 실행
    runner = run_single_agent_orchestration if args.run_mode == "single" else run_orchestration
    result = runner(
        domain=args.domain,
        reference_year=args.year,
        category_hints=category_hints,
        active_agents=active,
        industry=args.industry,
        company_type=args.company_type,
        time_horizon=args.time_horizon,
        total_budget=args.budget,
        objective=args.objective,
        priorities=priorities,
        future_trend_summary=args.future_trend,
        out_prefix=args.out_prefix,
        stage_mode=args.stage_mode,
        patent_method=args.patent_method,
        company_name=args.company_name,
        company_profile=args.company_profile,
        related_companies=related_companies,
        use_patent_map=args.use_patent_map,
    )

    # Orchestrator 최종 보고서 저장
    report_path = os.path.join(OUTPUTS_DIR, f"{args.out_prefix}{FILE_ORCHESTRATOR_REPORT}")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump({
            "problem_frame": result["problem_frame"],
            "active_agents": result["active_agents"],
            "run_mode": result.get("run_mode", args.run_mode),
            "patent_method": result["patent_method"],
            "iteration": result["iteration"],
            "review": result["review"],
            "artifact_paths": result["paths"],
        }, f, ensure_ascii=False, indent=2)

    # 최종 요약 출력
    print("\n" + "=" * 70)
    print("  파이프라인 완료 요약")
    print("=" * 70)
    print(f"  후보 기술 수             : {len(result['tech_candidates'])}")
    print(f"  로드맵 항목 수           : {len(result['planned_roadmap'])}")
    print(f"  stage 수                 : {len(result['stages'])}")
    print(f"  투자 전략 항목 수        : {len(result['investment_strategy'])}")
    print(f"  Orchestrator 결정         : {result['review']['decision']}")
    print(f"  REVISE 반복 횟수          : {result['iteration']}")
    print(f"\n  저장 파일:")
    print(f"    · {result['paths']['tech_candidates']}")
    print(f"    · {result['paths']['planned_roadmap']}")
    print(f"    · {result['paths']['investment_strategy']}")
    print(f"    · {report_path}")

    # ACCEPT 시 executive summary 를 화면에 노출
    if result["review"]["decision"] == "ACCEPT":
        es = (result["review"].get("report") or {}).get("executive_summary", "")
        if es:
            print("\n  ── Executive Summary ──────────────────────────────")
            print("  " + es.replace("\n", "\n  "))
    else:
        ds = result["review"].get("diagnostic_summary", "")
        if ds:
            print("\n  ── Diagnostic Summary ─────────────────────────────")
            print("  " + ds.replace("\n", "\n  "))
    print("=" * 70)

    return result


if __name__ == "__main__":
    main()
