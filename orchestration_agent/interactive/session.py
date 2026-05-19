"""
interactive/session.py
───────────────────────
4-에이전트 오케스트레이션 웹 데모 세션.

한 세션은:
  1. 자연어 입력 → domain / reference_year / category_hints 추출 (LLM)
  2. pipeline.run_orchestration(...) 실행
     - Orchestrator Setup → Agent 1 → Agent 2 → Agent 3 → Review
     - REVISE 시 선택적 재실행 루프 (최대 MAX_ORCHESTRATOR_ITERATIONS)
  3. pipeline 의 이벤트 훅(events_to) 으로 구조화 이벤트를 bus 에 전달
  4. pipeline 의 stdout 훅(stream_subprocess_stdout) 으로 sibling 에이전트 로그 캡처
  5. pipeline 자체의 print() 는 capture_stdout_to 로 캡처

tech_analysis_agent 의 HITL 과 달리 중간 체크포인트는 없고 (Orchestrator 가 ACCEPT/
REVISE 를 자체 판단), 대신 **active_agents 토글** 이 세션 시작 시 주어집니다.
"""

from __future__ import annotations

import json
import os
import re
import threading
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage

from config import OUTPUTS_DIR, FILE_ORCHESTRATOR_REPORT
from interactive.event_bus import EventBus, capture_stdout_to
from llm_factory import get_llm, describe_llm
from pipeline import run_orchestration, stream_subprocess_stdout, events_to
from state import ProblemFrame


# ── 세션 저장소 (단일 프로세스, 메모리 기반) ──────────────────
_SESSIONS: dict[str, "Session"] = {}
_LOCK = threading.Lock()


def get_session(sid: str) -> Optional["Session"]:
    with _LOCK:
        return _SESSIONS.get(sid)


def register_session(s: "Session") -> None:
    with _LOCK:
        _SESSIONS[s.id] = s


def drop_session(sid: str) -> None:
    with _LOCK:
        _SESSIONS.pop(sid, None)


# ── 자연어 → 파이프라인 입력 추출 ─────────────────────────────

_INTAKE_SYSTEM = """You extract structured parameters from a user's request about building a technology roadmap.
Return ONLY valid JSON (no prose, no markdown fences) matching this schema:
{
  "domain": "<concise technology domain in user's language>",
  "reference_year": <integer — the TARGET/END year of the roadmap, NOT the start year>,
  "category_hints": ["Equipment","Material","Process","Architecture","Packaging"],
  "industry": "<optional short industry label>",
  "objective": "<optional one-sentence objective>"
}

IMPORTANT — reference_year extraction rules:
- "2030년까지" / "by 2030" / "until 2030" → reference_year = 2030 (the END year)
- "향후 5년" / "next 5 years" → reference_year = current_year + 5
- "2030년 시장 진입" / "by 2030 market entry" → reference_year = 2030
- If unclear, default to (current_year + 5)
- reference_year MUST be in the FUTURE (greater than current year). Never extract a past year.

Pick a reasonable subset of category_hints for the domain. If user gave none, include all five.
industry / objective may be omitted if not inferable."""


def _extract_json(text: str) -> dict:
    # Qwen3 시리즈 등 thinking 모드 모델의 <think>...</think> 블록 자동 제거.
    text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"```(?:json)?\s*", "", text).replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]+\}", cleaned)
        if m:
            return json.loads(m.group())
        raise ValueError(f"Intake JSON parse failed: {text[:200]}")


def _extract_year_from_text(text: str, current_year: int) -> Optional[int]:
    """사용자 원문에서 4자리 미래 연도를 직접 발췌 (LLM 없이).

    예: '2030년까지', 'by 2030', 'until 2030', '2028 진입', '2030년 시장 개화'
    → 2030 / 2028 / 2030

    여러 연도가 있으면 가장 큰 미래 연도 (= 마감 연도) 우선.
    """
    if not isinstance(text, str):
        return None
    matches = re.findall(r"(?<!\d)(20\d{2}|21\d{2})(?!\d)", text)
    future_years = [int(y) for y in matches if int(y) > current_year]
    if future_years:
        return max(future_years)
    return None


