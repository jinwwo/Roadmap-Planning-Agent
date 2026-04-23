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


class RoadmapItem(TypedDict):
    """Roadmap Planner 최종 출력 단위 — Investment Strategist 입력 포맷"""
    tech_id: str
    name: str
    phase_name: str            # "Phase 1: Foundation R&D" 등
    start_q: str               # "YYYY QX"
    target_q: str              # "YYYY QX"
    prerequisites: List[str]
    lead_time_quarters: int
    justification: str


# ── LangGraph State ───────────────────────────────────────────

class RoadmapState(TypedDict):
    # ① 입력 (Analyst Agent 로부터)
    tech_candidates: List[TechCandidate]
    market_context: dict          # {"target_market": ..., "expected_boom_quarter": "YYYY QX"}

    # ② 중간 결과
    dependency_tree: Optional[dict]       # {tech_id: DependencyNode}
    timeline_draft: Optional[List[TimelineItem]]

    # ③ 최종 출력
    planned_roadmap: Optional[List[RoadmapItem]]

    # ④ 오케스트레이터 피드백 (재설계 시 사용)
    #    {"shift": [{"tech_id": "T02", "new_start_q": "2026 Q1"}],
    #     "drop":  ["T04"],
    #     "text":  ["피드백 문장", ...]}
    orchestrator_feedback: Optional[dict]

    # ⑤ 제어
    messages: Annotated[List[BaseMessage], add_messages]
    error: Optional[str]
    iteration: int
