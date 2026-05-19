"""
agents/patent_agent.py
───────────────────────
Patent Data Agent 노드

역할:
1. USPTO PatentsView API 로 원시 특허 데이터 수집
2. Claude 에게 분석 요청 → 구조화된 patent_analysis JSON 반환
3. AnalysisState 에 patent_raw_data / patent_analysis 저장
"""

import json
import re
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage

from llm_factory import get_llm
from config import PATENT_ANALYSIS_METHOD
from prompt_loader import load_prompt
from state import AnalysisState
from tools.patent_tools import USPTOPatentTool


# ── 헬퍼 함수 ─────────────────────────────────────────────────

def _extract_json(text: str) -> dict:
    """Claude 응답에서 JSON 블록 추출"""
    # 마크다운 코드블록 제거
    cleaned = re.sub(r"```(?:json)?\s*", "", text).replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # JSON 블록만 추출 시도
        match = re.search(r"\{[\s\S]+\}", cleaned)
        if match:
            return json.loads(match.group())
        raise ValueError(f"유효한 JSON을 파싱할 수 없습니다:\n{text[:300]}")


def _format_orchestrator_feedback(orchestrator_feedback: dict) -> str:
    """REVISE 시 전달된 feedback 을 patent_agent 프롬프트에 박을 섹션으로 포맷.
    Agent 1 은 후보 자체를 도출하므로, 누락된 기술/카테고리 보강 지시로 작동."""
    if not orchestrator_feedback:
        return ""
    items = orchestrator_feedback.get("text") or []
    items = [str(t).strip() for t in items if isinstance(t, str) and t.strip()]
    if not items:
        return ""
    bullet = "\n".join(f"- {t}" for t in items)
    return (
        "\n\n[ORCHESTRATOR REVISE FEEDBACK] — 직전 review 가 지적한 사항. "
        "기존 후보 풀을 가능한 한 유지하되, 명시적으로 누락된 트렌드/카테고리/기술이 있으면 "
        "후보로 추가 도출하여 final 분석에 포함하라.\n"
        f"{bullet}\n"
    )


def _collect_patent_data(domain: str, category_hints: list) -> dict:
    """
    USPTO API 로 도메인 + 카테고리별 특허 데이터를 수집합니다.
    """
    tool = USPTOPatentTool()

    # 도메인 키워드 정제 (앞 2~3 단어 추출)
    domain_keywords = " ".join(domain.split()[:4])

    # 카테고리별 키워드 매핑
    category_keywords = {
        "Equipment": f"{domain_keywords} equipment tool",
        "Material": f"{domain_keywords} material",
        "Process": f"{domain_keywords} process method",
        "Architecture": f"{domain_keywords} architecture design circuit",
        "Packaging": f"{domain_keywords} packaging bonding stacking",
    }

    # 힌트가 있으면 해당 카테고리만, 없으면 전체
    targets = (
        {k: v for k, v in category_keywords.items() if k in category_hints}
        if category_hints
        else category_keywords
    )

    raw_data = {"domain": domain, "categories": {}}
    for cat, keyword in targets.items():
        print(f"  [Patent] '{keyword}' 특허 데이터 수집 중...")
        raw_data["categories"][cat] = tool.collect_full_signal(keyword)

    return raw_data


# ── LangGraph 노드 함수 ───────────────────────────────────────

def run_patent_agent(state: AnalysisState) -> dict:
    """
    Patent Data Agent 노드.
    USPTO 데이터 수집 → Claude 분석 → patent_analysis 반환
    """
    print("\n[Patent Agent] 시작")
    messages = []

    try:
        # ① USPTO 데이터 수집
        print("[Patent Agent] USPTO API 데이터 수집 중...")
        patent_raw = _collect_patent_data(
            state["domain"], state.get("category_hints", [])
        )

        # ② Claude/Ollama 에게 분석 요청
        llm = get_llm(max_tokens=4096)
        prompt = load_prompt("patent_agent", PATENT_ANALYSIS_METHOD)
        patent_raw_for_prompt = json.dumps(
            patent_raw, ensure_ascii=False, indent=2
        )[:6000]

        user_prompt = prompt.render_user(
            domain=state["domain"],
            reference_year=state["reference_year"],
            category_hints=state.get("category_hints", []),
            patent_raw=patent_raw_for_prompt,
        )

        # Horizon 인식 가이드 — 강제 X, 참고 톤 (TRL 분포 권장)
        user_prompt = user_prompt + f"""

[Horizon 인식 — 권장 (강제 X)]
reference_year={state['reference_year']} 는 로드맵 horizon 의 **목표 종료 연도** 이다.
후보 기술 발굴 시 다음을 고려:
- 단기 (TRL 7+, 양산 가까운) / 중기 (TRL 5-6, 프로토타입) / 장기 (TRL 3-4, R&D) 가
  골고루 분포하도록 권장 — 모든 후보를 한 TRL 대역으로 채우지 말 것.
- 단 도메인 특성상 한 대역에 집중되는 게 자연스러우면 그대로 — 정직한 분석 우선.
- 즉 "정직한 분석" ≫ "TRL 분포 균형". 둘이 충돌하면 정직성 선호, 비슷하면 분포 권장.
"""

        # Orchestrator REVISE feedback 을 user_prompt 끝에 append
        user_prompt = user_prompt + _format_orchestrator_feedback(state.get("orchestrator_feedback"))

        print(f"[Patent Agent] prompt variant: {prompt.variant}")
        print("[Patent Agent] LLM 분석 요청 중...")
        response = llm.invoke(
            [
                SystemMessage(content=prompt.system),
                HumanMessage(content=user_prompt),
            ]
        )

        # ③ JSON 파싱
        result = _extract_json(response.content)
        patent_analysis = result.get("patent_analysis", [])
        patent_maps = result.get("patent_maps", {})

        print(f"[Patent Agent] 완료: {len(patent_analysis)}개 기술 식별")
        messages.append(AIMessage(content=f"Patent Agent: {len(patent_analysis)}개 후보 기술 분석 완료"))

        return {
            "patent_raw_data": patent_raw,
            "patent_analysis": patent_analysis,
            "patent_maps": patent_maps,
            "patent_prompt": prompt.metadata,
            "messages": messages,
            "error": None,
        }

    except Exception as e:
        err_msg = f"Patent Agent 오류: {str(e)}"
        print(f"[Patent Agent] ❌ {err_msg}")
        messages.append(AIMessage(content=err_msg))
        return {
            "patent_raw_data": {},
            "patent_analysis": [],
            "patent_maps": {},
            "patent_prompt": {
                "agent": "patent_agent",
                "variant": PATENT_ANALYSIS_METHOD,
                "error": "prompt load or analysis failed",
            },
            "messages": messages,
            "error": err_msg,
        }
