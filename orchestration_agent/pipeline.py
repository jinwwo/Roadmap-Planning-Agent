"""
pipeline.py
────────────
Orchestration Agent 의 핵심 실행 루프.

main.py 에서 호출하는 유일한 공개 엔트리는 `run_orchestration(...)`.

흐름:

  Orchestrator Setup (LLM 없이 CLI 인자 → ProblemFrame)
    │
    ▼
  ┌─ Agent 1 (Technology Analyst)    ← active_agents 에 따라 ON/OFF
  │        subprocess: tech_analysis_agent/graphs.analysis_graph.run_technology_analysis()
  │
  ├─ Agent 2 (Roadmap Planner)       ← active_agents 에 따라 ON/OFF
  │        subprocess: roadmap_planner_agent/graphs.roadmap_graph.run_roadmap_planner()
  │
  └─ Agent 3 (Investment Strategist) ← active_agents 에 따라 ON/OFF
           subprocess: investment_strategist_agent/agents/...
                       (stage_aggregator → strategist)

  Orchestrator Review (LLM)
    │
    ├─ ACCEPT → 7-섹션 report 반환, 종료
    └─ REVISE → refinement.rerun_agents 만 재실행 → 다시 Review
                 (최대 MAX_ORCHESTRATOR_ITERATIONS 회)

Agent OFF 전략 (보고서 성능 비교용):
  - Agent 1 OFF: 더미 후보 5개 + 고정 market_context (2028 Q1)
  - Agent 2 OFF: flat roadmap — 모든 기술을 단일 phase 에 배치, 의존성 / 역산 없음
  - Agent 3 OFF: 빈 investment_strategy (Orchestrator 가 결손을 감안해 평가)

sibling 에이전트들은 각자 자기 config/state/llm_factory 를 갖고 있어
라이브러리 import 시 네임 충돌이 나므로 **subprocess 로 각각 독립 실행** 합니다.
"""

import json
import os
import subprocess
import sys
from contextlib import contextmanager
from typing import List, Dict, Any, Optional, Callable

from agents.orchestrator import run_orchestrator_setup, run_orchestrator_review
from config import (
    SIBLING_TECH_ANALYST,
    SIBLING_ROADMAP_PLANNER,
    SIBLING_INVESTMENT_STRATEGIST,
    OUTPUTS_DIR,
    FILE_TECH_CANDIDATES,
    FILE_PLANNED_ROADMAP,
    FILE_INVESTMENT_STRATEGY,
    MAX_ORCHESTRATOR_ITERATIONS,
    SUBPROCESS_TIMEOUT_SEC,
)


# ──────────────────────────────────────────────────────────────
# 웹 세션용 훅 (옵션)
#   - CLI 실행 시: 훅 없음 → subprocess stdout 은 부모 프로세스로 inherit
#   - 웹 세션(server.py) 실행 시:
#       stream_subprocess_stdout(bus.log) 로 sibling 로그 라인 캡처
#       events_to(bus.emit) 로 구조화 이벤트 (agent_start/end, review_* ...)
# ──────────────────────────────────────────────────────────────

_STDOUT_HOOK: Optional[Callable[[str], None]] = None
_EVENT_HOOK: Optional[Callable[[str, dict], None]] = None


@contextmanager
def stream_subprocess_stdout(hook: Optional[Callable[[str], None]]):
    """
    with 블록 내부에서 _run_subprocess 가 sibling 에이전트를 Popen + PIPE 로 띄워
    stdout 한 줄마다 hook(line) 를 호출하도록 합니다.
    """
    global _STDOUT_HOOK
    prev, _STDOUT_HOOK = _STDOUT_HOOK, hook
    try:
        yield
    finally:
        _STDOUT_HOOK = prev


@contextmanager
def events_to(cb: Optional[Callable[[str, dict], None]]):
    """
    run_orchestration 진행 중 주요 전환 시점(setup/agent_start/agent_end/
    review_start/review_end/refine 등)에 cb(type, payload) 를 호출합니다.
    """
    global _EVENT_HOOK
    prev, _EVENT_HOOK = _EVENT_HOOK, cb
    try:
        yield
    finally:
        _EVENT_HOOK = prev


