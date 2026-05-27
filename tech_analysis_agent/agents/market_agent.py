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
import os
import re
from datetime import datetime
from pathlib import Path
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage

from llm_factory import get_llm
from config import USE_PATENT_MAP

from state import AnalysisState
from tools.market_tools import MarketIntelligenceTool

# ── 시스템 프롬프트 ───────────────────────────────────────────

MARKET_AGENT_SYSTEM_PROMPT = """You are an Industry Market Research Agent, a specialized sub-agent of the Technology Analysis system.
Your role is to analyze market intelligence data and extract structured signals about market attractiveness, market sizing, and timing for each candidate technology.
You do NOT make patent or technical maturity assessments.

The Patent Agent provides:
- candidate technologies inferred from company patent portfolios
- `actor_similarity_map`, where the user's company is connected to related companies by patent-theme similarity

Use this map as market-research guidance:
- Related actors with high similarity indicate relevant competitors, partners, customers, or ecosystem comparables.
- Shared technology areas indicate market/application angles to investigate.
- Do not alter tech_id values. The map guides search interpretation, not patent scoring.

---
[Research Process]

For each candidate technology, follow this process:
1. Market report discovery: identify credible market reports, analyst notes, industry news, or company/sector sources.
2. TAM/SAM/SOM estimation: infer a reasonable total, serviceable, and obtainable market range from evidence. If exact values are unavailable, provide an evidence-backed estimate.
3. CAGR and growth outlook: collect CAGR, forecast horizon, growth drivers, and adoption timing.
4. Competitive/application interpretation: use actor_similarity_map context to identify relevant market actors and adoption channels.
5. Scoring: compute market_score only after the above evidence is considered.

---
[Core Responsibilities]

For each technology in the input list, determine:
- tech_id (MUST match the input list exactly)
- name
- market_score (0–100, calculated per framework below)
- market_signals (sub-metric breakdown)
- tam_sam_som
- cagr_forecast
- key_market_reports
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

[Horizon 인식 — 권장 (강제 X, 가이드)]
사용자가 명시한 `reference_year` 는 로드맵 horizon 의 **목표 종료 연도** 이다.
boom_quarter 는 시장 데이터 그대로 정직하게 결정하되, 다음을 고려하라:

- 후보 기술 전체적으로 **boom_quarter 분포가 horizon 에 stagger** 되도록 신경 쓸 것
  (예: reference_year=2030 → 일부는 2027, 일부는 2028, 일부는 2029-2030 으로 분포)
- 모든 후보의 boom 이 한 시점에 몰리면 로드맵의 일부 구간이 비어 horizon 활용도 ↓
- 시장 데이터가 명백히 한쪽 시점을 가리키면 정직한 분석 우선 — 인위적 분산 금지
- **정직한 분석 ≫ 분포 균형** (둘이 충돌할 때만 정직성 선호, 비슷하면 분포 권장)

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
      "tam_sam_som": {
        "tam_usd_b": 50.0,
        "sam_usd_b": 15.0,
        "som_usd_b": 2.0,
        "basis": "..."
      },
      "cagr_forecast": {
        "cagr_pct": 18.5,
        "forecast_period": "2025-2030",
        "basis": "..."
      },
      "key_market_reports": [
        {"title": "...", "url": "...", "use": "TAM/CAGR/competition evidence"}
      ],
      "map_context_used": {
        "related_actors": ["..."],
        "shared_technology_areas": ["..."]
      },
      "expected_market_boom_quarter": "2028 Q1",
      "competitive_landscape": "Oligopoly – 1~2 dominant players",
      "data_quality": "real",
      "rationale": "..."
    }
  ]
}"""


def _market_system_prompt() -> str:
    """Return the Market Agent prompt with the patent-map experiment switch applied."""
    if USE_PATENT_MAP:
        return MARKET_AGENT_SYSTEM_PROMPT
    return MARKET_AGENT_SYSTEM_PROMPT + """

[Patent Map Experiment Setting — OVERRIDE]
USE_PATENT_MAP=false 입니다.
이번 실행은 actor_similarity_map 미사용 대조군입니다.
Patent Agent가 제공한 기술 후보군과 Tavily 시장 데이터만 사용하세요.
actor_similarity_map, patent_maps, map_context_used가 비어 있어도 오류로 보지 마세요.
"""


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


