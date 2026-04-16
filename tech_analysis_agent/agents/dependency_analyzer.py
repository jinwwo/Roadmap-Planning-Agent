"""
agents/dependency_analyzer.py
───────────────────────────────
Roadmap Planner Agent - Step 1: 기술 의존성 트리 구성 노드

역할:
1. Technology Analysis Agent 의 dependency_hints 를 기반으로
   Claude 가 기술 간 정밀한 선후 관계를 확립
2. 카테고리 계층(Material/Equipment → Process → Architecture/Packaging)
   을 적용하여 논리적 레이어를 부여
3. RoadmapState 에 dependency_tree 저장
"""

import json
import re
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage

from llm_factory import get_llm
from state import RoadmapState

# ── 카테고리 레이어 매핑 ──────────────────────────────────────
# 낮을수록 먼저 개발되어야 하는 기반 기술
CATEGORY_LAYER = {
    "Material": 0,
    "Equipment": 0,
    "Process": 1,
    "Architecture": 2,
    "Packaging": 2,
}

# ── 시스템 프롬프트 ───────────────────────────────────────────

DEPENDENCY_SYSTEM_PROMPT = """You are a Technology Dependency Analyzer for a semiconductor/deep-tech roadmap planning system.
Your role is to analyze a list of candidate technologies and establish a precise, logical dependency tree.

---
[Core Responsibilities]

For each technology, determine:
- tech_id (keep original)
- name (keep original)
- category (keep original)
- trl (keep original)
- prerequisites: List of tech_ids that MUST reach functional completion before this tech can begin
- dependents: List of tech_ids that depend on THIS technology
- layer: Integer representing development order (0=foundation, 1=process/integration, 2=system/packaging)

---
[Dependency Rules]

1. Category Hierarchy (strict):
   Material / Equipment (layer 0) → Process (layer 1) → Architecture / Packaging (layer 2)
   A Process technology CANNOT start before its required Material or Equipment technology is complete.

2. Intra-layer dependencies:
   Within the same layer, if Technology A's function is clearly required for Technology B to work,
   A is a prerequisite of B.

3. Hint integration:
   Use the provided dependency_hints as strong signals, but override them if logically inconsistent.

4. Cross-layer rules:
   - A technology cannot have prerequisites from a HIGHER layer.
   - Circular dependencies are FORBIDDEN.

5. Sparsity principle:
   Only list DIRECT prerequisites (not transitive). If A→B→C, C's prerequisites = [B], NOT [A, B].

---
[Layer Assignment]
- layer 0: Material, Equipment (foundational — must exist before anything else)
- layer 1: Process (requires materials and equipment)
- layer 2: Architecture, Packaging (requires processes to be established)

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


def _build_fallback_tree(tech_candidates: list) -> dict:
    """
    Claude 호출 없이 카테고리 계층만으로 단순 의존성 트리를 구성합니다.
    (오류 발생 시 폴백용)
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
            "name": t["name"],
            "category": cat,
            "trl": t.get("trl", 3),
            "prerequisites": [],
            "dependents": [],
            "layer": layer,
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
        tech_summary = [
            {
                "tech_id": t["tech_id"],
                "name": t["name"],
                "category": t["category"],
                "trl": t["trl"],
                "dependency_hints": t.get("dependency_hints", []),
            }
            for t in tech_candidates
        ]

        user_prompt = f"""
다음 {len(tech_candidates)}개의 후보 기술에 대한 의존성 트리를 구성해주세요.

[후보 기술 목록]
{json.dumps(tech_summary, ensure_ascii=False, indent=2)}

시장 컨텍스트: {state.get('market_context', {}).get('target_market', '')}
"""

        print("[Dependency Analyzer] Claude 의존성 분석 요청 중...")
        response = llm.invoke([
            SystemMessage(content=DEPENDENCY_SYSTEM_PROMPT),
            HumanMessage(content=user_prompt),
        ])

        result = _extract_json(response.content)
        dependency_tree = result.get("dependency_tree", {})

        print(f"[Dependency Analyzer] ✅ {len(dependency_tree)}개 노드 의존성 트리 구성 완료")

        # 레이어별 요약 출력
        layers = {0: [], 1: [], 2: []}
        for tid, node in dependency_tree.items():
            layers.get(node.get("layer", 1), []).append(tid)
        print(f"  Layer 0 (기반): {layers[0]}")
        print(f"  Layer 1 (공정): {layers[1]}")
        print(f"  Layer 2 (설계/패키징): {layers[2]}")

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
        print(f"[Dependency Analyzer] ⚠️ {err_msg}")
        fallback = _build_fallback_tree(tech_candidates)
        return {
            "dependency_tree": fallback,
            "messages": [AIMessage(content=err_msg)],
            "error": None,  # 폴백 성공이므로 에러 아님
        }
