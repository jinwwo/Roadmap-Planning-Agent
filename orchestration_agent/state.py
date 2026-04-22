"""
state.py
─────────
Orchestration Agent 내부에서 주고받는 데이터 구조 정의.

sibling 에이전트들은 subprocess 로 실행되고 결과는 JSON 파일로 전달되므로
여기서는 Python 레벨의 경량 TypedDict 만 둡니다.
"""

from typing import TypedDict, List, Optional


# ── Problem Frame ─────────────────────────────────────────────

class ProblemFrame(TypedDict):
    """사용자 입력을 Orchestrator 가 구조화한 문제 정의"""
    industry: str
    company_type: str
    time_horizon: str            # "2025-2030"
    total_budget: float          # USD
    objective: str
    strategic_priorities: List[str]
    future_trend_summary: str


# ── Orchestrator Review 결과 타입 (시스템 프롬프트 스키마와 동기) ─

class TrmAssessment(TypedDict, total=False):
    feasibility: dict
    sequencing: dict
    strategic_alignment: dict
    investment_rationality: dict
    portfolio_balance: dict


class Refinement(TypedDict, total=False):
    rerun_agents: List[str]
    feedback: List[str]


class OrchestratorReport(TypedDict, total=False):
    executive_summary: str
    technology_strategy: str
    roadmap_structure: str
    investment_strategy: str
    trend_alignment: str
    feasibility_and_risk: str
    expected_outcomes: str


class ReviewResult(TypedDict, total=False):
    decision: str                # "ACCEPT" | "REVISE"
    trm_assessment: TrmAssessment
    issues: List[str]
    refinement: Refinement
    report: OrchestratorReport
    diagnostic_summary: str


# ── 파이프라인 State (subprocess 러너들 사이에서 전달) ────────

class PipelineState(TypedDict, total=False):
    # 사용자 입력
    domain: str
    reference_year: int
    category_hints: List[str]

    # Orchestrator Setup 결과
    problem_frame: ProblemFrame
    active_agents: List[str]             # ["1", "2", "3"] 중 부분집합

    # 중간 산출물 경로 (절대경로)
    path_tech_candidates: str
    path_planned_roadmap: str
    path_investment_strategy: str

    # 로드된 중간 산출물 (review 에 전달)
    tech_candidates: List[dict]
    market_context: dict
    planned_roadmap: List[dict]
    investment_strategy: List[dict]
    stages: List[dict]

    # 피드백 채널 (REVISE 시 Roadmap/Investment 재실행에 반영)
    #   {"shift": [...], "drop": [...], "text": [...]}
    orchestrator_feedback: Optional[dict]

    # 반복 카운터
    iteration: int