def _format_upper_context(company_scenario: dict, strategic_direction: list) -> str:
    """Orchestrator 추출 Company Scenario + Strategic Direction → 상위 컨텍스트 블록.
    모든 sibling agent 의 LLM 노드에서 동일 형식 사용."""
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
        block += f"- Company: {cn}\n"
        block += f"- Industry: {ind}\n"
        if rev:
            block += f"- Annual Revenue: ${rev:,.0f}\n"
        if ratio:
            block += f"- R&D Budget Ratio: {ratio:.0%}\n"
        if rd:
            block += f"- Annual R&D Budget: ${rd:,.0f}\n"
        if horizon:
            block += f"- Planning Horizon: {horizon}\n"
    if strategic_direction:
        block += "\n[Strategic Direction]\n"
        for i, d in enumerate(strategic_direction, 1):
            block += f"  {i}. {d}\n"
    return block + "\n"
def _to_float(value, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value)
    match = re.search(r"-?\d+(?:\.\d+)?", text.replace(",", ""))
    if not match:
        return default
    try:
        return float(match.group())
    except ValueError:
        return default


def _extract_usd_b(value) -> float:
    if isinstance(value, dict):
        values = [_extract_usd_b(v) for v in value.values()]
        return max(values) if values else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").lower().replace(",", "")
    number = _to_float(text, 0.0)
    if not number:
        return 0.0
    if "trillion" in text:
        return number * 1000
    if "million" in text:
        return number / 1000
    return number


def _score_from_cagr_tam(cagr_pct: float, tam_usd_b: float) -> float:
    cagr_component = min(100.0, max(0.0, cagr_pct * 4.0))
    tam_component = 100.0 if tam_usd_b >= 10 else min(80.0, tam_usd_b * 8.0)
    return round((cagr_component * 0.6) + (tam_component * 0.4), 1)


