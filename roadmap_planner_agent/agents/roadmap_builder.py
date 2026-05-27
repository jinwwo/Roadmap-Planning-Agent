"""
agents/roadmap_builder.py
──────────────────────────
Roadmap Planner Agent — Step 3: 최종 로드맵 조립

역할:
1. timeline_draft 를 기반으로 LLM 에게 각 항목의 배치 논리(justification)
   와 phase_name 을 생성 요청
2. Investment Strategist Agent 로 전달할 최종 planned_roadmap 완성
3. 시장 개화 시점 도달 가능 여부 검증 (경고 메시지 포함)

이 단계는 start_q / target_q / prerequisites / lead_time_quarters 를
변경하지 않습니다 (Step 2 에서 확정됨).
"""

import json
import re
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage

from llm_factory import get_llm
from state import RoadmapState, RoadmapItem
from agents.timeline_calculator import quarter_to_int


# ── 시스템 프롬프트 ───────────────────────────────────────────

ROADMAP_BUILDER_SYSTEM_PROMPT = """You are a Chief Technology Officer finalizing a technology development roadmap.
You have received a pre-calculated timeline for each technology (start_q, target_q, lead_time already
determined by a backcasting algorithm).
Your job is to write a clear, professional justification for each timeline placement and assign a phase_name.

---
[Your Only Tasks]

For each technology in the timeline_draft, provide:
1. justification: 2–3 sentences explaining WHY this specific start/target quarter was chosen.
   Must mention: current TRL, lead_time_quarters, prerequisites (if any), and market target alignment.
2. phase_name : A meaningful label for this development stage.
   Examples: "1단계: 기반 R&D", "2단계: 공정 통합", "3단계: 시스템 검증", "4단계: 양산 전환"

DO NOT change start_q, target_q, prerequisites, or lead_time_quarters.
DO NOT add or remove technologies.

---
[phase_name 단조 증가 룰 — 필수]

**phase_name 의 단계 번호는 timeline 시작 분기 순서대로 단조 증가해야 한다.**
즉 같은 단계 안의 기술끼리는 시작 분기가 비슷하고, 단계 번호가 클수록 시작 분기가 늦어야 한다.

자연스러운 단계 흐름 (시간순):
  1 단계: 기반 R&D / 기술 탐색           ← 가장 빨리 시작 (TRL 낮음 또는 enabling tech)
  2 단계: 기술 개발 / 프로토타입          ← 그 다음
  3 단계: 통합 / 검증                     ← 더 후행
  4 단계: 양산 전환 / 상용화              ← 가장 늦게 (TRL 7+ 양산 가까운 단계)

금지 사항:
- "양산 전환" 단계가 "기술 개발" 단계보다 빨리 시작되면 안 됨
- 같은 시작 분기 ± 2 분기 이내 기술은 같은 단계로 묶어도 OK
- 단계 번호와 시작 분기가 모순되는 라벨링 금지

[입력의 timeline_draft 는 시작 분기 오름차순으로 정렬되어 전달됨] — 이 순서를 그대로 따라 단계 번호를 부여하면 안전.

---
[reference_year horizon 정합 — 필수]

**입력에 reference_year (예: 2030) 가 명시되면, 로드맵의 마지막 phase 의 target_q 가
reference_year 와 가까운 분기 (그 해 안 또는 직전 1년 이내) 까지 도달하도록 phase_name /
justification 을 작성하라.**

- 마지막 phase 의 justification 에 "reference_year={N} horizon 의 종료 시점에 양산 / 상용화 도달"
  같은 표현을 명시할 것
- 만약 timeline 의 마지막 target_q 가 reference_year 보다 한참 빠르면 (예: 2027 인데 reference_year=2030)
  → justification 에 "양산 후 안정화 / 차세대 R&D 단계가 {reference_year}까지 지속됨" 같은 후속 활동을 명시
- 단계 라벨이 "양산 전환" 으로 끝나도 reference_year 까지의 활동을 narrative 로 cover
- timeline 분기 자체는 변경 금지 (start_q / target_q 그대로) — 단 "reference_year 까지 활용" 은
  phase_name / justification 의 narrative 로 표현해야 함

---
[Language Rules — CRITICAL]
- `justification` 은 반드시 **한국어 2–3 문장** 으로 작성 (영문 혼용 금지,
  기술 고유명사/약어는 허용: EUV, ALD, GAA, HBM, TRL 등).
- `phase_name` 은 한국어 라벨 사용.

---
[Reasoning — 3 가지 분리 필수]

각 기술마다 **3가지 reasoning** 을 분리하여 작성 (보고서 시각화 + 추적성):

1. **year_placement**: 이 기술의 차년도 / 분기 배치 timing 이유.
   - 의존성 / TRL lead_time / 시장 boom 시점 / Strategic Direction 정합 등의 timing 측면
2. **tech_execution**: 이 기술을 **왜 수행해야 하는가** (Strategic Direction + 시장/기술 분석).
   - Strategic Direction 의 어떤 bullet 과 연결되는지 명시
3. **investment_selection**: 이 기술을 **왜 핵심 투자 대상**으로 선정했는가.
   - final_score / 시장 규모 / 회사 R&D 예산 대비 합리성

각 reasoning 은 2-3 문장. 단순 반복 X.

---
[CRITICAL] Output ONLY valid JSON. No markdown.

Output format:
{
  "roadmap": [
    {
      "tech_id": "T01",
      "phase_name": "1단계: 기반 R&D",
      "justification": "...",
      "reasoning": {
        "year_placement": "...",
        "tech_execution": "...",
        "investment_selection": "..."
      }
    }
  ]
}"""


