"""
main.py
────────
Investment Strategist Agent 진입점 (Agent 3)

사용 예:
  # Roadmap Planner 가 만든 JSON + Technology Analyst 의 tech_candidates 를 입력으로 사용
  python main.py \
    --roadmap ../roadmap_planner_agent/output_planned_roadmap.json \
    --tech    ../tech_analysis_agent/output_tech_candidates.json

  # 투자 정책 override
  python main.py --risk high --horizon long --total-budget 5000000000

입력 포맷:
  --roadmap : Roadmap Planner 의 output_planned_roadmap.json
              { "market_context": {...}, "planned_roadmap": [...] }
  --tech    : Technology Analyst 의 output_tech_candidates.json
              { "tech_candidates": [...] }
              (없으면 tech-level 정보 없이 stage 만으로 평가 — 품질 저하)

출력:
  output_investment_strategy.json
  { "market_context": {...},
    "stages": [...],               ← aggregator 결과 (참고용)
    "investment_strategy": [...] } ← strategist 최종 출력
"""

import argparse
import json
from typing import Optional

from config import (
    validate_config,
    DEFAULT_INPUT_ROADMAP,
    DEFAULT_OUTPUT_STRATEGY,
    DEFAULT_INVESTMENT_POLICY,
)
from agents.stage_aggregator import aggregate_stages
from agents.strategist import run_strategist


# ──────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(
        description="Investment Strategist Agent (Agent 3) — 로드맵 단계별 투자 전략 수립",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--roadmap", "-r", type=str,
        default=DEFAULT_INPUT_ROADMAP,
        help="Roadmap Planner 의 output_planned_roadmap.json 경로",
    )
    p.add_argument(
        "--tech", "-t", type=str,
        default="../tech_analysis_agent/output_tech_candidates.json",
        help="Technology Analyst 의 output_tech_candidates.json 경로 (선택)",
    )
    p.add_argument(
        "--output", "-o", type=str,
        default=DEFAULT_OUTPUT_STRATEGY,
        help="결과 JSON 저장 경로",
    )
    # Investment Policy override
    p.add_argument("--risk", type=str, default=None,
                   choices=["low", "medium", "high"],
                   help="risk_appetite")
    p.add_argument("--horizon", type=str, default=None,
                   choices=["short", "balanced", "long"],
                   help="investment_horizon")
    p.add_argument("--total-budget", dest="total_budget", type=float, default=None,
                   help="total_budget (USD, 숫자)")
    p.add_argument("--priority", type=str, default=None,
                   help='strategic_priority (쉼표 구분, 예: "market entry,core capability building")')
    # Stage aggregation 방식
    p.add_argument(
        "--stage-mode", dest="stage_mode", type=str, default="phase",
        choices=["phase", "horizon"],
        help=(
            "stage 집계 방식. "
            "phase=Roadmap Planner 의 phase_name 그대로 사용, "
            "horizon=start_q 기준 short/mid/long-term 으로 재분류"
        ),
    )
    return p.parse_args()


# ──────────────────────────────────────────────────────────────
# JSON 로더 (파일 없으면 None 반환)
# ──────────────────────────────────────────────────────────────

def _try_load(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except Exception as e:
        print(f"⚠️  {path} 로드 실패: {e}")
        return None


def _build_policy(args) -> dict:
    policy = dict(DEFAULT_INVESTMENT_POLICY)
    if args.risk:
        policy["risk_appetite"] = args.risk
    if args.horizon:
        policy["investment_horizon"] = args.horizon
    if args.total_budget is not None:
        policy["total_budget"] = float(args.total_budget)
    if args.priority:
        policy["strategic_priority"] = [p.strip() for p in args.priority.split(",") if p.strip()]
    return policy


# ──────────────────────────────────────────────────────────────
# Entry
# ──────────────────────────────────────────────────────────────

def main():
    args = parse_args()

    # ① 환경 변수 검증 (LLM provider)
    validate_config()

    # ② Roadmap (필수) + Technology Analysis (선택) 로드
    print(f"\n[Main] 로드맵 입력      : {args.roadmap}")
    roadmap_data = _try_load(args.roadmap)
    if not roadmap_data:
        raise SystemExit(f"❌ 로드맵 파일을 찾을 수 없습니다: {args.roadmap}")

    planned_roadmap = roadmap_data.get("planned_roadmap") or []
    market_context = roadmap_data.get("market_context") or {}
    if not planned_roadmap:
        raise SystemExit("❌ planned_roadmap 이 비어있습니다. 입력 JSON 을 확인하세요.")

    print(f"[Main] 기술 분석 입력   : {args.tech}")
    tech_data = _try_load(args.tech)
    tech_candidates = (tech_data or {}).get("tech_candidates") or []
    if not tech_candidates:
        print(f"  ⚠️  tech_candidates 없음 — stage 만으로 평가 진행 (품질 저하 가능)")

    # ③ Stage 집계
    print(f"\n[Main] Stage 집계 모드  : {args.stage_mode}")
    stages = aggregate_stages(
        planned_roadmap=planned_roadmap,
        market_context=market_context,
        use_phase_name=(args.stage_mode == "phase"),
    )
    if not stages:
        raise SystemExit("❌ 유효한 stage 가 없습니다 (dropped 항목만 존재?).")

    print("\n  ── Aggregated Stages ──────────────────────────")
    for s in stages:
        print(f"    • {s['stage']}  [{s['period']}]  — {s['num_items']}개 기술")
        for name in s["technologies"][:5]:
            print(f"        - {name}")
        if len(s["technologies"]) > 5:
            print(f"        ... and {len(s['technologies']) - 5} more")

    # ④ Investment Policy 구성
    policy = _build_policy(args)
    print(f"\n[Main] Investment Policy: {policy}")

    # ⑤ LLM 평가 + Tier 도출
    print("\n" + "=" * 70)
    print("  Investment Strategist Agent 시작")
    print(f"  stages 수     : {len(stages)}")
    print(f"  tech items    : {len(tech_candidates)}")
    print("=" * 70)

    strategies = run_strategist(
        stages=stages,
        tech_candidates=tech_candidates,
        investment_policy=policy,
        market_context=market_context,
    )

    # ⑥ 결과 저장
    output = {
        "market_context": market_context,
        "investment_policy": policy,
        "stages": stages,
        "investment_strategy": strategies,
    }
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # ⑦ 요약 출력
    print("\n" + "=" * 70)
    print("  파이프라인 완료 요약")
    print("=" * 70)
    tier_counts = {"Tier 1": 0, "Tier 2": 0, "Tier 3": 0}
    for s in strategies:
        tier_counts[s.get("recommended_investment_tier", "Tier 2")] = (
            tier_counts.get(s.get("recommended_investment_tier", "Tier 2"), 0) + 1
        )
    print(f"  stages 수           : {len(stages)}")
    print(f"  전략 항목 수         : {len(strategies)}")
    print(f"  Tier 분포            : T1={tier_counts['Tier 1']}, T2={tier_counts['Tier 2']}, T3={tier_counts['Tier 3']}")
    print(f"  저장 파일            : {args.output}")
    print("=" * 70)

    return output


if __name__ == "__main__":
    main()
