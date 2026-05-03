"""
agents/tech_selector.py
────────────────────────
Roadmap Planner Agent — Step 0 (Pre-step): 후보 기술 선별

역할:
  Agent 1 이 만든 후보 기술 N개를 받아 LLM 이 종합 평가하여 중요도가
  떨어지는 후보를 자율적으로 제외. 살아남은 K개만 dependency_analyzer 로 흘려보냄.

설계 원칙:
  - K 는 LLM 자율 (상한 없음)
  - 하한 보장: ROADMAP_TECH_K_MIN (기본 3)
      · LLM 이 K_MIN 미만 선택 → final_score 상위로 부족분 자동 보강
  - LLM 호출 실패 → 모든 후보 그대로 통과 (현재 동작 유지)
  - 선택 사유는 state["tech_selection"] 에 저장 → 최종 출력에 포함

판단 기준 (LLM 프롬프트로 전달):
  - final_score / market_score / patent_score
  - trl (성숙도)
  - expected_market_boom_quarter vs roadmap target
  - category 다양성 (Equipment / Process / Architecture 균형)
  - 중복성 (비슷한 기술 여러 개면 대표 1개만)
"""

import json
import os
import re
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage

from llm_factory import get_llm
from state import RoadmapState


SELECTOR_SYSTEM_PROMPT = """당신은 기술 로드맵의 후보 기술을 선별하는 Tech Selector Agent 이다.

[입력]
- 후보 기술 N개 (각 tech_id, name, category, trl, final_score, market_score, patent_score,
  expected_market_boom_quarter, rationale 일부)
- 목표 시장 컨텍스트 (target_market, expected_boom_quarter)

[큐레이션 원칙 — Agent 1 의 final_score 를 신뢰하되 단일 차원 의존 금지]

다음을 모두 만족하는 K개를 선별. 어느 한 기준이 압도적이지 않게.

1. **점수**: final_score 가 명백히 낮은 후보는 제외 후보.
2. **트렌드 정합**: market_signal / 시장 컨텍스트의 트렌드 키워드 (예: GAA, BSPDN, HBM,
   EUV, Low-k 등) 와 관련된 후보는 final_score 가 다소 낮아도 키워드당 최소 1개 보존.
3. **카테고리 균형**: Equipment / Material / Process / Architecture / Packaging 한쪽으로
   치우치지 않게 (시장 성격상 한 카테고리 핵심이면 비율 편중 OK).
4. **시점 분포**: expected_market_boom_quarter 가 한 시점에 몰리지 않게.
5. **중복 제거**: 비슷한 기능 후보 다수면 final_score 높은 대표 1개만.

평가를 다시 매기지 말 것 (final_score 가 정답). 위 5축 trade-off 만 큐레이션.

[출력 — strict JSON only, no prose, no markdown]
{
  "selected_tech_ids": ["T01","T03","T05",...],
  "dropped": [
    {"tech_id":"T02","reason":"<80자 이내 사유>"},
    ...
  ],
  "rationale": "<전체 선별 근거 200자 이내>"
}

[CRITICAL]
- selected_tech_ids 는 입력 후보의 tech_id 만 사용 — 새 ID 생성 금지.
- dropped 의 reason 은 80자 이내, 한국어 또는 입력 언어로 간결히.
- rationale 은 200자 이내, 전체 선별 정책을 1-2문장으로 요약."""


def _extract_json(text: str) -> dict:
    # Qwen3 thinking 잔여 블록 제거
    text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"```(?:json)?\s*", "", text).replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]+\}", cleaned)
        if m:
            return json.loads(m.group())
        raise ValueError(f"Selector JSON parse failed: {text[:200]}")


def _k_min() -> int:
    """ROADMAP_TECH_K_MIN 환경변수 (default 3)"""
    try:
        return max(1, int(os.getenv("ROADMAP_TECH_K_MIN", "3") or 3))
    except ValueError:
        return 3


