"""
graphs/global_graph.py
───────────────────────
전체 파이프라인 Global Orchestrator 그래프

노드 흐름:
  START
    │
    ▼
  [technology_analysis]    Agent 1: 후보 기술 발굴
    │
    ▼
  [roadmap_planner]         Agent 2: 로드맵 설계
    │
    ▼
  [investment_strategist]   Agent 3: 투자 전략 (Placeholder)
    │
    ▼
  END

캡슐화 원칙:
  Global Orchestrator 는 각 Agent 내부 구조를 알지 못함.
  입력/출력 인터페이스(GlobalState 필드)만 관리.
"""

from typing import Literal
from langgraph.graph import StateGraph, END
from langchain_core.messages import AIMessage

from state import GlobalState, AnalysisState, RoadmapState
from graphs.analysis_graph import create_analysis_graph
from graphs.roadmap_graph import create_roadmap_graph


# ── Agent 1: Technology Analysis ─────────────────────────────

def technology_analysis_node(state: GlobalState) -> dict:
    """
    Technology Analysis Agent 를 서브그래프로 실행합니다.
    GlobalState → AnalysisState 브리징 후 결과를 GlobalState 로 역브리징.
    """
    print("\n[Global Orchestrator] ▶ Agent 1: Technology Analysis 실행")

    analysis_graph = create_analysis_graph()

    sub_state: AnalysisState = {
        "domain": state["domain"],
        "reference_year": state["reference_year"],
        "category_hints": state.get("category_hints", []),
        "patent_raw_data": None,
        "market_raw_data": None,
        "patent_analysis": None,
        "market_analysis": None,
        "tech_candidates": None,
        "market_context": None,
        "messages": [],
        "error": None,
        "retry_count": 0,
    }

    result = analysis_graph.invoke(sub_state)
    candidates = result.get("tech_candidates") or []
    context = result.get("market_context") or {}

    print(f"\n[Global Orchestrator] Agent 1 완료: {len(candidates)}개 후보 기술 확보")

    return {
        "tech_candidates": candidates,
        "market_context": context,
        "current_step": "roadmap_planner",
        "messages": [
            AIMessage(
                content=(
                    f"[Agent 1 완료] {len(candidates)}개 후보 기술 발굴 → "
                    f"시장 개화 예상: {context.get('expected_boom_quarter', 'N/A')}"
                )
            )
        ],
        "error": result.get("error"),
    }


# ── Agent 2: Roadmap Planner ──────────────────────────────────

def roadmap_planner_node(state: GlobalState) -> dict:
    """
    Roadmap Planner Agent 를 서브그래프로 실행합니다.
    GlobalState → RoadmapState 브리징 후 결과를 GlobalState 로 역브리징.
    """
    print("\n[Global Orchestrator] ▶ Agent 2: Roadmap Planner 실행")

    tech_candidates = state.get("tech_candidates") or []
    market_context = state.get("market_context") or {}

    if not tech_candidates:
        msg = "[Agent 2] 후보 기술이 없어 Roadmap Planner 를 건너뜁니다."
        print(f"[Global Orchestrator] ⚠️ {msg}")
        return {
            "planned_roadmap": [],
            "current_step": "investment_strategist",
            "messages": [AIMessage(content=msg)],
        }

    roadmap_graph = create_roadmap_graph()

    sub_state: RoadmapState = {
        "tech_candidates": tech_candidates,
        "market_context": market_context,
        "dependency_tree": None,
        "timeline_draft": None,
        "planned_roadmap": None,
        "orchestrator_feedback": state.get("orchestrator_feedback"),
        "messages": [],
        "error": None,
        "iteration": 0,
    }

    result = roadmap_graph.invoke(sub_state)
    roadmap = result.get("planned_roadmap") or []
    dep_tree = result.get("dependency_tree") or {}

    print(f"\n[Global Orchestrator] Agent 2 완료: {len(roadmap)}개 로드맵 항목 생성")

    return {
        "planned_roadmap": roadmap,
        "dependency_tree": dep_tree,
        "current_step": "investment_strategist",
        "messages": [
            AIMessage(
                content=f"[Agent 2 완료] {len(roadmap)}개 로드맵 항목 → Investment Strategist 로 전달"
            )
        ],
        "error": result.get("error"),
    }


# ── Agent 3: Investment Strategist (Placeholder) ─────────────

def investment_strategist_node(state: GlobalState) -> dict:
    """
    Investment Strategist Agent (향후 구현 예정).
    """
    print("\n[Global Orchestrator] ▶ Agent 3: Investment Strategist (Placeholder)")
    roadmap_count = len(state.get("planned_roadmap") or [])
    print(f"  → {roadmap_count}개 로드맵 항목 수신. 투자 전략 로직은 향후 구현 예정.")

    return {
        "investment_plan": [],
        "current_step": "done",
        "messages": [
            AIMessage(content="[Agent 3] Investment Strategist: 향후 구현 예정 (Placeholder)")
        ],
    }


# ── 조건부 엣지 ──────────────────────────────────────────────

def route_after_analysis(state: GlobalState) -> Literal["roadmap_planner", "end"]:
    if state.get("error") and not state.get("tech_candidates"):
        print("[Global Orchestrator] ❌ Technology Analysis 실패 → 파이프라인 중단")
        return "end"
    return "roadmap_planner"


def route_after_roadmap(state: GlobalState) -> Literal["investment_strategist", "end"]:
    if state.get("error") and not state.get("planned_roadmap"):
        print("[Global Orchestrator] ❌ Roadmap Planner 실패 → 파이프라인 중단")
        return "end"
    return "investment_strategist"


# ── 글로벌 그래프 생성 ────────────────────────────────────────

def create_global_graph():
    """
    Global Orchestrator LangGraph 그래프 생성 및 컴파일

    계층 구조:
      GlobalGraph
      ├── technology_analysis  →  AnalysisGraph (SubGraph)
      │     ├── patent_agent
      │     ├── market_agent
      │     └── aggregator
      ├── roadmap_planner      →  RoadmapGraph (SubGraph)
      │     ├── dependency_analyzer
      │     ├── timeline_calculator
      │     └── roadmap_builder
      └── investment_strategist  (Placeholder)

    Returns
    -------
    CompiledGraph
    """
    graph = StateGraph(GlobalState)

    graph.add_node("technology_analysis", technology_analysis_node)
    graph.add_node("roadmap_planner", roadmap_planner_node)
    graph.add_node("investment_strategist", investment_strategist_node)

    graph.set_entry_point("technology_analysis")

    graph.add_conditional_edges(
        "technology_analysis",
        route_after_analysis,
        {"roadmap_planner": "roadmap_planner", "end": END},
    )
    graph.add_conditional_edges(
        "roadmap_planner",
        route_after_roadmap,
        {"investment_strategist": "investment_strategist", "end": END},
    )
    graph.add_edge("investment_strategist", END)

    return graph.compile()
