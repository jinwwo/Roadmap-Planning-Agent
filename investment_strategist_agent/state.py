"""
state.py
─────────
Investment Strategist Agent 내부 타입 정의.

이 에이전트의 판단 단위는 **개별 기술이 아니라 로드맵 단계(stage)** 이다.
Roadmap Planner 가 기술 단위 output 을 주더라도, 이 에이전트 내부에서
stage 단위로 집계한 뒤 LLM 평가를 수행한다.
"""

from typing import TypedDict, List, Optional


# ── 입력 측 타입 ─────────────────────────────────────────────

class RoadmapItem(TypedDict, total=False):
    """Roadmap Planner Agent 출력 단위 (참조용 — 필드가 누락되어도 동작해야 함)"""
    tech_id: str
    name: str
    phase_name: str
    start_q: str
    target_q: str
    prerequisites: List[str]
    lead_time_quarters: int
    justification: str


class TechAnalysis(TypedDict, total=False):
    """
    Technology Analyst Agent 의 원본 출력 (필드는 느슨). 시언 spec 과 맞추기 위해
    아래 필드들을 사용:
      - technology / name (기술명)
      - market_attractiveness (high/medium/low) 또는 market_score (0-100)
      - technology_maturity / trl
      - patent_competition
      - strategic_value
      - key_risks
    """
    tech_id: str
    name: str
    category: str
    trl: int
    final_score: float
    market_score: float
    patent_score: float
    expected_market_boom_quarter: str
    dependency_hints: List[str]
    rationale: str
    # 시언 spec 정렬 필드 (없으면 위 필드에서 유도)
    market_attractiveness: str
    technology_maturity: str
    patent_competition: str
    strategic_value: str
    key_risks: List[str]


# ── 단계(stage) 집계 결과 ─────────────────────────────────────

class StageSummary(TypedDict):
    """여러 RoadmapItem 을 하나의 stage 로 묶은 결과"""
    stage: str                    # "short-term" / "mid-term" / "long-term" 또는 phase_name
    period: str                   # "2025 Q1 - 2027 Q2"
    goal: str                     # 단계 목표 (phase_name 혹은 기본 문구)
    technologies: List[str]       # 포함된 기술명 리스트 (LLM 입력용)
    tech_ids: List[str]           # tech_id 리스트 (추적용)
    num_items: int


# ── 투자 전략 출력 타입 ──────────────────────────────────────

class EvaluationScores(TypedDict):
    market_opportunity: int      # 1-5
    strategic_fit: int           # 1-5
    executability: int           # 1-5
    uncertainty: int             # 1-5
    urgency: int                 # 1-5


class InvestmentStrategy(TypedDict, total=False):
    """시언 spec 의 출력 스키마 (stage 당 하나)"""
    stage: str
    period: str
    evaluation_scores: EvaluationScores
    investment_attractiveness: str        # high / medium / low
    investment_urgency: str               # high / medium / low
    recommended_investment_tier: str      # "Tier 1" / "Tier 2" / "Tier 3"
    investment_scope: str
    recommended_action: str
    rationale: List[str]                  # 2-4
    major_risks: List[str]                # 2-4
    resource_focus: List[str]             # 2-4


# ── Investment Policy ────────────────────────────────────────

class InvestmentPolicy(TypedDict, total=False):
    risk_appetite: str           # low / medium / high
    investment_horizon: str      # short / balanced / long
    budget_constraint: str       # low / medium / high
    strategic_priority: List[str]


# ── 에이전트 실행 입력 / 출력 ──────────────────────────────────

class AgentInput(TypedDict, total=False):
    market_context: dict
    planned_roadmap: List[RoadmapItem]
    technology_analysis: List[TechAnalysis]
    investment_policy: InvestmentPolicy


class AgentOutput(TypedDict):
    market_context: dict
    stages: List[StageSummary]
    investment_strategy: List[InvestmentStrategy]
