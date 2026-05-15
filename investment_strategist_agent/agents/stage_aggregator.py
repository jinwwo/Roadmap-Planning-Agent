"""
agents/stage_aggregator.py
───────────────────────────
Roadmap Planner Agent 의 기술 단위 출력 (planned_roadmap) 을
Investment Strategist 의 판단 단위인 **단계(stage)** 로 집계합니다.

집계 규칙 (우선순위 순):
  1) phase_name 이 명시되어 있으면 그대로 사용 (예: "1단계: 기반 R&D")
  2) phase_name 이 없거나 동일하게 처리해야 할 때는 start_q 기준으로
     short-term / mid-term / long-term 으로 자동 파티셔닝

LLM 호출은 하지 않는 pure Python 모듈.
"""

from typing import List, Dict
from collections import defaultdict

from config import SHORT_TERM_MAX_QUARTERS, MID_TERM_MAX_QUARTERS
from state import StageSummary


# ── 분기 산술 유틸 (roadmap_planner 와 동일한 표기를 기대) ───

def _quarter_to_int(q: str) -> int:
    """'2028 Q1' → 정수. 파싱 실패 시 None"""
    try:
        year_str, q_str = q.strip().split()
        year = int(year_str)
        q_num = int(q_str[1])
        return year * 4 + q_num
    except Exception:
        return None


def _int_to_quarter(n: int) -> str:
    year = n // 4
    q_num = n % 4
    if q_num == 0:
        q_num = 4
        year -= 1
    return f"{year} Q{q_num}"


# ── Stage 자동 분류 (phase_name 폴백용) ───────────────────────

def _classify_by_start_q(start_q: str, reference_q_int: int) -> str:
    """
    start_q 와 reference (현재 시점) 와의 분기 차이로 단계 분류.
      - 8분기 이내  → short-term
      - 16분기 이내 → mid-term
      - 그 이후     → long-term
    """
    sq_int = _quarter_to_int(start_q)
    if sq_int is None or reference_q_int is None:
        return "mid-term"
    delta = sq_int - reference_q_int
    if delta <= SHORT_TERM_MAX_QUARTERS:
        return "short-term"
    if delta <= MID_TERM_MAX_QUARTERS:
        return "mid-term"
    return "long-term"


# ── 공개 API ─────────────────────────────────────────────────

def aggregate_stages(
    planned_roadmap: List[dict],
    market_context: dict = None,
    use_phase_name: bool = True,
) -> List[StageSummary]:
    """
    Roadmap Planner 의 출력을 stage 단위로 집계.

    Parameters
    ----------
    planned_roadmap : Roadmap Planner 의 planned_roadmap 필드
    market_context  : 미사용 (API 일관성 목적)
    use_phase_name  : True 면 phase_name 이 있을 때 그것을 stage 라벨로 사용.
                      False 면 무조건 start_q 기반으로 short/mid/long 분류.

    Returns
    -------
    list[StageSummary]  — 각 stage 의 period / goal / technologies 포함
    """
    if not planned_roadmap:
        return []

    items = [r for r in planned_roadmap if (r.get("phase_name") or "") != "DROPPED"]
    if not items:
        return []

    # 모든 기술을 단일 stage 로 묶음 — 인위적 분류 제거. Strategist 가 per-tech 단위로 판단.
    starts = [m.get("year_idx_start") or 1 for m in items]
    ends = [m.get("year_idx_target") or 1 for m in items]
    period = f"{min(starts)}차년도 - {max(ends)}차년도"
    return [{
        "stage": "roadmap",
        "period": period,
        "goal": "전체 기술 로드맵",
        "technologies": [m.get("name", "") for m in items],
        "tech_ids": [m.get("tech_id", "") for m in items],
        "num_items": len(items),
    }]
