#!/usr/bin/env python3
"""
demo.py — TRM Evaluation Suite 데모
═══════════════════════════════════════
Agent 연결 없이 시뮬레이션 데이터로 전체 파이프라인을 시연합니다.

사용법:
    python demo.py              # 전체 3개 시나리오
    python demo.py --scenario 1 # 시나리오 1만
    python demo.py --scenario 2 # Ablation 비교만
    python demo.py --scenario 3 # Back test만
"""

import json
import copy
import time
import sys
import argparse

from adapters.roadmap_planning_agent import AgentOutputAdapter
from trm_evaluation import TRMEvaluationSuite
from data_sources import (
    HoldoutExtractor, PatentDataSource, MarketDataSource,
    MockPatentConnector, MockMarketConnector,
)


# ═══════════════════════════════════════════════════════════════
#  시뮬레이션 데이터: Agent 출력 Mock
#  (실제 Agent가 뱉는 것과 동일한 포맷)
# ═══════════════════════════════════════════════════════════════

MOCK_TECH_CANDIDATES = {
    "market_context": {
        "target_market": "AI 반도체",
        "expected_boom_quarter": "2027 Q2",
        "key_drivers": ["AI/HPC 수요 폭증", "2nm 이하 공정 경쟁", "Chiplet 패러다임"]
    },
    "tech_candidates": [
        {
            "tech_id": "T01", "name": "GAA 나노시트 공정", "category": "Process",
            "trl": 5, "patent_score": 78.2, "market_score": 84.5, "final_score": 81.7,
            "expected_market_boom_quarter": "2027 Q3",
            "dependency_hints": ["T03"], "rationale": "3nm 이하 로직 소자의 핵심 아키텍처"
        },
        {
            "tech_id": "T02", "name": "EUV 리소그래피", "category": "Equipment",
            "trl": 8, "patent_score": 92.1, "market_score": 88.3, "final_score": 90.0,
            "expected_market_boom_quarter": "2026 Q1",
            "dependency_hints": [], "rationale": "2nm 이하 패터닝의 유일한 양산 솔루션"
        },
        {
            "tech_id": "T03", "name": "High-k 게이트 유전체", "category": "Material",
            "trl": 7, "patent_score": 65.4, "market_score": 71.2, "final_score": 68.6,
            "expected_market_boom_quarter": "2026 Q4",
            "dependency_hints": [], "rationale": "GAA FET 게이트 스택의 핵심 소재"
        },
        {
            "tech_id": "T04", "name": "하이브리드 본딩", "category": "Packaging",
            "trl": 6, "patent_score": 73.8, "market_score": 79.6, "final_score": 77.0,
            "expected_market_boom_quarter": "2027 Q1",
            "dependency_hints": ["T02"], "rationale": "3D IC 적층의 차세대 표준"
        },
        {
            "tech_id": "T05", "name": "Backside PDN 공정", "category": "Process",
            "trl": 3, "patent_score": 55.0, "market_score": 62.1, "final_score": 58.9,
            "expected_market_boom_quarter": "2029 Q2",
            "dependency_hints": ["T01", "T03"], "rationale": "전력 효율 혁신 (2nm 이후)"
        },
        {
            "tech_id": "T06", "name": "칩렛 헤테로지니어스", "category": "Architecture",
            "trl": 6, "patent_score": 70.5, "market_score": 82.3, "final_score": 77.0,
            "expected_market_boom_quarter": "2027 Q2",
            "dependency_hints": ["T04"], "rationale": "SoC 대안으로 다이 분할 전략"
        },
    ]
}

