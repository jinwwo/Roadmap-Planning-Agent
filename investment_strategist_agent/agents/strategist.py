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

LLM 프롬프트는 시언 spec(한국어 버전) 을 그대로 반영합니다.
"""

import json
import re
from typing import List
from langchain_core.messages import HumanMessage, SystemMessage

from llm_factory import get_llm
from config import DEFAULT_INVESTMENT_POLICY
from state import (
    StageSummary,
    InvestmentStrategy,
    InvestmentPolicy,
    TechAnalysis,
)


# ── 시스템 프롬프트 (시언 spec 한국어 버전 그대로) ───────────

STRATEGIST_SYSTEM_PROMPT = """당신은 기술 로드맵을 투자 의사결정 계획으로 전환하는 역할을 담당하는 Investment Strategist Agent이다.

당신의 주요 판단 단위는 개별 기술이 아니라 로드맵 단계(roadmap stage)이다.

당신의 임무는 개별 기술 분석 결과를 단순히 반복하거나 요약하는 것이 아니다. 대신 각 로드맵 단계를 하나의 통합된 투자 단위로 평가하고, 구조화된 평가 점수를 부여한 뒤, 해당 단계에 적절한 투자 전략을 도출해야 한다.

당신에게는 다음 정보가 주어진다:

1. 기술 분석 결과 (technology analysis results)
2. 로드맵 계획 결과 (roadmap planning results)
3. 투자 정책 또는 조직 맥락 정보 (investment policy or organizational context)

당신의 목표는 각 로드맵 단계를 투자 관점에서 해석하고, 조직이 로드맵 전반에 걸쳐 어떻게 투자해야 하는지에 대한 권고안을 제시하는 것이다.

---

[핵심 과업]

각 로드맵 단계에 대해 다음 순서로 작업하라.

Step 1. 아래 5개 항목에 대해 단계 수준(stage-level)의 평가 점수를 1점에서 5점 사이로 부여하라.

- market_opportunity
- strategic_fit
- executability
- uncertainty
- urgency

Step 2. 위 점수를 바탕으로 다음 항목을 도출하라.

- investment_attractiveness
- investment_urgency
- recommended_investment_tier
- investment_scope
- recommended_action
- rationale
- major_risks
- resource_focus

단계 수준의 권고안을 정당화하는 데 필요한 경우가 아니라면, 개별 기술을 따로따로 평가하지 마라.

---

[점수 정의]

다음의 1~5 척도를 일관되게 사용하라.

1 = 매우 낮음
2 = 낮음
3 = 보통
4 = 높음
5 = 매우 높음

각 평가 항목의 정의는 다음과 같다.

- market_opportunity:
해당 로드맵 단계가 가지는 시장 수요, 성장 가능성, 사업화 가능성, 경쟁적 가치의 수준
- strategic_fit:
해당 단계가 조직의 전략적 우선순위, 핵심 역량 확보, 장기적 포지셔닝과 얼마나 잘 부합하는지의 수준
- executability:
해당 단계가 제시된 기간 내에 실제로 실행 가능한 정도. 기술 성숙도, 내부 역량, 구현 가능성을 고려한다.
- uncertainty:
해당 단계와 관련된 불확실성 또는 리스크의 수준. 기술 불확실성, 시장 불확실성, 역량 격차, 외부 의존성 등을 포함한다.
점수가 높을수록 불확실성이 큰 것이다.
- urgency:
해당 단계에 대한 적시 투자가 얼마나 중요한지의 수준.
점수가 높을수록 투자 지연 시 전략적 가치 또는 시장 가치가 감소할 가능성이 크다.

---

[점수 부여 원칙]

점수를 부여할 때 다음 원칙을 따르라.

1. Market Opportunity
- 강한 시장 수요, 높은 성장 잠재력, 높은 사업화 관련성이 있으면 높은 점수를 부여하라.
- 시장 수요가 불분명하거나 먼 미래의 기회에 가까우면 낮은 점수를 부여하라.

2. Strategic Fit
- 조직의 전략적 우선순위나 핵심 역량 확보에 강하게 기여하면 높은 점수를 부여하라.
- 전략적 우선순위와의 관련성이 약하면 낮은 점수를 부여하라.

