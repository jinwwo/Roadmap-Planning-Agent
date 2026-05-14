"""
graphs/roadmap_graph.py
────────────────────────
Roadmap Planner Agent 의 Local Orchestrator 그래프

노드 흐름은 환경변수 `ROADMAP_DESIGN_MODE` 로 결정:

  ────────────────────────────────────────────────────────────
  ROADMAP_DESIGN_MODE=holistic   (default — spec 의 LLM 통합)
  ────────────────────────────────────────────────────────────
  START
    │
    ▼
  [tech_selector]       후보 종합 평가 → K개 선별
    │
    ▼
  [roadmap_designer]    LLM 한 번에: dependency tree + lead_time + backcasting
    │                    + phase_name + start_q/target_q + justification
    ▼
  END

  ────────────────────────────────────────────────────────────
  ROADMAP_DESIGN_MODE=hybrid     (옛 모드 — Python 알고리즘 결정성 우선)
  ────────────────────────────────────────────────────────────
  START
    │
    ▼
  [tech_selector]
    │
    ▼
  [dependency_analyzer]   LLM: 트리 구성 + 레이어
    │
    ▼
  [timeline_calculator]   pure Python: TRL 역산 + Zero-slack
    │
    ▼
  [roadmap_builder]       LLM: phase_name + justification (분기 변경 X)
    │
    ▼
  END

오케스트레이터 피드백:
  - holistic: roadmap_designer 의 LLM 프롬프트에 박힘 (text/shift/drop 모두)
  - hybrid: timeline_calculator 가 shift/drop 처리 + 각 LLM 노드가 text 반영
"""

import os

from langgraph.graph import StateGraph, END

from state import RoadmapState
from agents.tech_selector import run_tech_selector
from agents.dependency_analyzer import run_dependency_analyzer
from agents.timeline_calculator import run_timeline_calculator
from agents.roadmap_builder import run_roadmap_builder
from agents.roadmap_designer import run_roadmap_designer


def _design_mode() -> str:
    """ROADMAP_DESIGN_MODE: 'holistic' (default) | 'hybrid'."""
    mode = (os.getenv("ROADMAP_DESIGN_MODE") or "holistic").strip().lower()
    return "hybrid" if mode == "hybrid" else "holistic"


# ── 조건부 엣지 ──────────────────────────────────────────────

def route_after_selector(state: RoadmapState) -> str:
    if not state.get("tech_candidates"):
        return "end"
    return "dependency_analyzer"


def route_after_dependency(state: RoadmapState) -> str:
    if state.get("error") and not state.get("dependency_tree"):
        return "end"
    return "timeline_calculator"


def route_after_timeline(state: RoadmapState) -> str:
    if state.get("error") and not state.get("timeline_draft"):
        return "end"
    return "roadmap_builder"


def route_after_selector_holistic(state: RoadmapState) -> str:
    if not state.get("tech_candidates"):
        return "end"
    return "roadmap_designer"


# ── 그래프 생성 ───────────────────────────────────────────────

def create_roadmap_graph():
    """
    Roadmap Planner Agent LangGraph 그래프 생성 및 컴파일.
    환경변수 ROADMAP_DESIGN_MODE 에 따라 holistic / hybrid 두 모드 분기.

    Returns
    -------
    CompiledGraph
    """
    mode = _design_mode()
    graph = StateGraph(RoadmapState)
    graph.add_node("tech_selector", run_tech_selector)

    if mode == "holistic":
        # spec 의 LLM 통합 모드 (default)
        print(f"[Roadmap Graph] 모드: holistic (spec LLM 통합)")
        graph.add_node("roadmap_designer", run_roadmap_designer)
        graph.set_entry_point("tech_selector")
        graph.add_conditional_edges(
            "tech_selector",
            route_after_selector_holistic,
            {"roadmap_designer": "roadmap_designer", "end": END},
        )
        graph.add_edge("roadmap_designer", END)
    else:
        # hybrid 모드 (옛 모드 — Python 알고리즘 결정성 우선)
        print(f"[Roadmap Graph] 모드: hybrid (Python timeline_calculator)")
        graph.add_node("dependency_analyzer", run_dependency_analyzer)
        graph.add_node("timeline_calculator", run_timeline_calculator)
        graph.add_node("roadmap_builder", run_roadmap_builder)
        graph.set_entry_point("tech_selector")
        graph.add_conditional_edges(
            "tech_selector",
            route_after_selector,
            {"dependency_analyzer": "dependency_analyzer", "end": END},
        )
        graph.add_conditional_edges(
            "dependency_analyzer",
            route_after_dependency,
            {"timeline_calculator": "timeline_calculator", "end": END},
        )
        graph.add_conditional_edges(
            "timeline_calculator",
            route_after_timeline,
            {"roadmap_builder": "roadmap_builder", "end": END},
        )
        graph.add_edge("roadmap_builder", END)

    return graph.compile()


# ── 직접 실행용 헬퍼 (라이브러리 호출) ───────────────────────

def run_roadmap_planner(
    tech_candidates: list,
    market_context: dict,
    orchestrator_feedback: dict = None,
    reference_year: int = None,
    company_scenario: dict = None,
    strategic_direction: list = None,
) -> dict:
    """
    Roadmap Planner Agent 를 단독 실행합니다.

    Parameters
    ----------
    tech_candidates       : Technology Analyst Agent 의 출력
    market_context        : {"target_market": ..., "expected_boom_quarter": ...}
    orchestrator_feedback : {"shift": [...], "drop": [...]} (선택)

    Returns
    -------
    dict  {planned_roadmap, dependency_tree, timeline_draft, messages, error}
    """
    graph = create_roadmap_graph()

    initial_state: RoadmapState = {
        "tech_candidates": tech_candidates,
        "market_context": market_context,
        "reference_year": reference_year,
        "tech_selection": None,
        "dependency_tree": None,
        "timeline_draft": None,
        "planned_roadmap": None,
        "orchestrator_feedback": orchestrator_feedback,
        "company_scenario": company_scenario,
        "strategic_direction": strategic_direction,
        "messages": [],
        "error": None,
        "iteration": 0,
    }

    print(f"\n{'=' * 60}")
    print(f"  Roadmap Planner Agent 시작")
    print(f"  후보 기술 수    : {len(tech_candidates)}")
    print(f"  시장 개화 목표 : {market_context.get('expected_boom_quarter', 'N/A')}")
    print(f"  Planning Horizon: {(company_scenario or {}).get('planning_horizon', '(unknown)')}  (reference_year={reference_year})")
    if orchestrator_feedback:
        print(f"  오케스트레이터 피드백: {orchestrator_feedback}")
    print(f"{'=' * 60}")

    final_state = graph.invoke(initial_state)

    print(f"\n{'=' * 60}")
    print(f"  Roadmap Planner Agent 완료")
    print(f"  로드맵 항목 수 : {len(final_state.get('planned_roadmap') or [])}")
    print(f"{'=' * 60}\n")

    return final_state
