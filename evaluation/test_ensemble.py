# -*- coding: utf-8 -*-
# 3번: Claude + GPT 앙상블 1건 테스트 (NAVER off) — holdout 추출 추가
import json, os
from trm_evaluation import TRMEvaluationSuite
import demo_server as ds

os.environ.setdefault("EVAL_CONNECTOR", "agent+kipris")
BASE = "../orchestration_agent/outputs"

def load_bundle(variant_dir, ind, co, strat):
    leaf = os.path.join(BASE, variant_dir, ind, co, strat)
    if not os.path.isdir(leaf):
        print(f"  경로 없음: {leaf}")
        return None
    td = json.load(open(os.path.join(leaf, "tech_candidates.json"), encoding="utf-8"))
    rd = json.load(open(os.path.join(leaf, "planned_roadmap.json"), encoding="utf-8"))
    iv = json.load(open(os.path.join(leaf, "investment_strategy.json"), encoding="utf-8"))
    rp = None
    rf = os.path.join(leaf, "orchestrator_report.json")
    if os.path.exists(rf):
        rp = json.load(open(rf, encoding="utf-8"))
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

print("=== NAVER off 기술선도 로드 ===")
pack = load_bundle("특허맵_Off", "AI LLM", "NAVER", "기술선도")
if pack is None:
    print("로드 실패."); exit(1)
print("  로드 OK")

print("\n=== holdout 추출 ===")
md = pack.get("metadata") or {}
ext = ds._build_extractor(md.get("company_name", ""), int(md.get("reference_year", 2030) or 2030))
if not pack.get("holdout_data"):
    pack = ext.extract_and_assemble(pack)
print("  holdout_data 키 있음:", "holdout_data" in pack)

print("\n=== 앙상블 평가 (anthropic + openai, mean) ===")
suite = TRMEvaluationSuite(providers=["anthropic", "openai"], aggregation="mean")
report = suite.evaluate(pack, use_api=True)

lj = report.get("llm_judge", report)
print("\n=== 앙상블 검증 ===")
print("  models_used:", lj.get("models_used"))
print("  aggregation:", lj.get("aggregation"))
print("\n  per_model_scores:")
for m, s in (lj.get("per_model_scores") or {}).items():
    print(f"    [{m}] {s}")
print("\n  집계 axis_scores:", lj.get("axis_scores"))
print("  strategic_fidelity 보존:", "strategic_fidelity" in (lj.get("axis_scores") or {}))
print("  llm_structural_score (4축):", lj.get("llm_structural_score"))
desc = lj.get("description", {})
print("  formula:", desc.get("formula", "")[:120])

json.dump(report, open("/tmp/ensemble_test.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("\n  → /tmp/ensemble_test.json 저장")
