"""
agents/patent_agent.py
───────────────────────
Patent Data Agent 노드

역할:
1. configured patent data provider(KIPRIS/mock) 로 원시 특허 데이터 수집
2. LLM 에게 분석 요청 → 구조화된 patent_analysis JSON 반환
3. AnalysisState 에 patent_raw_data / patent_analysis 저장
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage

from llm_factory import get_llm
from config import PATENT_ANALYSIS_METHOD, USE_MOCK_PATENT
from prompt_loader import load_prompt
from state import AnalysisState
from tools.patent_tools import PatentPortfolioTool


_NVIDIA_KIPRIS_RELATED = [
    "어드밴스드 마이크로 디바이시즈",
    "인텔",
    "구글",
    "브로드컴",
    "퀄컴",
    "타이완 세미콘덕터 매뉴팩쳐링",
    "에이알엠 리미티드",
]


_RELATED_COMPANY_FALLBACKS = {
    "ai": _NVIDIA_KIPRIS_RELATED,
    "gpu": _NVIDIA_KIPRIS_RELATED,
    "semiconductor": ["TSMC", "Intel", "Samsung Electronics", "ASML", "Applied Materials", "SK hynix"],
    "반도체": ["TSMC", "Intel", "Samsung Electronics", "ASML", "Applied Materials", "SK hynix"],
    "foundry": ["TSMC", "Intel", "Samsung Electronics", "GlobalFoundries", "UMC", "ASML"],
    "파운드리": ["TSMC", "Intel", "Samsung Electronics", "GlobalFoundries", "UMC", "ASML"],
    "battery": ["CATL", "LG Energy Solution", "Panasonic", "Samsung SDI", "BYD", "SK On"],
    "배터리": ["CATL", "LG Energy Solution", "Panasonic", "Samsung SDI", "BYD", "SK On"],
}


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


def _format_upper_context(company_scenario: dict, strategic_direction: list) -> str:
    """Orchestrator 가 추출한 Company Scenario + Strategic Direction 을
    LLM 프롬프트의 상위 컨텍스트 블록으로 포맷.
    모든 sibling agent 의 LLM 노드에서 동일한 형식으로 사용.
    """
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


def _append_patent_map_generation_note(user_prompt: str) -> str:
    """Keep Patent Agent output stable for downstream map-use ablation."""
    return user_prompt + """

[Patent Map Ablation Control]
Patent Agent는 ablation 조건과 무관하게 항상 `patent_analysis`와
`patent_maps.actor_similarity_map`을 모두 생성하세요.
ON/OFF 비교는 후속 Market Agent가 이 map을 사용할지 여부로만 수행됩니다.
"""


def _collect_patent_data(domain: str, category_hints: list) -> dict:
    """
    Legacy keyword-mode patent data collection is disabled for the current
    company-centered flow. Use C_company_portfolio with KIPRIS/mock data.
    """
    raise RuntimeError(
        "Keyword patent mode is disabled. Set PATENT_ANALYSIS_METHOD=C_company_portfolio "
        "and PATENT_DATA_PROVIDER=kipris or mock."
    )


def _normalize_actor_name(name: str) -> str:
    text = re.sub(r"\s+", " ", str(name or "")).strip()
    return text


def _fallback_related_companies(domain: str, company_name: str) -> list:
    domain_l = (domain or "").lower()
    candidates = []
    for key, companies in _RELATED_COMPANY_FALLBACKS.items():
        if key.lower() in domain_l:
            candidates.extend(companies)
    if not candidates:
        candidates = ["TSMC", "Intel", "Samsung Electronics", "ASML", "Applied Materials"]

    seen = set()
    output = []
    center_l = (company_name or "").lower()
    for company in candidates:
        key = company.lower()
        if key == center_l or key in seen:
            continue
        seen.add(key)
        output.append(company)
    return output[:7]


def _discover_related_companies(
    company_name: str,
    company_profile: str,
    domain: str,
    category_hints: list,
    provided: list | None = None,
) -> list:
    """회사 정보 기반 관련 기업 후보를 도출합니다. 명시 입력이 있으면 그것을 우선합니다."""
    if provided:
        companies = [_normalize_actor_name(c) for c in provided if _normalize_actor_name(c)]
        return companies[:8]

    system = """You identify peer or adjacent companies for patent portfolio analysis.
