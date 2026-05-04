# Roadmap Planner Agent (Agent 2)

Technology Analyst Agent (Agent 1) 가 발굴한 기술군을 입력받아,
기술 간 인과관계와 성숙도(TRL) 를 분석하여 시장 개화 시점에 맞춘
**연도/분기별 개발 타임라인**을 설계하는 LangGraph 기반 에이전트.

```
Tech-Analysis-Agent/
├── tech_analysis_agent/       ← Agent 1 (Technology Analyst)
├── roadmap_planner_agent/     ← 본 폴더 (Agent 2)
├── orchestration_agent/       ← (TBD)
└── investment_strategist_agent/ ← (TBD)
```

## 핵심 책임

- **후보 기술 큐레이션** — N개 후보 중 핵심 K개를 5축 균형으로 자율 선별 (`tech_selector`)
- **기술 계층 구조화** — Material/Equipment (Layer 0) → Process (Layer 1) → Architecture/Packaging (Layer 2)
- **타임라인 역산 (Backcasting)** — 목표 시장 개화 분기로부터 lead time 을 거꾸로 계산
- **Zero-slack 검증** — 선행 기술의 완료 분기 ≤ 후행 기술의 시작 분기
- **병목 유발 로드맵 생성** — 자원 제약 고려 없이 기술적 필요 일정 모두 배치
  (이후 Investment Strategist 가 '선택과 집중' 결정)

## 파이프라인

```
  START
    │
    ▼
  [tech_selector]          ← LLM: 후보 N개 → K개 선별 (5축 큐레이션)
    │                         · 점수 / 트렌드 정합 / 카테고리 균형 / 시점 분포 / 중복 제거
    │                         · ROADMAP_TECH_K_MIN 보장 (default 3)
    │                         · final_score 신뢰 (재평가 X) — 단순 큐레이션
    ▼
  [dependency_analyzer]    ← LLM: 기술 트리 구성 + 레이어 할당
    │                         (카테고리 계층 + dependency_hints 정밀화)
    ▼
  [timeline_calculator]    ← pure Python: TRL 기반 역산
    │                         (Kahn's topological sort + backcasting)
    ▼
  [roadmap_builder]        ← LLM: phase_name + justification 생성
    │
    ▼
  END
```

## 파일 구조

| 파일 | 역할 | LLM |
|------|------|-----|
| `main.py`                     | CLI 엔트리 (argparse) | - |
| `config.py`                   | LLM provider, TRL 리드타임, 카테고리 레이어 | - |
| `state.py`                    | LangGraph State TypedDict | - |
| `llm_factory.py`              | Claude / Ollama provider 추상화 | - |
| `agents/tech_selector.py`       | 후보 K개 선별 (5축 큐레이션 + K_MIN 보장) | ✅ |
| `agents/dependency_analyzer.py` | 기술 의존성 트리 구성 | ✅ |
| `agents/timeline_calculator.py` | TRL 역산 알고리즘 + Zero-slack | ❌ |
| `agents/roadmap_builder.py`     | 최종 로드맵 + Justification 생성 | ✅ |
| `graphs/roadmap_graph.py`       | 4-단계 LangGraph 조립 + `run_roadmap_planner()` 헬퍼 | - |

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
      "phase_name": "1단계: 기반 R&D",
      "start_q": "2025 Q1",
      "target_q": "2026 Q2",
      "prerequisites": [],
      "lead_time_quarters": 6,
      "justification": "공정 개발(T03) 을 위해 장비 셋업이 최우선되어야 함. TRL 4 기준 6개 분기 소요 예상."
    }
  ],
  "dependency_tree": { ... },
  "tech_selection": {
    "selected_count": 5,
    "rationale": "8개 후보 중 5개 선별. GAA·BSPDN 트렌드 후보 보존 + 카테고리 균형.",
    "dropped": [
      {"tech_id":"T02","name":"...","reason":"T01 과 기능 중복"},
      {"tech_id":"T08","name":"...","reason":"final_score 낮고 시점 부적합"}
    ]
  }
}
```

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
