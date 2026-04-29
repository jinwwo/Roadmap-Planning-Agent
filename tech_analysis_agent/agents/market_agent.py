"""
agents/market_agent.py
───────────────────────
Industry Market Size Agent 노드

역할:
1. Patent Agent 가 식별한 후보 기술별로 Tavily 시장 데이터 수집
2. Claude 에게 시장 분석 요청 → 구조화된 market_analysis JSON 반환
3. AnalysisState 에 market_raw_data / market_analysis 저장
"""

import json
import re
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage

from llm_factory import get_llm

from state import AnalysisState
from tools.market_tools import MarketIntelligenceTool

# ── 시스템 프롬프트 ───────────────────────────────────────────

MARKET_AGENT_SYSTEM_PROMPT = """You are an Industry Market Size Agent, a specialized sub-agent of the Technology Analysis system.
Your role is to analyze market intelligence data and extract structured signals about market attractiveness and timing for each candidate technology.
You do NOT make patent or technical maturity assessments.

If patent maps are provided, use them as strategic context:
- Use `technology_industry_map` to choose and justify relevant market/application angles.
- Use `actor_similarity_map` and `actor_relations_map` to interpret competitive and partnership context.
- Use `technology_affinity_map` to avoid evaluating each technology as an isolated item when adjacent technologies shape adoption.
- Do not alter tech_id values. Patent maps are context, not replacement market data.

---
[Core Responsibilities]

For each technology in the input list, determine:
- tech_id (MUST match the input list exactly)
- name
- market_score (0–100, calculated per framework below)
- market_signals (sub-metric breakdown)
- expected_market_boom_quarter ("YYYY QX" format)
- competitive_landscape (standard label)
- data_quality ("real" if grounded in provided data, "estimated" if inferred)
- rationale (2–3 sentences referencing TAM, timing, competitive context)

---
[Market Scoring Framework]

market_score = (tam_growth_rate × 0.35) + (time_to_market_urgency × 0.30)
             + (policy_and_investment_tailwind × 0.20) + (competitive_moat_potential × 0.15)

1. tam_growth_rate (0–100, weight 35%)
   - 80–100: Market CAGR > 20%, TAM > $10B by target year
   - 50–79:  CAGR 10–20% or TAM $1B–$10B
   - 0–49:   CAGR < 10% or TAM < $1B

2. time_to_market_urgency (0–100, weight 30%)
   - 80–100: Market window opens within 2–3 years; first-mover advantage critical
   - 50–79:  Window opens in 3–5 years; moderate urgency
   - 0–49:   Window > 5 years or already saturated

3. policy_and_investment_tailwind (0–100, weight 20%)
   - 80–100: Active government subsidies + major VC/corporate investment surge
   - 50–79:  Moderate policy support or investment interest
   - 0–49:   No notable support or declining interest

4. competitive_moat_potential (0–100, weight 15%)
   - 80–100: High barrier, few players, proprietary lock-in possible
   - 50–79:  Moderate competition, some differentiation room
   - 0–49:   Commoditized or saturated, low margin potential

---
[Competitive Landscape Labels] — use exactly one:
"Oligopoly – 1~2 dominant players"
"Concentrated – 3~5 key players"
"Fragmented – many players, low differentiation"
"Nascent – no clear leader yet"
"Commoditized – price-driven competition"

---
[Market Boom Quarter Estimation]
- Base on: TAM inflection point + production node transition announcements + policy timelines
- Format: "YYYY QX" (e.g., "2028 Q1")
- Default if data insufficient: 3 years from reference year, Q1

---
[Language Rules — CRITICAL]
- `name` 은 입력 리스트의 한국어 기술명을 그대로 사용하거나 동일 의미의 한국어로 유지.
- `rationale` 은 반드시 **한국어 2–3문장**.
- `competitive_landscape` 라벨은 위 영문 목록 그대로 유지.

---
[CRITICAL] tech_id values MUST exactly match input. Output ONLY valid JSON.

Output format:
{
  "market_analysis": [
    {
      "tech_id": "T01",
      "name": "...",
      "market_score": 84.5,
      "market_signals": {
        "tam_growth_rate": 90,
        "time_to_market_urgency": 85,
        "policy_and_investment_tailwind": 78,
        "competitive_moat_potential": 72
      },
      "expected_market_boom_quarter": "2028 Q1",
      "competitive_landscape": "Oligopoly – 1~2 dominant players",
      "data_quality": "real",
      "rationale": "..."
    }
  ]
}"""


# ── 헬퍼 함수 ─────────────────────────────────────────────────

