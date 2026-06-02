"""
state.py
────────
LangGraph 에서 사용하는 State TypedDict 정의.

AnalysisState : Technology Analysis Agent 내부 공유 상태
GlobalState   : 전체 파이프라인(Global Orchestrator) 공유 상태
"""

from typing import TypedDict, List, Optional, Annotated
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage


# ── 세부 타입 힌트 (가독성용) ─────────────────────────────────

class PatentSignals(TypedDict):
    filing_growth_rate: float
    citation_concentration: float
    white_space_index: float
    key_assignee_concentration: float


class MarketSignals(TypedDict):
    tam_growth_rate: float
    time_to_market_urgency: float
    policy_and_investment_tailwind: float
    competitive_moat_potential: float


class PatentAnalysisResult(TypedDict):
    tech_id: str
    name: str
    category: str           # Material / Equipment / Process / Architecture / Packaging
    estimated_trl: int      # 1–9
    patent_score: float     # 0–100
    patent_signals: PatentSignals
    dependency_hints: List[str]
    data_quality: str       # "real" | "estimated"
    rationale: str


class MarketAnalysisResult(TypedDict):
    tech_id: str
    name: str
    market_score: float     # 0–100
    market_signals: MarketSignals
    tam_sam_som: dict
    cagr_forecast: dict
    key_market_reports: List[dict]
    map_context_used: dict
    expected_market_boom_quarter: str   # "YYYY QX"
    competitive_landscape: str
    data_quality: str
    rationale: str


class TechCandidate(TypedDict):
    """Roadmap Planner Agent 로 전달되는 최종 후보 기술 포맷"""
    tech_id: str
    name: str
    category: str
    trl: int
    patent_score: float
    market_score: float
    final_score: float
    expected_market_boom_quarter: str
    dependency_hints: List[str]
    rationale: str


# ── Technology Analysis Agent 내부 상태 ───────────────────────

class AnalysisState(TypedDict):
    # ① 입력
    domain: str
    reference_year: int
    category_hints: List[str]
    company_name: Optional[str]
    company_profile: Optional[str]
    related_companies: Optional[List[str]]

    # ② 원시 데이터 (API 응답)
    patent_raw_data: Optional[dict]   # 특허 provider 응답 묶음
    market_raw_data: Optional[dict]   # Tavily 검색 결과 묶음

    # ③ LLM 분석 결과
    patent_analysis: Optional[List[PatentAnalysisResult]]
    patent_maps: Optional[dict]
    patent_prompt: Optional[dict]
    market_analysis: Optional[List[MarketAnalysisResult]]

    # ④ 최종 출력
    tech_candidates: Optional[List[TechCandidate]]
    market_context: Optional[dict]
    candidate_selection: Optional[dict]

    # ⑤ Orchestrator REVISE feedback (재실행 시 후보 재도출에 활용)
    #    {"text": ["피드백 문장", ...], "shift": [...], "drop": [...]}
    orchestrator_feedback: Optional[dict]

    # ⑤-b Company Scenario + Strategic Direction (상위 컨텍스트)
    # Orchestrator Setup 에서 추출되어 모든 LLM 노드 프롬프트에 박힘
    company_scenario: Optional[dict]
    strategic_direction: Optional[List[str]]

    # ⑥ 제어
    messages: Annotated[List[BaseMessage], add_messages]
    error: Optional[str]
    retry_count: int


# ── Global Orchestrator 상태 ──────────────────────────────────

class GlobalState(TypedDict):
    # 파이프라인 입력
    domain: str
    reference_year: int
    category_hints: List[str]
    company_name: Optional[str]
    company_profile: Optional[str]
    related_companies: Optional[List[str]]

    # Agent 1 출력 → Agent 2 입력
    tech_candidates: Optional[List[TechCandidate]]
    market_context: Optional[dict]
    candidate_selection: Optional[dict]
    patent_maps: Optional[dict]
    patent_prompt: Optional[dict]

    # Agent 2 출력 → Agent 3 입력
    planned_roadmap: Optional[List[dict]]
    dependency_tree: Optional[dict]

    # Agent 3 출력 (추후 구현)
    investment_plan: Optional[List[dict]]

    # 제어
    messages: Annotated[List[BaseMessage], add_messages]
    current_step: str
    error: Optional[str]


# ── Roadmap Planner Agent 내부 타입 ──────────────────────────

class RoadmapItem(TypedDict):
    """Roadmap Planner Agent 출력 단위"""
    tech_id: str
    name: str
    phase_name: str           # "Phase 1: Foundation" 등
    start_q: str              # "YYYY QX"
    target_q: str             # "YYYY QX"
    prerequisites: List[str]  # tech_id 리스트
    lead_time_quarters: int
    justification: str


class DependencyNode(TypedDict):
    tech_id: str
    name: str
    category: str
    trl: int
    prerequisites: List[str]   # 정밀화된 선행 기술 ID
    dependents: List[str]      # 이 기술에 의존하는 후행 기술 ID
    layer: int                 # 0=기반, 1=공정, 2=설계/패키징


class RoadmapState(TypedDict):
    # ① 입력 (Analysis Agent로부터)
    tech_candidates: List[TechCandidate]
    market_context: dict

    # ② 중간 결과
    dependency_tree: Optional[dict]      # {tech_id: DependencyNode}
    timeline_draft: Optional[List[dict]] # 분기 배치 초안

    # ③ 최종 출력
    planned_roadmap: Optional[List[RoadmapItem]]

    # ④ 오케스트레이터 피드백 (재설계 시)
    orchestrator_feedback: Optional[dict]  # {shift: [...], drop: [...]}

    # ⑤ 제어
    messages: Annotated[List[BaseMessage], add_messages]
    error: Optional[str]
    iteration: int  # 재설계 횟수 추적
