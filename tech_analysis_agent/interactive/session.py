"""
interactive/session.py
───────────────────────
사용자 대화 세션을 관리합니다.

한 세션은:
  1. 자연어 입력 → domain / reference_year / category_hints 추출 (LLM)
  2. AnalysisGraph 실행 (stdout → EventBus)
  3. HITL 체크포인트: 후보 기술 제시 → 사용자 drop/shift 응답 대기
  4. RoadmapGraph 실행 (orchestrator_feedback 적용)
  5. 최종 결과 방출

LangGraph interrupt 대신 간단한 threading.Event 로 HITL 을 구현합니다.
"""

from __future__ import annotations

import json
import re
import threading
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from langchain_core.messages import HumanMessage, SystemMessage

from interactive.event_bus import EventBus, capture_stdout_to
from llm_factory import get_llm, describe_llm
from graphs.analysis_graph import create_analysis_graph
from graphs.roadmap_graph import create_roadmap_graph
from state import AnalysisState, RoadmapState


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
  "category_hints": ["Equipment","Material","Process","Architecture","Packaging"]
}
Pick a reasonable subset of category_hints for the domain. If user gave none, include all five."""


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
    """사용자 자연어를 domain / reference_year / category_hints 로 파싱"""
    llm = get_llm(max_tokens=512)
    resp = llm.invoke([
        SystemMessage(content=_INTAKE_SYSTEM),
        HumanMessage(content=user_request),
    ])
    data = _extract_json(resp.content if hasattr(resp, "content") else str(resp))
    # 방어
    data.setdefault("reference_year", 2025)
    data.setdefault("category_hints", ["Equipment", "Material", "Process", "Architecture", "Packaging"])
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
    domain: str = ""
    reference_year: int = 2025
    category_hints: list = field(default_factory=list)

    tech_candidates: list = field(default_factory=list)
    market_context: dict = field(default_factory=dict)
    planned_roadmap: list = field(default_factory=list)
    dependency_tree: dict = field(default_factory=dict)

    # HITL
    _hitl_event: threading.Event = field(default_factory=threading.Event)
    orchestrator_feedback: Optional[dict] = None

    # control
    _thread: Optional[threading.Thread] = None
    done: bool = False

    # ── HITL API ──────────────────────────────────────────
    def submit_feedback(self, feedback: Optional[dict]) -> None:
        """클라이언트가 HITL 응답을 보낼 때 호출."""
        self.orchestrator_feedback = feedback
        self._hitl_event.set()

    # ── 파이프라인 실행 ───────────────────────────────────
    def start(self, user_request: str) -> None:
        self.user_request = user_request
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        bus = self.bus
        try:
            bus.emit("session_started", session_id=self.id, llm=describe_llm())

            # ── Step 0: Intake ─────────────────────────────
            bus.emit("step_start", step="intake", label="요청 분석")
            bus.log(f"사용자 요청: {self.user_request}", source="session")
            intake = extract_intake(self.user_request)
            self.domain = intake["domain"]
            self.reference_year = intake["reference_year"]
            self.category_hints = intake["category_hints"]
            bus.emit("intake_ready",
                     domain=self.domain,
                     reference_year=self.reference_year,
                     category_hints=self.category_hints)
            bus.emit("step_end", step="intake")

            # ── Step 1: Technology Analysis (Agent 1) ─────
            bus.emit("step_start", step="analysis", label="Agent 1 · 기술 분석")
            analysis_graph = create_analysis_graph()
            sub_state: AnalysisState = {
                "domain": self.domain,
                "reference_year": self.reference_year,
                "category_hints": self.category_hints,
                "company_name": None,
                "company_profile": self.user_request,
                "related_companies": None,
                "patent_raw_data": None,
                "market_raw_data": None,
                "patent_analysis": None,
                "patent_maps": None,
                "patent_prompt": None,
                "market_analysis": None,
                "tech_candidates": None,
                "market_context": None,
                "candidate_selection": None,
                "messages": [],
                "error": None,
                "retry_count": 0,
            }
            with capture_stdout_to(bus):
                analysis_result = analysis_graph.invoke(sub_state)

            self.tech_candidates = analysis_result.get("tech_candidates") or []
            self.market_context = analysis_result.get("market_context") or {}

            bus.emit("candidates_ready",
                     candidates=self.tech_candidates,
                     market_context=self.market_context)
            bus.emit("step_end", step="analysis",
                     count=len(self.tech_candidates))

            if not self.tech_candidates:
                bus.emit("error", message="후보 기술을 얻지 못했습니다. 파이프라인 중단.")
                bus.emit("done", ok=False)
                return

            # ── Step 2: HITL ───────────────────────────────
            bus.emit("hitl_request",
                     prompt="후보 기술 리스트를 검토하세요. drop/shift 를 적용하거나 그대로 진행할 수 있습니다.",
                     candidates=self.tech_candidates)
            # 최대 10분 대기 — 시간 내 응답 없으면 빈 피드백으로 진행
            got = self._hitl_event.wait(timeout=600)
            if not got:
                bus.log("HITL 응답 타임아웃 — 피드백 없이 진행합니다.", source="session")
                self.orchestrator_feedback = None

            # ── Step 3: Roadmap Planner (Agent 2) ─────────
            bus.emit("step_start", step="roadmap", label="Agent 2 · 로드맵 설계",
                     feedback=self.orchestrator_feedback)
            roadmap_graph = create_roadmap_graph()
            rm_state: RoadmapState = {
                "tech_candidates": self.tech_candidates,
                "market_context": self.market_context,
                "dependency_tree": None,
                "timeline_draft": None,
                "planned_roadmap": None,
                "orchestrator_feedback": self.orchestrator_feedback,
                "messages": [],
                "error": None,
                "iteration": 0,
            }
            with capture_stdout_to(bus):
                roadmap_result = roadmap_graph.invoke(rm_state)

            self.planned_roadmap = roadmap_result.get("planned_roadmap") or []
            self.dependency_tree = roadmap_result.get("dependency_tree") or {}

            bus.emit("roadmap_ready",
                     planned_roadmap=self.planned_roadmap,
                     dependency_tree=self.dependency_tree)
            bus.emit("step_end", step="roadmap",
                     count=len(self.planned_roadmap))

            bus.emit("done", ok=True)
        except Exception as e:
            tb = traceback.format_exc()
            bus.emit("error", message=str(e), traceback=tb)
            bus.emit("done", ok=False)
        finally:
            self.done = True
            # 닫지 않고 유지 — 클라이언트가 연결 유지하며 done 확인 후 자체 종료
