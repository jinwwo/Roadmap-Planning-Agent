# -*- coding: utf-8 -*-
# demo_web.html: badge 5->4, BackTest bars to V2 (Investment + Constraint merge)
import shutil, re
F="demo_web.html"
shutil.copy(F, F+".bak_bt5")
s=open(F,encoding="utf-8").read()
log=[]

# 1. badge '5축' -> '4축'  (\uc18d? no: 5+\ucd95). '5'+chuk
b_old="badge('5\ucd95'"
b_new="badge('4\ucd95'"
if b_old in s: s=s.replace(b_old,b_new); log.append("OK badge 5axis->4axis")
else: log.append("MISS badge 5axis")

# 2. badge '5지표' -> '5지표' keep but ensure correct. (5+\uc9c0\ud45c) leave as is
# (5 metrics still: Selection/Cost/Investment/Timing/Constraint)

# 3. BackTest bars: replace the bar(...) line
old_bars=("${bar(bt.sq,'Selection Quality')}${bar(bt.car,'Cost-Adj Return')}"
          "${bar(bt.bf,'Budget Feasibility')}${bar(bt.dv,'Dep Validity')}"
          "${bar(bt.ta,'Timing Accuracy')}")
# constraint = average of bf(budget) and dv(dependency)
new_bars=("${bar(bt.sq,'Selection Quality')}${bar(bt.car,'Cost-Adj Return')}"
          "${bar(bt.ir,'Investment Rationality')}${bar(bt.ta,'Timing Accuracy')}"
          "${bar((bt.bf+bt.dv)/2,'\uc81c\uc57d \uc900\uc218 (\uc608\uc0b0+\uc758\uc874\uc131)')}")
if old_bars in s: s=s.replace(old_bars,new_bars); log.append("OK BackTest bars -> V2 (Investment + Constraint)")
else: log.append("MISS BackTest bars (anchor not exact)")

open(F,"w",encoding="utf-8").write(s)
print("\n".join(log))
print("DONE. backup: demo_web.html.bak_bt5")
