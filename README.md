# Technology Roadmap · Multi-Agent System

LLM 기반 4-에이전트 파이프라인으로 **기업의 기술 로드맵을 자동 설계** 하는 시스템.
사용자가 자연어로 도메인을 입력하면 → 후보 기술 발굴 → 의존성/타임라인 역산 →
투자 전략 수립 → TRM 평가 기반 보고서 생성까지 완주.

> **이 문서는 팀 엔트리 포인트.** 
>
> **📖 추천 읽기 순서** (협업자용):
> 1. **(지금 이 문서)** — 전체 아키텍처 + 4 에이전트 역할 + 데이터 흐름 파악
> 2. **[ENVIRONMENT.md](ENVIRONMENT.md)** — 환경 셋업 (Docker 컨테이너 권장)
> 3. **본인 담당 agent 의 README** — 설계 세부
> 4. **[orchestration_agent/README.md](orchestration_agent/README.md)** (필요 시) — 다른 agent 와의 결합 방식 / 보고서 형식
>
> 세부 가이드:
> - 설치 환경 → [ENVIRONMENT.md](ENVIRONMENT.md)
> - CLI / Ablation 실험 → [DEMO.md](DEMO.md)
> - 웹 인터랙티브 데모 → [orchestration_agent/DEMO.md](orchestration_agent/DEMO.md)
> - 각 에이전트 설계 세부 → [tech_analysis_agent/README.md](tech_analysis_agent/README.md) · [roadmap_planner_agent/README.md](roadmap_planner_agent/README.md) · [investment_strategist_agent/README.md](investment_strategist_agent/README.md) · [orchestration_agent/README.md](orchestration_agent/README.md)

---

## 📐 아키텍처

```
Roadmap-Planning-Agent/                  ← GitHub 레포 루트 (이 폴더)
│
├── tech_analysis_agent/                   Agent 1
├── roadmap_planner_agent/                 Agent 2
├── investment_strategist_agent/           Agent 3
├── orchestration_agent/                   Orchestrator
│
├── run_container.sh                       Docker 컨테이너 띄우기 (호스트에서 실행)
├── container_bootstrap.sh                 컨테이너 안 환경 자동 셋업 (uv+ollama+venv+모델 pull)
│
├── .venv/                                 공용 Python 3.10 venv (uv 로 생성)
├── .env                                   공용 환경변수 (각 폴더에서 symlink, gitignored)
├── .env.example                           템플릿 (안전한 default 값)
├── requirements.txt                       통합 의존성 (4개 에이전트 공용)
├── README.md                              ← 이 문서
├── ENVIRONMENT.md                         설치/환경 상세 (Docker 포함)
├── DEMO.md                                시스템 전체 데모 가이드
└── scripts/
    ├── setup.sh                           원샷 세팅 (venv + deps + .env + Ollama)
    ├── run_ablation.sh                    --agent 조합 일괄 실행
    └── smoke_test.py                      LLM 없이 뼈대 검증
```

### 소유권 / 담당

| 폴더 | 담당 | 핵심 책임 |
|------|------|-----------|
| `tech_analysis_agent/`  | 외부 데이터 (USPTO 특허 + Tavily 시장) → 후보 기술 발굴 |
| `roadmap_planner_agent/`  | 의존성 트리 + TRL 역산 → 분기별 타임라인 |
| `investment_strategist_agent/` |  단계(stage) 단위 5-지표 평가 + Tier 도출 |
| `orchestration_agent/` |  위 셋 호출 + TRM 평가 + 최종 보고서 |


---

## 🤖 4개 에이전트 역할

### Agent 1 · Technology Analyst (tech_analysis_agent/)

**입력**: `{domain, reference_year, category_hints}` — 사용자 입력 (Orchestrator 가 파싱)
**출력**: `tech_candidates.json` — 후보 기술 목록 (N개, 최대 10개)

**내부 3 단계** (LangGraph 서브그래프):
1. `patent_agent` — USPTO 특허 데이터 수집 + Claude/Ollama 로 `patent_score` 산출
2. `market_agent` — Tavily 시장/정책/경쟁 데이터 수집 + LLM 으로 `market_score` 산출
3. `aggregator` — `final_score = patent×0.45 + market×0.55` + 필터링 + 정렬