def _emit(type_: str, **payload) -> None:
    if _EVENT_HOOK is not None:
        try:
            _EVENT_HOOK(type_, payload)
        except Exception:
            pass   # 이벤트 훅 실패는 파이프라인을 방해하지 않음


# ──────────────────────────────────────────────────────────────
# 공용 유틸
# ──────────────────────────────────────────────────────────────

def _output_path(filename: str, out_prefix: str = "") -> str:
    """outputs/<prefix><filename> 의 절대 경로"""
    return os.path.join(OUTPUTS_DIR, f"{out_prefix}{filename}")


def _run_subprocess(snippet: str, cwd: str, label: str) -> None:
    """
    sibling 에이전트를 별도 Python 프로세스로 실행합니다.

    _STDOUT_HOOK 이 설정되어 있지 않으면 stdout/stderr 를 그대로 상속 (CLI 모드).
    설정되어 있으면 Popen + PIPE 로 라인별로 hook 에 전달 (웹 세션 모드).
    """
    print(f"\n[Pipeline] ▶ subprocess 실행: {label}  (cwd={cwd})")
    if _STDOUT_HOOK is None:
        try:
            subprocess.run(
                [sys.executable, "-c", snippet],
                cwd=cwd,
                check=True,
                timeout=SUBPROCESS_TIMEOUT_SEC,
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"{label} subprocess 실패 (returncode={e.returncode})") from e
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"{label} 실행 시간 초과 ({SUBPROCESS_TIMEOUT_SEC}s)") from e
        return

    # 웹 세션 모드 — 라인별 스트리밍
    proc = subprocess.Popen(
        [sys.executable, "-u", "-c", snippet],   # -u : unbuffered stdout
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
        text=True,
        universal_newlines=True,
    )
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            try:
                _STDOUT_HOOK(line.rstrip("\n"))
            except Exception:
                pass
        ret = proc.wait(timeout=SUBPROCESS_TIMEOUT_SEC)
    except subprocess.TimeoutExpired as e:
        proc.kill()
        raise RuntimeError(f"{label} 실행 시간 초과 ({SUBPROCESS_TIMEOUT_SEC}s)") from e

    if ret != 0:
        raise RuntimeError(f"{label} subprocess 실패 (returncode={ret})")


