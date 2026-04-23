"""
agents/timeline_calculator.py
───────────────────────────────
Roadmap Planner Agent — Step 2: 역산(Backcasting) 타임라인 계산

역할:
1. dependency_tree + market_boom_quarter 를 기반으로
   TRL 기반 리드 타임 계산 후 역산 적용 (시장 개화 분기부터 거슬러 올라감)
2. Zero-slack 로직: 선행 기술의 완료 분기 < 후행 기술의 시작 분기
3. 오케스트레이터 피드백 수신 시 Shift / Drop 명령을 반영하여 재계산
4. RoadmapState 에 timeline_draft 저장

이 단계는 LLM 을 사용하지 않는 **pure Python 역산 알고리즘** 입니다.
(LLM 은 Step 1 의존성 분석과 Step 3 justification 작성에서만 사용)
"""

from langchain_core.messages import AIMessage

from state import RoadmapState
from config import TRL_LEAD_TIME_QUARTERS


# ── 분기 산술 유틸 ────────────────────────────────────────────

def quarter_to_int(q: str) -> int:
    """
    "2028 Q1"  →  정수 (year * 4 + q_num)

    비교/산술 연산에 사용. 파싱 실패 시 기본값 2028 Q1 으로 폴백.
    """
    try:
        year_str, q_str = q.strip().split()
        year = int(year_str)
        q_num = int(q_str[1])  # "Q3" → 3
        return year * 4 + q_num
    except Exception:
        return 2028 * 4 + 1


def int_to_quarter(n: int) -> str:
    """정수 → "YYYY QX" 포맷"""
    year = n // 4
    q_num = n % 4
    if q_num == 0:
        q_num = 4
        year -= 1
    return f"{year} Q{q_num}"


def subtract_quarters(q: str, delta: int) -> str:
    """분기 문자열에서 delta 분기를 뺌"""
    return int_to_quarter(quarter_to_int(q) - delta)


def add_quarters(q: str, delta: int) -> str:
    """분기 문자열에 delta 분기를 더함"""
    return int_to_quarter(quarter_to_int(q) + delta)


# ── TRL 기반 리드 타임 ────────────────────────────────────────

def get_lead_time(trl: int) -> int:
    """
    현재 TRL → 상용화까지 필요한 분기 수

    TRL 1–3 : 7분기 (약 2년, 원천 R&D)
    TRL 4–6 : 4분기 (약 1년, 프로토타이핑)
    TRL 7–8 : 2분기 (약 6개월, 최적화 / 스케일업)
    TRL 9   : 1분기 (이미 양산 가능)

    config.TRL_LEAD_TIME_QUARTERS 의 (min, max) 중앙값을 사용.
    """
    lo, hi = TRL_LEAD_TIME_QUARTERS.get(trl, (4, 6))
    return (lo + hi) // 2


def get_phase_name(trl: int, layer: int) -> str:
    """TRL + 레이어 기반으로 개발 단계명 결정 (Roadmap Builder 에서 override 가능)"""
    if trl <= 3:
        return "Phase 1: Fundamental R&D"
    elif trl <= 6:
        if layer == 0:
            return "Phase 2: Technology Development"
        else:
            return "Phase 2: Prototyping & Integration"
    else:
        return "Phase 3: Commercialization & Scale-up"


# ── 역산 알고리즘 ─────────────────────────────────────────────

def _topological_sort(tree: dict) -> list:
    """
    의존성 트리에 대해 Kahn's Algorithm 으로 위상 정렬.
    선행 기술이 항상 후행 기술보다 앞에 오도록 정렬.
    """
    in_degree = {tid: 0 for tid in tree}
    for tid, node in tree.items():
        for prereq in node.get("prerequisites", []):
            if prereq in tree:
                in_degree[tid] = in_degree.get(tid, 0) + 1

    # 레이어 낮은 순, id 사전순으로 큐 초기화
    queue = sorted(
        [tid for tid, deg in in_degree.items() if deg == 0],
        key=lambda t: (tree[t].get("layer", 1), t),
    )

    sorted_ids = []
    while queue:
        tid = queue.pop(0)
        sorted_ids.append(tid)
        for dep_id in tree[tid].get("dependents", []):
            if dep_id not in tree:
                continue
            in_degree[dep_id] -= 1
            if in_degree[dep_id] == 0:
                queue.append(dep_id)
                queue.sort(key=lambda t: (tree[t].get("layer", 1), t))

    # 순환 의존성 발생 시 나머지 추가 (정상 케이스에선 발생 안 함)
    remaining = [tid for tid in tree if tid not in sorted_ids]
    sorted_ids.extend(remaining)

    return sorted_ids