MOCK_PLANNED_ROADMAP = {
    "market_context": MOCK_TECH_CANDIDATES["market_context"],
    "planned_roadmap": [
        {"tech_id": "T02", "name": "EUV 리소그래피", "phase_name": "1단계: 장비 확보",
         "start_q": "2025 Q1", "target_q": "2026 Q2", "prerequisites": [],
         "lead_time_quarters": 5, "justification": "양산 장비 확보가 전체 로드맵의 전제"},
        {"tech_id": "T03", "name": "High-k 게이트 유전체", "phase_name": "1단계: 소재 R&D",
         "start_q": "2025 Q1", "target_q": "2026 Q3", "prerequisites": [],
         "lead_time_quarters": 6, "justification": "GAA 공정의 핵심 선행 소재"},
        {"tech_id": "T01", "name": "GAA 나노시트 공정", "phase_name": "2단계: 공정 개발",
         "start_q": "2026 Q4", "target_q": "2028 Q1", "prerequisites": ["T03"],
         "lead_time_quarters": 5, "justification": "High-k 소재 확보 후 공정 통합"},
        {"tech_id": "T04", "name": "하이브리드 본딩", "phase_name": "2단계: 패키징",
         "start_q": "2026 Q3", "target_q": "2027 Q4", "prerequisites": ["T02"],
         "lead_time_quarters": 5, "justification": "EUV 기반 다이 위에 3D 적층"},
        {"tech_id": "T06", "name": "칩렛 헤테로지니어스", "phase_name": "3단계: 통합",
         "start_q": "2028 Q1", "target_q": "2029 Q2", "prerequisites": ["T04"],
         "lead_time_quarters": 5, "justification": "하이브리드 본딩 기반 칩렛 통합"},
        {"tech_id": "T05", "name": "Backside PDN 공정", "phase_name": "3단계: 차세대",
         "start_q": "2028 Q2", "target_q": "2029 Q4", "prerequisites": ["T01", "T03"],
         "lead_time_quarters": 7, "justification": "GAA 공정 안정화 후 후면 전력망"},
    ]
}

MOCK_INVESTMENT_STRATEGY = {
    "market_context": MOCK_TECH_CANDIDATES["market_context"],
    "stages": [
        {"stage": "1단계: 장비·소재 확보", "period": "2025 Q1 - 2026 Q3",
         "goal": "EUV 장비 도입 + High-k 소재 R&D",
         "technologies": ["EUV 리소그래피", "High-k 게이트 유전체"],
         "tech_ids": ["T02", "T03"], "num_items": 2},
        {"stage": "2단계: 공정·패키징 개발", "period": "2026 Q3 - 2028 Q1",
         "goal": "GAA 공정 개발 + 하이브리드 본딩 적용",
         "technologies": ["GAA 나노시트 공정", "하이브리드 본딩"],
         "tech_ids": ["T01", "T04"], "num_items": 2},
        {"stage": "3단계: 통합·차세대", "period": "2028 Q1 - 2029 Q4",
         "goal": "칩렛 통합 + BSPDN 적용",
         "technologies": ["칩렛 헤테로지니어스", "Backside PDN 공정"],
         "tech_ids": ["T06", "T05"], "num_items": 2},
    ],
    "investment_strategy": [
        {"stage": "1단계: 장비·소재 확보", "period": "2025 Q1 - 2026 Q3",
         "evaluation_scores": {"market_opportunity": 5, "strategic_fit": 5,
                               "executability": 4, "uncertainty": 2, "urgency": 5},
         "investment_attractiveness": "high", "investment_urgency": "high",
         "recommended_investment_tier": "Tier 1",
         "investment_scope": "장비 구매 + 소재 공동연구",
         "recommended_action": "즉시 대규모 투자 집행",
         "rationale": ["EUV는 ASML 독점이라 선제 확보 필수", "High-k는 GAA의 선행 조건"],
         "major_risks": ["ASML 납기 지연", "소재 수율 불확실"],
         "resource_focus": ["장비 발주", "소재 파트너십"]},
        {"stage": "2단계: 공정·패키징 개발", "period": "2026 Q3 - 2028 Q1",
         "evaluation_scores": {"market_opportunity": 4, "strategic_fit": 4,
                               "executability": 3, "uncertainty": 3, "urgency": 4},
         "investment_attractiveness": "high", "investment_urgency": "medium",
         "recommended_investment_tier": "Tier 2",
         "investment_scope": "공정 라인 구축 + 패키징 파일럿",
         "recommended_action": "단계적 투자 (마일스톤 기반)",
         "rationale": ["GAA는 3nm 이하 필수", "하이브리드 본딩은 경쟁 가속"],
         "major_risks": ["수율 확보 시간", "다이 크기 제약"],
         "resource_focus": ["Fab 라인", "패키징 R&D"]},
        {"stage": "3단계: 통합·차세대", "period": "2028 Q1 - 2029 Q4",
         "evaluation_scores": {"market_opportunity": 3, "strategic_fit": 4,
                               "executability": 2, "uncertainty": 4, "urgency": 2},
         "investment_attractiveness": "medium", "investment_urgency": "low",
         "recommended_investment_tier": "Tier 3",
         "investment_scope": "연구 파일럿 + 컨소시엄 참여",
         "recommended_action": "탐색적 투자 유지, 기술 성숙도 모니터링",
         "rationale": ["칩렛은 시장 확대 중이나 표준 미확립", "BSPDN은 TRL 낮아 불확실"],
         "major_risks": ["표준 분열", "양산 전환 시기 불확실"],
         "resource_focus": ["컨소시엄", "논문/특허"]},
    ]
}

