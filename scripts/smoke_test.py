"""
smoke_test.py
──────────────
LLM 호출 없이 파이프라인 뼈대가 동작하는지 검증합니다.

확인 항목:
  1) roadmap_planner_agent 의 TRL 역산 + Zero-slack 불변량
  2) investment_strategist_agent 의 stage_aggregator
  3) orchestration_agent 의 Setup + Agent OFF 폴백 경로

사용법:
  source .venv/bin/activate
  python scripts/smoke_test.py
"""

import os
import sys
import json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _case_title(n: str):
    print(f"\n── {n} " + "─" * max(0, 60 - len(n)))


def test_timeline_calculator():
    _case_title("roadmap_planner_agent · backcast + zero-slack")
    sys.path.insert(0, os.path.join(ROOT, "roadmap_planner_agent"))
    try:
        from agents.dependency_analyzer import _build_fallback_tree
        from agents.timeline_calculator import (
            backcast_timeline, quarter_to_int, get_lead_time,
        )

        techs = [
            {"tech_id": "T01", "name": "High-NA EUV 장비", "trl": 4, "category": "Equipment"},
            {"tech_id": "T02", "name": "감광액(PR)",       "trl": 3, "category": "Material"},
            {"tech_id": "T03", "name": "GAA 공정",         "trl": 5, "category": "Process"},
            {"tech_id": "T04", "name": "BSPDN 아키텍처",   "trl": 3, "category": "Architecture"},
            {"tech_id": "T05", "name": "3D 본딩",          "trl": 4, "category": "Packaging"},
        ]
        tree = _build_fallback_tree(techs)
        _, timeline = backcast_timeline(tree, "2028 Q1")

        assert len(timeline) == 5, f"expected 5 items, got {len(timeline)}"
        print(f"  ✅ {len(timeline)}개 기술 역산 완료")

        for t in sorted(timeline, key=lambda x: quarter_to_int(x["start_q"])):
            print(
                f"    {t['tech_id']} | L{t['layer']} | "
                f"{t['start_q']} → {t['target_q']} | "
                f"lead={t['lead_time_quarters']}Q | prereqs={t['prerequisites']}"
            )

        # Zero-slack 검증
        by_id = {t["tech_id"]: t for t in timeline}
        for t in timeline:
            for pre in t["prerequisites"]:
                if pre in by_id:
                    assert quarter_to_int(by_id[pre]["target_q"]) < quarter_to_int(t["start_q"]), (
                        f"Zero-slack 위반: {pre} 완료={by_id[pre]['target_q']}  "
                        f">=  {t['tech_id']} 시작={t['start_q']}"
                    )
        print("  ✅ Zero-slack 불변량 만족 (prereq 완료 < 후행 시작)")

        # TRL 리드타임 sanity
        assert get_lead_time(2) >= 6 and get_lead_time(8) <= 2
        print(f"  ✅ TRL lead time: 2→{get_lead_time(2)}Q, 5→{get_lead_time(5)}Q, 8→{get_lead_time(8)}Q")
    finally:
        sys.path.remove(os.path.join(ROOT, "roadmap_planner_agent"))
        # 모듈 캐시 정리
        # 모듈 캐시 정리 — 다음 테스트가 다른 폴더의 동명 모듈을 깨끗이 import 하도록
        for k in list(sys.modules.keys()):
            if k in ("config", "state", "llm_factory", "pipeline", "agents") \
                    or k.startswith("agents.") or k.startswith("graphs"):
                del sys.modules[k]


