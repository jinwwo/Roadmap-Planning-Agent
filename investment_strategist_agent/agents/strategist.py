"""
agents/strategist.py
─────────────────────
Investment Strategist Agent — 로드맵 단계(stage) 기반 투자 의사결정

판단 단위 : **로드맵 단계(stage)** (개별 기술이 아님)
Step 1   : 각 stage 에 대해 5개 지표를 1–5점으로 평가
           (market_opportunity, strategic_fit, executability, uncertainty, urgency)
Step 2   : 위 점수를 바탕으로 다음 항목 도출
           (investment_attractiveness, investment_urgency, recommended_investment_tier,
            investment_scope, recommended_action, rationale, major_risks, resource_focus)

LLM 프롬프트는 한국어 평가 기준 + 출력 스키마를 그대로 반영합니다.
"""

import json
import os
import re
from typing import List
from langchain_core.messages import HumanMessage, SystemMessage

from llm_factory import get_llm
from config import DEFAULT_INVESTMENT_POLICY, LLM_PROVIDER, CLAUDE_MODEL, OLLAMA_MODEL
from state import (
    StageSummary,
    InvestmentStrategy,
    InvestmentPolicy,
)


# ── LLM 강도 감지 — single-call vs per-stage 분기 ───────────────

def _is_strong_llm() -> bool:
    """
    현재 LLM 이 single-call 안정 처리 가능한 강한 모델인지 판정.

    - Anthropic Claude (sonnet/opus/haiku 4.x+) → 강함
    - Ollama llama3.1:70b+, qwen2.5:32b+ → 강함
    - Ollama 8b 이하 → 약함
    - 환경변수 STRATEGIST_LLM_STRATEGY 로 강제 override 가능:
        "single_call"  → 무조건 강함으로 취급
        "per_stage"    → 무조건 약함으로 취급
    """
    override = (os.getenv("STRATEGIST_LLM_STRATEGY") or "").strip().lower()
    if override == "single_call":
        return True
    if override == "per_stage":
        return False

    if LLM_PROVIDER == "anthropic":
        # Claude 는 거의 모든 모델이 strict JSON + 16K 출력 안정
        return True

    # Ollama 의 경우 모델 크기로 판정
    model = (OLLAMA_MODEL or "").lower()
    # 27B+ 는 강함으로 간주 (qwen3/qwen3.5 계열 포함)
    strong_indicators = (
        ":70b", "70b-", ":32b", "32b-", ":34b", "34b-", ":27b", "27b-",
        "llama3.1:70b", "qwen2.5:32b", "qwen2.5:72b",
        "qwen3:27b", "qwen3:32b", "qwen3:72b",
        "qwen3.5:27b", "qwen3.5:32b", "qwen3.5:72b",
    )
    for ind in strong_indicators:
        if ind in model:
            return True
    return False


# ── 시스템 프롬프트 ───────────

