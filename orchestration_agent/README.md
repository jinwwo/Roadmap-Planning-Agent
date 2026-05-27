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
  Orchestrator Setup          ← Problem Setup (1-2 LLM 호출) + Task Orchestration
   (Phase A)                    · extract_setup_context() — 통합 1 콜 (Company Scenario + Strategic Direction):
                                    company_name / industry / annual_revenue /
                                    rd_budget_ratio / annual_rd_budget /
                                    planning_horizon / objective / strategic_direction[]
                                  + 자동 도출 (LLM 호출 X):
                                    domain (=industry) / reference_year (planning_horizon 파싱) /
                                    category_hints (5종 default)
                                · extract_investment_policy() → risk / horizon / budget / priorities
                                  (별도 정책 textarea 가 있을 때만)
                                → ProblemFrame 으로 통합 + active_agents 배분
                                → Company Scenario + Strategic Direction 이 모든 Agent
                                  1/2/3 의 LLM 프롬프트 [상위 컨텍스트] 에 주입
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
| `interactive/session.py`  | 웹/대화 세션 진입점. **`extract_setup_context()` 1콜로 사용자 자연어 → intake + Company Scenario + Strategic Direction 동시 추출** (이전 3 콜 → 1 콜로 단순화). 별도 `extract_investment_policy()` 는 정책 텍스트가 따로 있을 때만 사용 | ✅ |
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
| `<prefix>planned_roadmap.json`     | Agent 2 결과 (planned_roadmap with year_idx + 3-reasoning + dependency_tree) |
| `<prefix>investment_strategy.json` | Agent 3 결과 (stages + investment_strategy with tech_budget_usd + 3-reasoning) |
| `<prefix>orchestrator_report.json` | Orchestrator 최종 (problem_frame / active_agents / iteration / review / review_history[] / artifact_paths) |

## 최종 보고서 구조

`review.report` 는 다음을 포함:

- **`artifacts_summary` (LLM 호출 없이 Python 후처리로 채움)**:
  - `agent1_tech_candidates` — 후보 기술 raw 표
  - `agent2_planned_roadmap` — Designer 의 timeline + **year_idx_start/target + 3-reasoning** 풀 포함
  - `agent3_investment_strategy` — Strategist 의 stage + tech_investments (**tech_budget_usd + 3-reasoning** 풀 포함)
  - `year_tech_matrix` — 내부 데이터 구조 (`cells`, `yearly_budget_total`, `max_year`). 보고서 렌더 시 Gantt 차트로 변환
  - `insights` — Tier 분포 / 카테고리 분포 / 평균 TRL / dependency edges 등 집계

> 옛 7-섹션 LLM narrative (`executive_summary`, `technology_strategy` 등) 는 더 이상 생성/표시하지 않음 — 각 에이전트의 raw 출력만으로 보고서를 구성.

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
- **각 Agent 의 raw 출력 위주** — narrative 없이 후보 기술 / 로드맵 / 투자 전략 데이터 직접 제시
- **`artifacts_summary`** : Agent 1/2/3 의 출력 + 집계 insights (LLM 호출 없이 Python 후처리)
- **3 형식 자동 export** : `.json` / `.md` / `.html` (다음 섹션 참조)

`review_history[]` 에 매 iter 의 review 가 누적 저장됨.

Orchestrator 가 REVISE 결정을 내리면 `refinement.rerun_agents` 에 지정된 에이전트만
재실행되고 (파이프라인 순서상 앞 단계부터 뒤까지) `refinement.feedback` 이 자유 텍스트
채널로 **Agent 1 / 2 / 3 모든 에이전트의 시스템 프롬프트에 박혀** 다음 iter 의 산출물에 반영됩니다.
- Agent 1: `patent_agent` / `market_agent` 의 user_prompt 끝에 feedback 블록 (누락된 후보 보강 지시)
- Agent 2: `tech_selector` / `dependency_analyzer` / `roadmap_builder` 모두 feedback 받음
- Agent 3: `strategist` 의 prompt 에 박혀 Tier / 예산 비율 조정

## ProblemFrame 필드 (Setup 산출)

웹 UI 의 "Problem Frame (Orchestrator Setup)" 카드와 모든 Agent 의 [상위 컨텍스트] 블록에 사용:

| 필드 | 출처 | 비고 |
|---|---|---|
| `company_name` | extract_setup_context (LLM) | "NVIDIA", "Samsung" 등. 없으면 "(unknown)" |
| `industry` | extract_setup_context (LLM) | "AI / Semiconductor / GPU" 등 짧은 라벨 |
| `annual_revenue` | extract_setup_context (LLM) | USD 정수. "$60B" → 60_000_000_000 |
| `rd_budget_ratio` | extract_setup_context (LLM) | 0.0-1.0. "20%" → 0.20 |
| `annual_rd_budget` | extract_setup_context (LLM) | USD 정수. 누락 시 revenue × ratio 자동 도출 |
| `planning_horizon` | extract_setup_context (LLM) | "2026-2030 (5 years)" 형식 |
| `strategic_direction` | extract_setup_context (LLM) | 3-5개 bullet. 동일 LLM 콜에서 함께 생성 |
| `domain` | **자동 도출** (= `industry`) | USPTO / Tavily 검색 query 로 사용. LLM 별도 추출 X |
| `reference_year` | **자동 도출** (planning_horizon 의 종료 연도) | 예: "2026-2030" → 2030 |
| `category_hints` | **default 5종** | Equipment / Material / Process / Architecture / Packaging |
| `total_budget` | Investment Policy textarea | $5B 등. policy 미지정 시 기본 $5B |
| `strategic_priorities` | Investment Policy textarea | First-mover 등 keyword |

