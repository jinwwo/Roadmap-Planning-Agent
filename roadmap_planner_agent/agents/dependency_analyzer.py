"""
agents/dependency_analyzer.py
───────────────────────────────
Roadmap Planner Agent — Step 1: 기술 의존성 트리 구성

역할:
1. Technology Analyst Agent 의 dependency_hints 를 기반으로
   LLM 이 기술 간 정밀한 선후 관계(prerequisites / dependents) 를 확립
2. 카테고리 계층 (Material/Equipment → Process → Architecture/Packaging) 을
   적용하여 논리적 레이어(0/1/2) 를 부여
3. RoadmapState 에 dependency_tree 를 저장

출력 dependency_tree 포맷:
  {
    "T01": {
      "tech_id": "T01",
      "name": "...",
      "category": "Equipment",
      "trl": 4,
      "prerequisites": [],
      "dependents": ["T03"],
      "layer": 0
    },
    ...
  }
"""

import json
import re
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage

from llm_factory import get_llm
from config import CATEGORY_LAYER
from state import RoadmapState


# ── 시스템 프롬프트 ───────────────────────────────────────────

DEPENDENCY_SYSTEM_PROMPT = """You are a Technology Dependency Analyzer for a semiconductor / deep-tech roadmap planning system.
Your role is to analyze a list of candidate technologies and establish a precise, logical dependency tree.

---
[Core Responsibilities]

For each technology, determine:
- tech_id     (keep original)
- name        (keep original)
- category    (keep original)
- trl         (keep original)
- prerequisites : list of tech_ids that MUST reach functional completion before this tech can begin
- dependents   : list of tech_ids that depend on THIS technology
- layer        : integer representing development order
                 (0 = foundation, 1 = process/integration, 2 = system/packaging)

---
[Dependency Rules]

1. Category Hierarchy (strict):
   Material / Equipment (layer 0) → Process (layer 1) → Architecture / Packaging (layer 2)
   A Process technology CANNOT start before its required Material or Equipment technology is complete.

2. Intra-layer dependencies:
   Within the same layer, if Technology A's function is clearly required for
   Technology B to work, A is a prerequisite of B.

   For example, within the Process layer technologies often have natural sequence:
   - Foundation processes (e-beam inspection, basic etching, ALD precursor deposition)
     are typically required before advanced processes (DSA patterning, GAA fabrication, ALE).
   - Pattern definition (lithography) is typically required for pattern transfer (etching)
     and pattern refinement (selective deposition).
   - System verification techs (final integration, packaging-readiness) typically
     depend on at least one foundation or intermediate process tech.

   Use these examples as guidance only when functional dependency is genuine —
   do not invent dependencies just to vary the timeline.

3. Hint integration:
   Use the provided dependency_hints as strong signals, but override them if logically inconsistent.

3a. Description integration:
   Each tech may include a `description` field (Agent 1 의 시장/특허 분석 본문 발췌).
   Use it to refine intra-layer dependencies — e.g., if description mentions
   "EUV scanner is required for High-NA patterning", then the High-NA patterning tech
   has the EUV scanner as prerequisite.

4. Cross-layer rules:
   - A technology cannot have prerequisites from a HIGHER layer.
   - Circular dependencies are FORBIDDEN.

5. Sparsity principle:
   Only list DIRECT prerequisites (not transitive).
   If A → B → C, C's prerequisites = [B], NOT [A, B].

---
[Layer Assignment]
- layer 0 : Material, Equipment (foundational — must exist before anything else)
- layer 1 : Process            (requires materials and equipment)
- layer 2 : Architecture, Packaging (requires processes to be established)

---
[CRITICAL] Output ONLY valid JSON. No markdown. No explanation outside JSON.

Output format:
{
  "dependency_tree": {
    "T01": {
      "tech_id": "T01",
      "name": "...",
      "category": "Equipment",
      "trl": 4,
      "prerequisites": [],
      "dependents": ["T03"],
      "layer": 0
    },
    "T03": {
      "tech_id": "T03",
      "name": "...",
      "category": "Process",
      "trl": 5,
      "prerequisites": ["T01", "T02"],
      "dependents": [],
      "layer": 1
    }
  }
}"""


def _extract_json(text: str) -> dict:
    cleaned = re.sub(r"```(?:json)?\s*", "", text).replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]+\}", cleaned)
        if match:
            return json.loads(match.group())
        raise ValueError(f"JSON 파싱 실패:\n{text[:300]}")