MOCK_ORCHESTRATOR_REPORT = {
    "problem_frame": {
        "industry": "AI 반도체",
        "company_type": "Tier-1 파운드리",
        "time_horizon": "2025-2030",
        "total_budget": 5_000_000_000,
        "objective": "2nm 이하 첨단 공정 기술 리더십 확보",
        "strategic_priorities": [
            "GAA 기반 2nm 공정 양산",
            "3D IC 패키징 역량 확보",
            "AI 가속기 전용 최적화"
        ],
        "future_trend_summary": "AI/HPC 반도체 수요 급증, 2nm 이하 GAA 공정 전환 가속"
    },
    "active_agents": ["1", "2", "3"],
}

# Holdout 데이터 (mock: 2020년 baseline vs 2025년 realized)
HOLDOUT_FIXTURE_PATENT = {
    "gaa|nanosheet|나노시트": {
        "2020-12-31": {"total": 85}, "2025-12-31": {"total": 310},
    },
    "euv|리소그래피|lithography": {
        "2020-12-31": {"total": 520}, "2025-12-31": {"total": 1200},
    },
    "high k|게이트|dielectric": {
        "2020-12-31": {"total": 340}, "2025-12-31": {"total": 480},
    },
    "hybrid bonding|하이브리드|본딩": {
        "2020-12-31": {"total": 95}, "2025-12-31": {"total": 380},
    },
    "backside pdn|후면|power delivery": {
        "2020-12-31": {"total": 25}, "2025-12-31": {"total": 120},
    },
    "chiplet|칩렛|heterogeneous": {
        "2020-12-31": {"total": 110}, "2025-12-31": {"total": 450},
    },
}
HOLDOUT_FIXTURE_MARKET = {
    "gaa|nanosheet|나노시트": {
        "2020-12-31": {"market_size_m": 800}, "2025-12-31": {"market_size_m": 4200},
    },
    "euv|리소그래피|lithography": {
        "2020-12-31": {"market_size_m": 6500}, "2025-12-31": {"market_size_m": 18000},
    },
    "high k|게이트|dielectric": {
        "2020-12-31": {"market_size_m": 2200}, "2025-12-31": {"market_size_m": 3800},
    },
    "hybrid bonding|하이브리드|본딩": {
        "2020-12-31": {"market_size_m": 500}, "2025-12-31": {"market_size_m": 3500},
    },
    "backside pdn|후면|power delivery": {
        "2020-12-31": {"market_size_m": 100}, "2025-12-31": {"market_size_m": 600},
    },
    "chiplet|칩렛|heterogeneous": {
        "2020-12-31": {"market_size_m": 1800}, "2025-12-31": {"market_size_m": 8500},
    },
}


# ═══════════════════════════════════════════════════════════════
#  Helper
# ═══════════════════════════════════════════════════════════════

def banner(title, char="━", width=70):
    print(f"\n{char * width}")
    print(f"  {title}")
    print(f"{char * width}\n")


