"""
main.py
────────
Roadmap Planner Agent 진입점 (Agent 2)

사용 예:
  # Agent 1 (Technology Analyst) 가 만든 JSON 을 입력으로 사용
  python main.py --input ../tech_analysis_agent/output_tech_candidates.json

  # 오케스트레이터 피드백(shift/drop) 시뮬레이션
  python main.py --input input.json \
                 --shift "T02:2026 Q1" \
                 --drop  "T04"

입력 포맷 (JSON):
  {
    "market_context": {
      "target_market": "...",
      "expected_boom_quarter": "YYYY QX"
    },
    "tech_candidates": [
      {"tech_id": "T01", "name": "...", "trl": 4, "category": "Equipment", ...},
      ...
    ]
  }

출력:
  output_planned_roadmap.json  : Investment Strategist Agent 입력 포맷
"""

import argparse
import json
from typing import Optional

from config import validate_config
from graphs.roadmap_graph import run_roadmap_planner


# ──────────────────────────────────────────────────────────────
# CLI 파서
# ──────────────────────────────────────────────────────────────

def _parse_shift(raw: Optional[str]) -> list:
    """
    '--shift "T02:2026 Q1,T05:2027 Q3"'  →  [{tech_id, new_start_q}, ...]
    '--shift "T02:2026 Q1"'               →  [{tech_id, new_start_q}]
    """
    if not raw:
        return []
    out = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry or ":" not in entry:
            continue
        tid, new_q = entry.split(":", 1)
        out.append({"tech_id": tid.strip(), "new_start_q": new_q.strip()})
    return out


def _parse_drop(raw: Optional[str]) -> list:
    """'--drop "T04,T07"'  →  ["T04", "T07"]"""
    if not raw:
        return []
    return [t.strip() for t in raw.split(",") if t.strip()]


def parse_args():
    p = argparse.ArgumentParser(
        description="Roadmap Planner Agent (Agent 2) — TRL 역산 기반 기술 로드맵 설계",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--input", "-i", type=str,
        default="../tech_analysis_agent/output_tech_candidates.json",
        help="Technology Analyst Agent 출력 JSON 경로",
    )
    p.add_argument(
        "--output", "-o", type=str,
        default="output_planned_roadmap.json",
        help="Roadmap Planner 결과 저장 경로",
    )
    # 오케스트레이터 피드백 시뮬레이션
    p.add_argument(
        "--shift", type=str, default=None,
        help='시작 분기 강제 연기 (예: "T02:2026 Q1,T05:2027 Q3")',
    )
    p.add_argument(
        "--drop", type=str, default=None,
        help='로드맵 제외 대상 tech_id (예: "T04,T07")',
    )
    return p.parse_args()


# ──────────────────────────────────────────────────────────────
# Entry
# ──────────────────────────────────────────────────────────────

def main():
    args = parse_args()

    # ① 환경 변수 검증 (LLM provider)
    validate_config()

    # ② 입력 JSON 로드
    print(f"\n[Main] 입력 로드: {args.input}")
    with open(args.input, "r", encoding="utf-8") as f:
        data = json.load(f)

    tech_candidates = data.get("tech_candidates") or []
    market_context = data.get("market_context") or {}

    if not tech_candidates:
        raise SystemExit("❌ tech_candidates 가 비어있습니다. 입력 JSON 을 확인하세요.")

    # ③ 오케스트레이터 피드백 구성 (옵션)
    shift = _parse_shift(args.shift)
    drop = _parse_drop(args.drop)
    orchestrator_feedback = None
    if shift or drop:
        orchestrator_feedback = {"shift": shift, "drop": drop}
        print(f"[Main] 오케스트레이터 피드백: {orchestrator_feedback}")

    # ④ 실행
    print(f"\n" + "=" * 60)
    print(f"  Roadmap Planner Agent 시작")
    print(f"  입력 후보 기술 : {len(tech_candidates)}개")
    print(f"  목표 시장      : {market_context.get('target_market', 'N/A')}")
    print(f"  시장 개화 목표 : {market_context.get('expected_boom_quarter', 'N/A')}")
    print("=" * 60)

    result = run_roadmap_planner(
        tech_candidates=tech_candidates,
        market_context=market_context,
        orchestrator_feedback=orchestrator_feedback,
    )

    # ⑤ 결과 저장
    output = {
        "market_context": market_context,
        "planned_roadmap": result.get("planned_roadmap") or [],
    }
    # 중간 산출물(dependency_tree) 도 같이 저장하면 디버깅에 유용
    if result.get("dependency_tree"):
        output["dependency_tree"] = result["dependency_tree"]

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # ⑥ 요약 출력
    print("\n" + "=" * 60)
    print("  파이프라인 완료 요약")
    print("=" * 60)
    print(f"  로드맵 항목 수 : {len(output['planned_roadmap'])}")
    print(f"  저장 파일      : {args.output}")
    if result.get("error"):
        print(f"  ⚠️  오류      : {result['error']}")
    print("=" * 60)

    return result


if __name__ == "__main__":
    main()
