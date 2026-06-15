#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""#18, #19 누락분 보충 패처 (비ASCII 문자 escape 처리)"""
import sys, os
TARGET = "trm_evaluation.py"
if not os.path.exists(TARGET):
    print("X trm_evaluation.py 없음"); sys.exit(1)
src = open(TARGET, encoding="utf-8").read()

FIXES = [
  # #18: _describe_llm V2 evidence 기반
  ("\uac01 \ucd95 \uc810\uc218\uc758 \uc0b0\ucd9c \uadfc\uac70 \uc124\uba85\"\"\"\n        axes = llm[\"axis_scores\"]\n        categories = set(t.get(\"category\", \"\") for t in techs)\n        tiers = set(t.get(\"investment_tier\", \"\") for t in techs)\n        trl_vals = [t.get(\"TRL\", t.get(\"trl\", 5)) for t in techs]\n        trl_range = f\"{min(trl_vals)}~{max(trl_vals)}\" if trl_vals else \"N/A\"\n        dep_count = cr.get(\"dependency_violation_count\", 0)\n\n        descs = {\n            \"alignment\": (\n                f\"\uc810\uc218 {axes['alignment']}/5 \u2014 \"\n                f\"\uc120\ud0dd\ub41c \uae30\uc220\ub4e4\uc758 \uc2dc\uc7a5 \uc131\uc7a5\ub960(normalized) \ud3c9\uade0 \uae30\ubc18. \"\n                f\"\ub192\uc744\uc218\ub85d \uace0\uc131\uc7a5 \uc2dc\uc7a5\uc758 \uae30\uc220\uc744 \uc798 \uc120\ud0dd\ud588\ub2e4\ub294 \uc758\ubbf8.\"\n            ),\n            \"sequencing\": (\n                f\"\uc810\uc218 {axes['sequencing']}/5 \u2014 \"\n                f\"\uc758\uc874\uc131 \uc704\ubc18 {dep_count}\uac74 \uac10\uc9c0. \"\n                f\"{'\uc120\ud589\uad00\uacc4\uac00 \uc62c\ubc14\ub974\uac8c \uad6c\uc131\ub428.' if dep_count == 0 else f'{dep_count}\uac74\uc758 \uc120\ud589\uae30\uc220 \ubbf8\uc644\ub8cc \uc0c1\ud0dc\uc5d0\uc11c \ud6c4\ud589\uae30\uc220\uc774 \uc2dc\uc791\ub428.'}\"\n            ),\n            \"investment\": (\n                f\"\uc810\uc218 {axes['investment']}/5 \u2014 \"\n                f\"\uae30\uc220 \uac00\uce58(value) \ub300\ube44 \ud22c\uc790 \ub4f1\uae09(tier) \uc77c\uce58\ub3c4 \uae30\ubc18. \"\n                f\"\uac00\uce58 \ub192\uc740 \uae30\uc220\uc5d0 \ub192\uc740 \ud22c\uc790\ub97c \ubc30\ubd84\ud560\uc218\ub85d \ub192\uc740 \uc810\uc218.\"\n            ),\n            \"coherence\": (\n                f\"\uc810\uc218 {axes['coherence']}/5 \u2014 \"\n                f\"\uae30\uc220 \uce74\ud14c\uace0\ub9ac \ub2e4\uc591\uc131({len(categories)}\uc885: {', '.join(categories)})\uacfc \"\n                f\"TRL \ubd84\ud3ec(\ubc94\uc704 {trl_range}) \uae30\ubc18. \"\n                f\"\ub2e4\uc591\ud55c \uce74\ud14c\uace0\ub9ac\uc640 \ub113\uc740 TRL \ubc94\uc704\uc77c\uc218\ub85d \ub192\uc740 \uc810\uc218.\"\n            ),\n            \"balance\": (\n                f\"\uc810\uc218 {axes['balance']}/5 \u2014 \"\n                f\"\ud22c\uc790 \ub4f1\uae09 \ub2e4\uc591\uc131({len(tiers)}\uc885: {', '.join(tiers)}) \uae30\ubc18. \"\n                f\"Strategic/High/Medium/Low\uac00 \uace8\uace0\ub8e8 \uc788\uc744\uc218\ub85d \ub192\uc740 \uc810\uc218.\"\n            ),\n            \"formula\": (\n                f\"llm_structural_score = (0.3\u00d7{axes['alignment']} + 0.2\u00d7{axes['sequencing']} \"\n                f\"+ 0.2\u00d7{axes['investment']} + 0.2\u00d7{axes['coherence']} \"\n                f\"+ 0.1\u00d7{axes['balance']} - 1) / 4 \u00d7 100 = {llm['llm_structural_score']}\"\n            ),\n        }",
   "V2: \uac01 \ucd95 \uc810\uc218\uc758 \uc0b0\ucd9c \uadfc\uac70 (evidence \uc6b0\uc120, fallback\uc740 rule-based \uc124\uba85)\"\"\"\n        axes = llm[\"axis_scores\"]\n        evidence = llm.get(\"evidence\", {})\n        meta_strategic = \"\"  # blind \u2014 strategic direction\uc740 LLM\uc774 \uc790\uccb4 \ud310\ub2e8\n\n        descs = {}\n        for axis_key, axis_label in [\n            (\"trl_pathway\", \"TRL Pathway Realism\"),\n            (\"strategic_fidelity\", \"Strategic Direction Fidelity\"),\n            (\"competitive_awareness\", \"Competitive Landscape Awareness\"),\n            (\"market_timing\", \"Market Timing Precision\"),\n            (\"patent_market_convergence\", \"Patent-Market Convergence\"),\n        ]:\n            score = axes.get(axis_key, 3)\n            ev = evidence.get(axis_key, \"\")\n            if ev:\n                descs[axis_key] = f\"\uc810\uc218 {score}/5 \u2014 {ev}\"\n            else:\n                descs[axis_key] = f\"\uc810\uc218 {score}/5.\"\n\n        # formula\n        w = LLMStructuralJudge.WEIGHTS\n        formula_parts = \" + \".join(\n            f\"{w[k]}\u00d7{axes.get(k, 3)}\" for k in w\n        )\n        descs[\"formula\"] = (\n            f\"llm_structural_score = ({formula_parts} - 1) / 4 \u00d7 100 \"\n            f\"= {llm['llm_structural_score']}\"\n        )\n"),
  # #19: BT formula 설명 V2 가중치
  ("30\u00d7{bt['selection_quality']:.1f} + 0.30\u00d7{bt['cost_adjusted_return']:.1f} \"\n                f\"+ 0.15\u00d7{bt['investment_rationality_score']:.1f} + 0.10\u00d7{bt['budget_feasibility_score']:.1f} \"\n                f\"+ 0.10\u00d7{bt['dependency_validity_score']:.1f} + 0.0",
   "25\u00d7{bt['selection_quality']:.1f} + 0.25\u00d7{bt['cost_adjusted_return']:.1f} \"\n                f\"+ 0.15\u00d7{bt['investment_rationality_score']:.1f} + 0.05\u00d7{bt['budget_feasibility_score']:.1f} \"\n                f\"+ 0.05\u00d7{bt['dependency_validity_score']:.1f} + 0.2"),
]

applied = 0
for idx, (old, new) in enumerate(FIXES, 18):
    n = src.count(old)
    if n == 1:
        src = src.replace(old, new, 1); applied += 1; print("  OK #%d" % idx)
    elif n == 0:
        print("  ! #%d 대상없음 (이미 적용?)" % idx)
    else:
        print("  ! #%d %d회 중복" % (idx, n))

open(TARGET, "w", encoding="utf-8").write(src)
print("보충 완료: %d/2" % applied)

# 검증
s = open(TARGET, encoding="utf-8").read()
mul = chr(0xd7)
marker18 = "V2: " + "".join(chr(c) for c in [0xAC01,0x0020]) if False else None
v18_ok = ("evidence.get(axis_key" in s) and ("axes[" + chr(39) + "alignment" + chr(39) + "]" not in s)
v19_ok = ("0.25" + mul + "{bt[" + chr(39) + "selection_quality" + chr(39) + "]") in s
print("#18 적용:", v18_ok)
print("#19 적용:", v19_ok)
print("OK" if (v18_ok and v19_ok) else "확인 필요")
