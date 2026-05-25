# Orchestration Agent

Technology Roadmap 프로젝트의 **오케스트레이션 레이어**.
사용자 입력을 구조화된 문제(ProblemFrame)로 변환하고, 세 전문 에이전트를
**subprocess 로 독립 실행** 한 뒤, TRM 원칙으로 결과를 평가/재조율하여
최종 로드맵 보고서를 산출합니다.

```
Tech-Analysis-Agent/                    ← GitHub 레포 루트
├── tech_analysis_agent/                 ← Agent 1 (Technology Analyst)
├── roadmap_planner_agent/               ← Agent 2 (Roadmap Planner)
├── investment_strategist_agent/         ← Agent 3 (Investment Strategist)
└── orchestration_agent/                 ← 본 폴더
```

## 파이프라인

```
[User Input: 자연어 (예: "2030년까지의 2nm 파운드리 로드맵 그려줘. 예산 5B, 균형 위험")]
        │
        ▼
  Orchestrator Setup          ← Problem Setup (intake/policy LLM) + Task Orchestration
   (Phase A)                    · extract_intake()   → domain/reference_year/category_hints
                                · extract_investment_policy() → risk/horizon/budget/priorities
                                → ProblemFrame 으로 통합 + active_agents 배분
        │
        ▼
 ┌─ Agent 1 (subprocess) ←──┐ ← active_agents 에 따라 ON / OFF
 │  tech_analysis_agent/    │   + orchestrator_feedback (REVISE 시 자유 텍스트)
 │                          │
 ├─ Agent 2 (subprocess) ←──┤ ← orchestrator_feedback (text/shift/drop)
 │  roadmap_planner_agent/  │
 │                          │
 └─ Agent 3 (subprocess) ←──┘ ← investment_policy + orchestrator_feedback
    investment_strategist_agent/
        │
        ▼
  Orchestrator Review (LLM)   ← TRM 5-축 평가 (Phase B):
   (Phase B)                    feasibility / sequencing / alignment /
        │                       investment rationality / portfolio balance
        │                       + 매 iter 마다 7-섹션 보고서 생성 (잠정 또는 최종)
        │                       + [A1]/[A2]/[A3] 인라인 인용 + artifacts_summary 부록
        │
        ├── ACCEPT  → 최종 report → END
        └── REVISE  → refinement.rerun_agents + feedback 으로 재실행 → 다시 Review
                      (최대 MAX_ORCHESTRATOR_ITERATIONS 회)
                      매 iter 의 review 는 review_history 에 누적 저장
                      상한 도달 시 강제 ACCEPT + 잔여 issue 를 feasibility_and_risk 에 명시
```

## 왜 subprocess 인가?

세 sibling 에이전트는 각자 자기 이름의 `config.py`, `state.py`, `llm_factory.py`
모듈을 가지고 있습니다. Python 의 모듈 캐시(sys.modules) 는 top-level 이름
단위로 동작하므로, 한 프로세스 안에서 라이브러리처럼 import 하면
`config`, `state` 심볼이 먼저 import 된 에이전트 것으로 고정되어 네임 충돌이 납니다.

subprocess 방식은:
- 각자 완전히 격리된 Python 프로세스로 실행됨 → 충돌 없음
- 각 sibling 의 `.env`, config, requirements 가 독립적이어도 OK
- 실제 프로덕션에서 에이전트를 서비스로 배포하는 패턴과 동일
- JSON 파일이 에이전트 간 표준 인터페이스이므로 디버깅 편리

단점은 프로세스 기동 오버헤드(수백 ms~몇 초) 인데,
LLM 호출이 메인 비용인 파이프라인에서는 무시할 수 있습니다.

## 파일 구조