def _safe_load(path: str) -> dict:
    """JSON 로드 — 실패 시 빈 dict"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"[Pipeline] ⚠️  파일 없음: {path}")
        return {}


# ──────────────────────────────────────────────────────────────
# Agent OFF 용 폴백 생성기
# ──────────────────────────────────────────────────────────────

def _write_dummy_tech_candidates(domain: str, output_path: str) -> None:
    """Agent 1 OFF 용 얕은 후보 5개 JSON 작성"""
    data = {
        "market_context": {"target_market": domain, "expected_boom_quarter": "2028 Q1"},
        "tech_candidates": [
            {
                "tech_id": f"D{i:02d}",
                "name": f"Placeholder Tech {i}",
                "category": "Process",
                "trl": 4,
                "patent_score": 50.0,
                "market_score": 50.0,
                "final_score": 50.0,
                "expected_market_boom_quarter": "2028 Q1",
                "dependency_hints": [],
                "rationale": "(Agent 1 disabled — placeholder for A/B comparison)",
            }
            for i in range(1, 6)
        ],
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _write_flat_roadmap(tech_candidates_path: str, output_path: str) -> None:
    """Agent 2 OFF 용 flat 로드맵 — 모든 기술을 단일 phase 에 배치"""
    data = _safe_load(tech_candidates_path)
    candidates = data.get("tech_candidates", [])
    market_context = data.get("market_context", {})
    boom = market_context.get("expected_boom_quarter", "2028 Q1")

    flat = []
    for c in candidates:
        flat.append({
            "tech_id": c.get("tech_id"),
            "name": c.get("name"),
            "phase_name": "Phase 1: Flat (Roadmap Planner OFF)",
            "start_q": "2025 Q1",
            "target_q": boom,
            "prerequisites": [],
            "lead_time_quarters": 8,
            "justification": "(Roadmap Planner disabled) 의존성/역산 분석 없이 일괄 배치.",
        })

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump({
            "market_context": market_context,
            "planned_roadmap": flat,
        }, f, ensure_ascii=False, indent=2)


def _write_empty_strategy(roadmap_path: str, output_path: str) -> None:
    """Agent 3 OFF 용 빈 투자 계획"""
    roadmap_data = _safe_load(roadmap_path)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump({
            "market_context": roadmap_data.get("market_context", {}),
            "investment_policy": {},
            "stages": [],
            "investment_strategy": [],
        }, f, ensure_ascii=False, indent=2)


# ──────────────────────────────────────────────────────────────
# Agent 1 — Technology Analyst (subprocess)
# ──────────────────────────────────────────────────────────────

def _run_agent1(
    state: Dict[str, Any],
    out_prefix: str = "",
) -> None:
    """
    tech_analysis_agent/graphs.analysis_graph.run_technology_analysis() 를
    subprocess 로 호출하여 결과를 state["path_tech_candidates"] 에 저장.
    """
    active = state["active_agents"]
    out_path = _output_path(FILE_TECH_CANDIDATES, out_prefix)
    state["path_tech_candidates"] = out_path

    _emit("agent_start", agent="1", label="Agent 1 · Technology Analyst",
          active=("1" in active))
    if "1" not in active:
        print("\n[Pipeline] Agent 1 (Technology Analyst) — ⛔ OFF → 더미 후보 사용")
        _write_dummy_tech_candidates(state["domain"], out_path)
    else:
        print("\n[Pipeline] Agent 1 (Technology Analyst) — ✅ ON")
        domain = state["domain"]
        ref_year = state["reference_year"]
        hints = state.get("category_hints") or []

        snippet = f"""
import sys, json, os
sys.path.insert(0, os.getcwd())
from graphs.analysis_graph import run_technology_analysis

result = run_technology_analysis(
    domain={domain!r},
    reference_year={int(ref_year)},
    category_hints={list(hints)!r},
)