def extract_intake(user_request: str) -> dict:
    """사용자 자연어를 domain / reference_year / category_hints 등으로 파싱.
    LLM 응답이 빈 채로 와도 사용자 원문 기반 fallback 으로 동작 보장.

    reference_year 우선순위:
      ① 사용자 원문에서 regex 로 발췌한 미래 연도 (가장 신뢰성 높음, LLM 무관)
      ② LLM 이 추출한 reference_year
      ③ default = current_year + 5
    """
    from datetime import datetime
    current_year = datetime.now().year
    default_ref_year = current_year + 5  # 기본 5년 horizon

    # ① 원문 regex 추출 — LLM 호출 전에 먼저 시도
    text_year = _extract_year_from_text(user_request, current_year)

    fallback = {
        "domain": user_request.strip(),
        "reference_year": text_year or default_ref_year,
        "category_hints": ["Equipment", "Material", "Process", "Architecture", "Packaging"],
    }

    try:
        llm = get_llm(max_tokens=512)
        resp = llm.invoke([
            SystemMessage(content=_INTAKE_SYSTEM),
            HumanMessage(content=user_request),
        ])
        raw = resp.content if hasattr(resp, "content") else str(resp)
        if not raw or not raw.strip():
            print(f"[Session] ⚠️ Intake LLM 빈 응답 → 사용자 입력 fallback 사용")
            return fallback
        data = _extract_json(raw)
    except Exception as e:
        print(f"[Session] ⚠️ Intake 파싱 실패 ({e}) → fallback 사용. raw 샘플: {(raw[:200] if 'raw' in dir() else '(no resp)')!r}")
        return fallback

    data.setdefault("category_hints", fallback["category_hints"])
    data["domain"] = data.get("domain") or user_request.strip()

    # reference_year — 원문 regex 결과를 LLM 결과보다 우선
    try:
        ry_llm = int(data.get("reference_year", default_ref_year))
    except Exception:
        ry_llm = default_ref_year

    if text_year and text_year > current_year:
        if ry_llm != text_year:
            print(
                f"[Session] reference_year 보정: LLM={ry_llm} → 원문 regex={text_year} "
                f"(사용자 원문 우선)"
            )
        ry = text_year
    else:
        ry = ry_llm

    if ry <= current_year:
        print(f"[Session] ⚠️ reference_year={ry} 가 현재({current_year}) 이하 → {default_ref_year} 로 보정")
        ry = default_ref_year
    data["reference_year"] = ry
    return data


# ── Investment Policy 자연어 → 4 필드 추출 ───────────────────

_POLICY_SYSTEM = """You extract structured Investment Policy parameters from a user's natural-language description.
Return ONLY valid JSON (no prose, no markdown fences) with this schema:
{
  "risk_appetite": "low" | "medium" | "high",
  "investment_horizon": "short" | "balanced" | "long",
  "total_budget": <integer USD, e.g. 5000000000 for $5B>,
  "strategic_priority": ["<keyword 1>", "<keyword 2>", ...]
}

Rules:
- risk_appetite: "low"=conservative/위험회피, "medium"=balanced/균형, "high"=aggressive/공격적
- investment_horizon: "short"=1-2yr/단기, "balanced"=mixed/균형, "long"=5yr+/장기
- total_budget: parse numeric USD. "$5B"=5000000000, "$1B"=1000000000, "10억"=1000000000 (한국어 단위 주의: "억"=10^8, "조"=10^12. 단 일반적으론 USD 표기로 가정).
  If only currency hint ($, B, M) found, convert. If unclear, default 5000000000.
- strategic_priority: extract 2-5 short keywords (영문 권장), e.g. ["First-mover advantage", "Cost leadership"].
  If user said "균형/balanced" → ["Short-term commercialization", "Enabling technology", "Long-term exploratory bets"]
  If user said "공격적/aggressive" → ["First-mover advantage", "Market expansion", "Aggressive R&D"]
  If user said "보수적/conservative" → ["Risk minimization", "Proven technology", "Cost leadership"]

Defaults if a field cannot be inferred at all:
- risk_appetite: "medium"
- investment_horizon: "balanced"
- total_budget: 5000000000
- strategic_priority: ["Short-term commercialization", "Enabling technology", "Long-term exploratory bets"]"""