STRATEGIST_SYSTEM_PROMPT = """당신은 기술 로드맵을 투자 의사결정 계획으로 전환하는 Investment Strategist Agent 이다.

[판단 단위]
- 실제 투자 의사결정 단위 = **개별 기술 (tech_id)**.
- 로드맵 단계 (stage) 는 timing 컨텍스트 (period, synergy) 제공용.

[입력]
1. 로드맵 (planned_roadmap) — 각 기술의 수행 시점 (start_q, target_q, year_idx_start/target, reasoning).
2. 기술 데이터 (tech_candidates) — 각 기술의 TRL, market_score, patent_score, final_score,
   expected_market_boom_quarter, rationale (시장/기술 분석).
3. 시장 데이터 (market_context) — target_market, expected_boom_quarter.
4. 상위 컨텍스트 (Company Scenario / Strategic Direction) + Investment Policy
   (total_budget, risk_appetite, investment_horizon, strategic_priority).

[과업]
각 기술마다 아래 **5개 축**으로 1-5점 평가 후 예산을 배분하라.

축 정의:
1. **market_size_growth** (TAM/CAGR — 시장 크기·성장률)
   - 5: 거대 시장 ($100B+) + 빠른 성장 (CAGR 20%+)
   - 1: 작은 niche 시장 또는 stagnant
2. **tech_readiness** (TRL — 기술 준비도)
   - tech_candidate.trl 을 그대로 1-5 scale 로 환산 (TRL 1-2 → 1점, 3-4 → 2점, 5-6 → 3점, 7 → 4점, 8-9 → 5점)
3. **tech_risk** (기술 위험도 — 불확실성)
   - 5 = 위험 매우 큼 (낮은 TRL + 불명확한 path), 1 = 거의 검증됨
4. **competitive_advantage** (경쟁 우위)
   - 5: 회사 기존 강점 / 특허 포트폴리오 / Strategic Direction 정합 매우 높음
   - 1: 경쟁사 우위 영역, 우리 강점 없음
5. **development_urgency** (개발 긴급도)
   - 5: market boom 임박, 늦으면 기회 상실. 1: 충분히 여유 있음

---

[핵심 과업]

각 기술별로 5축 평가 + 예산 배분 + 투자 결정 reasoning 을 작성.
**stage 통합 narrative (stage_assessment) 는 작성하지 않는다 — 빈 문자열 또는 생략.**
모든 의사결정은 기술 (tech) 단위이고, stage 는 단순 컨테이너일 뿐.

각 tech 마다:
- `tech_id`, `name`: 입력 그대로
- `evaluation_scores` (5축 1-5 정수):
  · market_size_growth, tech_readiness, tech_risk, competitive_advantage, development_urgency
- `investment_attractiveness` (high/medium/low): 5축 종합 매력도
- `investment_urgency` (high/medium/low): 적시성
- `recommended_investment_tier` (Tier 1/2/3): 우선순위 라벨
- `tech_budget_usd` (정수, USD): 이 기술에 배분할 절대 금액
  · **모든 tech_budget_usd 의 합은 total_budget 을 절대 초과하면 안 됨 (hard constraint)**.
    초과 시 Review LLM 이 즉시 REVISE 한다.
  · 합이 total_budget 의 90-100% 범위가 이상적. 미만 (-25% 이내) 도 허용.
  · 5축 점수 + Tier + planning_horizon 길이를 고려한 합리적 배분.
  · 예: total_budget=$5B, 10개 tech 중 Tier 1 3개 (60%=$3B 분배) / Tier 2 4개 (30%=$1.5B) / Tier 3 3개 (10%=$500M). 합계 $5B.
- `tech_budget_rationale`: 왜 이 금액인지 (2-3 문장, 핵심 근거만).
- `reasoning` (3가지 분리 — Designer 의 reasoning 과 짝맞춤, **각 항목 2-3 문장**):
  · `market_evaluation`: TAM/CAGR, 시장 타이밍, target_market 적합도 등을 구체 수치와 함께 (2-3 문장).
  · `tech_evaluation`: TRL, 기술 위험, 경쟁 우위, 회사 강점 활용 가능성 (2-3 문장).
  · `investment_decision`: Tier + 예산 결정 근거. 5축 점수 종합 + Strategic Direction 인용 (2-3 문장).
- `investment_scope`: aggressive / proactive / selective / milestone-based / exploratory / watchful
- `recommended_action`: 실행 권고 (1-2 문장)
- `rationale`: 5축 점수 + 시장/기술 신호 인용 (2-4개 bullet, 각 1문장)
- `major_risks`: 이 기술 투자의 주요 리스크 (2-4개 bullet, 각 1문장)
- `resource_focus`: 집중 자원 — 인력, 인프라, PoC 등 (2-4개 bullet, 각 1문장)

Step 3. **각 stage 의 합산 budget ratio** (`stage_budget_ratio`)
- stage 안 tech_budget_usd 합 / total_budget. 0.0-1.0 사이 실수.
- 모든 stage 의 합 ≈ 1.0.

---

[점수 부여 — 1-5 척도, 기술 데이터/시장 데이터 직접 참조]

각 tech_candidate 의 fields (trl, market_score, patent_score, final_score,
expected_market_boom_quarter, rationale) 와 market_context, Strategic Direction 을
참조하여 점수 부여.

- **market_size_growth**: market_score 와 rationale 의 [Market] 섹션 (TAM/CAGR 언급)
  참조. 큰 TAM + 높은 CAGR → 5. 작은 niche → 1.
- **tech_readiness**: tech_candidate.trl 직접 사용.
  TRL 1-2 → 1, 3-4 → 2, 5-6 → 3, 7 → 4, 8-9 → 5.
- **tech_risk**: TRL 낮을수록 + uncertainty 신호 많을수록 ↑. 점수 ↑ = 위험 ↑ (부정).
- **competitive_advantage**: patent_score + Strategic Direction 정합 + 기존 강점 영역.
- **development_urgency**: 현재 분기 ~ expected_market_boom_quarter 까지의 distance ↓
  + boom 임박 → 5. 충분한 여유 → 1.

---

[Tier 가이드]

5축 점수 → Tier 결정 일반 가이드:
- market_size_growth ≥4 + competitive_advantage ≥4 + tech_risk ≤3 → Tier 1
- market_size_growth ≥3 + tech_risk =3 → Tier 2
- tech_risk ≥4 또는 market_size_growth ≤2 → Tier 3

기계적 변환 X — 종합 판단.

---

[Tier 정의]

다음 Tier 정의를 일관되게 사용하라.

- Tier 1:
높은 투자 우선순위를 의미한다.
시장 기회가 크고, 전략 적합성이 높으며, 단기 또는 근시일 내 실행 가능성이 충분한 단계에 해당한다.
선제적 투자 또는 우선 투자가 적합하다.
- Tier 2:
선택적 또는 단계적 투자를 의미한다.
유망한 단계이지만, 실행 가능성이 제한적이거나 의미 있는 불확실성이 존재하므로 마일스톤 기반 또는 조건부 투자가 적합하다.
- Tier 3:
탐색적 투자 또는 관찰 대상 단계를 의미한다.
즉시 실행 가능성이 낮거나 불확실성이 높기 때문에, 소규모 파일럿, 제한적 R&D, 모니터링, 옵션 확보 수준의 투자가 적합하다.

---

[기본 가정]

investment_policy가 제공되지 않은 경우, 다음을 기본값으로 가정하라.

- risk_appetite: medium
- investment_horizon: balanced
- total_budget: 미지정 (사용자가 명시한 산업/기업 규모 맥락에서 합리적으로 추정하여 평가)
- strategic_priority: 단기 실행 가능성과 장기 역량 확보의 균형

---

[출력 제약]

- 각 로드맵 단계마다 정확히 하나의 stage block 을 생성하라.
- 각 stage block 의 `tech_investments` 배열에는 그 stage 에 속한 모든 tech_candidate 에 대해 하나씩 평가 entry 를 생성 (누락 금지).
- 각 tech 의 `evaluation_scores` 는 market_size_growth, tech_readiness, tech_risk,
  competitive_advantage, development_urgency 5축 모두 반드시 1~5 정수.
- `investment_attractiveness`, `investment_urgency` 는 high/medium/low 중 하나.
- `recommended_investment_tier` 는 Tier 1/2/3 중 하나.
- `tech_budget_usd` 는 정수 (USD). 모든 tech_budget_usd 의 합이 total_budget 의 ±5% 이내.
- `reasoning.market_evaluation / tech_evaluation / investment_decision` 와 `tech_budget_rationale` 은 각각 2-3 문장.
- `recommended_action` 은 1-2 문장.
- `rationale / major_risks / resource_focus` 는 각각 2-4개 bullet (각 1문장).
- `stage_budget_ratio` 는 그 stage 안 tech_budget_usd 합 / total_budget. 모든 stage 합 ≈ 1.0.
- **`stage_assessment` 는 빈 문자열 "" 로 둘 것** (사용 X).
- 출력은 유효한 JSON only. JSON 외 텍스트 금지.

---

[출력 형식]

{
  "investment_strategy": [
    {
      "stage": "<단계명>",
      "period": "<기간>",
      "stage_assessment": "<stage 통합 판단 narrative — timing/synergy/dependency/scale 종합 1~3문장>",
      "tech_investments": [
        {
          "tech_id": "T01",
          "name": "<기술명>",
          "evaluation_scores": {
            "market_size_growth": 1,
            "tech_readiness": 1,
            "tech_risk": 1,
            "competitive_advantage": 1,
            "development_urgency": 1
          },
          "investment_attractiveness": "high | medium | low",
          "investment_urgency": "high | medium | low",
          "recommended_investment_tier": "Tier 1 | Tier 2 | Tier 3",
          "tech_budget_usd": 1500000000,
          "tech_budget_rationale": "<왜 이 금액인지 — 길이 제한 없이 충분히>",
          "reasoning": {
            "market_evaluation": "<TAM/CAGR/시장 타이밍 평가 근거 — 자세히, 길이 제한 없음>",
            "tech_evaluation": "<TRL/기술 위험/경쟁 우위 평가 근거 — 자세히>",
            "investment_decision": "<Tier + 예산 결정 근거 — 자세히>"
          },
          "investment_scope": "<투자 범위>",
          "recommended_action": "<실행 중심의 투자 권고안>",
          "rationale": [
            "<5축 점수 + 시장/기술 신호 인용 근거 1>",
            "<근거 2>"
          ],
          "major_risks": [
            "<리스크 1>",
            "<리스크 2>"
          ],
          "resource_focus": [
            "<자원 항목 1>",
            "<자원 항목 2>"
          ]
        }
      ],
      "stage_budget_ratio": 0.40
    }
  ]
}"""