> 옛 3개 필드 (`domain` / `reference_year` / `category_hints`) 는 더 이상 LLM 에게 묻지 않고, Company Scenario 에서 자동 도출됩니다. 사용자는 회사 정보 + 전략 방향만 의식하면 됨.

## Review 평가 우선순위 (대폭 단순화 — 두 가지만 본다)

Review LLM 은 단순한 2 가지만 점검 (5축 구조는 backward-compat 유지):

1. **예산 초과 (hard fail)** — `sum(tech_budget_usd) > total_budget` 시 **무조건 REVISE**.
   미만은 OK (-25% 까지 정상, 그 이상 미달이면 "미활용" 경고).
2. **예산 분배 (soft check)** — 한 Tier 또는 한 차년도가 **90% 초과 점유** 시만 issue.

이 두 가지 명확한 결함이 없으면 **ACCEPT**. issues 최대 2개.

### 3중 예산 초과 방지

| 단계 | 동작 |
|---|---|
| **1. Strategist Prompt** | "절대 초과 금지" hard constraint 명시 |
| **2. Strategist 후처리** | LLM 합이 초과해도 비례 축소 — `sum = total_budget` 보장 |
| **3. Review LLM** | 후처리 후에도 초과 발견되면 hard fail |

### Selector-aware Review

Review LLM prompt 에 **Tech Selector 의 큐레이션 결과** 가 함께 박힙니다 — N→K 필터링이 의도적임을 알림:

```
[TECH SELECTOR CURATION — IMPORTANT]
Out of the 8 candidates above, the tech_selector intentionally curated 5.
The following 3 were deliberately dropped:
  - T04 (...): final_score 낮음
  - T06 (...): 카테고리 중복

**DO NOT flag dropped candidates as `missing` / `under_invested`**.
```

→ dropped 후보 누락을 결함으로 오판하지 않음.
→ portfolio_balance 도 K 안에서만 판단.

### total_budget 자동 도출 (Setup)

사용자가 `Annual R&D Budget: $12B` + `Planning Horizon: 5 years` 만 입력해도,
`total_budget = annual_rd_budget × horizon_years = $60B` 으로 자동 계산됩니다
(별도 Investment Policy textarea 미입력 시).

→ `[Session] 💰 total_budget override (company_scenario 우선): annual $12B × 5년 = $60B`

REVISE 시 `refinement.feedback` 가 Agent 1/2/3 모든 시스템 프롬프트에 박혀 다음 iter 산출물에 반영됩니다.

## 최종 보고서 — 3 형식 자동 export

세션 종료 시 [outputs/](outputs/) 폴더에 3 파일 동시 저장:

| 파일 | 용도 |
|---|---|
| `web_<sid>_orchestrator_report.json` | raw 데이터 (재처리 / 연동) |
| `web_<sid>_orchestrator_report.md` | Markdown (GitHub / Notion 붙여넣기) |
| `web_<sid>_orchestrator_report.html` | 단일 HTML (다크 테마 인라인 CSS, 이메일 첨부) |

[report_export.py](report_export.py) 가 LLM 호출 없이 코드로만 렌더.

### 보고서 내용 구조 (3 형식 동일)

1. **Header** — Company / Industry / Horizon / Strategic Direction
2. **🔬 Agent 1** · Technology Candidates (표)
3. **🛣️ Agent 2** · Planned Roadmap (per-tech 카드 + 3-reasoning)
4. **💰 Agent 3** · Investment Strategy (per-tech tier + 예산 + 5축 + 3-reasoning + 리스크 + 자원)
5. **📅 TRM Gantt** (HTML 만) — 차년도별 색칠된 bar + 예산 badge + Reasoning
6. **📅 차년도별 활동 요약** — 1차년도부터 N차년도까지 활동 기술 + 시작 예산 합계
7. **📊 Year × Tech 매트릭스** — 표 형식
8. **✅ Final Review** — decision + issues
9. **📝 Narrative** (LLM 생성 7-섹션, 있을 때만)

### 브라우저로 보기

웹 UI 의 Review 섹션에 ACCEPT 시 다음 3 버튼 자동 표시:

- 📋 **HTML 보고서 새 탭에서 보기** — `/outputs/<file>.html` 직접 열기
- ⬇️ Markdown 다운로드
- ⬇️ JSON 다운로드

server.py 가 `/outputs/` 경로를 정적 마운트해서 별도 서버 셋업 불필요.

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
