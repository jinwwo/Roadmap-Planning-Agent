"""
agents/aggregator.py
─────────────────────
Technology Analysis Agent 최종 통합 노드

역할:
1. patent_analysis + market_analysis 를 tech_id 기준으로 매핑
2. final_score 계산
   - USE_PATENT_MAP ON/OFF 모두 동일: patent_score × 0.45 + market_score × 0.55
   - actor_map_score 는 진단/설명용 메타데이터이며 점수 가산에 사용하지 않음
3. MIN_FINAL_SCORE 미만 필터링
4. LLM selector 또는 공통 fallback ranker로 최종 K개 선택
   - USE_PATENT_MAP=true이면 selector가 actor_similarity_map을 근거 context로 볼 수 있음
   - 단, actor_map_score는 진단용이며 numeric bonus로 쓰지 않음
5. Roadmap Planner Agent 입력 포맷으로 변환하여 반환
"""

import math
import json
import os
import re
from datetime import datetime
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from llm_factory import get_llm
from config import (
    PATENT_WEIGHT,
    MARKET_WEIGHT,
    MIN_FINAL_SCORE,
    MAX_TECH_CANDIDATES,
    USE_PATENT_MAP,
    TECH_CANDIDATE_TARGET_K,
    TECH_CANDIDATE_KEEP_RATIO,
    USE_LLM_CANDIDATE_SELECTOR,
)
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
) -> tuple[float, dict]:
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


def _target_candidate_count(pool_count: int) -> int:
    if pool_count <= 0:
        return 0
    if TECH_CANDIDATE_TARGET_K > 0:
        return max(1, min(pool_count, MAX_TECH_CANDIDATES, TECH_CANDIDATE_TARGET_K))
    if pool_count <= 3:
        return min(pool_count, MAX_TECH_CANDIDATES)
    ratio_k = int(math.ceil(pool_count * TECH_CANDIDATE_KEEP_RATIO))
    return max(3, min(pool_count - 1, MAX_TECH_CANDIDATES, ratio_k))


def _selection_score(candidate: dict) -> float:
    patent = _safe_float(candidate.get("patent_score"))
    market = _safe_float(candidate.get("market_score"))
    final = _safe_float(candidate.get("final_score"))

    return round(
        final * 0.55
        + patent * 0.25
        + market * 0.20,
        2,
    )


def _candidate_selection_metrics(candidates: list[dict]) -> dict:
    if not candidates:
        return {
            "avg_final_score": 0.0,
            "avg_market_score": 0.0,
            "avg_patent_score": 0.0,
            "avg_actor_map_score": 0.0,
            "map_supported_count": 0,
        }

    def avg(key: str) -> float:
        return round(sum(_safe_float(c.get(key)) for c in candidates) / len(candidates), 2)

    return {
        "avg_final_score": avg("final_score"),
        "avg_market_score": avg("market_score"),
        "avg_patent_score": avg("patent_score"),
        "avg_actor_map_score": avg("actor_map_score"),
        "map_supported_count": sum(1 for c in candidates if _safe_float(c.get("actor_map_score")) > 0),
    }


def _extract_json(text: str) -> dict:
    cleaned = re.sub(r"```(?:json)?\s*", "", str(text or "")).replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]+\}", cleaned)
        if match:
            return json.loads(match.group())
        raise ValueError(f"유효한 JSON을 파싱할 수 없습니다: {cleaned[:300]}")


_CANDIDATE_SELECTOR_SYSTEM = """You are a fair technology-candidate shortlist selector.
Your job is to choose a roadmap-ready shortlist from a broader candidate pool.

Selection criteria:
- Strategic fit with the company scenario and strategic direction
- Patent evidence strength
- Market evidence strength, timing, and data quality
- Portfolio diversity and roadmap usefulness
- Dependency readiness and TRL appropriateness

Fairness rules:
- Use the same criteria whether patent-map context is present or absent.
- If actor_similarity_map is provided, use it only as contextual evidence about ecosystem relevance, competitors, partners, and shared technology areas.
- Do NOT add a numeric bonus merely because a candidate has a high actor-map score.
- Do NOT select candidates solely to make ON/OFF results different.
- Select only from input tech_id values. Output ONLY valid JSON.
"""


