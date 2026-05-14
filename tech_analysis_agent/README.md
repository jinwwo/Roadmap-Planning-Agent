# Technology Roadmap AI Agent System

LangGraph 기반 계층형 멀티에이전트 시스템으로, 특허 데이터와 시장 인텔리전스를 분석하여 기업의 기술 개발 로드맵을 자동으로 설계합니다.

---

## 전체 파이프라인

```
[사용자 입력: 도메인 / 기준연도 / 카테고리 힌트]
        │
        ▼
┌──────────────────────────────┐
│   Global Orchestrator        │  ← global_graph.py
│                              │
│  ┌────────────────────────┐  │
│  │  Agent 1               │  │
│  │  Technology Analysis   │  │  ← analysis_graph.py
│  │  ├─ Patent Agent       │  │     KIPRIS API + Claude/Ollama
│  │  ├─ Market Agent       │  │     Tavily API + Claude
│  │  └─ Aggregator         │  │     final_score 산출
│  └──────────┬─────────────┘  │
│             │ tech_candidates│
│  ┌──────────▼─────────────┐  │
│  │  Agent 2               │  │
│  │  Roadmap Planner       │  │  ← roadmap_graph.py
│  │  ├─ Dependency Analyzer│  │     기술 트리 구성
│  │  ├─ Timeline Calculator│  │     역산 알고리즘 (Pure Python)
│  │  └─ Roadmap Builder    │  │     Justification 생성
│  └──────────┬─────────────┘  │
│             │ planned_roadmap│
│  ┌──────────▼─────────────┐  │
│  │  Agent 3               │  │
│  │  Investment Strategist │  │  ← (향후 구현)
│  └────────────────────────┘  │
└──────────────────────────────┘
        │
        ▼
[output_tech_candidates.json]   ← Agent 1 결과
[output_planned_roadmap.json]   ← Agent 2 결과
```

---

## 프로젝트 구조

```
tech_roadmap_agent/
│
├── main.py                   # 진입점 — 전체 파이프라인 실행
├── config.py                 # 환경변수 로드 및 전역 설정값
├── state.py                  # LangGraph State TypedDict 전체 정의
├── .env.example              # 환경변수 설정 가이드 (의존성은 루트 requirements.txt)
│
├── tools/                    # 외부 API 클라이언트 (순수 데이터 수집)
│   ├── patent_tools.py       # KIPRIS/mock patent provider router
│   └── market_tools.py       # Tavily Search API 래퍼
│
├── agents/                   # 각 에이전트 노드 함수
│   │                           (LangGraph 노드로 등록되는 단위)
│   ├── patent_agent.py       # [Agent 1-1] 특허 데이터 수집 + Claude 분석
│   ├── market_agent.py       # [Agent 1-2] 시장 데이터 수집 + Claude 분석
│   ├── aggregator.py         # [Agent 1-3] patent/market 점수 통합
│   ├── dependency_analyzer.py# [Agent 2-1] 기술 의존성 트리 구성
│   ├── timeline_calculator.py# [Agent 2-2] TRL 기반 역산 타임라인 계산
│   └── roadmap_builder.py    # [Agent 2-3] 최종 로드맵 + Justification
│
└── graphs/                   # LangGraph 그래프 (오케스트레이터)
    ├── analysis_graph.py     # Agent 1 로컬 오케스트레이터
    ├── roadmap_graph.py      # Agent 2 로컬 오케스트레이터
    └── global_graph.py       # 전체 파이프라인 글로벌 오케스트레이터
```

---

## 파일별 역할 상세

### 핵심 설정

| 파일 | 역할 |
|------|------|
| `main.py` | 파이프라인 실행 진입점. 도메인/연도 설정 및 결과 JSON 저장 |
| `config.py` | `.env` 로드, API 키, 가중치, 임계값 등 전역 상수 관리 |
| `state.py` | `AnalysisState`, `RoadmapState`, `GlobalState` 등 모든 TypedDict 정의 |

### tools/ — 데이터 수집 레이어

| 파일 | 역할 | API |
|------|------|-----|
| `patent_tools.py` | 기업 특허 포트폴리오 검색, 연도별 출원 트렌드, 피인용 통계 수집 | KIPRIS Plus API 또는 mock/example data |
| `market_tools.py` | 시장 규모, 투자 동향, 정책 신호, 경쟁 구도, 상용화 타임라인 검색 | Tavily Search (무료 1000회/월) |