# ── JSON 유틸 ─────────────────────────────────────────────────

def _extract_json(text: str) -> dict:
    cleaned = re.sub(r"```(?:json)?\s*", "", text).replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]+\}", cleaned)
        if match:
            return json.loads(match.group())
        raise ValueError(f"JSON 파싱 실패:\n{text[:300]}")


# ── 출력 검증 & 정규화 ────────────────────────────────────────

_ALLOWED_LVL = {"high", "medium", "low"}
_ALLOWED_TIER = {"Tier 1", "Tier 2", "Tier 3"}


def _clip_score(v) -> int:
    try:
        iv = int(round(float(v)))
    except Exception:
        iv = 3
    return max(1, min(5, iv))


def _list_bounded(v, hi: int = 4) -> List[str]:
    if not isinstance(v, list):
        return []
    cleaned = [str(x).strip() for x in v if str(x).strip()]
    return cleaned[:hi]


_NEW_AXES = ("market_size_growth", "tech_readiness", "tech_risk",
             "competitive_advantage", "development_urgency")
# Backward compat — 옛 axis name → 새 axis name 매핑 (옛 LLM 응답 처리)
_OLD_TO_NEW_AXIS = {
    "market_opportunity": "market_size_growth",
    "strategic_fit":      "competitive_advantage",
    "executability":      "tech_readiness",
    "uncertainty":        "tech_risk",
    "urgency":            "development_urgency",
}