def step(msg):
    print(f"  → {msg}")
    time.sleep(0.15)  # 시각적 흐름감


def print_score_bar(label, score, width=30):
    filled = int(score / 100 * width)
    bar = "█" * filled + "░" * (width - filled)
    color = "\033[92m" if score >= 70 else "\033[93m" if score >= 50 else "\033[91m"
    reset = "\033[0m"
    print(f"    {label:<25s} {color}{bar}{reset} {score:>5.1f}")


def make_noA_scenario(agent_off):
    """Ablation: 특정 Agent OFF 시뮬레이션"""
    tech = copy.deepcopy(MOCK_TECH_CANDIDATES)
    roadmap = copy.deepcopy(MOCK_PLANNED_ROADMAP)
    invest = copy.deepcopy(MOCK_INVESTMENT_STRATEGY)
    report = copy.deepcopy(MOCK_ORCHESTRATOR_REPORT)

    if agent_off == 1:
        # Agent 1 OFF → 더미 후보 3개
        tech["tech_candidates"] = [
            {"tech_id": "D01", "name": "더미 기술 A", "category": "General",
             "trl": 5, "patent_score": 50, "market_score": 50, "final_score": 50,
             "expected_market_boom_quarter": "2027 Q1", "dependency_hints": [], "rationale": "dummy"},
            {"tech_id": "D02", "name": "더미 기술 B", "category": "General",
             "trl": 5, "patent_score": 50, "market_score": 50, "final_score": 50,
             "expected_market_boom_quarter": "2028 Q1", "dependency_hints": [], "rationale": "dummy"},
            {"tech_id": "D03", "name": "더미 기술 C", "category": "General",
             "trl": 5, "patent_score": 50, "market_score": 50, "final_score": 50,
             "expected_market_boom_quarter": "2029 Q1", "dependency_hints": [], "rationale": "dummy"},
        ]
        roadmap["planned_roadmap"] = [
            {"tech_id": "D01", "name": "더미 기술 A", "phase_name": "단일 Phase",
             "start_q": "2025 Q1", "target_q": "2027 Q4", "prerequisites": [],
             "lead_time_quarters": 11, "justification": "dummy"},
            {"tech_id": "D02", "name": "더미 기술 B", "phase_name": "단일 Phase",
             "start_q": "2025 Q1", "target_q": "2028 Q4", "prerequisites": [],
             "lead_time_quarters": 15, "justification": "dummy"},
            {"tech_id": "D03", "name": "더미 기술 C", "phase_name": "단일 Phase",
             "start_q": "2025 Q1", "target_q": "2029 Q4", "prerequisites": [],
             "lead_time_quarters": 19, "justification": "dummy"},
        ]
        invest["stages"] = [
            {"stage": "단일 Phase", "tech_ids": ["D01","D02","D03"],
             "technologies": ["더미 기술 A","더미 기술 B","더미 기술 C"], "num_items": 3}
        ]
        invest["investment_strategy"] = [
            {"stage": "단일 Phase", "evaluation_scores": {"market_opportunity": 3, "strategic_fit": 3,
             "executability": 3, "uncertainty": 3, "urgency": 3},
             "recommended_investment_tier": "Tier 2"}
        ]
        report["active_agents"] = ["2", "3"]

    elif agent_off == 2:
        # Agent 2 OFF → flat roadmap (의존성 없음, 단일 phase)
        for r in roadmap["planned_roadmap"]:
            r["phase_name"] = "Flat Phase"
            r["prerequisites"] = []
            r["start_q"] = "2025 Q1"
        report["active_agents"] = ["1", "3"]

    elif agent_off == 3:
        # Agent 3 OFF → 빈 투자 전략
        invest["stages"] = []
        invest["investment_strategy"] = []
        report["active_agents"] = ["1", "2"]

    return tech, roadmap, invest, report


# ═══════════════════════════════════════════════════════════════
#  시나리오 1: Agent 출력 → 어댑터 → 평가
# ═══════════════════════════════════════════════════════════════

