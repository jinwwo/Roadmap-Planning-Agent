"""
run_evaluation.py — TRM 평가 파이프라인 CLI 러너

입력 방식:
  A) Agent 출력 JSON 직접 지정 (--tech, --roadmap, --investment)
  B) 이미 조립된 input_pack (--input)
  C) 레포 구조 자동 탐지 (--auto-detect)

사용법:
    # Agent 출력에서 직접 변환 + 평가
    python run_evaluation.py \\
        --tech ../tech_analysis_agent/output_tech_candidates.json \\
        --roadmap ../roadmap_planner_agent/output_planned_roadmap.json \\
        --investment ../investment_strategist_agent/output_investment_strategy.json

    # 자동 탐지
    python run_evaluation.py --auto-detect

    # 기존 input_pack
    python run_evaluation.py --input samples/sample_input_multi_agent.json

    # Pairwise 비교
    python run_evaluation.py --input full.json --compare noA3.json

    # 앙상블 LLM Judge
    python run_evaluation.py --input pack.json --provider anthropic openai gemini

    # Holdout 추출 + 평가
    python run_evaluation.py --input pack.json --extract --connector mock
"""

import argparse
import json
import re
import sys
from pathlib import Path

from trm_evaluation import TRMEvaluationSuite
from adapters.roadmap_planning_agent import AgentOutputAdapter
from data_sources import (
    HoldoutExtractor, PatentDataSource, MarketDataSource,
    MockPatentConnector, MockMarketConnector, CSVMarketConnector,
    TavilyMarketConnector, USPTOConnector,
)


def load_json(path):
    p = Path(path)
    if not p.exists():
        print(f"✗ 파일 없음: {path}"); sys.exit(1)
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def validate(pack):
    required = ["metadata", "tech_candidates", "planned_roadmap",
                "investment_strategy", "constraints"]
    missing = [k for k in required if k not in pack]
    if missing:
        print(f"✗ 필수 필드 누락: {missing}"); return False
    n_tech = len(pack["tech_candidates"])
    n_road = len(pack["planned_roadmap"])
    has_h = "holdout_data" in pack and pack["holdout_data"]
    print(f"✓ 검증 통과 ({n_tech} 기술, {n_road} 로드맵, holdout: {'O' if has_h else 'X'})")
    return True


def auto_detect(base=".."):
    base = Path(base)
    checks = {
        "tech": [base/"tech_analysis_agent"/"output_tech_candidates.json"],
        "roadmap": [base/"roadmap_planner_agent"/"output_planned_roadmap.json"],
        "investment": [base/"investment_strategist_agent"/"output_investment_strategy.json"],
        "report": [base/"orchestration_agent"/"outputs"/"orchestrator_report.json",
                   base/"orchestration_agent"/"orchestrator_report.json"],
    }
    found = {}
    for key, paths in checks.items():
        for p in paths:
            if p.exists():
                found[key] = str(p); break
    return found


def build_extractor(connector, baseline, evaluation, window=5, mode="global"):
    """커넥터 + 모드에 따라 HoldoutExtractor 생성"""
    if "uspto" in connector:
        ps = PatentDataSource(USPTOConnector())
    else:
        ps = PatentDataSource(MockPatentConnector({}))
    if "tavily" in connector:
        ms = MarketDataSource(TavilyMarketConnector())
    elif "csv" in connector:
        ms = MarketDataSource(CSVMarketConnector())
    else:
        ms = MarketDataSource(MockMarketConnector({}))
    return HoldoutExtractor(ps, ms, baseline, evaluation, window, mode=mode)


