"""
single_agent_baseline.py
────────────────────────
Single-agent baseline for comparing against the current multi-agent pipeline.

This agent intentionally performs the work of Technology Analyst, Market
Analyst, Roadmap Planner, Investment Strategist, and Orchestrator Review in one
LLM call. It writes the same artifact shapes as the multi-agent pipeline so the
demo and downstream comparison scripts can reuse the existing UI.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage

from llm_factory import get_llm


_SYSTEM = """You are a single-agent technology roadmap baseline.

You must do, in one response, the jobs that are normally split across:
1. Patent/technology analysis
2. Market analysis
3. Roadmap planning
4. Investment strategy
5. Final TRM review/report

Return ONLY valid JSON. Do not use markdown fences.
Use the user's company context, strategic priorities, category hints, and
related companies if provided. If related companies are empty, infer reasonable
competitors/partners from the domain.

Important:
- This is a single-agent baseline, so do not claim that you called external APIs.
- If use_patent_map is true, explicitly reason about actor relationships as a
  proxy for patent-map-informed prioritization.
- If use_patent_map is false, prioritize only company fit, technology need, and
  market attractiveness, without actor-network centrality.
"""

_TOOL_AUGMENTED_SYSTEM = """You are a tool-augmented single-agent technology roadmap baseline.

You still act as one single reasoning agent: do not split the work into Patent
Agent, Market Agent, Roadmap Agent, or Investment Agent roles.

However, the caller has collected external tool data for you:
- patent portfolios from the configured patent provider
- market intelligence from the configured web search provider
- optional actor_similarity_map context

Use this tool evidence to produce one integrated TRM output. Return ONLY valid
JSON. Do not use markdown fences.

Fairness/comparison rules:
- This is not the multi-agent pipeline. Do not claim separate agents performed
  intermediate reasoning.
- If use_patent_map is true, actor_similarity_map may inform ecosystem and
  related-actor interpretation.
- Do not add an automatic numeric bonus merely because a map edge exists.
- If evidence is thin or a tool returned an error, say so in rationale/data
  quality rather than hallucinating precise evidence.