| 파일 | 역할 | LLM |
|------|------|-----|
| `main.py`                 | CLI 엔트리 (`--agent "1 2 3"` 등) | - |
| `config.py`               | sibling 경로, 출력 폴더, 기본 ProblemFrame 값 | - |
| `state.py`                | TypedDict (ProblemFrame, ReviewResult 등) | - |
| `llm_factory.py`          | Claude / Ollama provider 추상화 | - |
| `agents/orchestrator.py`  | Setup (CLI 모드 LLM 없음) + Review (TRM 5축 평가) + `generate_final_report` (7-섹션 + `[A1/A2/A3]` 인용 + `artifacts_summary` 부록) | ✅ |
| `interactive/session.py`  | 웹/대화 세션 진입점. **`extract_intake()` / `extract_investment_policy()` 가 사용자 자연어를 LLM 으로 ProblemFrame 으로 변환** | ✅ |
| `pipeline.py`             | subprocess 기반 3-에이전트 러너 + REVISE 루프. Agent 1/2/3 모두 `orchestrator_feedback` 전달 | - |
| `web/app.js · style.css`  | 7-섹션 보고서 + `artifacts_summary` 8번째 섹션 + REVISE feedback 내용 리스트 렌더 | - |
| `outputs/`                | 중간/최종 JSON 산출물 | - |

## 실행

### 환경 준비

```bash
# 4개 에이전트 통합 의존성 (루트 requirements.txt 한 번에 설치)
cd Tech-Analysis-Agent
pip install -r requirements.txt
```

LLM provider 설정은 각 폴더의 `.env` 를 이용합니다 (모두 동일한 이름 규약):

```ini
LLM_PROVIDER=anthropic         # 또는 ollama
ANTHROPIC_API_KEY=sk-ant-...
# 혹은
OLLAMA_MODEL=gemma3:27b
OLLAMA_BASE_URL=http://localhost:11434
```

### 기본 실행 (세 Agent 모두 ON)

```bash
python main.py
```

### Agent 유무에 따른 보고서 성능 비교 (ablation)

`--agent "..."` 로 활성화할 에이전트를 선택합니다.
OFF 된 에이전트는 최소 폴백 산출물(dummy candidates / flat roadmap / empty strategy)
로 대체됩니다. `--out-prefix` 를 다르게 주어 결과 파일이 덮어쓰이지 않게 합니다.

```bash
# 전 에이전트 ON (baseline)
python main.py --agent "1 2 3" --out-prefix "full_"

# Investment Strategist 제외 (Agent 3 OFF)
python main.py --agent "1 2"   --out-prefix "noA3_"

# Roadmap Planner 제외 (Agent 2 OFF → flat 로드맵)
python main.py --agent "1 3"   --out-prefix "noA2_"

# Technology Analyst 제외 (Agent 1 OFF → 더미 후보)
python main.py --agent "2 3"   --out-prefix "noA1_"

# Agent 1 만 (상한 비교용)
python main.py --agent "1"     --out-prefix "onlyA1_"
```

각 실험이 끝나면 `outputs/` 폴더에 prefix 가 다른 4개의 JSON 파일이 쌓입니다.
`<prefix>orchestrator_report.json` 의 `review.report.executive_summary` 를 서로
비교하면 에이전트 유무에 따른 보고서 품질 차이를 관찰할 수 있습니다.

### Problem Frame override

```bash
python main.py \
  --domain "차세대 HBM4 스택 패키지" \
  --year 2025 \
  --budget 3000000000 \
  --objective "HBM4 8-hi 스택 3년 내 양산" \
  --priorities "수율 안정화|TSV 미세화|냉각 솔루션"
```

## 출력 파일 (outputs/ 하위)

| 파일 | 내용 |
|------|------|
| `<prefix>tech_candidates.json`     | Agent 1 결과 (market_context + tech_candidates) |
| `<prefix>planned_roadmap.json`     | Agent 2 결과 (planned_roadmap + dependency_tree) |
| `<prefix>investment_strategy.json` | Agent 3 결과 (stages + investment_strategy) |
| `<prefix>orchestrator_report.json` | Orchestrator 최종 (problem_frame / active_agents / iteration / review / **review_history[]** / artifact_paths) |