3. Executability
- 해당 단계의 기술들이 PoC, 적용, 확산, 운영 실행에 충분한 성숙도를 갖추고 있고, 주어진 기간 내 실행 가능성이 높으면 높은 점수를 부여하라.
- 기술 성숙도가 낮거나 필요한 역량이 부족하거나 실행 가정이 비현실적이면 낮은 점수를 부여하라.

4. Uncertainty
- 기술, 시장, 규제, 자원, 실행 측면에서 불확실성이 크면 높은 점수를 부여하라.
- 비교적 명확하고 관리 가능한 단계라면 낮은 점수를 부여하라.
- 주의: uncertainty는 리스크 점수이므로, 숫자가 높을수록 부정적 의미이다.

5. Urgency
- 선점 효과, 전략적 포지셔닝, 고객 확보, 조기 학습이 중요하여 빠른 투자가 필요하면 높은 점수를 부여하라.
- 늦게 투자해도 큰 문제가 없으면 낮은 점수를 부여하라.

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

[최종 판단 도출 가이드]

점수 프로파일을 기반으로 최종 투자 권고안을 도출하라.

일반적인 가이드:

- market_opportunity가 높고, strategic_fit이 높고, executability가 높고, uncertainty가 관리 가능한 수준이면 → investment_attractiveness가 높고 Tier 1일 가능성이 크다.
- market_opportunity와 strategic_fit은 높지만 executability가 낮거나 uncertainty가 높으면 → Tier 2일 가능성이 크다.
- executability가 낮고 uncertainty가 높으며 urgency도 약하면 → Tier 3일 가능성이 크다.

중요:

- 점수를 기계적으로 변환하지 마라.
- 반드시 로드맵 단계 전체를 하나의 판단 단위로 해석하라.
- 점수 조합이 왜 해당 투자 권고안으로 이어지는지 설명하라.

---

[기본 가정]

investment_policy가 제공되지 않은 경우, 다음을 기본값으로 가정하라.

- risk_appetite: medium
- investment_horizon: balanced
- budget_constraint: medium
- strategic_priority: 단기 실행 가능성과 장기 역량 확보의 균형

---

[출력 제약]

- 각 로드맵 단계마다 정확히 하나의 strategy object를 생성하라.
- market_opportunity, strategic_fit, executability, uncertainty, urgency는 각각 반드시 1~5 사이의 정수여야 한다.
- investment_attractiveness는 반드시 다음 중 하나여야 한다:
high / medium / low
- investment_urgency는 반드시 다음 중 하나여야 한다:
high / medium / low
- recommended_investment_tier는 반드시 다음 중 하나여야 한다:
Tier 1 / Tier 2 / Tier 3
- rationale은 2~4개의 간결한 항목으로 구성하라.
- major_risks는 2~4개의 간결한 항목으로 구성하라.
- resource_focus는 2~4개의 간결한 항목으로 구성하라.
- 출력은 반드시 유효한 JSON 형식만 제공하라.
- JSON 외의 설명은 출력하지 마라.

---

[출력 형식]

