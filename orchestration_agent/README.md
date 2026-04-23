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
[User Input: domain, year, budget, priorities, ...]
        │
        ▼
  Orchestrator Setup          ← ProblemFrame 구조화 + Agent 역할 배분
        │
        ▼
 ┌─ Agent 1 (subprocess) ←──┐ ← active_agents 에 따라 ON / OFF
 │  tech_analysis_agent/    │
 │                          │
 ├─ Agent 2 (subprocess) ←──┤ ← orchestrator_feedback(text/shift/drop) 전달
 │  roadmap_planner_agent/  │
 │                          │
 └─ Agent 3 (subprocess) ←──┘ ← ProblemFrame → investment_policy 자동 매핑
    investment_strategist_agent/
        │
        ▼
  Orchestrator Review (LLM)   ← TRM 5-축 평가:
        │                         feasibility / sequencing / alignment /
        │                         investment rationality / portfolio balance
        │
        ├── ACCEPT  → 7-섹션 최종 report 생성 → END
        └── REVISE  → refinement.rerun_agents 만 재실행 → 다시 Review
                      (최대 MAX_ORCHESTRATOR_ITERATIONS 회)
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
| `agents/orchestrator.py`  | Setup (LLM 없음) + Review (LLM, TRM 평가 + 7-섹션 보고서) | ✅ |
| `pipeline.py`             | subprocess 기반 3-에이전트 러너 + REVISE 루프 | - |
| `outputs/`                | 중간/최종 JSON 산출물 | - |

## 실행

### 환경 준비

```bash
# Orchestration-Agent 자신의 의존성
cd orchestration_agent
pip install -r requirements.txt

# sibling 각자의 의존성도 먼저 설치되어 있어야 함
(cd ../tech_analysis_agent         && pip install -r requirements.txt)
(cd ../roadmap_planner_agent       && pip install -r requirements.txt)
(cd ../investment_strategist_agent && pip install -r requirements.txt)
```

LLM provider 설정은 각 폴더의 `.env` 를 이용합니다 (모두 동일한 이름 규약):

```ini
LLM_PROVIDER=anthropic         # 또는 ollama
ANTHROPIC_API_KEY=sk-ant-...
# 혹은
OLLAMA_MODEL=llama3.1:8b
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
| `<prefix>orchestrator_report.json` | Orchestrator 최종 (problem_frame / active_agents / iteration / review) |

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
    "executive_summary": "...",
    "technology_strategy": "...",
    "roadmap_structure": "...",
    "investment_strategy": "...",
    "trend_alignment": "...",
    "feasibility_and_risk": "...",
    "expected_outcomes": "..."
  },
  "diagnostic_summary": ""
}
```

REVISE 시에는 `report` 필드가 비워지고 `diagnostic_summary` 만 채워집니다.
Orchestrator 가 REVISE 결정을 내리면 `refinement.rerun_agents` 에 지정된
에이전트만 재실행되고 (파이프라인 순서상 앞 단계부터 뒤까지) 다시 Review 됩니다.

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
