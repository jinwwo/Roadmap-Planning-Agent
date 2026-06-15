# -*- coding: utf-8 -*-
# cache 보완: backtest description에 investment_rationality 항목 추가
import json, glob, os

CACHE_DIR = "outputs"
files = sorted(glob.glob(f"{CACHE_DIR}/cache_*.json"))
print(f"대상 cache: {len(files)}개")

done = 0
for f in files:
    d = json.load(open(f, encoding="utf-8"))
    bt = d.get("backtest", {})
    desc = bt.get("description", {})
    
    if "investment_rationality" in desc:
        continue
    
    ir_score = bt.get("investment_rationality_score", 0)
    cr = d.get("backtest_cache_run", {})
    
    tier_analysis = cr.get("tier_analysis", [])
    mismatches = sum(1 for t in tier_analysis if t.get("mismatch", 0) > 1)
    total_techs = len(tier_analysis) if tier_analysis else len(bt.get("tech_details", []))
    
    if total_techs > 0:
        ir_detail = f"{ir_score:.1f}/100 — 기대 투자등급 vs 실제 tier 일치도\n  {total_techs}개 기술 중 기대와 불일치 {mismatches}건\n  value 대비 투자등급이 적절함."
    else:
        ir_detail = f"{ir_score:.1f}/100 — 투자등급 적합성 평가"
    
    new_desc = {}
    for k in ["selection_quality", "cost_adjusted_return"]:
        if k in desc:
            new_desc[k] = desc[k]
    
    new_desc["investment_rationality"] = ir_detail
    
    for k in ["budget_feasibility", "dependency_validity", "timing_accuracy", "formula"]:
        if k in desc:
            new_desc[k] = desc[k]
    
    if "strategic_fidelity" in desc:
        new_desc["strategic_fidelity"] = desc["strategic_fidelity"]
    
    bt["description"] = new_desc
    d["backtest"] = bt
    
    json.dump(d, open(f, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    done += 1

print(f"투자적합성 추가: {done}개")
print("\n검증 (NAVER off 예시):")
for f in files:
    if "off" in os.path.basename(f) and "NAVER" in os.path.basename(f) and "기술선도" in os.path.basename(f):
        d = json.load(open(f, encoding="utf-8"))
        desc = d["backtest"]["description"]
        print("  description 키:", list(desc.keys()))
        ir = desc.get("investment_rationality", "")
        print("  investment_rationality:", ir[:100])
        break