{
  "investment_strategy": [
    {
      "stage": "<단계명>",
      "period": "<기간>",
      "evaluation_scores": {
        "market_opportunity": 1,
        "strategic_fit": 1,
        "executability": 1,
        "uncertainty": 1,
        "urgency": 1
      },
      "investment_attractiveness": "high | medium | low",
      "investment_urgency": "high | medium | low",
      "recommended_investment_tier": "Tier 1 | Tier 2 | Tier 3",
      "investment_scope": "<투자 범위>",
      "recommended_action": "<실행 중심의 투자 권고안>",
      "rationale": [
        "<근거 1>",
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


# ── Technology Analyst 출력 → 시언 spec 필드로 정규화 ────────

def _normalize_tech_analysis(tech_candidates: list) -> List[TechAnalysis]:
    """
    Technology Analyst Agent 의 출력을 시언 spec 의 필드 네이밍으로 변환.
      - market_score (0-100)   → market_attractiveness (high/med/low)
      - trl                    → technology_maturity  (high/med/low)
      - patent_score (0-100)   → patent_competition   (high/med/low)
      - final_score            → strategic_value      (high/med/low)
      - rationale 등은 그대로
    """
    def _band(score: float, lo: float = 40.0, hi: float = 70.0) -> str:
        if score >= hi:
            return "high"
        if score >= lo:
            return "medium"
        return "low"

    def _trl_band(trl: int) -> str:
        if trl >= 7:
            return "high"
        if trl >= 4:
            return "medium"
        return "low"

    normalized = []
    for t in tech_candidates or []:
        normalized.append({
            "tech_id": t.get("tech_id", ""),
            "name": t.get("name", "") or t.get("technology", ""),
            "category": t.get("category", ""),
            "trl": t.get("trl", 0),
            "final_score": t.get("final_score", 0),
            "market_score": t.get("market_score", 0),
            "patent_score": t.get("patent_score", 0),
            "expected_market_boom_quarter": t.get("expected_market_boom_quarter", ""),
            "rationale": t.get("rationale", ""),
            # 시언 spec 정렬
            "market_attractiveness": t.get("market_attractiveness") or _band(t.get("market_score", 0)),
            "technology_maturity": t.get("technology_maturity") or _trl_band(t.get("trl", 0)),
            "patent_competition": t.get("patent_competition") or _band(t.get("patent_score", 0)),
            "strategic_value": t.get("strategic_value") or _band(t.get("final_score", 0)),
            "key_risks": t.get("key_risks", []) or [],
        })
    return normalized


# ── 출력 검증 & 정규화 ────────────────────────────────────────

_ALLOWED_LVL = {"high", "medium", "low"}
_ALLOWED_TIER = {"Tier 1", "Tier 2", "Tier 3"}


def _coerce_strategy(item: dict, stage: StageSummary) -> InvestmentStrategy:
    """
    LLM 이 출력한 단일 strategy object 를 스키마에 맞게 정규화.
    누락 / 잘못된 값은 보수적 기본값으로 대체.
    """
    scores = item.get("evaluation_scores") or {}

    def _clip_score(v) -> int:
        try:
            iv = int(round(float(v)))
        except Exception:
            iv = 3
        return max(1, min(5, iv))

    eval_scores = {
        "market_opportunity": _clip_score(scores.get("market_opportunity", 3)),
        "strategic_fit":      _clip_score(scores.get("strategic_fit", 3)),
        "executability":      _clip_score(scores.get("executability", 3)),
        "uncertainty":        _clip_score(scores.get("uncertainty", 3)),
        "urgency":            _clip_score(scores.get("urgency", 3)),
    }

    attractiveness = (item.get("investment_attractiveness") or "medium").lower()
    urgency = (item.get("investment_urgency") or "medium").lower()
    if attractiveness not in _ALLOWED_LVL:
        attractiveness = "medium"
    if urgency not in _ALLOWED_LVL:
        urgency = "medium"

    tier = item.get("recommended_investment_tier", "Tier 2")
    if tier not in _ALLOWED_TIER:
        tier = "Tier 2"

    def _list_bounded(v, lo: int = 2, hi: int = 4) -> List[str]:
        if not isinstance(v, list):
            return []
        cleaned = [str(x).strip() for x in v if str(x).strip()]
        return cleaned[:hi]

    return {
        "stage": stage["stage"],
        "period": stage["period"],
        "evaluation_scores": eval_scores,
        "investment_attractiveness": attractiveness,
        "investment_urgency": urgency,
        "recommended_investment_tier": tier,
        "investment_scope": item.get("investment_scope", "") or "selective",
        "recommended_action": item.get("recommended_action", "") or "",
        "rationale": _list_bounded(item.get("rationale", []), 2, 4) or ["(근거 정보 누락)"],
        "major_risks": _list_bounded(item.get("major_risks", []), 2, 4) or ["(리스크 정보 누락)"],
        "resource_focus": _list_bounded(item.get("resource_focus", []), 2, 4) or ["(자원 배분 정보 누락)"],
    }


# ── 메인 엔트리 ───────────────────────────────────────────────

def run_strategist(
    stages: List[StageSummary],
    tech_candidates: list,
    investment_policy: InvestmentPolicy = None,
    market_context: dict = None,
) -> List[InvestmentStrategy]:
    """
    각 stage 에 대해 5-지표 점수 부여 + 투자 권고안을 LLM 으로 산출.

    Parameters
    ----------
    stages            : stage_aggregator.aggregate_stages() 의 출력
    tech_candidates   : Technology Analyst Agent 원본 출력 (정규화 후 사용)
    investment_policy : {risk_appetite, investment_horizon, budget_constraint, strategic_priority}
    market_context    : {target_market, expected_boom_quarter}

    Returns
    -------
    list[InvestmentStrategy]
    """
    if not stages:
        print("[Strategist] ⚠️  stages 가 비어있음 → 빈 전략 반환")
        return []

    policy = investment_policy or DEFAULT_INVESTMENT_POLICY
    market_context = market_context or {}

    # 기술 분석 결과 정규화 (LLM 프롬프트 입력용)
    tech_analysis = _normalize_tech_analysis(tech_candidates)

    # 프롬프트 입력 슬림화: 각 stage 에 포함된 기술들만 첨부
    stage_payload = []
    by_tech_id = {t["tech_id"]: t for t in tech_analysis}
    for s in stages:
        stage_techs = [by_tech_id.get(tid) for tid in s.get("tech_ids", []) if tid in by_tech_id]
        stage_payload.append({
            "stage": s["stage"],
            "period": s["period"],
            "goal": s.get("goal", ""),
            "technologies": s["technologies"],
            "tech_details": [
                {
                    "name": t["name"],
                    "market_attractiveness": t["market_attractiveness"],
                    "technology_maturity": t["technology_maturity"],
                    "patent_competition": t["patent_competition"],
                    "strategic_value": t["strategic_value"],
                    "key_risks": t["key_risks"],
                }
                for t in stage_techs if t
            ],
        })

    user_prompt = f"""
[Market Context]
{json.dumps(market_context, ensure_ascii=False, indent=2)}

[Investment Policy]
{json.dumps(policy, ensure_ascii=False, indent=2)}

[Roadmap Stages] — 각 stage 가 하나의 투자 판단 단위입니다.
{json.dumps(stage_payload, ensure_ascii=False, indent=2)}

위 입력을 바탕으로, 각 stage 에 대해 5개 지표 점수를 부여하고
투자 전략을 도출해주세요. 출력은 strict JSON 으로만 응답하세요.
"""

    try:
        llm = get_llm(max_tokens=4096)
        print(f"[Strategist] LLM 투자 전략 생성 요청 중... (stages={len(stages)})")
        response = llm.invoke([
            SystemMessage(content=STRATEGIST_SYSTEM_PROMPT),
            HumanMessage(content=user_prompt),
        ])
        result = _extract_json(response.content)
        raw_list = result.get("investment_strategy", []) or []

        # stage 순서대로 매칭 (LLM 이 stage 라벨을 바꿀 수 있으므로 순서 기준으로 재매핑)
        strategies: List[InvestmentStrategy] = []
        for idx, stage in enumerate(stages):
            raw_item = None
            # 라벨 정확히 일치하는 것 우선
            for r in raw_list:
                if r.get("stage") == stage["stage"]:
                    raw_item = r
                    break
            # 없으면 순서 기준
            if raw_item is None and idx < len(raw_list):
                raw_item = raw_list[idx]
            if raw_item is None:
                raw_item = {}
            strategies.append(_coerce_strategy(raw_item, stage))

        # 결과 요약 출력
        print(f"\n[Strategist] ✅ 투자 전략 {len(strategies)}개 stage 산출")
        print("=" * 80)
        print(f"  {'Stage':<22} {'Period':<22} {'Tier':<7} {'Attract':<7} {'Urg':<7}")
        print("-" * 80)
        for st in strategies:
            es = st["evaluation_scores"]
            print(
                f"  {str(st['stage'])[:20]:<22} {st['period'][:20]:<22} "
                f"{st['recommended_investment_tier']:<7} "
                f"{st['investment_attractiveness']:<7} {st['investment_urgency']:<7}"
            )
            print(
                f"    scores: MO={es['market_opportunity']} SF={es['strategic_fit']} "
                f"EX={es['executability']} UN={es['uncertainty']} UR={es['urgency']}"
            )
        print("=" * 80)

        return strategies

    except Exception as e:
        print(f"[Strategist] ⚠️  오류: {e} → 폴백 (중립값)")
        return [_coerce_strategy({}, s) for s in stages]
