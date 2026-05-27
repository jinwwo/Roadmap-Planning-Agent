"""
state.py
─────────
Roadmap Planner Agent 내부에서 사용하는 State TypedDict 정의.
LangGraph StateGraph 와 호환됩니다.
"""

from typing import TypedDict, List, Optional, Annotated
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage


# ── Technology Analyst Agent 로부터 전달받는 후보 기술 포맷 ───

class TechCandidate(TypedDict, total=False):
    """Agent 1 이 넘겨주는 후보 기술 (전체 필드는 optional, 최소 tech_id/name/trl/category 필요)"""
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


# ── Roadmap Planner 내부 타입 ────────────────────────────────

class DependencyNode(TypedDict):
    tech_id: str
    name: str
    category: str
    trl: int
    prerequisites: List[str]   # 정밀화된 선행 기술 ID
    dependents: List[str]      # 이 기술에 의존하는 후행 기술 ID
    layer: int                 # 0=기반, 1=공정, 2=설계/패키징


class TimelineItem(TypedDict, total=False):
    """역산 알고리즘 산출물 (Roadmap Builder 이전 중간 단계)"""
    tech_id: str
    name: str
    category: str
    trl: int
    layer: int
    prerequisites: List[str]
    lead_time_quarters: int
    start_q: str               # "YYYY QX"
    target_q: str              # "YYYY QX"
    dropped: bool              # Orchestrator drop 피드백 시 True


class RoadmapItem(TypedDict, total=False):
    """Roadmap Planner 최종 출력 단위 — Investment Strategist 입력 포맷.

    분기 단위 (start_q/target_q) + 차년도 단위 (year_idx_*) + 3가지 reasoning.
    """
    tech_id: str
    name: str
    phase_name: str            # "Phase 1: Foundation R&D" 등
    # 분기 단위 (기존 호환)
    start_q: str               # "YYYY QX"
    target_q: str              # "YYYY QX"
    # 차년도 단위 (시각화 / 보고서용) — planning_horizon 시작 연도 기준 1, 2, ..., N
    year_idx_start: int
    year_idx_target: int
    prerequisites: List[str]
    lead_time_quarters: int
    # 단일 justification (기존 호환) — reasoning.year_placement 와 동일 내용
    justification: str
    # 3가지 reasoning (보고서 시각화 + 추적성)
    reasoning: dict
    # {
    #   "year_placement":       "이 기술을 N차년도에 배치한 이유 (timing/dependency)",
    #   "tech_execution":       "이 기술을 수행해야 하는 이유 (Strategic Direction + 시장/기술 분석)",
    #   "investment_selection": "투자 선정 이유 (왜 이 기술이 핵심 투자 대상인가)",
    # }


# ── LangGraph State ───────────────────────────────────────────

class DroppedTech(TypedDict):
    tech_id: str
    name: str
    reason: str   # 80자 이내 — 왜 제외됐는지


class TechSelection(TypedDict, total=False):
    selected_count: int
    rationale: str                  # 전체 선별 근거 (200자 이내)
    dropped: List[DroppedTech]


class RoadmapState(TypedDict):
    # ① 입력 (Analyst Agent 로부터)
    tech_candidates: List[TechCandidate]
    market_context: dict          # {"target_market": ..., "expected_boom_quarter": "YYYY QX"}
    reference_year: Optional[int]  # 사용자 명시 horizon 종료 연도 (예: 2030)
                                    # tech_selector / roadmap_builder 의 LLM 프롬프트에서 활용

    # ② 중간 결과
    tech_selection: Optional[TechSelection]   # tech_selector 산출 (선별 사유 + 제외 목록)
    dependency_tree: Optional[dict]           # {tech_id: DependencyNode}
    timeline_draft: Optional[List[TimelineItem]]

    # ③ 최종 출력
    planned_roadmap: Optional[List[RoadmapItem]]

    # ④ 오케스트레이터 피드백 (재설계 시 사용)
    #    {"shift": [{"tech_id": "T02", "new_start_q": "2026 Q1"}],
    #     "drop":  ["T04"],
    #     "text":  ["피드백 문장", ...]}
    orchestrator_feedback: Optional[dict]

    # ④-b Company Scenario + Strategic Direction (상위 컨텍스트, Setup 추출)
    # 모든 LLM 노드 (tech_selector / roadmap_designer / dependency_analyzer /
    # roadmap_builder) 의 프롬프트에 박힘
    company_scenario: Optional[dict]
    strategic_direction: Optional[List[str]]

    # ⑤ 제어
    messages: Annotated[List[BaseMessage], add_messages]
    error: Optional[str]
    iteration: int