def _extract_json(text: str) -> dict:
    """Claude 응답에서 JSON 블록 추출"""
    cleaned = re.sub(r"```(?:json)?\s*", "", text).replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]+\}", cleaned)
        if match:
            return json.loads(match.group())
        raise ValueError(f"유효한 JSON을 파싱할 수 없습니다:\n{text[:300]}")


def _collect_market_data(
    domain: str,
    patent_analysis: list,
) -> dict:
    """
    Tavily API 로 각 후보 기술의 시장 데이터를 수집합니다.
    """
    tool = MarketIntelligenceTool()
    market_raw = {"domain": domain, "technologies": {}}

    for tech in patent_analysis:
        tech_name = tech.get("name", "")
        tech_id = tech.get("tech_id", "")
        print(f"  [Market] '{tech_name}' 시장 데이터 수집 중...")
        market_raw["technologies"][tech_id] = tool.collect_full_signal(tech_name, domain)

    return market_raw


# ── LangGraph 노드 함수 ───────────────────────────────────────

def run_market_agent(state: AnalysisState) -> dict:
    """
    Market Size Agent 노드.
    Tavily 데이터 수집 → Claude 분석 → market_analysis 반환
    """
    print("\n[Market Agent] 시작")
    messages = []

    patent_analysis = state.get("patent_analysis") or []
    if not patent_analysis:
        msg = "Market Agent: patent_analysis 가 비어있어 건너뜁니다."
        print(f"[Market Agent] ⚠️ {msg}")
        return {
            "market_raw_data": {},
            "market_analysis": [],
            "messages": [AIMessage(content=msg)],
            "error": msg,
        }

    try:
        # ① Tavily 시장 데이터 수집
        print("[Market Agent] Tavily 시장 데이터 수집 중...")
        market_raw = _collect_market_data(state["domain"], patent_analysis)
        patent_maps = state.get("patent_maps") or {}

        # ② 입력 기술 목록 준비 (tech_id 고정)
        tech_list = [
            {
                "tech_id": t["tech_id"],
                "name": t["name"],
                "category": t.get("category", ""),
                "roadmapping_signals": t.get("roadmapping_signals", {}),
            }
            for t in patent_analysis
        ]

        # ③ Claude 에게 분석 요청
        llm = get_llm(max_tokens=4096)
        patent_maps_block = ""
        if patent_maps:
            patent_maps_block = f"""
아래 patent_maps 는 Patent Agent가 기술 역량 기반 로드맵 관점으로 생성한 산출물입니다.
시장 분석 시 technology_industry_map은 시장/제품 영역 선택 근거로, actor map은 경쟁/협력 구도 해석 근거로, technology_affinity_map은 인접 기술과의 동반 채택 가능성 판단 근거로 사용하세요.

[Patent Maps]
{json.dumps(patent_maps, ensure_ascii=False, indent=2)[:6000]}
"""

        user_prompt = f"""
도메인: {state['domain']}
분석 기준 연도: {state['reference_year']}

분석 대상 기술 목록 (tech_id 변경 불가):
{json.dumps(tech_list, ensure_ascii=False, indent=2)}
{patent_maps_block}

아래는 Tavily Search API 로 수집한 실제 시장 인텔리전스 데이터입니다.
이 데이터를 기반으로 각 기술의 시장 매력도를 분석해주세요.

[수집된 시장 데이터]
{json.dumps(market_raw, ensure_ascii=False, indent=2)[:8000]}

위 데이터를 분석하여 지정된 JSON 포맷으로 market_analysis 를 출력하세요.
모든 tech_id는 반드시 입력 목록의 값과 동일해야 합니다.
"""

        print("[Market Agent] Claude 분석 요청 중...")
        response = llm.invoke(
            [
                SystemMessage(content=MARKET_AGENT_SYSTEM_PROMPT),
                HumanMessage(content=user_prompt),
            ]
        )

        # ④ JSON 파싱
        result = _extract_json(response.content)
        market_analysis = result.get("market_analysis", [])

        print(f"[Market Agent] 완료: {len(market_analysis)}개 기술 시장 분석")
        messages.append(AIMessage(content=f"Market Agent: {len(market_analysis)}개 기술 시장 분석 완료"))

        return {
            "market_raw_data": market_raw,
            "market_analysis": market_analysis,
            "messages": messages,
            "error": None,
        }

    except Exception as e:
        err_msg = f"Market Agent 오류: {str(e)}"
        print(f"[Market Agent] ❌ {err_msg}")
        messages.append(AIMessage(content=err_msg))
        return {
            "market_raw_data": {},
            "market_analysis": [],
            "messages": messages,
            "error": err_msg,
        }