out = {{
    "market_context": result.get("market_context") or {{}},
    "tech_candidates": result.get("tech_candidates") or [],
}}
with open({out_path!r}, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print(f"[Agent 1] 저장: {out_path!r} ({{len(out['tech_candidates'])}}개 후보)")
"""
        _run_subprocess(snippet, cwd=SIBLING_TECH_ANALYST, label="Technology Analyst")

    # 로드
    data = _safe_load(out_path)
    state["tech_candidates"] = data.get("tech_candidates", [])
    state["market_context"] = data.get("market_context", {})
    print(f"[Pipeline] Agent 1 결과 로드: {len(state['tech_candidates'])}개 후보")
    _emit("agent_end", agent="1", count=len(state["tech_candidates"]))
    _emit("candidates_ready",
          candidates=state["tech_candidates"],
          market_context=state["market_context"])


# ──────────────────────────────────────────────────────────────
# Agent 2 — Roadmap Planner (subprocess)
# ──────────────────────────────────────────────────────────────

def _run_agent2(
    state: Dict[str, Any],
    out_prefix: str = "",
) -> None:
    """
    roadmap_planner_agent/graphs.roadmap_graph.run_roadmap_planner() 를
    subprocess 로 호출. orchestrator_feedback (shift/drop/text) 도 전달.
    """
    active = state["active_agents"]
    out_path = _output_path(FILE_PLANNED_ROADMAP, out_prefix)
    state["path_planned_roadmap"] = out_path
    tech_path = state["path_tech_candidates"]

    _emit("agent_start", agent="2", label="Agent 2 · Roadmap Planner",
          active=("2" in active))
    if "2" not in active:
        print("\n[Pipeline] Agent 2 (Roadmap Planner) — ⛔ OFF → flat roadmap 생성")
        _write_flat_roadmap(tech_path, out_path)
    else:
        print("\n[Pipeline] Agent 2 (Roadmap Planner) — ✅ ON")
        feedback = state.get("orchestrator_feedback") or None

        snippet = f"""
import sys, json, os
sys.path.insert(0, os.getcwd())
from graphs.roadmap_graph import run_roadmap_planner

with open({tech_path!r}, "r", encoding="utf-8") as f:
    data = json.load(f)
tech_candidates = data.get("tech_candidates") or []
market_context = data.get("market_context") or {{}}

result = run_roadmap_planner(
    tech_candidates=tech_candidates,
    market_context=market_context,
    orchestrator_feedback={feedback!r},
)

out = {{
    "market_context": market_context,
    "planned_roadmap": result.get("planned_roadmap") or [],
    "dependency_tree": result.get("dependency_tree") or {{}},
}}
with open({out_path!r}, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print(f"[Agent 2] 저장: {out_path!r} ({{len(out['planned_roadmap'])}}개 항목)")
"""
        _run_subprocess(snippet, cwd=SIBLING_ROADMAP_PLANNER, label="Roadmap Planner")

    data = _safe_load(out_path)
    state["planned_roadmap"] = data.get("planned_roadmap", [])
    print(f"[Pipeline] Agent 2 결과 로드: {len(state['planned_roadmap'])}개 로드맵 항목")
    _emit("agent_end", agent="2", count=len(state["planned_roadmap"]))
    _emit("roadmap_ready", planned_roadmap=state["planned_roadmap"])


# ──────────────────────────────────────────────────────────────
# Agent 3 — Investment Strategist (subprocess)
# ──────────────────────────────────────────────────────────────

def _run_agent3(
    state: Dict[str, Any],
    out_prefix: str = "",
    stage_mode: str = "phase",
) -> None:
    """
    investment_strategist_agent 의 stage_aggregator + strategist 를 subprocess 호출.
    ProblemFrame 에서 investment_policy 를 유도하여 전달.
    """
    active = state["active_agents"]
    out_path = _output_path(FILE_INVESTMENT_STRATEGY, out_prefix)
    state["path_investment_strategy"] = out_path
    roadmap_path = state["path_planned_roadmap"]
    tech_path = state["path_tech_candidates"]

    _emit("agent_start", agent="3", label="Agent 3 · Investment Strategist",
          active=("3" in active))
    if "3" not in active:
        print("\n[Pipeline] Agent 3 (Investment Strategist) — ⛔ OFF → 빈 전략")
        _write_empty_strategy(roadmap_path, out_path)
    else:
        print("\n[Pipeline] Agent 3 (Investment Strategist) — ✅ ON")

        # ProblemFrame + state override → InvestmentPolicy 구성
        pf = state["problem_frame"]
        investment_policy = {
            "risk_appetite": state.get("risk_appetite") or "medium",
            "investment_horizon": state.get("investment_horizon") or "balanced",
            "total_budget": float(pf.get("total_budget", 0)),
            "strategic_priority": pf.get("strategic_priorities", []),
        }

        use_phase_name = (stage_mode == "phase")
        feedback = state.get("orchestrator_feedback") or None

        snippet = f"""
import sys, json, os
sys.path.insert(0, os.getcwd())
from agents.stage_aggregator import aggregate_stages
from agents.strategist import run_strategist

# planned_roadmap 은 Agent 2 출력에서, market_context 와 tech_candidates 는
# Agent 1 출력에서 직접 읽음 (single source of truth)
with open({roadmap_path!r}, "r", encoding="utf-8") as f:
    rd = json.load(f)
planned_roadmap = rd.get("planned_roadmap") or []

market_context = {{}}
tech_candidates = []
try:
    with open({tech_path!r}, "r", encoding="utf-8") as f:
        tc = json.load(f)
    market_context = tc.get("market_context") or {{}}
    tech_candidates = tc.get("tech_candidates") or []
except FileNotFoundError:
    # 폴백: Agent 1 파일이 없으면 Agent 2 의 passthrough 사용
    market_context = rd.get("market_context") or {{}}

stages = aggregate_stages(
    planned_roadmap=planned_roadmap,
    market_context=market_context,
    use_phase_name={use_phase_name},
)
strategies = run_strategist(
    stages=stages,
    tech_candidates=tech_candidates,
    investment_policy={investment_policy!r},
    market_context=market_context,
    orchestrator_feedback={feedback!r},
)

out = {{
    "market_context": market_context,
    "investment_policy": {investment_policy!r},
    "stages": stages,
    "investment_strategy": strategies,
}}
with open({out_path!r}, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print(f"[Agent 3] 저장: {out_path!r} ({{len(stages)}}개 stage)")
"""
        _run_subprocess(snippet, cwd=SIBLING_INVESTMENT_STRATEGIST, label="Investment Strategist")

    data = _safe_load(out_path)
    state["investment_strategy"] = data.get("investment_strategy", [])
    state["stages"] = data.get("stages", [])
    print(
        f"[Pipeline] Agent 3 결과 로드: {len(state['stages'])}개 stage, "
        f"{len(state['investment_strategy'])}건 전략"
    )
    _emit("agent_end", agent="3", count=len(state["investment_strategy"]))
    _emit("strategy_ready",
          stages=state["stages"],
          investment_strategy=state["investment_strategy"])


# ──────────────────────────────────────────────────────────────
# REVISE 재실행 매핑
# ──────────────────────────────────────────────────────────────

_RERUN_ORDER = ["Technology Analyst", "Roadmap Planner", "Investment Strategist"]


def _rerun_from(
    state: Dict[str, Any],
    rerun_agents: List[str],
    out_prefix: str,
    stage_mode: str,
) -> None:
    """
    rerun_agents 중 가장 앞선 Agent 부터 이후 모든 Agent 를 순차 재실행.
    (선형 파이프라인이므로 앞 단계를 재실행하면 뒷 단계도 같이 재실행해야 일관성 유지)

    예) ["Investment Strategist"]  → Agent 3 만
        ["Roadmap Planner"]        → Agent 2, 3
        ["Technology Analyst", ..] → Agent 1, 2, 3
    """
    if not rerun_agents:
        print("[Pipeline] REVISE 이지만 rerun_agents 가 비어있음 → 재실행 없이 Review 반복")
        return

    first_idx = min(_RERUN_ORDER.index(r) for r in rerun_agents if r in _RERUN_ORDER)
    to_run = _RERUN_ORDER[first_idx:]
    print(f"\n[Pipeline] ↻ REVISE → 재실행 대상: {to_run}")

    if "Technology Analyst" in to_run:
        _run_agent1(state, out_prefix=out_prefix)
    if "Roadmap Planner" in to_run:
        _run_agent2(state, out_prefix=out_prefix)
    if "Investment Strategist" in to_run:
        _run_agent3(state, out_prefix=out_prefix, stage_mode=stage_mode)


# ──────────────────────────────────────────────────────────────
# 공개 엔트리 — run_orchestration
# ──────────────────────────────────────────────────────────────

def run_orchestration(
    domain: str,
    reference_year: int,
    category_hints: Optional[List[str]] = None,
    active_agents: Optional[List[str]] = None,
    # Problem frame overrides
    industry: Optional[str] = None,
    company_type: Optional[str] = None,
    time_horizon: Optional[str] = None,
    total_budget: Optional[float] = None,
    objective: Optional[str] = None,
    priorities: Optional[List[str]] = None,
    future_trend_summary: Optional[str] = None,
    # Investment policy overrides (Agent 3 가 사용)
    risk_appetite: Optional[str] = None,        # low / medium / high
    investment_horizon: Optional[str] = None,   # short / balanced / long
    # 내부 설정
    out_prefix: str = "",
    stage_mode: str = "phase",
) -> Dict[str, Any]:
    """
    Orchestration Agent 전체 파이프라인 실행.

    Returns
    -------
    dict  {
        problem_frame, active_agents,
        tech_candidates, market_context,
        planned_roadmap,
        investment_strategy, stages,
        review, iteration,
        paths: {tech_candidates, planned_roadmap, investment_strategy}
    }
    """
    active_agents = active_agents or ["1", "2", "3"]
    category_hints = category_hints or []

    _emit("pipeline_start", active_agents=active_agents, domain=domain,
          reference_year=reference_year)

    # ① Phase A — Orchestrator Setup
    _emit("setup_start")
    problem_frame = run_orchestrator_setup(
        domain=domain,
        reference_year=reference_year,
        active_agents=active_agents,
        industry=industry,
        company_type=company_type,
        time_horizon=time_horizon,
        total_budget=total_budget,
        objective=objective,
        priorities=priorities,
        future_trend_summary=future_trend_summary,
    )
    _emit("setup_done", problem_frame=problem_frame, active_agents=active_agents)

    state: Dict[str, Any] = {
        "domain": domain,
        "reference_year": reference_year,
        "category_hints": category_hints,
        "problem_frame": problem_frame,
        "active_agents": active_agents,
        "tech_candidates": [],
        "market_context": {},
        "planned_roadmap": [],
        "investment_strategy": [],
        "stages": [],
        "orchestrator_feedback": None,
        # Investment policy override (None 이면 _run_agent3 가 기본값 사용)
        "risk_appetite": risk_appetite,
        "investment_horizon": investment_horizon,
    }

    # ② 첫 실행: 세 Agent 를 순서대로 (OFF 이면 폴백)
    _run_agent1(state, out_prefix=out_prefix)
    _run_agent2(state, out_prefix=out_prefix)
    _run_agent3(state, out_prefix=out_prefix, stage_mode=stage_mode)

    # ③ Phase B — Review + REVISE 루프
    previous_feedback: List[str] = []
    iteration = 0
    review = None
    review_history: List[Dict[str, Any]] = []  # 매 iter 의 review 누적

    while True:
        _emit("review_start", iteration=iteration + 1,
              max_iterations=MAX_ORCHESTRATOR_ITERATIONS)
        review = run_orchestrator_review(
            iteration=iteration,
            problem_frame=problem_frame,
            tech_candidates=state["tech_candidates"],
            planned_roadmap=state["planned_roadmap"],
            investment_strategy=state["investment_strategy"],
            stages=state["stages"],
            market_context=state["market_context"],
            previous_feedback=previous_feedback,
            active_agents=active_agents,
        )
        iteration += 1
        review_history.append({"iteration": iteration, "review": review})
        _emit("review_done", iteration=iteration, review=review)

        if review["decision"] == "ACCEPT":
            break

        if iteration >= MAX_ORCHESTRATOR_ITERATIONS:
            # Review 내부에서 이미 강제 ACCEPT 로 전환되었지만 이중 안전장치
            print("[Pipeline] MAX_ORCHESTRATOR_ITERATIONS 도달 → 루프 종료")
            break

        # REVISE 처리
        refinement = review.get("refinement") or {}
        rerun = refinement.get("rerun_agents", []) or []
        feedback = refinement.get("feedback", []) or []

        # 피드백을 Roadmap/Investment 재실행 프롬프트에 전달 (text 채널)
        state["orchestrator_feedback"] = {
            **(state.get("orchestrator_feedback") or {}),
            "text": feedback,
        }
        previous_feedback = feedback

        _emit("refine", rerun_agents=rerun, feedback=feedback, iteration=iteration)
        _rerun_from(state, rerun, out_prefix=out_prefix, stage_mode=stage_mode)

    # ④ 반환
    result = {
        "problem_frame": problem_frame,
        "active_agents": active_agents,
        "tech_candidates": state["tech_candidates"],
        "market_context": state["market_context"],
        "planned_roadmap": state["planned_roadmap"],
        "investment_strategy": state["investment_strategy"],
        "stages": state["stages"],
        "review": review,
        "review_history": review_history,
        "iteration": iteration,
        "paths": {
            "tech_candidates": state.get("path_tech_candidates"),
            "planned_roadmap": state.get("path_planned_roadmap"),
            "investment_strategy": state.get("path_investment_strategy"),
        },
    }
    _emit("pipeline_done", iteration=iteration,
          decision=review.get("decision") if review else None)
    return result