def _enforce_bidirectional(tree: dict) -> dict:
    """LLM 응답에서 한쪽만 등록된 의존성을 양방향으로 강제 정합.

    예) T01.dependents 에 T08 이 등록됐지만 T08.prerequisites 가 비어있으면
        T08.prerequisites 에 T01 자동 추가. 그 반대도 동일.

    이게 없으면 timeline_calculator 가 prerequisites 만 보기 때문에
    선행 기술이 후행 기술보다 늦게 시작되는 비정상 timeline 이 발생.
    """
    for tid, node in list(tree.items()):
        for prereq_id in list(node.get("prerequisites", []) or []):
            if prereq_id in tree:
                deps = tree[prereq_id].setdefault("dependents", []) or []
                if tid not in deps:
                    deps.append(tid)
                tree[prereq_id]["dependents"] = deps
        for dep_id in list(node.get("dependents", []) or []):
            if dep_id in tree:
                preqs = tree[dep_id].setdefault("prerequisites", []) or []
                if tid not in preqs:
                    preqs.append(tid)
                tree[dep_id]["prerequisites"] = preqs
    return tree


def _format_upper_context(company_scenario: dict, strategic_direction: list) -> str:
    """Orchestrator 추출 Company Scenario + Strategic Direction → 상위 컨텍스트 블록."""
    if not company_scenario and not strategic_direction:
        return ""
    block = "\n[Company Scenario & Strategic Direction — 상위 컨텍스트]\n"
    if company_scenario:
        cn = company_scenario.get("company_name", "")
        ind = company_scenario.get("industry", "")
        rev = company_scenario.get("annual_revenue", 0) or 0
        ratio = company_scenario.get("rd_budget_ratio", 0) or 0
        rd = company_scenario.get("annual_rd_budget", 0) or 0
        horizon = company_scenario.get("planning_horizon", "")
        block += f"- Company: {cn}\n- Industry: {ind}\n"
        if rev: block += f"- Annual Revenue: ${rev:,.0f}\n"
        if ratio: block += f"- R&D Budget Ratio: {ratio:.0%}\n"
        if rd: block += f"- Annual R&D Budget: ${rd:,.0f}\n"
        if horizon: block += f"- Planning Horizon: {horizon}\n"
    if strategic_direction:
        block += "\n[Strategic Direction]\n"
        for i, d in enumerate(strategic_direction, 1):
            block += f"  {i}. {d}\n"
    return block + "\n"


def _format_orchestrator_feedback(orchestrator_feedback: dict) -> str:
    """REVISE 시 전달된 feedback 을 dependency_analyzer 프롬프트에 박을 섹션으로 포맷."""
    if not orchestrator_feedback:
        return ""
    items = orchestrator_feedback.get("text") or []
    items = [str(t).strip() for t in items if isinstance(t, str) and t.strip()]
    if not items:
        return ""
    bullet = "\n".join(f"- {t}" for t in items)
    return (
        "\n[ORCHESTRATOR REVISE FEEDBACK] — 직전 review 가 지적한 사항. "
        "의존성 트리 / 레이어 할당 시 반영하라 (예: 시점 지연 지적 → prerequisite 단순화).\n"
        f"{bullet}\n"
    )


def _build_fallback_tree(tech_candidates: list) -> dict:
    """
    LLM 호출 없이 카테고리 계층만으로 단순 의존성 트리를 구성합니다.
    (LLM 오류 시 폴백용)
    """
    tree = {}
    layer0_ids = []
    layer1_ids = []

    for t in tech_candidates:
        tid = t["tech_id"]
        cat = t.get("category", "Process")
        layer = CATEGORY_LAYER.get(cat, 1)

        tree[tid] = {
            "tech_id": tid,
            "name": t.get("name", ""),
            "category": cat,
            "trl": t.get("trl", 3),
            "prerequisites": [],
            "dependents": [],
            "layer": layer,
            "expected_market_boom_quarter": t.get("expected_market_boom_quarter", ""),
        }
        if layer == 0:
            layer0_ids.append(tid)
        elif layer == 1:
            layer1_ids.append(tid)

    # layer 1 기술들은 모든 layer 0 기술에 의존 (단순화)
    for tid, node in tree.items():
        if node["layer"] == 1 and layer0_ids:
            node["prerequisites"] = layer0_ids[:2]  # 최대 2개로 제한
            for l0_id in layer0_ids[:2]:
                if tid not in tree[l0_id]["dependents"]:
                    tree[l0_id]["dependents"].append(tid)

        if node["layer"] == 2 and layer1_ids:
            node["prerequisites"] = layer1_ids[:2]
            for l1_id in layer1_ids[:2]:
                if tid not in tree[l1_id]["dependents"]:
                    tree[l1_id]["dependents"].append(tid)

    return tree


# ── LangGraph 노드 ────────────────────────────────────────────

