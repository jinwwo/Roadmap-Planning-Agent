"""
demo_server.py — TRM Evaluation FastAPI 서버
═══════════════════════════════════════════════
JSON 업로드 → holdout 추출 → LLM judge → back test → 결과 반환

실행:
    cd evaluation
    python demo_server.py
    # → http://localhost:8000

API:
    POST /evaluate          단일 평가 (JSON 업로드)
    POST /evaluate/compare  Pairwise 비교 (2개 JSON)
    GET  /results           저장된 결과 목록
    GET  /results/{id}      결과 상세
    DELETE /results/{id}    결과 삭제
    GET  /config            현재 설정 (커넥터, LLM provider 등)
    POST /config            설정 변경
    GET  /                  웹 UI (demo_web.html)
"""

import json
import os

# .env 자동 로드
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # python-dotenv 없으면 수동 파싱
    env_path = os.path.join(os.path.dirname(__file__), '.env')
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    os.environ.setdefault(k.strip(), v.strip())
import uuid
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional, List

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from trm_evaluation import TRMEvaluationSuite
from adapters.roadmap_planning_agent import AgentOutputAdapter
from data_sources import (
    HoldoutExtractor, PatentDataSource, MarketDataSource,
    MockPatentConnector, MockMarketConnector, CSVMarketConnector,
    TavilyMarketConnector, USPTOConnector,
)

# ═══════════════════════════════════════
#  App
# ═══════════════════════════════════════

app = FastAPI(title="TRM Evaluation Suite", version="1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

adapter = AgentOutputAdapter()

# 결과 저장소 (메모리)
result_store: dict = {}  # {id: {name, result, created_at}}

# ═══════════════════════════════════════
#  Config (런타임 변경 가능)
# ═══════════════════════════════════════

config = {
    "connector": os.environ.get("EVAL_CONNECTOR", "mock"),        # mock, csv, uspto, csv+uspto, tavily+uspto
    "holdout_mode": os.environ.get("EVAL_HOLDOUT_MODE", "global"),  # global, per-tech
    "baseline_date": os.environ.get("EVAL_BASELINE", "2020-12-31"),
    "evaluation_date": os.environ.get("EVAL_EVALUATION", "2025-12-31"),
    "llm_providers": [],   # [], ["anthropic"], ["anthropic","openai","gemini"]
    "aggregation": "mean",
}


def _build_extractor():
    connector = config["connector"]
    # 특허
    if "uspto" in connector:
        ps = PatentDataSource(USPTOConnector())
    else:
        ps = PatentDataSource(MockPatentConnector({}))
    # 시장
    if "tavily" in connector:
        ms = MarketDataSource(TavilyMarketConnector())
    elif "csv" in connector:
        ms = MarketDataSource(CSVMarketConnector())
    else:
        ms = MarketDataSource(MockMarketConnector({}))
    return HoldoutExtractor(ps, ms, config["baseline_date"],
                            config["evaluation_date"], mode=config["holdout_mode"])


def _build_suite():
    providers = config["llm_providers"]
    if providers:
        return TRMEvaluationSuite(providers=providers, aggregation=config["aggregation"])
    return TRMEvaluationSuite()


def _detect_and_convert(data: dict) -> dict:
    """업로드된 JSON을 자동 감지하여 input_pack으로 변환"""

    # 이미 input_pack 포맷인 경우
    if "tech_candidates" in data and "planned_roadmap" in data and "investment_strategy" in data:
        if "orchestrator_report" in data:
            # evaluation_bundle
            return adapter.convert_bundle(data)
        return data

    raise ValueError(
        "인식할 수 없는 JSON 포맷. "
        "evaluation_bundle.json 또는 input_pack.json 형식이 필요합니다. "
        "필수 키: tech_candidates, planned_roadmap, investment_strategy"
    )


def _run_evaluation(input_pack: dict, use_api: bool = None) -> dict:
    """input_pack → holdout 추출 → 평가 → 결과"""

    # holdout 추출
    if "holdout_data" not in input_pack or not input_pack["holdout_data"]:
        try:
            extractor = _build_extractor()
            input_pack = extractor.extract_and_assemble(input_pack)
        except Exception as e:
            print(f"  ⚠ Holdout 추출 실패: {e}")

    # 평가
    suite = _build_suite()
    api = use_api if use_api is not None else bool(config["llm_providers"])
    result = suite.evaluate(input_pack, use_api=api)
    return result


# ═══════════════════════════════════════
#  Routes
# ═══════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    """웹 UI 서빙"""
    html_path = Path(__file__).parent / "demo_web.html"
    if not html_path.exists():
        return HTMLResponse("<h1>demo_web.html not found</h1>", status_code=404)
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/config")
async def get_config():
    """현재 설정 반환"""
    return {
        **config,
        "available_connectors": ["mock", "csv", "uspto", "csv+uspto", "tavily", "tavily+uspto"],
        "available_providers": ["anthropic", "openai", "gemini"],
        "env_keys": {
            "ANTHROPIC_API_KEY": bool(os.environ.get("ANTHROPIC_API_KEY")),
            "OPENAI_API_KEY": bool(os.environ.get("OPENAI_API_KEY")),
            "GOOGLE_API_KEY": bool(os.environ.get("GOOGLE_API_KEY")),
            "TAVILY_API_KEY": bool(os.environ.get("TAVILY_API_KEY")),
        }
    }


@app.post("/config")
async def update_config(new_config: dict):
    """설정 업데이트"""
    allowed = {"connector", "holdout_mode", "baseline_date", "evaluation_date",
               "llm_providers", "aggregation"}
    for k, v in new_config.items():
        if k in allowed:
            config[k] = v
    return {"status": "ok", "config": config}


@app.post("/evaluate")
async def evaluate_endpoint(file: UploadFile = File(...), name: Optional[str] = Form(None)):
    """
    JSON 파일 업로드 → 자동 평가 → 결과 반환 + 저장

    지원 포맷:
    - evaluation_bundle.json (orchestrator_report + 3 agent 출력)
    - input_pack.json (직접 조립)
    """
    try:
        content = await file.read()
        data = json.loads(content)
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"JSON 파싱 실패: {e}")

    try:
        input_pack = _detect_and_convert(data)
    except ValueError as e:
        raise HTTPException(400, str(e))

    try:
        result = _run_evaluation(input_pack)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(500, f"평가 실패: {e}")

    # 저장
    rid = str(uuid.uuid4())[:8]
    rname = name or file.filename.replace(".json", "") or rid
    result_store[rid] = {
        "id": rid,
        "name": rname,
        "result": result,
        "created_at": datetime.now().isoformat(),
    }

    return {
        "id": rid,
        "name": rname,
        "result": result,
    }


