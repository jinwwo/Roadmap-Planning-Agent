"""
agents/orchestrator.py
───────────────────────
Orchestration Agent — 두 단계로 동작:

  Phase A. run_orchestrator_setup(...)
    - 사용자 입력(domain, reference_year, ...)을 구조화된 ProblemFrame 으로 변환
    - 각 Agent 의 역할 / 입력 / 지시사항을 로그로 출력
    - active_agents 에 따라 어떤 Agent 를 실제 구동할지 결정

  Phase B. run_orchestrator_review(...)
    - Agent 1/2/3 결과 + ProblemFrame + future trend 요약 을 LLM 에 제시
    - TRM 원칙으로 평가 후 decision = ACCEPT | REVISE
    - ACCEPT  : 7-섹션 최종 보고서(report) 반환
    - REVISE  : refinement.rerun_agents + feedback + diagnostic_summary 반환
                (pipeline 루프가 해당 Agent 만 재실행 후 다시 review 호출)

시스템 프롬프트는 사용자 제공 spec(In context) 을 그대로 반영했습니다.
"""

import json
import re
from typing import List, Optional
from langchain_core.messages import HumanMessage, SystemMessage

from llm_factory import get_llm
from config import (
    DEFAULT_INDUSTRY,
    DEFAULT_COMPANY_TYPE,
    DEFAULT_TIME_HORIZON,
    DEFAULT_TOTAL_BUDGET,
    DEFAULT_OBJECTIVE,
    DEFAULT_PRIORITIES,
    DEFAULT_FUTURE_TREND_SUMMARY,
    MAX_ORCHESTRATOR_ITERATIONS,
)
from state import ProblemFrame, ReviewResult


# ── JSON 유틸 ─────────────────────────────────────────────────

def _try_repair_json(text: str) -> str:
    """
    작은 LLM 이 자주 만드는 JSON 형식 오류 자동 보정.
    - smart quotes (" " ' ') → ASCII
    - trailing comma 제거 (,\s*[}\]])
    - 컨트롤 문자 제거 (\x00-\x1f 중 일부)
    """
    text = (
        text.replace("“", '"').replace("”", '"')
            .replace("‘", "'").replace("’", "'")
    )
    text = re.sub(r",\s*([}\]])", r"\1", text)
    return text


def _extract_json(text: str) -> dict:
    cleaned = re.sub(r"```(?:json)?\s*", "", text).replace("```", "").strip()
    # 1차: 그대로 파싱
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    # 2차: 첫 { 부터 마지막 } 까지 발췌
    match = re.search(r"\{[\s\S]+\}", cleaned)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            # 3차: 자동 보정 (trailing comma / smart quotes) 후 재시도
            try:
                return json.loads(_try_repair_json(match.group()))
            except json.JSONDecodeError:
                pass
    raise ValueError(f"JSON 파싱 실패:\n{text[:300]}")


# ── Partial Recovery — review LLM 응답이 깨진 경우 핵심 필드만 살림 ──

