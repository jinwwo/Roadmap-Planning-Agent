# -*- coding: utf-8 -*-
# btDesc 재구성: investment 설명 추가 + 순서 정리 (formula 맨 끝)
import shutil, re
F="demo_web.html"
shutil.copy(F, F+".bak_btorder")
s=open(F,encoding="utf-8").read()
log=[]

# 현재 btDesc IIFE를 통째로 교체. 앵커: "btDesc:(function(){" ... "return d;})()," 
# 정규식으로 btDesc IIFE 블록 잡기
m=re.search(r"btDesc:\(function\(\)\{.*?return d;\}\)\(\),", s, re.S)
if not m:
    print("MISS btDesc IIFE")
else:
    new=("btDesc:(function(){"
         "var sd=bt.description||{};"
         "var sq=bt.selection_quality||0,car=bt.cost_adjusted_return||0,"
         "ir=bt.investment_rationality_score||0,ta=bt.timing_accuracy_score||0,"
         "bf=bt.budget_feasibility_score||0,dv=bt.dependency_validity_score||0,con=(bf+dv)/2;"
         "var d={};"
         "d.selection_quality=sd.selection_quality||(sq.toFixed(1)+'/100');"
         "d.cost_adjusted_return=sd.cost_adjusted_return||(car.toFixed(1)+'/100');"
         "d.investment_rationality=sd.investment_rationality||(ir.toFixed(1)+'/100 \\u2014 \\uae30\\ub300 tier vs \\uc2e4\\uc81c tier \\uc77c\\uce58\\ub3c4');"
         "d.timing_accuracy=sd.timing_accuracy||(ta.toFixed(1)+'/100');"
         "d['\\uc81c\\uc57d \\uc900\\uc218']=con.toFixed(1)+'/100 \\u2014 \\uc608\\uc0b0 '+bf.toFixed(0)+', \\uc758\\uc874\\uc131 '+dv.toFixed(0);"
         "d.formula='backtest_score = 0.25\\u00d7'+sq.toFixed(1)+' + 0.25\\u00d7'+car.toFixed(1)+' + 0.15\\u00d7'+ir.toFixed(1)+' + 0.25\\u00d7'+ta.toFixed(1)+' + 0.10\\u00d7'+con.toFixed(1)+'(\\uc81c\\uc57d) = '+(bt.backtest_score||0).toFixed(1);"
         "return d;})(),")
    s=s[:m.start()]+new+s[m.end():]
    log.append("OK btDesc reordered + investment added")

open(F,"w",encoding="utf-8").write(s)
print("\n".join(log) if log else "no change")
print("DONE. backup: demo_web.html.bak_btorder")