@app.post("/evaluate/compare")
async def compare_endpoint(
    file_a: UploadFile = File(...),
    file_b: UploadFile = File(...),
    name_a: Optional[str] = Form(None),
    name_b: Optional[str] = Form(None),
):
    """두 JSON 업로드 → 각각 평가 → Pairwise 비교"""
    try:
        data_a = json.loads(await file_a.read())
        data_b = json.loads(await file_b.read())
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"JSON 파싱 실패: {e}")

    try:
        pack_a = _detect_and_convert(data_a)
        pack_b = _detect_and_convert(data_b)
    except ValueError as e:
        raise HTTPException(400, str(e))

    # holdout 추출
    extractor = _build_extractor()
    try:
        if "holdout_data" not in pack_a or not pack_a["holdout_data"]:
            pack_a = extractor.extract_and_assemble(pack_a)
        if "holdout_data" not in pack_b or not pack_b["holdout_data"]:
            pack_b = extractor.extract_and_assemble(pack_b)
    except Exception:
        pass

    suite = _build_suite()
    api = bool(config["llm_providers"])

    try:
        comparison = suite.compare(pack_a, pack_b, use_api=api)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(500, f"비교 실패: {e}")

    # 저장
    for label, report_key, fname, uname in [
        ("A", "report_A", file_a.filename, name_a),
        ("B", "report_B", file_b.filename, name_b),
    ]:
        rid = str(uuid.uuid4())[:8]
        rname = uname or fname.replace(".json", "") or rid
        result_store[rid] = {
            "id": rid,
            "name": rname,
            "result": comparison[report_key],
            "created_at": datetime.now().isoformat(),
        }

    return {
        "report_A": comparison["report_A"],
        "report_B": comparison["report_B"],
        "pairwise": comparison["pairwise"],
        "name_A": name_a or file_a.filename,
        "name_B": name_b or file_b.filename,
    }