def _compact_candidates_for_selector(candidates: list[dict]) -> list[dict]:
    compact = []
    for candidate in candidates:
        compact.append({
            "tech_id": candidate.get("tech_id"),
            "name": candidate.get("name"),
            "category": candidate.get("category"),
            "trl": candidate.get("trl"),
            "final_score": candidate.get("final_score"),
            "selection_score": candidate.get("selection_score"),
            "patent_score": candidate.get("patent_score"),
            "market_score": candidate.get("market_score"),
            "actor_map_score_diagnostic": candidate.get("actor_map_score", 0.0),
            "market_signals": candidate.get("market_signals", {}),
            "tam_sam_som": candidate.get("tam_sam_som", {}),
            "cagr_forecast": candidate.get("cagr_forecast", {}),
            "expected_market_boom_quarter": candidate.get("expected_market_boom_quarter"),
            "dependency_hints": candidate.get("dependency_hints", []),
            "key_market_reports": candidate.get("key_market_reports", [])[:2],
            "map_context_used": candidate.get("map_context_used", {}),
            "actor_map_context": candidate.get("actor_map_context", [])[:3],
            "rationale": candidate.get("rationale", "")[:900],
        })
    return compact


def _format_selector_context(
    *,
    candidates: list[dict],
    target_k: int,
    use_patent_map_context: bool,
    state: AnalysisState,
) -> str:
    map_context = {}
    if use_patent_map_context:
        edges = _actor_similarity_edges(state.get("patent_maps") or {})
        map_context = {
            "actor_similarity_map": sorted(
                edges,
                key=lambda e: _safe_float(e.get("similarity") or e.get("weight") or e.get("edge_weight")),
                reverse=True,
            )[:8],
            "usage": "contextual evidence only; no numeric bonus",
        }

    payload = {
        "domain": state.get("domain"),
        "reference_year": state.get("reference_year"),
        "company_name": state.get("company_name"),
        "company_scenario": state.get("company_scenario") or {},
        "strategic_direction": state.get("strategic_direction") or [],
        "target_k": target_k,
        "use_patent_map_context": use_patent_map_context,
        "fairness_controls": {
            "same_final_score_formula": True,
            "same_numeric_selection_formula_available_as_reference": True,
            "actor_map_score_is_diagnostic_only": True,
            "forced_map_bonus": False,
        },
        "candidate_pool": _compact_candidates_for_selector(candidates),
        "patent_map_context": map_context,
    }
    return f"""
아래 후보 pool에서 정확히 target_k={target_k}개를 선택하세요.
선택은 회사 전략, patent evidence, market evidence, roadmap 활용도 기준으로 설명해야 합니다.
patent_map_context가 제공된 경우에는 경쟁/협력 생태계 해석 근거로만 사용하고, actor_map_score 자체를 점수 보너스로 사용하지 마세요.

Output JSON schema:
{{
  "selected_tech_ids": ["T01", "..."],
  "dropped_candidates": [
    {{"tech_id": "T09", "reason": "왜 제외했는지 한국어로 간단히"}}
  ],
  "selection_rationale": "한국어 3-5문장",
  "evidence_used": {{
    "T01": ["patent", "market", "strategy", "actor_context"]
  }},
  "fairness_note": "No numeric patent-map bonus was applied."
}}

[Selection Input]
{json.dumps(payload, ensure_ascii=False, indent=2)}
"""


def _llm_select_final_candidates(
    *,
    candidates: list[dict],
    target_k: int,
    use_patent_map_context: bool,
    state: AnalysisState,
) -> tuple[list[str], dict] | tuple[None, dict]:
    if not USE_LLM_CANDIDATE_SELECTOR:
        return None, {"error": "USE_LLM_CANDIDATE_SELECTOR=false"}
    if target_k <= 0 or not candidates:
        return None, {"error": "empty candidate pool"}

    valid_ids = {c.get("tech_id") for c in candidates}
    prompt = _format_selector_context(
        candidates=candidates,
        target_k=target_k,
        use_patent_map_context=use_patent_map_context,
        state=state,
    )
    try:
        llm = get_llm(max_tokens=4096, temperature=0.1)
        response = llm.invoke([
            SystemMessage(content=_CANDIDATE_SELECTOR_SYSTEM),
            HumanMessage(content=prompt),
        ])
        raw = response.content if hasattr(response, "content") else str(response)
        parsed = _extract_json(raw)
        selected_ids = [
            str(tech_id)
            for tech_id in parsed.get("selected_tech_ids", [])
            if tech_id in valid_ids
        ]
        deduped = []
        for tech_id in selected_ids:
            if tech_id not in deduped:
                deduped.append(tech_id)
        if len(deduped) != target_k:
            raise ValueError(
                f"selector selected {len(deduped)} valid ids, expected {target_k}"
            )
        return deduped, {
            "selector_raw_response": raw,
            "selection_rationale": parsed.get("selection_rationale", ""),
            "evidence_used": parsed.get("evidence_used", {}),
            "fairness_note": parsed.get("fairness_note", ""),
            "dropped_candidates": parsed.get("dropped_candidates", []),
        }
    except Exception as e:
        return None, {
            "error": str(e),
            "selection_rationale": "LLM selector failed; deterministic fair fallback was used.",
        }


