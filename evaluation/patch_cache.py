# -*- coding: utf-8 -*-
# 1번: 기존 cache 36개를 4축 양식으로 정리
#  - llm_structural_score: 엑셀과 동일한 4축 재정규화(0.333/0.267/0.267/0.133) 값으로
#  - strategic_fidelity: 축 점수로 보존 (axis_scores엔 남김), structural 계산서만 제외
#  - composite: 4축 llm 기반 재계산 (실제 penalty 유지)
#  - description: strategic_fidelity 제외, formula는 0.35/0.25/0.25/0.15로 표시
#  - backtest: 점수 그대로, formula는 표시 정리(6항목 유지)
import json, glob, shutil, os

CACHE_DIR = "outputs"
BACKUP = "outputs/cache_bak_pre4axis"
os.makedirs(BACKUP, exist_ok=True)

# 점수 계산용 가중치 (엑셀과 동일 = 재정규화 0.333 계열)
W_SCORE = {"trl_pathway":0.333,"competitive_awareness":0.267,"market_timing":0.267,"patent_market_convergence":0.133}
# formula 표시용 가중치 (요청: 0.35/0.25/0.25/0.15)
W_DISP  = {"trl_pathway":0.35,"competitive_awareness":0.25,"market_timing":0.25,"patent_market_convergence":0.15}
AXES4 = ["trl_pathway","competitive_awareness","market_timing","patent_market_convergence"]
LAB = {"trl_pathway":"TRL","competitive_awareness":"\uacbd\uc7c1",
       "market_timing":"\ud0c0\uc774\ubc0d","patent_market_convergence":"\uc218\ub834"}

files = sorted(glob.glob(f"{CACHE_DIR}/cache_*.json"))
print(f"대상 cache: {len(files)}개")
done = 0
for f in files:
    shutil.copy(f, os.path.join(BACKUP, os.path.basename(f)))
    d = json.load(open(f, encoding="utf-8"))
    lj = d.get("llm_judge", {})
    bt = d.get("backtest", {})
    co = d.get("composite", {})

    # ── 축 점수 확보 (strategic_fidelity는 보존) ──
    def axval(k):
        # axis_scores 우선, 없으면 top-level
        return (lj.get("axis_scores", {}) or {}).get(k, lj.get(k, 3))
    t = axval("trl_pathway"); c = axval("competitive_awareness")
    m = axval("market_timing"); v = axval("patent_market_convergence")

    # ── 4축 structural_score (엑셀 동일: 0.333 계열) ──
    raw4 = W_SCORE["trl_pathway"]*t + W_SCORE["competitive_awareness"]*c \
         + W_SCORE["market_timing"]*m + W_SCORE["patent_market_convergence"]*v
    llm4 = round((raw4 - 1) / 4 * 100, 1)

    # 5축 원본 보존 (참조용)
    lj["llm_structural_score_5axis"] = lj.get("llm_structural_score")
    lj["llm_structural_score"] = llm4

    # ── description: strategic_fidelity 제외, formula는 0.35 표시 ──
    desc = dict(lj.get("description", {}) or {})
    desc.pop("strategic_fidelity", None)
    # formula 표시용(0.35/0.25/0.25/0.15)
    fp = " + ".join(f"{W_DISP[k]}\u00d7{axval(k)}" for k in AXES4)
    desc["formula"] = f"llm_structural_score = ({fp} - 1) / 4 \u00d7 100 = {llm4:.1f}"
    lj["description"] = desc

    # ── composite: 4축 llm 기반 재계산 (penalty 유지) ──
    pen = co.get("total_penalty", 0) or 0
    bts = bt.get("backtest_score", 0) or 0
    pre = round(0.4*llm4 + 0.6*bts, 1)
    fin = round(max(0, pre - pen), 1)
    co["pre_penalty_score"] = pre
    co["final_composite_score"] = fin
    # composite description 갱신
    pen_txt = f" Penalty: -{pen:.1f}pt." if pen > 0 else ""
    co["description"] = f"pre_penalty = 0.4\u00d7{llm4:.1f}(LLM) + 0.6\u00d7{bts:.1f}(Backtest) = {pre:.1f}.{pen_txt} \ucd5c\uc885 = {fin:.1f}."
    d["composite"] = co

    json.dump(d, open(f, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    done += 1

print(f"변환 완료: {done}개")
print(f"백업: {BACKUP}/")
print("\n검증 (NAVER off 예시):")
for f in files:
    if "off" in os.path.basename(f) and "NAVER" in os.path.basename(f) and "기술선도" in os.path.basename(f):
        d = json.load(open(f, encoding="utf-8"))
        lj = d["llm_judge"]
        print("  llm_structural_score(4축):", lj["llm_structural_score"], "(5축 원본:", lj.get("llm_structural_score_5axis"), ")")
        print("  formula:", lj["description"]["formula"])
        print("  composite:", d["composite"]["final_composite_score"])
        print("  strategic_fidelity 보존:", lj.get("strategic_fidelity") or (lj.get("axis_scores") or {}).get("strategic_fidelity"))
        break
