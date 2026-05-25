"""
agents/aggregator.py
─────────────────────
Technology Analysis Agent 최종 통합 노드

역할:
1. patent_analysis + market_analysis 를 tech_id 기준으로 매핑
2. final_score 계산
   - USE_PATENT_MAP=false: patent_score × 0.45 + market_score × 0.55
   - USE_PATENT_MAP=true : patent_score × 0.35 + market_score × 0.45 + actor_map_score × 0.20
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
from config import (
    PATENT_WEIGHT,
    MARKET_WEIGHT,
    MIN_FINAL_SCORE,
    MAX_TECH_CANDIDATES,
    USE_PATENT_MAP,
)
from state import AnalysisState, TechCandidate

MAP_PATENT_WEIGHT = 0.35
MAP_MARKET_WEIGHT = 0.45
MAP_ACTOR_WEIGHT = 0.20


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


def _as_list(value) -> list:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _candidate_text(patent: dict, market: dict) -> str:
    parts = [
        patent.get("tech_id", ""),
        patent.get("name", ""),
        patent.get("category", ""),
        patent.get("rationale", ""),
        market.get("name", ""),
        market.get("rationale", ""),
    ]
    parts.extend(_as_list(patent.get("source_companies")))
    parts.extend(_as_list(patent.get("dependency_hints")))
    for evidence in _as_list(patent.get("evidence_patents")):
        if isinstance(evidence, dict):
            parts.extend(
                [
                    evidence.get("title", ""),
                    evidence.get("applicant", ""),
                    evidence.get("abstract", ""),
                ]
            )
    return " ".join(str(p) for p in parts if p).lower()


def _map_area_hits(shared_areas: list, candidate_text: str) -> list[str]:
    hits: list[str] = []
    for area in shared_areas:
        area_text = str(area or "").strip()
        if not area_text:
            continue
        tokens = [
            token.lower()
            for token in re.findall(r"[A-Za-z0-9가-힣]+", area_text)
            if len(token) >= 2
        ]
        if any(token in candidate_text for token in tokens):
            hits.append(area_text)
    return hits


def _actor_similarity_edges(patent_maps: dict) -> list[dict]:
    edges = (patent_maps or {}).get("actor_similarity_map") or []
    return [edge for edge in edges if isinstance(edge, dict)]


def _actor_map_summary(edges: list[dict]) -> dict:
    top_edges = sorted(
        edges,
        key=lambda e: _safe_float(e.get("similarity") or e.get("weight") or e.get("edge_weight")),
        reverse=True,
    )[:8]
    return {
        "edge_count": len(edges),
        "top_related_actors": [
            {
                "related_actor": edge.get("related_actor") or edge.get("target"),
                "similarity": edge.get("similarity") or edge.get("weight") or edge.get("edge_weight"),
                "shared_technology_areas": edge.get("shared_technology_areas", []),
            }
            for edge in top_edges
        ],
    }


def _compute_actor_map_score(patent: dict, market: dict, edges: list[dict]) -> tuple[float, list[dict]]:
    if not edges:
        return 0.0, []

    candidate_text = _candidate_text(patent, market)
    source_companies = {str(c).lower() for c in _as_list(patent.get("source_companies"))}
    scored_edges: list[dict] = []

    for edge in edges:
        related_actor = str(edge.get("related_actor") or edge.get("target") or "").strip()
        if not related_actor:
            continue

        raw_similarity = _safe_float(
            edge.get("similarity") or edge.get("weight") or edge.get("edge_weight")
        )
        similarity = raw_similarity * 100 if raw_similarity <= 1 else raw_similarity
        similarity = max(0.0, min(similarity, 100.0))

        related_actor_l = related_actor.lower()
        actor_match = related_actor_l in source_companies or related_actor_l in candidate_text
        shared_areas = _as_list(edge.get("shared_technology_areas"))
        area_hits = _map_area_hits(shared_areas, candidate_text)

        if not actor_match and not area_hits:
            continue

        multiplier = 0.70
        if actor_match:
            multiplier += 0.20
        if area_hits:
            multiplier += 0.15
        if str(edge.get("evidence_level", "")).lower() == "direct":
            multiplier += 0.05

        scored_edges.append(
            {
                "related_actor": related_actor,
                "similarity": round(similarity, 2),
                "matched_by_actor": actor_match,
                "matched_shared_technology_areas": area_hits[:5],
                "score_contribution": round(min(100.0, similarity * multiplier), 2),
            }
        )

    if not scored_edges:
        return 0.0, []

    scored_edges.sort(key=lambda item: item["score_contribution"], reverse=True)
    top_edges = scored_edges[:3]
    actor_map_score = round(
        sum(item["score_contribution"] for item in top_edges) / len(top_edges),
        2,
    )
    return actor_map_score, top_edges


def _calculate_final_score(
    patent_score: float,
    market_score: float,
    actor_map_score: float,
    use_actor_map_score: bool,
) -> tuple[float, dict]:
    if use_actor_map_score:
        if market_score > 0:
            score = (
                patent_score * MAP_PATENT_WEIGHT
                + market_score * MAP_MARKET_WEIGHT
                + actor_map_score * MAP_ACTOR_WEIGHT
            )
            weights = {
                "patent_score": MAP_PATENT_WEIGHT,
                "market_score": MAP_MARKET_WEIGHT,
                "actor_map_score": MAP_ACTOR_WEIGHT,
            }
        else:
            score = patent_score * 0.55 + actor_map_score * 0.25
            weights = {
                "patent_score": 0.55,
                "market_score": 0.0,
                "actor_map_score": 0.25,
                "market_missing_penalty": True,
            }
        return round(score, 2), weights

    if market_score > 0:
        score = patent_score * PATENT_WEIGHT + market_score * MARKET_WEIGHT
        weights = {
            "patent_score": PATENT_WEIGHT,
            "market_score": MARKET_WEIGHT,
            "actor_map_score": 0.0,
        }
    else:
        score = patent_score * 0.7
        weights = {
            "patent_score": 0.7,
            "market_score": 0.0,
            "actor_map_score": 0.0,
            "market_missing_penalty": True,
        }
    return round(score, 2), weights


def run_aggregator(state: AnalysisState) -> dict:
    """
    통합 스코어링 노드.
    patent_analysis + market_analysis → tech_candidates 생성
    """
    print("\n[Aggregator] 시작")
    messages = []

    patent_list = state.get("patent_analysis") or []
    market_list = state.get("market_analysis") or []
    patent_maps = state.get("patent_maps") or {}
    actor_edges = _actor_similarity_edges(patent_maps) if USE_PATENT_MAP else []
    use_actor_map_score = USE_PATENT_MAP and bool(actor_edges)

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
        actor_map_score, actor_map_context = _compute_actor_map_score(
            patent,
            market,
            actor_edges,
        )

        # ② final_score 계산
        final_score, scoring_weights = _calculate_final_score(
            patent_score,
            market_score,
            actor_map_score,
            use_actor_map_score,
        )

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
            "actor_map_score": actor_map_score if use_actor_map_score else 0.0,
            "final_score": final_score,
            "scoring_weights": scoring_weights,
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
        if use_actor_map_score:
            candidate["actor_map_context"] = actor_map_context
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
        "use_patent_map": USE_PATENT_MAP,
        "use_actor_map_score": use_actor_map_score,
        "actor_map_summary": _actor_map_summary(actor_edges) if USE_PATENT_MAP else {},
    }

    # 결과 요약 출력
    print(f"\n[Aggregator] ✅ 최종 후보 기술 {len(tech_candidates)}개 선정")
    print("-" * 60)
    for c in tech_candidates:
        print(
            f"  {c['tech_id']} | {c['name'][:35]:<35} | "
            f"final={c['final_score']:.1f} "
            f"(patent={c['patent_score']:.1f}, market={c['market_score']:.1f}, "
            f"map={c.get('actor_map_score', 0):.1f})"
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
