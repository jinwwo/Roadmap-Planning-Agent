"""
server.py
──────────
FastAPI + Server-Sent Events 백엔드 (Orchestration Agent 웹 데모).

엔드포인트:
  GET  /                  → 데모 웹 페이지
  POST /api/session       → 새 세션 생성 + 파이프라인 시작
                             body: {
                               "request": "<user 자연어>",
                               "active_agents": ["1","2","3"],          # 선택 (기본 전부 ON)
                               "total_budget": 5_000_000_000,           # 선택 (USD 숫자)
                               "stage_mode": "phase" | "horizon",       # 선택
                               # Investment Policy (Agent 3 입력)
                               "risk_appetite": "low|medium|high",      # 선택 (기본 medium)
                               "investment_horizon": "short|balanced|long",  # 선택 (기본 balanced)
                               "strategic_priority": ["...", "..."]     # 선택 (기본 problem_frame)
                             }
  GET  /api/stream/{sid}  → SSE 스트림 (진행 상황 + 결과 이벤트)
  GET  /api/status        → LLM provider 정보
  DELETE /api/session/{sid} → 세션 정리

실행:
  python server.py                              # port 8000
  또는 uvicorn server:app --reload --port 8000
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config import validate_config
from llm_factory import describe_llm
from interactive.session import Session, register_session, get_session, drop_session
from scenario_loader import list_scenarios


ROOT = Path(__file__).parent
WEB_DIR = ROOT / "web"

app = FastAPI(title="Technology Roadmap Orchestration — Interactive Demo")


# ── 정적 자원 ────────────────────────────────────────────────

@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/favicon.ico")
def favicon():
    """브라우저의 자동 favicon 요청에 빈 응답 (404 로그 방지)"""
    from fastapi.responses import Response
    return Response(status_code=204)   # No Content


app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


# ── API 스키마 ───────────────────────────────────────────────

class StartRequest(BaseModel):
    request: str                                  # 사용자 자연어 요청
    active_agents: Optional[List[str]] = None     # 예: ["1","2","3"] · 미지정 시 전부 ON
    run_mode: Optional[str] = "multi"             # "multi" | "single"
    stage_mode: Optional[str] = "phase"           # "phase" | "horizon"
    scenario_id: Optional[str] = None             # predefined scenario id
    use_patent_map: Optional[bool] = None         # actor similarity map A/B toggle
    # Investment policy — 자연어 한 줄 (LLM 이 4 필드로 추출)
    investment_policy_text: Optional[str] = None
    # (하위 호환) 구조화 입력도 지원 — text 가 없으면 이걸 사용
    total_budget: Optional[float] = None
    risk_appetite: Optional[str] = None
    investment_horizon: Optional[str] = None
    strategic_priority: Optional[List[str]] = None


# ── 엔드포인트 ───────────────────────────────────────────────

@app.get("/api/status")
def status():
    return {"llm": describe_llm()}


@app.get("/api/scenarios")
def scenarios():
    return {"scenarios": list_scenarios()}


@app.post("/api/session")
def start_session(req: StartRequest):
    try:
        validate_config()
    except EnvironmentError as e:
        raise HTTPException(400, str(e))

    s = Session()
    register_session(s)
    s.start(
        user_request=req.request,
        active_agents=req.active_agents,
        run_mode=req.run_mode or "multi",
        total_budget=req.total_budget,
        stage_mode=req.stage_mode or "phase",
        scenario_id=req.scenario_id,
        use_patent_map=req.use_patent_map,
        risk_appetite=req.risk_appetite,
        investment_horizon=req.investment_horizon,
        strategic_priority=req.strategic_priority,
        investment_policy_text=req.investment_policy_text,
    )
    return {
        "session_id": s.id,
        "llm": describe_llm(),
        "active_agents": s.active_agents,
        "run_mode": s.run_mode,
    }


def _sse_format(event_type: str, data: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.get("/api/stream/{sid}")
def stream(sid: str):
    s = get_session(sid)
    if not s:
        raise HTTPException(404, "session not found")

    def gen():
        try:
            for ev in s.bus.iter_events(timeout=15):
                yield _sse_format(ev.type, ev.to_dict())
                if ev.type == "done":
                    break
        finally:
            pass

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",      # nginx buffering 방지
        "Connection": "keep-alive",
    }
    return StreamingResponse(gen(), media_type="text/event-stream", headers=headers)


@app.delete("/api/session/{sid}")
def end_session(sid: str):
    s = get_session(sid)
    if s:
        s.bus.close()
        drop_session(sid)
    return {"ok": True}


# ── 엔트리 ───────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False)