def scenario_1():
    banner("시나리오 1: Agent 출력 시뮬레이션 → 어댑터 변환 → 평가")

    # 1) Agent 출력 시뮬레이션
    step("Agent 1 (Technology Analyst) 출력 시뮬레이션...")
    print(f"    후보 기술 {len(MOCK_TECH_CANDIDATES['tech_candidates'])}개 생성")
    for t in MOCK_TECH_CANDIDATES["tech_candidates"]:
        print(f"      {t['tech_id']} {t['name']:<20s}  score={t['final_score']:.1f}  TRL={t['trl']}  boom={t['expected_market_boom_quarter']}")

    step("Agent 2 (Roadmap Planner) 출력 시뮬레이션...")
    print(f"    로드맵 {len(MOCK_PLANNED_ROADMAP['planned_roadmap'])}개 항목")
    for r in MOCK_PLANNED_ROADMAP["planned_roadmap"]:
        print(f"      {r['tech_id']} {r['start_q']} → {r['target_q']}  prereq={r['prerequisites']}  ({r['phase_name']})")

    step("Agent 3 (Investment Strategist) 출력 시뮬레이션...")
    print(f"    투자 전략 {len(MOCK_INVESTMENT_STRATEGY['investment_strategy'])}개 stage")
    for s in MOCK_INVESTMENT_STRATEGY["investment_strategy"]:
        sc = s["evaluation_scores"]
        print(f"      {s['stage']:<30s}  Tier={s['recommended_investment_tier']}  "
              f"opp={sc['market_opportunity']} fit={sc['strategic_fit']} exec={sc['executability']}")

    # 2) 어댑터 변환
    step("어댑터 변환 (Agent JSON → input_pack)...")
    adapter = AgentOutputAdapter()
    input_pack = adapter.convert(
        MOCK_TECH_CANDIDATES, MOCK_PLANNED_ROADMAP, MOCK_INVESTMENT_STRATEGY,
        MOCK_ORCHESTRATOR_REPORT, scenario_label="multi-agent (full)"
    )
    print(f"    ✓ 변환 완료")
    print(f"      tech_candidates:     {len(input_pack['tech_candidates'])}개")
    print(f"      planned_roadmap:     {len(input_pack['planned_roadmap'])}개")
    print(f"      investment_strategy: {len(input_pack['investment_strategy'])}개")
    print(f"      name→tech_name:      '{input_pack['tech_candidates'][0]['tech_name']}'")
    print(f"      '2025 Q1'→'{input_pack['planned_roadmap'][0]['start_q']}'")
    print(f"      'Tier 1'→'{input_pack['investment_strategy'][0]['investment_tier']}'")

    # 3) Holdout 추출
    step("Holdout 추출 (Mock DB, baseline=2020, eval=2025)...")
    extractor = HoldoutExtractor(
        patent_source=PatentDataSource(MockPatentConnector(HOLDOUT_FIXTURE_PATENT)),
        market_source=MarketDataSource(MockMarketConnector(HOLDOUT_FIXTURE_MARKET)),
        baseline_date="2020-12-31",
        evaluation_date="2025-12-31",
    )
    input_pack = extractor.extract_and_assemble(input_pack)
    print(f"    ✓ {len(input_pack['holdout_data'])}개 기술 holdout 데이터 추출")

    # 4) 평가
    step("평가 실행 (rule-based LLM Judge + Back Test)...")
    suite = TRMEvaluationSuite()
    report = suite.evaluate(input_pack)

    # 5) 결과 출력
    banner("평가 결과", "─")
    suite.print_summary(report)

    print("  ── 기술별 Back Test Value ──")
    for td in report["backtest"]["tech_details"]:
        val_pct = td["value"] * 100
        print_score_bar(td["tech_name"][:25], val_pct)

    return input_pack, report


# ═══════════════════════════════════════════════════════════════
#  시나리오 2: Ablation 비교
# ═══════════════════════════════════════════════════════════════

