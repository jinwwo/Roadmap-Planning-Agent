# -*- coding: utf-8 -*-
"""API=True 경로: tech_analysis_agent 직접 호출로 backtest용 데이터 생성"""
import os, sys, glob, json

TAA_PATH = "/root/Roadmap-Planning-Agent/tech_analysis_agent"
PATENT_LOG_BASE = "/root/Roadmap-Planning-Agent/orchestration_agent/outputs/patent_agent"

def _find_input_block(company: str):
    safe = company.replace(" ", "_").replace("&", "_")
    cands = glob.glob(os.path.join(PATENT_LOG_BASE, "batch_*", "*" + safe + "*patent_agent_log.json"))
    if not cands:
        cands = glob.glob(os.path.join(PATENT_LOG_BASE, "batch_*", "*patent_agent_log.json"))
    for c in cands:
        try:
            d = json.load(open(c, encoding="utf-8"))
            inp = d.get("input", {})
            if inp.get("company_name", "").replace(" ", "").lower() == company.replace(" ", "").lower():
                return inp
        except Exception:
            continue
    if cands:
        try:
            return json.load(open(cands[0], encoding="utf-8")).get("input", {})
        except Exception:
            pass
    return {}

def generate_tech_candidates(company: str, reference_year: int = 2030,
                             strategic_direction=None, use_patent_map: bool = True):
    inp = _find_input_block(company)
    domain = inp.get("domain", "")
    category_hints = inp.get("category_hints", [])
    company_profile = inp.get("company_profile", "")
    related = inp.get("related_companies")
    sys.path.insert(0, TAA_PATH)
    cwd = os.getcwd()
    os.chdir(TAA_PATH)
    try:
        from graphs.analysis_graph import run_technology_analysis
        os.environ.setdefault("USE_PATENT_MAP", "true" if use_patent_map else "false")
        fs = run_technology_analysis(
            domain=domain, reference_year=int(reference_year),
            category_hints=category_hints, company_name=company,
            company_profile=company_profile, related_companies=related,
            strategic_direction=strategic_direction,
        )
    finally:
        os.chdir(cwd)
    return {
        "tech_candidates": fs.get("tech_candidates") or [],
        "market_context": fs.get("market_context") or {},
        "error": fs.get("error"),
        "source": "agent_api(run_technology_analysis)",
    }