def _normalize_market_analysis(
    market_analysis: list,
    tech_list: list,
    market_raw: dict,
    reference_year: int,
) -> list:
    """Repair LLM schema drift into the canonical MarketAnalysisResult shape."""
    tech_by_id = {item.get("tech_id"): item for item in tech_list}
    raw_by_id = (market_raw.get("technologies") or {}) if isinstance(market_raw, dict) else {}
    normalized = []

    for item in market_analysis or []:
        if not isinstance(item, dict):
            continue
        tech_id = item.get("tech_id")
        if tech_id not in tech_by_id:
            continue
        tech = tech_by_id[tech_id]
        raw = raw_by_id.get(tech_id) or {}

        cagr_pct = _to_float(
            (item.get("cagr_forecast") or {}).get("cagr_pct")
            if isinstance(item.get("cagr_forecast"), dict)
            else item.get("growth_rate"),
            0.0,
        )
        tam_sam_som = item.get("tam_sam_som") if isinstance(item.get("tam_sam_som"), dict) else {}
        tam_usd_b = _to_float(tam_sam_som.get("tam_usd_b"), 0.0) if tam_sam_som else 0.0
        if not tam_usd_b:
            tam_usd_b = _extract_usd_b(item.get("market_size"))
        if not tam_sam_som:
            tam_sam_som = {
                "tam_usd_b": round(tam_usd_b, 2),
                "sam_usd_b": round(tam_usd_b * 0.3, 2) if tam_usd_b else 0.0,
                "som_usd_b": round(tam_usd_b * 0.05, 2) if tam_usd_b else 0.0,
                "basis": "LLM output normalized from market_size/growth_rate evidence.",
            }

        key_companies = item.get("key_companies") or []
        if isinstance(key_companies, list) and key_companies:
            max_share = max((_to_float(c.get("market_share"), 0.0) for c in key_companies if isinstance(c, dict)), default=0.0)
        else:
            max_share = 0.0

        market_signals = item.get("market_signals") if isinstance(item.get("market_signals"), dict) else {}
        if not market_signals:
            market_signals = {
                "tam_growth_rate": _score_from_cagr_tam(cagr_pct, tam_usd_b),
                "time_to_market_urgency": 82 if cagr_pct >= 15 else 65,
                "policy_and_investment_tailwind": 78 if item.get("policy_support") else 55,
                "competitive_moat_potential": 82 if max_share >= 50 else 65,
            }

        market_score = _to_float(item.get("market_score"), 0.0)
        if not market_score:
            market_score = round(
                float(market_signals.get("tam_growth_rate", 0)) * 0.35
                + float(market_signals.get("time_to_market_urgency", 0)) * 0.30
                + float(market_signals.get("policy_and_investment_tailwind", 0)) * 0.20
                + float(market_signals.get("competitive_moat_potential", 0)) * 0.15,
                1,
            )

        reports = item.get("key_market_reports") if isinstance(item.get("key_market_reports"), list) else []
        if not reports:
            reports = [
                {"title": r.get("title"), "url": r.get("url"), "use": "market report evidence"}
                for r in (raw.get("market_reports") or [])[:3]
            ]

        actor_context = raw.get("actor_context") or item.get("map_context_used") or {}
        boom = item.get("expected_market_boom_quarter")
        if not boom:
            boom_year = min(reference_year or 2030, 2028 if cagr_pct >= 15 else 2029)
            boom = f"{boom_year} Q1"

        normalized.append({
            "tech_id": tech_id,
            "name": tech.get("name") or item.get("name") or item.get("technology_name"),
            "market_score": market_score,
            "market_signals": market_signals,
            "tam_sam_som": tam_sam_som,
            "cagr_forecast": item.get("cagr_forecast") if isinstance(item.get("cagr_forecast"), dict) else {
                "cagr_pct": cagr_pct,
                "forecast_period": f"2025-{reference_year or 2030}",
                "basis": "LLM output normalized from growth_rate evidence.",
            },
            "key_market_reports": reports,
            "map_context_used": {
                "related_actors": actor_context.get("related_actors", []),
                "shared_technology_areas": actor_context.get("shared_technology_areas", []),
            },
            "expected_market_boom_quarter": boom,
            "competitive_landscape": item.get("competitive_landscape") or (
                "Oligopoly – 1~2 dominant players" if max_share >= 50 else "Concentrated – 3~5 key players"
            ),
            "data_quality": item.get("data_quality") or "real",
            "rationale": item.get("rationale") or "시장 보고서, 성장률, 투자/정책 신호, actor similarity map의 경쟁 구도를 종합해 산정했습니다.",
        })
    return normalized


def _format_orchestrator_feedback(orchestrator_feedback: dict) -> str:
    """REVISE 시 전달된 feedback 을 market_agent 프롬프트에 박을 섹션으로 포맷."""
    if not orchestrator_feedback:
        return ""
    items = orchestrator_feedback.get("text") or []
    items = [str(t).strip() for t in items if isinstance(t, str) and t.strip()]
    if not items:
        return ""
    bullet = "\n".join(f"- {t}" for t in items)
    return (
        "\n\n[ORCHESTRATOR REVISE FEEDBACK] — 직전 review 가 지적한 사항. "
        "해당 트렌드/기술의 시장 매력도 평가 시 반영하라.\n"
        f"{bullet}\n"
    )


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())
    return cleaned.strip("_") or "market_agent"


def _run_id() -> str:
    return (
        os.getenv("MARKET_AGENT_RUN_ID")
        or os.getenv("AGENT_RUN_ID")
        or datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    )


def _market_log_dir(run_id: str) -> Path:
    configured = os.getenv("MARKET_AGENT_LOG_DIR")
    if configured:
        return Path(configured)
    default_root = Path(__file__).resolve().parents[2] / "orchestration_agent" / "outputs" / "market_agent"
    return default_root / run_id