def _coerce_tech_investment(t_item: dict, stage_techs: list) -> dict:
    """LLM 이 출력한 단일 tech_investment object 를 스키마에 맞게 정규화."""
    scores = t_item.get("evaluation_scores") or {}
    # 새 axis name 우선, 없으면 옛 axis name (backward compat), 둘 다 없으면 3
    def _pick(new_key: str) -> int:
        if new_key in scores:
            return _clip_score(scores[new_key])
        for old_key, mapped in _OLD_TO_NEW_AXIS.items():
            if mapped == new_key and old_key in scores:
                return _clip_score(scores[old_key])
        return 3
    eval_scores = {ax: _pick(ax) for ax in _NEW_AXES}

    attractiveness = (t_item.get("investment_attractiveness") or "medium").lower()
    urgency = (t_item.get("investment_urgency") or "medium").lower()
    if attractiveness not in _ALLOWED_LVL:
        attractiveness = "medium"
    if urgency not in _ALLOWED_LVL:
        urgency = "medium"

    tier = t_item.get("recommended_investment_tier", "Tier 2")
    if tier not in _ALLOWED_TIER:
        tier = "Tier 2"

    # tech_budget_usd — LLM 직접 출력 우선, 누락 시 0 (후처리에서 균등 배분 폴백)
    try:
        budget_usd = float(t_item.get("tech_budget_usd", 0) or 0)
    except Exception:
        budget_usd = 0.0
    budget_usd = max(0.0, budget_usd)

    # reasoning 3 분리 — LLM 출력 우선, 누락 시 다른 필드에서 폴백
    reasoning_raw = t_item.get("reasoning", {}) or {}
    rationale_list = t_item.get("rationale", []) or []
    rationale_joined = "; ".join(str(x) for x in rationale_list[:3]) if isinstance(rationale_list, list) else ""
    reasoning = {
        "market_evaluation": (reasoning_raw.get("market_evaluation") or "").strip() or "(reasoning 누락)",
        "tech_evaluation": (reasoning_raw.get("tech_evaluation") or "").strip() or "(reasoning 누락)",
        "investment_decision": (reasoning_raw.get("investment_decision") or "").strip()
            or (t_item.get("tech_budget_rationale") or rationale_joined or "(reasoning 누락)"),
    }

    return {
        "tech_id": t_item.get("tech_id", "") or "",
        "name": t_item.get("name", "") or "",
        "evaluation_scores": eval_scores,
        "investment_attractiveness": attractiveness,
        "investment_urgency": urgency,
        "recommended_investment_tier": tier,
        "tech_budget_usd": budget_usd,
        "tech_budget_rationale": (t_item.get("tech_budget_rationale", "") or "").strip(),
        "reasoning": reasoning,
        "investment_scope": t_item.get("investment_scope", "") or "selective",
        "recommended_action": t_item.get("recommended_action", "") or "",
        "rationale": _list_bounded(t_item.get("rationale", []), 4),
    }


def _coerce_strategy(item: dict, stage: StageSummary, stage_techs: list) -> InvestmentStrategy:
    """
    LLM 이 출력한 단일 stage block 을 스키마에 맞게 정규화.
    누락 / 잘못된 값은 보수적 기본값으로 대체.

    Backward compatibility: LLM 이 옛날 stage-level 스키마 (evaluation_scores,
    recommended_investment_tier 등을 stage 객체에 직접) 로 출력하면 stage 단위
    값을 모든 tech 에 복제해서 살림.
    """
    raw_invs = item.get("tech_investments") or []

    # ── Salvage: LLM 이 stage-level 옛 스키마로 출력한 경우 ───
    has_new_schema = bool(raw_invs)
    has_old_schema = (
        not has_new_schema
        and (
            item.get("evaluation_scores")
            or item.get("recommended_investment_tier")
            or item.get("investment_attractiveness")
            or item.get("recommended_action")
        )
    )

    by_tech_id = {ti.get("tech_id"): ti for ti in raw_invs if isinstance(ti, dict)}
    tech_investments = []
    for t in stage_techs:
        tid = t.get("tech_id", "")
        if tid in by_tech_id:
            tech_investments.append(_coerce_tech_investment(by_tech_id[tid], stage_techs))
        elif has_old_schema:
            # 옛 스키마 살림: stage 의 평가를 모든 tech 에 복제 (rationale 만 일반화)
            replicated = _coerce_tech_investment({
                "tech_id": tid,
                "name": t.get("name", ""),
                "evaluation_scores": item.get("evaluation_scores", {}),
                "investment_attractiveness": item.get("investment_attractiveness", "medium"),
                "investment_urgency": item.get("investment_urgency", "medium"),
                "recommended_investment_tier": item.get("recommended_investment_tier", "Tier 2"),
                "investment_scope": item.get("investment_scope", ""),
                "recommended_action": item.get("recommended_action", ""),
                "rationale": item.get("rationale", []),
                "major_risks": item.get("major_risks", []),
                "resource_focus": item.get("resource_focus", []),
            }, stage_techs)
            tech_investments.append(replicated)
        else:
            # 진짜 누락 시 입력 tech 정보로 최소 객체 생성
            placeholder = _coerce_tech_investment({
                "tech_id": tid,
                "name": t.get("name", ""),
            }, stage_techs)
            placeholder["recommended_action"] = "(LLM 평가 누락)"
            tech_investments.append(placeholder)

    # stage_assessment — 더 이상 사용하지 않음. 옛 호환 위해 LLM 출력 보존만 (없으면 빈 문자열)
    stage_assessment = (item.get("stage_assessment") or "").strip()

    # stage_budget_ratio — 0.0~1.0 사이로 clip (정규화는 후처리에서)
    raw_ratio = item.get("stage_budget_ratio")
    try:
        sratio = float(raw_ratio) if raw_ratio is not None else 0.0
    except Exception:
        sratio = 0.0
    sratio = max(0.0, min(1.0, sratio))

    return {
        "stage": stage["stage"],
        "period": stage["period"],
        "stage_assessment": stage_assessment,
        "stage_budget_ratio": sratio,
        "tech_investments": tech_investments,
    }


# ── 메인 엔트리 ───────────────────────────────────────────────