def _format_orchestrator_feedback(orchestrator_feedback: dict) -> str:
    """REVISE 시 전달된 feedback 을 selector 프롬프트에 박을 섹션으로 포맷."""
    if not orchestrator_feedback:
        return ""
    items = orchestrator_feedback.get("text") or []
    items = [str(t).strip() for t in items if isinstance(t, str) and t.strip()]
    if not items:
        return ""
    bullet = "\n".join(f"- {t}" for t in items)
    return (
        "\n[ORCHESTRATOR REVISE FEEDBACK] — 직전 review 가 지적한 사항. "
        "선별 시 우선순위 / 제외 결정에 반영하라.\n"
        f"{bullet}\n"
    )


def _fill_to_min(
    selected_ids: list,
    tech_candidates: list,
    k_min: int,
) -> tuple:
    """
    selected_ids 가 K_MIN 미만이면 final_score 상위로 보강.
    반환: (보강된_selected_ids, 자동보강된_id_list)
    """
    selected_set = set(selected_ids)
    if len(selected_set) >= k_min:
        return list(selected_ids), []

    by_score = sorted(
        tech_candidates,
        key=lambda t: float(t.get("final_score", 0) or 0),
        reverse=True,
    )
    auto_added = []
    for t in by_score:
        if len(selected_set) >= k_min:
            break
        tid = t["tech_id"]
        if tid not in selected_set:
            selected_set.add(tid)
            selected_ids.append(tid)
            auto_added.append(tid)
    return list(selected_ids), auto_added


# ── LangGraph 노드 ────────────────────────────────────────────

