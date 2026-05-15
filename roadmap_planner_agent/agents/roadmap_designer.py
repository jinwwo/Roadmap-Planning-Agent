"""
agents/roadmap_designer.py
───────────────────────────
Roadmap Planner Agent — Holistic LLM 통합 노드 (spec 그대로)

이 노드는 dependency_analyzer + timeline_calculator + roadmap_builder 의 책임을 통합하여
**LLM 한 번의 호출** 로 다음을 모두 산출:
  - dependency tree (mental model)
  - TRL-based lead time
  - Backcasting from market boom_quarter
  - phase_name + start_q + target_q + prerequisites + justification
  - Zero-slack 검증 (LLM 자체 책임)

spec 의 System Prompt 를 그대로 박음. 결정성은 약간 낮지만, 알고리즘적 분배가 만드는
부자연스러운 분포 (sparse chain → 한 시점 몰림) 문제 해결.
"""

import json
import re
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage

from llm_factory import get_llm
from state import RoadmapState


# ── Spec 의 System Prompt (그대로) ────────────────────────────

DESIGNER_SYSTEM_PROMPT = """당신은 기술 로드맵을 작성하는 Roadmap Planner Agent 이다.

[입력]
1. 후보 기술 목록 (tech_id, name, category, TRL, description, expected_market_boom_quarter, dependency_hints)
2. 시장 정보 (target_market, expected_boom_quarter)
3. Planning Horizon / reference_year (예: 2026~2030)
4. Company Scenario & Strategic Direction (상위 컨텍스트)
5. (선택) Orchestrator feedback

[수행 작업]
각 기술을 Planning Horizon (1차년도 ~ N차년도) 안에 배치한 timeline 을 작성하라.

배치 규칙:
- **의존성 (가장 중요)**: 후보들의 dependency_hints / 카테고리 / TRL 로부터 선행-후행 관계를 추론.
  선행 기술의 종료가 후행 기술 시작보다 빠르도록 배치 (분기 단위 gap).
- **기술 정보**: TRL 이 낮을수록 lead time 이 길다 (TRL 1-3: 장기, 4-6: 중기, 7-8: 단기).
- **시장 정보**: expected_market_boom_quarter 는 양산/상업화 준비 **중간 milestone**.
  종료점 아님 — boom 이후에도 차세대 기술 R&D 가 reference_year 까지 이어진다.
- **Horizon 활용 (필수)**: timeline 의 종료 연도 = **reference_year** (예: 2030).
  · expected_boom_quarter 가 2029 Q2 라고 해서 모든 기술을 2029 안에 끝내지 마라.
  · **반드시 1개 이상의 기술이 N차년도 (마지막 차년도) 까지 이어져야 한다.**
    예: planning_horizon=2026-2030 (5 years) → 최소 1개 기술의 year_idx_target = 5.
  · 양산 준비 기술은 boom_q 까지, 차세대/장기 R&D 는 reference_year 까지 stagger.
- **단계 분류 (1단계/2단계 등) 같은 시간 카테고리는 만들지 마라.** 자유롭게 dependency 기반으로
  배치하고, 단순히 "이 기술이 언제 시작해서 언제 끝나는가" 만 결정.

[Horizon 활용 체크리스트 — 출력 전 self-check]
1. min(year_idx_start) = 1 인가? (가장 빠른 기술이 1차년도 시작)
2. max(year_idx_target) = N (= Planning Horizon 길이)? 그렇지 않으면 가장 적합한 기술을 N 까지 연장
3. year_idx_target 분포가 한 해에 80% 이상 몰려있지 않은가?
4. 양산 deadline (expected_boom_quarter 의 차년도) 이후에도 차세대 R&D 기술이 배치돼 있는가?

[출력 — strict JSON only, no prose, no markdown]
{
  "roadmap_timeline": [
    {
      "tech_id": "T01",
      "name": "기술명",
      "start_q": "2026 Q1",
      "target_q": "2027 Q2",
      "year_idx_start": 1,
      "year_idx_target": 2,
      "prerequisites": ["T03"],
      "lead_time_quarters": 6,
      "reasoning": {
        "year_placement": "이 기술을 해당 시점에 배치한 이유 (의존성/TRL/시장 시점 측면, 2-3 문장).",
        "tech_execution": "이 기술을 왜 수행해야 하는가 (Strategic Direction / 시장 기회 / 기술 차별화 측면, 2-3 문장).",
        "investment_selection": "이 기술을 왜 핵심 투자 대상으로 선정했는가 (시장 규모 / 경쟁 우위 / R&D 예산 합리성 측면, 2-3 문장)."
      }
    }
  ]
}

[필드 규칙]
- start_q / target_q: "YYYY QX" 형식 (예: "2026 Q1", "2028 Q3").
- year_idx_start / year_idx_target: Planning Horizon 시작 연도를 1차년도로 환산한 정수.
  · 예: planning_horizon=2026-2030 → "2026 Q1" = 1차년도, "2030 Q4" = 5차년도.
- prerequisites: 입력 tech_id 만 사용. 새 ID 생성 금지.
- lead_time_quarters: 정수 (분기 단위).
- reasoning 3개 필드 모두 한국어 2-3 문장. 각각 다른 관점에서 작성.
- 모든 기술마다 하나의 timeline object — 누락 금지.

[CRITICAL]
- Output strict JSON only. 코드펜스 금지, 프롬프트 외 텍스트 금지.
- 후보 기술 전부 포함 (드롭 금지).
"""