def _partial_recover_review(text: str) -> dict:
    """
    Review LLM 의 JSON 응답이 형식 오류로 파싱 실패했을 때,
    의미는 살아있으니 핵심 필드만 regex 로 발췌해 dict 재조립.

    추출 대상:
      - decision (ACCEPT/REVISE)
      - issues[]  (axis + text 형태 또는 plain string)
      - refinement.rerun_agents[], refinement.feedback[]
      - trm_assessment 의 boolean / 점수 필드
      - report 7섹션
      - diagnostic_summary
    """
    out: dict = {
        "decision": "",
        "trm_assessment": {},
        "issues": [],
        "refinement": {"rerun_agents": [], "feedback": []},
        "report": {},
        "diagnostic_summary": "",
    }

    def _strs_in_array(block: str) -> list:
        return [s for s in re.findall(r'"((?:[^"\\]|\\.)*?)"', block) if s]

    # decision
    m = re.search(r'"decision"\s*:\s*"([^"]+)"', text, re.IGNORECASE)
    if m:
        out["decision"] = m.group(1).strip().upper()

    # diagnostic_summary
    m = re.search(r'"diagnostic_summary"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
    if m:
        out["diagnostic_summary"] = m.group(1)

    # issues (배열 안의 객체 또는 문자열 모두 흡수)
    m = re.search(r'"issues"\s*:\s*\[([\s\S]*?)\]\s*(?:,|\}|$)', text)
    if m:
        block = m.group(1)
        # 객체 형태 ({"axis":"...", "text":"..."})
        objs = re.findall(r"\{[^{}]*\}", block)
        if objs:
            for o in objs:
                axis_m = re.search(r'"axis"\s*:\s*"([^"]+)"', o)
                text_m = re.search(
                    r'"(?:text|description|message|detail)"\s*:\s*"((?:[^"\\]|\\.)*)"', o
                )
                if axis_m or text_m:
                    out["issues"].append({
                        "axis": axis_m.group(1) if axis_m else "",
                        "text": text_m.group(1) if text_m else "",
                    })
        else:
            out["issues"] = _strs_in_array(block)

    # refinement.rerun_agents
    m = re.search(r'"rerun_agents"\s*:\s*\[([\s\S]*?)\]', text)
    if m:
        out["refinement"]["rerun_agents"] = _strs_in_array(m.group(1))

    # refinement.feedback
    m = re.search(r'"feedback"\s*:\s*\[([\s\S]*?)\]', text)
    if m:
        out["refinement"]["feedback"] = _strs_in_array(m.group(1))

    # trm_assessment 의 5축 — 각 boolean / 점수만 추출
    trm_block_m = re.search(r'"trm_assessment"\s*:\s*\{([\s\S]*?)\}\s*,\s*"', text)
    trm_block = trm_block_m.group(1) if trm_block_m else text  # 전체에서도 fallback 검색

    bool_keys = (
        "budget_feasible", "schedule_feasible", "dependency_feasible",
        "over_invested", "under_invested",
    )
    num_keys = (
        ("strategic_alignment", "company_fit"),
        ("strategic_alignment", "future_trend_alignment"),
        ("portfolio_balance", "short_long_balance"),
        ("portfolio_balance", "risk_balance"),
    )

    trm_collected: dict = {}
    for k in bool_keys:
        bm = re.search(rf'"{k}"\s*:\s*(true|false)', trm_block, re.IGNORECASE)
        if bm:
            trm_collected[k] = (bm.group(1).lower() == "true")

    for parent, child in num_keys:
        nm = re.search(rf'"{child}"\s*:\s*([0-9.]+)', trm_block)
        if nm:
            try:
                trm_collected.setdefault(parent, {})[child] = float(nm.group(1))
            except ValueError:
                pass

    if trm_collected:
        out["trm_assessment"] = trm_collected

    # report 7섹션
    for k in (
        "executive_summary", "technology_strategy", "roadmap_structure",
        "investment_strategy", "trend_alignment", "feasibility_and_risk",
        "expected_outcomes",
    ):
        m = re.search(rf'"{k}"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
        if m:
            out["report"][k] = m.group(1)

    return out


# ──────────────────────────────────────────────────────────────
# Phase A. Problem Setup
# ──────────────────────────────────────────────────────────────

def run_orchestrator_setup(
    domain: str,
    reference_year: int,
    active_agents: List[str],
    industry: Optional[str] = None,
    company_type: Optional[str] = None,
    time_horizon: Optional[str] = None,
    total_budget: Optional[float] = None,
    objective: Optional[str] = None,
    priorities: Optional[List[str]] = None,
    future_trend_summary: Optional[str] = None,
) -> ProblemFrame:
    """
    사용자 입력 → 구조화된 ProblemFrame.
    CLI 에서 override 되지 않은 필드는 config 의 default 값을 사용.
    """
    print("\n[Orchestrator · Setup] ▶ 문제 구조화")

    pf: ProblemFrame = {
        "industry": industry or DEFAULT_INDUSTRY,
        "company_type": company_type or DEFAULT_COMPANY_TYPE,
        "time_horizon": time_horizon or DEFAULT_TIME_HORIZON,
        "total_budget": float(total_budget) if total_budget is not None else DEFAULT_TOTAL_BUDGET,
        "objective": objective or DEFAULT_OBJECTIVE,
        "strategic_priorities": list(priorities) if priorities else list(DEFAULT_PRIORITIES),
        "future_trend_summary": future_trend_summary or DEFAULT_FUTURE_TREND_SUMMARY,
    }

    print("  ── Problem Frame ──────────────────────────────")
    print(f"    industry            : {pf['industry']}")
    print(f"    company_type        : {pf['company_type']}")
    print(f"    time_horizon        : {pf['time_horizon']}")
    print(f"    total_budget        : {pf['total_budget']:,.0f} USD")
    print(f"    objective           : {pf['objective']}")
    print(f"    strategic_priorities:")
    for i, p in enumerate(pf["strategic_priorities"], 1):
        print(f"      {i}. {p}")
    print(f"    domain (raw)        : {domain}")
    print(f"    reference_year      : {reference_year}")
    print(f"    active_agents       : {active_agents}")

    # 각 Agent 에게 전달할 지시사항 (로그용 요약)
    instructions = {
        "1": "Technology Analyst Agent : USPTO + Tavily 신호로 후보 기술 발굴 → tech_candidates",
        "2": "Roadmap Planner Agent    : dependency tree + TRL 기반 역산 타임라인 → planned_roadmap",
        "3": "Investment Strategist    : stage 집계 + 5-지표 평가 + Tier 도출 → investment_strategy",
    }
    print("\n  ── Agent Assignments ──────────────────────────")
    for k in ["1", "2", "3"]:
        mark = "✅ ON " if k in active_agents else "⛔ OFF"
        print(f"    Agent {k} {mark} — {instructions[k]}")

    return pf


# ──────────────────────────────────────────────────────────────
# Phase B. Review (TRM 평가 + Report 생성)
# ──────────────────────────────────────────────────────────────

REVIEW_SYSTEM_PROMPT = """You are a unified orchestration agent for AI-based technology roadmapping (TRM).

Your role is NOT just to summarize outputs.
You must:
- define the problem,
- coordinate multiple agents,
- evaluate the generated roadmap,
- refine it iteratively,
- and generate a final strategic report.

--------------------------------------------------
[YOUR RESPONSIBILITIES]

1. Problem Setup
   - Interpret the user goal
   - Define industry, scope, time horizon, and budget

2. Task Orchestration
   - Assign tasks to:
     - Technology Analyst Agent
     - Roadmap Planner Agent
     - Investment Strategist Agent
   - Ensure each agent receives appropriate context

3. Integration
   - Combine outputs into a coherent roadmap
   - Align roadmap and investment plan

4. Evaluation
   - Evaluate roadmap using TRM principles
   - Detect issues and inconsistencies

5. Iterative Refinement
   - Decide ACCEPT or REVISE
   - If REVISE:
     - Select which agent(s) to rerun
     - Provide clear feedback

6. Report Generation
   - If ACCEPT:
     - Generate a structured TRM report

--------------------------------------------------
[TRM STRUCTURING PRINCIPLES]

A valid technology roadmap MUST:

1. Connect market opportunity, business impact, and technologies
2. Organize technologies along a realistic time horizon
3. Respect dependency relationships
4. Stay within budget constraints
5. Balance short-term and long-term investments
6. Reflect company capability
7. Align with future technology trends

IMPORTANT:
- Do NOT evaluate as a simple list
- Evaluate as a structured, time-based strategy

--------------------------------------------------
[EVALUATION PRIORITIES]

Evaluate in this order:

1. Feasibility
   - Budget realistic?
   - Timeline realistic?

2. Dependency correctness
   - Are prerequisite technologies placed first?

3. Strategic alignment
   - Matches company capability?
   - Matches future trends?

4. Economic potential
   - High-value technologies prioritized?

5. Portfolio balance
   - Short-term vs long-term
   - Risk distribution

--------------------------------------------------
[IMPORTANT THINKING RULES]

- High growth ≠ immediate investment
- Long-term tech requires enabling tech first
- Investment must match difficulty
- Avoid over-concentration
- Consider company capability gap
- Roadmap must be executable

--------------------------------------------------
[REVIEW TASK]

You MUST:

1. Evaluate feasibility
2. Check dependency validity
3. Evaluate trend alignment
4. Evaluate investment rationality
5. Identify:
   - Missing technologies
   - Over-invested technologies
   - Under-invested technologies
   - Timing errors

6. Decide:
   - ACCEPT
   - REVISE

If REVISE:
- Specify agents to rerun
- Provide actionable feedback

Each item in `issues` MUST be an object with two keys:
- `axis` : EXACTLY one of these five strings (case-sensitive, no variations):
    "feasibility" | "sequencing" | "strategic_alignment"
    | "investment_rationality" | "portfolio_balance"
- `text` : concise one-line description of the issue (MUST be non-empty)

STRICT axis rules — issues violating these will be silently dropped:
- DO NOT invent new axis names. "investment_attractiveness", "investment_urgency",
  "investment_tier", "investment_scope" are STAGE FIELDS, NOT axes. Investment
  concerns go under `investment_rationality`.
- DO NOT use "alignment", "technology_strategy", "trend_alignment" — these map to
  `strategic_alignment`.
- DO NOT use "dependency", "dependencies", "roadmap", "timeline" — use `sequencing`.
- DO NOT use "portfolio", "balance", "risk_balance" — use `portfolio_balance`.
- DO NOT use "budget", "schedule" — these are sub-fields of `feasibility`.
- DO NOT output issues with empty `text`. If you have nothing concrete to say about
  an axis, set `trm_assessment.<axis>.comment` instead and omit from issues.
- DO NOT output plain string issues.

[CONSISTENCY RULES — trm_assessment 의 수치와 issues 의 텍스트는 반드시 일치해야 한다]
- `portfolio_balance.short_long_balance` ≥ 0.40 이면 issues 에 "short-term dominate"
  / "단기 우세" / "near-term focus" 같은 표현을 절대 쓰지 말 것.
  진짜 단기 우세를 주장하려면 short_long_balance 값을 0.40 미만으로 낮춰야 한다.
- `strategic_alignment.company_fit` 또는 `future_trend_alignment` 점수를 채우지 않은
  채로 strategic_alignment issue 를 쓰지 말 것. issue 를 쓸 거면 점수도 함께 낮게 채워라
  (예: misalign 주장 시 fit ≤ 0.5 또는 trend ≤ 0.5).
- `feasibility.budget_feasible` 또는 `schedule_feasible` 이 true 인데 feasibility
  issue 를 쓰지 말 것. issue 를 쓸 거면 해당 boolean 을 false 로 바꿔라.
- 모든 axis 에 대해 동일 — 점수 / boolean 과 issue 텍스트는 한 방향을 가리켜야 한다.

[REVISE 시 rerun_agents 배정 — axis 별 책임 Agent]
- feasibility            → ["Roadmap Planner", "Investment Strategist"]
- sequencing             → ["Roadmap Planner"]
- strategic_alignment    → ["Technology Analyst"]    ← 빠뜨리지 말 것
- investment_rationality → ["Investment Strategist"]
- portfolio_balance      → ["Roadmap Planner", "Investment Strategist"]
issues 에 axis A 가 들어있다면 그 axis 의 책임 Agent 는 반드시 rerun_agents 에 포함하라.
(누락 시 코드가 자동 보강하지만, 처음부터 정확히 출력하는 것이 우선)

--------------------------------------------------
[REPORT GENERATION]

If decision == "ACCEPT":

Generate a structured TRM report including:

1. Executive Summary
2. Technology Strategy
3. Roadmap Structure
4. Investment Strategy
5. Trend Alignment
6. Feasibility & Risk Analysis
7. Expected Outcomes

Report Requirements:
- Formal tone
- Logical explanation
- Justify every decision
- Avoid vague statements
- 한국어로 작성 (기술 고유명사/약어는 영문 허용: EUV, ALD, GAA, HBM, TRL, BSPDN 등).

--------------------------------------------------
[REVISE RULE]

If decision == "REVISE":

- DO NOT generate full report
- Provide diagnostic summary only (한국어 1-2 문단)

--------------------------------------------------
[OUTPUT FORMAT — STRICT JSON ONLY]

CRITICAL FIELD-LEVEL RULES:
- `decision` MUST be a plain string: exactly "ACCEPT" or "REVISE".
  DO NOT wrap it in an object like {"acceptance": true, ...} or
  {"investment_plan": ..., "roadmap_draft": ..., "issues": ...}.
  DO NOT nest the entire response inside the `decision` field.
- `issues`, `refinement`, `report` MUST appear at the TOP LEVEL of your JSON,
  NOT nested inside `decision` or any other field.
- DO NOT echo back the input data (investment_plan, roadmap_draft, tech_candidates)
  in your output. The orchestrator already has them. Your job is to EVALUATE.

{
  "decision": "ACCEPT or REVISE",

  "trm_assessment": {
    "feasibility": {
      "budget_feasible": true,
      "schedule_feasible": true,
      "comment": ""
    },
    "sequencing": {
      "dependency_valid": true,
      "comment": ""
    },
    "strategic_alignment": {
      "company_fit": 0.0,
      "future_trend_alignment": 0.0,
      "comment": ""
    },
    "investment_rationality": {
      "over_invested": [],
      "under_invested": [],
      "comment": ""
    },
    "portfolio_balance": {
      "short_long_balance": 0.0,
      "risk_balance": 0.0,
      "comment": ""
    }
  },

  "issues": [
    {"axis": "feasibility | sequencing | strategic_alignment | investment_rationality | portfolio_balance",
     "text": "..."}
  ],

  "refinement": {
    "rerun_agents": ["Technology Analyst" | "Roadmap Planner" | "Investment Strategist"],
    "feedback": ["actionable instruction string", ...]
  },

  // rerun_agents MUST be a flat array of STRINGS — exactly one of the three
  //   canonical names above. DO NOT output objects like
  //   {"agent_id": "Agent 1", "task": "..."}. Put the task description in
  //   `feedback` as a string instead.

  "report": {
    "executive_summary": "",
    "technology_strategy": "",
    "roadmap_structure": "",
    "investment_strategy": "",
    "trend_alignment": "",
    "feasibility_and_risk": "",
    "expected_outcomes": ""
  },

  "diagnostic_summary": ""
}

--------------------------------------------------
[SCOPE RULE · DISABLED AGENTS — VERY IMPORTANT]

The input will tell you which agents are ACTIVE (section "7. Active Agents").
If an agent is NOT in the active list, its output is intentionally absent for
this ablation experiment. You MUST observe the following:

1. DO NOT list the absence of a disabled agent's output as an `issue`.
   Example: if Investment Strategist is disabled, "Missing investment strategy"
   is NOT an issue — it is an expected configuration.

2. DO NOT include disabled agents in `refinement.rerun_agents`.
   Disabled agents cannot be rerun within the same experiment — requesting
   rerun of them would create an infinite REVISE loop.

3. DO NOT demand content that requires a disabled agent. Evaluate only within
   the scope of active agents. Axes tied to disabled agents are post-processed
   to "N/A (agent disabled)" by the orchestrator — you may leave them empty.

4. Every `issues[i]` MUST include an `axis` field. Issues tied to an axis whose
   supporting agent is disabled will be dropped. Tag them correctly.

5. When ACCEPT, the 7-section report MUST still be generated, but sections that
   depend on disabled agents can be shorter or reference the absence as an
   acknowledged experiment limitation (not a problem to fix).

6. When REVISE, `rerun_agents` must contain ONLY active agents. If the only
   issues trace back to disabled agents, decide ACCEPT instead of REVISE.

--------------------------------------------------
[CRITICAL RULES]

- Output MUST be valid JSON
- DO NOT include text outside JSON
- If ACCEPT → include report (all 7 sections filled)
- If REVISE → include diagnostic_summary only, leave report fields as ""
- rerun_agents values MUST be from ACTIVE agents only
   (subset of ["Technology Analyst", "Roadmap Planner", "Investment Strategist"])
- Be consistent and deterministic"""


def _build_review_user_prompt(
    problem_frame: dict,
    tech_candidates: list,
    planned_roadmap: list,
    investment_strategy: list,
    stages: list,
    market_context: dict,
    previous_feedback: list,
    active_agents: list,
) -> str:
    """
    Review LLM 용 사용자 프롬프트 구성.

    ★ 중요: 비활성 agent 에 해당하는 섹션은 **프롬프트에서 완전히 제외**한다.
      (빈 배열을 보여주면 LLM 이 "missing" 을 issue 로 잡는 경향이 있어서)
      대신 상단 [EXPERIMENT SCOPE] 에서 어떤 입력만 평가해야 하는지 선언한다.
    """
    # ── Active agents 에 따른 섹션 활성화 여부 ──────────────
    a1_on = "1" in active_agents
    a2_on = "2" in active_agents
    a3_on = "3" in active_agents

    # TRM 5-축 중 평가 가능한 것 (disabled agent 필요한 것은 N/A)
    evaluable = {
        "feasibility":            True,            # problem_frame 만으로도 부분 평가 가능
        "sequencing":             a2_on,           # Roadmap Planner 필요
        "strategic_alignment":    a1_on,           # Tech Analyst 필요
        "investment_rationality": a3_on,           # Investment Strategist 필요
        "portfolio_balance":      a2_on and a3_on, # 둘 다 필요
    }
    eval_on  = [k for k, v in evaluable.items() if v]
    eval_off = [k for k, v in evaluable.items() if not v]

    constraints = {
        "total_budget": problem_frame.get("total_budget"),
        "time_horizon": problem_frame.get("time_horizon"),
        "industry": problem_frame.get("industry"),
    }
    future_trend = problem_frame.get("future_trend_summary", "")

    # ── 섹션 조립 (active 한 것만) ──────────────────────────
    sections = []

    # Problem Frame — 항상 표시
    sections.append(f"""[PROBLEM FRAME]
- Industry    : {problem_frame.get('industry')}
- Company Type: {problem_frame.get('company_type')}
- Time Horizon: {problem_frame.get('time_horizon')}
- Total Budget: {problem_frame.get('total_budget')}
- Objective   : {problem_frame.get('objective')}

Strategic Priorities:
{json.dumps(problem_frame.get('strategic_priorities', []), ensure_ascii=False, indent=2)}""")

    # EXPERIMENT SCOPE — 어떤 축을 평가할지 / 하지 않을지 명시
    scope_lines = [
        "[EXPERIMENT SCOPE — READ CAREFULLY]",
        f"This is an ablation experiment. Active agents: {active_agents}.",
        "Only the following agents produced output in this run; only their",
        "areas are in scope for evaluation:",
    ]
    if a1_on: scope_lines.append("  • Agent 1 (Technology Analyst)  — technology candidates")
    if a2_on: scope_lines.append("  • Agent 2 (Roadmap Planner)     — roadmap / dependencies / timeline")
    if a3_on: scope_lines.append("  • Agent 3 (Investment Strategist) — investment tiers / budget allocation")
    if eval_off:
        scope_lines.append("")
        scope_lines.append("The following TRM axes CANNOT be evaluated in this experiment and MUST")
        scope_lines.append("be marked as 'N/A (agent disabled)' in trm_assessment:")
        for ax in eval_off:
            scope_lines.append(f"  • {ax}")
        scope_lines.append("")
        scope_lines.append("FORBIDDEN in this run:")
        if not a1_on:
            scope_lines.append("  ✗ Do NOT raise issues about missing tech candidates / weak rationale / trend alignment.")
        if not a2_on:
            scope_lines.append("  ✗ Do NOT raise issues about dependency / sequencing / roadmap structure.")
        if not a3_on:
            scope_lines.append("  ✗ Do NOT raise issues about investment plan / budget / tier / portfolio.")
        scope_lines.append("  ✗ Do NOT include disabled agents in `refinement.rerun_agents`.")
        scope_lines.append("  ✗ Do NOT ask active agents to recover content that only a disabled agent can produce.")
    sections.append("\n".join(scope_lines))

    # Tech Candidates (A1 on 일 때만)
    if a1_on:
        tech_slim = [
            {"tech_id": t.get("tech_id"), "name": t.get("name"),
             "category": t.get("category"), "trl": t.get("trl"),
             "final_score": t.get("final_score"),
             "expected_market_boom_quarter": t.get("expected_market_boom_quarter")}
            for t in (tech_candidates or [])
        ]
        sections.append(
            f"[TECHNOLOGY CANDIDATES · {len(tech_slim)} items]\n"
            + json.dumps(tech_slim, ensure_ascii=False, indent=2)
        )

    # Roadmap (A2 on 일 때만)
    if a2_on:
        roadmap_slim = [
            {"tech_id": r.get("tech_id"), "name": r.get("name"),
             "phase_name": r.get("phase_name"),
             "start_q": r.get("start_q"), "target_q": r.get("target_q"),
             "prerequisites": r.get("prerequisites"),
             "lead_time_quarters": r.get("lead_time_quarters")}
            for r in (planned_roadmap or [])
        ]
        sections.append(
            f"[ROADMAP DRAFT · {len(roadmap_slim)} items]\n"
            + json.dumps(roadmap_slim, ensure_ascii=False, indent=2)
        )

    # Investment Plan (A3 on 일 때만)
    if a3_on:
        # 새 구조: stage 컨테이너 + tech_investments 안에 per-tech 평가
        invest_slim = []
        for s in (investment_strategy or []):
            tech_invs_slim = [
                {
                    "tech_id": ti.get("tech_id"),
                    "name": ti.get("name"),
                    "evaluation_scores": ti.get("evaluation_scores"),
                    "investment_attractiveness": ti.get("investment_attractiveness"),
                    "investment_urgency": ti.get("investment_urgency"),
                    "recommended_investment_tier": ti.get("recommended_investment_tier"),
                    "investment_scope": ti.get("investment_scope"),
                }
                for ti in (s.get("tech_investments") or [])
            ]
            invest_slim.append({
                "stage": s.get("stage"),
                "period": s.get("period"),
                "stage_assessment": s.get("stage_assessment"),
                "tech_investments": tech_invs_slim,
            })
        sections.append(
            f"[INVESTMENT PLAN · {len(invest_slim)} stages · 각 stage 안에 per-tech tier 부여]\n"
            + json.dumps(invest_slim, ensure_ascii=False, indent=2)
        )

    # Constraints + Future Trend + Previous Feedback — 항상 표시
    sections.append(
        "[CONSTRAINTS]\n" + json.dumps(constraints, ensure_ascii=False, indent=2)
    )
    sections.append("[FUTURE TECHNOLOGY TRENDS]\n" + future_trend)
    sections.append(
        "[PREVIOUS FEEDBACK from last REVISE iteration]\n"
        + json.dumps(previous_feedback, ensure_ascii=False, indent=2)
    )

    # 끝맺음 지시
    sections.append("""[YOUR TASK]
Evaluate the above inputs within the declared EXPERIMENT SCOPE.
- Only raise issues that active agents can address.
- Only put active agents in `refinement.rerun_agents`.
- For disabled TRM axes, set the comment to "N/A (agent disabled)".

Return strict JSON exactly matching the specified schema.""")

    return "\n\n--------------------------------------------------\n".join(sections)


def _force_accept_on_last_iteration(result: dict, iteration: int) -> dict:
    """
    MAX_ORCHESTRATOR_ITERATIONS 에 도달했는데도 REVISE 면 강제 ACCEPT 로 전환.
    (무한 루프 방지. 이 시점에선 report 가 비어있을 수 있으므로 호출자가
     generate_final_report() 로 채워 넣어야 합니다 — run_orchestrator_review 참고.)
    """
    if iteration + 1 >= MAX_ORCHESTRATOR_ITERATIONS and result.get("decision") == "REVISE":
        print(
            f"[Orchestrator · Review] ⚠️  iteration {iteration+1} / "
            f"{MAX_ORCHESTRATOR_ITERATIONS} 도달 → 강제 ACCEPT 전환"
        )
        result["decision"] = "ACCEPT"
        result["_forced_accept"] = True   # 호출자가 report 재생성 여부 판단용
    return result


# ──────────────────────────────────────────────────────────────
# Final Report Generator — 강제 ACCEPT 또는 LLM 이 report 를 비워둔 경우 폴백
# ──────────────────────────────────────────────────────────────

FINAL_REPORT_SYSTEM_PROMPT = """You are a senior strategy writer finalizing a TRM report.

Given the current problem frame, roadmap, and investment strategy (along with any
residual issues from prior review iterations), produce a structured 7-section TRM
report in Korean. This is the FINAL report — the iteration cycle is over, so you
MUST produce content for every section regardless of remaining issues. Mention any
residual issues as caveats inside `feasibility_and_risk` rather than refusing to write.

---
[섹션별 작성 가이드 — 각 섹션 4~6문장 / ~500자 / 7섹션 모두 다른 주제]

각 섹션은 입력 데이터의 특정 부분을 인용해 작성한다. 단순 보일러플레이트 (`"본 보고서는 ... 산업에서"`) 로 시작하지 말 것.

1. executive_summary
   입력 : PROBLEM FRAME (industry, company_type, time_horizon, total_budget, objective)
        + 전체 stage 수 + Tier 1 비율 (INVESTMENT STRATEGY 에서 집계)
   필수 인용 : 기업 유형 / 목표 시장 / 시장 boom 분기 / 투자 규모 / 핵심 Tier 1 기술 1~2개 (tech_id)
   금지 : "본 보고서는 ... 산업에서" 같은 일반 도입부

2. technology_strategy
   입력 : TECH CANDIDATES (final_score 상위, category 분포, market_signal)
   필수 인용 : 핵심 tech_id 3~4개 + final_score 또는 시장 규모 / category (Equipment/Material/Process) 비율 /
             어떤 카테고리에 우선 투자하는 논리

3. roadmap_structure
   입력 : PLANNED ROADMAP (phase_name, start_q/target_q, prerequisites) + stages
   필수 인용 : 각 phase 의 시작/완료 분기 (예: "1단계 R&D: 2027 Q1-Q4") /
             대표 tech_id / 핵심 의존성 (예: "T01 → T03 prereq")
   금지 : phase 이름만 나열하고 끝내기

4. investment_strategy
   입력 : INVESTMENT STRATEGY 의 tech_investments[] 의 Tier 분포 + investment_policy +
          recommended_action
   필수 인용 :
     • Tier 1/2/3 분포 ("10개 중 4개 Tier 1, 4개 Tier 2, 2개 Tier 3")
     • 핵심 Tier 1 tech_id 와 그 이유 (시장 규모, urgency 등)
     • total_budget 활용 방향 (단기 R&D vs 장기 베팅)
   금지 : "투자 전략을 수립한다" 같은 내용 없는 문장

5. trend_alignment
   입력 : PROBLEM FRAME.future_trend_summary + tech_candidates.expected_market_boom_quarter
   필수 인용 : future_trend 에 언급된 키워드 (HBM, GAA, BSPDN, EUV 등) 직접 인용 /
             로드맵 stage 가 그 트렌드와 어떻게 맞물리는지 timing 근거

6. feasibility_and_risk
   입력 : 잔여 issues + 잔여 feedback + total_budget + tech_investments[].major_risks
   필수 인용 :
     • 예산 충분성 평가 (total_budget vs 예상 투자 규모)
     • 핵심 리스크 2~3개 (tech_id 명시)
     • 잔여 이슈 (RESIDUAL ISSUES 에 있다면 모두 명시 — 이 섹션의 책임)

7. expected_outcomes
   입력 : market_context.expected_boom_quarter + Tier 1 기술 시장 규모 +
          investment_attractiveness 분포
   필수 인용 : 정량 KPI (시장 점유율, 매출 추정 등) / 시장 포지션 / 후행 효과

---
[전체 작성 룰]

- **각 섹션 4~6문장, ~500자** — 너무 짧으면 (200자 미만) 부족
- **7섹션 서로 다른 주제** — 같은 문장/패턴을 다른 섹션에 반복 금지
  · 같은 사실은 한 섹션에서만 언급 (예: 시장 규모 → 1번 또는 2번에만)
  · 한 섹션에서 다 말한 내용을 다른 섹션에서 또 말하지 말 것
- **보일러플레이트 시작 금지** — "본 보고서는...", "다음과 같다" 같은 도입부 X
- **tech_id 자연스럽게 인용** — T01, T03 같은 식으로 본문에 녹여서
- **수치 인용** — 시장 규모 ($X B), CAGR (Y%), 분기 명시 (2028 Q1) 등 가능한 한 인용
- **한국어 작성**, 기술 약어 영문 허용 (EUV, ALD, GAA, HBM, TRL, BSPDN 등)
- **입력에 없는 사실 지어내지 말 것** — TECH CANDIDATES, PLANNED ROADMAP, INVESTMENT STRATEGY 에서만 인용

[Output — STRICT JSON ONLY]
{
  "executive_summary": "",
  "technology_strategy": "",
  "roadmap_structure": "",
  "investment_strategy": "",
  "trend_alignment": "",
  "feasibility_and_risk": "",
  "expected_outcomes": ""
}"""


def _empty_report() -> dict:
    return {k: "" for k in [
        "executive_summary", "technology_strategy", "roadmap_structure",
        "investment_strategy", "trend_alignment", "feasibility_and_risk",
        "expected_outcomes",
    ]}


def _report_fill_count(report: dict) -> int:
    if not isinstance(report, dict):
        return 0
    return sum(1 for v in report.values() if isinstance(v, str) and v.strip())


def generate_final_report(
    problem_frame: dict,
    tech_candidates: list,
    planned_roadmap: list,
    investment_strategy: list,
    stages: list,
    market_context: dict,
    residual_issues: list,
    residual_feedback: list,
) -> dict:
    """
    7-섹션 TRM 최종 보고서를 전용 LLM 콜로 생성.

    강제 ACCEPT 직후 또는 ACCEPT 지만 report 가 부실한 경우 호출.
    """
    # ── Slim payload (보고서 본문 인용 가능하도록 풍부한 분석 신호 포함) ──
    def _extract_section_signal(rationale: str, section: str, limit: int = 240) -> str:
        """Agent 1 의 rationale 에서 [Market] 또는 [Patent] 섹션만 발췌"""
        if not isinstance(rationale, str):
            return ""
        marker = f"[{section}]"
        idx = rationale.find(marker)
        if idx < 0:
            return ""
        part = rationale[idx + len(marker):].strip()
        for other in ("[Patent]", "[Market]", "[Risk]", "[Competition]"):
            if other == marker:
                continue
            j = part.find(other)
            if j >= 0:
                part = part[:j].strip()
        return part[:limit] + ("…" if len(part) > limit else "")

    # Agent 1 결과 — 각 tech 의 시장/특허 정량 신호 + 본문 발췌
    tech_slim = [
        {"tech_id": t.get("tech_id"), "name": t.get("name"),
         "category": t.get("category"), "trl": t.get("trl"),
         "final_score": t.get("final_score"),
         "market_score": t.get("market_score"),
         "patent_score": t.get("patent_score"),
         "expected_market_boom_quarter": t.get("expected_market_boom_quarter", ""),
         "market_signal": _extract_section_signal(t.get("rationale", ""), "Market"),
         "patent_signal": _extract_section_signal(t.get("rationale", ""), "Patent")}
        for t in (tech_candidates or [])
    ]

    # Agent 2 결과 — 분기 + 의존성 + 한국어 정당화
    roadmap_slim = [
        {"tech_id": r.get("tech_id"), "name": r.get("name"),
         "phase_name": r.get("phase_name"),
         "start_q": r.get("start_q"), "target_q": r.get("target_q"),
         "prerequisites": r.get("prerequisites"),
         "lead_time_quarters": r.get("lead_time_quarters"),
         "justification": (r.get("justification", "") or "")[:280]}
        for r in (planned_roadmap or [])
    ]

    # Agent 3 결과 — per-tech 풀 평가 (rationale, risks, resource_focus 모두 포함)
    invest_slim = []
    for s in (investment_strategy or []):
        tech_invs_slim = [
            {
                "tech_id": ti.get("tech_id"),
                "name": ti.get("name"),
                "evaluation_scores": ti.get("evaluation_scores"),
                "investment_attractiveness": ti.get("investment_attractiveness"),
                "investment_urgency": ti.get("investment_urgency"),
                "recommended_investment_tier": ti.get("recommended_investment_tier"),
                "investment_scope": ti.get("investment_scope"),
                "recommended_action": ti.get("recommended_action"),
                # rationale, major_risks, resource_focus 모두 풀 (보고서가 풍부하게 인용 가능)
                "rationale": ti.get("rationale") or [],
                "major_risks": ti.get("major_risks") or [],
                "resource_focus": ti.get("resource_focus") or [],
            }
            for ti in (s.get("tech_investments") or [])
        ]
        invest_slim.append({
            "stage": s.get("stage"),
            "period": s.get("period"),
            "stage_assessment": s.get("stage_assessment"),
            "tech_investments": tech_invs_slim,
        })

    # stages (stage_aggregator 출력 — 각 phase 의 period/goal/technologies)
    stages_slim = [
        {"stage": s.get("stage"), "period": s.get("period"),
         "goal": s.get("goal"), "num_items": s.get("num_items"),
         "technologies": s.get("technologies", [])}
        for s in (stages or [])
    ]

    # ── Pre-aggregated insights (LLM 이 보고서에서 인용하기 좋게 미리 계산) ──

    # Tier 분포
    tier_counts = {"Tier 1": 0, "Tier 2": 0, "Tier 3": 0}
    for s in invest_slim:
        for ti in s.get("tech_investments", []):
            t = ti.get("recommended_investment_tier", "Tier 2")
            if t in tier_counts:
                tier_counts[t] += 1

    # Top tech by final_score (technology_strategy 섹션의 핵심 인용)
    top_tech_by_score = sorted(
        [t for t in tech_slim if isinstance(t.get("final_score"), (int, float))],
        key=lambda t: t.get("final_score", 0),
        reverse=True
    )[:5]
    top_tech_summary = [
        {"tech_id": t["tech_id"], "name": t["name"], "category": t.get("category"),
         "final_score": t.get("final_score"), "market_score": t.get("market_score"),
         "boom": t.get("expected_market_boom_quarter")}
        for t in top_tech_by_score
    ]

    # Category 분포 (technology_strategy 의 카테고리 비율 근거)
    category_counts = {}
    for t in tech_slim:
        c = t.get("category") or "Unknown"
        category_counts[c] = category_counts.get(c, 0) + 1

    # 평균 TRL (실행 가능성 지표)
    trls = [t.get("trl") for t in tech_slim if isinstance(t.get("trl"), (int, float))]
    avg_trl = round(sum(trls) / len(trls), 1) if trls else 0

    # Tier 1 기술 리스트 (executive_summary, investment_strategy 의 핵심 인용)
    tier1_list = []
    for s in invest_slim:
        for ti in s.get("tech_investments", []):
            if ti.get("recommended_investment_tier") == "Tier 1":
                tier1_list.append({
                    "tech_id": ti.get("tech_id"),
                    "name": ti.get("name"),
                    "stage": s.get("stage"),
                    "key_reason": (ti.get("rationale") or [""])[0][:120],
                })

    # 의존성 엣지 (roadmap_structure 의 prereq 흐름 인용)
    dependency_edges = []
    for r in roadmap_slim:
        for prereq in (r.get("prerequisites") or []):
            dependency_edges.append(f"{prereq} → {r.get('tech_id')}")

    # 시장 boom_quarter 분포
    boom_counts = {}
    for t in tech_slim:
        bq = t.get("expected_market_boom_quarter") or "?"
        boom_counts[bq] = boom_counts.get(bq, 0) + 1

    insights = {
        "tier_distribution": tier_counts,
        "total_techs": sum(tier_counts.values()) or len(tech_slim),
        "top_5_tech_by_score": top_tech_summary,
        "category_distribution": category_counts,
        "avg_trl": avg_trl,
        "tier1_tech_list": tier1_list,
        "dependency_edges": dependency_edges[:15],  # 핵심 prereq 만
        "boom_quarter_distribution": boom_counts,
    }

    user_prompt = f"""[PROBLEM FRAME]
- Industry    : {problem_frame.get('industry')}
- Company Type: {problem_frame.get('company_type')}
- Time Horizon: {problem_frame.get('time_horizon')}
- Total Budget: {problem_frame.get('total_budget')}
- Objective   : {problem_frame.get('objective')}
- Priorities  : {json.dumps(problem_frame.get('strategic_priorities', []), ensure_ascii=False)}

[FUTURE TREND]
{problem_frame.get('future_trend_summary', '')}

[MARKET CONTEXT]
{json.dumps(market_context or {}, ensure_ascii=False, indent=2)}

[TECH CANDIDATES ({len(tech_slim)})] — final_score 와 market_signal 인용해 technology_strategy / trend_alignment 작성
{json.dumps(tech_slim, ensure_ascii=False, indent=2)}

[PLANNED ROADMAP ({len(roadmap_slim)})] — phase_name + 분기 + prerequisites 인용해 roadmap_structure 작성
{json.dumps(roadmap_slim, ensure_ascii=False, indent=2)}

[ROADMAP STAGES SUMMARY ({len(stages_slim)})] — phase 별 묶음 (roadmap_structure 의 큰 그림)
{json.dumps(stages_slim, ensure_ascii=False, indent=2)}

[INVESTMENT STRATEGY — {len(invest_slim)} stages] — Tier 분포 + 핵심 tech_id + per-tech rationale/risks/resource_focus 인용해 investment_strategy 작성
{json.dumps(invest_slim, ensure_ascii=False, indent=2)}

[PRE-AGGREGATED INSIGHTS] — 보고서 작성 시 직접 인용 (계산 완료된 핵심 통계)
{json.dumps(insights, ensure_ascii=False, indent=2)}

위 insights 활용 가이드:
- executive_summary    → tier_distribution + tier1_tech_list + total_techs 인용
- technology_strategy  → top_5_tech_by_score + category_distribution + avg_trl 인용
- roadmap_structure    → dependency_edges + 각 phase 의 period 인용
- investment_strategy  → tier_distribution + tier1_tech_list (key_reason) + invest_slim 의 recommended_action
- trend_alignment      → boom_quarter_distribution + tech_slim 의 market_signal/patent_signal
- feasibility_and_risk → tier_distribution 의 균형 평가 + invest_slim 의 major_risks + 잔여 이슈
- expected_outcomes    → tier1_tech_list 의 시장 규모 + boom_quarter_distribution

[RESIDUAL ISSUES FROM LAST REVIEW] (이 이슈는 feasibility_and_risk 섹션에 명시적으로 언급할 것)
{json.dumps(residual_issues or [], ensure_ascii=False, indent=2)}

[RESIDUAL FEEDBACK]
{json.dumps(residual_feedback or [], ensure_ascii=False, indent=2)}

위 입력을 바탕으로 7-섹션 TRM 보고서를 생성하라. 모든 섹션을 채워야 한다.
"""

    raw_text = ""
    try:
        # max_tokens 8192: 7 섹션 × ~500자 = ~3500자 = ~5000+ 토큰 필요
        llm = get_llm(max_tokens=8192)
        response = llm.invoke([
            SystemMessage(content=FINAL_REPORT_SYSTEM_PROMPT),
            HumanMessage(content=user_prompt),
        ])
        raw_text = response.content if hasattr(response, "content") else str(response)
        print(f"[Orchestrator · Report] LLM raw 응답 {len(raw_text)}자 · 첫 300자:\n{raw_text[:300]!r}")
        raw = _extract_json(raw_text)
        print(f"[Orchestrator · Report] 파싱 완료 · raw keys: {list(raw.keys()) if isinstance(raw, dict) else type(raw).__name__}")
    except Exception as e:
        print(f"[Orchestrator · Report] ⚠️  생성/파싱 실패: {e} → 최소 폴백")
        if raw_text:
            print(f"    raw 응답 샘플: {raw_text[:300]!r}")
        base = _empty_report()
        issues_str = "; ".join(str(i) for i in (residual_issues or [])) or "(없음)"
        base["executive_summary"] = (
            f"보고서 생성 중 오류로 최소 보고서만 제공합니다. 잔여 이슈: {issues_str}"
        )
        base["feasibility_and_risk"] = f"잔여 이슈: {issues_str}"
        return base

    def _coerce_section_value(v) -> str:
        """LLM 이 string 외 형태 (dict / list) 로 줬을 때 string 으로 강제 변환.
        흔한 패턴:
          - {"title": "...", "text": "..."}      → text 우선
          - {"body": "..."}, {"content": "..."}  → body/content 추출
          - ["문장1", "문장2"]                   → join
        """
        if isinstance(v, str):
            return v
        if isinstance(v, dict):
            # 우선순위 키
            for key in ("text", "body", "content", "value", "summary", "description"):
                sub = v.get(key)
                if isinstance(sub, str) and sub.strip():
                    return sub
            # title + 나머지 string 조합
            title = v.get("title")
            other_strs = [
                str(x) for k2, x in v.items()
                if k2 != "title" and isinstance(x, str) and x.strip()
            ]
            if title and isinstance(title, str) and other_strs:
                return f"{title}: " + " ".join(other_strs)
            if other_strs:
                return " ".join(other_strs)
            if title and isinstance(title, str):
                return title
            return ""
        if isinstance(v, list):
            return " ".join(
                str(x) for x in v
                if isinstance(x, (str, int, float)) and str(x).strip()
            )
        return ""

    # 누락 섹션은 빈 문자열로 보전
    report = _empty_report()
    filled_count = 0
    for k in report.keys():
        v = raw.get(k)
        coerced = _coerce_section_value(v)
        report[k] = coerced
        if coerced.strip():
            filled_count += 1

    # LLM 이 모든 섹션을 빈 string 으로 반환한 경우 경고 + insights 기반 boilerplate
    if filled_count == 0:
        print(
            f"[Orchestrator · Report] ⚠️  LLM 이 7섹션 모두 빈 응답 — raw keys={list(raw.keys())}"
        )
        print(f"    raw 응답 샘플: {raw_text[:500]!r}")
        # insights 기반 최소 보고서로 채움 (전부 빈 채로 두지 않게)
        tier_dist = insights.get("tier_distribution", {})
        top5 = insights.get("top_5_tech_by_score", [])
        avg_trl = insights.get("avg_trl", 0)
        report["executive_summary"] = (
            f"본 로드맵은 {problem_frame.get('industry')} 영역에서 "
            f"총 {insights.get('total_techs', 0)}개 후보 기술 (평균 TRL {avg_trl}) 을 "
            f"Tier 분포 T1={tier_dist.get('Tier 1', 0)} / T2={tier_dist.get('Tier 2', 0)} / "
            f"T3={tier_dist.get('Tier 3', 0)} 로 배분하며 "
            f"총 예산 ${problem_frame.get('total_budget', 0):,.0f} 규모로 추진합니다."
        )
        if top5:
            top_names = ", ".join(f"{t['tech_id']}({t.get('name','')[:15]})" for t in top5[:3])
            report["technology_strategy"] = f"final_score 상위 핵심 기술: {top_names}."
        issues_str = "; ".join(str(i) for i in (residual_issues or [])) or "(없음)"
        report["feasibility_and_risk"] = f"잔여 이슈: {issues_str}"

    return report


def run_orchestrator_review(
    iteration: int,
    problem_frame: dict,
    tech_candidates: list,
    planned_roadmap: list,
    investment_strategy: list,
    stages: list,
    market_context: dict,
    previous_feedback: list,
    active_agents: list,
) -> ReviewResult:
    """
    TRM 평가 + ACCEPT/REVISE 결정 + (ACCEPT 시) 7-섹션 보고서 생성.
    """
    print(f"\n[Orchestrator · Review] ▶ iteration={iteration+1} / {MAX_ORCHESTRATOR_ITERATIONS}")

    raw_text = ""
    try:
        llm = get_llm(max_tokens=4096)
        user_prompt = _build_review_user_prompt(
            problem_frame=problem_frame,
            tech_candidates=tech_candidates,
            planned_roadmap=planned_roadmap,
            investment_strategy=investment_strategy,
            stages=stages,
            market_context=market_context,
            previous_feedback=previous_feedback,
            active_agents=active_agents,
        )
        response = llm.invoke([
            SystemMessage(content=REVIEW_SYSTEM_PROMPT),
            HumanMessage(content=user_prompt),
        ])
        raw_text = response.content if hasattr(response, "content") else str(response)
        try:
            result = _extract_json(raw_text)
        except Exception as parse_err:
            # JSON 파싱 실패 → partial recovery 시도 (LLM 응답 자체는 살아있음)
            print(
                f"[Orchestrator · Review] ⚠️  JSON 파싱 실패: {parse_err} "
                f"→ partial recovery 시도 (raw {len(raw_text)}자)"
            )
            recovered = _partial_recover_review(raw_text)
            non_empty = sum([
                1 if recovered.get("decision") else 0,
                1 if recovered.get("issues") else 0,
                1 if recovered.get("trm_assessment") else 0,
                1 if recovered.get("report") else 0,
            ])
            print(
                f"[Orchestrator · Review] partial recovery 결과: "
                f"decision={recovered.get('decision') or '∅'} · "
                f"issues={len(recovered.get('issues') or [])} · "
                f"trm_keys={len(recovered.get('trm_assessment') or {})} · "
                f"report_sections={len(recovered.get('report') or {})}"
            )
            if non_empty >= 1:
                result = recovered
                # 핵심 필드 누락 보강
                if not result.get("decision"):
                    result["decision"] = "ACCEPT"
                if not result.get("refinement"):
                    result["refinement"] = {"rerun_agents": [], "feedback": []}
                # 파싱 오류도 issue 로 남겨 추적 가능하게
                issues = list(result.get("issues") or [])
                issues.append({"axis": "review_error", "text": f"json_parse: {parse_err}"})
                result["issues"] = issues
                if not result.get("diagnostic_summary"):
                    result["diagnostic_summary"] = (
                        "LLM 응답 JSON 형식 오류 — partial recovery 로 핵심 필드만 살림."
                    )
            else:
                # 회복 실패 → 기존 폴백
                raise parse_err
    except Exception as e:
        print(f"[Orchestrator · Review] ⚠️  LLM 호출/파싱 오류: {e} → 폴백 ACCEPT")
        result = {
            "decision": "ACCEPT",
            "trm_assessment": {},
            "issues": [f"review_error: {e}"],
            "refinement": {"rerun_agents": [], "feedback": []},
            "report": {
                "executive_summary": "Review LLM 호출 실패로 기본 보고서를 생성합니다.",
                "technology_strategy": "",
                "roadmap_structure": "",
                "investment_strategy": "",
                "trend_alignment": "",
                "feasibility_and_risk": "",
                "expected_outcomes": "",
            },
            "diagnostic_summary": "",
        }

    # ── decision 필드 타입 정규화 ───────────────────────────────
    # LLM 이 "ACCEPT" 대신 {"value": "ACCEPT"} / {"decision": "ACCEPT"} /
    # {"action": "ACCEPT"} / {"verdict": "ACCEPT"} 같은 dict 로 반환하거나
    # ["ACCEPT"] 처럼 리스트로 반환하는 경우가 있어 string 으로 강제 변환.
    raw_decision = result.get("decision")

    def _coerce_decision(d):
        if isinstance(d, str):
            return d.strip().upper()
        if isinstance(d, dict):
            # 1) 문자열 키: "value"/"decision"/"action"/"verdict"/"result"/"label"/"status"
            for k in ("value", "decision", "action", "verdict", "result", "label", "status"):
                v = d.get(k)
                if isinstance(v, str) and v.strip():
                    s = v.strip().upper()
                    # "APPROVED"/"PASS"/"OK" 등을 ACCEPT 로 매핑
                    if s in ("APPROVED", "PASS", "PASSED", "OK", "APPROVE"):
                        return "ACCEPT"
                    if s in ("REJECTED", "FAIL", "FAILED", "REJECT"):
                        return "REVISE"
                    return s
            # 2) boolean 키: "acceptance"/"approved"/"accepted"/"pass"
            for k in ("acceptance", "approved", "accepted", "pass"):
                v = d.get(k)
                if isinstance(v, bool):
                    return "ACCEPT" if v else "REVISE"
            # 3) boolean "rejected"/"revise"
            for k in ("rejected", "revise", "needs_revision"):
                v = d.get(k)
                if isinstance(v, bool):
                    return "REVISE" if v else "ACCEPT"
            return ""
        if isinstance(d, bool):
            return "ACCEPT" if d else "REVISE"
        if isinstance(d, list) and d:
            first = d[0]
            if isinstance(first, str):
                return first.strip().upper()
        return ""

    coerced = _coerce_decision(raw_decision)

    # LLM 이 decision 필드 안에 response 전체를 통째로 쑤셔넣은 경우
    # (예: {"investment_plan": ..., "issues": ...})
    # → decision 안의 익숙한 키를 top-level 로 복구
    if coerced not in ("ACCEPT", "REVISE") and isinstance(raw_decision, dict):
        promoted = []
        for nested_key in ("issues", "refinement", "report", "trm_assessment",
                           "diagnostic_summary"):
            if nested_key in raw_decision and not result.get(nested_key):
                result[nested_key] = raw_decision[nested_key]
                promoted.append(nested_key)
        if promoted:
            print(
                f"[Orchestrator · Review] ⚠️  decision dict 안에서 "
                f"{promoted} 를 top-level 로 복구"
            )

    if coerced not in ("ACCEPT", "REVISE"):
        # 알 수 없으면 안전하게 ACCEPT (무한 루프 방지)
        if raw_decision is not None:
            # 전체 dict 를 로그에 찍으면 너무 길어서 type + 키 목록만 노출
            if isinstance(raw_decision, dict):
                summary = f"dict keys={list(raw_decision.keys())}"
            elif isinstance(raw_decision, (list, tuple)):
                summary = f"{type(raw_decision).__name__} len={len(raw_decision)}"
            else:
                summary = repr(raw_decision)[:120]
            print(
                f"[Orchestrator · Review] ⚠️  decision 필드 해석 실패 "
                f"({type(raw_decision).__name__}: {summary}) → ACCEPT 로 폴백"
            )
        coerced = "ACCEPT"
    if coerced != raw_decision:
        result["decision"] = coerced

    result = _force_accept_on_last_iteration(result, iteration)

    # ── 비활성 agent 필터링 가드 ─────────────────────────────
    # LLM 이 프롬프트 지시를 어기고 OFF 된 agent 를 rerun 대상으로 지정했거나
    # disabled agent 의 부재를 issue 로 잡은 경우 자동 보정.
    #
    # LLM 이 "Technology Analyst" / "Technology Analyst Agent" / "technology analyst"
    # 등 다양한 형태로 라벨을 낼 수 있어 **정규화 매칭** 사용.
    _CANONICAL_LABELS = {
        # Canonical 풀네임
        "technology analyst": ("Technology Analyst", "1"),
        "roadmap planner":    ("Roadmap Planner",    "2"),
        "investment strategist": ("Investment Strategist", "3"),
        # 숫자 id 별칭 — "Agent 1", "agent_1", "1" 모두 커버
        "1":                  ("Technology Analyst", "1"),
        "2":                  ("Roadmap Planner",    "2"),
        "3":                  ("Investment Strategist", "3"),
        # LLM 이 자주 쓰는 줄임말 별칭
        "technology":         ("Technology Analyst", "1"),
        "tech":               ("Technology Analyst", "1"),
        "tech analyst":       ("Technology Analyst", "1"),
        "analyst":            ("Technology Analyst", "1"),
        "roadmap":            ("Roadmap Planner",    "2"),
        "planner":            ("Roadmap Planner",    "2"),
        "investment":         ("Investment Strategist", "3"),
        "invest":             ("Investment Strategist", "3"),
        "strategist":         ("Investment Strategist", "3"),
    }

    def _normalize_agent_label(s):
        """임의 라벨(string or dict) → (canonical_label, key) 또는 None"""
        # LLM 이 {"agent_id": "Agent 1", "task": "..."} 형태로 반환하는 경우 대응
        if isinstance(s, dict):
            s = s.get("agent_id") or s.get("agent") or s.get("name") or s.get("id")
        if not isinstance(s, str):
            return None
        base = s.strip().lower()
        # "Agent" 접미사 / "Agent" 접두사 / 군더더기 제거
        for suf in (" agent", " 에이전트"):
            if base.endswith(suf):
                base = base[: -len(suf)].strip()
        for pre in ("agent ", "agent_", "agent"):
            if base.startswith(pre):
                base = base[len(pre):].strip()
                break
        return _CANONICAL_LABELS.get(base)

    def _extract_task_text(r) -> str:
        """rerun_agents 항목이 dict 면 task/feedback 텍스트 추출 (피드백 보강용)"""
        if isinstance(r, dict):
            for k in ("task", "feedback", "instruction", "reason"):
                v = r.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
        return ""

    ref = result.get("refinement") or {}
    raw_rerun = ref.get("rerun_agents", []) or []

    filtered_rerun = []
    dropped = []
    extra_feedback = []   # dict 항목의 task 를 feedback 으로 보존
    for r in raw_rerun:
        hit = _normalize_agent_label(r)
        if hit is not None and hit[1] in active_agents:
            filtered_rerun.append(hit[0])   # 정규화된 canonical 라벨로 저장
            t = _extract_task_text(r)
            if t:
                extra_feedback.append(f"[{hit[0]}] {t}")
        else:
            dropped.append(r)

    # dict 형태로 온 task 들은 refinement.feedback 에 추가 (회수 방지)
    if extra_feedback:
        existing_fb = list(ref.get("feedback") or [])
        ref["feedback"] = existing_fb + extra_feedback
        result["refinement"] = ref

    if dropped:
        print(f"[Orchestrator · Review] ⚠️  rerun 요청 중 비활성/미매칭 제거: {dropped}")
    if filtered_rerun != raw_rerun:
        ref["rerun_agents"] = filtered_rerun
        result["refinement"] = ref

    if result.get("decision") == "REVISE" and not filtered_rerun:
        print(
            "[Orchestrator · Review] REVISE 이지만 남은 rerun 대상이 없음 "
            "(모두 비활성 agent 또는 미매칭 라벨) → ACCEPT 로 전환"
        )
        result["decision"] = "ACCEPT"
        result["_forced_accept"] = True

    # ── 비활성 축 override + axis 기반 issue drop ─────────
    # ablation study 순수성은 "키워드 매칭" 이 아니라 "TRM 축 ↔ agent 매핑"
    # 이라는 구조적 관계로 처리한다.
    #
    #   axis                      ← 필요 agent
    #   ─────────────────────────────────────
    #   feasibility               (problem_frame — 항상 가능)
    #   sequencing                Agent 2
    #   strategic_alignment       Agent 1
    #   investment_rationality    Agent 3
    #   portfolio_balance         Agent 2 AND Agent 3
    a1_on = "1" in active_agents
    a2_on = "2" in active_agents
    a3_on = "3" in active_agents
    _AXIS_ACTIVE = {
        "feasibility":            True,
        "sequencing":             a2_on,
        "strategic_alignment":    a1_on,
        "investment_rationality": a3_on,
        "portfolio_balance":      a2_on and a3_on,
    }
    _AXIS_STUBS = {
        "feasibility": {
            "budget_feasible": None, "schedule_feasible": None,
            "comment": "N/A (agent disabled)",
        },
        "sequencing": {
            "dependency_valid": None, "comment": "N/A (agent disabled)",
        },
        "strategic_alignment": {
            "company_fit": None, "future_trend_alignment": None,
            "comment": "N/A (agent disabled)",
        },
        "investment_rationality": {
            "over_invested": [], "under_invested": [],
            "comment": "N/A (agent disabled)",
        },
        "portfolio_balance": {
            "short_long_balance": None, "risk_balance": None,
            "comment": "N/A (agent disabled)",
        },
    }

    # (1) TRM 축 override — LLM 이 뭘 냈든 disabled 축은 코드가 덮어씀
    trm = result.get("trm_assessment") or {}
    for axis, on in _AXIS_ACTIVE.items():
        if not on:
            trm[axis] = _AXIS_STUBS[axis]
    result["trm_assessment"] = trm

    # (2) issues 정규화 + 비활성 축 드롭
    #     순서: ①axis 정규화 → ②무효 issue 드롭 → ③disabled axis 드롭
    #     (ablation 순수성 유지: 별칭을 canonical 축으로 매핑한 다음에
    #      disabled 여부를 판단해야 Agent OFF 일 때 제대로 걸러짐)
    _CANONICAL_AXES = {
        "feasibility", "sequencing", "strategic_alignment",
        "investment_rationality", "portfolio_balance",
    }
    # LLM 이 자주 만드는 별칭/오타 → canonical 매핑
    _AXIS_ALIASES = {
        # investment_rationality 계열 (stage 필드명을 axis 로 오인하는 경우)
        "investment_attractiveness":  "investment_rationality",
        "investment_urgency":         "investment_rationality",
        "investment_tier":            "investment_rationality",
        "investment_scope":           "investment_rationality",
        "investment":                 "investment_rationality",
        "investment_plan":            "investment_rationality",
        "budget_allocation":          "investment_rationality",
        # strategic_alignment 계열
        "alignment":                  "strategic_alignment",
        "strategy_alignment":         "strategic_alignment",
        "technology_strategy":        "strategic_alignment",
        "trend_alignment":            "strategic_alignment",
        # sequencing 계열
        "dependency":                 "sequencing",
        "dependencies":               "sequencing",
        "roadmap":                    "sequencing",
        "timeline":                   "sequencing",
        # portfolio_balance 계열
        "portfolio":                  "portfolio_balance",
        "balance":                    "portfolio_balance",
        "risk_balance":               "portfolio_balance",
        # feasibility 계열
        "budget":                     "feasibility",
        "schedule":                   "feasibility",
    }

    def _canonicalize_axis(ax):
        if not isinstance(ax, str):
            return None
        k = ax.strip().lower().replace(" ", "_").replace("-", "_")
        if k in _CANONICAL_AXES:
            return k
        return _AXIS_ALIASES.get(k)   # None → 알 수 없는 축

    # 일반 "category" 이름 → TRM axis 매핑 (LLM 이 자유 카테고리로 내는 경우)
    _CATEGORY_TO_AXIS = {
        "roadmap":              "sequencing",
        "dependency":           "sequencing",
        "timeline":             "sequencing",
        "schedule":             "feasibility",
        "budget":               "feasibility",
        "cost":                 "feasibility",
        "strategy":             "strategic_alignment",
        "alignment":            "strategic_alignment",
        "technology":           "strategic_alignment",
        "trend":                "strategic_alignment",
        "investment":           "investment_rationality",
        "portfolio":            "portfolio_balance",
        "risk":                 "portfolio_balance",
        "balance":              "portfolio_balance",
    }

    def _infer_axis_from_text(text: str) -> Optional[str]:
        """text 내용에서 axis 추론 (axis 키가 없을 때 fallback)"""
        if not isinstance(text, str):
            return None
        t = text.lower()
        # 우선순위: 더 구체적인 키워드 먼저
        if any(k in t for k in ["over-invested", "under-invested", "investment tier", "over invested", "under invested", "tier is too", "투자 tier", "tier 배분"]):
            return "investment_rationality"
        if any(k in t for k in ["budget", "cost", "예산", "비용 초과", "exceed"]):
            return "feasibility"
        if any(k in t for k in ["schedule", "timeline", "기한", "lead time"]):
            return "feasibility"
        if any(k in t for k in ["dependency", "prerequisite", "prereq", "sequenc", "선후", "의존"]):
            return "sequencing"
        if any(k in t for k in ["short-long", "short/long", "portfolio", "balance", "장단기 균형"]):
            return "portfolio_balance"
        if any(k in t for k in ["trend", "future", "alignment", "strategy", "company fit", "전략 적합", "트렌드"]):
            return "strategic_alignment"
        return None

    def _extract_issue_axis_and_text(it: dict):
        """
        dict issue 에서 (axis, text) 추출. 표준 스키마 외 변종 포맷도 지원.
          표준 :  {"axis": "...", "text": "..."}
          변종 :  {"id": "T01", "description": "...", "category": "Roadmap", "severity": "..."}
                 {"type": "...", "message": "..."}
                 {"issue_id": "T01", "description": "...investment tier...", "urgency": "High"}
        """
        # axis 후보 키
        canon = None
        for ak in ("axis", "category", "type", "area", "dimension"):
            raw_ax = it.get(ak)
            if raw_ax:
                canon = _canonicalize_axis(raw_ax)
                if canon is None and isinstance(raw_ax, str):
                    canon = _CATEGORY_TO_AXIS.get(raw_ax.strip().lower())
                if canon:
                    break

        # text 후보 키
        text = ""
        for tk in ("text", "description", "message", "detail", "issue", "comment"):
            v = it.get(tk)
            if isinstance(v, str) and v.strip():
                text = v.strip()
                break

        # axis 키가 없거나 매칭 실패 시 → text 내용에서 키워드로 추론
        if canon is None and text:
            canon = _infer_axis_from_text(text)

        return canon, text

    disabled_axes = {ax for ax, on in _AXIS_ACTIVE.items() if not on}

    raw_issues = result.get("issues") or []
    kept_issues = []
    dropped_invalid = []     # axis 알 수 없음 or text 비어있음
    dropped_disabled = []    # disabled 축으로 매핑되어 드롭
    normalized_log = []      # 별칭 → canonical 매핑 로그

    for it in raw_issues:
        if isinstance(it, dict):
            canon, text = _extract_issue_axis_and_text(it)

            # text 가 비어있으면 issue 로서 의미 없음 → 드롭
            if not text:
                dropped_invalid.append(it)
                continue

            # axis 가 canonical 5개 중 하나가 아니고 별칭도 아니면 드롭
            if canon is None:
                dropped_invalid.append(it)
                continue

            # 원본 axis key 와 다르게 정규화된 경우 로그 (추적용)
            raw_ax_hint = it.get("axis") or it.get("category") or it.get("type")
            if isinstance(raw_ax_hint, str) and raw_ax_hint.strip().lower().replace(" ", "_").replace("-", "_") != canon:
                normalized_log.append((raw_ax_hint, canon))

            # 정규화된 dict 재조립 (다른 메타 필드는 유지)
            it = {**it, "axis": canon, "text": text}

            # disabled axis 로 매핑됐으면 드롭 (ablation 순수성)
            if canon in disabled_axes:
                dropped_disabled.append(it)
                continue

            kept_issues.append(it)
        elif isinstance(it, str):
            # plain string issue — 스키마 위반이지만 복구 시도.
            # 문자열 전체가 canonical axis 이름/별칭이면 내용 없는 빈 라벨이므로 드롭.
            s = it.strip()
            canon = _canonicalize_axis(s)
            if canon is not None and len(s.split()) <= 2:
                # "strategic_alignment", "investment attractiveness" 같은 bare axis 라벨
                dropped_invalid.append(it)
                continue
            # 그 외 의미 있는 문장은 하위 호환으로 유지
            kept_issues.append(s)
        else:
            # 기타 타입 (list/None/숫자 등) → 드롭
            dropped_invalid.append(it)

    if normalized_log:
        print(f"[Orchestrator · Review] axis 별칭 정규화 {len(normalized_log)}건:")
        for orig, canon in normalized_log:
            print(f"    {orig!r} → {canon!r}")
    if dropped_invalid:
        print(
            f"[Orchestrator · Review] 무효 issue {len(dropped_invalid)}건 drop "
            f"(axis 미매칭 or text 공란):"
        )
        for d in dropped_invalid:
            print(f"    - {d}")
    if dropped_disabled:
        print(
            f"[Orchestrator · Review] 비활성 축({sorted(disabled_axes)}) "
            f"관련 issue {len(dropped_disabled)}건 drop:"
        )
        for d in dropped_disabled:
            print(f"    - {d}")

    if normalized_log or dropped_invalid or dropped_disabled:
        result["issues"] = kept_issues

    # (3) REVISE 인데 남은 issue 가 전부 disabled 축이어서 사라졌고
    #     rerun 대상도 없으면 → ACCEPT 전환 (위의 rerun 필터 block 에서
    #     이미 처리되지만, issue 가 이유인 경우를 위해 한번 더 확인).
    if result.get("decision") == "REVISE" and not kept_issues and not filtered_rerun:
        print(
            "[Orchestrator · Review] 모든 issue 가 비활성 축으로 drop 됨 "
            "→ REVISE 근거 소멸 · ACCEPT 로 전환"
        )
        result["decision"] = "ACCEPT"
        result["_forced_accept"] = True

    # ── (4) TRM FAIL 자동 감지 → issues / feedback 자동 추가 + REVISE 강제 ──
    # LLM 이 trm_assessment 에 FAIL 을 표시했는데 issues 가 비어있고 ACCEPT 인
    # 모순 케이스를 자동 보정. (사용자 케이스: feasibility/sequencing FAIL 인데
    # decision=ACCEPT, issues=[] 로 통과되는 버그)
    fail_axis_to_agent = {
        "feasibility":            ["Roadmap Planner", "Investment Strategist"],
        "sequencing":             ["Roadmap Planner"],
        "strategic_alignment":    ["Technology Analyst"],
        "investment_rationality": ["Investment Strategist"],
        "portfolio_balance":      ["Roadmap Planner", "Investment Strategist"],
    }

    auto_issues = []
    auto_feedback = []
    auto_rerun_set = set(filtered_rerun)
    trm = result.get("trm_assessment") or {}

    feas = trm.get("feasibility") or {}
    if feas.get("budget_feasible") is False:
        msg = (feas.get("comment") or "").strip() or "Budget exceeds the planned allocation."
        auto_issues.append({"axis": "feasibility", "text": f"Budget infeasible: {msg}"})
        auto_feedback.append(f"[Feasibility/Budget] {msg} — 예산 내로 투자 규모 재조정 필요.")
    if feas.get("schedule_feasible") is False:
        msg = (feas.get("comment") or "").strip() or "Schedule is unrealistic given TRL."
        auto_issues.append({"axis": "feasibility", "text": f"Schedule infeasible: {msg}"})
        auto_feedback.append(f"[Feasibility/Schedule] {msg} — 타임라인 현실성 재검토 필요.")

    seq = trm.get("sequencing") or {}
    if seq.get("dependency_valid") is False:
        msg = (seq.get("comment") or "").strip() or "Dependency relationships violated."
        auto_issues.append({"axis": "sequencing", "text": f"Dependency invalid: {msg}"})
        auto_feedback.append(f"[Sequencing] {msg} — 선행 기술 완료 → 후행 기술 시작 순서 재조정.")

    inv = trm.get("investment_rationality") or {}
    over_inv = inv.get("over_invested") or []
    under_inv = inv.get("under_invested") or []
    if over_inv:
        auto_issues.append({"axis": "investment_rationality", "text": f"Over-invested: {', '.join(map(str, over_inv))}"})
        auto_feedback.append(f"[Investment] 과투자 기술 ({', '.join(map(str, over_inv))}) — Tier 하향 조정 검토.")
    if under_inv:
        auto_issues.append({"axis": "investment_rationality", "text": f"Under-invested: {', '.join(map(str, under_inv))}"})
        auto_feedback.append(f"[Investment] 저투자 기술 ({', '.join(map(str, under_inv))}) — Tier 상향 조정 검토.")

    if auto_issues:
        # 기존 issues 와 중복 제거 (text 같으면 중복으로 판단)
        existing_texts = {it.get("text") if isinstance(it, dict) else str(it) for it in kept_issues}
        for ai in auto_issues:
            if ai.get("text") not in existing_texts:
                # disabled axis 면 추가하지 않음 (ablation 순수성)
                if ai["axis"] not in disabled_axes:
                    kept_issues.append(ai)

        # 자동 rerun 대상 추가 (active agents 만)
        for ai in auto_issues:
            for ag in fail_axis_to_agent.get(ai["axis"], []):
                # Agent label → key 매핑
                hit = _normalize_agent_label(ag)
                if hit and hit[1] in active_agents and hit[0] not in auto_rerun_set:
                    auto_rerun_set.add(hit[0])

    # ── kept_issues 의 axis 도 책임 Agent 자동 보강 ──
    # LLM 이 issues 는 정확히 적었지만 rerun_agents 에 책임 Agent 를 빠뜨린 경우
    # (예: strategic_alignment issue 를 적고 rerun_agents 에서 Technology Analyst 누락).
    issue_axes_present = {
        it.get("axis") for it in kept_issues
        if isinstance(it, dict) and isinstance(it.get("axis"), str)
    }
    rerun_added_for_issues = []
    for ax in issue_axes_present:
        if ax in disabled_axes:
            continue
        for ag in fail_axis_to_agent.get(ax, []):
            hit = _normalize_agent_label(ag)
            if hit and hit[1] in active_agents and hit[0] not in auto_rerun_set:
                auto_rerun_set.add(hit[0])
                rerun_added_for_issues.append((ax, hit[0]))
    if rerun_added_for_issues:
        print(
            f"[Orchestrator · Review] issue axis 기반 rerun 자동 보강 "
            f"{len(rerun_added_for_issues)}건:"
        )
        for ax, ag in rerun_added_for_issues:
            print(f"    axis={ax} → +{ag}")

    # ── 공통 후처리 — 어느 보강이라도 발생했으면 result 에 반영 ──
    if auto_issues or rerun_added_for_issues:
        # feedback 합치기 (auto_feedback 만 — issue axis 보강은 feedback 추가 안 함)
        existing_feedback = list((result.get("refinement") or {}).get("feedback") or [])
        for f in auto_feedback:
            if f not in existing_feedback:
                existing_feedback.append(f)

        # refinement 갱신
        ref = result.get("refinement") or {}
        order = ["Technology Analyst", "Roadmap Planner", "Investment Strategist"]
        ref["rerun_agents"] = sorted(auto_rerun_set, key=lambda x: order.index(x) if x in order else 99)
        ref["feedback"] = existing_feedback
        result["refinement"] = ref
        filtered_rerun = list(ref["rerun_agents"])

        # ACCEPT 였는데 FAIL 이 있으면 REVISE 로 전환 (단 max iteration 안 넘었으면)
        if auto_issues and result.get("decision") == "ACCEPT" and iteration + 1 < MAX_ORCHESTRATOR_ITERATIONS:
            print(
                f"[Orchestrator · Review] ⚠️ TRM FAIL {len(auto_issues)}건 감지 "
                f"(LLM 이 ACCEPT 로 통과시킴) → REVISE 로 자동 전환"
            )
            result["decision"] = "REVISE"
            result.pop("_forced_accept", None)
        elif auto_issues and result.get("decision") == "ACCEPT":
            print(
                f"[Orchestrator · Review] ⚠️ TRM FAIL {len(auto_issues)}건 있지만 "
                f"max iteration 도달 → ACCEPT 유지 (최종 보고서 feasibility_and_risk 에 명시됨)"
            )
        elif auto_issues:
            print(
                f"[Orchestrator · Review] TRM FAIL → 자동 issues {len(auto_issues)}건 "
                f"+ feedback {len(auto_feedback)}건 추가"
            )

    # ── 보고서 생성 — ACCEPT/REVISE 모두 (매 iter 결과를 풀 보고서로 보여줌) ──
    # REVISE 일 때는 "잠정" 보고서 — residual_issues 가 feasibility_and_risk 에 명시됨.
    # 다음 iter 에서 갱신되며 최종 ACCEPT 시점의 보고서가 최종본.
    decision_upper = (result.get("decision") or "ACCEPT").upper()
    report = result.get("report") or {}
    if _report_fill_count(report) < 3:
        if decision_upper == "REVISE":
            label = f"REVISE iter {iteration+1} 잠정 보고서"
        elif result.get("_forced_accept"):
            label = "강제 ACCEPT"
        else:
            label = "report 섹션 부족"
        print(f"[Orchestrator · Review] {label} → 보고서 전용 LLM 호출")
        try:
            final_report = generate_final_report(
                problem_frame=problem_frame,
                tech_candidates=tech_candidates,
                planned_roadmap=planned_roadmap,
                investment_strategy=investment_strategy,
                stages=stages,
                market_context=market_context,
                residual_issues=result.get("issues") or [],
                residual_feedback=(result.get("refinement") or {}).get("feedback") or [],
            )
            result["report"] = final_report
            fill = _report_fill_count(final_report)
            print(f"[Orchestrator · Review] 보고서 생성 완료 · {fill}/7 섹션 ({label})")
        except Exception as e:
            print(f"[Orchestrator · Review] ⚠️  보고서 생성 실패: {e}")

    # 로그 출력
    decision = (result.get("decision") or "ACCEPT").upper()
    print(f"[Orchestrator · Review] decision = {decision}")

    # issues 를 최종적으로 문자열로 평탄화 (web UI 호환).
    # dict 면 "[axis] text" 로 변환, 그 외(str/기타)는 str() 적용.
    def _issue_to_str(it) -> str:
        if isinstance(it, dict):
            ax = it.get("axis") or ""
            tx = it.get("text") or ""
            if ax and tx:
                return f"[{ax}] {tx}"
            return tx or ax or json.dumps(it, ensure_ascii=False)
        return str(it)

    issues_raw = result.get("issues", []) or []
    issues = [_issue_to_str(i) for i in issues_raw]
    if issues:
        print(f"  issues ({len(issues)}):")
        for s in issues:
            print(f"    • {s}")

    refinement = result.get("refinement") or {}
    rerun = refinement.get("rerun_agents", []) or []
    feedback = refinement.get("feedback", []) or []

    if decision == "REVISE":
        print(f"  → rerun_agents: {rerun}")
        for f in feedback:
            print(f"    - {f}")

    # 정규화된 ReviewResult 로 리턴 (내부 플래그 _forced_accept 는 노출하지 않음)
    return {
        "decision": decision,
        "trm_assessment": result.get("trm_assessment") or {},
        "issues": issues,
        "refinement": {"rerun_agents": rerun, "feedback": feedback},
        "report": result.get("report") or {},
        "diagnostic_summary": result.get("diagnostic_summary", "") or "",
    }