def test_stage_aggregator():
    _case_title("investment_strategist_agent · stage_aggregator")
    sys.path.insert(0, os.path.join(ROOT, "investment_strategist_agent"))
    try:
        from agents.stage_aggregator import aggregate_stages

        # phase_name 이 있는 로드맵
        roadmap = [
            {"tech_id": "T01", "name": "장비 A", "phase_name": "1단계: 기반 R&D",
             "start_q": "2025 Q1", "target_q": "2026 Q2", "prerequisites": [], "lead_time_quarters": 6},
            {"tech_id": "T02", "name": "소재 B", "phase_name": "1단계: 기반 R&D",
             "start_q": "2025 Q1", "target_q": "2026 Q4", "prerequisites": [], "lead_time_quarters": 8},
            {"tech_id": "T03", "name": "공정 C", "phase_name": "2단계: 공정 통합",
             "start_q": "2026 Q3", "target_q": "2027 Q3", "prerequisites": ["T01", "T02"], "lead_time_quarters": 4},
            {"tech_id": "T04", "name": "패키지 D", "phase_name": "3단계: 시스템 검증",
             "start_q": "2027 Q1", "target_q": "2027 Q4", "prerequisites": [], "lead_time_quarters": 4},
        ]
        stages = aggregate_stages(roadmap, use_phase_name=True)
        assert len(stages) == 3
        print(f"  ✅ phase 모드: {len(stages)}개 stage 집계")
        for s in stages:
            print(f"    • {s['stage']}  [{s['period']}]  technologies={s['technologies']}")

        # horizon 모드 (시간 기준)
        stages_h = aggregate_stages(roadmap, use_phase_name=False)
        print(f"  ✅ horizon 모드: {len(stages_h)}개 stage 집계")
        for s in stages_h:
            print(f"    • {s['stage']}  [{s['period']}]  n={s['num_items']}")
    finally:
        sys.path.remove(os.path.join(ROOT, "investment_strategist_agent"))
        # 모듈 캐시 정리 — 다음 테스트가 다른 폴더의 동명 모듈을 깨끗이 import 하도록
        for k in list(sys.modules.keys()):
            if k in ("config", "state", "llm_factory", "pipeline", "agents") \
                    or k.startswith("agents.") or k.startswith("graphs"):
                del sys.modules[k]


def test_orchestrator_setup_and_fallbacks():
    _case_title("orchestration_agent · setup + OFF fallbacks")
    sys.path.insert(0, os.path.join(ROOT, "orchestration_agent"))
    try:
        from agents.orchestrator import run_orchestrator_setup
        from pipeline import (
            _write_dummy_tech_candidates,
            _write_flat_roadmap,
            _write_empty_strategy,
        )

        # Setup
        pf = run_orchestrator_setup(
            domain="차세대 HBM4", reference_year=2025,
            active_agents=["1", "2", "3"],
            total_budget=1_000_000_000.0,
            objective="HBM4 3년 내 양산",
            priorities=["수율", "TSV", "냉각"],
        )
        assert pf["total_budget"] == 1_000_000_000.0
        assert len(pf["strategic_priorities"]) == 3
        print("  ✅ ProblemFrame 구성 OK")

        # OFF 폴백 3종
        outputs = os.path.join(ROOT, "orchestration_agent", "outputs")
        os.makedirs(outputs, exist_ok=True)

        p1 = os.path.join(outputs, "smoke_tech_candidates.json")
        p2 = os.path.join(outputs, "smoke_planned_roadmap.json")
        p3 = os.path.join(outputs, "smoke_investment_strategy.json")

        _write_dummy_tech_candidates("HBM4", p1)
        _write_flat_roadmap(p1, p2)
        _write_empty_strategy(p2, p3)

        for p, key in [(p1, "tech_candidates"), (p2, "planned_roadmap"), (p3, "investment_strategy")]:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            assert key in data
            print(f"  ✅ {os.path.basename(p)}  ({key}={len(data[key])})")

        # 정리
        for p in [p1, p2, p3]:
            os.remove(p)
    finally:
        sys.path.remove(os.path.join(ROOT, "orchestration_agent"))
        for k in list(sys.modules.keys()):
            if k in ("config", "state", "llm_factory", "pipeline") or k.startswith("agents."):
                del sys.modules[k]


def main():
    print("═" * 65)
    print("  Tech-Analysis-Agent · LLM 없이 뼈대 검증")
    print("═" * 65)
    test_timeline_calculator()
    test_stage_aggregator()
    test_orchestrator_setup_and_fallbacks()
    print("\n" + "═" * 65)
    print("  ✅ 모든 smoke test 통과")
    print("═" * 65)


if __name__ == "__main__":
    main()
