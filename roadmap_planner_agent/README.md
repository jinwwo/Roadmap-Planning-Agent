# Roadmap Planner Agent (Agent 2)

Technology Analyst Agent (Agent 1) 가 발굴한 후보 기술군을 입력받아,
**기술 간 종속성 + 기술/시장 정보 + Strategic Direction** 을 바탕으로
Planning Horizon (1차년도 ~ N차년도) 안에 각 기술을 배치한 **차년도 기반 로드맵** 을
설계하는 LangGraph 기반 에이전트.

단계 분류 (1단계/2단계 등) 없이 dependency-driven 으로 자유롭게 배치.

```
Tech-Analysis-Agent/
├── tech_analysis_agent/       ← Agent 1 (Technology Analyst)
├── roadmap_planner_agent/     ← 본 폴더 (Agent 2)
├── orchestration_agent/       ← (TBD)
└── investment_strategist_agent/ ← (TBD)
```

## 핵심 책임

- **후보 기술 큐레이션** — Strategic Direction 정합 최우선으로 N개 후보 중 K개 선별 (`tech_selector`, K_MIN=3)
- **종속성 + 기술/시장 기반 timeline 설계** — phase 분류 없이 각 기술의 start_q / target_q + 차년도 (year_idx_start/target) 결정
- **3-분리 reasoning 출력** — 각 기술마다 `year_placement` (왜 N차년도) / `tech_execution` (왜 수행) / `investment_selection` (왜 핵심 투자 후보) 별도 작성
- **상위 컨텍스트 주입** — Company Scenario + Strategic Direction 을 모든 LLM 노드 프롬프트 상단에 주입
- **Planning Horizon 준수** — reference_year 가 horizon 종료점. 모든 기술이 한 시점에 몰리지 않게 펼침

## 파이프라인 — 두 가지 설계 모드 (`ROADMAP_DESIGN_MODE`)

### `holistic` (default — 2 LLM 노드)

```
  START
    │
    ▼
  [tech_selector]          ← LLM: 후보 N개 → K개 선별
    │                         · 축 #0 (최우선): Strategic Direction 정합 — 각 bullet 당 1개 이상 보존
    │                         · 점수 / 트렌드 정합 / 카테고리 균형 / 시점 분포 / 중복 제거
    │                         · ROADMAP_TECH_K_MIN 보장 (default 3)
    │                         · final_score 신뢰 (재평가 X) — 단순 큐레이션
    ▼
  [roadmap_designer]       ← LLM 통합 처리:
    │                         · 종속성 (dependency_hints / 카테고리 / TRL) 가장 우선
    │                         · 기술 정보 + 시장 정보 + reference_year horizon
    │                         · 단계 분류 (1단계/2단계) 없이 자유 배치
    │                         · 출력: start_q / target_q + year_idx_start / year_idx_target
    │                                 + reasoning {year_placement / tech_execution / investment_selection}
    │
    │  [후처리 안전망 — Python]
    │    1. _enforce_dependency_gap   : prereq target_q < dependent start_q 위반 시
    │                                    dependent 를 push forward + cascade (최대 5 pass)
    │    2. horizon 안전망            : max(year_idx_target) < N차년도 면 가장 후행 기술
    │                                    (TRL ↓ + final_score ↑) 을 N차년도까지 자동 연장
    ▼
  END
```

차년도 단위 자유 배치 + dependency-driven. 두 후처리로 LLM 의 dependency / horizon 누락을 자동 보정.

### `hybrid` (옛 모드 — Python 알고리즘 결정성 우선)

```
  START
    │
    ▼
  [tech_selector]          ← (동일)
    │
    ▼
  [dependency_analyzer]    ← LLM: 기술 트리 구성 + 레이어 할당 + 양방향 정합
    │                         (카테고리 계층 + dependency_hints 정밀화)
    ▼
  [timeline_calculator]    ← pure Python: TRL 기반 역산
    │                         · Kahn's topological sort + backcasting
    │                         · reference_year leaf 안전망 (가장 후행 기술만 horizon 끝까지)
    ▼
  [roadmap_builder]        ← LLM: phase_name + justification (분기 변경 X)
    │                         · phase_name 단조 증가 룰 (시간순)
    │                         · reference_year horizon narrative
    ▼
  END
```

결정성 보장 (TRL lead_time 강제, Zero-slack 검증). 단 chain 이 sparse 하면 timeline 이 한 시점에 몰리는 경향.

