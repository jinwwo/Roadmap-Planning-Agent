import json, glob, os, re

def parse_name(fn):
    # cache_특허맵_off_자동차_모빌리티_Hyundai Motor_기술선도_fd20cfec.json
    b = os.path.basename(fn).replace('cache_','').replace('.json','')
    b = re.sub(r'_[0-9a-f]{8,}$','',b)        # 끝 해시 제거
    pm = 'on' if b.startswith('특허맵_on') else 'off'
    b2 = b.replace('특허맵_on_','').replace('특허맵_off_','')
    # 산업: 앞 토큰(자동차_모빌리티 / 바이오_헬스케어 / 반도체)
    for ind in ['자동차_모빌리티','바이오_헬스케어','반도체']:
        if b2.startswith(ind):
            rest = b2[len(ind)+1:]
            break
    else:
        ind, rest = '?', b2
    # rest = "{회사}_{전략}", 전략은 기술선도/시장이익최대
    for st in ['기술선도','시장이익최대']:
        if rest.endswith(st):
            co = rest[:-(len(st)+1)]
            strat = st
            break
    else:
        co, strat = rest, '?'
    return pm, ind, co, strat

out = []
for f in sorted(glob.glob('outputs/cache_*.json')):
    d = json.load(open(f, encoding='utf-8'))
    pm, ind, co, strat = parse_name(f)
    lj = d.get('llm_judge',{}) or {}
    bt = d.get('backtest',{}) or {}
    co_ = d.get('composite',{}) or {}
    cr = d.get('constraints',{}) or {}
    llm = lj.get('llm_structural_score') or 0
    bts = bt.get('backtest_score') or 0
    out.append({
        "name": os.path.basename(f).replace('cache_','').replace('.json',''),
        "pm": pm, "ind": ind, "co": co, "st": strat,
        "composite": co_.get('final_composite_score') or 0,
        "penalty": co_.get('total_penalty') or 0,
        "llm_score": round(llm,1),
        "backtest": round(bts,1),
        "gap": round(bts - llm, 1),
        # V2 5축
        "trl": lj.get('trl_pathway') or 0,
        "fidelity": lj.get('strategic_fidelity') or 0,
        "compet": lj.get('competitive_awareness') or 0,
        "timing_ax": lj.get('market_timing') or 0,
        "converg": lj.get('patent_market_convergence') or 0,
        # BT 세부
        "sel_quality": bt.get('selection_quality') or 0,
        "cost_adj": bt.get('cost_adjusted_return') or 0,
        "timing": bt.get('timing_accuracy_score') or 0,
        "dep_valid": bt.get('dependency_validity_score') or 0,
        "budget": bt.get('budget_feasibility_score') or 0,
        "mean_timing_err": cr.get('mean_timing_error_years') or 0,
        "models": lj.get('models_used') or [],
    })

out.sort(key=lambda x: -x['composite'])
json.dump(out, open('outputs/all_results_summary.json','w',encoding='utf-8'),
          ensure_ascii=False, indent=2)
print(f"✓ {len(out)}건 → outputs/all_results_summary.json")
# 미리보기
for r in out[:3]:
    print(f"  {r['composite']:.1f} {r['co']} {r['st']} {r['pm']} | LLM={r['llm_score']} BT={r['backtest']} TRL={r['trl']}")
