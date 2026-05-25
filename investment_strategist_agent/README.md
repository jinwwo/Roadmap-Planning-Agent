# Investment Strategist Agent (Agent 3)

앞선 두 에이전트 (Technology Analyst / Roadmap Planner) 의 결과를 종합해,
**로드맵 단계(stage) 단위로** 투자 매력도·시급성·Tier·범위·권고안을
도출하는 에이전트.

```
Tech-Analysis-Agent/
├── tech_analysis_agent/              ← Agent 1 (Technology Analyst)
├── roadmap_planner_agent/            ← Agent 2 (Roadmap Planner)
├── investment_strategist_agent/      ← 본 폴더 (Agent 3)
└── orchestration_agent/              ← Orchestrator (4-Agent 통합 + TRM 평가)
```

## 핵심 원칙

- **투자 의사결정의 실제 단위는 개별 기술 (tech)** 이다. Stage 는 컨테이너 (시간/의존성 그룹) 역할.
- 각 stage 에 대해 **두 수준** 의 작업을 수행:

  **① Stage 통합 판단 (narrative)** — 점수 X, 1~3문장 서술
  - Timing: stage period vs `expected_boom_quarter` (적시인가?)
  - Synergy: stage 안 기술들 함께 진행되어야 하는가?
  - Dependency: 후속 stage 와의 의존 관계
  - Scale: 자원 집약도

  **② 각 기술별 투자 평가 (실제 의사결정)** — Stage 통합 판단을 컨텍스트로 두고, 각 tech 마다:
  - 5-지표 점수 (1~5):
    - `market_opportunity` · `strategic_fit` · `executability` · `uncertainty` · `urgency`
  - `recommended_investment_tier` (Tier 1/2/3) — **같은 stage 안에서도 tech 마다 다를 수 있음**
  - `investment_attractiveness`, `investment_urgency`
  - `recommended_action`, `rationale`, `major_risks`, `resource_focus`

  **③ Stage 단위 예산 분배** — LLM 이 각 stage 의 `stage_budget_ratio` (0.0~1.0) 결정
  - 모든 stage 의 합이 1.0 이 되도록 코드가 자동 정규화
  - `stage_estimated_usd = total_budget × stage_budget_ratio` 자동 계산
  - per-tech 분배는 안 함 (Tier 1/2/3 라벨이 사실상 우선순위 차등)

> 같은 stage 안의 두 기술이 다른 Tier 를 받을 수 있다. 예: 1단계 R&D 안에서 T01 멀티빔은 Tier 1 (시장 거대 + 즉시 투자), T02 극저온 에칭은 Tier 2 (조건부).

### 입력 처리 원칙 — 변환 없이 raw 전달

**Agent 1 (Technology Analyst) 와 Agent 2 (Roadmap Planner) 의 출력을 그대로 LLM 에 전달**합니다.
점수→등급 변환이나 rationale 추출 같은 사전 가공은 하지 않습니다.
LLM 이 raw 점수 (0~100), TRL (1~9), rationale 의 [Market]/[Patent] 본문을 모두 보고
맥락 종합으로 평가 점수를 매깁니다.

각 stage 에 첨부되는 `tech_candidates` 는 Agent 1 원본 그대로:
```json
{
  "tech_id": "T01",
  "name": "멀티빔 e-beam 검사",
  "category": "Equipment",
  "trl": 4,                                   // raw 정수
  "market_score": 84.5,                       // raw 0~100
  "patent_score": 81.5,
  "final_score": 83.15,
  "expected_market_boom_quarter": "2028 Q1",
  "rationale": "[Patent] ... [Market] $117.1B 시장 ...19.9% CAGR ..."
}
```

## Tier 정의