_ALLOWED_RISK = {"low", "medium", "high"}
_ALLOWED_HORIZON = {"short", "balanced", "long"}


def extract_investment_policy(policy_text: str) -> dict:
    """
    자연어 투자 정책 → {risk_appetite, investment_horizon, total_budget, strategic_priority}.
    LLM 추출 실패 시 안전한 기본값 반환.
    """
    fallback = {
        "risk_appetite": "medium",
        "investment_horizon": "balanced",
        "total_budget": 5_000_000_000.0,
        "strategic_priority": [
            "Short-term commercialization readiness",
            "Enabling-technology foundation (materials / equipment)",
            "Balanced long-term exploratory bets",
        ],
    }

    if not policy_text or not policy_text.strip():
        return fallback

    try:
        llm = get_llm(max_tokens=512)
        resp = llm.invoke([
            SystemMessage(content=_POLICY_SYSTEM),
            HumanMessage(content=policy_text),
        ])
        data = _extract_json(resp.content if hasattr(resp, "content") else str(resp))
    except Exception as e:
        print(f"[Session] ⚠️ investment_policy_text 파싱 실패: {e} → 기본값 사용")
        return fallback

    # 필드 검증 + 정규화
    risk = (data.get("risk_appetite") or "medium").strip().lower()
    if risk not in _ALLOWED_RISK:
        risk = "medium"

    horizon = (data.get("investment_horizon") or "balanced").strip().lower()
    if horizon not in _ALLOWED_HORIZON:
        horizon = "balanced"

    try:
        budget = float(data.get("total_budget", fallback["total_budget"]))
        if budget <= 0:
            budget = fallback["total_budget"]
    except Exception:
        budget = fallback["total_budget"]

    priorities = data.get("strategic_priority", []) or []
    if isinstance(priorities, list):
        priorities = [str(p).strip() for p in priorities if str(p).strip()]
    else:
        priorities = []
    if not priorities:
        priorities = fallback["strategic_priority"]

    return {
        "risk_appetite": risk,
        "investment_horizon": horizon,
        "total_budget": budget,
        "strategic_priority": priorities,
    }


# ── Session ──────────────────────────────────────────────────