**오프라인 데모 모드**: `USE_MOCK_PATENT=1`, `USE_MOCK_MARKET=1` 설정 시
[tools/mock_data.py](tech_analysis_agent/tools/mock_data.py) 의 합성 데이터로 동작.
Mock 데이터는 반도체 업계의 실제 기술 개념 (EUV, DSA, ALD, GAA, HBM 본딩 등) 30종 템플릿 기반 

**웹 UI**: 이 에이전트만 **자체 FastAPI 웹 UI** 도 가짐
`cd tech_analysis_agent && bash scripts/run.sh` 로 단독 실행 가능.

---

### Agent 2 · Roadmap Planner (roadmap_planner_agent/)

**입력**: `tech_candidates.json` + `reference_year` + `orchestrator_feedback` (선택)
**출력**: `planned_roadmap.json` — 기술별 `{tech_id, phase_name, start_q, target_q, prerequisites, lead_time_quarters, justification}` + `tech_selection` (선별 사유)

**두 가지 설계 모드** — 환경변수 `ROADMAP_DESIGN_MODE` 로 토글:

#### `holistic` (default — spec 의 LLM 통합 모드)
1. **`tech_selector`** — LLM 이 후보 N개 중 핵심 K개를 자율 선별. **5축 큐레이션** (점수 / 트렌드 정합 / 카테고리 균형 / 시점 분포 / 중복 제거). final_score 신뢰 + 단일 차원 의존 금지. **`ROADMAP_TECH_K_MIN`** (default 3) 최소 보장.
2. **`roadmap_designer`** — **LLM 한 번에 통합 처리** (spec System Prompt 그대로):
   - dependency tree (mental model) + TRL-based lead time + Backcasting from boom_quarter
   - `phase_name` + `start_q` + `target_q` + `prerequisites` + `justification` 모두 한 번에
   - Zero-slack 자체 검증 + `reference_year` horizon stagger 분포

→ 자연스러운 horizon 분포 + 의미적 단계 흐름. 결정성 약간 ↓.

#### `hybrid` (옛 모드 — Python 결정성 우선)
1. `tech_selector` (동일)
2. `dependency_analyzer` — LLM 이 의존성 트리 + 양방향 정합 보강
3. `timeline_calculator` — **pure Python** TRL 역산 (TRL 1-3: 5분기 / 4-6: 3분기 / 7-8: 2분기 / 9: 1분기) + Zero-slack + leaf 기술 reference_year 안전망
4. `roadmap_builder` — LLM 이 `phase_name` + `justification` (분기 변경 X), 시간순 단조 증가 룰

→ 결정성 ↑ (TRL lead_time 강제). 단 chain sparse 시 timeline 한 시점 몰림 경향.

**Orchestrator 피드백 반영** (3 채널):
- `shift` — 특정 기술 시작 분기 강제 변경 (cascade 자동)
- `drop`  — 기술 제외 (`dropped=True` 표시)
- `text`  — Orchestrator REVISE 의 자유 텍스트 피드백 → **모든 LLM 노드의 프롬프트에 박힘** (holistic: tech_selector + roadmap_designer / hybrid: tech_selector + dependency_analyzer + roadmap_builder)

**기술명 복구 패치** (hybrid): 작은 LLM 이 `name` 을 할루시네이션 하면 원본 `tech_candidates` 에서 강제 덮어쓰기.

---

### Agent 3 · Investment Strategist (investment_strategist_agent/)

**입력**: `planned_roadmap.json` + `tech_candidates.json` + `investment_policy` (선택)
**출력**: `investment_strategy.json` — stage 당 하나의 전략 객체

**핵심 원칙** : Stage 는 컨테이너 (시간/의존성 그룹), **실제 의사결정 단위는 개별 기술**.

**내부 2 단계**:
1. `stage_aggregator` — **pure Python**. 기술 단위 로드맵을 stage 로 집계
   - 기본 (`--stage-mode phase`): Roadmap Planner 의 `phase_name` 그대로 사용
   - `--stage-mode horizon`: 시작 분기 기준 short-term (≤8Q) / mid-term (≤16Q) / long-term
