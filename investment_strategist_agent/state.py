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
    Technology Analyst Agent 의 원본 출력 (필드는 느슨). Strategist 입력에 맞추기 위해
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
    # Strategist 입력 정규화 필드 (없으면 위 필드에서 유도)
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
    uncertainty: int             # 1-5 (높을수록 리스크 큼)
    urgency: int                 # 1-5


class TechInvestment(TypedDict, total=False):
    """개별 기술 단위 투자 평가 — 투자 의사결정의 진짜 단위 (Tier 라벨 위주)"""
    tech_id: str
    name: str
    evaluation_scores: EvaluationScores   # 5-지표 (1~5 정수)
    investment_attractiveness: str        # high / medium / low
    investment_urgency: str               # high / medium / low
    recommended_investment_tier: str      # "Tier 1" / "Tier 2" / "Tier 3"
    investment_scope: str
    recommended_action: str
    rationale: List[str]                  # 2-4
    major_risks: List[str]                # 2-4
    resource_focus: List[str]             # 2-4


class InvestmentStrategy(TypedDict, total=False):
    """
    Stage 컨테이너 — stage 통합 판단(narrative) + stage 예산 + 그 안의 기술별 평가
    예산은 stage 단위만 결정 (per-tech 분배는 없음)
    """
    stage: str
    period: str
    stage_assessment: str                 # stage 통합 판단 (timing/synergy/의존성 narrative)
    stage_budget_ratio: float             # 0.0~1.0, 모든 stage 합 = 1.0 (LLM 결정)
    stage_estimated_usd: float            # total_budget × stage_budget_ratio (코드 계산)
    tech_investments: List[TechInvestment]  # 각 기술별 투자 평가 (Tier 라벨 + 권고)


# ── Investment Policy ────────────────────────────────────────

class InvestmentPolicy(TypedDict, total=False):
    risk_appetite: str           # low / medium / high
    investment_horizon: str      # short / balanced / long
    total_budget: float          # 전체 예산 (USD) — Orchestrator 의 total_budget 그대로
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