def run_tech_selector(state: RoadmapState) -> dict:
    """
    후보 기술 선별 노드.
    state["tech_candidates"] 를 in-place 필터링 + state["tech_selection"] 산출.
    """
    print("\n[Tech Selector] 시작")
    tech_candidates = state.get("tech_candidates", []) or []
    if not tech_candidates:
        msg = "Tech Selector: tech_candidates 가 비어있어 스킵."
        print(f"[Tech Selector] ⚠️ {msg}")
        return {
            "tech_selection": {"selected_count": 0, "rationale": msg, "dropped": []},
            "messages": [AIMessage(content=msg)],
        }

    n_in = len(tech_candidates)
    k_min = _k_min()

    # 후보가 K_MIN 이하면 선별 의미 없음 — 모두 통과
    if n_in <= k_min:
        msg = f"Tech Selector: 후보 {n_in}개 ≤ K_MIN({k_min}) → 모두 통과"
        print(f"[Tech Selector] {msg}")
        return {
            "tech_selection": {
                "selected_count": n_in,
                "rationale": msg,
                "dropped": [],
            },
            "messages": [AIMessage(content=msg)],
        }

    market_ctx = state.get("market_context", {}) or {}
    feedback_block = _format_orchestrator_feedback(state.get("orchestrator_feedback"))

    # LLM 입력 — 핵심 필드만 (토큰 절약)
    def _slim(t: dict) -> dict:
        rationale = t.get("rationale", "") or ""
        if isinstance(rationale, str) and len(rationale) > 200:
            rationale = rationale[:200] + "…"
        return {
            "tech_id": t.get("tech_id"),
            "name": t.get("name", ""),
            "category": t.get("category", ""),
            "trl": t.get("trl", 0),
            "final_score": t.get("final_score", 0),
            "market_score": t.get("market_score", 0),
            "patent_score": t.get("patent_score", 0),
            "expected_market_boom_quarter": t.get("expected_market_boom_quarter", ""),
            "rationale": rationale,
        }

    user_prompt = f"""[후보 기술 {n_in}개]
{json.dumps([_slim(t) for t in tech_candidates], ensure_ascii=False, indent=2)}

[시장 컨텍스트]
- target_market: {market_ctx.get("target_market", "")}
- expected_boom_quarter: {market_ctx.get("expected_boom_quarter", "")}
{feedback_block}
위 후보 중 중요도가 떨어지는 기술을 자율적으로 제외하고 핵심만 남겨주세요.
최소 {k_min}개는 유지해야 합니다.
"""

    try:
        llm = get_llm(max_tokens=2048)
        print(f"[Tech Selector] LLM 선별 요청 중 (후보 {n_in}개, 최소 {k_min}개 유지)")
        response = llm.invoke([
            SystemMessage(content=SELECTOR_SYSTEM_PROMPT),
            HumanMessage(content=user_prompt),
        ])
        raw = response.content if hasattr(response, "content") else str(response)
        if not raw or not raw.strip():
            raise ValueError("LLM 빈 응답")
        result = _extract_json(raw)

        selected_ids = result.get("selected_tech_ids", []) or []
        # 입력에 없는 ID 거름
        valid_ids_set = {t["tech_id"] for t in tech_candidates}
        selected_ids = [tid for tid in selected_ids if tid in valid_ids_set]
        # 중복 제거 (순서 유지)
        seen = set()
        selected_ids = [tid for tid in selected_ids if not (tid in seen or seen.add(tid))]

        dropped_input = result.get("dropped", []) or []
        rationale_text = (result.get("rationale", "") or "").strip()[:300]

    except Exception as e:
        # LLM 호출 실패 → 모든 후보 통과 (안전 fallback)
        msg = f"Tech Selector: LLM 실패 ({e}) → 모든 후보 통과"
        print(f"[Tech Selector] ⚠️ {msg}")
        return {
            "tech_selection": {
                "selected_count": n_in,
                "rationale": msg,
                "dropped": [],
            },
            "messages": [AIMessage(content=msg)],
        }

    # 하한 보장 — K_MIN 미만이면 final_score 상위로 채움
    selected_ids, auto_added = _fill_to_min(selected_ids, tech_candidates, k_min)

    # tech_candidates 를 in-place 필터링
    selected_set = set(selected_ids)
    by_id = {t["tech_id"]: t for t in tech_candidates}
    filtered = [by_id[tid] for tid in selected_ids if tid in by_id]

    # dropped 목록 — LLM 결과 + 자동 누락분 보강
    name_by_id = {t["tech_id"]: t.get("name", "") for t in tech_candidates}
    final_dropped = []
    seen_dropped = set()
    for d in dropped_input:
        tid = d.get("tech_id") if isinstance(d, dict) else None
        if not tid or tid in selected_set or tid in seen_dropped:
            continue  # selected 인데 dropped 에도 있으면 selected 우선
        if tid not in valid_ids_set:
            continue
        seen_dropped.add(tid)
        reason = (d.get("reason", "") if isinstance(d, dict) else "")
        if isinstance(reason, str) and len(reason) > 80:
            reason = reason[:80] + "…"
        final_dropped.append({
            "tech_id": tid,
            "name": name_by_id.get(tid, ""),
            "reason": reason or "(LLM 사유 미제공)",
        })
    # selected 에도 dropped 에도 없는 후보 → "selected/dropped 미분류" 로 dropped 처리
    for tid in valid_ids_set:
        if tid in selected_set or tid in seen_dropped:
            continue
        final_dropped.append({
            "tech_id": tid,
            "name": name_by_id.get(tid, ""),
            "reason": "(selector 미분류 — 안전상 제외)",
        })

    if auto_added:
        rationale_text = (
            (rationale_text + " " if rationale_text else "")
            + f"[K_MIN={k_min} 보장 위해 자동 보강: {auto_added}]"
        )[:300]

    print(f"[Tech Selector] ✅ {n_in} → {len(filtered)}개 선별 (제외 {len(final_dropped)}개)")
    if filtered:
        print(f"  selected: {[t['tech_id'] for t in filtered]}")
    if final_dropped:
        print(f"  dropped : {[d['tech_id'] for d in final_dropped]}")

    return {
        "tech_candidates": filtered,
        "tech_selection": {
            "selected_count": len(filtered),
            "rationale": rationale_text or f"{n_in}개 후보 중 {len(filtered)}개 선별",
            "dropped": final_dropped,
        },
        "messages": [AIMessage(
            content=f"Tech Selector: {n_in} → {len(filtered)}개 선별 완료"
        )],
    }