def _extract_json(text: str) -> dict:
    cleaned = re.sub(r"```(?:json)?\s*", "", text).replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]+\}", cleaned)
        if match:
            return json.loads(match.group())
        raise ValueError(f"JSON 파싱 실패:\n{text[:300]}")


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
    """REVISE 시 전달된 feedback 을 roadmap_builder 프롬프트에 박을 섹션으로 포맷."""
    if not orchestrator_feedback:
        return ""
    items = orchestrator_feedback.get("text") or []
    items = [str(t).strip() for t in items if isinstance(t, str) and t.strip()]
    if not items:
        return ""
    bullet = "\n".join(f"- {t}" for t in items)
    return (
        "\n[ORCHESTRATOR REVISE FEEDBACK] — 직전 review 가 지적한 사항. "
        "각 기술의 justification 작성 시 반드시 언급하여 어떻게 대응했는지 명시하라.\n"
        f"{bullet}\n"
    )


def _check_market_feasibility(timeline_draft: list, market_boom_q: str) -> list:
    """
    각 기술의 완료 분기가 시장 개화 전인지 검증.
    늦는 기술에 대해 경고를 반환합니다.
    """
    boom_int = quarter_to_int(market_boom_q)
    warnings = []
    for item in timeline_draft:
        if item.get("dropped"):
            continue
        target_q = item.get("target_q", "")
        if target_q and quarter_to_int(target_q) > boom_int:
            warnings.append(
                f"⚠️  {item['tech_id']} ({item['name'][:25]}) — "
                f"완료 예정 {target_q} 이 시장 개화 {market_boom_q} 이후"
            )
    return warnings


# ── LangGraph 노드 ────────────────────────────────────────────

