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
    # 70B+ 또는 32B+ 는 강함으로 간주
    strong_indicators = (":70b", "70b-", ":32b", "32b-", ":34b", "34b-", "llama3.1:70b", "qwen2.5:32b", "qwen2.5:72b")
    for ind in strong_indicators:
        if ind in model:
            return True
    return False


# ── 시스템 프롬프트 ───────────

STRATEGIST_SYSTEM_PROMPT = """당신은 기술 로드맵을 투자 의사결정 계획으로 전환하는 역할을 담당하는 Investment Strategist Agent이다.

당신의 주요 판단 단위는 개별 기술이 아니라 로드맵 단계(roadmap stage)이다.

당신의 임무는 개별 기술 분석 결과를 단순히 반복하거나 요약하는 것이 아니다. 대신 각 로드맵 단계를 하나의 통합된 투자 단위로 평가하고, 구조화된 평가 점수를 부여한 뒤, 해당 단계에 적절한 투자 전략을 도출해야 한다.

당신에게는 다음 정보가 주어진다:

1. 기술 분석 결과 (technology analysis results)
2. 로드맵 계획 결과 (roadmap planning results)
3. 투자 정책 또는 조직 맥락 정보 (investment policy or organizational context)
4. 시장 맥락 정보 (market context — target_market, expected_boom_quarter)

당신의 목표는 각 로드맵 단계를 투자 관점에서 해석하고, 조직이 로드맵 전반에 걸쳐 어떻게 투자해야 하는지에 대한 권고안을 제시하는 것이다.

각 stage 의 tech_candidates 에 포함된 시장 정보 (market_score, expected_market_boom_quarter, rationale 의 [Market] 섹션 등) 를 참고하여 평가에 반영하라.

---

[핵심 과업]

각 로드맵 단계에 대해 다음 순서로 작업하라.

Step 1. **stage 통합 판단** (narrative) — 점수를 매기는 게 아니라, 이 stage 를 전체로 보았을 때의 통합적 판단을 1~3문장으로 `stage_assessment` 에 작성하라.

다음 관점을 종합:
- **Timing**: stage period vs `expected_boom_quarter` — 적시인가, 지연인가, 너무 이른가?
- **Synergy**: stage 안의 기술들이 함께 진행되어야 하는 시너지가 있는가? 일부만 해도 되는가?
- **Dependency**: 후속 stage 와의 의존 관계 — 이 stage 가 무엇을 후속에 제공하는가?
- **Scale**: 전체적으로 어느 정도의 자원이 필요한 stage 인가?

이 판단은 Step 2 의 각 기술 평가에 컨텍스트로 작용한다.

Step 2. **stage 안의 각 기술별 투자 평가** (`tech_investments`) — 투자 의사결정의 실제 단위는 개별 기술이다. Step 1 의 stage 통합 판단을 컨텍스트로 두고, 각 tech_candidate 에 대해 평가 점수와 투자 전략을 결정하라.

각 tech 에 대해:

- `tech_id`, `name`: 입력에서 그대로 가져옴
- `evaluation_scores` (5축 1~5):
  · market_opportunity, strategic_fit, executability, uncertainty, urgency
  · 평가 기준 / 점수 정의 / 점수 부여 원칙은 아래 [점수 정의], [점수 부여 원칙] 적용
  · **stage 컨텍스트 반영**: stage period, 시장 timing, 의존성 등을 점수에 반영. 예: 같은 시장 강도여도 boom 이후의 stage 면 urgency 가 낮아진다.
- `investment_attractiveness` (high/medium/low): 시장 기회·전략 적합성·실행성을 종합한 매력도
- `investment_urgency` (high/medium/low): 적시성. stage period + boom timing 반영.
- `recommended_investment_tier` (Tier 1/2/3):
  · 같은 stage 안에서도 tech 마다 다를 수 있다 — 예: 1단계에서 T01 은 Tier 1, T02 는 Tier 2
- `investment_scope`: aggressive / proactive / selective / milestone-based / exploratory / watchful 등
- `recommended_action`: 실행 중심의 투자 권고 (1~2문장)
- `rationale`: 2~4개 항목. **stage 통합 판단 + tech 자체 신호** 둘 다 인용 (예: "stage 가 boom 직전 적시 + T01 의 시장 규모 $117B → Tier 1")
- `major_risks`: 2~4개. tech 고유 리스크 + stage 차원 리스크 모두 가능
- `resource_focus`: 2~4개. PoC 예산, 인력, 인프라, 검증 등

Step 3. **각 stage 의 예산 배분 비율** 결정 (`stage_budget_ratio`)

각 stage 가 전체 total_budget 의 몇 % 를 받을지를 LLM 이 결정:
- 모든 stage 의 ratio 합 ≈ 1.0 (전체 100%) — 코드가 자동 정규화하지만 LLM 이 의식적으로 맞추기
- 0.0 ~ 1.0 사이 실수 (예: 0.40 = 40%)

판단 기준:
- stage 의 Tier 분포 (Tier 1 많은 stage 면 비중 ↑)
- stage 의 timing 중요도 (boom 직전 stage 면 비중 ↑)
- stage 의 자원 집약도 (인프라/장비 큰 stage 면 비중 ↑)
- 전체 로드맵에서 이 stage 의 전략적 위치

예시 (3개 stage, total_budget = $5B):
- 1단계 R&D: 0.40 ($2.0B) — 기반 인프라 큰 투자
- 2단계 공정 통합: 0.35 ($1.75B) — 핵심 기술 검증
- 3단계 시스템 검증: 0.25 ($1.25B) — 마지막 통합

**Per-tech 별 예산은 결정하지 않음** — Tier 라벨이 우선순위 신호 역할.

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

6. Resource Requirement
- 해당 단계가 대규모 자본, 핵심 인력, 인프라 구축, 외부 파트너십, 광범위한 검증 작업 등을 요구하면 높은 점수를 부여하라.
- 소규모 PoC 또는 기존 자원으로 처리 가능한 단계라면 낮은 점수를 부여하라.

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
- total_budget: 미지정 (사용자가 명시한 산업/기업 규모 맥락에서 합리적으로 추정하여 평가)
- strategic_priority: 단기 실행 가능성과 장기 역량 확보의 균형

---

[출력 제약]

- 각 로드맵 단계마다 정확히 하나의 stage block 을 생성하라.
- 각 stage block 의 `tech_investments` 배열에는 그 stage 에 속한 모든 tech_candidate 에 대해 하나씩 평가 entry 를 생성하라 (입력에 들어온 tech 를 누락하지 마라).
- 각 tech 의 `evaluation_scores` 는 market_opportunity, strategic_fit, executability, uncertainty, urgency 5축 모두 반드시 1~5 사이의 정수여야 한다.
- 각 tech 의 `investment_attractiveness`, `investment_urgency` 는 반드시 high / medium / low 중 하나.
- 각 tech 의 `recommended_investment_tier` 는 반드시 Tier 1 / Tier 2 / Tier 3 중 하나.
- 같은 stage 안의 tech 들이 모두 같은 Tier 일 필요는 없다 (오히려 차별화가 자연스러움).
- 각 tech 의 `rationale`, `major_risks`, `resource_focus` 는 각각 2~4개의 간결한 항목.
- 각 stage 의 `stage_budget_ratio` 는 0.0~1.0 사이의 실수, 모든 stage 합 ≈ 1.0.
- `stage_assessment` 는 1~3 문장의 narrative (점수 X).
- 출력은 반드시 유효한 JSON 형식만 제공하라.
- JSON 외의 설명은 출력하지 마라.

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
            "<stage 판단 + tech 신호 종합 근거 1>",
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


def _coerce_tech_investment(t_item: dict, stage_techs: list) -> dict:
    """LLM 이 출력한 단일 tech_investment object 를 스키마에 맞게 정규화."""
    scores = t_item.get("evaluation_scores") or {}
    eval_scores = {
        "market_opportunity": _clip_score(scores.get("market_opportunity", 3)),
        "strategic_fit":      _clip_score(scores.get("strategic_fit", 3)),
        "executability":      _clip_score(scores.get("executability", 3)),
        "uncertainty":        _clip_score(scores.get("uncertainty", 3)),
        "urgency":            _clip_score(scores.get("urgency", 3)),
    }

    attractiveness = (t_item.get("investment_attractiveness") or "medium").lower()
    urgency = (t_item.get("investment_urgency") or "medium").lower()
    if attractiveness not in _ALLOWED_LVL:
        attractiveness = "medium"
    if urgency not in _ALLOWED_LVL:
        urgency = "medium"

    tier = t_item.get("recommended_investment_tier", "Tier 2")
    if tier not in _ALLOWED_TIER:
        tier = "Tier 2"

    return {
        "tech_id": t_item.get("tech_id", "") or "",
        "name": t_item.get("name", "") or "",
        "evaluation_scores": eval_scores,
        "investment_attractiveness": attractiveness,
        "investment_urgency": urgency,
        "recommended_investment_tier": tier,
        "investment_scope": t_item.get("investment_scope", "") or "selective",
        "recommended_action": t_item.get("recommended_action", "") or "",
        "rationale": _list_bounded(t_item.get("rationale", []), 4) or ["(근거 정보 누락)"],
        "major_risks": _list_bounded(t_item.get("major_risks", []), 4) or ["(리스크 정보 누락)"],
        "resource_focus": _list_bounded(t_item.get("resource_focus", []), 4) or ["(자원 배분 정보 누락)"],
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

    # stage_assessment 폴백 — 옛 스키마면 stage 의 첫 rationale 항목으로 대체
    stage_assessment = (item.get("stage_assessment") or "").strip()
    if not stage_assessment and has_old_schema:
        old_rationale = item.get("rationale", [])
        if isinstance(old_rationale, list) and old_rationale:
            stage_assessment = "; ".join(str(r) for r in old_rationale[:2])
    if not stage_assessment:
        stage_assessment = "(stage 통합 판단 누락)"

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
            stages, tech_candidates, investment_policy, market_context, orchestrator_feedback
        )
    else:
        print(f"[Strategist] 작은 LLM 감지 ({LLM_PROVIDER}/{OLLAMA_MODEL}) → per-stage 분할 전략")
        strategies = _run_strategist_per_stage(
            stages, tech_candidates, investment_policy, market_context, orchestrator_feedback
        )

    # ── 후처리: stage_budget_ratio 정규화 + stage_estimated_usd 계산 ──
    total_budget = float((investment_policy or {}).get("total_budget", 0) or 0)
    strategies = _normalize_budget_allocation(strategies, total_budget)
    return strategies


def _normalize_budget_allocation(strategies: list, total_budget: float) -> list:
    """
    Stage 단위 예산 분배 — 모든 stage 의 stage_budget_ratio 합이 1.0 이 되도록 정규화
    + 절대 USD 계산. LLM 이 정확히 1.0 으로 안 맞춰도 자동 보정.

    각 stage 안의 tech 들에는 별도 USD 분배하지 않음 — Tier 라벨이 우선순위 신호.
    """
    # 모든 stage 의 stage_budget_ratio 수집
    raw_ratios = [st.get("stage_budget_ratio", 0) or 0 for st in strategies]
    total_ratio = sum(raw_ratios)

    if total_ratio <= 0:
        # 모두 0 이면 균등 분배
        n = len(strategies)
        if n > 0:
            scale = 1.0 / n
            norm_ratios = [scale] * n
        else:
            norm_ratios = []
    else:
        # 정규화 (합 = 1.0)
        norm_ratios = [r / total_ratio for r in raw_ratios]

    # 각 stage 에 정규화 ratio + 절대 USD 기록
    for st, nr in zip(strategies, norm_ratios):
        st["stage_budget_ratio"] = round(nr, 4)
        st["stage_estimated_usd"] = round(total_budget * nr, 0)

    # 합산 검증 로그
    total_estimated = sum(st.get("stage_estimated_usd", 0) for st in strategies)
    if total_budget > 0:
        ratio_summary = ", ".join(
            f"{st['stage'][:14]}: {st.get('stage_budget_ratio', 0)*100:.0f}%"
            for st in strategies
        )
        print(
            f"[Strategist] 💰 단계별 예산 분배: total ${total_budget/1e9:.2f}B "
            f"vs 합계 ${total_estimated/1e9:.2f}B "
            f"(원본 ratio 합 {total_ratio:.2f} → 정규화 1.00)"
        )
        print(f"             → {ratio_summary}")
    return strategies


# ── 전략 1: Per-stage 분할 (작은 LLM 용) ──────────────────────

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


def _run_strategist_per_stage(
    stages: List[StageSummary],
    tech_candidates: list,
    investment_policy: InvestmentPolicy = None,
    market_context: dict = None,
    orchestrator_feedback: dict = None,
) -> List[InvestmentStrategy]:
    """
    Per-stage LLM 분할 전략.
    각 stage 별 독립 LLM 콜 + cross-stage summary 동봉.
    출력 토큰 제한 / JSON 스키마 약한 작은 LLM 에 안정적.
    """
    policy = investment_policy or DEFAULT_INVESTMENT_POLICY
    market_context = market_context or {}
    feedback_block = _format_orchestrator_feedback(orchestrator_feedback)

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

        user_prompt = f"""
