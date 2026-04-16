"""
server.py
──────────
FastAPI + Server-Sent Events 백엔드.

엔드포인트:
  GET  /                  → 데모 웹 페이지
  POST /api/session       → 새 세션 생성 + 파이프라인 시작
  GET  /api/stream/{sid}  → SSE 스트림 (progress + HITL 요청)
  POST /api/feedback/{sid}→ HITL 응답 (drop / shift)
  GET  /api/status        → LLM provider 정보

실행:
  python server.py
  또는: uvicorn server:app --reload --port 8000
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config import validate_config
from llm_factory import describe_llm
from interactive.session import Session, register_session, get_session, drop_session


ROOT = Path(__file__).parent
WEB_DIR = ROOT / "web"

app = FastAPI(title="Technology Roadmap Agent — Interactive Demo")


# ── 정적 자원 ────────────────────────────────────────────────

@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


# ── API 스키마 ───────────────────────────────────────────────

class StartRequest(BaseModel):
    request: str  # 사용자 자연어 요청


class FeedbackRequest(BaseModel):
    # None → 그대로 진행,  {"drop":[...], "shift":[{"tech_id":..., "new_start_q":...}]}
    feedback: dict | None = None


# ── 엔드포인트 ───────────────────────────────────────────────

@app.get("/api/status")
def status():
    return {"llm": describe_llm()}


@app.post("/api/session")
def start_session(req: StartRequest):
    try:
        validate_config()
    except EnvironmentError as e:
        raise HTTPException(400, str(e))

    s = Session()
    register_session(s)
    s.start(req.request)
    return {"session_id": s.id, "llm": describe_llm()}


@app.post("/api/feedback/{sid}")
def submit_feedback(sid: str, req: FeedbackRequest):
    s = get_session(sid)
    if not s:
        raise HTTPException(404, "session not found")
    s.submit_feedback(req.feedback)
    return {"ok": True}


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
            # done 후 약간의 여유를 두고 정리
            pass

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",   # nginx buffering 방지
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