### agents/ — 분석 레이어

| 파일 | 역할 | LLM 사용 |
|------|------|----------|
| `patent_agent.py` | KIPRIS 원시 데이터 → LLM 분석 → `patent_analysis` JSON | ✅ |
| `market_agent.py` | Tavily 원시 데이터 → Claude 분석 → `market_analysis` JSON | ✅ |
| `aggregator.py` | `final_score = patent×0.45 + market×0.55`, 필터링·정렬 | ❌ |
| `dependency_analyzer.py` | 카테고리 계층 + dependency_hints → Claude가 정밀 의존성 트리 구성 | ✅ |
| `timeline_calculator.py` | TRL 기반 리드 타임 + 위상정렬 + 역산 알고리즘 (Zero-slack 보장) | ❌ |
| `roadmap_builder.py` | 타임라인 초안 → Claude가 phase_name + justification 생성 | ✅ |

### graphs/ — 오케스트레이션 레이어

| 파일 | 역할 |
|------|------|
| `analysis_graph.py` | `patent_agent → market_agent → aggregator` 순서 제어 |
| `roadmap_graph.py` | `dependency_analyzer → timeline_calculator → roadmap_builder` 순서 제어 |
| `global_graph.py` | `technology_analysis → roadmap_planner → investment_strategist` 전체 제어 |

---

## 시작하기

### 1. 설치

```bash
cd scripts
bash setup.sh
```

`setup.sh`가 자동으로 수행하는 작업:
1. Ollama 설치 (없으면)
2. Ollama 데몬 기동
3. LLM 모델 pull (`llama3.1:8b` 기본)
4. Python 패키지 설치 (루트 `../requirements.txt` 통합 사용)
5. `.env` 생성 (오프라인 데모 프리셋)

> 다른 모델을 사용하려면: `bash setup.sh --model qwen2.5:7b`

### 2. 실행

```bash
# 로컬 서버
bash run.sh

# 외부 공개 (Cloudflare 터널)
bash run.sh --public

# tmux 백그라운드 실행
bash run.sh --tmux
```

실행 후 브라우저에서 `http://localhost:8000` 접속.

### 3. 종료

```bash
bash stop.sh
```

### 4. 출력 파일

| 파일 | 내용 |
|------|------|
| `output_tech_candidates.json` | Agent 1 결과: 후보 기술 리스트 (Roadmap Planner 입력 포맷) |
| `output_planned_roadmap.json` | Agent 2 결과: 분기별 개발 로드맵 (Investment Strategist 입력 포맷) |

### Anthropic Claude 사용 시 (선택)

로컬 Ollama 대신 Claude API를 사용하려면 `.env`를 수정하세요:

```ini
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
TAVILY_API_KEY=tvly-...          # 비워두면 Mock 데이터 자동 사용
```

---

## 오케스트레이터 피드백 (Shift / Drop)

`main.py`에서 Investment Strategist의 예산 결정을 수동으로 시뮬레이션할 수 있습니다:

```python
orchestrator_feedback = {
    "shift": [{"tech_id": "T02", "new_start_q": "2026 Q1"}],  # 예산 부족 → 착수 연기
    "drop":  ["T04"],  # 우선순위 제외
}
```

적용 시 `timeline_calculator`가 Shift/Drop을 반영하고 연쇄 지연(Cascade)을 자동 처리합니다.

---

## 사용 API

| API | 용도 | 비용 |
|-----|------|------|
| KIPRIS Plus | 한국 특허/공개 데이터 검색, 기업별 포트폴리오 수집 | API key 필요 |
| Tavily Search | 시장 규모, 투자 동향, 정책 뉴스 | 무료 (1,000 searches/월) |
| Anthropic Claude | LLM 분석 및 서술 생성 | API 사용료 |

---

## 향후 구현 예정

- **Agent 3: Investment Strategist** — 예산 제약 기반 Tier 분류 및 투자 우선순위 결정
- **오케스트레이터 자동 피드백 루프** — Agent 3 결과를 Agent 2에 자동 반영하여 로드맵 재설계
