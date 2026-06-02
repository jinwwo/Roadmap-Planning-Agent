"""
main.py
────────
Technology Roadmap AI Agent System 진입점

전체 파이프라인:
  Agent 1: Technology Analysis  (KIPRIS + Tavily + Claude/Ollama)
  Agent 2: Roadmap Planner      (Dependency Tree + Backcasting + Claude)
  Agent 3: Investment Strategist (향후 구현)

실행:
  python main.py

결과물:
  output_tech_candidates.json  : Agent 1 출력 (Agent 2 입력 포맷)
  output_planned_roadmap.json  : Agent 2 출력 (Agent 3 입력 포맷)
"""

import json
import os
from config import validate_config
from graphs.global_graph import create_global_graph
from state import GlobalState


def main():
    # ① 환경 변수 검증
    validate_config()
    patent_method = os.getenv("PATENT_ANALYSIS_METHOD", "C_company_portfolio")

    # ② 분석 요청 설정
    domain = "AI / Semiconductor / GPU"
    company_name = "NVIDIA"
    company_profile = (
        "Company Scenario: NVIDIA. Industry: AI / Semiconductor / GPU. "
        "Annual Revenue: ~60B USD. R&D Budget Ratio: ~20%. Annual R&D Budget: ~12B USD. "
        "Planning Horizon: 2026-2030 (5 years). Strategic Direction: Maintain leadership "
        "in AI hardware (GPU); expand AI infrastructure and platform ecosystem; strengthen "
        "end-to-end AI stack (hardware + software)."
    )
    reference_year = 2030
    category_hints = ["Architecture", "Packaging", "Process", "Equipment"]

    # ③ [선택] 오케스트레이터 피드백 예시
    #    실제 Investment Strategist 가 예산 부족을 이유로 일부 기술을 연기/제외할 때 사용
    orchestrator_feedback = None
    # 예시 (주석 해제하면 적용):
    # orchestrator_feedback = {
    #     "shift": [{"tech_id": "T02", "new_start_q": "2026 Q1"}],
    #     "drop":  ["T04"],
    # }

    # ④ 글로벌 그래프 생성 및 초기 상태 구성
    global_graph = create_global_graph()

    initial_state: GlobalState = {
        "domain": domain,
        "reference_year": reference_year,
        "category_hints": category_hints,
        "company_name": company_name,
        "company_profile": company_profile,
        "related_companies": None,
        "tech_candidates": None,
        "market_context": None,
        "candidate_selection": None,
        "patent_maps": None,
        "patent_prompt": None,
        "planned_roadmap": None,
        "dependency_tree": None,
        "investment_plan": None,
        "orchestrator_feedback": orchestrator_feedback,
        "messages": [],
        "current_step": "technology_analysis",
        "error": None,
    }

    # ⑤ 전체 파이프라인 실행
    print("\n" + "=" * 60)
    print("  Technology Roadmap AI Agent System 시작")
    print(f"  도메인  : {domain}")
    print(f"  기준연도: {reference_year}")
    print(f"  patent_method: {patent_method}")
    print("=" * 60)

    final_state = global_graph.invoke(initial_state)

    # ⑥ Agent 1 결과 저장
    agent1_output = {
        "market_context": final_state.get("market_context", {}),
        "tech_candidates": final_state.get("tech_candidates", []),
        "candidate_selection": final_state.get("candidate_selection", {}),
        "patent_maps": final_state.get("patent_maps", {}),
        "patent_prompt": final_state.get("patent_prompt", {}),
    }
    with open("output_tech_candidates.json", "w", encoding="utf-8") as f:
        json.dump(agent1_output, f, ensure_ascii=False, indent=2)

    # ⑦ Agent 2 결과 저장
    agent2_output = {
        "market_context": final_state.get("market_context", {}),
        "planned_roadmap": final_state.get("planned_roadmap", []),
    }
    with open("output_planned_roadmap.json", "w", encoding="utf-8") as f:
        json.dump(agent2_output, f, ensure_ascii=False, indent=2)

    # ⑧ 최종 요약 출력
    print("\n" + "=" * 60)
    print("  파이프라인 완료 요약")
    print("=" * 60)
    print(f"  Agent 1 출력: {len(agent1_output['tech_candidates'])}개 후보 기술")
    print(f"  Agent 2 출력: {len(agent2_output['planned_roadmap'])}개 로드맵 항목")
    print(f"\n  저장 파일:")
    print(f"    output_tech_candidates.json  (→ Agent 2 입력)")
    print(f"    output_planned_roadmap.json  (→ Agent 3 입력)")

    if final_state.get("error"):
        print(f"\n  ⚠️  오류: {final_state['error']}")

    return final_state


if __name__ == "__main__":
    main()