def _select_final_candidates(
    candidates: list[dict],
    use_patent_map_context: bool,
    state: AnalysisState,
) -> tuple[list[dict], dict]:
    pool_count = len(candidates)
    target_k = _target_candidate_count(pool_count)
    if pool_count <= target_k:
        selected = sorted(candidates, key=lambda x: x["final_score"], reverse=True)
        for c in selected:
            c["selection_score"] = _selection_score(c)
            c["selection_mode"] = "fair_map_context_on" if use_patent_map_context else "fair_map_context_off"
        return selected, {
            "use_patent_map": USE_PATENT_MAP,
            "use_actor_map_score": False,
            "selection_mode": "fair_map_context_on" if use_patent_map_context else "fair_map_context_off",
            "pool_count": pool_count,
            "target_k": target_k,
            "selected_count": len(selected),
            "selected_tech_ids": [c.get("tech_id") for c in selected],
            "dropped_candidates": [],
            "selection_metrics": _candidate_selection_metrics(selected),
            "fairness_controls": {
                "same_final_score_formula": True,
                "same_selection_score_formula": True,
                "actor_map_score_is_diagnostic_only": True,
                "map_anchor_slots": 0,
                "llm_selector_enabled": USE_LLM_CANDIDATE_SELECTOR,
            },
            "note": "pool_count <= target_k; no shortlist drop applied",
        }

    enriched = []
    for candidate in candidates:
        item = dict(candidate)
        item["selection_score"] = _selection_score(item)
        item["selection_mode"] = "fair_map_context_on" if use_patent_map_context else "fair_map_context_off"
        enriched.append(item)

    selector_ids, selector_meta = _llm_select_final_candidates(
        candidates=enriched,
        target_k=target_k,
        use_patent_map_context=use_patent_map_context,
        state=state,
    )
    if selector_ids:
        by_id = {candidate.get("tech_id"): candidate for candidate in enriched}
        selected = []
        for rank, tech_id in enumerate(selector_ids, 1):
            item = dict(by_id[tech_id])
            item["selection_rank"] = rank
            item["selection_mode"] = (
                "llm_fair_contextual_selector_map_on"
                if use_patent_map_context
                else "llm_fair_contextual_selector_map_off"
            )
            selected.append(item)

        dropped_reason_by_id = {
            item.get("tech_id"): item.get("reason", "LLM selector 제외")
            for item in selector_meta.get("dropped_candidates", [])
            if isinstance(item, dict)
        }
        dropped = [
            {
                "tech_id": c.get("tech_id"),
                "name": c.get("name"),
                "final_score": c.get("final_score"),
                "selection_score": c.get("selection_score"),
                "actor_map_score": c.get("actor_map_score", 0.0),
                "reason": dropped_reason_by_id.get(c.get("tech_id"), "LLM selector가 전략/시장/특허 근거상 우선순위 밖으로 판단"),
            }
            for c in enriched
            if c.get("tech_id") not in selector_ids
        ]
        return selected, {
            "use_patent_map": USE_PATENT_MAP,
            "use_actor_map_score": False,
            "selection_mode": (
                "llm_fair_contextual_selector_map_on"
                if use_patent_map_context
                else "llm_fair_contextual_selector_map_off"
            ),
            "pool_count": pool_count,
            "target_k": target_k,
            "selected_count": len(selected),
            "selected_tech_ids": [c.get("tech_id") for c in selected],
            "map_anchor_tech_ids": [],
            "dropped_candidates": dropped,
            "selection_metrics": _candidate_selection_metrics(selected),
            "selection_weights": {
                "final_score": 0.55,
                "patent_score": 0.25,
                "market_score": 0.20,
                "actor_map_score": 0.0,
            },
            "selector": {
                "method": "llm_fair_contextual_selector",
                "used": True,
                "selection_rationale": selector_meta.get("selection_rationale", ""),
                "evidence_used": selector_meta.get("evidence_used", {}),
                "fairness_note": selector_meta.get("fairness_note", ""),
            },
            "fairness_controls": {
                "same_final_score_formula": True,
                "same_selection_score_formula_available_as_fallback": True,
                "actor_map_score_is_diagnostic_only": True,
                "map_anchor_slots": 0,
                "llm_selector_uses_map_as_context_only": use_patent_map_context,
            },
        }

    ranked = sorted(
        enriched,
        key=lambda c: (
            _safe_float(c.get("selection_score")),
            _safe_float(c.get("final_score")),
            _safe_float(c.get("patent_score")),
        ),
        reverse=True,
    )
    selected_by_id: dict[str, dict] = {}
    for candidate in ranked:
        if len(selected_by_id) >= target_k:
            break
        selected_by_id.setdefault(candidate.get("tech_id"), candidate)

    selected = list(selected_by_id.values())
    selected.sort(
        key=lambda c: (
            _safe_float(c.get("selection_score")),
            _safe_float(c.get("final_score")),
        ),
        reverse=True,
    )
    dropped = [
        {
            "tech_id": c.get("tech_id"),
            "name": c.get("name"),
            "final_score": c.get("final_score"),
            "selection_score": c.get("selection_score"),
            "actor_map_score": c.get("actor_map_score", 0.0),
            "reason": "공통 selection_score 기준 하위",
        }
        for c in ranked
        if c.get("tech_id") not in selected_by_id
    ]

    return selected, {
        "use_patent_map": USE_PATENT_MAP,
        "use_actor_map_score": False,
        "selection_mode": "fair_map_context_on" if use_patent_map_context else "fair_map_context_off",
        "pool_count": pool_count,
        "target_k": target_k,
        "selected_count": len(selected),
        "selected_tech_ids": [c.get("tech_id") for c in selected],
        "map_anchor_tech_ids": [],
        "dropped_candidates": dropped,
        "selection_metrics": _candidate_selection_metrics(selected),
        "selection_weights": {
            "final_score": 0.55,
            "patent_score": 0.25,
            "market_score": 0.20,
            "actor_map_score": 0.0,
        },
        "fairness_controls": {
            "same_final_score_formula": True,
            "same_selection_score_formula": True,
            "actor_map_score_is_diagnostic_only": True,
            "map_anchor_slots": 0,
            "llm_selector_enabled": USE_LLM_CANDIDATE_SELECTOR,
        },
        "selector": {
            "method": "deterministic_fair_fallback",
            "used": False,
            "error": selector_meta.get("error") if isinstance(selector_meta, dict) else None,
            "selection_rationale": (
                selector_meta.get("selection_rationale")
                if isinstance(selector_meta, dict)
                else "Deterministic fair fallback was used."
            ),
        },
    }


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
    use_patent_map_context = USE_PATENT_MAP and bool(actor_edges)

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
            "actor_map_score": actor_map_score if use_patent_map_context else 0.0,
            "actor_map_score_usage": "diagnostic_only",
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
        if use_patent_map_context:
            candidate["actor_map_context"] = actor_map_context
        if patent.get("roadmapping_signals"):
            candidate["roadmapping_signals"] = patent.get("roadmapping_signals")
        if patent.get("patent_signals"):
            candidate["patent_signals"] = patent.get("patent_signals")
        tech_candidates.append(candidate)

    # ⑤ ON/OFF 별 최종 후보 선택
    candidate_pool = sorted(tech_candidates, key=lambda x: x["final_score"], reverse=True)
    tech_candidates, candidate_selection = _select_final_candidates(
        candidate_pool,
        use_patent_map_context,
        state,
    )

    # ⑥ market_context 구성 (Roadmap Planner 입력용)
    market_context = {
        "target_market": state["domain"],
        "expected_boom_quarter": _find_dominant_boom_quarter(market_list),
        "market_research_summary": _market_research_summary(market_list),
        "use_patent_map": USE_PATENT_MAP,
        "use_actor_map_score": False,
        "use_patent_map_context": use_patent_map_context,
        "actor_map_summary": _actor_map_summary(actor_edges) if USE_PATENT_MAP else {},
        "candidate_selection": candidate_selection,
    }

    # 결과 요약 출력
    print(f"\n[Aggregator] ✅ 최종 후보 기술 {len(tech_candidates)}개 선정")
    print("-" * 60)
    for c in tech_candidates:
        print(
            f"  {c['tech_id']} | {c['name'][:35]:<35} | "
            f"final={c['final_score']:.1f} "
            f"select={c.get('selection_score', 0):.1f} "
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
        "candidate_selection": candidate_selection,
        "patent_maps": state.get("patent_maps") or {},
        "messages": messages,
        "error": None,
    }
    _write_aggregator_outputs(state, output)
    return output
