# -*- coding: utf-8 -*-
# demo_web.html 4-axis patch (no Korean literals; use \u escapes)
import shutil
F = "demo_web.html"
shutil.copy(F, F + ".bak_5axis")
s = open(F, encoding="utf-8").read()
log = []

# 1. radar ks/ls : 5 -> 4 (drop strategic_fidelity)
old_ks = ("const ks=['trl_pathway','strategic_fidelity','competitive_awareness',"
          "'market_timing','patent_market_convergence'],"
          "ls=['TRL','\u808b','\u8b66','\ud0c0\uc774\ubc0d','\uc218\ub834'];")
# NOTE: above Korean may differ; we do a regex-free targeted replace using anchors
import re
m = re.search(r"const ks=\[[^\]]*\],ls=\[[^\]]*\];", s)
if m:
    new_ks = ("const ks=['trl_pathway','competitive_awareness','market_timing',"
              "'patent_market_convergence'],"
              "ls=['TRL','\\uacbd\\uc7c1','\\ud0c0\\uc774\\ubc0d','\\uc218\\ub834'];")
    s = s[:m.start()] + new_ks + s[m.end():]
    log.append("OK radar ks/ls -> 4axis")
else:
    log.append("MISS radar ks")

s2 = s.replace("st=2*Math.PI/5", "st=2*Math.PI/4")
if s2 != s: s = s2; log.append("OK radar angle /4")
else: log.append("MISS radar angle")

# 2. N() axes : drop strategic_fidelity
old_axes = ("axes:{trl_pathway:lj.trl_pathway||3,strategic_fidelity:lj.strategic_fidelity||3,"
            "competitive_awareness:lj.competitive_awareness||3,market_timing:lj.market_timing||3,"
            "patent_market_convergence:lj.patent_market_convergence||3},")
new_axes = ("axes:{trl_pathway:lj.trl_pathway||3,competitive_awareness:lj.competitive_awareness||3,"
            "market_timing:lj.market_timing||3,patent_market_convergence:lj.patent_market_convergence||3},")
if old_axes in s: s = s.replace(old_axes, new_axes); log.append("OK N axes 4")
else: log.append("MISS N axes")

# 3. N() llmScore : 4axis recompute
old_llm = "llmScore:lj.llm_structural_score||0,"
new_llm = ("llmScore:(function(){var t=lj.trl_pathway||3,c=lj.competitive_awareness||3,"
           "m=lj.market_timing||3,v=lj.patent_market_convergence||3;"
           "return ((0.333*t+0.267*c+0.267*m+0.133*v-1)/4)*100;})(),")
if old_llm in s: s = s.replace(old_llm, new_llm); log.append("OK N llmScore 4")
else: log.append("MISS N llmScore")

# 4. N() composite : 4axis recompute
old_comp = "composite:{pre:co.pre_penalty_score||0,pen:co.total_penalty||0,fin:co.final_composite_score||0},"
new_comp = ("composite:(function(){var t=lj.trl_pathway||3,c=lj.competitive_awareness||3,"
            "m=lj.market_timing||3,v=lj.patent_market_convergence||3,"
            "L=((0.333*t+0.267*c+0.267*m+0.133*v-1)/4)*100,"
            "B=bt.backtest_score||0,P=co.total_penalty||0,"
            "pre=0.4*L+0.6*B;return{pre:pre,pen:P,fin:Math.max(0,pre-P)};})(),")
if old_comp in s: s = s.replace(old_comp, new_comp); log.append("OK N composite 4")
else: log.append("MISS N composite")

# 5. badge 5axis -> 4axis  ('5'+\ucd95)
s2 = s.replace("badge('5\\ucd95'", "badge('4\\ucd95'")
if s2 != s: s = s2; log.append("OK badge 4axis")
else: log.append("note: badge pattern not found (maybe inline)")

open(F, "w", encoding="utf-8").write(s)
print("\n".join(log))
print("DONE. backup: demo_web.html.bak_5axis")
