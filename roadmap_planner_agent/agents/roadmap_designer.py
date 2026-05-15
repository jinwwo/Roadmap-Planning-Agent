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
1. 후보 기술 목록 (tech_id, name, category, TRL, description, dependency_hints)
2. 시장 정보 (target_market, expected_boom_quarter)
3. Planning Horizon (예: 1차년도 ~ N차년도)
4. Company Scenario & Strategic Direction (상위 컨텍스트)
5. (선택) Orchestrator feedback

[수행 작업]
각 기술을 Planning Horizon **차년도 단위** (1차년도 ~ N차년도) 로 배치하라.
**분기 (Q1/Q2/Q3/Q4) 는 고려하지 않는다 — 연 단위만 결정.**

배치 규칙:
- **의존성 (가장 중요)**: dependency_hints / 카테고리 / TRL 로부터 선행-후행 관계를 추론.
  선행 기술의 종료 차년도 < 후행 기술 시작 차년도 (최소 동일 차년도 또는 다음 차년도 시작).
- **기술 정보**: TRL 이 낮을수록 더 많은 차년도 필요 (TRL 1-3: 3-5년차, 4-6: 2-3년차, 7-8: 1-2년차).
- **시장 정보**: expected_boom_quarter 는 양산/상업화 준비 **중간 milestone** 으로만 참고.
  종료점 아님 — boom 이후에도 차세대 기술 R&D 가 N차년도까지 이어진다.
- **Horizon 활용 (필수)**: 최소 1개 이상의 기술이 N차년도 (마지막 차년도) 까지 이어져야 한다.
- **단계 분류 (1단계/2단계 등) 만들지 말 것.** 자유롭게 dependency 기반으로 차년도 배치.

[Horizon 활용 체크리스트 — 출력 전 self-check]
1. min(year_idx_start) = 1 인가? (가장 빠른 기술이 1차년도 시작)
2. max(year_idx_target) = N (Planning Horizon 길이) 인가? 아니면 가장 적합한 기술을 N 까지 연장
3. year_idx_target 분포가 한 해에 80% 이상 몰려있지 않은가?

[출력 — strict JSON only, no prose, no markdown]
{
  "roadmap_timeline": [
    {
      "tech_id": "T01",
      "name": "기술명",
      "year_idx_start": 1,
      "year_idx_target": 2,
      "prerequisites": ["T03"],
      "reasoning": {
        "year_placement": "이 기술을 해당 차년도에 배치한 이유 (의존성/TRL/시장 시점 측면, 2-3 문장).",
        "tech_execution": "이 기술을 왜 수행해야 하는가 (Strategic Direction / 시장 기회 / 기술 차별화 측면, 2-3 문장).",
        "investment_selection": "이 기술을 왜 핵심 투자 대상으로 선정했는가 (시장 규모 / 경쟁 우위 / R&D 예산 합리성 측면, 2-3 문장)."
      }
    }
  ]
}

[필드 규칙]
- year_idx_start / year_idx_target: 정수 1~N (N = Planning Horizon 길이).
  · 예: 2026-2030 (5년) → 1=2026, 2=2027, ..., 5=2030.
- prerequisites: **반드시 입력 tech_candidates 안의 tech_id 만 사용**.
  · 입력에 없는 ID (Selector 가 제외했거나 가상의 기술) 를 prereq 로 쓰면 안 됨.
  · prereq 로 쓸 적합한 ID 가 없으면 빈 배열 `[]` 사용.
- reasoning 3개 필드 모두 한국어 2-3 문장. 각각 다른 관점에서 작성.
- 모든 기술마다 하나의 timeline object — 누락 금지.
- **분기 (Q1/Q2/Q3/Q4) 는 출력하지 마라** — 연도만 결정.

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


