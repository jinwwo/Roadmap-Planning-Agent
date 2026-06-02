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

# API 키가 있으면 자동으로 provider 추가
if os.environ.get("ANTHROPIC_API_KEY"):
    config["llm_providers"].append("anthropic")
if os.environ.get("OPENAI_API_KEY"):
    config["llm_providers"].append("openai")
if os.environ.get("GOOGLE_API_KEY"):
    config["llm_providers"].append("gemini")


def _build_extractor(company: str = "", eval_year: int = 2030):
    connector = config["connector"]
    # 특허
    if "kipris" in connector:
        from data_sources import KiprisConnector
        ps = PatentDataSource(KiprisConnector())
    elif "uspto" in connector:
        ps = PatentDataSource(USPTOConnector())
    else:
        ps = PatentDataSource(MockPatentConnector({}))





    # 시장
    if "agent" in connector:
        from data_sources import AgentMarketConnector
        ms = MarketDataSource(AgentMarketConnector(company, eval_year))
    elif "tavily" in connector:
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
    # 1) 이미 input_pack 또는 번들 포맷
    if "tech_candidates" in data and "planned_roadmap" in data and "investment_strategy" in data:
        if "orchestrator_report" in data or "active_agents" in data:
            return adapter.convert_bundle(data)
        return data
    # 2) 새 스키마: orchestrator_report.json 자체에 모든 데이터 포함
    if "problem_frame" in data and "tech_candidates" in data:
        return adapter.convert_bundle({"orchestrator_report": data})
    # 3) review_history가 있는 report
    if "review" in data and ("artifact_paths" in data or "review_history" in data):
        return adapter.convert_bundle({"orchestrator_report": data})
    raise ValueError("인식 불가 JSON. evaluation_bundle / input_pack / orchestrator_report 필요.")