`orchestrator_report.json` 의 `review` 구조 (ACCEPT 시):

```json
{
  "decision": "ACCEPT",
  "trm_assessment": {
    "feasibility":       {"budget_feasible": true, "schedule_feasible": true, "comment": "..."},
    "sequencing":        {"dependency_valid": true, "comment": "..."},
    "strategic_alignment": {"company_fit": 0.8, "future_trend_alignment": 0.9, "comment": "..."},
    "investment_rationality": {"over_invested": [], "under_invested": [], "comment": "..."},
    "portfolio_balance": {"short_long_balance": 0.7, "risk_balance": 0.65, "comment": "..."}
  },
  "issues": [],
  "refinement": {"rerun_agents": [], "feedback": []},
  "report": {
    "executive_summary": "T01(High-NA EUV) 최종점수 88.71 [A1] 을 Tier 1 [A3] 로 분류, 2026 Q3 → 2027 Q1 [A2] 양산 전환...",
    "technology_strategy": "...",
    "roadmap_structure": "...",
    "investment_strategy": "...",
    "trend_alignment": "...",
    "feasibility_and_risk": "...",
    "expected_outcomes": "...",
    "artifacts_summary": {
      "agent1_tech_candidates": [...],
      "agent2_planned_roadmap":  [...],
      "agent3_investment_strategy": [...],
      "insights": {"tier_distribution": {...}, "top_5_tech_by_score": [...], "dependency_edges": [...]}
    }
  },
  "diagnostic_summary": ""
}
```

**보고서 형식 — 핵심**:
- **7-섹션 한국어 narrative** (`executive_summary` ... `expected_outcomes`)
- **인라인 인용**: 각 수치/판정 뒤에 `[A1]` (Tech Analyst), `[A2]` (Roadmap Planner), `[A3]` (Strategist) 마커 — 협업자가 출처 추적 가능
- **`artifacts_summary` 8번째 섹션**: Agent 1/2/3 의 raw 데이터 + 집계 insights 자동 첨부 (LLM 호출 없이 Python 후처리). UI 에서도 표 형태로 렌더.

**REVISE 시에도 7-섹션 보고서가 생성됩니다** (잠정 보고서). 잔여 issues / feedback 은
`feasibility_and_risk` 섹션에 명시되며, 다음 iter 에서 갱신되어 최종 ACCEPT 시점
보고서가 최종본이 됩니다. `review_history[]` 에 매 iter 의 review + report 가 누적 저장됨.

Orchestrator 가 REVISE 결정을 내리면 `refinement.rerun_agents` 에 지정된 에이전트만
재실행되고 (파이프라인 순서상 앞 단계부터 뒤까지) `refinement.feedback` 이 자유 텍스트
채널로 **Agent 1 / 2 / 3 모든 에이전트의 시스템 프롬프트에 박혀** 다음 iter 의 산출물에 반영됩니다.
- Agent 1: `patent_agent` / `market_agent` 의 user_prompt 끝에 feedback 블록 (누락된 후보 보강 지시)
- Agent 2: `tech_selector` / `dependency_analyzer` / `roadmap_builder` 모두 feedback 받음
- Agent 3: `strategist` 의 prompt 에 박혀 Tier / 예산 비율 조정

## 자동 보정 메커니즘

LLM 이 review 응답에서 빠뜨리거나 모순된 정보를 코드가 자동 보강합니다:

