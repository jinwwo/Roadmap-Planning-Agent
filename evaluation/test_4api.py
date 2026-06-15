# -*- coding: utf-8 -*-
# 4번 테스트: NAVER 1건 holdout 추출 — API=False(raw) 모드
import json, os
import demo_server as ds

BASE = "../orchestration_agent/outputs"

def load_bundle(variant_dir, ind, co, strat):
    leaf = os.path.join(BASE, variant_dir, ind, co, strat)
    if not os.path.isdir(leaf):
        print("경로 없음:", leaf); return None
    td = json.load(open(os.path.join(leaf, "tech_candidates.json"), encoding="utf-8"))
    rd = json.load(open(os.path.join(leaf, "planned_roadmap.json"), encoding="utf-8"))
    iv = json.load(open(os.path.join(leaf, "investment_strategy.json"), encoding="utf-8"))
    rp = None
    rf = os.path.join(leaf, "orchestrator_report.json")
    if os.path.exists(rf): rp = json.load(open(rf, encoding="utf-8"))
    bundle = {
        "orchestrator_report": rp,
        "tech_candidates": td.get("tech_candidates", []),
        "planned_roadmap": rd.get("planned_roadmap", []),
        "investment_strategy": iv.get("investment_strategy", []),
        "stages": iv.get("stages", []),
        "market_context": td.get("market_context", {}),
        "active_agents": rp.get("active_agents", ["1","2","3"]) if rp else ["1","2","3"],
    }
    return ds._detect_and_convert(bundle)

pack = load_bundle("특허맵_Off", "AI LLM", "NAVER", "기술선도")
if pack is None: exit(1)
md = pack.get("metadata") or {}
company = md.get("company_name", "NAVER")
eval_year = int(md.get("reference_year", 2030) or 2030)
print(f"회사: {company}, eval_year: {eval_year}")

# AgentPatentConnector 단독 동작 확인
print("\n=== AgentPatentConnector 직접 테스트 ===")
from data_sources import AgentPatentConnector
apc = AgentPatentConnector(company, eval_year)
print("  filing_trend 로드:", apc._trend, "| cagr:", apc._cagr, "| total:", apc._total)
r_base = apc.fetch_patents(["llm","ai"], "2024-12-31", 5)
r_eval = apc.fetch_patents(["llm","ai"], "2030-12-31", 5)
print(f"  baseline(2024): {r_base.total_patents}건 (src={r_base.source})")
print(f"  evaluation(2030): {r_eval.total_patents}건 (src={r_eval.source})")
print(f"  → 성장: {r_base.total_patents} → {r_eval.total_patents}")

# 전체 holdout 추출 (API=False)
print("\n=== API=False holdout 추출 (AgentPatent + AgentMarket) ===")
ds.config["api_direct"] = False
ext = ds._build_extractor(company, eval_year)
full = ext.extract_and_assemble(pack)
hd = full.get("holdout_data", {})
print(f"  기술 수: {len(hd)}")
for tid, h in list(hd.items())[:4]:
    print(f"  [{tid}] patent {h['baseline_patents']}→{h['realized_patents']} | "
          f"market {h['baseline_market_m']:.0f}→{h['realized_market_m']:.0f}M | "
          f"src={h['patent_source']}/{h['market_source']}")

json.dump(full, open("/tmp/holdout_false.json","w",encoding="utf-8"), ensure_ascii=False, indent=1)
print("\n  → /tmp/holdout_false.json 저장")
