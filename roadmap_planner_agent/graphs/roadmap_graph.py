"""
graphs/roadmap_graph.py
────────────────────────
Roadmap Planner Agent 의 Local Orchestrator 그래프

노드 흐름:
  START
    │
    ▼
  [tech_selector]          후보 기술 종합 평가 → 핵심만 K개 선별 (최소 K_MIN 보장)
    │
    ▼
  [dependency_analyzer]    선별된 기술 트리 구성 + 레이어 할당 (LLM)
    │
    ▼
  [timeline_calculator]    TRL 기반 역산 → 분기별 start/target 산출 (pure Python)
    │
    ▼
  [roadmap_builder]        LLM justification 생성 → 최종 로드맵
    │
    ▼
  END

오케스트레이터 피드백 처리:
  - orchestrator_feedback 이 있으면 timeline_calculator 에서 자동 반영
  - Shift : 특정 기술의 시작 분기를 강제 연기
  - Drop  : 특정 기술을 로드맵에서 제외 (dropped=True 표시)
"""

from langgraph.graph import StateGraph, END

from state import RoadmapState
from agents.tech_selector import run_tech_selector
from agents.dependency_analyzer import run_dependency_analyzer
from agents.timeline_calculator import run_timeline_calculator
from agents.roadmap_builder import run_roadmap_builder


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


# ── 그래프 생성 ───────────────────────────────────────────────

def create_roadmap_graph():
    """
    Roadmap Planner Agent LangGraph 그래프 생성 및 컴파일

    Returns
    -------
    CompiledGraph
    """
    graph = StateGraph(RoadmapState)

    graph.add_node("tech_selector", run_tech_selector)
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
        "tech_selection": None,
        "dependency_tree": None,
        "timeline_draft": None,
        "planned_roadmap": None,
        "orchestrator_feedback": orchestrator_feedback,
        "messages": [],
        "error": None,
        "iteration": 0,
    }

    print(f"\n{'=' * 60}")
    print(f"  Roadmap Planner Agent 시작")
    print(f"  후보 기술 수    : {len(tech_candidates)}")
    print(f"  시장 개화 목표 : {market_context.get('expected_boom_quarter', 'N/A')}")
    if orchestrator_feedback:
        print(f"  오케스트레이터 피드백: {orchestrator_feedback}")
    print(f"{'=' * 60}")

    final_state = graph.invoke(initial_state)

    print(f"\n{'=' * 60}")
    print(f"  Roadmap Planner Agent 완료")
    print(f"  로드맵 항목 수 : {len(final_state.get('planned_roadmap') or [])}")
    print(f"{'=' * 60}\n")

    return final_state
