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

DESIGNER_SYSTEM_PROMPT = """You are a Roadmap Planner Agent (or Chief Technology Officer) responsible for translating fragmented technology analysis results into a structured, time-phased technology development timeline.
Your primary unit of judgment is the temporal placement and logical sequencing of technologies, not their financial viability.
Your task is NOT to evaluate budget, investment priority, or financial ROI. Instead, you must evaluate each technology's maturity (TRL) and dependencies to determine exactly WHEN it should be developed to meet the target market window.

You will be given:
1. technology analysis results (including tech_id, name, current TRL, description),
2. target market context (expected boom year/quarter),
3. orchestrator feedback (only if re-routing or shifting is required due to downstream constraints).

Your goal is to construct a logical, dependency-driven timeline using a backcasting approach, placing each technology into an appropriate developmental phase.
---
[Core Responsibilities]

For each technology candidate, determine:
- phase_name,
- start_quarter,
- target_completion_quarter,
- prerequisites,
- lead_time_estimation,
- justification.

You must first build a mental dependency tree (Hierarchy) of all given technologies, calculate the required lead time based on their current TRL, and then backcast from the target market date.
Do not consider budget constraints or resource limitations. Assume infinite resources; focus purely on technical execution time and logical order.
---
[definitions]

- Phase Name:
The temporal grouping of the roadmap stage (e.g., Short-term R&D, Mid-term Prototyping, Long-term Commercialization, or specific milestones like Phase 1: Foundation).

- Start Quarter & Target Completion Quarter:
The specific timeframe allocated for developing this technology (e.g., "2025 Q1", "2026 Q3").

- Prerequisites:
A list of tech_ids that must reach a functional level of completion BEFORE this technology can begin or scale.

- Lead Time Estimation:
The expected duration required to advance the technology from its current TRL to a commercial/deployable state (e.g., "6 quarters").

- Justification:
The logical rationale supporting the chosen timeline. Must explicitly mention TRL, dependencies, and alignment with the market target.
---
[Evaluation Principles]

When placing each technology on the timeline, consider the following dimensions:

1. Dependency Alignment (Hierarchy)
- Does this technology rely on foundational components (e.g., materials, equipment) being developed first?
- Base technologies must precede applied/system technologies.
- Hierarchy: [Material/Equipment] → [Unit Process] → [Packaging/Architecture]

2. TRL-based Lead Time
- Low TRL (1-3): Requires fundamental R&D. Lead time is inherently long (e.g., 6-8+ quarters).
- Mid TRL (4-6): Requires prototyping and integration. Lead time is moderate (e.g., 3-5 quarters).
- High TRL (7-8): Requires optimization and scaling. Lead time is short (e.g., 1-2 quarters).

3. Backcasting from Market Target
- Identify the ultimate target date (e.g., Market Boom in 2028 Q1).
- Work backward: Final integration must finish by 2027 Q4, which means subsystem X must finish by 2027 Q2, etc.

4. Logical Consistency (Zero-Slack Check)
- A technology cannot finish AFTER the target market window closes.
- A dependent technology cannot start BEFORE its prerequisites have made sufficient progress.
- 선행 기술의 target_q ≤ 후행 기술의 start_q - 1 (분기 차이 보장)

5. Horizon Distribution (reference_year 가 주어진 경우)
- 입력에 reference_year (예: 2030) 가 주어지면 그 시점이 로드맵 horizon 의 종료 연도.
- timeline 이 horizon 에 자연스럽게 분포되도록 단계별 stagger.
  · 단기 (TRL 7+, enabling tech) → 빠른 시작/완료 (예: 첫 1-2년)
  · 중기 (TRL 5-6) → 중간 구간
  · 장기 (TRL 3-4, R&D 단계) → reference_year 가까이까지 활용
- 모든 기술이 한 시점에 몰리지 않도록 — 각 단계가 horizon 에 골고루 펼쳐져야 함.
---
[Important Rules]

- Do not simply list the technologies; you must explicitly connect them in time.
- Ignore budget, cost, and investment tiers. Your only constraints are time and physics.
- If Orchestrator feedback is provided (e.g., "Shift T03 to 2026"), you must shift T03 AND cascade the delay to all technologies that depend on T03.
- Be explicit about why a specific quarter was chosen in the justification.
- Keep the output structured, concise, and consistent across all roadmap items.
---
[Default Assumptions]

If target context is not provided, assume the following:
- target_market_boom: 3 years from the current date.
- base_lead_time_speed: Advancing 1 TRL takes exactly 1 quarter.
- granularity: Timelines must be mapped at the Quarter (Q1, Q2, Q3, Q4) level.
---
[Language]

- justification 은 반드시 **한국어 2-3 문장** 으로 작성 (영문 혼용 금지, 기술 약어 EUV/ALD/GAA/HBM/TRL 등은 허용).
- phase_name 은 한국어 라벨 (예: "1단계: 기반 R&D", "2단계: 공정 통합", "3단계: 시스템 검증", "4단계: 양산 전환").

---
[Output Constraints]

- Provide exactly one timeline object per technology candidate in a roadmap_timeline array.
- Format dates strictly as "YYYY QX" (e.g., 2025 Q1).
- prerequisites must be an array of strings containing ONLY valid tech_ids provided in the input (or an empty array [] if none).
- justification must contain 2 to 3 concise sentences in Korean.
- lead_time_quarters must be an integer (number of quarters).
- Output must be in valid JSON format only.

[Output Schema]
{
  "roadmap_timeline": [
    {
      "tech_id": "T01",
      "name": "...",
      "phase_name": "1단계: 기반 R&D",
      "start_q": "2025 Q1",
      "target_q": "2026 Q2",
      "prerequisites": [],
      "lead_time_quarters": 6,
      "justification": "..."
    }
  ]
}
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
    horizon_block = (
        f"\n- reference_year (horizon 종료 연도): {reference_year}"
        if reference_year else ""
    )

    user_prompt = f"""[market_context]
