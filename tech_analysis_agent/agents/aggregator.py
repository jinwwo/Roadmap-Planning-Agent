"""
agents/aggregator.py
─────────────────────
Technology Analysis Agent 최종 통합 노드

역할:
1. patent_analysis + market_analysis 를 tech_id 기준으로 매핑
2. final_score = patent_score × 0.45 + market_score × 0.55 계산
3. MIN_FINAL_SCORE 미만 필터링
4. final_score 내림차순 정렬 후 MAX_TECH_CANDIDATES 개 선택
5. Roadmap Planner Agent 입력 포맷으로 변환하여 반환
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path

from langchain_core.messages import AIMessage
from config import PATENT_WEIGHT, MARKET_WEIGHT, MIN_FINAL_SCORE, MAX_TECH_CANDIDATES
from state import AnalysisState, TechCandidate


def _find_dominant_boom_quarter(market_analysis: list) -> str:
    """시장 분석 결과에서 가장 빈번한 시장 개화 분기를 도출"""
    if not market_analysis:
        return "2028 Q1"
    quarters = [m.get("expected_market_boom_quarter", "") for m in market_analysis]
    quarters = [q for q in quarters if q]
    if not quarters:
        return "2028 Q1"
    # 최빈값 반환
    return max(set(quarters), key=quarters.count)


def _run_id() -> str:
    return os.getenv("AGGREGATOR_RUN_ID") or os.getenv("AGENT_RUN_ID") or datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())
    return cleaned.strip("_") or "aggregator"


def _aggregator_log_dir(run_id: str) -> Path:
    configured = os.getenv("AGGREGATOR_LOG_DIR")
    if configured:
        return Path(configured)
    default_root = Path(__file__).resolve().parents[2] / "orchestration_agent" / "outputs" / "aggregator"
    return default_root / run_id


def _write_aggregator_outputs(state: AnalysisState, output: dict) -> None:
    try:
        run_id = _run_id()
        output_dir = _aggregator_log_dir(run_id)
        output_dir.mkdir(parents=True, exist_ok=True)
        prefix = _safe_filename(state.get("company_name") or state.get("domain") or "aggregator")
        serializable_output = {k: v for k, v in output.items() if k != "messages"}
        serializable_output["messages"] = [
            getattr(msg, "content", str(msg))
            for msg in output.get("messages", [])
        ]
        payload = {
            "run_id": run_id,
            "timestamp_utc": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "input": {
                "patent_analysis": state.get("patent_analysis") or [],
                "market_analysis": state.get("market_analysis") or [],
                "patent_maps": state.get("patent_maps") or {},
            },
            "output": serializable_output,
        }
        log_path = output_dir / f"{prefix}_aggregator_output.json"
        log_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        (output_dir / "latest_aggregator_output.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[Aggregator] output 저장: {log_path}")
    except Exception as e:
        print(f"[Aggregator] ⚠️ output 저장 실패: {e}")


def _market_research_summary(market_list: list) -> dict:
    by_tech = {}
    for item in market_list or []:
        tech_id = item.get("tech_id")
        if not tech_id:
            continue
        by_tech[tech_id] = {
            "name": item.get("name"),
            "market_score": item.get("market_score"),
            "expected_market_boom_quarter": item.get("expected_market_boom_quarter"),
            "tam_sam_som": item.get("tam_sam_som"),
            "cagr_forecast": item.get("cagr_forecast"),
            "competitive_landscape": item.get("competitive_landscape"),
            "key_market_reports": item.get("key_market_reports", []),
            "map_context_used": item.get("map_context_used", {}),
        }
    return by_tech


def run_aggregator(state: AnalysisState) -> dict:
    """
    통합 스코어링 노드.
    patent_analysis + market_analysis → tech_candidates 생성
    """
    print("\n[Aggregator] 시작")
    messages = []

    patent_list = state.get("patent_analysis") or []
    market_list = state.get("market_analysis") or []

    if not patent_list:
        msg = "Aggregator: patent_analysis 가 비어있어 중단합니다."
        print(f"[Aggregator] ❌ {msg}")
        return {
            "tech_candidates": [],
            "market_context": {},
            "messages": [AIMessage(content=msg)],
            "error": msg,
        }

    # ① market_analysis를 tech_id 기준 딕셔너리로 변환
    market_map: dict = {m["tech_id"]: m for m in market_list}

    tech_candidates: list[TechCandidate] = []

    for patent in patent_list:
        tech_id = patent.get("tech_id", "")
        market = market_map.get(tech_id, {})

        patent_score = float(patent.get("patent_score", 0))
        market_score = float(market.get("market_score", 0)) if market else 0.0

        # ② final_score 계산
        if market_score > 0:
            final_score = round(
                patent_score * PATENT_WEIGHT + market_score * MARKET_WEIGHT, 2
            )
        else:
            # 시장 데이터 없을 시 특허 점수만 사용 (패널티)
            final_score = round(patent_score * 0.7, 2)

        # ③ 임계값 필터링
        if final_score < MIN_FINAL_SCORE:
            print(f"  [Aggregator] {tech_id} 필터링 (final_score={final_score} < {MIN_FINAL_SCORE})")
            continue

        # ④ TechCandidate 생성
        candidate: TechCandidate = {
            "tech_id": tech_id,
            "name": patent.get("name", ""),
            "category": patent.get("category", ""),
            "trl": patent.get("estimated_trl", 1),
            "patent_score": patent_score,
            "market_score": market_score,
            "final_score": final_score,
            "expected_market_boom_quarter": market.get(
                "expected_market_boom_quarter", "2028 Q1"
            ),
            "dependency_hints": patent.get("dependency_hints", []),
            "rationale": (
                f"[Patent] {patent.get('rationale', '')} "
                f"[Market] {market.get('rationale', '')}"
            ).strip(),
        }
        if market:
            candidate["market_signals"] = market.get("market_signals", {})
            candidate["tam_sam_som"] = market.get("tam_sam_som", {})
            candidate["cagr_forecast"] = market.get("cagr_forecast", {})
            candidate["key_market_reports"] = market.get("key_market_reports", [])
            candidate["map_context_used"] = market.get("map_context_used", {})
        if patent.get("roadmapping_signals"):
            candidate["roadmapping_signals"] = patent.get("roadmapping_signals")
        if patent.get("patent_signals"):
            candidate["patent_signals"] = patent.get("patent_signals")
        tech_candidates.append(candidate)

    # ⑤ 정렬 + 상위 N개 선택
    tech_candidates.sort(key=lambda x: x["final_score"], reverse=True)
    tech_candidates = tech_candidates[:MAX_TECH_CANDIDATES]

    # ⑥ market_context 구성 (Roadmap Planner 입력용)
    market_context = {
        "target_market": state["domain"],
        "expected_boom_quarter": _find_dominant_boom_quarter(market_list),
        "market_research_summary": _market_research_summary(market_list),
    }

    # 결과 요약 출력
    print(f"\n[Aggregator] ✅ 최종 후보 기술 {len(tech_candidates)}개 선정")
    print("-" * 60)
    for c in tech_candidates:
        print(
            f"  {c['tech_id']} | {c['name'][:35]:<35} | "
            f"final={c['final_score']:.1f} "
            f"(patent={c['patent_score']:.1f}, market={c['market_score']:.1f})"
        )
    print("-" * 60)

    messages.append(
        AIMessage(
            content=(
                f"Aggregator: 최종 {len(tech_candidates)}개 기술 선정 완료. "
                f"시장 개화 예상 시점: {market_context['expected_boom_quarter']}"
            )
        )
    )

    output = {
        "tech_candidates": tech_candidates,
        "market_context": market_context,
        "patent_maps": state.get("patent_maps") or {},
        "messages": messages,
        "error": None,
    }
    _write_aggregator_outputs(state, output)
    return output