def _enforce_dependency_gap(roadmap_items: list, horizon_len: int) -> int:
    """후처리 — 차년도 기준 dependency 검증. prereq.year_idx_target <= dependent.year_idx_start.
    위반 시 dependent 를 push forward + cascade.

    Returns: 보정된 기술 수.
    """
    if not roadmap_items:
        return 0
    by_id = {r.get("tech_id"): r for r in roadmap_items if r.get("tech_id")}
    fixed_total = 0
    for _pass in range(5):
        pass_fixed = 0
        for r in roadmap_items:
            prereqs = r.get("prerequisites") or []
            if not prereqs:
                continue
            this_start = r.get("year_idx_start") or 0
            this_target = r.get("year_idx_target") or this_start
            if not this_start:
                continue
            # prereq 들 중 가장 늦은 year_idx_target
            latest_prereq_target = 0
            for pid in prereqs:
                p = by_id.get(pid)
                if not p:
                    continue
                pt = p.get("year_idx_target") or 0
                latest_prereq_target = max(latest_prereq_target, pt)
            if latest_prereq_target == 0:
                continue
            # 최소 시작 차년도 = 선행 완료 차년도와 동일 또는 그 다음
            required_start = latest_prereq_target
            if this_start >= required_start:
                continue
            delta = required_start - this_start
            new_start = this_start + delta
            new_target = max(this_target + delta, new_start)
            if new_target > horizon_len:
                new_target = horizon_len
            r["year_idx_start"] = new_start
            r["year_idx_target"] = new_target
            note = (f" [후처리: dependency 보정 — prereq {prereqs} 완료 후로 {delta}차년도 push]")
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
            # year_idx (LLM 직접 출력) — horizon 범위로 clamp
            yi_s_llm = item.get("year_idx_start") or 1
            yi_t_llm = item.get("year_idx_target") or yi_s_llm
            year_idx_start = max(1, min(int(yi_s_llm), horizon_len_target))
            year_idx_target = max(year_idx_start, min(int(yi_t_llm), horizon_len_target))

            # reasoning
            reasoning_raw = item.get("reasoning", {}) or {}
            reasoning = {
                "year_placement": reasoning_raw.get("year_placement") or "(reasoning 누락)",
                "tech_execution": reasoning_raw.get("tech_execution") or "(reasoning 누락)",
                "investment_selection": reasoning_raw.get("investment_selection") or "(reasoning 누락)",
            }

            # prerequisites: 입력 tech_candidates 에 실제 있는 ID 만 보존 (dangling 제거)
            raw_prereqs = item.get("prerequisites", []) or []
            valid_prereqs = [p for p in raw_prereqs if p in by_id and p != tid]
            dropped_prereqs = [p for p in raw_prereqs if p not in by_id]

            planned_roadmap.append({
                "tech_id": tid,
                "name": item.get("name") or src.get("name", ""),
                "year_idx_start": year_idx_start,
                "year_idx_target": year_idx_target,
                "prerequisites": valid_prereqs,
                "reasoning": reasoning,
                "dropped": False,
            })
            if dropped_prereqs:
                print(f"[Roadmap Designer] ⚠️ {tid} 의 dangling prereq 제거: {dropped_prereqs} "
                      f"(Selector 에서 dropped 됐거나 hallucinated)")

        # 누락된 tech_id 가 있으면 fallback
        for tid, src in by_id.items():
            if tid not in seen_ids:
                planned_roadmap.append({
                    "tech_id": tid,
                    "name": src.get("name", ""),
                    "year_idx_start": 1,
                    "year_idx_target": 1,
                    "prerequisites": [],
                    "reasoning": {
                        "year_placement": "(LLM 누락 — 임시 배치)",
                        "tech_execution": "(LLM 누락)",
                        "investment_selection": "(LLM 누락)",
                    },
                    "dropped": False,
                })

        # ── Dependency 안전망: year_idx 기준 prereq 완료 ≤ dependent 시작 ──
        dep_fixed = _enforce_dependency_gap(planned_roadmap, horizon_len_target)
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
                stretch_note = (
                    f" [후처리: horizon 안전망 — LLM 이 {current_max}차년도까지만 채워서 "
                    f"{horizon_len_target}차년도까지 연장]"
                )
                if isinstance(stretch_candidate.get("reasoning"), dict):
                    stretch_candidate["reasoning"]["year_placement"] = (
                        (stretch_candidate["reasoning"].get("year_placement") or "") + stretch_note
                    )
                print(f"[Roadmap Designer] ⚠️ horizon 안전망: {stretch_candidate['tech_id']} "
                      f"의 year_idx_target {old_yt} → {horizon_len_target} 연장 "
                      f"(LLM max={current_max} < horizon={horizon_len_target})")

        # dependency_tree 자동 생성 (downstream Strategist 가 활용)
        dependency_tree = _build_dependency_tree(planned_roadmap, tech_candidates)

        print(f"[Roadmap Designer] ✅ {len(planned_roadmap)}개 기술 timeline 산출 (horizon={horizon_len_target}년)")
        for r in sorted(planned_roadmap, key=lambda x: x.get("year_idx_start", 99)):
            print(f"  {r['tech_id']:5s} {r.get('name', '')[:30]:30s}  ({r['year_idx_start']}차년도 → {r['year_idx_target']}차년도)")

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
        # fallback — 모든 기술을 1차년도에 임시 배치
        planned_roadmap = []
        for t in tech_candidates:
            planned_roadmap.append({
                "tech_id": t["tech_id"],
                "name": t.get("name", ""),
                "year_idx_start": 1,
                "year_idx_target": 1,
                "prerequisites": [],
                "reasoning": {
                    "year_placement": f"(LLM 실패 fallback: {e})",
                    "tech_execution": "(LLM 실패 fallback)",
                    "investment_selection": "(LLM 실패 fallback)",
                },
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