[Market Context]
{json.dumps(market_context, ensure_ascii=False, indent=2)}

[Investment Policy]
{json.dumps(policy, ensure_ascii=False, indent=2)}

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
- stage_assessment 작성 시 위 ALL STAGES SUMMARY 를 참고해 다른 stage 와의 timing / synergy / dependency 를 명시
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

    user_prompt = f"""
[Market Context]
{json.dumps(market_context, ensure_ascii=False, indent=2)}

[Investment Policy]
{json.dumps(policy, ensure_ascii=False, indent=2)}

[Roadmap Stages] — 전체 {len(stages)}개 stage 한 번에 평가 (cross-stage 추론 적극 활용)
각 stage 의 tech_candidates 는 Technology Analyst Agent 의 원본 기술 분석 결과입니다.
점수는 0~100, TRL 은 1~9, rationale 에 [Patent]/[Market] 섹션 포함될 수 있습니다.
{json.dumps(full_payload, ensure_ascii=False, indent=2)}
{feedback_block}
[작업 지시]
- 전체 {len(stages)}개 stage 모두 평가 — investment_strategy 배열에 정확히 {len(stages)}개 항목
- 각 stage 의 stage_assessment 작성 시 다른 stage 와의 timing / synergy / dependency 명시
- tech_investments 의 Tier 결정 시 전체 로드맵 안에서 stage 의 상대적 중요도 + 전체 total_budget 분배 균형 고려
  (모든 stage 가 Tier 1 일 수 없음 — 전체 균형이 중요)
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