### 모드 전환

`.env` 의 `ROADMAP_DESIGN_MODE=holistic` (default) 또는 `=hybrid` 로 토글. server 재시작.

## 파일 구조

| 파일 | 역할 | LLM |
|------|------|-----|
| `main.py`                     | CLI 엔트리 (argparse) | - |
| `config.py`                   | LLM provider, TRL 리드타임, 카테고리 레이어 | - |
| `state.py`                    | LangGraph State TypedDict | - |
| `llm_factory.py`              | Claude / Ollama provider 추상화 | - |
| `agents/tech_selector.py`       | 후보 K개 선별 (5축 큐레이션 + K_MIN 보장) | ✅ |
| `agents/roadmap_designer.py`    | **(holistic 모드)** dependency + lead_time + backcasting + year_idx + 3-reasoning 한 번에 LLM 통합 + **후처리 2 안전망** (dependency gap / horizon stretch) | ✅ |
| `agents/dependency_analyzer.py` | (hybrid 모드) 기술 의존성 트리 + 양방향 정합 | ✅ |
| `agents/timeline_calculator.py` | (hybrid 모드) TRL 역산 알고리즘 + Zero-slack + reference_year leaf 안전망 | ❌ |
| `agents/roadmap_builder.py`     | (hybrid 모드) phase_name + Justification (분기 변경 X) | ✅ |
| `graphs/roadmap_graph.py`       | LangGraph 조립 (`ROADMAP_DESIGN_MODE` 분기) + `run_roadmap_planner()` 헬퍼 | - |

## 입력 / 출력 포맷

### 입력 (Agent 1 의 `output_tech_candidates.json`)

```json
{
  "market_context": {
    "target_market": "차세대 2nm 이하 파운드리 및 AI 가속기 시장",
    "expected_boom_quarter": "2028 Q1"
  },
  "tech_candidates": [
    {
      "tech_id": "T01",
      "name": "High-NA EUV 노광 장비 커스터마이징",
      "trl": 4,
      "category": "Equipment",
      "dependency_hints": []
    }
  ]
}
```

### 출력 (`output_planned_roadmap.json`)

```json
{
  "market_context": { ... },
  "planned_roadmap": [
    {
      "tech_id": "T01",
      "name": "High-NA EUV 노광 장비 커스터마이징",
      "year_idx_start": 1,
      "year_idx_target": 2,
      "prerequisites": [],
      "reasoning": {
        "year_placement": "TRL 4 + 후속 공정의 prereq → 1차년도 시작 필수.",
        "tech_execution": "Strategic Direction #1 'AI 하드웨어 리더십 유지' 와 직결. 시장 boom 직전 양산 준비.",
        "investment_selection": "final_score 88.7 + market_score 92 로 후보 최상위. R&D 예산 $12B 의 ~15% 배정 합리적."
      }
    }
  ],
  "dependency_tree": { ... },
  "tech_selection": {
    "selected_count": 5,
    "rationale": "10개 후보 중 5개 선별. Strategic Direction 정합 + 카테고리 균형.",
    "dropped": [
      {"tech_id":"T02","name":"...","reason":"T01 과 기능 중복"}
    ]
  }
}
```

### `year_idx_start` / `year_idx_target` (차년도)

Planning Horizon 시작 연도를 1차년도로 환산한 정수.
- 예: `planning_horizon=2026-2030` → "2026 Q1" = 1차년도, "2030 Q4" = 5차년도
- 웹 시각화 (1차년도 ~ N차년도 간트) 와 보고서가 이 필드 사용
- LLM 이 직접 출력하지만 누락 시 `start_q/target_q` 에서 자동 도출 (`_derive_year_idx`)

### 3-분리 `reasoning` dict

| 키 | 관점 | 용도 |
|----|------|------|
| `year_placement` | 차년도 배치 timing 이유 | 보고서 — "왜 N차년도?" |
| `tech_execution` | 기술 수행 정당성 | 보고서 — "왜 이 기술?" (Strategic Direction 인용) |
| `investment_selection` | 투자 선정 1차 사유 | Strategist 의 입력 — Tier 결정 컨텍스트 |

## TRL 기반 리드 타임

| TRL | 의미 | 리드 타임 (분기) |
|-----|------|-----------|
| 1–3 | 기초 연구 | 5 |
| 4–6 | 프로토타이핑 | 3 |
| 7–8 | 최적화 / 양산 준비 | 2 |
| 9   | 양산 가능 | 1 |