"""

_TOOL_PLANNER_SYSTEM = """You plan API/tool queries for a tool-augmented single-agent TRM run.
Return ONLY valid JSON.
Choose related companies and seed technologies that should be researched.
The seed technologies should be concise English search phrases.
"""


def _strip_think(text: str) -> str:
    return re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE).strip()


def _extract_json(text: str) -> dict:
    text = _strip_think(text)
    cleaned = re.sub(r"```(?:json)?\s*", "", text).replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]+\}", cleaned)
        if m:
            return json.loads(m.group())
        raise


def _num(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _fallback_candidates(domain: str, reference_year: int, use_patent_map: Optional[bool]) -> List[dict]:
    suffix = "actor-network weighted" if use_patent_map else "direct strategic-fit weighted"
    seeds = _fallback_seed_technologies(domain, ["Architecture", "Process", "Equipment", "Software", "Material"])
    names = [
        (
            f"S{idx:02d}",
            seed.get("name", f"{domain} technology candidate {idx}"),
            seed.get("category", "Architecture"),
            4 + (idx % 3),
            max(70.0, 84.0 - idx * 2.0),
        )
        for idx, seed in enumerate(seeds[:5], 1)
    ]
    boom = f"{max(reference_year - 2, 2027)} Q4"
    out = []
    for tech_id, name, category, trl, score in names:
        patent_score = score - 4 if use_patent_map is False else score
        market_score = score + 1 if use_patent_map is False else score - 1
        out.append({
            "tech_id": tech_id,
            "name": name,
            "category": category,
            "trl": trl,
            "patent_score": round(patent_score, 1),
            "market_score": round(market_score, 1),
            "final_score": round((patent_score * 0.45) + (market_score * 0.55), 1),
            "expected_market_boom_quarter": boom,
            "dependency_hints": [],
            "rationale": f"Single-agent fallback for {domain}; scoring is {suffix}.",
        })
    return out


def _fallback_related_companies(domain: str, company_name: Optional[str]) -> list[str]:
    text = f"{domain} {company_name or ''}".lower()
    if "llm" in text or "language model" in text or "ai platform" in text:
        return ["Google", "Microsoft", "OpenAI", "Meta", "Anthropic"]
    if "battery" in text or "cathode" in text:
        return ["LG Energy Solution", "Samsung SDI", "SK On", "CATL", "Panasonic"]
    if "adas" in text or "autonomous" in text or "mobility" in text:
        return ["Mobileye", "Waymo", "NVIDIA", "Bosch", "Continental"]
    if "semiconductor" in text or "gpu" in text:
        return ["AMD", "Intel", "TSMC", "Qualcomm", "Broadcom"]
    return []


def _fallback_seed_technologies(domain: str, category_hints: Optional[list[str]] = None) -> list[dict]:
    text = domain.lower()
    if "llm" in text or "language model" in text or "ai platform" in text:
        names = [
            "LLM inference optimization",
            "retrieval augmented generation platform",
            "agentic AI workflow orchestration",
            "multimodal foundation model",
            "AI safety and evaluation pipeline",
        ]
    elif "battery" in text or "cathode" in text:
        names = [
            "high nickel cathode material",
            "single crystal cathode manufacturing",
            "dry electrode process",
            "solid state battery interface material",
            "battery recycling precursor recovery",
        ]
    elif "adas" in text or "autonomous" in text:
        names = [
            "sensor fusion perception stack",
            "vision transformer ADAS perception",
            "software defined vehicle control platform",
            "lidar radar camera fusion",
            "drive policy simulation validation",
        ]
    elif "semiconductor" in text or "gpu" in text:
        names = [
            "advanced chiplet packaging",
            "backside power delivery network",
            "AI accelerator architecture",
            "HBM memory interconnect",
            "data center cooling for AI infrastructure",
        ]
    else:
        names = [f"{domain} technology platform", f"{domain} automation", f"{domain} optimization"]
    categories = category_hints or []
    return [
        {
            "tech_id": f"TS{i:02d}",
            "name": name,
            "category": categories[(i - 1) % len(categories)] if categories else "Architecture",
            "reason": "fallback seed technology for tool collection",
        }
        for i, name in enumerate(names, 1)
    ]


def _plan_tool_queries(
    *,
    domain: str,
    reference_year: int,
    category_hints: Optional[List[str]],
    problem_frame: dict,
    company_name: Optional[str],
    company_profile: Optional[str],
    related_companies: Optional[List[str]],
) -> dict:
    fallback_related = related_companies or _fallback_related_companies(domain, company_name)
    fallback_seeds = _fallback_seed_technologies(domain, category_hints)
    payload = {
        "domain": domain,
        "reference_year": reference_year,
        "category_hints": category_hints or [],
        "problem_frame": problem_frame or {},
        "company_name": company_name,
        "company_profile": company_profile,
        "related_companies_from_user": related_companies or [],
    }
    schema = {
        "related_companies": ["Company A"],
        "seed_technologies": [
            {
                "tech_id": "TS01",
                "name": "English search phrase",
                "category": "Architecture",
                "reason": "why this should be researched",
            }
        ],
        "planning_rationale": "short explanation",
    }
    try:
        llm = get_llm(max_tokens=2048, json_mode=True, temperature=0.1)
        resp = llm.invoke([
            SystemMessage(content=_TOOL_PLANNER_SYSTEM),
            HumanMessage(content=(
                "Plan patent and market API collection for this TRM input.\n"
                "Return 4-7 related companies and 5-8 seed technologies.\n"
                f"Schema:\n{json.dumps(schema, ensure_ascii=False, indent=2)}\n\n"
                f"Input:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
            )),
        ])
        data = _extract_json(resp.content if hasattr(resp, "content") else str(resp))
    except Exception as e:
        data = {"tool_planning_error": str(e)}

    planned_related = [
        str(item).strip()
        for item in data.get("related_companies", [])
        if str(item).strip()
    ] if isinstance(data.get("related_companies"), list) else []
    seeds = [
        item for item in data.get("seed_technologies", [])
        if isinstance(item, dict) and item.get("name")
    ] if isinstance(data.get("seed_technologies"), list) else []

    if not planned_related:
        planned_related = fallback_related
    if not seeds:
        seeds = fallback_seeds

    # Keep tool use bounded for batch runs.
    return {
        "related_companies": planned_related[:6],
        "seed_technologies": seeds[:8],
        "planning_rationale": data.get("planning_rationale") or "Tool query plan generated for single-agent baseline.",
        "fallback_used": bool(data.get("tool_planning_error")),
        "error": data.get("tool_planning_error"),
    }


def _collect_tool_context(
    *,
    domain: str,
    company_name: Optional[str],
    related_companies: list[str],
    seed_technologies: list[dict],
    use_patent_map: Optional[bool],
) -> dict:
    """Collect patent/market data in the tech_analysis_agent process."""
    tech_dir = Path(__file__).resolve().parents[2] / "tech_analysis_agent"
    if not tech_dir.exists():
        return {"error": f"tech_analysis_agent not found at {tech_dir}"}

    payload = {
        "domain": domain,
        "company_name": company_name,
        "related_companies": related_companies,
        "seed_technologies": seed_technologies,
        "use_patent_map": bool(use_patent_map),
    }
    snippet = r"""
