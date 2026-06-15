# -*- coding: utf-8 -*-
# run_pairwise.py 5축 W → 4축(0.35/0.25/0.25/0.15) 정리
import shutil

SRC = "run_pairwise.py"
shutil.copy(SRC, SRC + ".bak_4axis")
src = open(SRC, encoding="utf-8").read()

old_w = '''            W = {"trl_pathway":0.25,"strategic_fidelity":0.25,"competitive_awareness":0.20,
                 "market_timing":0.20,"patent_market_convergence":0.10}'''
new_w = '''            W = {"trl_pathway":0.35,"competitive_awareness":0.25,
                 "market_timing":0.25,"patent_market_convergence":0.15}'''
assert old_w in src, "5축 W 못 찾음"
src = src.replace(old_w, new_w)

src = src.replace(
    "# 5축 가중 종합점수 (단독 llm_structural_score와 같은 WEIGHTS·척도)",
    "# 4축 가중 종합점수 (단독 llm_structural_score와 같은 WEIGHTS·척도, strategic 제외)"
)

open(SRC, "w", encoding="utf-8").write(src)
print("run_pairwise.py 4축 정리 완료")
print("  W: 0.35/0.25/0.25/0.15 (strategic_fidelity 제외)")
print("  백업:", SRC + ".bak_4axis")

import py_compile
try:
    py_compile.compile(SRC, doraise=True)
    print("  문법 OK")
except Exception as e:
    print("  문법 에러:", e)
    shutil.copy(SRC + ".bak_4axis", SRC)
    print("  롤백함")