def _run_evaluation(input_pack: dict, use_api: bool = None) -> dict:
    """input_pack → holdout 추출 → 평가 → 결과"""

    # holdout 추출
    if "holdout_data" not in input_pack or not input_pack["holdout_data"]:
        try:
            _md = input_pack.get("metadata") or {}
            _comp = _md.get("company_name", "")
            _yr = int(_md.get("reference_year", 2030) or 2030)
            extractor = _build_extractor(_comp, _yr)
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
    _md = pack_a.get("metadata") or {}
    extractor = _build_extractor(_md.get("company_name", ""), int(_md.get("reference_year", 2030) or 2030))
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
    """서버 시작 시 outputs/ 폴더의 JSON을 자동 묶어서 평가. 캐시 있으면 재사용."""
    import re
    import hashlib

    cache_dir = os.path.join(os.path.dirname(__file__), "outputs")
    os.makedirs(cache_dir, exist_ok=True)

    base_output_dir = os.path.join(os.path.dirname(__file__), "..", "orchestration_agent", "outputs")
    if not os.path.isdir(base_output_dir):
        print(f"  ⚠ outputs 폴더 없음: {base_output_dir}")
        return

    def _file_hash(fpath):
        with open(fpath, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()[:12]

    def _load_cache(cache_path):
        if os.path.exists(cache_path):
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return None

    def _save_cache(cache_path, result_data):
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(result_data, f, ensure_ascii=False, indent=2)

    def _scan_leaf_dir(leaf_dir, display_name):
        """leaf 디렉토리에서 3개 JSON을 묶어서 평가"""
        tech_file = os.path.join(leaf_dir, "tech_candidates.json")
        roadmap_file = os.path.join(leaf_dir, "planned_roadmap.json")
        invest_file = os.path.join(leaf_dir, "investment_strategy.json")
        report_file = os.path.join(leaf_dir, "orchestrator_report.json")

        if not (os.path.exists(tech_file) and os.path.exists(roadmap_file) and os.path.exists(invest_file)):
            return

        # 이미 로드된 이름이면 스킵
        if any(r["name"] == display_name for r in result_store.values()):
            return

        # 캐시 확인
        combined_hash = hashlib.md5(
            (_file_hash(tech_file) + _file_hash(roadmap_file) + _file_hash(invest_file)).encode()
        ).hexdigest()[:12]
        cache_path = os.path.join(cache_dir, f"cache_{display_name}_{combined_hash}.json")
        cached = _load_cache(cache_path)

        if cached:
            rid = str(uuid.uuid4())[:8]
            result_store[rid] = {"id": rid, "name": display_name, "result": cached,
                                 "created_at": datetime.now().isoformat()}
            score = cached.get("composite", {}).get("final_composite_score", 0)
            print(f"    ✓ {display_name}: {score:.1f}점 (캐시)")
            return

        try:
            with open(tech_file, "r", encoding="utf-8") as f:
                tech_data = json.load(f)
            with open(roadmap_file, "r", encoding="utf-8") as f:
                roadmap_data = json.load(f)
            with open(invest_file, "r", encoding="utf-8") as f:
                invest_data = json.load(f)
            report_data = None
            if os.path.exists(report_file):
                with open(report_file, "r", encoding="utf-8") as f:
                    report_data = json.load(f)

            bundle = {
                "orchestrator_report": report_data,
                "tech_candidates": tech_data.get("tech_candidates", []),
                "planned_roadmap": roadmap_data.get("planned_roadmap", []),
                "investment_strategy": invest_data.get("investment_strategy", []),
                "stages": invest_data.get("stages", []),
                "market_context": tech_data.get("market_context", {}),
                "active_agents": report_data.get("active_agents", ["1", "2", "3"]) if report_data else ["1", "2", "3"],
            }
            pack = _detect_and_convert(bundle)
            dn = display_name
            md = pack.setdefault("metadata", {})
            if "시장이익최대" in dn:
                md["strategy_type"] = "시장이익최대"
            elif "기술선도" in dn:
                md["strategy_type"] = "기술선도"
            result = _run_evaluation(pack)
            rid = str(uuid.uuid4())[:8]
            result_store[rid] = {"id": rid, "name": display_name, "result": result,
                                 "created_at": datetime.now().isoformat()}
            _save_cache(cache_path, result)
            score = result.get("composite", {}).get("final_composite_score", 0)
            print(f"    ✓ {display_name}: {score:.1f}점 (신규→캐시 저장)")
            import time; time.sleep(2)  # API rate limit 방지

        except Exception as e:
            print(f"    ⚠ {display_name}: {e}")

    # ═══════════════════════════════════════
    # 1) 특허맵 ON: outputs/{industry}/{company}/{strategy}/
    # 2) 특허맵 OFF: outputs/특허맵_Off/{industry}/{company}/{strategy}/
    # ═══════════════════════════════════════

    print(f"  스캔: {base_output_dir}")

    for entry in sorted(os.listdir(base_output_dir)):
        entry_path = os.path.join(base_output_dir, entry)
        if not os.path.isdir(entry_path):
            continue

        if entry == "특허맵_Off":
            # 특허맵 OFF 구조: 특허맵_Off/{industry}/{company}/{strategy}/
            for industry in sorted(os.listdir(entry_path)):
                ind_path = os.path.join(entry_path, industry)
                if not os.path.isdir(ind_path):
                    continue
                for company in sorted(os.listdir(ind_path)):
                    comp_path = os.path.join(ind_path, company)
                    if not os.path.isdir(comp_path):
                        continue
                    for strategy in sorted(os.listdir(comp_path)):
                        strat_path = os.path.join(comp_path, strategy)
                        if not os.path.isdir(strat_path):
                            continue
                        display_name = f"특허맵_off_{industry}_{company}_{strategy}"
                        _scan_leaf_dir(strat_path, display_name)
        elif entry == "특허맵_On":
            # 특허맵 ON 구조: 특허맵_On/{industry}/{company}/{strategy}/
            for industry in sorted(os.listdir(entry_path)):
                ind_path = os.path.join(entry_path, industry)
                if not os.path.isdir(ind_path):
                    continue
                for company in sorted(os.listdir(ind_path)):
                    comp_path = os.path.join(ind_path, company)
                    if not os.path.isdir(comp_path):
                        continue
                    for strategy in sorted(os.listdir(comp_path)):
                        strat_path = os.path.join(comp_path, strategy)
                        if not os.path.isdir(strat_path):
                            continue
                        display_name = f"특허맵_on_{industry}_{company}_{strategy}"
                        _scan_leaf_dir(strat_path, display_name)
        else:
            # past, past_0601 등 기타 디렉토리는 스캔 안 함
            continue


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