Return ONLY valid JSON:
{"related_companies":["Company A","Company B",...]}
Choose companies whose patent portfolios are likely relevant to the user's company and domain.
Prefer real company names. Do not include the user's own company."""
    user = f"""[User company]
company_name: {company_name}
company_profile: {company_profile}

[Analysis domain]
domain: {domain}
category_hints: {category_hints}

Find 4-6 related companies for patent portfolio comparison."""

    try:
        llm = get_llm(max_tokens=1024)
        response = llm.invoke([
            SystemMessage(content=system),
            HumanMessage(content=user),
        ])
        result = _extract_json(response.content)
        companies = result.get("related_companies") or []
        companies = [_normalize_actor_name(c) for c in companies if _normalize_actor_name(c)]
        companies = [c for c in companies if c.lower() != (company_name or "").lower()]
        return companies[:6] or _fallback_related_companies(domain, company_name)
    except Exception as e:
        print(f"[Patent Agent] 관련 기업 LLM 탐색 실패 → fallback 사용 ({e})")
        return _fallback_related_companies(domain, company_name)


def _collect_company_patent_data(
    *,
    company_name: str,
    company_profile: str,
    domain: str,
    category_hints: list,
    related_companies: list | None,
) -> dict:
    """
    우리 기업을 중심으로 관련 기업을 찾고, 각 기업의 특허 포트폴리오를 수집합니다.
    """
    tool = PatentPortfolioTool()
    center = _normalize_actor_name(company_name) or "User Company"
    peers = _discover_related_companies(
        center,
        company_profile,
        domain,
        category_hints,
        provided=related_companies,
    )
    domain_keywords = " ".join(
        [str(domain or "").strip(), *[str(c).strip() for c in category_hints or []]]
    ).strip()

    raw_data = {
        "analysis_mode": "company_portfolio",
        "domain": domain,
        "company": {
            "name": center,
            "profile": company_profile,
        },
        "related_companies": peers,
        "company_portfolios": {},
    }

    targets = [center] + peers
    for company in targets:
        print(f"  [Patent] '{company}' 기업 특허 포트폴리오 수집 중...")
        portfolio = tool.collect_company_portfolio(
            company,
            domain_keywords=domain_keywords,
        )
        if company != center and not portfolio.get("recent_patents"):
            print(f"  [Patent] '{company}' 검색 결과 0건 → 관련 기업 후보에서 제외")
            continue
        raw_data["company_portfolios"][company] = portfolio

    raw_data["related_companies"] = [
        company for company in peers
        if company in raw_data["company_portfolios"]
    ]

    return raw_data


def _is_company_portfolio_mode(state: AnalysisState) -> bool:
    if PATENT_ANALYSIS_METHOD == "C_company_portfolio":
        return True
    return bool(state.get("company_name") or state.get("company_profile") or state.get("related_companies"))


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())
    return cleaned.strip("_") or "patent_agent"


def _patent_log_dir() -> Path:
    run_id = os.getenv("PATENT_AGENT_RUN_ID") or os.getenv("AGENT_RUN_ID")
    default_dir = Path(__file__).resolve().parents[2] / "orchestration_agent" / "outputs" / "patent_agent"
    if run_id:
        default_dir = default_dir / run_id
    return Path(os.getenv("PATENT_AGENT_LOG_DIR", str(default_dir)))


def _summarize_portfolios(patent_raw: dict) -> dict:
    portfolios = patent_raw.get("company_portfolios") or {}
    return {
        actor: {
            "source": data.get("_source"),
            "mock": data.get("_mock"),
            "error": data.get("error"),
            "source_breakdown": data.get("source_breakdown"),
            "num_patents": len(data.get("recent_patents") or []),
            "first_patent": {
                key: (data.get("recent_patents") or [{}])[0].get(key)
                for key in ("id", "title", "date", "assignee", "jurisdiction", "source")
            } if data.get("recent_patents") else {},
        }
        for actor, data in portfolios.items()
        if isinstance(data, dict)
    }