def run_strategist(
    stages: List[StageSummary],
    tech_candidates: list,
    investment_policy: InvestmentPolicy = None,
    market_context: dict = None,
    orchestrator_feedback: dict = None,
    company_scenario: dict = None,
    strategic_direction: list = None,
    planned_roadmap: list = None,
) -> List[InvestmentStrategy]:
    """
    각 stage 에 대해 5-지표 점수 부여 + 투자 권고안을 LLM 으로 산출.

    LLM 강도에 따라 자동 분기:
      - 강한 LLM (Claude / Ollama 32B+) → single-call (cross-stage 추론 풍부)
      - 약한 LLM (Ollama 8B 이하)        → per-stage 분할 + cross-stage summary

    Parameters
    ----------
    stages                : stage_aggregator.aggregate_stages() 의 출력
    tech_candidates       : Technology Analyst Agent 원본 출력 (그대로 LLM 에 전달)
    investment_policy     : {risk_appetite, investment_horizon, total_budget, strategic_priority}
    market_context        : {target_market, expected_boom_quarter}
    orchestrator_feedback : Orchestrator REVISE 시 전달되는 {"text": [...], ...}

    Returns
    -------
    list[InvestmentStrategy]
    """
    if not stages:
        print("[Strategist] ⚠️  stages 가 비어있음 → 빈 전략 반환")
        return []

    if _is_strong_llm():
        print(f"[Strategist] 강한 LLM 감지 ({LLM_PROVIDER}) → single-call 전략")
        strategies = _run_strategist_single_call(
            stages, tech_candidates, investment_policy, market_context, orchestrator_feedback,
            company_scenario=company_scenario, strategic_direction=strategic_direction,
            planned_roadmap=planned_roadmap,
        )
    else:
        print(f"[Strategist] 작은 LLM 감지 ({LLM_PROVIDER}/{OLLAMA_MODEL}) → per-stage 분할 전략")
        strategies = _run_strategist_per_stage(
            stages, tech_candidates, investment_policy, market_context, orchestrator_feedback,
            company_scenario=company_scenario, strategic_direction=strategic_direction,
            planned_roadmap=planned_roadmap,
        )

    # ── 후처리: stage_budget_ratio 정규화 + stage_estimated_usd 계산 ──
    total_budget = float((investment_policy or {}).get("total_budget", 0) or 0)
    strategies = _normalize_budget_allocation(strategies, total_budget)
    return strategies


_TIER_WEIGHT = {"Tier 1": 3.0, "Tier 2": 1.5, "Tier 3": 1.0}


def _normalize_budget_allocation(strategies: list, total_budget: float) -> list:
    """
    예산 분배 정규화:
    1. tech_budget_usd 가 있으면 그것을 기준 — total_budget 으로 정규화.
    2. 없으면 Tier weight (Tier1:Tier2:Tier3 = 3:1.5:1) 로 폴백 분배.
    3. stage_budget_ratio = stage 안 tech_budget_usd 합 / total_budget 으로 재계산.
    """
    # ── 1) 각 tech 의 raw budget 수집 ──
    all_techs = []  # list of (stage_idx, tech_dict)
    for si, st in enumerate(strategies):
        for ti in st.get("tech_investments", []):
            all_techs.append((si, ti))

    # ── 2) LLM 이 제공한 tech_budget_usd 의 합 ──
    raw_budgets = [ti.get("tech_budget_usd", 0) or 0 for _, ti in all_techs]
    raw_sum = sum(raw_budgets)

    if raw_sum > 0 and total_budget > 0:
        # 정규화: 합이 total_budget 과 일치하도록 scale (overflow 방지 hard guarantee)
        scale = total_budget / raw_sum
        if raw_sum > total_budget:
            print(f"[Strategist] ⚠️ LLM 출력 합 ${raw_sum/1e9:.2f}B > total_budget ${total_budget/1e9:.2f}B "
                  f"→ 비례 축소 (scale={scale:.3f})")
        for (_, ti), raw in zip(all_techs, raw_budgets):
            ti["tech_budget_usd"] = round(raw * scale, 0)
    elif total_budget > 0:
        # 폴백: Tier weight 기반 분배
        weights = [_TIER_WEIGHT.get(ti.get("recommended_investment_tier", "Tier 2"), 1.5) for _, ti in all_techs]
        w_sum = sum(weights) or 1.0
        for (_, ti), w in zip(all_techs, weights):
            ti["tech_budget_usd"] = round(total_budget * w / w_sum, 0)
            if not ti.get("tech_budget_rationale"):
                ti["tech_budget_rationale"] = f"(Tier weight 자동 분배 — {ti.get('recommended_investment_tier', 'Tier 2')})"

    # ── 3) stage_budget_ratio = stage 안 tech_budget_usd 합 / total_budget ──
    for st in strategies:
        stage_sum = sum(ti.get("tech_budget_usd", 0) for ti in st.get("tech_investments", []))
        st["stage_estimated_usd"] = round(stage_sum, 0)
        st["stage_budget_ratio"] = round(stage_sum / total_budget, 4) if total_budget > 0 else 0.0

    # ── 4) 로그 ──
    if total_budget > 0:
        total_estimated = sum(st.get("stage_estimated_usd", 0) for st in strategies)
        ratio_summary = ", ".join(
            f"{st['stage'][:14]}: {st.get('stage_budget_ratio', 0)*100:.0f}%"
            for st in strategies
        )
        print(
            f"[Strategist] 💰 예산 분배: total ${total_budget/1e9:.2f}B "
            f"vs 합계 ${total_estimated/1e9:.2f}B "
            f"(LLM raw_sum=${raw_sum/1e9:.2f}B → 정규화)"
        )
        print(f"             → stage 분포: {ratio_summary}")
        tier_totals = {}
        for _, ti in all_techs:
            tier = ti.get("recommended_investment_tier", "Tier 2")
            tier_totals[tier] = tier_totals.get(tier, 0) + ti.get("tech_budget_usd", 0)
        tier_summary = ", ".join(f"{t}: ${v/1e9:.2f}B" for t, v in sorted(tier_totals.items()))
        print(f"             → Tier 분포: {tier_summary}")
    return strategies