def run_dependency_analyzer(state: RoadmapState) -> dict:
    """
    기술 의존성 트리 구성 노드.
    tech_candidates → dependency_tree
    """
    print("\n[Dependency Analyzer] 시작")
    messages = []

    tech_candidates = state.get("tech_candidates", [])
    if not tech_candidates:
        msg = "Dependency Analyzer: tech_candidates 가 비어있습니다."
        return {"dependency_tree": {}, "messages": [AIMessage(content=msg)], "error": msg}

    try:
        llm = get_llm(max_tokens=3000)

        # 입력 데이터 요약 (필요 필드만)
        # rationale 의 [Patent]/[Market] 본문은 의존성 추론에 도움이 되므로 포함
        # (단 길면 240자 제한)
        def _slim_rationale(r: str) -> str:
            if not isinstance(r, str):
                return ""
            return r[:240] + ("…" if len(r) > 240 else "")

        tech_summary = [
            {
                "tech_id": t["tech_id"],
                "name": t.get("name", ""),
                "category": t.get("category", "Process"),
                "trl": t.get("trl", 3),
                "dependency_hints": t.get("dependency_hints", []),
                "description": _slim_rationale(t.get("rationale", "")),
            }
            for t in tech_candidates
        ]

        feedback_block = _format_orchestrator_feedback(state.get("orchestrator_feedback"))
        upper_block = _format_upper_context(
            state.get("company_scenario"),
            state.get("strategic_direction"),
        )

        user_prompt = f"""{upper_block}
다음 {len(tech_candidates)}개의 후보 기술에 대한 의존성 트리를 구성해주세요.

[후보 기술 목록]
{json.dumps(tech_summary, ensure_ascii=False, indent=2)}

시장 컨텍스트: {state.get('market_context', {}).get('target_market', '')}
{feedback_block}"""

        print("[Dependency Analyzer] LLM 의존성 분석 요청 중...")
        response = llm.invoke([
            SystemMessage(content=DEPENDENCY_SYSTEM_PROMPT),
            HumanMessage(content=user_prompt),
        ])

        result = _extract_json(response.content)
        dependency_tree = result.get("dependency_tree", {})

        # ── 원본 필드 강제 복구 ──────────────────────────────
        # 작은 모델(llama3.1:8b 등)이 "keep original" 지시를 무시하고
        # name/category/trl 를 재생성/할루시네이션 하는 경우가 있음.
        # prerequisites / dependents / layer 만 LLM 결과를 유지하고 나머지는
        # 원본 tech_candidates 값으로 강제 덮어쓰기.
        # 추가로 expected_market_boom_quarter (per-tech) 도 함께 보존 — 역산 시 활용.
        tc_by_id = {t["tech_id"]: t for t in tech_candidates}
        for tid, node in list(dependency_tree.items()):
            src = tc_by_id.get(tid)
            if src is None:
                continue
            node["tech_id"] = tid
            node["name"] = src.get("name", node.get("name", ""))
            node["category"] = src.get("category", node.get("category", ""))
            node["trl"] = src.get("trl", node.get("trl", 1))
            # per-tech 시장 개화 시점 (Agent 1 산출) — timeline_calculator 가 활용
            if src.get("expected_market_boom_quarter"):
                node["expected_market_boom_quarter"] = src["expected_market_boom_quarter"]

        # LLM 이 tech_id 를 누락시켰을 때 원본을 fallback 노드로 보강
        for tid, src in tc_by_id.items():
            if tid not in dependency_tree:
                dependency_tree[tid] = {
                    "tech_id": tid,
                    "name": src.get("name", ""),
                    "category": src.get("category", "Process"),
                    "trl": src.get("trl", 3),
                    "prerequisites": [],
                    "dependents": [],
                    "layer": CATEGORY_LAYER.get(src.get("category", "Process"), 1),
                    "expected_market_boom_quarter": src.get("expected_market_boom_quarter", ""),
                }

        # ── 양방향 정합 강제 ───────────────────────────────────
        # LLM 이 한쪽 (예: T01.dependents) 만 등록하고 반대쪽 (T08.prerequisites)
        # 을 누락하면 timeline_calculator 가 후자만 보기 때문에 후행 기술이
        # 선행 기술보다 빨리 시작되는 비정상 timeline 발생. 강제 양방향 보강.
        dependency_tree = _enforce_bidirectional(dependency_tree)

        print(f"[Dependency Analyzer] ✅ {len(dependency_tree)}개 노드 의존성 트리 구성 완료 (원본 필드 복구 + 양방향 정합)")

        # 레이어별 요약 출력
        layers = {0: [], 1: [], 2: []}
        for tid, node in dependency_tree.items():
            layers.get(node.get("layer", 1), []).append(tid)
        print(f"  Layer 0 (기반)     : {layers[0]}")
        print(f"  Layer 1 (공정)     : {layers[1]}")
        print(f"  Layer 2 (설계/패키지): {layers[2]}")

        messages.append(AIMessage(
            content=f"Dependency Analyzer: {len(dependency_tree)}개 노드 트리 완성"
        ))

        return {
            "dependency_tree": dependency_tree,
            "messages": messages,
            "error": None,
        }

    except Exception as e:
        err_msg = f"Dependency Analyzer 오류: {e}. 폴백 트리 사용."
        print(f"[Dependency Analyzer] ⚠️  {err_msg}")
        fallback = _build_fallback_tree(tech_candidates)
        return {
            "dependency_tree": fallback,
            "messages": [AIMessage(content=err_msg)],
            "error": None,  # 폴백 성공이므로 에러 아님
        }