| Tier | 의미 | 적용 상황 |
|------|------|----------|
| **Tier 1** — 적극 투자 | 선제적 · 우선 투자 | 시장 기회 크고 실행 가능성 충분 |
| **Tier 2** — 선택적 투자 | 조건부 · 단계적 · 마일스톤 기반 | 유망하나 실행 불확실성 있음 |
| **Tier 3** — 탐색적 투자 | 소규모 파일럿 · 모니터링 · 옵션 확보 | 장기 · 불확실성 높음 |

## 입력 흐름 (전체 정리)

### Step 1 — Pipeline 이 2개 파일에서 3가지 데이터 추출
([orchestration_agent/pipeline.py:413-426](../orchestration_agent/pipeline.py#L413-L426))

| 추출 데이터 | 출처 파일 | 출처 Agent |
|---|---|---|
| `planned_roadmap` | `planned_roadmap.json` | Agent 2 직접 산출 |
| `market_context` | `tech_candidates.json` | Agent 1 직접 산출 |
| `tech_candidates` | `tech_candidates.json` | Agent 1 직접 산출 |

> `market_context` 는 Agent 1 의 원본 파일에서 직접 읽습니다 (single source of truth).
> Agent 2 도 `planned_roadmap.json` 에 passthrough 로 갖고 있지만 사용하지 않음.

`investment_policy = {risk_appetite, investment_horizon, total_budget, strategic_priority}`
는 다음 중 한 가지 경로로 구성됩니다:

| 실행 모드 | 설정 방법 |
|---|---|
| **웹 데모** ([orchestration_agent/server.py](../orchestration_agent/server.py)) | 메인 textarea 에 자연어로 적기 (예: *"Tier-1 IDM Foundry, 예산 $5B, 위험 균형, horizon 균형, first-mover 우선"*) → 서버에서 `extract_investment_policy()` LLM 콜이 4 필드 자동 추출 |
| **CLI · Orchestrator** ([orchestration_agent/main.py](../orchestration_agent/main.py)) | `--budget`, `--priorities` 등 argparse 플래그 |
| **CLI · Standalone** ([main.py](main.py)) | `--risk` / `--horizon` / `--total-budget` / `--priority` 옵션 |

미지정 시 기본값: `risk_appetite=medium`, `investment_horizon=balanced`, `total_budget=5000000000`, `strategic_priority=균형 preset (단기 사업화 + 기반 기술 + 장기 베팅)`.

### Step 2 — phase_name 으로 stage 그룹핑
([aggregate_stages](agents/stage_aggregator.py))

```
phase_name="1단계: 기반 R&D"  →  stage 1 (T01, T02 묶임)
phase_name="2단계: 공정 통합"  →  stage 2 (T03, T04, T05 묶임)
```
※ Agent 3 가 stage 를 새로 만드는 게 아님. **Agent 2 가 만든 phase 수만큼 stage 가 자동 생성**.

각 stage 에 해당 phase 에 속한 tech 들의 **Agent 1 원본 정보를 그대로** 첨부.

### Step 3 — LLM 에게 가는 최종 입력 (3 섹션 user prompt)

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[Market Context]                                    ← 전체 시장
{
  "target_market": "반도체",
  "expected_boom_quarter": "2028 Q1"
}

[Investment Policy]                                  ← 웹 폼 / CLI 에서 직접 입력
{
  "risk_appetite": "medium",                         ← low / medium / high
  "investment_horizon": "balanced",                  ← short / balanced / long
  "total_budget": 5000000000,                        ← 숫자 (USD)
  "strategic_priority": [...]                        ← 회사 전략 우선순위 키워드
}

[Roadmap Stages] — 각 stage 가 하나의 투자 판단 단위
[
  {
    "stage": "1단계: 기반 R&D",
    "period": "2026 Q3 - 2027 Q4",
    "goal": "1단계: 기반 R&D",
    "technologies": ["멀티빔 e-beam 검사", ...],
    "tech_candidates": [                                   ← Agent 1 원본 그대로
      {
        "tech_id": "T01",
        "name": "멀티빔 e-beam 검사",
        "category": "Equipment",
        "trl": 4,
        "market_score": 84.5,
        "patent_score": 81.5,
        "final_score": 83.15,
        "expected_market_boom_quarter": "2028 Q1",
        "rationale": "[Patent] ... [Market] $117.1B ...19.9% CAGR ..."
      },
      { ... }
    ]
  },
  { ... stage 2 ... },
  { ... stage 3 ... }
]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### Step 4 — LLM 이 산출

각 stage 마다 다음 두 가지를 동시에 산출:

**A. Stage 통합 판단 (narrative)** — `stage_assessment` 1~3문장
- timing / synergy / dependency / scale 종합 판단

**B. 각 기술별 투자 평가 (tech_investments[])** — stage 의 tech 마다 하나씩
- 5-지표 점수 (1~5): market_opportunity / strategic_fit / executability / uncertainty / urgency
- Tier (Tier 1/2/3) — **같은 stage 안에서도 tech 마다 다를 수 있음**
- investment_attractiveness / investment_urgency / investment_scope
- recommended_action, rationale, major_risks, resource_focus

### Step 5 — LLM 강도별 자동 분기 (Adaptive Strategy)

[`run_strategist`](agents/strategist.py) 가 LLM 강도를 감지해서 **두 전략 중 하나로 자동 분기** 합니다 ([`_is_strong_llm()`](agents/strategist.py)).

| LLM 환경 | 자동 선택 | 이유 |
|---|---|---|
| **Anthropic Claude (sonnet/opus/haiku 4.x+)** | ⭐ Single-call | 16K 출력 안정 + JSON strict + 풍부한 cross-stage 추론 |
| **Ollama 27B+** (아래 패턴 매칭) | ⭐ Single-call | 큰 모델은 single-call 처리 가능 |
| **Ollama 9B 이하 (기본 데모)** | ✅ Per-stage 분할 | 출력 토큰 제한 / JSON 약함 → 안정성 우선 |

**Single-call 로 인식되는 Ollama 모델** ([strategist.py:_is_strong_llm](agents/strategist.py)):

| 카테고리 | 매칭 패턴 |
|---|---|
| 크기 패턴 | `:27b`, `27b-`, `:32b`, `32b-`, `:34b`, `34b-`, `:70b`, `70b-` |
| 명시적 (Gemma / Qwen) | `gemma3:27b`, `qwen3:27b`, `qwen3:32b`, `qwen3:72b`, `qwen3.5:27b`, `qwen3.5:32b`, `qwen3.5:72b` |
| 명시적 (Qwen2.5 / Llama 3.1) | `qwen2.5:32b`, `qwen2.5:72b`, `llama3.1:70b` |

→ 즉 27B 이상이면 자동으로 single-call. Qwen3 / Qwen3.5 는 명시적으로 등록되어 있어 모델명만 봐도 인식 가능 (단 `OLLAMA_NO_THINK=1` 권장 — 자세한 건 [ENVIRONMENT.md](../ENVIRONMENT.md) 참고).

**환경변수 override**: `STRATEGIST_LLM_STRATEGY=single_call` 또는 `per_stage` 로 강제 지정 가능 (디버깅·실험용).

#### Single-call 전략 (강한 LLM)
- 모든 stage 한 번에 LLM 에 전달 (max_tokens=16384)
- Cross-stage 추론 풍부 (Tier 균형, 예산 분배 등)
- **Self-healing**: 실패 시 → 자동으로 per-stage 폴백

#### Per-stage 분할 전략 (작은 LLM)
- Stage 별 독립 LLM 콜 (max_tokens=4096 / 콜)
- 각 콜에 `[ALL ROADMAP STAGES SUMMARY]` 동봉 → cross-stage 맥락 부분 보존
- **에러 격리** — 한 stage 실패 ≠ 전체 실패
- 단일 stage 만 placeholder, 나머지 정상 진행

```
[강한 LLM 감지]
   ↓
Single-call 시도
   ↓ 실패 시
Per-stage 자동 폴백 (Self-healing)
   ↓ 일부 stage 실패 시
해당 stage 만 placeholder, 다른 stage 정상
```

### Backward Compatibility — LLM 변동성 대응

[`_coerce_strategy`](agents/strategist.py) 가 LLM 의 다양한 출력 형식을 모두 흡수:

| LLM 출력 패턴 | 처리 |
|---|---|
| 새 스키마 `tech_investments[]` 정상 | ✅ 그대로 사용 |
| 옛 스키마 (stage-level evaluation_scores) | ⭐ Salvage: stage 평가를 모든 tech 에 복제 |
| 일부 tech 누락 | placeholder 생성 (입력 tech 와 길이 일치 보장) |
| 완전 실패 (exception) | 모든 tech placeholder + 다음 stage 계속 진행 |

### 한 그림으로

```
┌── Agent 1 ──┐         ┌── Agent 2 ──┐
│ tech_       │         │ planned_    │
│ candidates  │         │ roadmap     │
│ .json       │         │ .json       │
│             │         │             │
│  raw 점수   │         │  phase_name │
│  + rationale│         │  + start_q  │
│  + boom_Q   │         │  + target_q │
│             │         │  + prereq   │
└──────┬──────┘         └─────┬───────┘
       │                       │
       │                       ▼
       │        ┌─ aggregate_stages (Pure Python) ─┐
       │        │  phase_name 으로 그룹핑            │
       │        │  → stages[] (period, goal 등)     │
       │        └──────────┬─────────────────────────┘
       │                   │
       └────────┬──────────┘
                ▼
   ┌─ LLM User Prompt (3 섹션) ───────┐
   │ ① [Market Context]                │
   │ ② [Investment Policy]             │
   │ ③ [Roadmap Stages]                │
   │     각 stage 안에 tech_candidates │
   │     (Agent 1 원본 그대로)         │
   └────────────┬─────────────────────┘
                ▼
       Strategist LLM
        ┌──────────────────────────────┐
        │ Stage 1 :                    │
        │   stage_assessment           │
        │     (narrative)              │
        │   tech_investments []        │
        │     ├ T01: 5축 점수 + Tier...│
        │     ├ T02: 5축 점수 + Tier...│
        │     └ ...                    │
        │ Stage 2 : (반복)             │
        └──────┬───────────────────────┘
               ▼
        investment_strategy[]
```

## 파일 구조

| 파일 | 역할 | LLM |
|------|------|-----|
| `main.py`                        | CLI 엔트리 | - |
| `config.py`                      | LLM provider, stage 분류 경계, 기본 policy | - |
| `state.py`                       | TypedDict (StageSummary, InvestmentStrategy 등) | - |
| `llm_factory.py`                 | Claude / Ollama provider 추상화 | - |
| `agents/stage_aggregator.py`     | planned_roadmap → StageSummary[] · `phase_name` 으로 그룹핑 (Agent 2 가 만든 phase 수만큼 stage 생성) | ❌ |
| `agents/strategist.py`           | 각 stage 에 Agent 1 raw `tech_candidates` 첨부 → LLM 으로 5-지표 평가 + Tier 도출 | ✅ |

## 입력 / 출력 포맷

Agent 3 는 **2개의 JSON 파일** 을 읽어서 3가지 데이터를 추출합니다:

| 추출 데이터 | 어느 파일에서 | 코드 |
|---|---|---|
| `planned_roadmap` | Agent 2 의 `planned_roadmap.json` | [pipeline.py:415](../orchestration_agent/pipeline.py#L415) |
| `market_context` | Agent 1 의 `tech_candidates.json` | [pipeline.py:422](../orchestration_agent/pipeline.py#L422) |
| `tech_candidates` | Agent 1 의 `tech_candidates.json` | [pipeline.py:423](../orchestration_agent/pipeline.py#L423) |

### 입력 ① — `planned_roadmap.json` (Agent 2 산출 · 필수)

```json
{
  "market_context": { ... },                // ← Agent 2 의 passthrough 이지만 사용하지 않음
  "planned_roadmap": [                      // ← Agent 2 가 직접 만든 부분 (사용)
    {
      "tech_id": "T01",
      "name": "High-NA EUV 노광 장비 커스터마이징",
      "phase_name": "1단계: 기반 R&D",       // ← stage 그룹핑 키
      "start_q": "2025 Q1",
      "target_q": "2026 Q2",
      "prerequisites": [],
      "lead_time_quarters": 6,
      "justification": "..."
    }
  ]
}
```

### 입력 ② — `tech_candidates.json` (Agent 1 산출 · 필수)

```json
{
  "market_context": {                        // ← 여기서 직접 읽음
    "target_market": "반도체",
    "expected_boom_quarter": "2028 Q1"
  },
  "tech_candidates": [                       // ← 여기서 직접 읽음 (raw 그대로 LLM 에 전달)
    {
      "tech_id": "T01",
      "name": "...",
      "category": "Equipment",
      "trl": 4,                              // raw 정수
      "market_score": 72.0,                  // raw 0~100
      "patent_score": 85.0,
      "final_score": 78.5,
      "expected_market_boom_quarter": "2028 Q1",
      "rationale": "[Patent] ... [Market] ..."
    }
  ]
}
```

> Agent 1 파일이 없으면 폴백으로 `planned_roadmap.json` 의 `market_context` (passthrough) 를 사용. 단 `tech_candidates` 는 Agent 1 출력에서만 읽으므로 평가 품질이 급격히 저하됨.

### 출력 — `output_investment_strategy.json`

```json
{
  "market_context": { ... },
  "investment_policy": {
    "risk_appetite": "medium",
    "investment_horizon": "balanced",
    "total_budget": 5000000000,
    "strategic_priority": ["market entry", "core capability building"]
  },
  "stages": [
    {
      "stage": "1단계: 기반 R&D",
      "period": "2025 Q1 - 2026 Q4",
      "goal": "1단계: 기반 R&D",
      "technologies": ["High-NA EUV 노광 장비 커스터마이징", "..."],
      "tech_ids": ["T01", "..."],
      "num_items": 3
    }
  ],
  "investment_strategy": [
    {
      "stage": "1단계: 기반 R&D",
      "period": "2025 Q1 - 2026 Q4",
      "stage_assessment": "boom_quarter (2028 Q1) 직전 R&D 단계로 적시. 멀티빔 e-beam 검사와 극저온 에칭이 함께 진행되어야 후속 공정 통합 단계의 기반이 마련된다.",
      "stage_budget_ratio": 0.40,                    // ← LLM 이 결정 (모든 stage 합 = 1.0)
      "stage_estimated_usd": 2000000000.0,           // ← 코드가 total_budget × ratio 로 자동 계산
      "tech_investments": [
        {
          "tech_id": "T01",
          "name": "멀티빔 e-beam 검사",
          "evaluation_scores": {
            "market_opportunity": 5,
            "strategic_fit": 5,
            "executability": 4,
            "uncertainty": 2,
            "urgency": 5
          },
          "investment_attractiveness": "high",
          "investment_urgency": "high",
          "recommended_investment_tier": "Tier 1",
          "investment_scope": "proactive and execution-focused",
          "recommended_action": "$117B 시장 + 19.9% CAGR 반영, 2026 Q3 PoC 즉시 착수",
          "rationale": [
            "stage 가 boom 직전 적시 + T01 시장 규모 $117B 로 즉시 투자 가치 큼",
            "Tier-1 공장 capex $34B 이미 진행 → 시장 진입 창 좁음"
          ],
          "major_risks": [
            "장비 공급망 의존",
            "초기 PoC 성과가 제한적일 경우 사업화 전환 지연"
          ],
          "resource_focus": [
            "PoC 예산",
            "장비 셋업 인력",
            "초기 제품화 개발 역량"
          ]
        },
        {
          "tech_id": "T02",
          "name": "극저온 에칭 장비",
          "evaluation_scores": { "...": "..." },
          "recommended_investment_tier": "Tier 2",
          "investment_scope": "milestone-based",
          "recommended_action": "T01 PoC 결과 검증 후 단계적 투자",
          "rationale": [
            "stage 통합 판단상 T01 보완재이지만 즉시 풀 투자 위험",
            "23.5% CAGR 매력적이나 capex 규모 $119B 로 큼"
          ],
          "...": "..."
        }
      ]
    }
  ]
}
```

> 같은 1단계 안에서도 T01 은 Tier 1, T02 는 Tier 2 가 가능 — **stage 컨텍스트는 narrative (`stage_assessment`) 로, 의사결정은 tech 단위 (`tech_investments`) 로**.

## Stage 집계 방식 (`--stage-mode`)

| 모드 | 의미 |
|------|------|
| `phase` (기본) | Roadmap Planner 의 `phase_name` 을 그대로 stage 라벨로 사용 |
| `horizon`      | `start_q` 기준으로 `short-term (≤8Q) / mid-term (≤16Q) / long-term` 자동 재분류 |

Roadmap Planner 가 `phase_name` 을 세분화(예: "1단계", "2단계", "3단계", "4단계")
한 경우엔 `phase` 모드가 자연스럽고, 단순히 시간대별 투자 전략이 필요하면
`horizon` 모드를 사용하세요.

## 실행

### 설치

```bash
# 의존성은 루트 requirements.txt 에 통합되어 있음
cd Tech-Analysis-Agent
pip install -r requirements.txt
```

LLM provider 는 `.env` 로 설정 (tech_analysis_agent 와 동일):

```ini
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
# 혹은
OLLAMA_MODEL=gemma3:27b
OLLAMA_BASE_URL=http://localhost:11434
```

### 기본 실행

```bash
python main.py
# == python main.py \
#      --roadmap ../roadmap_planner_agent/output_planned_roadmap.json \
#      --tech    ../tech_analysis_agent/output_tech_candidates.json
```

### 투자 정책 override

```bash
# 공격적 · 단기 · 예산 50억 USD
python main.py --risk high --horizon short --total-budget 5000000000

# 전략 우선순위 변경
python main.py --priority "first-mover advantage,ecosystem lock-in"

# horizon 기반 자동 분류 모드
python main.py --stage-mode horizon
```

## CLI 옵션 요약

| 옵션 | 의미 | 기본값 |
|------|------|--------|
| `--roadmap` / `-r`  | Roadmap Planner 출력 JSON | `../roadmap_planner_agent/output_planned_roadmap.json` |
| `--tech` / `-t`     | Technology Analyst 출력 JSON | `../tech_analysis_agent/output_tech_candidates.json` |
| `--output` / `-o`   | 결과 JSON 저장 경로 | `output_investment_strategy.json` |
| `--stage-mode`      | `phase` / `horizon` | `phase` |
| `--risk`            | `low` / `medium` / `high` | policy 기본값 |
| `--horizon`         | `short` / `balanced` / `long` | policy 기본값 |
| `--total-budget` | 전체 예산 (USD, 숫자) | policy 기본값 |
| `--priority`        | 쉼표 구분 우선순위 리스트 | policy 기본값 |

## 라이브러리 호출 (Orchestration Agent 에서 사용)

```python
from investment_strategist_agent.agents.stage_aggregator import aggregate_stages
from investment_strategist_agent.agents.strategist import run_strategist

stages = aggregate_stages(planned_roadmap, market_context)
strategies = run_strategist(
    stages=stages,
    tech_candidates=tech_candidates,
    investment_policy={"risk_appetite": "medium", ...},
    market_context=market_context,
)
```
