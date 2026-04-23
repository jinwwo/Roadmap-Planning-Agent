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
  "reference_year": <integer, the baseline year; default to 2025 if unclear>,
  "category_hints": ["Equipment","Material","Process","Architecture","Packaging"],
  "industry": "<optional short industry label>",
  "objective": "<optional one-sentence objective>"
}
Pick a reasonable subset of category_hints for the domain. If user gave none, include all five.
industry / objective may be omitted if not inferable."""


def _extract_json(text: str) -> dict:
    cleaned = re.sub(r"```(?:json)?\s*", "", text).replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]+\}", cleaned)
        if m:
            return json.loads(m.group())
        raise ValueError(f"Intake JSON parse failed: {text[:200]}")


def extract_intake(user_request: str) -> dict:
    """사용자 자연어를 domain / reference_year / category_hints 등으로 파싱"""
    llm = get_llm(max_tokens=512)
    resp = llm.invoke([
        SystemMessage(content=_INTAKE_SYSTEM),
        HumanMessage(content=user_request),
    ])
    data = _extract_json(resp.content if hasattr(resp, "content") else str(resp))
    data.setdefault("reference_year", 2025)
    data.setdefault("category_hints",
                    ["Equipment", "Material", "Process", "Architecture", "Packaging"])
    data["domain"] = data.get("domain") or user_request.strip()
    try:
        data["reference_year"] = int(data["reference_year"])
    except Exception:
        data["reference_year"] = 2025
    return data


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

    # 파생 필드 (intake 이후 채워짐)
    domain: str = ""
    reference_year: int = 2025
    category_hints: List[str] = field(default_factory=list)
    industry: Optional[str] = None
    objective: Optional[str] = None

    # control
    _thread: Optional[threading.Thread] = None
    done: bool = False

    # ── 파이프라인 실행 ───────────────────────────────────
    def start(self, user_request: str,
              active_agents: Optional[List[str]] = None,
              total_budget: Optional[float] = None,
              stage_mode: str = "phase") -> None:
        self.user_request = user_request
        if active_agents:
            self.active_agents = [a for a in active_agents if a in ("1", "2", "3")] or ["1", "2", "3"]
        if total_budget is not None:
            self.total_budget = float(total_budget)
        self.stage_mode = stage_mode or "phase"
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

            bus.emit("intake_ready",
                     domain=self.domain,
                     reference_year=self.reference_year,
                     category_hints=self.category_hints,
                     industry=self.industry,
                     objective=self.objective,
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
                            total_budget=self.total_budget,
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