def _compact_patent_raw_for_prompt(patent_raw: dict, per_actor_limit: int = 8) -> str:
    """Build a balanced LLM input so every actor survives domestic+foreign expansion."""
    if patent_raw.get("analysis_mode") != "company_portfolio":
        return json.dumps(patent_raw, ensure_ascii=False, indent=2)[:30000]

    compact = {
        "analysis_mode": patent_raw.get("analysis_mode"),
        "domain": patent_raw.get("domain"),
        "company": patent_raw.get("company"),
        "related_companies": patent_raw.get("related_companies"),
        "company_portfolios": {},
    }
    for actor, portfolio in (patent_raw.get("company_portfolios") or {}).items():
        patents = portfolio.get("recent_patents") or []
        compact["company_portfolios"][actor] = {
            "company_name": portfolio.get("company_name", actor),
            "domain_keywords": portfolio.get("domain_keywords"),
            "source": portfolio.get("_source"),
            "source_breakdown": portfolio.get("source_breakdown"),
            "filing_trend": portfolio.get("filing_trend"),
            "citation_summary": portfolio.get("citation_summary"),
            "recent_patents": [
                {
                    "id": item.get("id"),
                    "title": item.get("title"),
                    "abstract": (item.get("abstract") or "")[:360],
                    "date": item.get("date"),
                    "assignee": item.get("assignee"),
                    "jurisdiction": item.get("jurisdiction"),
                    "source": item.get("source") or item.get("_source"),
                    "ipc": item.get("ipc"),
                    "cpc": item.get("cpc"),
                }
                for item in patents[:per_actor_limit]
            ],
            "omitted_patent_count": max(0, len(patents) - per_actor_limit),
        }
    return json.dumps(compact, ensure_ascii=False, indent=2)