값은 단일 정수 (이전엔 `(min, max)` 튜플이었으나 backcasting 결정성 보장을 위해 단순화).
[config.py](config.py) 의 `TRL_LEAD_TIME_QUARTERS` 에서 조정.

## 실행

### 설치

```bash
# 의존성은 루트 requirements.txt 에 통합되어 있음
cd Tech-Analysis-Agent
pip install -r requirements.txt
```

LLM provider 는 `.env` 를 통해 설정 (tech_analysis_agent 의 `.env` 와 동일):

```ini
LLM_PROVIDER=anthropic         # 또는 ollama
ANTHROPIC_API_KEY=sk-ant-...
# 혹은
OLLAMA_MODEL=llama3.1:8b
OLLAMA_BASE_URL=http://localhost:11434
```

### 기본 실행

```bash
# Agent 1 의 출력을 입력으로 사용
python main.py --input ../tech_analysis_agent/output_tech_candidates.json

# 결과는 output_planned_roadmap.json 에 저장
```

### 오케스트레이터 피드백 시뮬레이션

Investment Strategist Agent 가 예산 부족을 이유로 특정 기술을 연기(Shift)
또는 제외(Drop) 시키는 상황을 CLI 로 바로 재현 가능:

```bash
# T02 의 시작을 2026 Q1 로 연기, T04 는 로드맵에서 제외
python main.py \
  --input ../tech_analysis_agent/output_tech_candidates.json \
  --shift "T02:2026 Q1" \
  --drop  "T04"

# 여러 건 지정
python main.py \
  --shift "T02:2026 Q1,T05:2027 Q3" \
  --drop  "T04,T07"
```

Shift 는 해당 기술뿐 아니라 그 기술에 의존하는 후행 기술들도 자동으로
연쇄 지연(Cascade) 처리됩니다 (timeline_calculator 의 Zero-slack 로직).

## 라이브러리 호출 (Orchestration Agent 에서 사용)

```python
from roadmap_planner_agent.graphs.roadmap_graph import run_roadmap_planner

result = run_roadmap_planner(
    tech_candidates=tech_candidates,
    market_context=market_context,
    orchestrator_feedback={
        "shift": [{"tech_id": "T02", "new_start_q": "2026 Q1"}],
        "drop":  ["T04"],
        "text":  ["[portfolio_balance] 단기 우세 — 후기 단계로 일부 기술 미루기"],
    },
)
# result["planned_roadmap"]   : List[RoadmapItem]
# result["dependency_tree"]   : Dict[tech_id, DependencyNode]
# result["timeline_draft"]    : 중간 산출물
```

`orchestrator_feedback` 의 세 채널:

| 채널 | 의미 | 적용 위치 |
|------|------|---------|
| `shift` | 특정 기술의 시작 분기 강제 변경 (cascade 자동 처리) | timeline_calculator |
| `drop`  | 기술 제외 (dropped=True 표시) | timeline_calculator |
| `text`  | Orchestrator REVISE 의 자유 피드백 (한국어/영문 OK) | dependency_analyzer + roadmap_builder 의 LLM 프롬프트 |

`text` 는 LLM 이 시스템 프롬프트와 함께 받아 다음 iter 에서 분기 / 의존성 / phase_name 을 조정하는 데 사용합니다.

## 의존성 분석 강화

`dependency_analyzer` 는 카테고리 계층 (Layer 0/1/2) 간 의존성뿐 아니라
**같은 레이어 안의 정밀한 인과관계** 도 LLM 이 추론하도록 프롬프트에서 예시를 제공
(예: 검사 장비 T01 → 공정 T02 의 결과 검증 / ALD 장비 → ALD 공정 등).
단, hard constraint 는 아니며 명백히 필요한 경우만 의존성 추가하라는 지침 (spec 준수).

## CLI 옵션 요약

| 옵션 | 의미 | 기본값 |
|------|------|--------|
| `--input` / `-i`  | Agent 1 출력 JSON 경로 | `../tech_analysis_agent/output_tech_candidates.json` |
| `--output` / `-o` | 결과 JSON 저장 경로 | `output_planned_roadmap.json` |
| `--shift`         | `"T02:2026 Q1,..."` 형식 Shift 명령 | 없음 |
| `--drop`          | `"T04,T07"` 형식 Drop 명령 | 없음 |