2. `strategist` — LLM 이 각 stage 에 대해 **두 수준** 동시 산출:
   - **A. Stage narrative**: `stage_assessment` 1~3 문장 (timing / synergy / dependency / scale)
   - **B. Per-tech 평가** (`tech_investments[]`): stage 안 각 기술마다
     - 5-지표 점수 (1-5): `market_opportunity` / `strategic_fit` / `executability` / `uncertainty` / `urgency`
     - **Tier 1 / 2 / 3** — 같은 stage 안에서도 tech 마다 다를 수 있음
     - `investment_attractiveness`, `investment_urgency`, `investment_scope`, `recommended_action`, `rationale[]`, `major_risks[]`, `resource_focus[]`
   - **C. Stage-level 예산 분배**: `stage_budget_ratio` (0.0~1.0, 모든 stage 합 = 1.0). 코드가 자동 정규화 + `stage_estimated_usd = total_budget × ratio` 계산.

**Adaptive LLM 전략** ([_is_strong_llm](investment_strategist_agent/agents/strategist.py)):
- 강한 LLM (Claude / Ollama 27B+) → **Single-call** (모든 stage 한 번에, cross-stage 추론 풍부, max_tokens=16384)
  - 인식되는 모델: `:27b`, `:32b`, `:34b`, `:70b`, `qwen3.5:27b/32b/72b`, `qwen3:27b/32b/72b`, `llama3.1:70b`, `qwen2.5:32b/72b`
- 작은 LLM (Ollama 8B 이하) → **Per-stage 분할** (stage 별 독립 콜 + cross-stage summary 동봉, JSON 안정성 우선)
- Single-call 실패 시 → per-stage 자동 폴백 (self-healing)
- 환경변수 `STRATEGIST_LLM_STRATEGY=single_call` 또는 `=per_stage` 로 강제 override 가능

**Orchestrator REVISE feedback 반영**: `orchestrator_feedback.text` 채널이 strategist 의 LLM 프롬프트에 박혀 다음 iter 의 Tier / 예산 비율 조정에 직접 사용됨.

**외부 API 호출 없음** — Agent 1/2 의 출력 + Problem Frame 의 `investment_policy` 만으로 추론.

---

### Orchestrator · Orchestration Agent (orchestration_agent/)

**파이프라인 제어 + TRM 평가**. 두 phase 로 동작:

#### Phase A · Setup (Problem Setup + Task Orchestration)
계획서의 "Problem Setup" 단계 — 사용자 자연어 입력을 **LLM 이 해석** 하여 구조화된 문제로 변환.