def _write_patent_agent_log(
    *,
    state: AnalysisState,
    run_id: str,
    prompt_variant: str,
    patent_raw: dict,
    patent_raw_for_prompt: str,
    system_prompt: str | None = None,
    user_prompt: str | None = None,
    llm_raw_response: str | None = None,
    patent_analysis: list | None = None,
    patent_maps: dict | None = None,
    rendered_map_paths: dict | None = None,
    error: str | None = None,
) -> None:
    """Write API/search and LLM output logs without including API keys."""
    try:
        output_dir = _patent_log_dir()
        output_dir.mkdir(parents=True, exist_ok=True)
        prefix = f"{run_id}_{_safe_filename(state.get('company_name') or state.get('domain'))}"
        payload = {
            "run_id": run_id,
            "timestamp_utc": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "input": {
                "domain": state.get("domain"),
                "reference_year": state.get("reference_year"),
                "category_hints": state.get("category_hints", []),
                "company_name": state.get("company_name"),
                "company_profile": state.get("company_profile"),
                "related_companies": state.get("related_companies"),
                "patent_map_generation": "always",
            },
            "prompt_variant": prompt_variant,
            "provider_summary": _summarize_portfolios(patent_raw),
            "patent_raw_data": patent_raw,
            "patent_raw_for_prompt": patent_raw_for_prompt,
            "llm_request": {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
            },
            "llm_response": {
                "raw_content": llm_raw_response,
            },
            "patent_analysis": patent_analysis or [],
            "patent_maps": patent_maps or {},
            "rendered_map_paths": rendered_map_paths or {},
            "error": error,
        }
        path = output_dir / f"{prefix}_patent_agent_log.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        latest = output_dir / "latest_patent_agent_log.json"
        latest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        if patent_maps:
            (output_dir / f"{prefix}_actor_similarity_map.json").write_text(
                json.dumps((patent_maps or {}).get("actor_similarity_map", []), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        print(f"[Patent Agent] API/search log 저장: {path}")
    except Exception as log_error:
        print(f"[Patent Agent] ⚠️ log 저장 실패: {log_error}")


# ── LangGraph 노드 함수 ───────────────────────────────────────

def run_patent_agent(state: AnalysisState) -> dict:
    """
    Patent Data Agent 노드.
    Patent provider 데이터 수집 → LLM 분석 → patent_analysis 반환
    """
    print("\n[Patent Agent] 시작")
    messages = []
    run_id = os.getenv("PATENT_AGENT_RUN_ID") or os.getenv("AGENT_RUN_ID") or datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    patent_raw = {}
    patent_raw_for_prompt = ""
    prompt_variant = PATENT_ANALYSIS_METHOD
    system_prompt = None
    user_prompt = None
    llm_raw_response = None

    try:
        # ① Patent provider 데이터 수집
        print("[Patent Agent] Patent provider 데이터 수집 중...")
        if _is_company_portfolio_mode(state):
            patent_raw = _collect_company_patent_data(
                company_name=state.get("company_name") or state["domain"],
                company_profile=state.get("company_profile") or state["domain"],
                domain=state["domain"],
                category_hints=state.get("category_hints", []),
                related_companies=state.get("related_companies"),
            )
            prompt_variant = "C_company_portfolio"
        else:
            patent_raw = _collect_patent_data(
                state["domain"], state.get("category_hints", [])
            )
            prompt_variant = PATENT_ANALYSIS_METHOD

        # ② Claude/Ollama 에게 분석 요청
        llm = get_llm(max_tokens=16384)  # patent_map cross-actor 응답이 길어 짤림 → 4096 → 16384
        prompt = load_prompt("patent_agent", prompt_variant)
        patent_raw_for_prompt = _compact_patent_raw_for_prompt(patent_raw)

        _write_patent_agent_log(
            state=state,
            run_id=run_id,
            prompt_variant=prompt_variant,
            patent_raw=patent_raw,
            patent_raw_for_prompt=patent_raw_for_prompt,
        )

        rendered = prompt.render_user(
            domain=state["domain"],
            reference_year=state["reference_year"],
            category_hints=state.get("category_hints", []),
            company_name=patent_raw.get("company", {}).get("name", state.get("company_name") or ""),
            company_profile=patent_raw.get("company", {}).get("profile", state.get("company_profile") or ""),
            related_companies=patent_raw.get("related_companies", state.get("related_companies") or []),
            patent_raw=patent_raw_for_prompt,
        )

        # Company Scenario + Strategic Direction (상위 컨텍스트) — 모든 분석의 기반,
        # prompt 맨 앞에 prepend 하여 LLM 이 가장 먼저 인식하도록.
        upper_block = _format_upper_context(
            state.get("company_scenario"),
            state.get("strategic_direction"),
        )

        # Horizon 인식 가이드 (분석 가이드)
        horizon_guide = f"""

[Horizon 인식 — 권장 (강제 X)]
reference_year={state['reference_year']} 는 로드맵 horizon 의 **목표 종료 연도** 이다.
후보 기술 발굴 시 다음을 고려:
- 단기 (TRL 7+, 양산 가까운) / 중기 (TRL 5-6, 프로토타입) / 장기 (TRL 3-4, R&D) 가
  골고루 분포하도록 권장 — 모든 후보를 한 TRL 대역으로 채우지 말 것.
- 단 도메인 특성상 한 대역에 집중되는 게 자연스러우면 그대로 — 정직한 분석 우선.
- 즉 "정직한 분석" ≫ "TRL 분포 균형". 둘이 충돌하면 정직성 선호, 비슷하면 분포 권장.
"""

        feedback_block = _format_orchestrator_feedback(state.get("orchestrator_feedback"))

        # 최종 user_prompt: [상위 컨텍스트] → [본문] → [horizon 가이드] → [feedback]
        user_prompt = upper_block + rendered + horizon_guide + feedback_block
        # Orchestrator REVISE feedback 을 user_prompt 끝에 append
        user_prompt = user_prompt + _format_orchestrator_feedback(state.get("orchestrator_feedback"))
        system_prompt = prompt.system
        user_prompt = _append_patent_map_generation_note(user_prompt)

        _write_patent_agent_log(
            state=state,
            run_id=run_id,
            prompt_variant=prompt_variant,
            patent_raw=patent_raw,
            patent_raw_for_prompt=patent_raw_for_prompt,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )

        if patent_raw.get("analysis_mode") == "company_portfolio" and not USE_MOCK_PATENT:
            portfolios = patent_raw.get("company_portfolios") or {}
            real_patent_count = sum(
                len((p or {}).get("recent_patents") or [])
                for p in portfolios.values()
                if not (p or {}).get("_mock")
            )
            if real_patent_count == 0:
                errors = {
                    actor: data.get("error")
                    for actor, data in portfolios.items()
                    if isinstance(data, dict) and data.get("error")
                }
                raise RuntimeError(
                    "Company portfolio mode requires real patent data, but no real patents were collected. "
                    f"Errors: {errors}"
                )

        print(f"[Patent Agent] prompt variant: {prompt.variant}")
        print("[Patent Agent] LLM 분석 요청 중...")

        def _validate_schema(parsed: dict) -> str | None:
            """Return error reason if schema invalid, else None."""
            if not isinstance(parsed, dict):
                return f"top-level must be dict, got {type(parsed).__name__}"
            pa = parsed.get("patent_analysis")
            if not isinstance(pa, list):
                return (
                    f"`patent_analysis` must be a JSON ARRAY of candidate objects, "
                    f"got {type(pa).__name__}"
                )
            if not pa:
                return "`patent_analysis` array is empty (need 8-12 candidates)"
            non_dict = [i for i, x in enumerate(pa) if not isinstance(x, dict)]
            if non_dict:
                return f"`patent_analysis` contains non-object items at indices {non_dict[:5]}"
            return None

        max_attempts = 2
        retry_user_prompt = user_prompt
        result = {}
        for attempt in range(1, max_attempts + 1):
            response = llm.invoke(
                [
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=retry_user_prompt),
                ]
            )
            llm_raw_response = response.content
            result = _extract_json(llm_raw_response)
            err = _validate_schema(result)
            if err is None:
                break
            print(f"[Patent Agent] ⚠️ LLM 응답 schema invalid (attempt {attempt}/{max_attempts}): {err}")
            if attempt < max_attempts:
                # 재요청 시 환각 패턴을 명시적으로 짚어줌
                retry_user_prompt = (
                    user_prompt
                    + f"\n\n[Retry Reason — Strict Schema Required]\n"
                    + f"Your previous response was invalid: {err}\n"
                    + "Output `patent_analysis` MUST be a JSON ARRAY `[{...}, {...}, ...]` "
                    + "with 8-12 candidate OBJECTS — each with tech_id, name, category, "
                    + "patent_score, etc. NEVER a single object with summary/key_technologies/"
                    + "trend_insights keys.\n"
                )

        patent_analysis = result.get("patent_analysis", [])
        patent_maps = result.get("patent_maps", {})
        rendered_map_paths = {}
        if patent_maps:
            try:
                from tools.patent_map_renderer import render_patent_maps

                render_prefix = f"{run_id}_{_safe_filename(state.get('company_name') or state.get('domain'))}"
                rendered_map_paths = render_patent_maps(
                    patent_maps,
                    str(_patent_log_dir()),
                    render_prefix,
                )
            except Exception as render_error:
                print(f"[Patent Agent] ⚠️ map 렌더링 실패: {render_error}")

        _write_patent_agent_log(
            state=state,
            run_id=run_id,
            prompt_variant=prompt_variant,
            patent_raw=patent_raw,
            patent_raw_for_prompt=patent_raw_for_prompt,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            llm_raw_response=llm_raw_response,
            patent_analysis=patent_analysis,
            patent_maps=patent_maps,
            rendered_map_paths=rendered_map_paths,
        )

        print(f"[Patent Agent] 완료: {len(patent_analysis)}개 기술 식별")
        messages.append(AIMessage(content=f"Patent Agent: {len(patent_analysis)}개 후보 기술 분석 완료"))

        return {
            "patent_raw_data": patent_raw,
            "patent_analysis": patent_analysis,
            "patent_maps": patent_maps,
            "patent_prompt": {**prompt.metadata, "patent_map_generation": "always"},
            "messages": messages,
            "error": None,
        }

    except Exception as e:
        err_msg = f"Patent Agent 오류: {str(e)}"
        print(f"[Patent Agent] ❌ {err_msg}")
        if patent_raw:
            _write_patent_agent_log(
                state=state,
                run_id=run_id,
                prompt_variant=prompt_variant,
                patent_raw=patent_raw,
                patent_raw_for_prompt=patent_raw_for_prompt,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                llm_raw_response=llm_raw_response,
                error=err_msg,
            )
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
