src = open("run_pairwise.py", encoding="utf-8").read()

# 기존 load_bundle 함수 전체를 찾아서 교체
import re
# load_bundle 시작 ~ 다음 def 전까지
old = '''def load_bundle(variant_dir, ind, co, strat):
    leaf = os.path.join(BASE, variant_dir, ind, co, strat)
    if not os.path.isdir(leaf): return None
    try:
        td=json.load(open(os.path.join(leaf,"tech_candidates.json"),encoding="utf-8"))
        rd=json.load(open(os.path.join(leaf,"planned_roadmap.json"),encoding="utf-8"))
        iv=json.load(open(os.path.join(leaf,"investment_strategy.json"),encoding="utf-8"))
        rp=None
        rf=os.path.join(leaf,"orchestrator_report.json")
        if os.path.exists(rf): rp=json.load(open(rf,encoding="utf-8"))
    except Exception as e:
        print(f"    load fail {leaf}: {e}"); return None
    bundle={"orchestrator_report":rp,"tech_candidates":td.get("tech_candidates",[]),
        "planned_roadmap":rd.get("planned_roadmap",[]),"investment_strategy":iv.get("investment_strategy",[]),
        "stages":iv.get("stages",[]),"market_context":td.get("market_context",{}),
        "active_agents":rp.get("active_agents",["1","2","3"]) if rp else ["1","2","3"]}
    return ds._detect_and_convert(bundle)'''

new = '''def load_bundle(variant_dir, ind, co, strat):
    leaf = os.path.join(BASE, variant_dir, ind, co, strat)
    # ── API=True: tech_analysis_agent 실시간 호출로 tech_candidates 생성 ──
    if os.environ.get("EVAL_API", "").lower() in ("1","true","yes"):
        try:
            from tech_candidates_source import generate_tech_candidates
        except Exception as e:
            print(f"    API=True import 실패: {e}"); return None
        strat_dir = ["기술선도"] if "기술선도" in strat else ["시장이익최대"]
        use_pm = ("On" in variant_dir or "on" in variant_dir)
        gen = generate_tech_candidates(company=co, reference_year=2030,
                                       strategic_direction=strat_dir, use_patent_map=use_pm)
        if gen.get("error") or not gen.get("tech_candidates"):
            print(f"    API=True 생성 실패 {co}: {str(gen.get('error',''))[:80]}"); return None
        if not os.path.isdir(leaf): return None
        try:
            rd=json.load(open(os.path.join(leaf,"planned_roadmap.json"),encoding="utf-8"))
            iv=json.load(open(os.path.join(leaf,"investment_strategy.json"),encoding="utf-8"))
        except Exception as e:
            print(f"    roadmap/investment load fail {leaf}: {e}"); return None
        bundle={"orchestrator_report":None,"tech_candidates":gen["tech_candidates"],
            "planned_roadmap":rd.get("planned_roadmap",[]),"investment_strategy":iv.get("investment_strategy",[]),
            "stages":iv.get("stages",[]),"market_context":gen.get("market_context",{}),
            "active_agents":["1","2","3"]}
        return ds._detect_and_convert(bundle)
    # ── API=False: agent 출력 파일 읽기 (기존) ──
    if not os.path.isdir(leaf): return None
    try:
        td=json.load(open(os.path.join(leaf,"tech_candidates.json"),encoding="utf-8"))
        rd=json.load(open(os.path.join(leaf,"planned_roadmap.json"),encoding="utf-8"))
        iv=json.load(open(os.path.join(leaf,"investment_strategy.json"),encoding="utf-8"))
        rp=None
        rf=os.path.join(leaf,"orchestrator_report.json")
        if os.path.exists(rf): rp=json.load(open(rf,encoding="utf-8"))
    except Exception as e:
        print(f"    load fail {leaf}: {e}"); return None
    bundle={"orchestrator_report":rp,"tech_candidates":td.get("tech_candidates",[]),
        "planned_roadmap":rd.get("planned_roadmap",[]),"investment_strategy":iv.get("investment_strategy",[]),
        "stages":iv.get("stages",[]),"market_context":td.get("market_context",{}),
        "active_agents":rp.get("active_agents",["1","2","3"]) if rp else ["1","2","3"]}
    return ds._detect_and_convert(bundle)'''

if "EVAL_API" in src:
    print("이미 통합됨 — skip")
else:
    assert old in src, "기존 load_bundle 못 찾음 (수동 확인 필요)"
    src = src.replace(old, new)
    open("run_pairwise.py", "w", encoding="utf-8").write(src)
    print("load_bundle API=True/False 분기 통합 완료")

import py_compile
try:
    py_compile.compile("run_pairwise.py", doraise=True)
    print("run_pairwise.py 문법 OK")
except Exception as e:
    print("문법 에러:", e)
    import shutil; shutil.copy("run_pairwise.py.bak_4api", "run_pairwise.py")
    print("롤백함")
