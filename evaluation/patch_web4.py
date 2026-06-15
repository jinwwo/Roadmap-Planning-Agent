# -*- coding: utf-8 -*-
# demo_web.html: descPanel(llmDesc/btDesc)을 4축/V2로 가공
import shutil
F="demo_web.html"
shutil.copy(F, F+".bak_desc")
s=open(F,encoding="utf-8").read()
log=[]

# 246줄: llmDesc:lj.description||{},btDesc:bt.description||{},compDesc:co.description||''};
old="llmDesc:lj.description||{},btDesc:bt.description||{},compDesc:co.description||''};"
new=(
"llmDesc:(function(){"
"var d=Object.assign({},lj.description||{});"
"delete d.strategic_fidelity;"  # 5axis 제외
"var t=lj.trl_pathway||3,c=lj.competitive_awareness||3,m=lj.market_timing||3,v=lj.patent_market_convergence||3,"
"L=((0.333*t+0.267*c+0.267*m+0.133*v-1)/4)*100;"
"d.formula='llm_structural_score = (0.333\\u00d7'+t+' + 0.267\\u00d7'+c+' + 0.267\\u00d7'+m+' + 0.133\\u00d7'+v+' - 1) / 4 \\u00d7 100 = '+L.toFixed(1);"
"return d;})(),"
"btDesc:(function(){"
"var d=Object.assign({},bt.description||{});"
# budget/dep 제거하고 constraint 추가
"var bf=bt.budget_feasibility_score||0,dv=bt.dependency_validity_score||0,con=(bf+dv)/2;"
"var bfTxt=d.budget_feasibility||'',dvTxt=d.dependency_validity||'';"
"delete d.budget_feasibility;delete d.dependency_validity;"
"d['\\uc81c\\uc57d \\uc900\\uc218']=con.toFixed(1)+'/100 \\u2014 \\uc608\\uc0b0 '+bf.toFixed(0)+', \\uc758\\uc874\\uc131 '+dv.toFixed(0);"
# V2 formula
"var sq=bt.selection_quality||0,car=bt.cost_adjusted_return||0,ir=bt.investment_rationality_score||0,ta=bt.timing_accuracy_score||0;"
"d.formula='backtest_score = 0.25\\u00d7'+sq.toFixed(1)+' + 0.25\\u00d7'+car.toFixed(1)+' + 0.15\\u00d7'+ir.toFixed(1)+' + 0.25\\u00d7'+ta.toFixed(1)+' + 0.10\\u00d7'+con.toFixed(1)+'('+'\\uc81c\\uc57d'+') = '+(bt.backtest_score||0).toFixed(1);"
"return d;})(),"
"compDesc:co.description||''};"
)
if old in s:
    s=s.replace(old,new); log.append("OK llmDesc/btDesc -> 4axis/V2")
else:
    log.append("MISS desc anchor")

open(F,"w",encoding="utf-8").write(s)
print("\n".join(log))
print("DONE. backup: demo_web.html.bak_desc")