import json, os, re, sys
from tools.patent_tools import PatentPortfolioTool
from tools.market_tools import MarketIntelligenceTool

payload = json.loads(sys.stdin.read())
domain = payload.get("domain") or ""
company_name = payload.get("company_name") or ""
related_companies = payload.get("related_companies") or []
seed_technologies = payload.get("seed_technologies") or []
use_patent_map = bool(payload.get("use_patent_map"))

def tokens(text):
    return {
        t.lower()
        for t in re.findall(r"[A-Za-z0-9가-힣]+", str(text or ""))
        if len(t) >= 3
    }

def patent_text(portfolio):
    parts = []
    for key in ("recent_patents", "top_patents", "patents"):
        for item in portfolio.get(key) or []:
            if isinstance(item, dict):
                parts.extend([
                    item.get("title", ""),
                    item.get("abstract", ""),
                    item.get("summary", ""),
                ])
    return " ".join(parts)

companies = []
for c in [company_name, *related_companies]:
    c = str(c or "").strip()
    if c and c not in companies:
        companies.append(c)

patent_tool = PatentPortfolioTool()
patent_portfolios = {}
for company in companies:
    try:
        patent_portfolios[company] = patent_tool.collect_company_portfolio(company, domain)
    except Exception as e:
        patent_portfolios[company] = {"company": company, "error": str(e)}

center_tokens = tokens(patent_text(patent_portfolios.get(company_name, {})))
seed_terms = {}
for seed in seed_technologies:
    name = seed.get("name", "")
    seed_terms[name] = tokens(name)

edges = []
for company in companies:
    if not company_name or company == company_name:
        continue
    text = patent_text(patent_portfolios.get(company, {}))
    company_tokens = tokens(text)
    shared_token_count = len(center_tokens & company_tokens)
    matched_areas = []
    for seed_name, terms in seed_terms.items():
        if terms and (terms & company_tokens):
            matched_areas.append(seed_name)
    raw = min(0.95, 0.35 + shared_token_count * 0.015 + len(matched_areas) * 0.08)
    if matched_areas or shared_token_count:
        edges.append({
            "center_actor": company_name,
            "related_actor": company,
            "similarity": round(raw, 3),
            "edge_weight": round(raw, 3),
            "shared_technology_areas": matched_areas[:6],
            "evidence_level": "tool_single_heuristic",
        })

market_tool = MarketIntelligenceTool()
actor_context = {}
if use_patent_map:
    actor_context = {
        "center_actor": company_name,
        "related_actors": [edge["related_actor"] for edge in edges[:5]],
        "shared_technology_areas": [
            area
            for edge in edges[:5]
            for area in edge.get("shared_technology_areas", [])
        ][:8],
    }

market_raw = {"domain": domain, "use_patent_map": use_patent_map, "technologies": {}}
for seed in seed_technologies:
    tech_id = seed.get("tech_id") or seed.get("id") or seed.get("name")
    name = seed.get("name") or str(tech_id)
    try:
        market_raw["technologies"][tech_id] = market_tool.collect_full_signal(
            name,
            domain,
            actor_context=actor_context if use_patent_map else {},
        )
    except Exception as e:
        market_raw["technologies"][tech_id] = {
            "tech_name": name,
            "domain": domain,
            "actor_context": actor_context if use_patent_map else {},
            "error": str(e),
        }
    market_raw["technologies"][tech_id]["seed"] = seed