def run_roadmap_builder(state: RoadmapState) -> dict:
    """
    최종 로드맵 조립 노드.
    timeline_draft → planned_roadmap (justification 포함)
    """
    print("\n[Roadmap Builder] 시작")
    messages = []

    timeline_draft = state.get("timeline_draft", [])
    market_context = state.get("market_context", {})
    market_boom_q = market_context.get("expected_boom_quarter", "2028 Q1")

    if not timeline_draft:
        msg = "Roadmap Builder: timeline_draft 가 비어있습니다."
        return {"planned_roadmap": [], "messages": [AIMessage(content=msg)], "error": msg}

    try:
        # ① 시장 적시성 검증
        warnings = _check_market_feasibility(timeline_draft, market_boom_q)
        if warnings:
            print("\n[Roadmap Builder] 📋 시장 개화 전 완료 검증:")
            for w in warnings:
                print(f"  {w}")

        # ② LLM 에 justification 생성 요청 (Drop 된 기술 제외)
        llm = get_llm(max_tokens=4096)
        active_items = [t for t in timeline_draft if not t.get("dropped")]
        # phase_name 단조 증가 룰을 위해 시작 분기 오름차순으로 정렬해 전달
        active_items = sorted(active_items, key=lambda t: quarter_to_int(t.get("start_q", "9999 Q4")))

        feedback_block = _format_orchestrator_feedback(state.get("orchestrator_feedback"))
        upper_block = _format_upper_context(
            state.get("company_scenario"),
            state.get("strategic_direction"),
        )
        ref_year = state.get("reference_year")
        horizon_block = (
            f"\nreference_year: {ref_year} (로드맵 horizon 의 종료 연도 — phase_name / "
            f"justification 작성 시 이 시점까지의 활동을 고려)"
            if ref_year else ""
        )

        user_prompt = f"""{upper_block}
목표 시장     : {market_context.get('target_market', '')}
시장 개화 목표: {market_boom_q}{horizon_block}

아래는 역산 알고리즘으로 산출된 기술별 타임라인입니다 (시작 분기 오름차순 정렬).
각 항목에 대해 phase_name 과 justification 을 작성해주세요.

[타임라인 초안]
{json.dumps(active_items, ensure_ascii=False, indent=2)}

{f"[주의] 다음 기술들은 시장 개화 이후 완료 예정: {warnings}" if warnings else ""}
{feedback_block}"""

        print("[Roadmap Builder] LLM justification 생성 요청 중...")
        response = llm.invoke([
            SystemMessage(content=ROADMAP_BUILDER_SYSTEM_PROMPT),
            HumanMessage(content=user_prompt),
        ])

        result = _extract_json(response.content)
        justification_map = {
            r["tech_id"]: {
                "phase_name": r.get("phase_name", ""),
                "justification": r.get("justification", ""),
                "reasoning": r.get("reasoning", {}) or {},
            }
            for r in result.get("roadmap", [])
        }

        # 차년도 변환을 위해 horizon 시작 연도 추출
        ref_year = state.get("reference_year")
        company_scenario = state.get("company_scenario") or {}
        planning_horizon = company_scenario.get("planning_horizon", "") or ""
        m = re.search(r"(20\d{2}|21\d{2})", planning_horizon)
        if m:
            horizon_start_year = int(m.group(1))
        elif ref_year:
            horizon_start_year = ref_year - 4
        else:
            from datetime import datetime
            horizon_start_year = datetime.now().year

        def _q_to_year(q: str) -> int:
            mm = re.search(r"(20\d{2}|21\d{2})", q or "")
            return int(mm.group(1)) if mm else 0

        # ③ 최종 RoadmapItem 리스트 조립
        planned_roadmap: list[RoadmapItem] = []

        for item in timeline_draft:
            tid = item["tech_id"]
            jdata = justification_map.get(tid, {})

            # dropped 는 별도 표시하여 포함 (Investment Strategist 가 참고 가능)
            if item.get("dropped"):
                planned_roadmap.append({
                    "tech_id": tid,
                    "name": item.get("name", ""),
                    "phase_name": "DROPPED",
                    "start_q": "N/A",
                    "target_q": "N/A",
                    "year_idx_start": 0,
                    "year_idx_target": 0,
                    "prerequisites": [],
                    "lead_time_quarters": 0,
                    "justification": "오케스트레이터 피드백으로 로드맵에서 제외됨.",
                    "reasoning": {
                        "year_placement": "(dropped)",
                        "tech_execution": "(dropped)",
                        "investment_selection": "(dropped)",
                    },
                })
                continue

            # 차년도 도출
            start_q = item.get("start_q", "N/A")
            target_q = item.get("target_q", "N/A")
            sy, ty = _q_to_year(start_q), _q_to_year(target_q)
            yi_s = max(1, sy - horizon_start_year + 1) if sy else 1
            yi_t = max(yi_s, ty - horizon_start_year + 1) if ty else yi_s

            # reasoning: LLM 출력 우선, 누락 필드 fallback
            llm_reasoning = jdata.get("reasoning", {})
            just = jdata.get("justification", "") or ""
            reasoning = {
                "year_placement": llm_reasoning.get("year_placement") or just,
                "tech_execution": llm_reasoning.get("tech_execution") or "(reasoning 누락)",
                "investment_selection": llm_reasoning.get("investment_selection") or "(reasoning 누락)",
            }

            roadmap_item: RoadmapItem = {
                "tech_id": tid,
                "name": item.get("name", ""),
                "phase_name": (
                    jdata.get("phase_name")
                    or item.get("phase_name", "1단계: R&D")
                ),
                "start_q": start_q,
                "target_q": target_q,
                "year_idx_start": yi_s,
                "year_idx_target": yi_t,
                "prerequisites": item.get("prerequisites", []),
                "lead_time_quarters": item.get("lead_time_quarters", 0),
                "justification": (
                    just
                    + (f" [⚠️ 시장 개화({market_boom_q}) 이후 완료 예정]"
                       if any(tid in w for w in warnings) else "")
                ),
                "reasoning": reasoning,
            }
            planned_roadmap.append(roadmap_item)

        # ④ 최종 출력 요약
        print(f"\n[Roadmap Builder] ✅ 최종 로드맵 {len(planned_roadmap)}개 항목 완성")
        print("=" * 78)
        print(f"  {'ID':<5} {'기술명':<32} {'시작':<10} {'완료':<10} Phase")
        print("-" * 78)
        for r in sorted(planned_roadmap, key=lambda x: x.get("start_q", "9999")):
            print(
                f"  {r['tech_id']:<5} {r['name'][:30]:<32} "
                f"{r['start_q']:<10} {r['target_q']:<10} {r['phase_name']}"
            )
        print("=" * 78)

        warning_msg = f" (경고 {len(warnings)}건)" if warnings else ""
        messages.append(AIMessage(
            content=f"Roadmap Builder: 로드맵 {len(planned_roadmap)}개 완성{warning_msg}"
        ))

        return {
            "planned_roadmap": planned_roadmap,
            "messages": messages,
            "error": None,
        }

    except Exception as e:
        # 폴백: justification 없이 timeline_draft 그대로 변환
        err_msg = f"Roadmap Builder 오류: {e}. 기본 justification 사용."
        print(f"[Roadmap Builder] ⚠️  {err_msg}")

        fallback_roadmap = [
            {
                "tech_id": item["tech_id"],
                "name": item.get("name", ""),
                "phase_name": item.get("phase_name", "1단계: R&D"),
                "start_q": item.get("start_q", "N/A"),
                "target_q": item.get("target_q", "N/A"),
                "prerequisites": item.get("prerequisites", []),
                "lead_time_quarters": item.get("lead_time_quarters", 0),
                "justification": (
                    f"TRL {item.get('trl', '?')} 기준 "
                    f"{item.get('lead_time_quarters', 0)}분기 리드 타임 적용. "
                    f"시장 개화({market_boom_q}) 역산."
                ),
            }
            for item in timeline_draft if not item.get("dropped")
        ]
        return {
            "planned_roadmap": fallback_roadmap,
            "messages": [AIMessage(content=err_msg)],
            "error": None,
        }