- **Intake LLM** ([session.py:112](orchestration_agent/interactive/session.py#L112) `extract_intake()`):
  사용자 입력 (예: *"2030년까지의 2nm 파운드리 로드맵 그려줘"*) → `{domain, reference_year, category_hints}`
- **Policy LLM** ([session.py:207](orchestration_agent/interactive/session.py#L207) `extract_investment_policy()`):
  자연어 정책 (예: *"예산 5B, 균형 위험"*) → `{risk_appetite, investment_horizon, total_budget, strategic_priority}`
- → **ProblemFrame** 으로 통합: `industry`, `company_type`, `time_horizon`, `total_budget`, `objective`, `strategic_priorities[]`, `future_trend_summary`
- **Task Orchestration**: `active_agents` 결정 (`--agent "1 2 3"` 등) + 각 에이전트에게 역할 + 입력 + 지시사항 배분
- (CLI 모드 `python main.py --domain ... --reference-year ...` 처럼 인자로 직접 주면 intake LLM 호출 없이 ProblemFrame 직접 구성 가능 — 이 경우만 LLM 없음)

#### Phase B · Review (LLM)
Agent 1/2/3 결과를 받아 **TRM 5-축 평가**:

| 축 | 검사 항목 |
|---|-----------|
| **Feasibility** | 예산/일정 실행 가능성 |
| **Sequencing** | 선행 기술이 먼저 배치됐는가 |
| **Strategic Alignment** | 회사 역량 + 미래 트렌드 정합 (0-1 score) |
| **Investment Rationality** | 과잉/부족 투자 tech_id 식별 |
| **Portfolio Balance** | 단기/장기 균형, 리스크 분산 (0-1 score) |

결과 → `decision: "ACCEPT" | "REVISE"`
- **ACCEPT** → 7-섹션 한국어 TRM 보고서 생성 → 종료
- **REVISE** → `refinement.rerun_agents` + `feedback` 로 재실행 지시 — feedback 은 **Agent 1 / 2 / 3 모든 에이전트의 LLM 프롬프트에 자동 박힘** (각 sibling 의 `_format_orchestrator_feedback()` helper)

**REVISE 루프**: `MAX_ORCHESTRATOR_ITERATIONS` (기본 2) 까지 반복.
상한 도달 시 강제 ACCEPT + 전용 LLM 콜로 보고서 채움. 잔여 issue 는 `feasibility_and_risk` 섹션에 명시.

**최종 TRM 보고서 형식** (`orchestrator_report.json` 의 `review.report`):
- **7-섹션 한국어 narrative**: `executive_summary` / `technology_strategy` / `roadmap_structure` / `investment_strategy` / `trend_alignment` / `feasibility_and_risk` / `expected_outcomes`
- **인라인 출처 인용**: 각 수치/판정 뒤에 `[A1]` (Tech Analyst), `[A2]` (Roadmap Planner), `[A3]` (Strategist) 마커
  - 예: *"T01 최종점수 88.71 [A1] 을 Tier 1 [A3] 로 분류, 2026 Q3 → 2027 Q1 [A2] 양산 전환"*
- **`artifacts_summary` 부록** (8번째 섹션): Agent 1/2/3 의 핵심 출력 raw 데이터 + 집계 insights (Tier 분포, top tech, 카테고리 분포, dependency edges 등). UI 에서 `[A?]` 마커 따라 추적 가능.

**subprocess 기반**: 각 sibling 은 자기 `config.py`/`state.py` 를 가져서 같은 프로세스에서 import 하면 네임 충돌. Orchestrator 는 `subprocess.Popen` 으로 각 에이전트를 별도 Python 프로세스로 호출해 완전 격리. 웹 데모에서는 stdout 을 PIPE 로 받아 SSE 로 스트리밍.

---

## 🔄 전체 파이프라인 흐름

```
[User 입력: "2030년까지의 2nm 파운드리 로드맵을 그려줘"]
        │
        ▼
  Orchestrator Setup             ← Problem Setup (intake/policy LLM) + Task Orchestration
  (Phase A)                         자연어 → ProblemFrame: industry=반도체, budget=$5B, priorities=[...]
        │
        ▼
  ┌─ Agent 1 · Technology Analyst  [subprocess]
  │     - USPTO 특허 + Tavily 시장 (또는 mock_data.py)
  │     - Claude/Ollama 분석
  │     - final_score 산출
  │     → tech_candidates.json (6~10 건)
  │
  ├─ Agent 2 · Roadmap Planner    [subprocess]
  │     - dependency_analyzer (LLM): 의존성 트리 + layer
  │     - timeline_calculator (python): TRL 역산 + Zero-slack
  │     - roadmap_builder (LLM): phase_name + justification
  │     → planned_roadmap.json (기술 단위)
  │
  └─ Agent 3 · Investment Strategist [subprocess]
        - stage_aggregator (python): 기술 → stage 집계
        - strategist (LLM): 5-지표 평가 + Tier 도출
        → investment_strategy.json (stage 단위)
        │
        ▼
  Orchestrator Review (LLM)      ← TRM 5-축 평가 (Phase B)
        │
        ├── ACCEPT → 7-섹션 보고서 생성 → orchestrator_report.json
        │
        └── REVISE → refinement.rerun_agents 재실행
                     │ (파이프라인 순서상 가장 앞선 agent 부터 끝까지)
                     │
                     ▼
                  다시 Review ... (최대 MAX_ITERATIONS 회)
                     │
                     ▼
                  상한 도달 시 강제 ACCEPT + 전용 LLM 콜로 보고서 작성
                     │
                     ▼
                   종료
```

### 데이터 흐름 (시장 데이터 예시)

```
mock_data.py / Tavily API          (시장 규모, CAGR, 정책, 경쟁)
      ↓
market_agent (LLM)                  market_score: 84.5,
                                    expected_market_boom_quarter: "2028 Q1"
      ↓
aggregator                          tech_candidates[i] 에 포함
      ↓ (subprocess 경계, JSON 파일)
Agent 2, 3 가 tech_candidates[i].market_score / boom_quarter 활용
      ↓
Agent 3 _normalize_tech_analysis    market_score → market_attractiveness (high/med/low)
      ↓ (LLM 프롬프트)
Stage 의 5-지표 점수 (market_opportunity 등) → Tier 결정
```

**요약**: 외부 시장 데이터는 **Agent 1 에서만 수집**되고, 이후 Agent 2/3 는 Agent 1 의 결과를 JSON 으로 받아 해석. Agent 3 전용 외부 API 호출은 없음.

---

## 🚀 실행 방법

### 1단계 · 최초 1회만 — 환경 세팅

#### 옵션 A: Docker 컨테이너 (권장 — GPU 호스트)

호스트 환경을 건드리지 않고, 컨테이너 안에서 끝까지 자동 셋업.

```bash
# 호스트에서
cd /path/to/Roadmap-Planning-Agent           # 레포 폴더 안
bash run_container.sh                        # nvcr.io/nvidia/pytorch + GPU 마운트

# 컨테이너 안에서 (한 줄)
bash /workspace/26-tech-roadmap/Roadmap-Planning-Agent/container_bootstrap.sh
```

`container_bootstrap.sh` 가 자동 처리: uv/ollama 설치, GPU 멀티 분산 환경변수, ollama 데몬 기동, venv + requirements + 모델 pull (`.env` 의 `OLLAMA_MODEL` 읽음). **idempotent** — 재실행 안전. 자세한 옵션은 [ENVIRONMENT.md](ENVIRONMENT.md#docker-컨테이너-셋업-권장) 참고.

#### 옵션 B: 직접 설치 (호스트 또는 기존 venv)

```bash
cd /path/to/Roadmap-Planning-Agent
bash scripts/setup.sh
```

`setup.sh` 가 하는 일:
1. `uv venv --python 3.10 .venv` (공용 venv 생성)
2. 루트 `requirements.txt` 설치 (4개 에이전트 공용 의존성)
3. `.env` 없으면 `.env.example` 에서 복사 (`OLLAMA_NO_THINK=1`, `OLLAMA_FORMAT_JSON=1` 등 Qwen3.5 안전 default 포함)
4. 각 sibling 폴더에 `.env -> ../.env` symlink 생성
5. (기본 Ollama 모드) 데몬 기동 + `.env` 의 `OLLAMA_MODEL` pull

Anthropic Claude 쓰려면:
```bash
bash scripts/setup.sh --anthropic    # Ollama 스킵
# .env 에 LLM_PROVIDER=anthropic, ANTHROPIC_API_KEY=sk-ant-... 수동 입력
```

상세한 수동 설치는 [ENVIRONMENT.md](ENVIRONMENT.md) 참고.

### 2단계 · venv 활성화

```bash
source .venv/bin/activate
```

Ollama 가 user-local 에 있다면:
```bash
export PATH="$HOME/.local/ollama/bin:$PATH"
```

### 3단계 · 실행 (세 가지 방법 중 선택)

#### A) CLI — 원샷 파이프라인

```bash
cd orchestration_agent
python main.py                           # 세 Agent 모두 ON
python main.py --agent "1 2"             # Agent 3 OFF (비교용)
python main.py --agent "1" --out-prefix "only1_"  # 출력 prefix 지정
```

출력: `orchestration_agent/outputs/` 에 4개 JSON:
- `<prefix>tech_candidates.json` (Agent 1)
- `<prefix>planned_roadmap.json` (Agent 2)
- `<prefix>investment_strategy.json` (Agent 3)
- `<prefix>orchestrator_report.json` (최종 TRM 보고서)

#### B) 웹 데모 — 브라우저 인터랙티브

```bash
cd orchestration_agent
bash scripts/run.sh                      # http://localhost:8000
# 또는 포트 변경: PORT=9000 bash scripts/run.sh
```

브라우저에서 할 것:
1. 상단에서 **Agent 1/2/3 체크박스** 로 활성화할 에이전트 선택
2. 하단 입력창에 자연어 입력 (예: `"2030년까지의 2nm 파운드리 로드맵을 그려줘"`)
3. **▶ 전송** 클릭
4. 왼쪽에서 실시간 로그, 오른쪽에서 구조화된 결과 (IN/OUT 카드 · 간트 차트 · Tier 카드 · 7-섹션 보고서) 관찰

GPU 가 있으면 Ollama 가 자동 사용, 약 3–6분 소요.

#### C) Agent 유무 A/B 비교 (Ablation)

```bash
bash scripts/run_ablation.sh             # full / noA3 / noA2 / noA1 한 번에
bash scripts/run_ablation.sh full noA3   # 일부만
```

출력 파일 prefix 가 다르므로 `orchestration_agent/outputs/` 에 네 세트 JSON 쌓임 → `diff` 해서 에이전트 유무에 따른 보고서 차이 비교 가능.

---

## 🧪 개별 에이전트 독립 실행 (디버깅용)

각 에이전트는 standalone 으로도 실행 가능:

```bash
# Agent 1 (도메인 하드코딩된 main.py)
cd tech_analysis_agent && python main.py

# Agent 2 (Agent 1 의 출력 JSON 을 입력으로)
cd roadmap_planner_agent && python main.py \
  --input ../tech_analysis_agent/output_tech_candidates.json

# Agent 3 (Agent 2 의 출력 JSON 을 입력으로)
cd investment_strategist_agent && python main.py \
  --roadmap ../roadmap_planner_agent/output_planned_roadmap.json \
  --tech    ../tech_analysis_agent/output_tech_candidates.json
```

Orchestrator 경유와 같은 결과.

---

## 🛠️ LLM 없이 뼈대 검증

LLM 호출 없이 TRL 역산, stage 집계, Orchestrator Setup 등 순수 Python 로직만 검증:

```bash
source .venv/bin/activate
python scripts/smoke_test.py
```

---

## 📦 출력 파일 구조

### `orchestrator_report.json` — 최종 산출물

```json
{
  "problem_frame": {
    "industry": "...",
    "company_type": "Tier-1 IDM / Foundry",
    "time_horizon": "2025-2030",
    "total_budget": 5000000000,
    "objective": "...",
    "strategic_priorities": [ "...", "...", "..." ],
    "future_trend_summary": "..."
  },
  "active_agents": ["1", "2", "3"],
  "iteration": 2,                        ← Review 수행 횟수
  "review": {
    "decision": "ACCEPT",                ← "ACCEPT" | "REVISE"
    "trm_assessment": {                  ← 5-축 평가
      "feasibility":            { "budget_feasible": true, "schedule_feasible": true, "comment": "..." },
      "sequencing":             { "dependency_valid": true, "comment": "..." },
      "strategic_alignment":    { "company_fit": 0.8, "future_trend_alignment": 0.9, "comment": "..." },
      "investment_rationality": { "over_invested": [], "under_invested": [], "comment": "..." },
      "portfolio_balance":      { "short_long_balance": 0.7, "risk_balance": 0.65, "comment": "..." }
    },
    "issues": [],
    "refinement": { "rerun_agents": [], "feedback": [] },
    "report": {                          ← 7-섹션 한국어 최종 보고서
      "executive_summary":    "...",
      "technology_strategy":  "...",
      "roadmap_structure":    "...",
      "investment_strategy":  "...",
      "trend_alignment":      "...",
      "feasibility_and_risk": "...",
      "expected_outcomes":    "..."
    },
    "diagnostic_summary": ""
  },
  "artifact_paths": { ... 세 에이전트 결과 JSON 절대 경로 ... }
}
```

REVISE 시 `report` 가 비워지고 `diagnostic_summary` 만 채워짐. 단, iteration 상한 도달 시 **강제 ACCEPT + 전용 LLM 콜** 로 report 를 채움.

---

## ⚙️ 환경 변수 (`.env`)

루트 `.env` 한 파일만 수정하면 4개 폴더 모두 반영 (symlink 덕분).

| 키 | 의미 | 기본값 |
|----|------|--------|
| `LLM_PROVIDER` | `ollama` / `anthropic` | `ollama` |
| `ANTHROPIC_API_KEY` | Claude 쓸 때 필수 | (비어있음) |
| `CLAUDE_MODEL` | Claude 모델 | `claude-sonnet-4-20250514` |
| `OLLAMA_MODEL` | 로컬 모델 | `llama3.1:8b` |
| `OLLAMA_BASE_URL` | Ollama 서버 | `http://localhost:11434` |
| `TAVILY_API_KEY` | Tavily 시장 검색 API key (`USE_MOCK_MARKET=0`이면 필요) | (비어있음) |
| `PATENTSVIEW_API_KEY` | PatentsView PatentSearch API key (`USE_MOCK_PATENT=0`이면 필요) | (비어있음) |
| `USE_MOCK_PATENT` | PatentsView mock 사용 | `0` |
| `USE_MOCK_MARKET` | Tavily mock 사용 | `0` |
| `MAX_ORCHESTRATOR_ITERATIONS` | Review REVISE 루프 상한 | `2` |
| `SUBPROCESS_TIMEOUT_SEC` | sibling 호출 timeout | `900` |

---

## 🧯 자주 겪는 문제

| 증상 | 해결 |
|------|------|
| `ModuleNotFoundError: No module named 'langchain_ollama'` | venv 활성화 안 됨 — `source .venv/bin/activate` |
| `ollama: command not found` | user-local 설치 시 PATH — `export PATH=$HOME/.local/ollama/bin:$PATH` |
| `ConnectionError: http://localhost:11434` | Ollama 데몬 미기동 — `ollama serve > /tmp/ollama.log 2>&1 &` |
| 기술명이 깨져 나옴 (`하분울을 ...`) | 작은 모델 할루시네이션 — 이미 `dependency_analyzer.py` 에서 post-process 로 복구 중 |
| Review 가 늘 REVISE → iter 상한 도달 | 작은 모델 한계 — 큰 모델 (`qwen2.5:14b`, Anthropic Claude) 로 교체 |
| subprocess timeout | `.env` 의 `SUBPROCESS_TIMEOUT_SEC` 증가 |
| JSON 파싱 실패 | `get_llm(json_mode=True)` 가 이미 적용됨. 큰 모델로 교체 고려 |

---

## 🧩 Agent 유무에 따른 보고서 품질 비교

`--agent` 토글로 A/B 실험. 각 Agent 가 OFF 되면:

| Agent OFF | 대체 산출물 | 보고서 영향 |
|-----------|------------|------------|
| **Agent 1** | 더미 후보 5개 (`D01`-`D05`) + 고정 market_context | `technology_strategy` / `trend_alignment` 얕음 · 근거 빈약 |
| **Agent 2** | Flat 로드맵 (모든 기술이 단일 phase, 의존성 없음) | `sequencing.dependency_valid=false` · `roadmap_structure` 단조로움 |
| **Agent 3** | 빈 `stages` / `investment_strategy` | `investment_strategy` 공란 · `investment_rationality` 평가 불가 |

Orchestrator Review 프롬프트는 어떤 Agent 가 OFF 되었는지 명시적으로 받아 degraded 상태로 평가하므로, **같은 질의 + 다른 조합** 의 `orchestrator_report.json` 을 비교하면 각 에이전트가 만드는 가치를 정량 확인 가능.

### Ablation 실험의 무한 REVISE 방지 (SCOPE RULE + 가드레일)

Agent 를 OFF 한 채로 실험하면 Review LLM 이 "Missing investment strategy" 같은
부재 자체를 issue 로 잡아 계속 REVISE 하는 현상이 있을 수 있음. 이를 막기 위해
**이중 안전장치**가 들어가 있음:

1. **시스템 프롬프트 `[SCOPE RULE · DISABLED AGENTS]` 섹션**
   ([agents/orchestrator.py](orchestration_agent/agents/orchestrator.py))
   - 비활성 agent 의 부재를 `issues` 로 올리지 말 것
   - `rerun_agents` 에 비활성 agent 포함하지 말 것
   - 평가 불가 TRM 축은 `comment: "N/A (agent disabled)"` 로 표기
   - 문제가 전부 비활성 agent 에서 기인하면 REVISE 대신 ACCEPT

2. **파이프라인 가드레일** ([run_orchestrator_review](orchestration_agent/agents/orchestrator.py))
   - LLM 이 규칙을 무시하고 OFF agent 를 rerun 지정해도 **자동 필터링**
   - 필터링 후 rerun 대상이 비어있는데 여전히 REVISE 면 **ACCEPT 로 강제 전환**
   - ACCEPT 전환 시 `generate_final_report()` 가 7-섹션 보고서를 전용 LLM 콜로 생성

즉 `--agent "1 2"` 실험은 **반드시 유한 시간 안에 수렴**하고 보고서도 채워짐 —
비활성 agent 로 인한 무의미한 iteration 소모 없음.

---

## 📚 추가로 읽을 것

- **[DEMO.md](DEMO.md)** — CLI 데모 · ablation 실험 · 출력 파일 구조 상세
- **[ENVIRONMENT.md](ENVIRONMENT.md)** — 수동 설치 · Ollama user-local 설치 · 트러블슈팅
- **[orchestration_agent/DEMO.md](orchestration_agent/DEMO.md)** — 웹 인터랙티브 데모 사용법 · SSE 이벤트 스키마 · 아키텍처
- **[tech_analysis_agent/README.md](tech_analysis_agent/README.md)** — Agent 1 내부 구조 (patent/market tools, aggregator)
- **[tech_analysis_agent/DEMO.md](tech_analysis_agent/DEMO.md)** — Agent 1 전용 웹 데모 (HITL 체크포인트 포함)
- **[roadmap_planner_agent/README.md](roadmap_planner_agent/README.md)** — Agent 2 의 TRL 역산 알고리즘
- **[investment_strategist_agent/README.md](investment_strategist_agent/README.md)** — Agent 3 의 5-지표 spec + Tier 정의

---

## 🙋 FAQ

**Q. 왜 subprocess 로 sibling 을 호출해? 그냥 import 하면 안 돼?**
A. 네 에이전트가 모두 `config.py`, `state.py`, `llm_factory.py` 를 자기 폴더에 가짐.
Python `sys.modules` 캐시는 top-level 이름 기준이라 import 하면 네임 충돌.
subprocess 는 완전 격리 + 각자 다른 `.env` 가능 + 실제 멀티 서비스 배포와 동일 패턴.

**Q. `iteration: 2` 인데 `review.decision: "ACCEPT"` 이면 뭘 의미해?**
A. iter 1 에서 REVISE 나왔고, rerun_agents 재실행한 뒤 iter 2 에서도 REVISE.
하지만 `MAX_ORCHESTRATOR_ITERATIONS=2` 상한 도달해서 **강제 ACCEPT** 로 전환한 거.
최종 보고서는 전용 LLM 콜로 채워짐.

**Q. Agent 3 도 외부 시장 데이터를 써야 하지 않아?**
A. 현 설계는 **Agent 1 의 시장 데이터만 사용**. Agent 3 은 `tech_candidates.market_score` →
`market_attractiveness (high/med/low)` 로 정규화해서 LLM 프롬프트에 넣음.
경쟁사 투자 동향 / VC 투자 데이터 같은 걸 추가하고 싶으면 새 tool + mock 추가 필요.

**Q. `MAX_ORCHESTRATOR_ITERATIONS` 를 늘리면 더 좋은 보고서 나와?**
A. 작은 모델(8B 급)은 판정이 과하게 엄격해서 아무리 돌려도 REVISE 가 많음.
2–3 회에서 멈추고 보고서 폴백에 의존하는 게 현실적.
더 좋은 보고서 원하면 **큰 모델로 교체**가 근본 해결.

**Q. 내 도메인은 반도체가 아닌데? (배터리, 바이오 등)**
A. `--domain "..."`, `--industry "..."`, `--objective "..."`, `--priorities "a|b|c"` 로 override.
단, 현재 mock_data.py 는 반도체 기술 개념만 템플릿화되어 있으므로, 다른 도메인은
**실제 API** (Tavily 키 + USPTO 접근) 로 동작시키는 게 좋음.

---

## 👥 기여 가이드

- 각자 자기 폴더 (`<agent>_agent/`) 만 수정하면 merge 충돌 거의 없음
- 루트 공용 파일 (`.env.example`, `requirements.txt`, `DEMO.md` 등) 수정 시 팀에 알림
- Agent 간 인터페이스는 JSON 스키마로 고정:
  - `tech_candidates.json`: `{market_context, tech_candidates[]}`
  - `planned_roadmap.json`: `{market_context, planned_roadmap[], dependency_tree?}`
  - `investment_strategy.json`: `{market_context, stages[], investment_strategy[]}`
  - 스키마 변경 시 사전 협의 필수
- 새 LLM 모델 테스트는 `.env` 의 `OLLAMA_MODEL` 또는 `CLAUDE_MODEL` 바꾸면 됨