def backcast_timeline(
    dependency_tree: dict,
    market_boom_q: str,
    orchestrator_feedback: dict = None,
) -> tuple:
    """
    시장 개화 분기를 기준으로 역산하여 각 기술의 시작/완료 분기를 결정.

    Parameters
    ----------
    dependency_tree       : {tech_id: DependencyNode}
    market_boom_q         : 목표 시장 개화 분기 ("YYYY QX")
    orchestrator_feedback : {
                              "shift": [{"tech_id": "T02", "new_start_q": "2026 Q1"}],
                              "drop":  ["T04"]
                            }

    Returns
    -------
    (sorted_ids, timeline_draft)
      sorted_ids    : 위상 정렬된 tech_id 리스트
      timeline_draft: list[TimelineItem]
    """
    feedback = orchestrator_feedback or {}
    drop_ids = set(feedback.get("drop", []) or [])
    shift_map = {s["tech_id"]: s["new_start_q"] for s in (feedback.get("shift", []) or [])}

    boom_int = quarter_to_int(market_boom_q)

    # ① Drop 처리: 제외 기술 제거
    active_tree = {
        tid: node for tid, node in dependency_tree.items()
        if tid not in drop_ids
    }

    # ② 위상 정렬 (레이어 → 선행 기술 완료 순)
    sorted_ids = _topological_sort(active_tree)

    # ③ 각 기술의 완료 분기를 저장할 딕셔너리
    completion = {}   # {tech_id: target_q_int}
    timeline = {}     # {tech_id: {start_q, target_q, ...}}

    for tid in sorted_ids:
        node = active_tree[tid]
        trl = node.get("trl", 3)
        lead_time = get_lead_time(trl)
        layer = node.get("layer", 1)
        prereqs = [p for p in node.get("prerequisites", []) if p in active_tree]

        # ④ 역산: 이 기술이 완료되어야 하는 최후 시점 결정
        # - 다음 기술들의 시작 시점 중 가장 빠른 것 - 1
        # - 기본은 market_boom_q - 1 분기 (최종 통합 직전에 완료)
        latest_needed = boom_int - 1

        dependents = [
            d for d in node.get("dependents", [])
            if d in active_tree and d in timeline
        ]
        for dep_id in dependents:
            dep_start_int = quarter_to_int(timeline[dep_id]["start_q"])
            if dep_start_int - 1 < latest_needed:
                latest_needed = dep_start_int - 1

        # ⑤ 완료 분기 = latest_needed, 시작 분기 = 완료 - lead_time
        target_q_int = latest_needed
        start_q_int = target_q_int - lead_time

        # ⑥ 선행 기술 완료 이후 시작 보장 (Zero-slack)
        for prereq_id in prereqs:
            if prereq_id in completion:
                prereq_end = completion[prereq_id]
                if start_q_int <= prereq_end:
                    # 선행 완료 다음 분기에 시작
                    start_q_int = prereq_end + 1
                    target_q_int = start_q_int + lead_time

        # ⑦ 오케스트레이터 강제 시작 분기 적용
        if tid in shift_map:
            forced_start_int = quarter_to_int(shift_map[tid])
            if forced_start_int > start_q_int:
                start_q_int = forced_start_int
                target_q_int = start_q_int + lead_time
                print(f"  [Timeline] {tid} → 오케스트레이터 명령으로 {shift_map[tid]} 로 시작 연기")

        completion[tid] = target_q_int
        timeline[tid] = {
            "tech_id": tid,
            "name": node.get("name", ""),
            "category": node.get("category", ""),
            "trl": trl,
            "layer": layer,
            "phase_name": get_phase_name(trl, layer),
            "start_q": int_to_quarter(start_q_int),
            "target_q": int_to_quarter(target_q_int),
            "prerequisites": prereqs,
            "lead_time_quarters": lead_time,
            "dropped": False,
        }

    # ⑧ Drop 된 기술은 dropped=True 로 표시하여 포함 (보고용)
    for tid in drop_ids:
        if tid in dependency_tree:
            node = dependency_tree[tid]
            timeline[tid] = {
                "tech_id": tid,
                "name": node.get("name", ""),
                "category": node.get("category", ""),
                "trl": node.get("trl", 1),
                "layer": node.get("layer", 0),
                "phase_name": "DROPPED",
                "start_q": "N/A",
                "target_q": "N/A",
                "prerequisites": [],
                "lead_time_quarters": 0,
                "dropped": True,
            }

    return sorted_ids, list(timeline.values())


# ── LangGraph 노드 ────────────────────────────────────────────

def run_timeline_calculator(state: RoadmapState) -> dict:
    """
    역산 타임라인 계산 노드.
    dependency_tree + market_boom_q → timeline_draft
    """
    print("\n[Timeline Calculator] 시작")
    messages = []

    dependency_tree = state.get("dependency_tree", {})
    market_context = state.get("market_context", {})
    market_boom_q = market_context.get("expected_boom_quarter", "2028 Q1")
    feedback = state.get("orchestrator_feedback")

    if not dependency_tree:
        msg = "Timeline Calculator: dependency_tree 가 비어있습니다."
        return {"timeline_draft": [], "messages": [AIMessage(content=msg)], "error": msg}

    print(f"[Timeline Calculator] 역산 기준: {market_boom_q}")
    if feedback:
        print(f"[Timeline Calculator] 오케스트레이터 피드백 적용: {feedback}")

    _, timeline_draft = backcast_timeline(dependency_tree, market_boom_q, feedback)

    # 결과 출력
    print(f"\n[Timeline Calculator] ✅ {len(timeline_draft)}개 기술 타임라인 산출")
    print("-" * 74)
    for item in sorted(timeline_draft, key=lambda x: x.get("start_q", "9999 Q4")):
        if item.get("dropped"):
            print(f"  {item['tech_id']} | {'[DROPPED]':<30} |")
        else:
            print(
                f"  {item['tech_id']} | {item['name'][:30]:<30} | "
                f"{item['start_q']} → {item['target_q']} "
                f"(TRL {item['trl']}, {item['lead_time_quarters']}Q)"
            )
    print("-" * 74)

    messages.append(AIMessage(
        content=f"Timeline Calculator: {len(timeline_draft)}개 기술 역산 완료 (기준: {market_boom_q})"
    ))

    return {
        "timeline_draft": timeline_draft,
        "messages": messages,
        "error": None,
    }