- target_market: {target_market}
- expected_boom_quarter: {market_boom_q}{horizon_block}

[tech_candidates ({len(tech_candidates)})]
{json.dumps([_slim(t) for t in tech_candidates], ensure_ascii=False, indent=2)}
{feedback_block}
위 후보들의 의존성 / TRL / 시장 시점 / horizon 을 종합하여 spec 의 Output Schema 에 따라
roadmap_timeline 을 작성하라.
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

        # 출력 schema 정규화 — RoadmapItem 형식으로
        by_id = {t["tech_id"]: t for t in tech_candidates}
        planned_roadmap = []
        seen_ids = set()
        for item in roadmap_items:
            tid = item.get("tech_id")
            if not tid or tid in seen_ids or tid not in by_id:
                continue
            seen_ids.add(tid)
            src = by_id[tid]
            planned_roadmap.append({
                "tech_id": tid,
                "name": item.get("name") or src.get("name", ""),
                "phase_name": item.get("phase_name", ""),
                "start_q": item.get("start_q", ""),
                "target_q": item.get("target_q", ""),
                "prerequisites": list(item.get("prerequisites", []) or []),
                "lead_time_quarters": int(item.get("lead_time_quarters", 0) or 0),
                "justification": item.get("justification", ""),
                "dropped": False,
            })

        # 누락된 tech_id 가 있으면 fallback (LLM 누락 방지)
        for tid, src in by_id.items():
            if tid not in seen_ids:
                planned_roadmap.append({
                    "tech_id": tid,
                    "name": src.get("name", ""),
                    "phase_name": "(LLM 누락 — 보조 항목)",
                    "start_q": market_boom_q,
                    "target_q": market_boom_q,
                    "prerequisites": [],
                    "lead_time_quarters": 0,
                    "justification": "(Roadmap Designer LLM 응답에서 누락 → 시장 boom 시점에 임시 배치)",
                    "dropped": False,
                })

        # dependency_tree 자동 생성 (downstream Strategist 가 활용)
        dependency_tree = _build_dependency_tree(planned_roadmap, tech_candidates)

        print(f"[Roadmap Designer] ✅ {len(planned_roadmap)}개 기술 timeline 산출")
        for r in sorted(planned_roadmap, key=lambda x: x.get("start_q", "9999 Q4")):
            print(f"  {r['tech_id']:5s} {r['phase_name']:30s} {r['start_q']} → {r['target_q']}")

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