def scenario_2():
    banner("시나리오 2: Ablation 비교 (full vs noA1 vs noA2 vs noA3)")

    adapter = AgentOutputAdapter()
    suite = TRMEvaluationSuite()
    results = {}

    configs = [
        ("full",  None,  "Agent 1+2+3 전부 ON"),
        ("noA1",  1,     "Agent 1 OFF (더미 후보)"),
        ("noA2",  2,     "Agent 2 OFF (flat 로드맵)"),
        ("noA3",  3,     "Agent 3 OFF (투자 전략 없음)"),
    ]

    for label, off, desc in configs:
        step(f"{label}: {desc}")
        if off is None:
            tech, roadmap, invest, report_data = (
                MOCK_TECH_CANDIDATES, MOCK_PLANNED_ROADMAP,
                MOCK_INVESTMENT_STRATEGY, MOCK_ORCHESTRATOR_REPORT
            )
        else:
            tech, roadmap, invest, report_data = make_noA_scenario(off)

        pack = adapter.convert(tech, roadmap, invest, report_data,
                               scenario_label=label)

        # Holdout (full과 동일한 기준)
        ext = HoldoutExtractor(
            patent_source=PatentDataSource(MockPatentConnector(HOLDOUT_FIXTURE_PATENT)),
            market_source=MarketDataSource(MockMarketConnector(HOLDOUT_FIXTURE_MARKET)),
            baseline_date="2020-12-31", evaluation_date="2025-12-31",
        )
        pack = ext.extract_and_assemble(pack)
        r = suite.evaluate(pack)
        results[label] = r
        print(f"    → Composite: {r['composite']['final_composite_score']:.1f}  "
              f"(LLM={r['llm_judge']['llm_structural_score']:.1f}, "
              f"BT={r['backtest']['backtest_score']:.1f}, "
              f"penalty=-{r['composite']['total_penalty']:.1f})")

    # 비교 테이블
    banner("Ablation 비교 결과", "─")
    header = f"  {'시나리오':<10s} {'LLM Judge':>10s} {'Backtest':>10s} {'Penalty':>10s} {'Composite':>12s}"
    print(header)
    print("  " + "─" * 55)
    for label, r in results.items():
        llm = r['llm_judge']['llm_structural_score']
        bt = r['backtest']['backtest_score']
        pen = r['composite']['total_penalty']
        comp = r['composite']['final_composite_score']
        print(f"  {label:<10s} {llm:>10.1f} {bt:>10.1f} {pen:>10.1f} {comp:>12.1f}")

    # Delta from full
    full_score = results["full"]["composite"]["final_composite_score"]
    print()
    print("  ── full 대비 Delta ──")
    for label in ["noA1", "noA2", "noA3"]:
        delta = results[label]["composite"]["final_composite_score"] - full_score
        arrow = "▼" if delta < 0 else "▲"
        color = "\033[91m" if delta < 0 else "\033[92m"
        reset = "\033[0m"
        print(f"    {label}: {color}{arrow} {abs(delta):.1f}pt{reset}")

    return results


# ═══════════════════════════════════════════════════════════════
#  시나리오 3: Mock Back Test
# ═══════════════════════════════════════════════════════════════

