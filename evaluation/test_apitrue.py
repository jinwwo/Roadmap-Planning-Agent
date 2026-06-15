# -*- coding: utf-8 -*-
# API=True 실제 호출 테스트: NAVER 1건 tech_candidates 생성
import json, time
from tech_candidates_source import generate_tech_candidates

print("=== API=True: NAVER tech_candidates 실시간 생성 ===")
print("(KIPRIS+Tavily+LLM 호출 — 수 분 소요)\n")
t0 = time.time()
result = generate_tech_candidates("NAVER", reference_year=2030,
                                  strategic_direction=["기술선도"], use_patent_map=True)
dt = time.time() - t0
print(f"\n소요: {dt:.0f}초")
print("error:", result.get("error"))
tc = result.get("tech_candidates", [])
print(f"생성된 tech_candidates: {len(tc)}개")

# backtest 필드 확인
print("\n=== backtest용 필드 확인 (첫 3개 기술) ===")
for t in tc[:3]:
    print(f"  [{t.get('tech_id')}] {t.get('name','')[:30]}")
    print(f"    patent_score: {t.get('patent_score')}  market_score: {t.get('market_score')}  final: {t.get('final_score')}")
    print(f"    boom_q: {t.get('expected_market_boom_quarter')}")
    ps = t.get('patent_signals', {})
    print(f"    patent_signals: {ps if ps else '없음'}")
    tss = t.get('tam_sam_som', {})
    print(f"    tam_sam_som: {tss if tss else '없음'}")

mc = result.get("market_context", {})
print(f"\nmarket_context 키: {list(mc.keys())[:8]}")

json.dump(result, open("/tmp/apitrue_naver.json","w",encoding="utf-8"), ensure_ascii=False, indent=1)
print("\n→ /tmp/apitrue_naver.json 저장")
