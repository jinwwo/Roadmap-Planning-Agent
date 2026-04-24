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

def _extract_json(text: str) -> dict:
    cleaned = re.sub(r"```(?:json)?\s*", "", text).replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]+\}", cleaned)
        if match:
            return json.loads(match.group())
        raise ValueError(f"JSON 파싱 실패:\n{text[:300]}")


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
        invest_slim = [
            {"stage": s.get("stage"), "period": s.get("period"),
             "evaluation_scores": s.get("evaluation_scores"),
             "investment_attractiveness": s.get("investment_attractiveness"),
             "investment_urgency": s.get("investment_urgency"),
             "recommended_investment_tier": s.get("recommended_investment_tier"),
             "investment_scope": s.get("investment_scope")}
            for s in (investment_strategy or [])
        ]
        sections.append(
            f"[INVESTMENT PLAN · stage-level · {len(invest_slim)} items]\n"
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
[Sections]
1. executive_summary      : 2-3 문장. 핵심 메시지.
2. technology_strategy    : 어떤 기술군에 어떤 논리로 집중하는지.
3. roadmap_structure      : 단계(phase/stage) 흐름 · 선후 의존성 · 분기별 타임라인 요약.
4. investment_strategy    : Tier 배분 근거 · 예산 논리 · 단기/장기 균형.
5. trend_alignment        : 미래 기술 동향과의 정합성 · 선제 포지셔닝.
6. feasibility_and_risk   : 예산/일정 실행 가능성 · 리스크 · **잔여 이슈 기록**.
7. expected_outcomes      : 성공 시 기대 성과 · KPI · 시장 포지션.

[Rules]
- 한국어로 작성. 기술 고유명사는 영문 허용 (EUV, ALD, GAA, HBM, TRL 등).
- 각 섹션은 최소 2문장 이상.
- 입력 데이터에서 드러나지 않는 사실을 지어내지 말 것.
- 모든 7개 섹션에 내용을 채울 것.

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
    # Slim payload
    tech_slim = [
        {"tech_id": t.get("tech_id"), "name": t.get("name"),
         "category": t.get("category"), "trl": t.get("trl"),
         "final_score": t.get("final_score")}
        for t in (tech_candidates or [])
    ]
    roadmap_slim = [
        {"tech_id": r.get("tech_id"), "name": r.get("name"),
         "phase_name": r.get("phase_name"),
         "start_q": r.get("start_q"), "target_q": r.get("target_q"),
         "prerequisites": r.get("prerequisites"),
         "lead_time_quarters": r.get("lead_time_quarters")}
        for r in (planned_roadmap or [])
    ]
    invest_slim = [
        {"stage": s.get("stage"), "period": s.get("period"),
         "evaluation_scores": s.get("evaluation_scores"),
         "recommended_investment_tier": s.get("recommended_investment_tier"),
         "investment_scope": s.get("investment_scope"),
         "recommended_action": s.get("recommended_action")}
        for s in (investment_strategy or [])
    ]

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

[TECH CANDIDATES ({len(tech_slim)})]
{json.dumps(tech_slim, ensure_ascii=False, indent=2)}

[PLANNED ROADMAP ({len(roadmap_slim)})]
{json.dumps(roadmap_slim, ensure_ascii=False, indent=2)}

[INVESTMENT STRATEGY — {len(invest_slim)} stages]
{json.dumps(invest_slim, ensure_ascii=False, indent=2)}

[RESIDUAL ISSUES FROM LAST REVIEW] (이 이슈는 feasibility_and_risk 섹션에 명시적으로 언급할 것)
{json.dumps(residual_issues or [], ensure_ascii=False, indent=2)}

[RESIDUAL FEEDBACK]
{json.dumps(residual_feedback or [], ensure_ascii=False, indent=2)}

위 입력을 바탕으로 7-섹션 TRM 보고서를 생성하라. 모든 섹션을 채워야 한다.
"""

    try:
        llm = get_llm(max_tokens=4096)
        response = llm.invoke([
            SystemMessage(content=FINAL_REPORT_SYSTEM_PROMPT),
            HumanMessage(content=user_prompt),
        ])
        raw = _extract_json(response.content)
    except Exception as e:
        print(f"[Orchestrator · Report] ⚠️  생성 실패: {e} → 최소 폴백")
        base = _empty_report()
        issues_str = "; ".join(residual_issues or []) or "(없음)"
        base["executive_summary"] = (
            f"반복 상한 도달 후 자동 보고서 생성 중 오류로 최소 보고서만 제공합니다. 잔여 이슈: {issues_str}"
        )
        base["feasibility_and_risk"] = f"잔여 이슈: {issues_str}"
        return base

    # 누락 섹션은 빈 문자열로 보전
    report = _empty_report()
    for k in report.keys():
        v = raw.get(k)
        if isinstance(v, str):
            report[k] = v
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
        result = _extract_json(response.content)
    except Exception as e:
        print(f"[Orchestrator · Review] ⚠️  LLM 호출 오류: {e} → 폴백 ACCEPT")
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
        "technology analyst": ("Technology Analyst", "1"),
        "roadmap planner":    ("Roadmap Planner",    "2"),
        "investment strategist": ("Investment Strategist", "3"),
        # 숫자 id 별칭 — "Agent 1", "agent_1", "1" 모두 커버
        "1":                  ("Technology Analyst", "1"),
        "2":                  ("Roadmap Planner",    "2"),
        "3":                  ("Investment Strategist", "3"),
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

    def _extract_issue_axis_and_text(it: dict):
        """
        dict issue 에서 (axis, text) 추출. 표준 스키마 외 변종 포맷도 지원.
          표준 :  {"axis": "...", "text": "..."}
          변종 :  {"id": "T01", "description": "...", "category": "Roadmap", "severity": "..."}
                 {"type": "...", "message": "..."} 등
        """
        # axis 후보 키
        for ak in ("axis", "category", "type", "area", "dimension"):
            raw_ax = it.get(ak)
            if raw_ax:
                canon = _canonicalize_axis(raw_ax)
                if canon is None and isinstance(raw_ax, str):
                    canon = _CATEGORY_TO_AXIS.get(raw_ax.strip().lower())
                if canon:
                    break
        else:
            canon = None

        # text 후보 키
        text = ""
        for tk in ("text", "description", "message", "detail", "issue", "comment"):
            v = it.get(tk)
            if isinstance(v, str) and v.strip():
                text = v.strip()
                break

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

    # ── ACCEPT 이고 보고서가 비어있으면 "보고서 전용 LLM 콜" 로 채움 ──
    #    (강제 ACCEPT 또는 LLM 이 REVISE 를 낸 결과 ACCEPT 로 flip 된 케이스)
    decision_upper = (result.get("decision") or "ACCEPT").upper()
    if decision_upper == "ACCEPT":
        report = result.get("report") or {}
        if _report_fill_count(report) < 3:
            reason = "강제 ACCEPT" if result.get("_forced_accept") else "report 섹션 부족"
            print(f"[Orchestrator · Review] {reason} → 최종 보고서 전용 LLM 호출")
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
                print(f"[Orchestrator · Review] 최종 보고서 생성 완료 · {fill}/7 섹션")
            except Exception as e:
                print(f"[Orchestrator · Review] ⚠️  최종 보고서 생성 실패: {e}")

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