def main():
    p = argparse.ArgumentParser(description="TRM Evaluation Runner")

    g = p.add_argument_group("입력")
    g.add_argument("--input", help="조립된 input_pack JSON")
    g.add_argument("--tech", help="output_tech_candidates.json")
    g.add_argument("--roadmap", help="output_planned_roadmap.json")
    g.add_argument("--investment", help="output_investment_strategy.json")
    g.add_argument("--report", help="orchestrator_report.json (선택)")
    g.add_argument("--auto-detect", action="store_true")
    g.add_argument("--repo-root", default="..")
    g.add_argument("--scenario", default="multi-agent")

    p.add_argument("--compare", help="Pairwise 비교 대상 JSON")
    p.add_argument("--provider", nargs="+", choices=["anthropic","openai","gemini"])
    p.add_argument("--aggregation", default="mean", choices=["mean","median"])

    p.add_argument("--extract", action="store_true",
                   help="holdout 데이터 DB에서 추출")
    p.add_argument("--connector", default="mock",
                   help="holdout 커넥터: mock, csv, tavily, uspto, csv+uspto, tavily+uspto")
    p.add_argument("--holdout-mode", default="global", choices=["global", "per-tech"],
                   help="global: 모든 기술 동일 시점 / per-tech: 기술별 start_q/target_q 기반")
    p.add_argument("--baseline-date", default="2020-12-31")
    p.add_argument("--evaluation-date", default="2025-12-31")

    p.add_argument("--output", help="결과 JSON 저장 경로")
    p.add_argument("--save-input-pack", help="변환된 input_pack 저장")
    p.add_argument("-v", "--verbose", action="store_true")

    args = p.parse_args()
    adapter = AgentOutputAdapter()

    # ── 입력 로드 ──
    if args.input:
        print(f"\n[1] Input: {args.input}")
        input_pack = load_json(args.input)
    elif args.auto_detect:
        print(f"\n[1] 자동 탐지 (root: {args.repo_root})")
        found = auto_detect(args.repo_root)
        for k, v in found.items(): print(f"    {k}: {v}")
        if not {"tech","roadmap","investment"}.issubset(found):
            print("    ✗ 필수 파일 부족"); sys.exit(1)
        input_pack = adapter.from_files(
            found["tech"], found["roadmap"], found["investment"],
            report_path=found.get("report"), scenario_label=args.scenario)
    elif args.tech and args.roadmap and args.investment:
        print(f"\n[1] Agent 출력 변환")
        input_pack = adapter.from_files(
            args.tech, args.roadmap, args.investment,
            report_path=args.report, scenario_label=args.scenario)
    else:
        print("✗ --input, --auto-detect, 또는 --tech/--roadmap/--investment 필요")
        sys.exit(1)

    if args.save_input_pack:
        with open(args.save_input_pack, "w", encoding="utf-8") as f:
            json.dump(input_pack, f, indent=2, ensure_ascii=False)
        print(f"    ✓ input_pack 저장: {args.save_input_pack}")

    # ── Holdout ──
    if args.extract or "holdout_data" not in input_pack:
        hm = args.holdout_mode
        print(f"\n[1.5] Holdout 추출 ({args.connector}, mode={hm})")
        try:
            ext = build_extractor(args.connector, args.baseline_date,
                                  args.evaluation_date, mode=hm)
            input_pack = ext.extract_and_assemble(input_pack)
            print(f"      ✓ {len(input_pack['holdout_data'])}개 기술")
            if hm == "per-tech":
                for tid, hd in input_pack["holdout_data"].items():
                    print(f"        {tid}: {hd['baseline_date']} → {hd['evaluation_date']}")
        except NotImplementedError:
            print(f"    ⚠ {args.connector} 미구현 — holdout 없이 진행")

    if not validate(input_pack): sys.exit(1)

    # ── Suite ──
    use_api = bool(args.provider)
    suite = (TRMEvaluationSuite(providers=args.provider, aggregation=args.aggregation)
             if args.provider else TRMEvaluationSuite())
    mode = f"{'+'.join(args.provider)} ({args.aggregation})" if args.provider else "rule-based"
    print(f"[2] LLM Judge: {mode}")

    # ── 실행 ──
    if args.compare:
        cmp_pack = load_json(args.compare)
        result = suite.compare(input_pack, cmp_pack, use_api=use_api)
        suite.print_summary(result["report_A"])
        suite.print_summary(result["report_B"])
        pw = result["pairwise"]
        print("━"*60 + "\n  Pairwise 결과\n" + "━"*60)
        print(f"  LLM   → A:{pw['llm_judge_comparison']['score_A']:.1f} "
              f"B:{pw['llm_judge_comparison']['score_B']:.1f} "
              f"W:{pw['llm_judge_comparison']['llm_winner']}")
        print(f"  BT    → A:{pw['backtest_comparison']['score_A']:.1f} "
              f"B:{pw['backtest_comparison']['score_B']:.1f} "
              f"W:{pw['backtest_comparison']['backtest_winner']}")
        print(f"  Final → A:{pw['final_comparison']['score_A']:.1f} "
              f"B:{pw['final_comparison']['score_B']:.1f} "
              f"Δ={pw['final_comparison']['delta']:.1f} "
              f"W:{pw['final_comparison']['final_winner']}\n")
    else:
        print(f"\n[3] 평가 실행...\n")
        result = suite.evaluate(input_pack, use_api=use_api)
        suite.print_summary(result)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"✓ 결과 저장: {args.output}")

    if args.verbose:
        print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