| 보정 | 트리거 | 동작 |
|------|------|------|
| **TRM FAIL 자동 감지** | `trm_assessment` 의 boolean 이 `false` 인데 `decision=ACCEPT` 로 통과 | issues / feedback 자동 추가 + ACCEPT → REVISE 강제 전환 |
| **rerun_agents 자동 보강** | `issues[].axis` 의 책임 Agent 가 `rerun_agents` 에서 누락 | axis → Agent 매핑으로 자동 추가 (예: `strategic_alignment` → Technology Analyst) |
| **JSON 파싱 강건화** | LLM 응답이 trailing comma / smart quotes 등 형식 오류 | 자동 보정 후 재파싱 |
| **Partial recovery** | JSON 파싱 완전 실패 (e.g. 쉼표 누락) | regex 로 `decision`/`issues`/`trm_assessment`/`refinement`/`report` 핵심 필드만 발췌해 dict 재조립 |
| **Issue axis 정규화** | `axis` 가 별칭 (e.g. `alignment`, `dependency`) 또는 누락 | 5축 canonical 이름으로 매핑, text 기반 추론 |
| **강제 ACCEPT** | `MAX_ORCHESTRATOR_ITERATIONS` 도달했는데 여전히 REVISE | ACCEPT 로 전환 후 잔여 issue 를 보고서 `feasibility_and_risk` 에 명시 |

axis → 책임 Agent 매핑 (`fail_axis_to_agent`):

| axis | 자동 보강 대상 |
|------|------|
| `feasibility`            | Roadmap Planner + Investment Strategist |
| `sequencing`             | Roadmap Planner |
| `strategic_alignment`    | Technology Analyst |
| `investment_rationality` | Investment Strategist |
| `portfolio_balance`      | Roadmap Planner + Investment Strategist |

## Agent OFF 시 폴백 동작

보고서 품질 대조 실험을 위해, 각 Agent 가 OFF 되면 다음과 같은 **최소 산출물**을
다음 단계로 넘깁니다:

| Agent | OFF 시 대체 산출물 |
|-------|--------------------|
| 1     | 더미 후보 5개 + 고정 market_context (boom=2028 Q1) |
| 2     | flat roadmap — 전 기술을 단일 phase, 의존성/역산 없음 |
| 3     | 빈 stages / investment_strategy |

Orchestrator Review 프롬프트에는 어떤 Agent 가 OFF 되었는지가
`Notes (degraded inputs due to disabled agents)` 섹션으로 명시되어
LLM 이 결손 사유를 알고 평가합니다.

## CLI 옵션 요약

| 옵션 | 의미 | 기본값 |
|------|------|--------|
| `--domain`        | 분석 도메인 | `차세대 2nm 이하 파운드리 및 AI 가속기 시장` |
| `--year`          | 기준 연도 | `2025` |
| `--categories`    | 기술 카테고리 힌트 (콤마) | 5종 모두 |
| `--agent`         | 활성 Agent 번호 (예: `"1 2 3"`) | `1 2 3` |
| `--industry`      | Problem Frame industry | `AI / Semiconductor` |
| `--company-type`  | Problem Frame company_type | `Tier-1 IDM / Foundry` |
| `--time-horizon`  | 시간 지평 | `2025-2030` |
| `--budget`        | 총 예산 (USD) | `5_000_000_000` |
| `--objective`     | 목표 서술 | 기본 문장 |
| `--priorities`    | 우선순위 (파이프 `\|` 구분) | 3종 |
| `--future-trend`  | Review 에 전달할 미래 동향 요약 | 기본 문장 |
| `--stage-mode`    | Agent 3 의 stage 집계 방식 (`phase`/`horizon`) | `phase` |
| `--out-prefix`    | outputs/ 파일명 prefix | `""` |

## 환경 변수

| 변수 | 의미 | 기본값 |
|------|------|--------|
| `MAX_ORCHESTRATOR_ITERATIONS` | REVISE 루프 상한 | `2` |
| `SUBPROCESS_TIMEOUT_SEC`      | sibling 호출 timeout (초) | `900` |
| `LLM_PROVIDER`, `ANTHROPIC_API_KEY`, `OLLAMA_MODEL`, `OLLAMA_BASE_URL` | LLM 설정 | sibling 과 동일 |