def _extract_json(text: str) -> dict:
    text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"```(?:json)?\s*", "", text).replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]+\}", cleaned)
        if m:
            return json.loads(m.group())
        raise ValueError(f"Roadmap Designer JSON parse failed:\n{text[:300]}")


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
    """REVISE 시 전달된 feedback 을 designer 프롬프트에 박을 섹션으로 포맷."""
    if not orchestrator_feedback:
        return ""
    items = orchestrator_feedback.get("text") or []
    items = [str(t).strip() for t in items if isinstance(t, str) and t.strip()]
    shift_items = orchestrator_feedback.get("shift") or []
    drop_items = orchestrator_feedback.get("drop") or []

    block = "\n[ORCHESTRATOR FEEDBACK] — 직전 review 의 지적사항. 반드시 반영하라.\n"
    if items:
        block += "\n[Text feedback]\n" + "\n".join(f"- {t}" for t in items)
    if shift_items:
        block += "\n[Shift commands] (특정 기술 시작 분기 강제 변경)\n"
        for s in shift_items:
            block += f"- {s.get('tech_id')} → {s.get('new_start_q')}\n"
    if drop_items:
        block += "\n[Drop commands] (이 기술 ID 들은 로드맵에서 제외)\n"
        block += ", ".join(drop_items) + "\n"
    return block + "\n"


def _parse_horizon_start_year(planning_horizon: str, reference_year: int = None) -> int:
    """planning_horizon (예: "2026-2030", "2025 ~ 2030", "2026-2030 (5 years)") 에서
    시작 연도 추출. 실패 시 reference_year - 4 fallback (5년 horizon 가정)."""
    if planning_horizon:
        m = re.search(r"(20\d{2}|21\d{2})", planning_horizon)
        if m:
            return int(m.group(1))
    if reference_year:
        return reference_year - 4
    from datetime import datetime
    return datetime.now().year


def _quarter_to_year(q: str) -> int:
    """'2026 Q1' 등에서 연도(int)만 추출. 실패 시 0."""
    if not q:
        return 0
    m = re.search(r"(20\d{2}|21\d{2})", q)
    return int(m.group(1)) if m else 0


def _derive_year_idx(start_q: str, target_q: str, horizon_start_year: int) -> tuple:
    """분기 → 차년도 (1, 2, 3, ...) 변환. horizon_start_year=2026 이면 2026→1, 2027→2."""
    sy = _quarter_to_year(start_q)
    ty = _quarter_to_year(target_q)
    yi_s = max(1, sy - horizon_start_year + 1) if sy else 1
    yi_t = max(yi_s, ty - horizon_start_year + 1) if ty else yi_s
    return yi_s, yi_t


def _q_to_int(q: str) -> int:
    """'2026 Q3' → 2026*4+3=8107. 파싱 실패 시 0."""
    m = re.match(r"\s*(20\d{2}|21\d{2})\s*Q([1-4])\s*", q or "")
    if not m:
        return 0
    return int(m.group(1)) * 4 + int(m.group(2))