# ── 전략 1: Per-stage 분할 (작은 LLM 용) ──────────────────────

def _format_upper_context(company_scenario: dict, strategic_direction: list) -> str:
    """Orchestrator 추출 Company Scenario + Strategic Direction → 상위 컨텍스트 블록."""
    if not company_scenario and not strategic_direction:
        return ""
    block = "\n[Company Scenario & Strategic Direction — 상위 컨텍스트]\n"
    if company_scenario:
        cn = company_scenario.get("company_name", "")
        ind = company_scenario.get("industry", "")
        rev = company_scenario.get("annual_revenue", 0) or 0
        ratio = company_scenario.get("rd_budget_ratio", 0) or 0
        rd = company_scenario.get("annual_rd_budget", 0) or 0
        horizon = company_scenario.get("planning_horizon", "")
        block += f"- Company: {cn}\n- Industry: {ind}\n"
        if rev: block += f"- Annual Revenue: ${rev:,.0f}\n"
        if ratio: block += f"- R&D Budget Ratio: {ratio:.0%}\n"
        if rd: block += f"- Annual R&D Budget: ${rd:,.0f}\n"
        if horizon: block += f"- Planning Horizon: {horizon}\n"
    if strategic_direction:
        block += "\n[Strategic Direction]\n"
        for i, d in enumerate(strategic_direction, 1):
            block += f"  {i}. {d}\n"
    return block + "\n"


def _format_orchestrator_feedback(orchestrator_feedback: dict) -> str:
    """REVISE 시 전달된 feedback 을 LLM 프롬프트에 박을 한국어 섹션으로 포맷."""
    if not orchestrator_feedback:
        return ""
    items = orchestrator_feedback.get("text") or []
    items = [str(t).strip() for t in items if isinstance(t, str) and t.strip()]
    if not items:
        return ""
    bullet = "\n".join(f"- {t}" for t in items)
    return (
        "\n[ORCHESTRATOR REVISE FEEDBACK] — 직전 review 가 지적한 사항. "
        "이번 산출물에 반영해 Tier / 예산 / 투자 강도를 조정하라.\n"
        f"{bullet}\n"
    )


def _build_roadmap_mapping_block(planned_roadmap: list, tech_ids: list = None) -> str:
    """planned_roadmap 의 {tech_id, year_idx, reasoning} 을 user_prompt 용 블록으로 포맷.
    tech_ids 가 주어지면 해당 ID 만 필터링."""
    if not planned_roadmap:
        return ""
    by_id = {r.get("tech_id"): r for r in planned_roadmap if r.get("tech_id")}
    selected_ids = tech_ids if tech_ids else list(by_id.keys())
    lines = ["\n[Roadmap Mapping — Designer 가 매핑한 {기술, 수행년도, reasoning}]"]
    for tid in selected_ids:
        r = by_id.get(tid)
        if not r:
            continue
        ys = r.get("year_idx_start", "?")
        yt = r.get("year_idx_target", "?")
        reasoning = r.get("reasoning") or {}
        lines.append(f"- {tid} ({r.get('name', '')}): {ys}차년도 → {yt}차년도 "
                     f"({r.get('start_q', '')} → {r.get('target_q', '')})")
        yp = reasoning.get("year_placement") or r.get("justification", "")
        te = reasoning.get("tech_execution", "")
        if yp: lines.append(f"    · 차년도 배치 이유: {yp}")
        if te: lines.append(f"    · 기술 수행 이유: {te}")
    return "\n".join(lines) + "\n"