def _write_market_agent_log(
    *,
    state: AnalysisState,
    run_id: str,
    market_raw: dict,
    tech_list: list,
    patent_maps: dict,
    system_prompt: str | None = None,
    user_prompt: str | None = None,
    llm_raw_response: str | None = None,
    market_analysis: list | None = None,
    error: str | None = None,
) -> None:
    try:
        output_dir = _market_log_dir(run_id)
        output_dir.mkdir(parents=True, exist_ok=True)
        prefix = _safe_filename(state.get("company_name") or state.get("domain") or "market")
        payload = {
            "run_id": run_id,
            "timestamp_utc": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "input": {
                "domain": state.get("domain"),
                "reference_year": state.get("reference_year"),
                "company_name": state.get("company_name"),
                "category_hints": state.get("category_hints", []),
                "use_patent_map": USE_PATENT_MAP,
            },
            "tech_list": tech_list,
            "patent_maps": patent_maps,
            "market_raw_data": market_raw,
            "llm_request": {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
            },
            "llm_response": {
                "raw_content": llm_raw_response,
            },
            "market_analysis": market_analysis or [],
            "error": error,
        }
        log_path = output_dir / f"{prefix}_market_agent_log.json"
        log_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        (output_dir / "latest_market_agent_log.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (output_dir / "market_raw_data.json").write_text(
            json.dumps(market_raw, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if market_analysis is not None:
            (output_dir / "market_analysis.json").write_text(
                json.dumps(market_analysis or [], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        print(f"[Market Agent] log 저장: {log_path}")
    except Exception as log_error:
        print(f"[Market Agent] ⚠️ log 저장 실패: {log_error}")


def _actor_similarity_contexts(patent_maps: dict) -> dict:
    edges = (patent_maps or {}).get("actor_similarity_map") or []
    edges = sorted(
        [e for e in edges if isinstance(e, dict)],
        key=lambda e: float(e.get("edge_weight", e.get("similarity", 0)) or 0),
        reverse=True,
    )
    center = ""
    related = []
    shared = []
    for edge in edges[:8]:
        center = center or edge.get("center_actor") or edge.get("actor_a") or ""
        actor = edge.get("related_actor") or edge.get("actor_b")
        if actor and actor not in related:
            related.append(actor)
        for area in edge.get("shared_technology_areas") or []:
            if area and area not in shared:
                shared.append(area)
    return {
        "center_actor": center,
        "related_actors": related,
        "shared_technology_areas": shared,
        "top_edges": edges[:6],
    }


def _context_for_tech(tech: dict, patent_maps: dict, global_context: dict) -> dict:
    source_companies = [
        item for item in (tech.get("source_companies") or [])
        if isinstance(item, str) and item.strip()
    ]
    tech_words = {
        token.lower()
        for token in re.split(r"[\s,/·+()\\-]+", tech.get("name", ""))
        if len(token) >= 2
    }
    matched_edges = []
    for edge in global_context.get("top_edges") or []:
        actor = edge.get("related_actor") or edge.get("actor_b")
        areas = edge.get("shared_technology_areas") or []
        area_words = {
            token.lower()
            for area in areas
            for token in re.split(r"[\s,/·+()\\-]+", str(area))
            if len(token) >= 2
        }
        if actor in source_companies or (tech_words and tech_words.intersection(area_words)):
            matched_edges.append(edge)

    selected_edges = matched_edges or (global_context.get("top_edges") or [])[:4]
    related = []
    shared = []
    for edge in selected_edges:
        actor = edge.get("related_actor") or edge.get("actor_b")
        if actor and actor not in related:
            related.append(actor)
        for area in edge.get("shared_technology_areas") or []:
            if area and area not in shared:
                shared.append(area)

    return {
        "center_actor": global_context.get("center_actor"),
        "related_actors": related[:4],
        "shared_technology_areas": shared[:8],
        "source_companies": source_companies[:6],
        "map_edges": selected_edges[:4],
    }


def _compact_items(items: list, limit: int = 1) -> list:
    compact = []
    for item in (items or [])[:limit]:
        compact.append({
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "score": item.get("score", 0),
        })
    return compact


def _compact_market_raw_for_prompt(market_raw: dict, per_section_limit: int = 1) -> str:
    compact = {
        "domain": market_raw.get("domain"),
        "research_process": market_raw.get("research_process", []),
        "actor_similarity_context": market_raw.get("actor_similarity_context", {}),
        "technologies": {},
    }
    for tech_id, data in (market_raw.get("technologies") or {}).items():
        actor_context = data.get("actor_context", {}) or {}
        queries = data.get("queries", {}) or {}
        compact["technologies"][tech_id] = {
            "tech_name": data.get("tech_name"),
            "domain": data.get("domain"),
            "actor_context": {
                "center_actor": actor_context.get("center_actor"),
                "related_actors": (actor_context.get("related_actors", []) or [])[:3],
                "shared_technology_areas": (actor_context.get("shared_technology_areas", []) or [])[:4],
                "source_companies": (actor_context.get("source_companies", []) or [])[:3],
            },
            "market_reports": _compact_items(data.get("market_reports"), per_section_limit),
            "tam_sam_som_data": _compact_items(data.get("tam_sam_som_data"), per_section_limit),
            "cagr_forecast_data": _compact_items(data.get("cagr_forecast_data"), per_section_limit),
            "competitive_data": _compact_items(data.get("competitive_data"), per_section_limit),
            "_mock": data.get("_mock"),
            "error": data.get("error"),
        }
    return json.dumps(compact, ensure_ascii=False, indent=2)


def _collect_market_data(
    domain: str,
    patent_analysis: list,
    patent_maps: dict,
) -> dict:
    """
    Tavily API 로 각 후보 기술의 시장 데이터를 수집합니다.
    USE_PATENT_MAP=true이면 actor_similarity_map의 관련 actor/shared area를 검색 context로 사용합니다.
    """
    tool = MarketIntelligenceTool()
    if not tool.use_mock and not tool.client:
        raise RuntimeError(tool.init_error or "Tavily client is not initialized")
    active_patent_maps = patent_maps if USE_PATENT_MAP else {}
    global_actor_context = _actor_similarity_contexts(active_patent_maps)
    research_process = [
        "market_report_discovery",
        "tam_sam_som_estimation",
        "cagr_growth_outlook_collection",
    ]
    if USE_PATENT_MAP and active_patent_maps:
        research_process.append("actor_similarity_map_driven_competitive_context")
    else:
        research_process.append("candidate_technology_based_competitive_context")

    market_raw = {
        "domain": domain,
        "use_patent_map": USE_PATENT_MAP,
        "research_process": research_process,
        "actor_similarity_context": global_actor_context,
        "technologies": {},
    }

    for tech in patent_analysis:
        tech_name = tech.get("name", "")
        tech_id = tech.get("tech_id", "")
        actor_context = _context_for_tech(tech, active_patent_maps, global_actor_context)
        related = ", ".join(actor_context.get("related_actors") or [])
        print(f"  [Market] '{tech_name}' 시장 데이터 수집 중... (actors: {related or 'N/A'})")
        market_raw["technologies"][tech_id] = tool.collect_full_signal(
            tech_name,
            domain,
            actor_context=actor_context,
        )

    return market_raw


# ── LangGraph 노드 함수 ───────────────────────────────────────

def run_market_agent(state: AnalysisState) -> dict:
    """
    Market Size Agent 노드.
    Tavily 데이터 수집 → Claude 분석 → market_analysis 반환
    """
    print("\n[Market Agent] 시작")
    messages = []
    run_id = _run_id()
    market_raw = {}
    tech_list = []
    patent_maps = state.get("patent_maps") or {}
    if not USE_PATENT_MAP:
        patent_maps = {}
    user_prompt = None
    system_prompt = _market_system_prompt()
    llm_raw_response = None

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
        market_raw = _collect_market_data(state["domain"], patent_analysis, patent_maps)

        # ② 입력 기술 목록 준비 (tech_id 고정)
        tech_list = [
            {
                "tech_id": t["tech_id"],
                "name": t["name"],
                "category": t.get("category", ""),
                "roadmapping_signals": t.get("roadmapping_signals", {}),
                "source_companies": t.get("source_companies", []),
                "evidence_patents": t.get("evidence_patents", []),
            }
            for t in patent_analysis
        ]

        # ③ Claude 에게 분석 요청
        llm = get_llm(max_tokens=4096)
        patent_maps_block = ""
        if USE_PATENT_MAP and patent_maps:
            patent_maps_block = f"""
아래 patent_maps 는 Patent Agent가 관련 기업 특허 포트폴리오 기반으로 생성한 산출물입니다.
현재 map은 actor_similarity_map 중심입니다. 각 edge의 related_actor, similarity, shared_technology_areas를 시장 조사 범위/경쟁 구도/파트너 생태계 해석 근거로 사용하세요.

[Patent Maps]
{json.dumps(patent_maps, ensure_ascii=False, indent=2)[:6000]}
"""
        else:
            patent_maps_block = """
이번 실행은 USE_PATENT_MAP=false 또는 patent_maps 미제공 상태입니다.
actor_similarity_map 없이 기술 후보군, source_companies, evidence_patents, Tavily 시장 데이터만 사용해 시장성을 평가하세요.
"""

        research_process_text = (
            "시장 보고서 탐색, TAM/SAM/SOM 근거 탐색, CAGR/성장 전망 수집, "
            "actor_similarity_map 기반 경쟁/협력 구도 탐색"
            if USE_PATENT_MAP and patent_maps
            else
            "시장 보고서 탐색, TAM/SAM/SOM 근거 탐색, CAGR/성장 전망 수집, "
            "기술 후보군 기반 경쟁/협력 구도 탐색"
        )

        user_prompt = f"""
도메인: {state['domain']}
분석 기준 연도: {state['reference_year']}

분석 대상 기술 목록 (tech_id 변경 불가):
{json.dumps(tech_list, ensure_ascii=False, indent=2)}
{patent_maps_block}

아래는 Tavily Search API 로 수집한 실제 시장 인텔리전스 데이터입니다.
수집 프로세스는 {research_process_text}을 포함합니다.
이 데이터를 기반으로 각 기술의 시장 매력도와 시장 규모/성장 전망을 분석해주세요.

[수집된 시장 데이터]
{_compact_market_raw_for_prompt(market_raw)}

위 데이터를 분석하여 지정된 JSON 포맷으로 market_analysis 를 출력하세요.
모든 tech_id는 반드시 입력 목록의 값과 동일해야 합니다.
"""

        # Company Scenario + Strategic Direction (상위 컨텍스트) — prompt 맨 앞으로
        upper_block = _format_upper_context(
            state.get("company_scenario"),
            state.get("strategic_direction"),
        )
        feedback_block = _format_orchestrator_feedback(state.get("orchestrator_feedback"))
        # 최종 user_prompt: [상위 컨텍스트] → [본문] → [feedback]
        user_prompt = upper_block + user_prompt + feedback_block

        _write_market_agent_log(
            state=state,
            run_id=run_id,
            market_raw=market_raw,
            tech_list=tech_list,
            patent_maps=patent_maps,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )

        print("[Market Agent] LLM 분석 요청 중...")
        response = llm.invoke(
            [
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt),
            ]
        )
        llm_raw_response = response.content

        # ④ JSON 파싱
        result = _extract_json(llm_raw_response)
        market_analysis = _normalize_market_analysis(
            result.get("market_analysis", []),
            tech_list,
            market_raw,
            state.get("reference_year"),
        )

        _write_market_agent_log(
            state=state,
            run_id=run_id,
            market_raw=market_raw,
            tech_list=tech_list,
            patent_maps=patent_maps,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            llm_raw_response=llm_raw_response,
            market_analysis=market_analysis,
        )

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
        _write_market_agent_log(
            state=state,
            run_id=run_id,
            market_raw=market_raw,
            tech_list=tech_list,
            patent_maps=patent_maps,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            llm_raw_response=llm_raw_response,
            error=err_msg,
        )
        messages.append(AIMessage(content=err_msg))
        return {
            "market_raw_data": {},
            "market_analysis": [],
            "messages": messages,
            "error": err_msg,
        }