def _int_to_q(n: int) -> str:
    """역변환."""
    q = n % 4
    y = n // 4
    if q == 0:
        q = 4; y -= 1
    return f"{y} Q{q}"


def _q_int_to_year(n: int) -> int:
    """quarter int → 실제 연도. Q4 의 경우 // 4 가 +1 오프셋 되는 문제를 보정.
    예: 8124 (2030 Q4) → 2030 (// 4 만 하면 2031).
    """
    q = n % 4
    y = n // 4
    if q == 0:
        y -= 1
    return y


def _enforce_dependency_gap(roadmap_items: list, horizon_start_year: int, reference_year: int) -> int:
    """후처리 — prereq target_q < dependent start_q (최소 1분기 gap) 보장.
    위반 시 dependent 를 push forward 하고 cascade.

    Returns: 보정된 기술 수.
    """
    if not roadmap_items:
        return 0
    by_id = {r.get("tech_id"): r for r in roadmap_items if r.get("tech_id")}
    # 보정 — 여러 pass 까지 안정될 때까지 반복 (cyclic 방지 max 5 pass)
    fixed_total = 0
    for _pass in range(5):
        pass_fixed = 0
        for r in roadmap_items:
            prereqs = r.get("prerequisites") or []
            if not prereqs:
                continue
            this_start = _q_to_int(r.get("start_q", ""))
            this_target = _q_to_int(r.get("target_q", ""))
            if not this_start:
                continue
            # prereq 들 중 가장 늦은 target_q + 1 분기가 최소 start
            latest_prereq_target = 0
            for pid in prereqs:
                p = by_id.get(pid)
                if not p:
                    continue
                pt = _q_to_int(p.get("target_q", ""))
                latest_prereq_target = max(latest_prereq_target, pt)
            if latest_prereq_target == 0:
                continue
            required_start = latest_prereq_target + 1
            if this_start >= required_start:
                continue
            # 위반 — push forward
            delta = required_start - this_start
            new_start = this_start + delta
            new_target = max(this_target + delta, new_start)
            # reference_year 초과 방지
            ref_max = (reference_year or 2030) * 4 + 4
            if new_target > ref_max:
                new_target = ref_max
            r["start_q"] = _int_to_q(new_start)
            r["target_q"] = _int_to_q(new_target)
            # year_idx 재계산
            r["year_idx_start"] = max(1, _q_int_to_year(new_start) - horizon_start_year + 1)
            r["year_idx_target"] = max(r["year_idx_start"], _q_int_to_year(new_target) - horizon_start_year + 1)
            note = (f" [후처리: dependency 보정 — prereq {prereqs} target={_int_to_q(latest_prereq_target)} "
                    f"이후로 {delta}분기 push]")
            r["justification"] = (r.get("justification") or "") + note
            if isinstance(r.get("reasoning"), dict):
                r["reasoning"]["year_placement"] = (r["reasoning"].get("year_placement") or "") + note
            pass_fixed += 1
        fixed_total += pass_fixed
        if pass_fixed == 0:
            break
    return fixed_total


def _build_dependency_tree(roadmap_items: list, tech_candidates: list) -> dict:
    """LLM 출력의 prerequisites 로부터 dependency_tree 자동 구성.
    Investment Strategist 등 downstream 이 dependency_tree 를 참조할 수 있도록.
    """
    from config import CATEGORY_LAYER

    by_id = {t["tech_id"]: t for t in tech_candidates}
    tree = {}
    for item in roadmap_items:
        tid = item.get("tech_id")
        if not tid:
            continue
        src = by_id.get(tid, {})
        tree[tid] = {
            "tech_id": tid,
            "name": item.get("name") or src.get("name", ""),
            "category": src.get("category", "Process"),
            "trl": src.get("trl", 3),
            "prerequisites": list(item.get("prerequisites", []) or []),
            "dependents": [],
            "layer": CATEGORY_LAYER.get(src.get("category", "Process"), 1),
            "expected_market_boom_quarter": src.get("expected_market_boom_quarter", ""),
        }

    # 양방향 정합 — prereq 에 등장한 ID 를 보고 dependents 채움
    for tid, node in tree.items():
        for prereq_id in node["prerequisites"]:
            if prereq_id in tree:
                deps = tree[prereq_id].setdefault("dependents", [])
                if tid not in deps:
                    deps.append(tid)

    return tree