def scenario_3():
    banner("시나리오 3: Back Test 상세 (holdout 데이터 기반 정량 평가)")

    adapter = AgentOutputAdapter()
    pack = adapter.convert(
        MOCK_TECH_CANDIDATES, MOCK_PLANNED_ROADMAP, MOCK_INVESTMENT_STRATEGY,
        MOCK_ORCHESTRATOR_REPORT, scenario_label="backtest-demo"
    )

    # Holdout
    ext = HoldoutExtractor(
        patent_source=PatentDataSource(MockPatentConnector(HOLDOUT_FIXTURE_PATENT)),
        market_source=MarketDataSource(MockMarketConnector(HOLDOUT_FIXTURE_MARKET)),
        baseline_date="2020-12-31", evaluation_date="2025-12-31",
    )
    pack = ext.extract_and_assemble(pack)

    step("Holdout 데이터 (baseline 2020 vs realized 2025):")
    for tid, hd in pack["holdout_data"].items():
        bp = hd.get("baseline_patents", "?")
        rp = hd.get("realized_patents", "?")
        bm = hd.get("baseline_market_m", "?")
        rm = hd.get("realized_market_m", "?")
        growth = f"+{((rp/bp - 1)*100):.0f}%" if isinstance(bp, (int,float)) and bp > 0 else "?"
        print(f"    {tid}  patents: {bp}→{rp} ({growth})  market: ${bm}M→${rm}M")

    step("평가 실행...")
    suite = TRMEvaluationSuite()
    report = suite.evaluate(pack)

    # Back test 상세
    banner("Back Test 지표 상세", "─")

    metrics = [
        ("Selection Quality", report["backtest"]["selection_quality"]),
        ("Cost-Adjusted Return", report["backtest"]["cost_adjusted_return"]),
        ("Investment Rationality", report["backtest"]["investment_rationality_score"]),
        ("Budget Feasibility", report["backtest"]["budget_feasibility_score"]),
        ("Dependency Validity", report["backtest"]["dependency_validity_score"]),
        ("Timing Accuracy", report["backtest"]["timing_accuracy_score"]),
    ]
    weights = [0.30, 0.30, 0.15, 0.10, 0.10, 0.05]

    for (label, score), w in zip(metrics, weights):
        w_str = f"(w={w:.2f})"
        print_score_bar(f"{label} {w_str}", score)

    print()
    bt_total = report["backtest"]["backtest_score"]
    print(f"    {'Backtest Total':<25s} {'':>30s} {bt_total:>5.1f}")

    # Constraint 위반
    cr = report["constraints"]
    print(f"\n  ── Constraint 위반 ──")
    print(f"    예산 위반:     {cr['budget_violation_pct']:.1f}%")
    print(f"    의존성 위반:   {cr['dependency_violations']}건")
    print(f"    타이밍 오차:   {cr['mean_timing_error_years']:.2f}년")

    if cr.get("dependency_details"):
        for d in cr["dependency_details"]:
            print(f"      ⚠ {d['tech_name']}: 선행 {d['prerequisite']} 완료 전 시작 (gap: {d['gap_quarters']}Q)")

    # 수식 재현
    banner("수식 재현", "─")
    llm = report["llm_judge"]["llm_structural_score"]
    bt = report["backtest"]["backtest_score"]
    pen = report["composite"]["total_penalty"]
    pre = report["composite"]["pre_penalty_score"]
    final = report["composite"]["final_composite_score"]

    print(f"    LLM Structural Score  = {llm:.1f}")
    print(f"    Backtest Score        = {bt:.1f}")
    print(f"    Pre-penalty           = 0.4 × {llm:.1f} + 0.6 × {bt:.1f} = {pre:.1f}")
    print(f"    Penalty               = {pen:.1f}")
    print(f"    Final Composite       = {pre:.1f} - {pen:.1f} = {final:.1f}")

    return report


# ═══════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="TRM Evaluation Demo")
    parser.add_argument("--scenario", type=int, choices=[1, 2, 3],
                        help="특정 시나리오만 실행 (1/2/3)")
    args = parser.parse_args()

    print()
    print("╔══════════════════════════════════════════════════════════════════╗")
    print("║           TRM Final Evaluation Suite — Demo                    ║")
    print("║   LLM Judge + Back Test → Composite Score                      ║")
    print("╚══════════════════════════════════════════════════════════════════╝")

    scenarios = [1, 2, 3] if args.scenario is None else [args.scenario]

    if 1 in scenarios:
        scenario_1()
    if 2 in scenarios:
        scenario_2()
    if 3 in scenarios:
        scenario_3()

    banner("데모 완료", "═")
    print("  다음 단계:")
    print("    • 실제 Agent 출력으로 평가: python run_evaluation.py --auto-detect")
    print("    • LLM 앙상블 평가:          --provider anthropic openai gemini")
    print("    • 웹 데모 (다음 대화에서):    python demo_web.py")
    print()


if __name__ == "__main__":
    main()