def _run_strategist_per_stage(
    stages: List[StageSummary],
    tech_candidates: list,
    investment_policy: InvestmentPolicy = None,
    market_context: dict = None,
    orchestrator_feedback: dict = None,
    company_scenario: dict = None,
    strategic_direction: list = None,
    planned_roadmap: list = None,
) -> List[InvestmentStrategy]:
    """
    Per-stage LLM 분할 전략.
    각 stage 별 독립 LLM 콜 + cross-stage summary 동봉.
    출력 토큰 제한 / JSON 스키마 약한 작은 LLM 에 안정적.
    """
    policy = investment_policy or DEFAULT_INVESTMENT_POLICY
    market_context = market_context or {}
    feedback_block = _format_orchestrator_feedback(orchestrator_feedback)
    upper_block = _format_upper_context(company_scenario, strategic_direction)

    by_tech_id = {t.get("tech_id"): t for t in (tech_candidates or []) if t.get("tech_id")}

    # ── Per-stage LLM 콜 — 각 stage 별로 독립 LLM 호출 ───────
    # 이유: 한 번에 모든 stage 를 처리하면 출력 토큰이 너무 커서
    #       (10 tech × per-tech 풀 평가 ≈ 4000+ 토큰) JSON 잘림 → 전체 fallback.
    #       stage 별 분리하면 출력 ~1500 토큰 / 콜 → 안정적.
    #
    # 단점 보완: 각 콜에 [ALL STAGES SUMMARY] 를 포함시켜 cross-stage 맥락
    #          (timing 비교, 상대 Tier, 예산 분배 큰 그림) 보존.
    print(f"[Strategist] Per-stage LLM 콜 시작 ({len(stages)}개 stage)")
    strategies: List[InvestmentStrategy] = []

    # 전체 stage 요약 (모든 콜에 공통으로 포함)
    all_stages_summary = [
        {
            "stage": s["stage"],
            "period": s["period"],
            "tech_count": len(s.get("tech_ids", [])),
            "tech_ids": s.get("tech_ids", []),
        }
        for s in stages
    ]

    for idx, stage in enumerate(stages):
        stage_techs = [by_tech_id[tid] for tid in stage.get("tech_ids", []) if tid in by_tech_id]
        single_stage_payload = [{
            "stage": stage["stage"],
            "period": stage["period"],
            "goal": stage.get("goal", ""),
            "technologies": stage["technologies"],
            "tech_candidates": stage_techs,
        }]
        roadmap_block = _build_roadmap_mapping_block(planned_roadmap, stage.get("tech_ids"))

        user_prompt = f"""{upper_block}[Market Context]
{json.dumps(market_context, ensure_ascii=False, indent=2)}

[Investment Policy]
{json.dumps(policy, ensure_ascii=False, indent=2)}
{roadmap_block}
[ALL ROADMAP STAGES SUMMARY] — 전체 로드맵 맥락 (cross-stage 평가 시 참조)
이번 평가가 아닌 다른 stage 들도 같이 보여 timing / 상대 우선순위 / 예산 분배의 큰 그림을 잡으세요.
- 평가 중인 stage 의 idx: {idx + 1} / {len(stages)}
- 전체 stage 목록:
{json.dumps(all_stages_summary, ensure_ascii=False, indent=2)}

[CURRENT STAGE — EVALUATE IN DETAIL]
이번 콜에서 상세 평가할 1 개 stage 입니다. tech_candidates 는 Technology Analyst Agent 의 원본 기술 분석 결과입니다.
(점수 0~100, TRL 1~9, rationale 에 [Patent]/[Market] 섹션 포함)
{json.dumps(single_stage_payload, ensure_ascii=False, indent=2)}
{feedback_block}
[작업 지시]
- stage_assessment 는 빈 문자열 "" 로 둘 것 (사용 X).
- 각 tech 마다 reasoning 3분리 + tech_budget_rationale 을 각각 **2-3 문장** 으로 작성 (핵심 근거만).
- tech_investments 의 Tier 결정 시 전체 로드맵 안에서 이 stage 의 상대적 중요도 고려
  (예: 후속 stage 의 기반인 stage → Tier 높게 / 후순위 stage → 더 신중하게)
- ORCHESTRATOR REVISE FEEDBACK 이 있으면 해당 지적을 반드시 반영하여 Tier 분포 / 예산 비율 조정
- 출력은 strict JSON 으로만, investment_strategy 배열에 정확히 1 개 항목
"""

        try:
            llm = get_llm(max_tokens=4096)   # 단일 stage 면 4K 충분
            print(f"  [{idx+1}/{len(stages)}] {stage['stage'][:30]} (tech {len(stage_techs)}개) LLM 호출...")
            response = llm.invoke([
                SystemMessage(content=STRATEGIST_SYSTEM_PROMPT),
                HumanMessage(content=user_prompt),
            ])
            raw_text = response.content if hasattr(response, "content") else str(response)
            result = _extract_json(raw_text)
            raw_list = result.get("investment_strategy", []) or []
            raw_item = raw_list[0] if raw_list else {}
            strategies.append(_coerce_strategy(raw_item, stage, stage_techs))
            print(f"     ✅ {stage['stage'][:30]} 평가 완료 (tech_investments {len(raw_item.get('tech_investments') or [])}건)")
        except Exception as e:
            # 단일 stage 실패해도 다른 stage 는 계속 진행
            print(f"     ⚠️ {stage['stage'][:30]} LLM 오류: {e} → 폴백 (중립값)")
            try:
                # 디버깅용: 응답 일부 출력
                sample = (raw_text[:300] if 'raw_text' in dir() else '(no response)')
                print(f"        응답 샘플: {sample!r}")
            except Exception:
                pass
            strategies.append(_coerce_strategy({}, stage, stage_techs))

    _print_strategy_summary(strategies)
    return strategies


# ── 전략 2: Single-call (강한 LLM 용) ────────────────────────

