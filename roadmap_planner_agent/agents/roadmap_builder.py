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
[Language Rules — CRITICAL]
- `justification` 은 반드시 **한국어 2–3 문장** 으로 작성 (영문 혼용 금지,
  기술 고유명사/약어는 허용: EUV, ALD, GAA, HBM, TRL 등).
- `phase_name` 은 한국어 라벨 사용.

---
[CRITICAL] Output ONLY valid JSON. No markdown.

Output format:
{
  "roadmap": [
    {
      "tech_id": "T01",
      "phase_name": "1단계: 기반 R&D",
      "justification": "..."
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

        user_prompt = f"""
목표 시장     : {market_context.get('target_market', '')}
시장 개화 목표: {market_boom_q}

아래는 역산 알고리즘으로 산출된 기술별 타임라인입니다.
각 항목에 대해 phase_name 과 justification 을 작성해주세요.

[타임라인 초안]
{json.dumps(active_items, ensure_ascii=False, indent=2)}

{f"[주의] 다음 기술들은 시장 개화 이후 완료 예정: {warnings}" if warnings else ""}
"""

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
            }
            for r in result.get("roadmap", [])
        }

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
                    "prerequisites": [],
                    "lead_time_quarters": 0,
                    "justification": "오케스트레이터 피드백으로 로드맵에서 제외됨.",
                })
                continue

            roadmap_item: RoadmapItem = {
                "tech_id": tid,
                "name": item.get("name", ""),
                "phase_name": (
                    jdata.get("phase_name")
                    or item.get("phase_name", "1단계: R&D")
                ),
                "start_q": item.get("start_q", "N/A"),
                "target_q": item.get("target_q", "N/A"),
                "prerequisites": item.get("prerequisites", []),
                "lead_time_quarters": item.get("lead_time_quarters", 0),
                "justification": (
                    jdata.get("justification", "")
                    + (f" [⚠️ 시장 개화({market_boom_q}) 이후 완료 예정]"
                       if any(tid in w for w in warnings) else "")
                ),
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
