# Investment Strategist Agent (Agent 3)

앞선 두 에이전트 (Technology Analyst / Roadmap Planner) 의 결과를 종합해,
**로드맵 단계(stage) 단위로** 투자 매력도·시급성·Tier·범위·권고안을
도출하는 에이전트.

```
Tech-Analysis-Agent/
├── tech_analysis_agent/              ← Agent 1 (Technology Analyst)
├── roadmap_planner_agent/            ← Agent 2 (Roadmap Planner)
├── investment_strategist_agent/      ← 본 폴더 (Agent 3)
└── orchestration_agent/              ← (TBD)
```

## 핵심 원칙 (시언 spec)

- **판단 단위는 개별 기술이 아니라 로드맵 단계(stage)** 이다.
- 각 stage 에 대해 먼저 **5-지표 점수 (1–5점)** 를 부여:
  - `market_opportunity`  : 시장 기회 · 성장 가능성
  - `strategic_fit`       : 조직 전략 · 핵심 역량 부합도
  - `executability`       : 기간 내 실행 가능성
  - `uncertainty`         : 기술·시장·외부 의존성 불확실성 (높을수록 부정적)
  - `urgency`             : 적시 투자 중요도 (지연 시 가치 하락)
- 점수 프로파일을 해석해 **Tier / 권고안 / 리스크 / 자원 배분** 을 도출.

## Tier 정의

| Tier | 의미 | 적용 상황 |
|------|------|----------|
| **Tier 1** — 적극 투자 | 선제적 · 우선 투자 | 시장 기회 크고 실행 가능성 충분 |
| **Tier 2** — 선택적 투자 | 조건부 · 단계적 · 마일스톤 기반 | 유망하나 실행 불확실성 있음 |
| **Tier 3** — 탐색적 투자 | 소규모 파일럿 · 모니터링 · 옵션 확보 | 장기 · 불확실성 높음 |

## 파이프라인

```
 [planned_roadmap]  +  [tech_candidates]
        │                     │
        ▼                     ▼
   stage_aggregator  ←──  정규화
        │ (기술 단위 → stage 단위 집계)
        ▼
   stages[]  +  tech_details  +  investment_policy
        │
        ▼
   strategist  (LLM)
        │ Step 1. 5-지표 점수 (1~5)
        │ Step 2. Tier / 권고안 / 리스크 / 자원 배분 도출
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
| `agents/stage_aggregator.py`     | planned_roadmap → StageSummary[] (phase_name 또는 시간 기준) | ❌ |
| `agents/strategist.py`           | 5-지표 평가 + Tier 도출 (시언 spec 프롬프트) | ✅ |

## 입력 / 출력 포맷

### 입력 — Roadmap Planner 의 `output_planned_roadmap.json`

```json
{
  "market_context": {
    "target_market": "차세대 2nm 이하 파운드리 및 AI 가속기 시장",
    "expected_boom_quarter": "2028 Q1"
  },
  "planned_roadmap": [
    {
      "tech_id": "T01",
      "name": "High-NA EUV 노광 장비 커스터마이징",
      "phase_name": "1단계: 기반 R&D",
      "start_q": "2025 Q1",
      "target_q": "2026 Q2",
      "prerequisites": [],
      "lead_time_quarters": 6,
      "justification": "..."
    }
  ]
}
```

(선택) 입력 — Technology Analyst 의 `output_tech_candidates.json`

```json
{
  "tech_candidates": [
    {
      "tech_id": "T01",
      "name": "...",
      "trl": 4,
      "category": "Equipment",
      "final_score": 78.5,
      "market_score": 72.0,
      "patent_score": 85.0,
      "rationale": "..."
    }
  ]
}
```

> tech_candidates 가 제공되지 않으면 stage 정보만으로 LLM 평가가 이루어지며,
> 평가 품질이 저하될 수 있습니다.

### 출력 — `output_investment_strategy.json`

```json
{
  "market_context": { ... },
  "investment_policy": {
    "risk_appetite": "medium",
    "investment_horizon": "balanced",
    "budget_constraint": "medium",
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
      "evaluation_scores": {
        "market_opportunity": 4,
        "strategic_fit": 5,
        "executability": 4,
        "uncertainty": 3,
        "urgency": 5
      },
      "investment_attractiveness": "high",
      "investment_urgency": "high",
      "recommended_investment_tier": "Tier 1",
      "investment_scope": "proactive and execution-focused",
      "recommended_action": "PoC 와 초기 사업화를 연계한 우선 투자",
      "rationale": [
        "단기 시장 진입 가능성이 높아 빠른 성과 창출이 가능함",
        "초기 고객 확보가 후속 단계 확장 기반으로 작용할 수 있음"
      ],
      "major_risks": [
        "High-NA EUV 장비 공급망 의존 리스크",
        "초기 PoC 성과가 제한적일 경우 사업화 전환이 늦어질 수 있음"
      ],
      "resource_focus": [
        "PoC 예산",
        "장비 셋업 인력",
        "초기 제품화 개발 역량"
      ]
    }
  ]
}
```

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
cd investment_strategist_agent
pip install -r requirements.txt
```

LLM provider 는 `.env` 로 설정 (tech_analysis_agent 와 동일):

```ini
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
# 혹은
OLLAMA_MODEL=llama3.1:8b
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
# 공격적 · 단기 · 예산 여유
python main.py --risk high --horizon short --budget-constraint low

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
| `--budget-constraint` | `low` / `medium` / `high` | policy 기본값 |
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
