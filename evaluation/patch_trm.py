# -*- coding: utf-8 -*-
# 2번: trm_evaluation.py 4축 가중치(0.35/0.25/0.25/0.15) 패치
import re, shutil

SRC = "trm_evaluation.py"
shutil.copy(SRC, SRC + ".bak_4axis_patch")
src = open(SRC, encoding="utf-8").read()

# 1) WEIGHTS 정의 교체
old_weights = '''    # ── V2 weights (new primary) ──
    WEIGHTS = {
        "trl_pathway": 0.25,
        "strategic_fidelity": 0.25,
        "competitive_awareness": 0.20,
        "market_timing": 0.20,
        "patent_market_convergence": 0.10,
    }'''
new_weights = '''    # ── V2 weights (4axis primary, strategic_fidelity 제외) ──
    WEIGHTS = {
        "trl_pathway": 0.35,
        "competitive_awareness": 0.25,
        "market_timing": 0.25,
        "patent_market_convergence": 0.15,
    }
    # LLM 축 수집/strategic_fidelity 보존용 (5축, score 계산엔 미사용)
    WEIGHTS_FULL = {
        "trl_pathway": 0.35,
        "strategic_fidelity": 0.0,
        "competitive_awareness": 0.25,
        "market_timing": 0.25,
        "patent_market_convergence": 0.15,
    }'''
assert old_weights in src, "WEIGHTS 정의 못 찾음"
src = src.replace(old_weights, new_weights)

# 2) 축 수집: WEIGHTS → WEIGHTS_FULL
old_collect = '''                    per_model_scores[provider] = {
                        axis: payload.get(axis, 3) for axis in self.WEIGHTS
                    }'''
new_collect = '''                    per_model_scores[provider] = {
                        axis: payload.get(axis, 3) for axis in self.WEIGHTS_FULL
                    }'''
assert old_collect in src, "축 수집부 못 찾음"
src = src.replace(old_collect, new_collect)

# 3) _aggregate_scores: list(self.WEIGHTS.keys()) → WEIGHTS_FULL
old_agg = '''        """여러 모델의 점수를 집계 (평균 또는 중간값)."""
        axes = list(self.WEIGHTS.keys())'''
new_agg = '''        """여러 모델의 점수를 집계 (평균 또는 중간값)."""
        axes = list(self.WEIGHTS_FULL.keys())'''
assert old_agg in src, "_aggregate_scores 못 찾음"
src = src.replace(old_agg, new_agg)

# 4) 941줄 범위검증
src = re.sub(r'(\n\s+)for axis in self\.WEIGHTS:', r'\1for axis in self.WEIGHTS_FULL:', src)

# 5) fallback evidence
old_fb = '"evidence": {k: "API call failed, fallback score" for k in self.WEIGHTS},'
new_fb = '"evidence": {k: "API call failed, fallback score" for k in self.WEIGHTS_FULL},'
assert old_fb in src, "fallback evidence 못 찾음"
src = src.replace(old_fb, new_fb)

open(SRC, "w", encoding="utf-8").write(src)
print("패치 완료:")
print("  1) WEIGHTS: 4축(0.35/0.25/0.25/0.15)")
print("  2) WEIGHTS_FULL: 5축(strategic_fidelity=0.0 보존)")
print("  3) 축 수집 → WEIGHTS_FULL")
print("  4) _aggregate_scores → WEIGHTS_FULL")
print("  5) 범위검증/fallback → WEIGHTS_FULL")
print("  6) structural_score/formula → WEIGHTS(4축)")
print("  백업: " + SRC + ".bak_4axis_patch")

import py_compile
try:
    py_compile.compile(SRC, doraise=True)
    print("  문법 OK")
except py_compile.PyCompileError as e:
    print("  문법 에러!", e)
    shutil.copy(SRC + ".bak_4axis_patch", SRC)
    print("  롤백함")