# ── LangGraph 노드 ────────────────────────────────────────────

def run_roadmap_designer(state: RoadmapState) -> dict:
    """
    Holistic LLM 통합 노드 — spec 의 System Prompt 그대로 적용.

    출력:
      - planned_roadmap (List[RoadmapItem])
      - dependency_tree (prerequisites 로부터 자동 생성)
      - timeline_draft (planned_roadmap 과 동일 — 호환성용)
    """
    print("\n[Roadmap Designer] 시작 (spec holistic mode)")
    messages = []

    tech_candidates = state.get("tech_candidates", []) or []
    if not tech_candidates:
        msg = "Roadmap Designer: tech_candidates 가 비어있습니다."
        return {"planned_roadmap": [], "messages": [AIMessage(content=msg)], "error": msg}

    market_context = state.get("market_context", {}) or {}
    market_boom_q = market_context.get("expected_boom_quarter", "2028 Q1")
    target_market = market_context.get("target_market", "")
    reference_year = state.get("reference_year")
    orchestrator_feedback = state.get("orchestrator_feedback")

    # LLM 입력 준비 — 핵심 필드만 (토큰 절약)
    def _slim(t: dict) -> dict:
        rationale = t.get("rationale", "") or ""
        if isinstance(rationale, str) and len(rationale) > 240:
            rationale = rationale[:240] + "…"
        return {
            "tech_id": t.get("tech_id"),
            "name": t.get("name", ""),
            "category": t.get("category", ""),
            "trl": t.get("trl", 0),
            "expected_market_boom_quarter": t.get("expected_market_boom_quarter", ""),
            "dependency_hints": t.get("dependency_hints", []) or [],
            "description": rationale,
        }

    feedback_block = _format_orchestrator_feedback(orchestrator_feedback)
    company_scenario = state.get("company_scenario") or {}
    upper_block = _format_upper_context(
        company_scenario,
        state.get("strategic_direction"),
    )
    planning_horizon_str = company_scenario.get("planning_horizon", "")
    horizon_start_year = _parse_horizon_start_year(planning_horizon_str, reference_year)
    horizon_len = (reference_year - horizon_start_year + 1) if reference_year else 5
    horizon_block = (
        f"\n- Planning Horizon: {planning_horizon_str or f'{horizon_start_year}-{reference_year}'}"
        f"\n- horizon 시작 연도: {horizon_start_year} (=1차년도)"
        f"\n- horizon 종료 연도: {reference_year} (=last/{horizon_len}차년도)"
        if reference_year else ""
    )

    user_prompt = f"""{upper_block}[market_context]
- target_market: {target_market}
- expected_boom_quarter (중간 milestone, 종료점 아님): {market_boom_q}{horizon_block}

[tech_candidates ({len(tech_candidates)})]
{json.dumps([_slim(t) for t in tech_candidates], ensure_ascii=False, indent=2)}
{feedback_block}
[작업 지시]
위 정보를 바탕으로 각 기술을 Planning Horizon **1차년도~{horizon_len}차년도** 에 배치한 roadmap_timeline 을 작성하라.

[필수 제약 — 출력 전 반드시 확인]
1. **max(year_idx_target) = {horizon_len}** — 최소 1개 기술의 year_idx_target 이 마지막 차년도({horizon_len}차년도, ={reference_year}년) 와 같아야 한다.
2. 모든 기술이 boom_quarter ({market_boom_q}) 안에 끝나면 안 됨. 차세대 R&D / 장기 진화 기술 1개 이상은 {reference_year} 까지 이어진다.
3. 종속성 (dependency_hints / 카테고리 / TRL) 을 가장 우선시.
4. 단계 구분 (1단계/2단계 등) 은 만들지 말고, 자유롭게 dependency 기반으로 배치.
"""

    try:
        # max_tokens 8192 — 후보 8-10개 × 분석 결과 안전 여유
        llm = get_llm(max_tokens=8192)
        print(f"[Roadmap Designer] LLM 호출 (후보 {len(tech_candidates)}개, boom={market_boom_q}, ref_year={reference_year})")
        response = llm.invoke([
            SystemMessage(content=DESIGNER_SYSTEM_PROMPT),
            HumanMessage(content=user_prompt),
        ])
        raw = response.content if hasattr(response, "content") else str(response)
        if not raw or not raw.strip():
            raise ValueError("LLM 빈 응답")

        result = _extract_json(raw)
        roadmap_items = result.get("roadmap_timeline", []) or []

        # planning_horizon 시작 연도 추출 — 차년도 변환의 기준
        planning_horizon = (
            (company_scenario or {}).get("planning_horizon", "")
            or market_context.get("planning_horizon", "")
            or ""
        )
        horizon_start_year = _parse_horizon_start_year(planning_horizon, reference_year)
        horizon_len_target = (reference_year - horizon_start_year + 1) if reference_year else 5

        # 출력 schema 정규화 — RoadmapItem 형식으로 + year_idx + reasoning 자동 보강
        by_id = {t["tech_id"]: t for t in tech_candidates}
        planned_roadmap = []
        seen_ids = set()
        for item in roadmap_items:
            tid = item.get("tech_id")
            if not tid or tid in seen_ids or tid not in by_id:
                continue
            seen_ids.add(tid)
            src = by_id[tid]
            start_q = item.get("start_q", "")
            target_q = item.get("target_q", "")
            # year_idx: LLM 출력 우선, 없으면 분기에서 자동 도출
            yi_s_llm = item.get("year_idx_start")
            yi_t_llm = item.get("year_idx_target")
            if isinstance(yi_s_llm, int) and isinstance(yi_t_llm, int) and yi_s_llm > 0:
                year_idx_start = max(1, min(yi_s_llm, horizon_len_target))
                year_idx_target = max(year_idx_start, min(yi_t_llm, horizon_len_target))
            else:
                year_idx_start, year_idx_target = _derive_year_idx(start_q, target_q, horizon_start_year)

            # reasoning: LLM 출력 우선, 누락 필드는 justification 으로 채움
            reasoning_raw = item.get("reasoning", {}) or {}
            just = item.get("justification", "") or ""
            reasoning = {
                "year_placement": reasoning_raw.get("year_placement") or just,
                "tech_execution": reasoning_raw.get("tech_execution") or "(reasoning 누락)",
                "investment_selection": reasoning_raw.get("investment_selection") or "(reasoning 누락)",
            }
            # justification 이 비어있으면 reasoning.year_placement 로 채움 (호환)
            if not just:
                just = reasoning["year_placement"]

            planned_roadmap.append({
                "tech_id": tid,
                "name": item.get("name") or src.get("name", ""),
                "phase_name": item.get("phase_name", ""),
                "start_q": start_q,
                "target_q": target_q,
                "year_idx_start": year_idx_start,
                "year_idx_target": year_idx_target,
                "prerequisites": list(item.get("prerequisites", []) or []),
                "lead_time_quarters": int(item.get("lead_time_quarters", 0) or 0),
                "justification": just,
                "reasoning": reasoning,
                "dropped": False,
            })

        # 누락된 tech_id 가 있으면 fallback (LLM 누락 방지)
        for tid, src in by_id.items():
            if tid not in seen_ids:
                planned_roadmap.append({
                    "tech_id": tid,
                    "name": src.get("name", ""),
                    "phase_name": "",
                    "start_q": market_boom_q,
                    "target_q": market_boom_q,
                    "year_idx_start": 1,
                    "year_idx_target": 1,
                    "prerequisites": [],
                    "lead_time_quarters": 0,
                    "justification": "(Roadmap Designer LLM 응답에서 누락 → 시장 boom 시점에 임시 배치)",
                    "reasoning": {
                        "year_placement": "(LLM 누락 — 임시 배치)",
                        "tech_execution": "(LLM 누락)",
                        "investment_selection": "(LLM 누락)",
                    },
                    "dropped": False,
                })

        # ── Dependency 안전망: prereq target < dependent start 위반 자동 보정 ──
        dep_fixed = _enforce_dependency_gap(planned_roadmap, horizon_start_year, reference_year)
        if dep_fixed:
            print(f"[Roadmap Designer] ⚠️ dependency 안전망: {dep_fixed}건 자동 보정 (prereq target → dependent start gap 강제)")

        # ── Horizon 안전망: max(year_idx_target) < horizon_len 이면 가장 후행 기술을 연장 ──
        if reference_year:
            horizon_len_target = reference_year - horizon_start_year + 1
            current_max = max((r.get("year_idx_target", 0) for r in planned_roadmap), default=0)
            if current_max < horizon_len_target and planned_roadmap:
                # 후보: TRL 낮은 + final_score 높은 기술 (장기 R&D 후보)
                def _stretch_score(r):
                    src = by_id.get(r.get("tech_id"), {})
                    trl = src.get("trl", 5) or 5
                    fscore = src.get("final_score", 50) or 50
                    return (-trl, fscore)  # 낮은 TRL 우선, 그 다음 high score
                stretch_candidate = sorted(planned_roadmap, key=_stretch_score)[0]
                old_yt = stretch_candidate.get("year_idx_target", 1)
                stretch_candidate["year_idx_target"] = horizon_len_target
                stretch_candidate["target_q"] = f"{reference_year} Q4"
                stretch_note = (
                    f" [후처리: horizon 안전망 — LLM 이 {current_max}차년도까지만 채워서 "
                    f"{horizon_len_target}차년도까지 연장]"
                )
                stretch_candidate["justification"] = (stretch_candidate.get("justification") or "") + stretch_note
                if isinstance(stretch_candidate.get("reasoning"), dict):
                    stretch_candidate["reasoning"]["year_placement"] = (
                        (stretch_candidate["reasoning"].get("year_placement") or "") + stretch_note
                    )
                print(f"[Roadmap Designer] ⚠️ horizon 안전망: {stretch_candidate['tech_id']} "
                      f"의 year_idx_target {old_yt} → {horizon_len_target} 연장 "
                      f"(LLM max={current_max} < horizon={horizon_len_target})")

        # dependency_tree 자동 생성 (downstream Strategist 가 활용)
        dependency_tree = _build_dependency_tree(planned_roadmap, tech_candidates)

        print(f"[Roadmap Designer] ✅ {len(planned_roadmap)}개 기술 timeline 산출 (horizon 시작={horizon_start_year})")
        for r in sorted(planned_roadmap, key=lambda x: (x.get("year_idx_start", 99), x.get("start_q", "9999 Q4"))):
            print(f"  {r['tech_id']:5s} {r['phase_name']:30s} {r['start_q']} → {r['target_q']}  ({r['year_idx_start']}차년도 → {r['year_idx_target']}차년도)")

        messages.append(AIMessage(
            content=f"Roadmap Designer: {len(planned_roadmap)}개 기술 통합 timeline 완성"
        ))

        return {
            "planned_roadmap": planned_roadmap,
            "dependency_tree": dependency_tree,
            "timeline_draft": planned_roadmap,   # 호환성용
            "messages": messages,
            "error": None,
        }

    except Exception as e:
        err_msg = f"Roadmap Designer 오류: {e}"
        print(f"[Roadmap Designer] ❌ {err_msg}")
        # fallback — 시장 boom 시점에 모든 기술 배치
        planned_roadmap = []
        for t in tech_candidates:
            planned_roadmap.append({
                "tech_id": t["tech_id"],
                "name": t.get("name", ""),
                "phase_name": "1단계: fallback",
                "year_idx_start": 1,
                "year_idx_target": 1,
                "reasoning": {
                    "year_placement": "(LLM 실패 fallback)",
                    "tech_execution": "(LLM 실패 fallback)",
                    "investment_selection": "(LLM 실패 fallback)",
                },
                "start_q": market_boom_q,
                "target_q": market_boom_q,
                "prerequisites": [],
                "lead_time_quarters": 0,
                "justification": f"(LLM 실패: {e} — fallback 배치)",
                "dropped": False,
            })
        dependency_tree = _build_dependency_tree(planned_roadmap, tech_candidates)
        return {
            "planned_roadmap": planned_roadmap,
            "dependency_tree": dependency_tree,
            "timeline_draft": planned_roadmap,
            "messages": [AIMessage(content=err_msg)],
            "error": None,   # fallback 성공이므로 에러 아님
        }
