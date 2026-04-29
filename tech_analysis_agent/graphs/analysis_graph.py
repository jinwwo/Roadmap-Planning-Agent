"""
graphs/analysis_graph.py
─────────────────────────
Technology Analysis Agent 의 Local Orchestrator 그래프

노드 흐름:
  START
    │
    ▼
  [patent_agent]        USPTO 데이터 수집 + Claude 특허 분석
    │
    ▼
  [market_agent]        Tavily 데이터 수집 + Claude 시장 분석
    │
    ▼
  [aggregator]          final_score 통합 → tech_candidates 출력
    │
    ▼
  END

오류 처리:
  - patent_agent 실패 시 → 즉시 END (시장 분석 의미 없음)
  - market_agent 실패 시 → aggregator 로 진행 (특허 점수만 활용)
"""

from langgraph.graph import StateGraph, END
from langchain_core.messages import AIMessage

from state import AnalysisState
from agents.patent_agent import run_patent_agent
from agents.market_agent import run_market_agent
from agents.aggregator import run_aggregator


# ── 조건부 엣지 함수 ──────────────────────────────────────────

def route_after_patent(state: AnalysisState) -> str:
    """
    Patent Agent 이후 라우팅:
    - 분석 결과가 있으면 → market_agent
    - 치명적 오류이면 → END
    """
    if state.get("error") and not state.get("patent_analysis"):
        print("[Graph] Patent Agent 치명적 오류 → 파이프라인 중단")
        return "end"
    return "market_agent"


def route_after_market(state: AnalysisState) -> str:
    """
    Market Agent 이후 라우팅:
    - 항상 aggregator 로 진행 (시장 데이터 없어도 특허 점수로 진행 가능)
    """
    if state.get("error") and not state.get("patent_analysis"):
        return "end"
    return "aggregator"


# ── 그래프 생성 ───────────────────────────────────────────────

def create_analysis_graph():
    """
    Technology Analysis Agent LangGraph 그래프 생성 및 컴파일

    Returns
    -------
    CompiledGraph  (invoke 가능한 컴파일된 그래프)
    """
    graph = StateGraph(AnalysisState)

    # 노드 등록
    graph.add_node("patent_agent", run_patent_agent)
    graph.add_node("market_agent", run_market_agent)
    graph.add_node("aggregator", run_aggregator)

    # 엔트리 포인트
    graph.set_entry_point("patent_agent")

    # 엣지 연결 (조건부)
    graph.add_conditional_edges(
        "patent_agent",
        route_after_patent,
        {"market_agent": "market_agent", "end": END},
    )
    graph.add_conditional_edges(
        "market_agent",
        route_after_market,
        {"aggregator": "aggregator", "end": END},
    )
    graph.add_edge("aggregator", END)

    return graph.compile()


# ── 직접 실행용 헬퍼 ─────────────────────────────────────────

def run_technology_analysis(
    domain: str,
    reference_year: int,
    category_hints: list = None,
) -> dict:
    """
    Technology Analysis Agent 를 단독 실행합니다.

    Parameters
    ----------
    domain         : 분석 대상 산업 도메인
    reference_year : 기준 연도
    category_hints : 집중할 기술 카테고리 (선택, 없으면 전체)

    Returns
    -------
    dict  {tech_candidates, market_context, messages, error}
    """
    graph = create_analysis_graph()

    initial_state: AnalysisState = {
        "domain": domain,
        "reference_year": reference_year,
        "category_hints": category_hints or [],
        "patent_raw_data": None,
        "market_raw_data": None,
        "patent_analysis": None,
        "patent_maps": None,
        "patent_prompt": None,
        "market_analysis": None,
        "tech_candidates": None,
        "market_context": None,
        "messages": [],
        "error": None,
        "retry_count": 0,
    }

    print(f"\n{'='*60}")
    print(f"  Technology Analysis Agent 시작")
    print(f"  도메인  : {domain}")
    print(f"  기준연도: {reference_year}")
    print(f"  카테고리: {category_hints or '전체'}")
    print(f"{'='*60}")

    final_state = graph.invoke(initial_state)

    print(f"\n{'='*60}")
    print(f"  Technology Analysis Agent 완료")
    print(f"  최종 후보 기술 수: {len(final_state.get('tech_candidates') or [])}")
    print(f"{'='*60}\n")

    return final_state