out = {
    "provider_notes": {
        "patent_provider": getattr(patent_tool, "provider", "unknown"),
        "market_mock": getattr(market_tool, "use_mock", None),
        "use_patent_map": use_patent_map,
    },
    "patent_raw_data": {
        "company_portfolios": patent_portfolios,
        "companies": companies,
    },
    "patent_maps": {
        "actor_similarity_map": edges if use_patent_map else [],
    },
    "market_raw_data": market_raw,
}
print(json.dumps(out, ensure_ascii=False))
"""
    env = os.environ.copy()
    if use_patent_map is not None:
        env["USE_PATENT_MAP"] = "true" if use_patent_map else "false"
    try:
        proc = subprocess.run(
            [sys.executable, "-c", snippet],
            cwd=str(tech_dir),
            env=env,
            input=json.dumps(payload, ensure_ascii=False),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=int(os.getenv("TOOL_SINGLE_COLLECTION_TIMEOUT_SEC", "900") or 900),
        )
        if proc.returncode != 0:
            return {
                "error": f"tool collection subprocess failed rc={proc.returncode}",
                "stderr": proc.stderr[-4000:],
                "stdout": proc.stdout[-1000:],
            }
        stdout = proc.stdout.strip()
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]*\}\s*$", stdout)
            if match:
                return json.loads(match.group())
            raise
    except Exception as e:
        return {"error": str(e)}


def _compact_tool_context(tool_context: dict) -> dict:
    """Keep full tool data in outputs but send a compact version to the LLM."""
    if not isinstance(tool_context, dict):
        return {}
    patent_raw = tool_context.get("patent_raw_data") or {}
    portfolios = patent_raw.get("company_portfolios") or {}
    compact_portfolios = {}
    for company, portfolio in portfolios.items():
        if not isinstance(portfolio, dict):
            continue
        patents = []
        for key in ("recent_patents", "top_patents", "patents"):
            for item in portfolio.get(key) or []:
                if isinstance(item, dict):
                    patents.append({
                        "title": item.get("title", ""),
                        "date": item.get("date") or item.get("application_date") or item.get("publication_date"),
                        "abstract": str(item.get("abstract", ""))[:350],
                        "applicant": item.get("applicant") or item.get("assignee") or company,
                    })
                if len(patents) >= 5:
                    break
            if len(patents) >= 5:
                break
        compact_portfolios[company] = {
            "source": portfolio.get("_source") or portfolio.get("source"),
            "_mock": portfolio.get("_mock"),
            "error": portfolio.get("error"),
            "recent_patents": patents,
        }

    market_raw = tool_context.get("market_raw_data") or {}
    compact_market = {
        "domain": market_raw.get("domain"),
        "use_patent_map": market_raw.get("use_patent_map"),
        "technologies": {},
    }
    for tech_id, data in (market_raw.get("technologies") or {}).items():
        if not isinstance(data, dict):
            continue

        def compact_items(key: str) -> list[dict]:
            out = []
            for item in data.get(key) or []:
                if isinstance(item, dict):
                    out.append({
                        "title": item.get("title", ""),
                        "url": item.get("url", ""),
                        "content": str(item.get("content", ""))[:300],
                        "score": item.get("score", 0),
                    })
                if len(out) >= 2:
                    break
            return out

        compact_market["technologies"][tech_id] = {
            "tech_name": data.get("tech_name"),
            "seed": data.get("seed", {}),
            "actor_context": data.get("actor_context", {}),
            "market_reports": compact_items("market_reports"),
            "tam_sam_som_data": compact_items("tam_sam_som_data"),
            "cagr_forecast_data": compact_items("cagr_forecast_data"),
            "competitive_data": compact_items("competitive_data"),
            "timeline_data": compact_items("timeline_data"),
            "error": data.get("error"),
            "_mock": data.get("_mock"),
        }

    return {
        "provider_notes": tool_context.get("provider_notes") or {},
        "patent_raw_data": {
            "companies": patent_raw.get("companies") or list(compact_portfolios.keys()),
            "company_portfolios": compact_portfolios,
        },
        "patent_maps": tool_context.get("patent_maps") or {},
        "market_raw_data": compact_market,
        "error": tool_context.get("error"),
    }


def _fallback_roadmap(candidates: List[dict], reference_year: int) -> List[dict]:
    start_year = max(reference_year - 4, 2026)
    roadmap = []
    for idx, c in enumerate(candidates):
        start_q = f"{start_year + min(idx // 2, 2)} Q{1 + (idx % 2) * 2}"
        target_q = f"{min(reference_year, start_year + 2 + idx // 2)} Q4"
        roadmap.append({
            "tech_id": c.get("tech_id"),
            "name": c.get("name"),
            "phase_name": "Single-Agent Baseline Roadmap",
            "start_q": start_q,
            "target_q": target_q,
            "prerequisites": c.get("dependency_hints") or [],
            "lead_time_quarters": 8 + idx,
            "justification": "Single agent estimated sequencing from TRL, strategy fit, and market timing.",
        })
    return roadmap


def _year_idx_from_quarter(value: Any, reference_year: int) -> Optional[int]:
    match = re.search(r"(20\d{2})", str(value or ""))
    if not match:
        return None
    start_year = max(reference_year - 4, 2026)
    return max(1, min(5, int(match.group(1)) - start_year + 1))


def _fallback_strategy(
    candidates: List[dict],
    roadmap: List[dict],
    total_budget: float,
) -> tuple[List[dict], List[dict]]:
    stages = [
        {
            "stage": "Stage 1: Foundation",
            "period": "2026 Q1-2027 Q4",
            "tech_ids": [c.get("tech_id") for c in candidates[:2]],
        },
        {
            "stage": "Stage 2: Scale-up",
            "period": "2028 Q1-2029 Q2",
            "tech_ids": [c.get("tech_id") for c in candidates[2:4]],
        },
        {
            "stage": "Stage 3: Platform expansion",
            "period": "2029 Q3-2030 Q4",
            "tech_ids": [c.get("tech_id") for c in candidates[4:]],
        },
    ]
    by_id = {c.get("tech_id"): c for c in candidates}
    strategy = []
    ratios = [0.35, 0.4, 0.25]
    for stage, ratio in zip(stages, ratios):
        tech_investments = []
        for tech_id in stage["tech_ids"]:
            c = by_id.get(tech_id, {})
            tech_investments.append({
                "tech_id": tech_id,
                "name": c.get("name"),
                "recommended_investment_tier": "Tier 1" if _num(c.get("final_score"), 0) >= 80 else "Tier 2",
                "investment_attractiveness": "high",
                "investment_urgency": "high" if _num(c.get("trl"), 0) >= 4 else "medium",
                "investment_scope": "R&D + ecosystem partnership",
                "evaluation_scores": {
                    "market_opportunity": 4,
                    "strategic_fit": 5,
                    "executability": 4,
                    "uncertainty": 3,
                    "urgency": 4,
                },
                "recommended_action": "Prioritize as part of the single-agent baseline portfolio.",
                "rationale": ["High relevance to the company scenario and roadmap horizon."],
                "major_risks": ["Single-agent estimate; no independent agent critique."],
                "resource_focus": ["Architecture validation", "partner ecosystem", "software enablement"],
            })
        strategy.append({
            "stage": stage["stage"],
            "period": stage["period"],
            "stage_budget_ratio": ratio,
            "stage_estimated_usd": total_budget * ratio if total_budget else 0,
            "stage_assessment": "Single-agent integrated investment judgment.",
            "tech_investments": tech_investments,
        })
    return stages, strategy


def _normalize_roadmap_schema(roadmap: List[dict], reference_year: int) -> None:
    for item in roadmap:
        if not isinstance(item, dict):
            continue
        if not item.get("year_idx_start"):
            item["year_idx_start"] = _year_idx_from_quarter(item.get("start_q"), reference_year) or 1
        if not item.get("year_idx_target"):
            item["year_idx_target"] = (
                _year_idx_from_quarter(item.get("target_q"), reference_year)
                or item.get("year_idx_start")
                or 1
            )
        if item["year_idx_target"] < item["year_idx_start"]:
            item["year_idx_target"] = item["year_idx_start"]


def _normalize_investment_schema(
    strategy: List[dict],
    candidates: List[dict],
    total_budget: float,
) -> None:
    by_id = {c.get("tech_id"): c for c in candidates if isinstance(c, dict)}
    default_ratios = [0.35, 0.4, 0.25]
    for stage_idx, stage in enumerate(strategy):
        if not isinstance(stage, dict):
            continue
        ratio = _num(stage.get("stage_budget_ratio"), default_ratios[min(stage_idx, len(default_ratios) - 1)])
        stage["stage_budget_ratio"] = ratio
        stage_budget = _num(stage.get("stage_estimated_usd"), total_budget * ratio if total_budget else 0)
        stage["stage_estimated_usd"] = stage_budget
        investments = [ti for ti in (stage.get("tech_investments") or []) if isinstance(ti, dict)]
        score_sum = sum(max(1.0, _num(by_id.get(ti.get("tech_id"), {}).get("final_score"), 50)) for ti in investments)
        for ti in investments:
            tech = by_id.get(ti.get("tech_id"), {})
            ti.setdefault("name", tech.get("name", ""))
            if not ti.get("tech_budget_usd") and stage_budget and score_sum:
                weight = max(1.0, _num(tech.get("final_score"), 50)) / score_sum
                ti["tech_budget_usd"] = round(stage_budget * weight, 2)
            ti.setdefault("tech_budget_rationale", "Allocated within the single-agent stage budget according to normalized candidate priority.")

            scores = ti.get("evaluation_scores") if isinstance(ti.get("evaluation_scores"), dict) else {}
            if scores:
                scores.setdefault("market_size_growth", scores.get("market_opportunity", 3))
                scores.setdefault("tech_readiness", scores.get("executability", 3))
                scores.setdefault("tech_risk", scores.get("uncertainty", 3))
                scores.setdefault("competitive_advantage", scores.get("strategic_fit", 3))
                scores.setdefault("development_urgency", scores.get("urgency", 3))
            else:
                ti["evaluation_scores"] = {
                    "market_size_growth": 3,
                    "tech_readiness": 3,
                    "tech_risk": 3,
                    "competitive_advantage": 3,
                    "development_urgency": 3,
                }


def _normalize_result(
    data: dict,
    domain: str,
    reference_year: int,
    total_budget: float,
    use_patent_map: Optional[bool],
    tool_context: Optional[dict] = None,
    tool_planning: Optional[dict] = None,
    baseline_mode: str = "single_agent",
    generation_error: Optional[str] = None,
) -> Dict[str, Any]:
    def _component_score(value: Any, default: float) -> float:
        score = _num(value, default)
        if 0 < score <= 1:
            score *= 100
        elif 1 < score <= 10:
            score *= 10
        return round(max(0, min(100, score)), 1)

    def _final_score(value: Any, default: float) -> float:
        score = _num(value, default)
        if 0 < score <= 1:
            score *= 100
        elif 1 < score <= 10:
            score *= 10
        elif 10 < score <= 20:
            score *= 5
        return round(max(0, min(100, score)), 1)

    market_context = data.get("market_context") if isinstance(data.get("market_context"), dict) else {}
    market_context.setdefault("target_market", domain)
    market_context.setdefault("expected_boom_quarter", f"{max(reference_year - 2, 2027)} Q4")
    market_context.setdefault("single_agent_baseline", True)
    market_context.setdefault("tool_augmented", baseline_mode == "tool_augmented_single_agent")
    market_context.setdefault("use_patent_map", use_patent_map)
    if generation_error:
        market_context["single_agent_generation_error"] = generation_error

    candidates = data.get("tech_candidates") if isinstance(data.get("tech_candidates"), list) else []
    if not candidates:
        candidates = _fallback_candidates(domain, reference_year, use_patent_map)
    for idx, c in enumerate(candidates, start=1):
        if not isinstance(c, dict):
            continue
        c.setdefault("tech_id", f"S{idx:02d}")
        if not c.get("name") and c.get("technology_name"):
            c["name"] = c.get("technology_name")
        c.setdefault("name", f"Single-agent technology candidate {idx}")
        c.setdefault("category", "Architecture")
        c["trl"] = int(_num(c.get("trl"), 4))
        c["patent_score"] = _component_score(c.get("patent_score"), 70)
        c["market_score"] = _component_score(c.get("market_score"), 70)
        model_final = _final_score(
            c.get("final_score"),
            (c["patent_score"] + c["market_score"]) / 2,
        )
        if c["patent_score"] > 0 and c["market_score"] > 0:
            c["final_score"] = round((c["patent_score"] * 0.45) + (c["market_score"] * 0.55), 1)
        else:
            c["final_score"] = model_final
        c.setdefault("expected_market_boom_quarter", market_context["expected_boom_quarter"])
        c.setdefault("dependency_hints", [])
        c.setdefault("rationale", "Generated by single-agent baseline.")

    roadmap = data.get("planned_roadmap") if isinstance(data.get("planned_roadmap"), list) else []
    if not roadmap:
        roadmap = _fallback_roadmap(candidates, reference_year)
    else:
        # Some models return only a phase-level roadmap. Keep their items, but
        # supplement missing candidate IDs so downstream comparison has a
        # complete candidate-to-roadmap path.
        fallback_by_id = {
            item.get("tech_id"): item
            for item in _fallback_roadmap(candidates, reference_year)
            if isinstance(item, dict)
        }
        seen = {
            item.get("tech_id")
            for item in roadmap
            if isinstance(item, dict) and item.get("tech_id")
        }
        for c in candidates:
            tech_id = c.get("tech_id") if isinstance(c, dict) else None
            if tech_id and tech_id not in seen and tech_id in fallback_by_id:
                supplemental = dict(fallback_by_id[tech_id])
                supplemental["justification"] = (
                    supplemental.get("justification", "")
                    + " Supplemental item added because the single-agent response omitted this candidate from the roadmap."
                ).strip()
                roadmap.append(supplemental)
                seen.add(tech_id)
    _normalize_roadmap_schema(roadmap, reference_year)

    stages = data.get("stages") if isinstance(data.get("stages"), list) else []
    strategy = data.get("investment_strategy") if isinstance(data.get("investment_strategy"), list) else []
    strategy_tech_ids = {
        ti.get("tech_id")
        for stage in strategy
        if isinstance(stage, dict)
        for ti in (stage.get("tech_investments") or [])
        if isinstance(ti, dict) and ti.get("tech_id")
    }
    candidate_ids = {
        c.get("tech_id")
        for c in candidates
        if isinstance(c, dict) and c.get("tech_id")
    }
    if not stages or not strategy or not candidate_ids.issubset(strategy_tech_ids):
        stages, strategy = _fallback_strategy(candidates, roadmap, total_budget)
    _normalize_investment_schema(strategy, candidates, total_budget)

    report = data.get("orchestrator_report") if isinstance(data.get("orchestrator_report"), dict) else {}
    report.setdefault("executive_summary", "Single-agent baseline generated an integrated TRM without inter-agent decomposition.")
    report.setdefault("technology_strategy", "Prioritize technologies that match the company strategy and market timing.")
    report.setdefault("roadmap_structure", "Sequence foundation, scale-up, and platform expansion work toward the target year.")
    report.setdefault("investment_strategy", "Allocate capital by stage according to urgency, strategic fit, and execution risk.")
    report.setdefault("trend_alignment", "The portfolio is aligned to AI infrastructure, accelerated computing, and ecosystem expansion.")
    report.setdefault("feasibility_and_risk", "Residual risk is higher than the multi-agent pipeline because critique and tool specialization are collapsed.")
    report.setdefault("expected_outcomes", "A comparable baseline output for multi-agent vs single-agent evaluation.")

    review = data.get("review") if isinstance(data.get("review"), dict) else {}
    review.setdefault("decision", "ACCEPT")
    review.setdefault("issues", [])
    review.setdefault("refinement", {"rerun_agents": [], "feedback": []})
    review.setdefault("diagnostic_summary", "")
    review.setdefault("trm_assessment", {
        "feasibility": {"budget_feasible": True, "schedule_feasible": True, "comment": "Single-agent estimate."},
        "sequencing": {"dependency_valid": True, "comment": "Sequencing is internally estimated."},
        "strategic_alignment": {"company_fit": 0.82, "future_trend_alignment": 0.8, "comment": "Aligned to scenario priorities."},
        "investment_rationality": {"comment": "Budget tiers are estimated inside one model call.", "over_invested": [], "under_invested": []},
        "portfolio_balance": {"short_long_balance": 0.75, "risk_balance": 0.7, "comment": "Balanced but less independently challenged."},
    })
    if not isinstance(review.get("report"), dict) or not any(review.get("report", {}).values()):
        review["report"] = report

    return {
        "market_context": market_context,
        "tech_candidates": candidates,
        "planned_roadmap": roadmap,
        "stages": stages,
        "investment_strategy": strategy,
        "review": review,
        "raw_response": data,
        "tool_planning": tool_planning or {},
        "patent_raw_data": (tool_context or {}).get("patent_raw_data") or {},
        "market_raw_data": (tool_context or {}).get("market_raw_data") or {},
        "patent_maps": (tool_context or {}).get("patent_maps") or {},
        "tool_context": tool_context or {},
        "generation_error": generation_error,
    }


def run_single_agent_baseline(
    *,
    domain: str,
    reference_year: int,
    category_hints: Optional[List[str]],
    problem_frame: dict,
    company_name: Optional[str],
    company_profile: Optional[str],
    related_companies: Optional[List[str]],
    use_patent_map: Optional[bool],
    tool_augmented: bool = False,
) -> Dict[str, Any]:
    total_budget = _num((problem_frame or {}).get("total_budget"), 0)
    tool_planning = {}
    tool_context = {}
    if tool_augmented:
        tool_planning = _plan_tool_queries(
            domain=domain,
            reference_year=reference_year,
            category_hints=category_hints,
            problem_frame=problem_frame,
            company_name=company_name,
            company_profile=company_profile,
            related_companies=related_companies,
        )
        print(
            "[Tool Single Agent] API/tool 데이터 수집 중... "
            f"companies={len(tool_planning.get('related_companies') or []) + (1 if company_name else 0)}, "
            f"seed_tech={len(tool_planning.get('seed_technologies') or [])}"
        )
        tool_context = _collect_tool_context(
            domain=domain,
            company_name=company_name,
            related_companies=tool_planning.get("related_companies") or [],
            seed_technologies=tool_planning.get("seed_technologies") or [],
            use_patent_map=use_patent_map,
        )
    payload = {
        "domain": domain,
        "reference_year": reference_year,
        "category_hints": category_hints or [],
        "problem_frame": problem_frame or {},
        "company_name": company_name,
        "company_profile": company_profile,
        "related_companies": related_companies or [],
        "use_patent_map": use_patent_map,
        "tool_augmented": tool_augmented,
        "tool_planning": tool_planning,
        "tool_context": _compact_tool_context(tool_context) if tool_augmented else {},
    }
    schema = {
        "market_context": {
            "target_market": "string",
            "expected_boom_quarter": "YYYY Qn",
            "tam_sam_som": {"tam_usd": "number", "sam_usd": "number", "som_usd": "number"},
            "cagr": "string or number",
            "growth_outlook": "string",
        },
        "tech_candidates": [
            {
                "tech_id": "S01",
                "name": "string",
                "category": "string",
                "trl": 1,
                "patent_score": 0,
                "market_score": 0,
                "final_score": 0,
                "expected_market_boom_quarter": "YYYY Qn",
                "dependency_hints": ["string"],
                "rationale": "string",
            }
        ],
        "planned_roadmap": [
            {
                "tech_id": "S01",
                "name": "string",
                "phase_name": "string",
                "start_q": "YYYY Qn",
                "target_q": "YYYY Qn",
                "prerequisites": ["string"],
                "lead_time_quarters": 1,
                "justification": "string",
            }
        ],
        "stages": [
            {"stage": "string", "period": "string", "tech_ids": ["S01"]}
        ],
        "investment_strategy": [
            {
                "stage": "string",
                "period": "string",
                "stage_budget_ratio": 0.3,
                "stage_estimated_usd": 0,
                "stage_assessment": "string",
                "tech_investments": [
                    {
                        "tech_id": "S01",
                        "name": "string",
                        "recommended_investment_tier": "Tier 1",
                        "investment_attractiveness": "high",
                        "investment_urgency": "high",
                        "investment_scope": "string",
                        "evaluation_scores": {
                            "market_opportunity": 4,
                            "strategic_fit": 4,
                            "executability": 4,
                            "uncertainty": 3,
                            "urgency": 4,
                        },
                        "recommended_action": "string",
                        "rationale": ["string"],
                        "major_risks": ["string"],
                        "resource_focus": ["string"],
                    }
                ],
            }
        ],
        "review": {
            "decision": "ACCEPT",
            "trm_assessment": {},
            "issues": [],
            "refinement": {"rerun_agents": [], "feedback": []},
            "diagnostic_summary": "",
            "report": {},
        },
        "orchestrator_report": {
            "executive_summary": "string",
            "technology_strategy": "string",
            "roadmap_structure": "string",
            "investment_strategy": "string",
            "trend_alignment": "string",
            "feasibility_and_risk": "string",
            "expected_outcomes": "string",
        },
    }
    user = (
        "Build a complete single-agent TRM baseline from this input.\n"
        "Generate 5 to 8 technology candidates.\n"
        "Keep IDs stable as S01, S02, ...\n"
        "If tool_context is present, ground candidate choices in the patent and market evidence there.\n"
        "Return JSON matching this schema:\n"
        f"{json.dumps(schema, ensure_ascii=False, indent=2)}\n\n"
        "Input:\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )

    try:
        llm = get_llm(max_tokens=8192, json_mode=True, temperature=0.1)
        resp = llm.invoke([
            SystemMessage(content=_TOOL_AUGMENTED_SYSTEM if tool_augmented else _SYSTEM),
            HumanMessage(content=user),
        ])
        raw = resp.content if hasattr(resp, "content") else str(resp)
        data = _extract_json(raw)
    except Exception as e:
        generation_error = str(e)
        data = {
            "single_agent_error": generation_error,
            "market_context": {
                "target_market": domain,
                "expected_boom_quarter": f"{max(reference_year - 2, 2027)} Q4",
                "growth_outlook": "Fallback generated because the single-agent LLM call failed.",
            },
        }
    else:
        generation_error = None

    result = _normalize_result(
        data=data,
        domain=domain,
        reference_year=reference_year,
        total_budget=total_budget,
        use_patent_map=use_patent_map,
        tool_context=tool_context,
        tool_planning=tool_planning,
        baseline_mode="tool_augmented_single_agent" if tool_augmented else "single_agent",
        generation_error=generation_error,
    )
    result["baseline_mode"] = "tool_augmented_single_agent" if tool_augmented else "single_agent"
    if generation_error:
        result["llm_data_quality"] = (
            "tool_augmented_api_collected_llm_fallback"
            if tool_augmented
            else "single_agent_llm_fallback"
        )
    else:
        result["llm_data_quality"] = "tool_augmented_single_call_estimate" if tool_augmented else "single_call_estimate"
    return result
