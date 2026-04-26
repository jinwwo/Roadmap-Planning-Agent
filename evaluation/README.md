# evaluation/ — TRM Final Evaluation Suite

> `Roadmap-Planning-Agent` 레포에서 생성된 기술 로드맵의 품질을  
> **Orchestrator 바깥에서 독립적으로 측정**하는 사후 평가 도구입니다.

---

## 목차

1. [이 도구가 하는 일](#1-이-도구가-하는-일)
2. [평가 구조: 두 축으로 본다](#2-평가-구조-두-축으로-본다)
3. [데이터 흐름: 입력부터 결과까지](#3-데이터-흐름-입력부터-결과까지)
4. [LLM-as-a-Judge 상세](#4-llm-as-a-judge-상세)
5. [Back Test 상세](#5-back-test-상세)
6. [Composite Score: 최종 점수](#6-composite-score-최종-점수)
7. [Description: 왜 이 점수인가](#7-description-왜-이-점수인가)
8. [Holdout 데이터: 어디서 어떻게 가져오는가](#8-holdout-데이터-어디서-어떻게-가져오는가)
9. [어댑터: Agent 출력 → 평가 입력 변환](#9-어댑터-agent-출력--평가-입력-변환)
10. [실행 방법](#10-실행-방법)
11. [웹 UI](#11-웹-ui)
12. [출력 JSON 스키마](#12-출력-json-스키마)
13. [파일 구조](#13-파일-구조)
14. [설계 원칙과 제약](#14-설계-원칙과-제약)
15. [남은 작업](#15-남은-작업)

---

## 1. 이 도구가 하는 일

Orchestrator가 Agent 1·2·3의 출력을 받아 TRM 5축 평가를 수행하고 ACCEPT/REVISE 결정을 내리는 것은 **런타임 품질 제어**입니다. 이 도구는 그와 별개로, 최종 생성된 로드맵이 **실제로 얼마나 좋은 로드맵인지**를 사후적으로 정량 측정합니다.

핵심 용도:

- **Single-agent vs Multi-agent 비교**: 같은 입력에 대해 두 방식의 로드맵 품질을 정량 비교
- **Ablation 실험**: Agent 1/2/3 중 하나를 끄고 실행한 결과(full vs noA1 vs noA2 vs noA3)를 비교하여 각 Agent의 기여도 측정
- **로드맵 품질의 재현 가능한 benchmark**: LLM 판단(주관) + 수식 기반 back test(객관)를 조합하여 누구나 같은 기준으로 평가

### Orchestrator 평가와의 차이

| | Orchestrator (Phase B) | 이 평가 도구 |
|---|---|---|
| 위치 | 파이프라인 내부 | 파이프라인 완료 후 (사후) |
| 목적 | ACCEPT/REVISE 결정 | 정량적 성능 측정 + 비교 |
| LLM 평가 | 단일 LLM으로 수정 지시 | Multi-provider 앙상블로 **점수화** (0-100) |
| 정량 평가 | 없음 | Back test (특허/시장 holdout 데이터) |
| 비교 기능 | 없음 | Pairwise 비교 + Ablation 일괄 비교 |

---

## 2. 평가 구조: 두 축으로 본다

```
┌──────────────────────┐     ┌──────────────────────┐
│  LLM-as-a-Judge      │     │  Back Test           │
│  "구조가 좋은가?"     │     │  "실제로 좋은 선택?" │
│                      │     │                      │
│  • Tech-Market       │     │  • Selection Quality │
│    Alignment         │     │  • Cost-Adj Return   │
│  • Sequencing        │     │  • Investment Ratio. │
│  • Investment Ratio. │     │  • Budget Feasib.    │
│  • Coherence         │     │  • Dependency Valid. │
│  • Portfolio Balance │     │  • Timing Accuracy   │
│                      │     │                      │
│  Claude / GPT /      │     │  순수 Python 수식    │
│  Gemini API 호출     │     │  LLM 없음            │
│                      │     │                      │
│  → 0-100점           │     │  → 0-100점           │
└──────────┬───────────┘     └──────────┬───────────┘
           │         가중치 0.4                │         가중치 0.6
           └──────────────┬──────────────────┘
                          │
                ┌─────────▼─────────┐
                │  Composite Score  │
                │  − Penalties      │
                │  → 최종 0-100점   │
                └───────────────────┘
```

**왜 두 축인가:**
- LLM judge 단독으로는 투자 기술의 성장성·수익·투자 대비 수익을 **객관적으로** 측정할 수 없음
- Back test 단독으로는 기술 간 선행관계, 포트폴리오 밸런스 같은 **구조적 정합성**을 잡아내지 못함
- 주관(LLM) + 객관(수식) 조합으로 신뢰성 확보

---

## 3. 데이터 흐름: 입력부터 결과까지

```
[Agent 1: tech_candidates.json]  ─┐
[Agent 2: planned_roadmap.json]   ─┤
[Agent 3: investment_strategy.json]┤
[Orchestrator: orchestrator_report]┘
                │
                ▼
        ┌── adapter.py ──┐
        │  필드명 변환    │   name→tech_name, trl→TRL
        │  분기 정규화    │   "2028 Q1"→"2028-Q1"
        │  Tier 매핑      │   "Tier 1"→"Strategic"
        │  stage→tech     │   stage 단위를 tech_id 단위로 풀기
        └────────┬───────┘
                 ▼
          input_pack (통합 JSON)
                 │
        ┌────────┤ holdout_data 없으면
        ▼        ▼
  data_sources.py에서 holdout 추출
  ├── USPTO PatentsView API (특허, 시점 필터 O)
  ├── CSV 시장 DB (시장 규모, 시점 필터 O)
  └── Tavily Search API (시장, 시점 필터 X — fallback)
                 │
                 ▼
        ┌── trm_evaluation.py ──────────────────────────┐
        │                                               │
        │  Step 1. AgentOutputNormalizer                 │
        │    holdout에서 log-ratio feature 계산          │
        │    universe 전체 기준 min-max 정규화           │
        │                                               │
        │  Step 2. ConstraintChecker                     │
        │    예산 초과, 의존성 위반, 타이밍 오차 검사    │
        │                                               │
        │  Step 3. LLMStructuralJudge                    │
        │    5축 평가 (API 또는 rule-based)              │
        │                                               │
        │  Step 4. BackTestEvaluator                     │
        │    6개 정량 지표 산출                          │
        │                                               │
        │  Step 5. ScoreAggregator                       │
        │    0.4×LLM + 0.6×BT − penalties               │
        │                                               │
        │  Step 6. BenchmarkReportGenerator              │
        │    최종 JSON + Description 생성               │
        │                                               │
        └───────────────────────────┬───────────────────┘
                                    │
                                    ▼
                          evaluation_result.json
                          ├── llm_judge (5축 + description)
                          ├── backtest (6지표 + description)
                          ├── composite (최종 + description)
                          ├── constraints (위반 상세)
                          └── diagnostics (요약 + 실패점)
```

---

## 4. LLM-as-a-Judge 상세

### 5개 평가 축 (각 1-5점)

| 축 | 가중치 | 평가 내용 | 높으면 | 낮으면 |
|---|---|---|---|---|
| **Alignment** | 0.3 | 선택된 기술이 고성장 시장과 맞는가 | 시장 성장률 높은 기술 중심 | 정체/하락 시장 기술 선택 |
| **Sequencing** | 0.2 | 선행관계가 올바른가 | 의존성 위반 0건 | 선행기술 미완료 상태에서 후행기술 시작 |
| **Investment** | 0.2 | 가치 높은 기술에 높은 투자를 했는가 | value-tier 일치 | 가치 낮은 기술에 Strategic 투자 |
| **Coherence** | 0.2 | 기술 포트폴리오가 전략적으로 일관적인가 | 다양한 카테고리 + 넓은 TRL 범위 | 단일 카테고리 편중 |
| **Balance** | 0.1 | 투자 등급이 분산되어 있는가 | Strategic/High/Medium/Low 골고루 | 한 등급에 편중 |

### 점수 계산

```python
# 1-5 스케일의 가중합 → 0-100 스케일
llm_raw = 0.3×alignment + 0.2×sequencing + 0.2×investment + 0.2×coherence + 0.1×balance
llm_structural_score = (llm_raw - 1) / 4 × 100
```

예시: alignment=4, sequencing=5, investment=3, coherence=4, balance=3
→ raw = 0.3×4 + 0.2×5 + 0.2×3 + 0.2×4 + 0.1×3 = 3.9
→ score = (3.9 - 1) / 4 × 100 = **72.5**

### 3가지 실행 모드

| 모드 | 방법 | 용도 |
|---|---|---|
| **Rule-based** | API 없이, 구조적 특성으로 점수 근사 | 빠른 테스트, 오프라인 |
| **단일 LLM** | Claude, GPT, 또는 Gemini 하나로 평가 | 일반 실험 |
| **앙상블** | 3개 모델의 축별 평균(mean) 또는 중간값(median) | 논문용, judge bias 측정 |

앙상블에서는 `per_model_scores`에 각 모델의 원본 점수가 보관되어 모델 간 합의 수준(표준편차)을 분석할 수 있습니다.

---

## 5. Back Test 상세

Back test는 **LLM을 사용하지 않습니다.** 순수 Python 수식으로 계산합니다.

### 입력 데이터

Agent가 선택한 기술의 **holdout 데이터** (Agent가 보지 못한 외부 데이터):

```json
{
  "T01": {
    "baseline_patents": 85,       ← Agent가 볼 수 있었던 시점의 특허 수
    "realized_patents": 310,      ← 이후 실제 관측된 특허 수 (검증용)
    "baseline_market_m": 800,     ← Agent가 볼 수 있었던 시점의 시장 규모 ($M)
    "realized_market_m": 4200     ← 이후 실제 관측된 시장 규모 ($M)
  }
}
```

### 기술별 가치 계산 (value_i)

```python
# 1. Raw feature 계산 (log-ratio)
patent_growth = log((realized_patents + 1) / (baseline_patents + 1))
market_growth = log((realized_market + 1) / (baseline_market + 1))
market_size   = log(realized_market + 1)

# 2. 정규화 (universe 전체 기준 min-max)
#    ※ 선택된 기술끼리만 하면 왜곡됨 — 전체 후보 universe 포함
patent_growth_norm = (patent_growth - min_all) / (max_all - min_all)

# 3. 가치 산출
value_i = 0.4 × patent_growth_norm + 0.3 × market_growth_norm + 0.3 × market_size_norm
```

### 비용 반영 수익 (cost_adjusted_return)

```python
# Agent 3이 매긴 투자 등급을 비용으로 변환
COST_MAP = {"Low": 1, "Medium": 2, "High": 3, "Strategic": 4}
cost_norm = (COST_MAP[tier] - 1) / 3    # 0~1 정규화

# 가치에서 비용을 차감 (λ=0.15)
return_i = value_i - 0.15 × cost_norm
```

의미: 같은 value라도 Low로 투자했으면 return이 높고, Strategic으로 투자했으면 return이 낮아짐. **"적은 비용으로 높은 가치를 얻었는가"**를 측정.

### 6개 지표

| 지표 | 가중치 | 계산 방법 | 의미 |
|---|---|---|---|
| **selection_quality** | 0.30 | 선택된 기술들의 평균 value × 100 | 좋은 기술을 골랐는가 |
| **cost_adjusted_return** | 0.30 | 평균 (value - λ×cost) × 100 | 투자 대비 가치가 높은가 |
| **investment_rationality** | 0.15 | 1 - (tier 불일치율) | 기대 tier와 실제 tier가 맞는가 |
| **budget_feasibility** | 0.10 | 100 - 예산초과율×2 | 예산 내에서 집행했는가 |
| **dependency_validity** | 0.10 | 100 - 위반건수×20 | 선행관계를 지켰는가 |
| **timing_accuracy** | 0.05 | 100 - 평균타이밍오차×30 | 시장 개화 시점에 맞췄는가 |

### Backtest Score

```python
backtest_score = (
    0.30 × selection_quality +
    0.30 × cost_adjusted_return +
    0.15 × investment_rationality +
    0.10 × budget_feasibility +
    0.10 × dependency_validity +
    0.05 × timing_accuracy
)
```

### investment_rationality 상세

단순히 "어려우면 High"가 아니라, 아래 기준으로 **기대 tier**를 계산한 뒤 실제 tier와 비교:

- value > 0.7 → +1 등급
- value > 0.5 → +1 등급
- 시장 개화 2년 이내 → +1 등급
- 다른 기술의 선행 기술이면 → +1 등급
- TRL ≥ 6 → 최소 Medium

1단계 차이까지는 허용, 2단계 이상 차이면 mismatch로 카운트.

---

## 6. Composite Score: 최종 점수

```python
pre_penalty = 0.4 × llm_structural_score + 0.6 × backtest_score
```

LLM judge보다 back test 비중이 높은 이유: LLM judge가 경제성·수익을 객관적으로 측정하지 못하므로, 정량 데이터 기반인 back test를 더 신뢰.

### Penalty (hard constraint 위반)

구조적으로 말이 안 되는 로드맵이 경제 proxy만 높다고 좋은 로드맵이 되면 안 되므로, hard constraint 위반은 별도 페널티로 차감:

| 위반 종류 | 페널티 |
|---|---|
| 의존성 위반 | 위반 1건당 **-3pt** |
| 예산 초과 | 초과율 × **0.5pt** |
| 타이밍 오차 > 1년 | (오차 - 1) × **5pt** |

```python
final_composite_score = max(0, pre_penalty - total_penalty)
```

### 예시

```
LLM = 70.0, Backtest = 66.0
pre_penalty = 0.4×70.0 + 0.6×66.0 = 67.6
penalties = dep_violation 3건×3 + budget 10%×0.5 = 9 + 5 = 14.0
final = 67.6 - 14.0 = 53.6
```

---

## 7. Description: 왜 이 점수인가

모든 점수에 **산출 근거 설명(description)**이 자동 생성됩니다.

### LLM Judge Description 예시

```json
{
  "alignment": "점수 3/5 — 선택된 기술들의 시장 성장률(normalized) 평균 기반. 높을수록 고성장 시장의 기술을 잘 선택했다는 의미.",
  "sequencing": "점수 2/5 — 의존성 위반 3건 감지. 3건의 선행기술 미완료 상태에서 후행기술이 시작됨.",
  "investment": "점수 5/5 — 기술 가치(value) 대비 투자 등급(tier) 일치도 기반. 가치 높은 기술에 높은 투자를 배분할수록 높은 점수.",
  "coherence": "점수 5/5 — 기술 카테고리 다양성(3종: Process, Equipment, Material)과 TRL 분포(범위 3~8) 기반.",
  "balance": "점수 5/5 — 투자 등급 다양성(4종: Strategic, High, Medium, Low) 기반.",
  "formula": "(0.3×3 + 0.2×2 + 0.2×5 + 0.2×5 + 0.1×5 - 1) / 4 × 100 = 70.0"
}
```

### Backtest Description 예시

```json
{
  "selection_quality": "61.0/100 — 선택된 4개 기술의 평균 value(holdout 기반). 최고: On-Device LLM(0.907), 최저: Neuromorphic Sensor(0.360).",
  "cost_adjusted_return": "53.5/100 — value에서 투자 비용(tier 기반)을 차감한 순수익 proxy. 높은 tier 기술의 value가 낮으면 점수가 떨어짐.",
  "dependency_validity": "40.0/100 — 의존성 위반 3건. 위반 1건당 -20점.",
  "timing_accuracy": "92.5/100 — 평균 타이밍 오차 0.25년. 시장 개화 시점 대비 적절한 타이밍.",
  "formula": "0.30×61.0 + 0.30×53.5 + 0.15×100.0 + 0.10×80.0 + 0.10×40.0 + 0.05×92.5 = 66.0"
}
```

### Composite Description 예시

```
pre_penalty = 0.4×70.0(LLM) + 0.6×66.0(Backtest) = 67.6.
Penalty: dep_violation: 3×3=9pt, budget: 10%×0.5=5.0pt. 총 -14.0pt.
최종 = 53.6.
```

---

## 8. Holdout 데이터: 어디서 어떻게 가져오는가

### Back Test의 핵심 전제

Agent가 로드맵을 작성할 때 **볼 수 없었던 미래 데이터**로 Agent의 선택을 사후 검증합니다. 이를 위해 같은 DB의 다른 시점 데이터를 사용합니다.

```
   동일 DB
   ┌──────────────────────────────────────────────┐
   │ ~baseline 시점 데이터 ──── Agent가 사용       │
   │                                              │
   │ baseline~evaluation 데이터 ── Back Test 검증  │
   └──────────────────────────────────────────────┘
```

### 시점 모드 (--holdout-mode)

| 모드 | 동작 | 용도 |
|---|---|---|
| **global** (기본) | 모든 기술에 동일한 baseline/evaluation 적용 | 단순, 빠른 테스트 |
| **per-tech** | 각 기술의 start_q→baseline, target_q→evaluation으로 개별 시점 | 정밀한 back test |

per-tech 예시:

```
T01 (GAA 나노시트): start_q=2026-Q4 → baseline=2026-09-30, target_q=2028-Q1 → evaluation=2028-03-31
T02 (EUV 리소그래피): start_q=2025-Q1 → baseline=2024-12-31, target_q=2026-Q2 → evaluation=2026-06-30
```

### 데이터 소스 (--connector)

| 커넥터 | 특허 | 시장 | 시점 필터 | 비고 |
|---|---|---|---|---|
| `mock` | Mock 생성 | Mock 생성 | N/A | 테스트용 |
| `csv` | Mock | CSV 파일 (`data/market_data.csv`) | ✅ | 수동 수집 데이터 |
| `uspto` | USPTO PatentsView API | Mock | ✅ | 미국 특허 (무료, 키 불필요) |
| `tavily` | Mock | Tavily Search API | ⚠ 제한적 | 실시간 검색, `TAVILY_API_KEY` 필요 |
| `csv+uspto` | USPTO | CSV | ✅ | 특허+시장 모두 실제 데이터 |
| `tavily+uspto` | USPTO | Tavily | ⚠ 특허만 | 특허 시점필터 + 시장 실시간 |

### CSV 시장 DB 포맷 (`data/market_data.csv`)

```csv
keyword,year,market_size_m,cagr_pct,source
euv lithography,2020,6500,22.0,Statista
euv lithography,2025,18000,16.5,Gartner
chiplet,2020,1800,40.5,IDC
chiplet,2025,8500,30.0,IDC
```

현재 반도체 6개 기술(GAA, EUV, High-k, Hybrid Bonding, BSPDN, Chiplet)의 2018~2025 데이터가 포함되어 있습니다. 실험 시 실제 데이터로 교체하면 됩니다.

### 독립성 원칙

**Agent 내부 점수(`patent_score`, `market_score`, `final_score`)를 back test에서 재사용하지 않습니다.** Agent가 만든 점수로 Agent를 평가하면 순환평가가 되므로, holdout 데이터에서 독립적으로 재계산합니다.

---

## 9. 어댑터: Agent 출력 → 평가 입력 변환

Agent 3개 + Orchestrator의 출력 JSON을 평가 도구가 소비할 수 있는 `input_pack` 포맷으로 변환합니다.

### 변환 규칙

| Agent 출력 | → 평가 도구 | 변환 내용 |
|---|---|---|
| `name` | `tech_name` | 필드명 변경 |
| `trl` (소문자) | `TRL` (대문자) | 필드명 변경 |
| `"2028 Q1"` (공백) | `"2028-Q1"` (하이픈) | 분기 표기 정규화 |
| `recommended_investment_tier: "Tier 1"` | `investment_tier: "Strategic"` | Tier 1→Strategic, Tier 2→High, Tier 3→Medium |
| `evaluation_scores.market_opportunity` | `market_opportunity` (flat) | nested 구조 해체 |
| `stages[].tech_ids` (stage 단위) | 각 `tech_id` 단위 | Agent 3은 stage로 묶어서 평가하지만 back test는 기술 단위가 필요 |
| `phase_name: "DROPPED"` | (제외) | Orchestrator가 drop한 기술 자동 제외 |
| `prerequisites` (roadmap) | `dependency_hints` (tech) | roadmap의 선행관계를 tech_candidates에 역주입 |

### 입력 방식

| 방식 | 명령 | 설명 |
|---|---|---|
| **번들 1개** | `--bundle evaluation_bundle.json` | 4개 출력이 합쳐진 JSON |
| **자동 탐지** | `--auto-detect` | Orchestrator outputs/에서 4개 파일 자동 수집 → 번들 생성 |
| **개별 지정** | `--tech ... --roadmap ... --investment ...` | 파일 경로 직접 지정 |
| **input_pack** | `--input input_pack.json` | 이미 변환된 JSON |

---

## 10. 실행 방법

### 설치

```bash
cd evaluation
pip install -r requirements.txt
```

### 기본 실행 (Mock, API 없이)

```bash
python run_evaluation.py --input samples/sample_input_multi_agent.json
```

### Agent 실행 후 자동 평가

```bash
# 1. Agent 파이프라인 실행 (기존)
cd orchestration_agent && python main.py

# 2. 평가 (자동 탐지)
cd ../evaluation && python run_evaluation.py --auto-detect
```

### 번들로 평가

```bash
python run_evaluation.py --bundle ../orchestration_agent/outputs/evaluation_bundle.json
```

### Holdout 추출 (Back test용)

```bash
# Mock
python run_evaluation.py --input pack.json --extract --connector mock

# CSV 시장 + USPTO 특허 (실제)
python run_evaluation.py --input pack.json --extract --connector csv+uspto \
    --holdout-mode per-tech \
    --baseline-date 2020-12-31 --evaluation-date 2025-12-31

# Tavily 시장 + USPTO 특허
python run_evaluation.py --input pack.json --extract --connector tavily+uspto
```

### LLM 앙상블 평가

```bash
export ANTHROPIC_API_KEY='sk-ant-...'
export OPENAI_API_KEY='sk-...'
export GOOGLE_API_KEY='...'

python run_evaluation.py --auto-detect \
    --provider anthropic openai gemini --aggregation mean
```

### Pairwise 비교

```bash
python run_evaluation.py --bundle full_bundle.json --compare noA3_bundle.json
```

### CLI 데모 (3개 시나리오)

```bash
python demo.py              # 전체
python demo.py --scenario 1 # Agent → 어댑터 → 평가
python demo.py --scenario 2 # Ablation 비교
python demo.py --scenario 3 # Back test 상세
```

### 결과 저장

```bash
python run_evaluation.py --auto-detect --output result.json -v
```

---

## 11. 웹 UI

```bash
# 브라우저에서 더블클릭 (서버 불필요)
open demo_web.html
```

### 기능

**📋 결과 목록 탭**
- JSON 파일을 드래그 & 드롭 (여러 개 동시 가능)
- Mock 데모 / Ablation 4종 원클릭 추가
- 각 결과 카드에 Composite 점수 + LLM/BT 미리보기
- 하단에 전체 비교 테이블

**📊 상세 탭** (결과 하나 클릭)
- Score 링 3개 (LLM + Backtest = Composite)
- LLM Judge: 레이더 차트 + **Description 패널**
- Back Test: 6지표 bar + **Description 패널**
- Composite: 수식 재현 + 페널티 내역
- 기술별 Value 테이블 (Patent↑, Market↑, Size, Value, Tier, Return)
- Constraint 위반 카드

**🔀 비교 탭** (두 개 체크)
- A vs B 스코어 링 나란히 + Winner 표시 + Delta
- LLM 축별 비교 (화살표 ◀/▶)
- Backtest 지표별 비교

### 지원 포맷

업로드하면 포맷을 자동 감지합니다:

| 포맷 | 감지 기준 |
|---|---|
| `evaluation_bundle.json` | `orchestrator_report` 키 존재 |
| `input_pack.json` | `tech_candidates` + `planned_roadmap` 존재 |
| Agent 출력 (Tier 1/2/3 등) | 어댑터 자동 변환 |

---

## 12. 출력 JSON 스키마

```json
{
  "metadata": {
    "scenario": "multi-agent",
    "domain": "AI 반도체",
    "num_technologies": 6
  },

  "llm_judge": {
    "alignment": 4, "sequencing": 5, "investment": 3,
    "coherence": 4, "balance": 3,
    "llm_structural_score": 72.5,
    "aggregation": "mean",
    "models_used": ["anthropic:claude-sonnet-4", "openai:gpt-4o"],
    "per_model_scores": { ... },
    "evidence": { "alignment": "...", ... },
    "strengths": [...], "weaknesses": [...],
    "description": {
      "alignment": "점수 4/5 — ...",
      "sequencing": "점수 5/5 — ...",
      "formula": "..."
    }
  },

  "backtest": {
    "tech_details": [
      { "tech_id": "T01", "value": 0.696, "cost_adjusted_return": 0.546, ... }
    ],
    "selection_quality": 81.2,
    "cost_adjusted_return": 73.4,
    "investment_rationality_score": 68.0,
    "budget_feasibility_score": 100.0,
    "dependency_validity_score": 85.0,
    "timing_accuracy_score": 72.0,
    "backtest_score": 77.0,
    "description": {
      "selection_quality": "81.2/100 — 6개 평균 value. 최고: EUV(0.89)...",
      "formula": "0.30×81.2 + ... = 77.0"
    }
  },

  "constraints": {
    "budget_violation_pct": 0,
    "dependency_violations": 1,
    "dependency_details": [...],
    "mean_timing_error_years": 0.8,
    "timing_details": [...]
  },

  "composite": {
    "pre_penalty_score": 75.3,
    "penalties": [{ "type": "dependency_violation", "amount": 3 }],
    "total_penalty": 3.0,
    "final_composite_score": 72.3,
    "description": "0.4×72.5 + 0.6×77.0 = 75.3. Penalty: dep:3pt. Final = 72.3."
  },

  "diagnostics": {
    "summary": "LLM 구조 평가 72.5점, Backtest 77.0점. 최종 72.3점.",
    "key_failure_points": ["의존성 위반: T01이 T03 완료 전 시작"]
  }
}
```

---

## 13. 파일 구조

```
evaluation/
├── trm_evaluation.py              ← 6단계 평가 엔진 + Description 생성
├── data_sources.py                ← DB 커넥터 (USPTO/CSV/Tavily/Mock)
│                                     + HoldoutExtractor (global/per-tech)
├── run_evaluation.py              ← CLI 러너
├── demo.py                        ← CLI 데모 (3개 시나리오)
├── demo_web.html                  ← 웹 UI (다중 결과 비교, 브라우저 더블클릭)
│
├── adapters/
│   ├── __init__.py
│   └── roadmap_planning_agent.py  ← Agent JSON 어댑터
│                                     (from_bundle / from_auto_detect / from_files)
├── data/
│   └── market_data.csv            ← CSV 시장 DB (반도체 6개 기술, 2018~2025)
│
├── samples/
│   ├── sample_input_multi_agent.json
│   ├── sample_input_single_agent.json
│   └── agent_output_only.json
│
├── requirements.txt
├── .gitignore
└── README.md                      ← 이 문서
```

---

## 14. 설계 원칙과 제약

### 설계 원칙

| 원칙 | 설명 |
|---|---|
| **독립성** | Agent 내부 점수를 평가에 재사용하지 않음 (순환평가 방지) |
| **같은 DB** | Agent와 Back Test가 같은 커넥터를 공유 (교란변수 제거) |
| **정규화 기준** | universe 전체 기준 min-max (선택 기술끼리만 하면 왜곡) |
| **LLM + 수식 분리** | 주관(LLM judge) + 객관(back test) 조합으로 신뢰성 확보 |
| **Agent 코드 무수정** | 평가 도구가 독립적으로 동작, Orchestrator 코드 수정 불필요 |

### 알려진 제약

| 제약 | 영향 | 대응 |
|---|---|---|
| USPTO만 사용 (미국 특허) | TSMC(대만), Samsung(한국) 특허 누락 | 논문에 한계로 명시. 미국 출원이 글로벌 트렌드 proxy |
| Tavily 시점 필터 불가 | 시장 데이터 back test 정밀도 한계 | CSV DB로 대체 (시점 필터 지원) |
| Rule-based proxy 미검증 | 실제 LLM 판단과 차이 가능 | 실험에서는 실제 API 사용 권장 |
| LLM judge 비결정적 | 같은 입력에 다른 점수 | temperature=0 + 다회 실행 평균 권장 |

---

## 15. 남은 작업

| 작업 | 상태 |
|---|---|
| 6단계 평가 파이프라인 | ✅ |
| LLM Judge Multi-provider (Claude/GPT/Gemini) | ✅ |
| Back Test 6개 지표 | ✅ |
| Description 자동 생성 | ✅ |
| 기술별 시점 윈도우 (per-tech) | ✅ |
| TavilyMarketConnector | ✅ |
| CSVMarketConnector | ✅ |
| USPTOConnector | ✅ |
| 어댑터 (from_bundle / from_auto_detect) | ✅ |
| 웹 UI (다중 결과 목록 + 상세 + 비교) | ✅ |
| CLI 데모 | ✅ |
| KIPRIS 커넥터 (한국 특허) | 🟡 스텁 |
| Ablation holdout 공정성 | 🟡 설계 결정 필요 |
| 실험 자동화 (multi-seed) | 🟡 |
| 결과 시각화 (논문용 차트) | 🟡 |
