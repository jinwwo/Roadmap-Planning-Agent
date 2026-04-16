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
from state import AnalysisState
from tools.patent_tools import USPTOPatentTool

# ── 시스템 프롬프트 ───────────────────────────────────────────

PATENT_AGENT_SYSTEM_PROMPT = """You are a Patent Data Agent, a specialized sub-agent of the Technology Analysis system.
Your role is to analyze patent data and extract structured signals about technology maturity, momentum, and white-space opportunities.
You do NOT make investment or roadmap decisions. Your sole purpose is to convert raw patent landscape data into a structured, scoreable signal for each candidate technology.

---
[Core Responsibilities]

Based on the provided raw patent data, identify candidate technologies and output structured patent signal analysis.
For each identified candidate technology, determine:
- tech_id (assigned sequentially: T01, T02, ...)
- name
- category (Material / Equipment / Process / Architecture / Packaging)
- estimated_trl (1–9, based on patent maturity signals)
- patent_score (0–100, calculated per framework below)
- patent_signals (sub-metric breakdown)
- dependency_hints (list of tech_ids that likely precede this technology)
- data_quality ("real" if based on actual API data, "estimated" if inferred)
- rationale (2–3 sentences)

---
[Patent Scoring Framework]

patent_score = (filing_growth_rate × 0.30) + (citation_concentration × 0.25)
             + (white_space_index × 0.25) + (key_assignee_concentration × 0.20)

1. filing_growth_rate (0–100, weight 30%)
   - 80–100: CAGR > 30% over last 3 years
   - 50–79:  CAGR 10–30%
   - 0–49:   CAGR < 10% or declining

2. citation_concentration (0–100, weight 25%)
   - 80–100: Top 10% patents hold > 60% of citations (high_citation_ratio > 60)
   - 50–79:  Top 10% hold 30–60%
   - 0–49:   Evenly distributed or low citation count

3. white_space_index (0–100, weight 25%)
   - 80–100: Low total_patents but high CAGR (emerging area)
   - 50–79:  Moderate density with identifiable gaps
   - 0–49:   Saturated area (total_patents very high, low CAGR)

4. key_assignee_concentration (0–100, weight 20%)
   - 80–100: Top assignees include Tier-1 companies (TSMC, ASML, Samsung, Intel, etc.)
   - 50–79:  Mix of mid-tier corporate and academic
   - 0–49:   Mostly academic/small players

---
[TRL Estimation]
- TRL 1–3: Academic/research filings dominant, high white-space, low Tier-1 assignees
- TRL 4–6: Mix of research and corporate, moderate citation density
- TRL 7–9: Dominant corporate filers, dense prior art, continuation filings

---
[Dependency Hinting]
- Material/Equipment → Process → Architecture/Packaging (general order)
- If technologies are clearly co-dependent, add dependency_hints
- Use only tech_ids defined in THIS output

---
[Naming Rules — CRITICAL]
- `name` MUST be a concise TECHNOLOGY CONCEPT in **Korean** (2–6 words). Example: "EUV 리소그래피", "3D 칩렛 스태킹", "High-k ALD 공정", "Backside 전력망".
- DO NOT copy patent titles verbatim. ABSTRACT the underlying concept.
- BAD (절대 이렇게 쓰지 말 것): "Method and apparatus for ...", "System for enhanced ... with improved yield", "EUV lithography process for 반도체 equipment tool".
- GOOD: 짧고 명확한 한국어 기술 개념 명칭만.
- `rationale` 도 반드시 **한국어 2–3문장**으로 작성.
- `category` 는 영문 그대로 유지 (Material / Equipment / Process / Architecture / Packaging).

---
[CRITICAL] Return 5–10 candidate technologies. Output ONLY valid JSON. No markdown, no explanation outside JSON.

Output format:
{
  "patent_analysis": [
    {
      "tech_id": "T01",
      "name": "...",
      "category": "Equipment",
      "estimated_trl": 4,
      "patent_score": 81.5,
      "patent_signals": {
        "filing_growth_rate": 85,
        "citation_concentration": 78,
        "white_space_index": 72,
        "key_assignee_concentration": 92
      },
      "dependency_hints": [],
      "data_quality": "real",
      "rationale": "..."
    }
  ]
}"""


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

        # ② Claude 에게 분석 요청
        llm = get_llm(max_tokens=4096)

        user_prompt = f"""
도메인: {state['domain']}
분석 기준 연도: {state['reference_year']}
카테고리 힌트: {state.get('category_hints', [])}

아래는 USPTO PatentsView API에서 수집한 실제 특허 데이터입니다.
이 데이터를 기반으로 해당 도메인의 유망 후보 기술들을 분석해주세요.

[수집된 특허 데이터]
{json.dumps(patent_raw, ensure_ascii=False, indent=2)[:6000]}

위 데이터를 분석하여 지정된 JSON 포맷으로 patent_analysis 를 출력하세요.
"""

        print("[Patent Agent] Claude 분석 요청 중...")
        response = llm.invoke(
            [
                SystemMessage(content=PATENT_AGENT_SYSTEM_PROMPT),
                HumanMessage(content=user_prompt),
            ]
        )

        # ③ JSON 파싱
        result = _extract_json(response.content)
        patent_analysis = result.get("patent_analysis", [])

        print(f"[Patent Agent] 완료: {len(patent_analysis)}개 기술 식별")
        messages.append(AIMessage(content=f"Patent Agent: {len(patent_analysis)}개 후보 기술 분석 완료"))

        return {
            "patent_raw_data": patent_raw,
            "patent_analysis": patent_analysis,
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
            "messages": messages,
            "error": err_msg,
        }