@dataclass
class Session:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    bus: EventBus = field(default_factory=EventBus)

    # pipeline I/O
    user_request: str = ""
    active_agents: List[str] = field(default_factory=lambda: ["1", "2", "3"])
    total_budget: Optional[float] = None
    stage_mode: str = "phase"
    # Investment policy (Agent 3 가 사용)
    risk_appetite: Optional[str] = None
    investment_horizon: Optional[str] = None
    strategic_priority: Optional[List[str]] = None

    # 파생 필드 (intake 이후 채워짐)
    domain: str = ""
    reference_year: int = 2025
    category_hints: List[str] = field(default_factory=list)
    industry: Optional[str] = None
    objective: Optional[str] = None
    time_horizon: Optional[str] = None   # 산술 도출: "{현재}-{reference_year}"

    # control
    _thread: Optional[threading.Thread] = None
    done: bool = False

    # ── 파이프라인 실행 ───────────────────────────────────
    def start(self, user_request: str,
              active_agents: Optional[List[str]] = None,
              total_budget: Optional[float] = None,
              stage_mode: str = "phase",
              risk_appetite: Optional[str] = None,
              investment_horizon: Optional[str] = None,
              strategic_priority: Optional[List[str]] = None,
              investment_policy_text: Optional[str] = None) -> None:
        self.user_request = user_request
        if active_agents:
            self.active_agents = [a for a in active_agents if a in ("1", "2", "3")] or ["1", "2", "3"]
        self.stage_mode = stage_mode or "phase"

        # Investment Policy: 자연어 텍스트 우선, 그 다음 구조화 입력 fallback
        if investment_policy_text and investment_policy_text.strip():
            policy = extract_investment_policy(investment_policy_text)
            self.risk_appetite = policy["risk_appetite"]
            self.investment_horizon = policy["investment_horizon"]
            self.total_budget = policy["total_budget"]
            self.strategic_priority = policy["strategic_priority"]
        else:
            # 하위 호환: 구조화 입력 그대로 사용
            if total_budget is not None:
                self.total_budget = float(total_budget)
            if risk_appetite in ("low", "medium", "high"):
                self.risk_appetite = risk_appetite
            if investment_horizon in ("short", "balanced", "long"):
                self.investment_horizon = investment_horizon
            if strategic_priority:
                self.strategic_priority = [p.strip() for p in strategic_priority if str(p).strip()]

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _bridge_event(self, type_: str, payload: dict) -> None:
        """pipeline._EVENT_HOOK → bus.emit 브릿지"""
        # step_start/step_end 변환 (UI 가 기존 패턴 그대로 사용)
        if type_ == "setup_start":
            self.bus.emit("step_start", step="setup", label="Orchestrator · Problem Setup")
        elif type_ == "setup_done":
            self.bus.emit("step_end", step="setup")
            self.bus.emit("setup_done", **payload)
        elif type_ == "agent_start":
            agent = payload.get("agent", "?")
            label = payload.get("label", f"Agent {agent}")
            step_id = f"agent{agent}_iter{len([x for x in self._iter_log if x == f'agent{agent}']) + 1}"
            self._iter_log.append(f"agent{agent}")
            self._agent_step_map[agent] = step_id
            active = payload.get("active", True)
            self.bus.emit("step_start", step=step_id, label=label + ("" if active else " (OFF)"))
        elif type_ == "agent_end":
            agent = payload.get("agent", "?")
            step_id = self._agent_step_map.get(agent, f"agent{agent}")
            self.bus.emit("step_end", step=step_id, count=payload.get("count", 0))
        elif type_ == "review_start":
            step_id = f"review_iter{payload.get('iteration', 1)}"
            self._review_step = step_id
            self.bus.emit("step_start", step=step_id,
                          label=f"Orchestrator · Review (iter {payload.get('iteration', 1)})")
        elif type_ == "review_done":
            step_id = self._review_step or "review"
            review = payload.get("review") or {}
            self.bus.emit("step_end", step=step_id, count=None)
            self.bus.emit("review_done", review=review, iteration=payload.get("iteration"))
        elif type_ == "refine":
            self.bus.emit("refine",
                          rerun_agents=payload.get("rerun_agents", []),
                          feedback=payload.get("feedback", []),
                          iteration=payload.get("iteration"))
        elif type_ == "candidates_ready":
            self.bus.emit("candidates_ready", **payload)
        elif type_ == "roadmap_ready":
            self.bus.emit("roadmap_ready", **payload)
        elif type_ == "strategy_ready":
            self.bus.emit("strategy_ready", **payload)
        # pipeline_start / pipeline_done 은 session 이 직접 처리

    def _run(self) -> None:
        bus = self.bus
        # agent 별 현재 step_id (재실행 시 새 id 가 됨)
        self._agent_step_map: dict = {}
        self._iter_log: list = []
        self._review_step: Optional[str] = None

        try:
            bus.emit("session_started", session_id=self.id, llm=describe_llm(),
                     active_agents=self.active_agents)

            # ── Step 0: Intake ─────────────────────────────
            bus.emit("step_start", step="intake", label="요청 분석")
            bus.log(f"사용자 요청: {self.user_request}", source="session")
            intake = extract_intake(self.user_request)
            self.domain = intake["domain"]
            self.reference_year = intake["reference_year"]
            self.category_hints = intake["category_hints"]
            self.industry = intake.get("industry")
            self.objective = intake.get("objective")
            # time_horizon 자동 도출 — 현재 연도부터 reference_year 까지
            # (reference_year 는 extract_intake 에서 이미 sanity check 완료)
            from datetime import datetime
            current_year = datetime.now().year
            if self.reference_year > current_year:
                self.time_horizon = f"{current_year}-{self.reference_year}"
            else:
                # 안전망: 만약 reference_year 가 여전히 과거면 5년 horizon 으로
                self.time_horizon = f"{current_year}-{current_year + 5}"
                bus.log(f"⚠️ reference_year={self.reference_year} 비정상 → time_horizon={self.time_horizon} 로 보정", source="session")

            bus.emit("intake_ready",
                     domain=self.domain,
                     reference_year=self.reference_year,
                     category_hints=self.category_hints,
                     industry=self.industry,
                     objective=self.objective,
                     time_horizon=self.time_horizon,
                     # Investment Policy (자연어에서 LLM 추출됐거나 폼/기본값)
                     risk_appetite=self.risk_appetite,
                     investment_horizon=self.investment_horizon,
                     total_budget=self.total_budget,
                     strategic_priority=self.strategic_priority,
                     active_agents=self.active_agents)
            bus.emit("step_end", step="intake")

            # ── Run full orchestration ────────────────────
            # pipeline 의 stdout 과 이벤트를 모두 bus 로 흘림
            def _on_line(line: str) -> None:
                bus.log(line, source="agent")

            # out_prefix 는 세션 id 의 앞부분 사용 — outputs/<sid>_*.json 저장
            out_prefix = f"web_{self.id}_"

            with events_to(self._bridge_event):
                with stream_subprocess_stdout(_on_line):
                    with capture_stdout_to(bus):
                        result = run_orchestration(
                            domain=self.domain,
                            reference_year=self.reference_year,
                            category_hints=self.category_hints,
                            active_agents=self.active_agents,
                            industry=self.industry,
                            objective=self.objective,
                            time_horizon=self.time_horizon,
                            total_budget=self.total_budget,
                            priorities=self.strategic_priority,
                            risk_appetite=self.risk_appetite,
                            investment_horizon=self.investment_horizon,
                            out_prefix=out_prefix,
                            stage_mode=self.stage_mode,
                        )

            # Orchestrator 최종 보고서 저장 (outputs/web_<sid>_orchestrator_report.json)
            report_path = os.path.join(OUTPUTS_DIR, f"{out_prefix}{FILE_ORCHESTRATOR_REPORT}")
            try:
                with open(report_path, "w", encoding="utf-8") as f:
                    json.dump({
                        "problem_frame": result.get("problem_frame"),
                        "active_agents": result.get("active_agents"),
                        "iteration": result.get("iteration"),
                        "review": result.get("review"),
                        "review_history": result.get("review_history") or [],
                        "artifact_paths": result.get("paths"),
                    }, f, ensure_ascii=False, indent=2)
                bus.log(f"[Session] 보고서 저장: {report_path}", source="session")
            except Exception as e:
                bus.log(f"[Session] ⚠️ 보고서 저장 실패: {e}", source="session")

            bus.emit("final", result=_slim_result(result))
            bus.emit("done", ok=True)

        except Exception as e:
            tb = traceback.format_exc()
            bus.emit("error", message=str(e), traceback=tb)
            bus.emit("done", ok=False)
        finally:
            self.done = True


def _slim_result(result: dict) -> dict:
    """SSE 페이로드로 전달하기 위한 경량화 (대용량 필드 제외 / 최종 리포트만 포함)"""
    return {
        "problem_frame": result.get("problem_frame"),
        "active_agents": result.get("active_agents"),
        "iteration": result.get("iteration"),
        "review": result.get("review"),
        "artifact_paths": result.get("paths", {}),
        "counts": {
            "tech_candidates": len(result.get("tech_candidates") or []),
            "planned_roadmap": len(result.get("planned_roadmap") or []),
            "stages": len(result.get("stages") or []),
            "investment_strategy": len(result.get("investment_strategy") or []),
        },
    }
