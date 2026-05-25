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
import re
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
    names = [
        ("S01", "Accelerated AI interconnect fabric", "Architecture", 4, 83.0),
        ("S02", "Advanced GPU memory hierarchy and scheduling", "Architecture", 5, 81.0),
        ("S03", "Chiplet packaging for AI accelerators", "Packaging", 4, 79.0),
        ("S04", "AI infrastructure software orchestration stack", "Software", 5, 77.0),
        ("S05", "Energy-efficient accelerator process co-optimization", "Process", 3, 75.0),
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


def _normalize_result(
    data: dict,
    domain: str,
    reference_year: int,
    total_budget: float,
    use_patent_map: Optional[bool],
) -> Dict[str, Any]:
    market_context = data.get("market_context") if isinstance(data.get("market_context"), dict) else {}
    market_context.setdefault("target_market", domain)
    market_context.setdefault("expected_boom_quarter", f"{max(reference_year - 2, 2027)} Q4")
    market_context.setdefault("single_agent_baseline", True)
    market_context.setdefault("use_patent_map", use_patent_map)

    candidates = data.get("tech_candidates") if isinstance(data.get("tech_candidates"), list) else []
    if not candidates:
        candidates = _fallback_candidates(domain, reference_year, use_patent_map)
    for idx, c in enumerate(candidates, start=1):
        if not isinstance(c, dict):
            continue
        c.setdefault("tech_id", f"S{idx:02d}")
        c.setdefault("name", f"Single-agent technology candidate {idx}")
        c.setdefault("category", "Architecture")
        c["trl"] = int(_num(c.get("trl"), 4))
        c["patent_score"] = round(_num(c.get("patent_score"), 70), 1)
        c["market_score"] = round(_num(c.get("market_score"), 70), 1)
        c["final_score"] = round(_num(c.get("final_score"), (c["patent_score"] + c["market_score"]) / 2), 1)
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
) -> Dict[str, Any]:
    total_budget = _num((problem_frame or {}).get("total_budget"), 0)
    payload = {
        "domain": domain,
        "reference_year": reference_year,
        "category_hints": category_hints or [],
        "problem_frame": problem_frame or {},
        "company_name": company_name,
        "company_profile": company_profile,
        "related_companies": related_companies or [],
        "use_patent_map": use_patent_map,
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
        "Return JSON matching this schema:\n"
        f"{json.dumps(schema, ensure_ascii=False, indent=2)}\n\n"
        "Input:\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )

    try:
        llm = get_llm(max_tokens=8192, json_mode=True, temperature=0.1)
        resp = llm.invoke([
            SystemMessage(content=_SYSTEM),
            HumanMessage(content=user),
        ])
        raw = resp.content if hasattr(resp, "content") else str(resp)
        data = _extract_json(raw)
    except Exception as e:
        data = {
            "single_agent_error": str(e),
            "market_context": {
                "target_market": domain,
                "expected_boom_quarter": f"{max(reference_year - 2, 2027)} Q4",
                "growth_outlook": "Fallback generated because the single-agent LLM call failed.",
            },
        }

    result = _normalize_result(
        data=data,
        domain=domain,
        reference_year=reference_year,
        total_budget=total_budget,
        use_patent_map=use_patent_map,
    )
    result["baseline_mode"] = "single_agent"
    result["llm_data_quality"] = "single_call_estimate"
    return result