def _run_strategist_single_call(
    stages: List[StageSummary],
    tech_candidates: list,
    investment_policy: InvestmentPolicy = None,
    market_context: dict = None,
    orchestrator_feedback: dict = None,
    company_scenario: dict = None,
    strategic_direction: list = None,
    planned_roadmap: list = None,
) -> List[InvestmentStrategy]:
    """
    한 번의 LLM 콜로 모든 stage 처리.
    Cross-stage 추론 가장 풍부 — Tier 의 상대적 우선순위, 예산 분배 균형,
    timing/synergy 종합 판단을 하나의 컨텍스트에서 결정.
    Claude / Ollama 32B+ 같은 강한 LLM 에서만 안정적 (출력 토큰 ~5000+).
    """
    policy = investment_policy or DEFAULT_INVESTMENT_POLICY
    market_context = market_context or {}
    feedback_block = _format_orchestrator_feedback(orchestrator_feedback)
    upper_block = _format_upper_context(company_scenario, strategic_direction)
    by_tech_id = {t.get("tech_id"): t for t in (tech_candidates or []) if t.get("tech_id")}

    # 모든 stage 의 tech_candidates 풀 데이터 동봉
    full_payload = []
    for s in stages:
        stage_techs = [by_tech_id[tid] for tid in s.get("tech_ids", []) if tid in by_tech_id]
        full_payload.append({
            "stage": s["stage"],
            "period": s["period"],
            "goal": s.get("goal", ""),
            "technologies": s["technologies"],
            "tech_candidates": stage_techs,
        })

    roadmap_block = _build_roadmap_mapping_block(planned_roadmap)
    user_prompt = f"""{upper_block}[Market Context]
{json.dumps(market_context, ensure_ascii=False, indent=2)}

[Investment Policy]
{json.dumps(policy, ensure_ascii=False, indent=2)}
{roadmap_block}
[Roadmap Stages] — 전체 {len(stages)}개 stage 한 번에 평가 (cross-stage 추론 적극 활용)
각 stage 의 tech_candidates 는 Technology Analyst Agent 의 원본 기술 분석 결과입니다.
점수는 0~100, TRL 은 1~9, rationale 에 [Patent]/[Market] 섹션 포함될 수 있습니다.
{json.dumps(full_payload, ensure_ascii=False, indent=2)}
{feedback_block}
[작업 지시]
- 전체 {len(stages)}개 stage 모두 평가 — investment_strategy 배열에 정확히 {len(stages)}개 항목
- stage_assessment 는 빈 문자열 "" 로 둘 것 (사용 X).
- 각 tech 마다 reasoning 3분리 (market_evaluation / tech_evaluation / investment_decision) + tech_budget_rationale 을
  **충분히 자세히** 작성 (글자수 제한 없음 — 시장/기술/투자 결정 근거를 구체적인 수치 인용과 함께).
- tech_investments 의 Tier 결정 시 전체 로드맵 안에서 stage 의 상대적 중요도 + 전체 total_budget 분배 균형 고려
  (모든 tech 가 Tier 1 일 수 없음 — 전체 균형이 중요)
- ORCHESTRATOR REVISE FEEDBACK 이 있으면 해당 지적을 반드시 반영하여 Tier 분포 / 예산 비율 조정
- 출력은 strict JSON 으로만, 모든 필드 채우기
"""

    strategies: List[InvestmentStrategy] = []
    try:
        # 강한 LLM 은 16K 출력 가능 (Claude Sonnet 4.x / Ollama 70b)
        llm = get_llm(max_tokens=16384)
        print(f"[Strategist] LLM single-call 시작 ({len(stages)}개 stage 한 번에)")
        response = llm.invoke([
            SystemMessage(content=STRATEGIST_SYSTEM_PROMPT),
            HumanMessage(content=user_prompt),
        ])
        raw_text = response.content if hasattr(response, "content") else str(response)
        result = _extract_json(raw_text)
        raw_list = result.get("investment_strategy", []) or []

        # stage 순서 매칭
        for idx, stage in enumerate(stages):
            raw_item = None
            for r in raw_list:
                if r.get("stage") == stage["stage"]:
                    raw_item = r
                    break
            if raw_item is None and idx < len(raw_list):
                raw_item = raw_list[idx]
            if raw_item is None:
                raw_item = {}
            stage_techs = [by_tech_id[tid] for tid in stage.get("tech_ids", []) if tid in by_tech_id]
            strategies.append(_coerce_strategy(raw_item, stage, stage_techs))
        print(f"     ✅ Single-call 평가 완료 — {len(raw_list)} stage 응답")
    except Exception as e:
        # 강한 LLM 도 가끔 실패 — per-stage 로 폴백
        print(f"[Strategist] ⚠️ Single-call 실패: {e} → per-stage 분할로 자동 폴백")
        return _run_strategist_per_stage(
            stages, tech_candidates, investment_policy, market_context, orchestrator_feedback
        )

    _print_strategy_summary(strategies)
    return strategies


# ── 결과 요약 출력 (공통) ────────────────────────────────────

def _print_strategy_summary(strategies: List[InvestmentStrategy]) -> None:
    print(f"\n[Strategist] ✅ 투자 전략 {len(strategies)}개 stage 산출 완료")
    print("=" * 80)
    for st in strategies:
        print(f"  ▶ {st['stage']}  [{st['period']}]")
        print(f"    assessment: {st.get('stage_assessment', '')[:80]}")
        for ti in st.get("tech_investments", []):
            es = ti.get("evaluation_scores", {})
            print(
                f"      · {ti.get('tech_id', ''):<6} {ti.get('name', '')[:24]:<26} "
                f"{ti.get('recommended_investment_tier', ''):<7} "
                f"attract={ti.get('investment_attractiveness', ''):<6} "
                f"MO={es.get('market_opportunity', '?')} EX={es.get('executability', '?')} UR={es.get('urgency', '?')}"
            )
    print("=" * 80)