@app.get("/results")
async def list_results():
    """저장된 결과 목록"""
    items = []
    for rid, data in result_store.items():
        r = data["result"]
        items.append({
            "id": rid,
            "name": data["name"],
            "created_at": data["created_at"],
            "composite_score": r.get("composite", {}).get("final_composite_score", 0),
            "llm_score": r.get("llm_judge", {}).get("llm_structural_score", 0),
            "backtest_score": r.get("backtest", {}).get("backtest_score", 0),
            "domain": r.get("metadata", {}).get("domain", ""),
            "scenario": r.get("metadata", {}).get("scenario", ""),
        })
    items.sort(key=lambda x: x["created_at"], reverse=True)
    return {"results": items, "count": len(items)}


@app.get("/results/{rid}")
async def get_result(rid: str):
    """결과 상세"""
    if rid not in result_store:
        raise HTTPException(404, f"결과 없음: {rid}")
    return result_store[rid]


@app.delete("/results/{rid}")
async def delete_result(rid: str):
    """결과 삭제"""
    if rid not in result_store:
        raise HTTPException(404, f"결과 없음: {rid}")
    del result_store[rid]
    return {"status": "deleted", "id": rid}


# ═══════════════════════════════════════
#  Main
# ═══════════════════════════════════════

# ═══════════════════════════════════════
#  Startup: outputs/ 자동 스캔
# ═══════════════════════════════════════

import glob

def scan_outputs_on_startup():
    """서버 시작 시 outputs/ 폴더의 번들 JSON을 자동 평가"""
    scan_dirs = [
        os.path.join(os.path.dirname(__file__), "..", "orchestration_agent", "outputs"),
        os.path.join(os.path.dirname(__file__), "outputs"),
        os.path.join(os.path.dirname(__file__), "samples"),
    ]
    for scan_dir in scan_dirs:
        if not os.path.isdir(scan_dir):
            continue
        for fpath in sorted(glob.glob(os.path.join(scan_dir, "*.json"))):
            fname = os.path.basename(fpath)
            # 이미 평가 결과인 파일은 건너뛰기
            if "result" in fname or "sample_input" in fname:
                continue
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                pack = _detect_and_convert(data)
                result = _run_evaluation(pack)
                rid = str(uuid.uuid4())[:8]
                rname = fname.replace(".json", "")
                result_store[rid] = {
                    "id": rid,
                    "name": rname,
                    "result": result,
                    "created_at": datetime.now().isoformat(),
                }
                score = result.get("composite", {}).get("final_composite_score", 0)
                print(f"  ✓ {rname}: {score:.1f}점")
            except Exception as e:
                print(f"  ⚠ {fname}: {e}")

@app.on_event("startup")
async def startup():
    print("\n[Startup] outputs 폴더 스캔 중...")
    scan_outputs_on_startup()
    if result_store:
        print(f"[Startup] {len(result_store)}개 결과 로드 완료\n")
    else:
        print("[Startup] 자동 로드된 결과 없음\n")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("EVAL_PORT", 8000))
    print(f"""
╔══════════════════════════════════════════════════╗
║  TRM Evaluation Suite — Server                   ║
║  http://localhost:{port}                            ║
╚══════════════════════════════════════════════════╝

설정:
  connector:    {config['connector']}
  holdout_mode: {config['holdout_mode']}
  baseline:     {config['baseline_date']}
  evaluation:   {config['evaluation_date']}
  llm_providers:{config['llm_providers'] or '(rule-based)'}

환경변수로 변경:
  EVAL_CONNECTOR=csv+uspto
  EVAL_HOLDOUT_MODE=per-tech
  ANTHROPIC_API_KEY=sk-ant-...
  OPENAI_API_KEY=sk-...
  TAVILY_API_KEY=tvly-...
""")
    uvicorn.run(app, host="0.0.0.0", port=port)
