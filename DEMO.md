# Technology Roadmap Multi-Agent · 전체 데모 가이드

4개의 sibling 에이전트가 순차/피드백 구조로 연결되어
**사용자 입력 → 후보 기술 → 로드맵 → 투자 전략 → TRM 보고서** 까지 자동 생성합니다.

- 🧠 **로컬 LLM (Ollama)** 기본 — API 키 불필요
- 🔁 **ACCEPT / REVISE 루프** — Orchestrator 가 결과를 평가하고 특정 agent 만 재실행
- 🧩 **Agent 단위 on/off** — `--agent "1 2 3"` 로 ablation / 보고서 품질 비교 가능
- 🛰️ **완전 오프라인 모드** — USPTO/Tavily 없어도 mock 데이터로 동작

---

## 🏗️ 아키텍처

```
Tech-Analysis-Agent/                    ← GitHub 레포 루트
├── tech_analysis_agent/                  ← Agent 1  (Technology Analyst)
├── roadmap_planner_agent/                ← Agent 2  (Roadmap Planner)
├── investment_strategist_agent/          ← Agent 3  (Investment Strategist)
├── orchestration_agent/                  ← Orchestrator (본 데모 진입점)
│     └── main.py  ← argparse --agent "1 2 3"
│
├── .venv/                                ← 공용 Python 3.10 venv (uv)
├── .env                                  ← 공용 환경변수 (각 폴더에서 symlink)
└── scripts/
    ├── setup.sh                          ← 원샷 설치 스크립트
    ├── run_ablation.sh                   ← --agent 조합 일괄 실행
    └── smoke_test.py                     ← LLM 없이 뼈대 검증
```

## 🔄 전체 파이프라인

```
[User Input: domain, year, budget, priorities, ...]
        │
        ▼
   Orchestrator Setup           ← ProblemFrame 구조화 + Agent 역할 배분
        │
        │  (subprocess)
        ▼
 ┌─ Agent 1 · Technology Analyst       ← tech_analysis_agent
 │    USPTO + Tavily + Claude/Ollama
 │    → tech_candidates.json
 │
 ├─ Agent 2 · Roadmap Planner          ← roadmap_planner_agent
 │    Dependency Tree + TRL 역산 + Zero-slack
 │    → planned_roadmap.json
 │
 └─ Agent 3 · Investment Strategist    ← investment_strategist_agent
      Stage 집계 + 5-지표 평가 + Tier 도출
      → investment_strategy.json

        │
        ▼
   Orchestrator Review (LLM)    ← TRM 5-축 평가
        │                           (feasibility / sequencing / alignment /
        │                            investment rationality / portfolio balance)
        │
        ├── ACCEPT  → 7-섹션 최종 report 생성 → END
        └── REVISE  → refinement.rerun_agents 만 재실행 → Review 반복
                       (최대 MAX_ORCHESTRATOR_ITERATIONS 회)
```

각 sibling 은 subprocess 로 독립 실행되어 config/state 네임 충돌 없이 깔끔하게 연결됩니다.

---

## 🚀 한 줄 실행 (Linux / macOS)

```bash
cd Tech-Analysis-Agent
bash scripts/setup.sh                   # uv 로 venv + 의존성 + .env 심볼릭링크
source .venv/bin/activate               # (setup.sh 는 서브셸이라 다시 activate 필요)
cd orchestration_agent
python main.py                           # 세 Agent 모두 ON · 기본 도메인 (CLI)
# 또는 웹 데모:
bash scripts/run.sh                      # http://localhost:8000 (interactive)
```

웹 데모의 자세한 사용법 / Agent 토글로 A/B 비교 / SSE 이벤트 스키마는
[orchestration_agent/DEMO.md](orchestration_agent/DEMO.md) 참고.

결과물:
- `orchestration_agent/outputs/tech_candidates.json`
- `orchestration_agent/outputs/planned_roadmap.json`
- `orchestration_agent/outputs/investment_strategy.json`
- `orchestration_agent/outputs/orchestrator_report.json`  ← **최종 TRM 보고서**

---

## 🛠️ 수동 설치 (스크립트 없이)

### 1. 공용 venv + 의존성

```bash
cd Tech-Analysis-Agent
uv venv --python 3.10 .venv                  # 또는 python3.10 -m venv .venv
source .venv/bin/activate

# 루트의 통합 requirements.txt 한 번에 설치
uv pip install -r requirements.txt
```

### 2. `.env` (공용)

```bash
cp .env.example .env

# 각 에이전트 폴더에서 공용 .env 를 참조하도록 symlink
for d in tech_analysis_agent roadmap_planner_agent \
         investment_strategist_agent orchestration_agent; do
  ln -sf ../.env "$d/.env"
done
```

`.env` 내용 (기본: 오프라인 Ollama + Mock):

```ini
LLM_PROVIDER=ollama
OLLAMA_MODEL=llama3.1:8b
OLLAMA_BASE_URL=http://localhost:11434

# tech_analysis_agent 의 외부 API (없어도 mock 자동 사용)
TAVILY_API_KEY=
PATENTSVIEW_API_KEY=
USE_MOCK_MARKET=0
USE_MOCK_PATENT=0

# Orchestrator
MAX_ORCHESTRATOR_ITERATIONS=2
SUBPROCESS_TIMEOUT_SEC=900
```

### 3. Ollama 준비

```bash
# 공식 설치
curl -fsSL https://ollama.com/install.sh | sh

# 또는 sudo 없이 user-local 설치 (GitHub release 직접)
mkdir -p ~/.local/ollama
curl -L https://github.com/ollama/ollama/releases/download/v0.21.0/ollama-linux-amd64.tar.zst \
  | python -c "import zstandard,sys,tarfile; tarfile.open(fileobj=zstandard.ZstdDecompressor().stream_reader(sys.stdin.buffer), mode='r|').extractall('$HOME/.local/ollama')"
export PATH="$HOME/.local/ollama/bin:$PATH"

# 데몬 + 모델
ollama serve > /tmp/ollama.log 2>&1 &
ollama pull llama3.1:8b
```

### 4. 실행

```bash
cd orchestration_agent
python main.py
```

또는 Anthropic Claude 사용:

```bash
# .env 에서 LLM_PROVIDER=anthropic, ANTHROPIC_API_KEY=sk-ant-... 로 변경
python main.py
```

---

## 🧪 Agent 유무에 따른 보고서 성능 비교 (ablation)

```bash
cd orchestration_agent

# baseline: 세 에이전트 모두 ON
python main.py --agent "1 2 3" --out-prefix "full_"

# Agent 3 제외 (투자 전략 없이 로드맵만)
python main.py --agent "1 2"   --out-prefix "noA3_"

# Agent 2 제외 (역산/의존성 없는 flat 로드맵)
python main.py --agent "1 3"   --out-prefix "noA2_"

# Agent 1 제외 (더미 후보로 시작)
python main.py --agent "2 3"   --out-prefix "noA1_"

# Agent 1 단독 (상한 비교)
python main.py --agent "1"     --out-prefix "onlyA1_"
```

또는 원샷:

```bash
bash scripts/run_ablation.sh                         # 기본 4종 실행
bash scripts/run_ablation.sh full noA3 onlyA1         # 선택 조합만
```

각 조합의 `<prefix>orchestrator_report.json` 에서 `review.report.executive_summary` /
`trm_assessment` 를 비교하면 **어떤 에이전트가 어떤 품질 차이를 만드는지** 확인 가능.

| Agent | OFF 시 대체 산출물 | 보고서에 예상되는 영향 |
|-------|--------------------|------------------------|
| 1     | 더미 후보 5개 + 고정 market_context | `technology_strategy` / `trend_alignment` 얕아짐 |
| 2     | flat roadmap (단일 phase, 의존성 없음) | `roadmap_structure`, `sequencing.dependency_valid` 악화 |
| 3     | 빈 stages / investment_strategy | `investment_strategy`, `investment_rationality` 공란 또는 추정 |

Orchestrator Review 프롬프트는 어떤 Agent 가 OFF 되었는지 명시하므로,
LLM 이 **"이 입력은 degraded 다"** 를 자체적으로 인지하고 평가에 반영합니다.

### Ablation 의 무한 REVISE 방지 · SCOPE RULE + 가드레일

Agent 를 OFF 한 채로 실험하면 Review LLM 이 "Missing investment strategy" 같은
**부재 자체를 issue 로 잡고 REVISE 판단** → 그러나 OFF 된 agent 는 rerun 해도
여전히 빈 결과 → 무의미한 iteration loop 로 빠질 위험.

이를 막기 위해 **이중 안전장치**를 넣었습니다:

1. **시스템 프롬프트 `[SCOPE RULE · DISABLED AGENTS]` 섹션**
   ([orchestration_agent/agents/orchestrator.py](orchestration_agent/agents/orchestrator.py))
   에 LLM 대상 명시적 지시:
   - 비활성 agent 의 output 부재는 issue 아님
   - `refinement.rerun_agents` 에 비활성 agent 포함 금지
   - 평가 불가 TRM 축은 `comment: "N/A (agent disabled)"` 로 표기
   - 이슈가 전부 비활성 agent 에서 기인하면 REVISE 대신 ACCEPT

2. **파이프라인 가드레일** (LLM 이 지시를 무시해도 자동 보정):
   - `rerun_agents` 에서 비활성 agent 자동 필터링
   - 필터링 후 rerun 대상이 비어있고 여전히 REVISE 면 **ACCEPT 로 강제 전환**
   - ACCEPT 전환 시 `generate_final_report()` 전용 LLM 콜로 7-섹션 보고서 생성

결과적으로 `bash scripts/run_ablation.sh` 같은 일괄 실험이 **각 조합마다 유한
시간 안에 수렴**하고 각각 최종 보고서 JSON 이 생성되어 비교 가능.

---

## 🧯 트러블슈팅

| 증상 | 원인 / 해결 |
|---|---|
| `ModuleNotFoundError: No module named 'config'` | 각 에이전트는 자기 폴더 기준 import — `cd <agent>` 후 실행하거나 orchestration_agent 통해 호출 |
| `sibling 폴더를 찾을 수 없습니다` | 4개 폴더가 `Tech-Analysis-Agent/` 아래 sibling 으로 있어야 함. 폴더 이동/이름 변경 확인 |
| Ollama 연결 실패 (`ConnectionError`) | `curl http://localhost:11434/api/tags` 로 데몬 확인. 없으면 `ollama serve &` |
| JSON 파싱 에러 (Review 강제 ACCEPT fallback) | 7B 모델이 복잡한 JSON 스키마 실패 가능 — `qwen2.5:14b-instruct`, `llama3.1:70b` 같은 큰 모델로 교체 |
| USPTO 503 / 400 | `.env` 에 `USE_MOCK_PATENT=1` 설정 |
| subprocess timeout | 긴 분석 시 `SUBPROCESS_TIMEOUT_SEC=1800` (30분) 등으로 조정 |

---

## 🔎 단일 에이전트 독립 실행 (데모 없이 디버깅용)

각 폴더는 standalone 으로도 동작하도록 자체 CLI 를 가집니다:

```bash
# Agent 1 단독 (tech_analysis_agent 의 기존 main.py — 하드코딩된 도메인으로 일괄 실행)
cd tech_analysis_agent && python main.py

# Agent 2 단독 (Agent 1 의 출력 JSON 을 입력으로)
cd roadmap_planner_agent && python main.py \
  --input ../tech_analysis_agent/output_tech_candidates.json

# Agent 3 단독 (Agent 2 의 출력 JSON 을 입력으로)
cd investment_strategist_agent && python main.py \
  --roadmap ../roadmap_planner_agent/output_planned_roadmap.json \
  --tech    ../tech_analysis_agent/output_tech_candidates.json

# Orchestration 통합 (위 흐름 + LLM 평가/재시도 + 최종 보고서)
cd orchestration_agent && python main.py
```

---

## 🔬 LLM 없는 뼈대 검증

```bash
source .venv/bin/activate
python scripts/smoke_test.py
```

위 스크립트는 LLM 호출 없이 다음을 검증합니다:
- Roadmap Planner 의 TRL 역산 + Zero-slack 불변량
- Investment Strategist 의 stage 집계
- Orchestrator 의 Setup + Agent OFF 폴백 3종

LLM 호출 의존하는 부분은 import 만 확인하므로 LLM 없이도 빠르게 정상성 확인 가능.

---

## 📂 입출력 포맷 요약

### Orchestrator 최종 보고서 (`orchestrator_report.json`)

```json
{
  "problem_frame": {
    "industry": "...",
    "company_type": "...",
    "time_horizon": "2025-2030",
    "total_budget": 5000000000,
    "objective": "...",
    "strategic_priorities": [ "...", "...", "..." ]
  },
  "active_agents": ["1", "2", "3"],
  "iteration": 1,
  "review": {
    "decision": "ACCEPT",
    "trm_assessment": {
      "feasibility":       { "budget_feasible": true, "schedule_feasible": true, "comment": "..." },
      "sequencing":        { "dependency_valid": true, "comment": "..." },
      "strategic_alignment": { "company_fit": 0.8, "future_trend_alignment": 0.9, "comment": "..." },
      "investment_rationality": { "over_invested": [], "under_invested": [], "comment": "..." },
      "portfolio_balance": { "short_long_balance": 0.7, "risk_balance": 0.65, "comment": "..." }
    },
    "issues": [],
    "refinement": { "rerun_agents": [], "feedback": [] },
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
  },
  "artifact_paths": {
    "tech_candidates": "...",
    "planned_roadmap": "...",
    "investment_strategy": "..."
  }
}
```

REVISE 가 최종 결정인 경우 `report` 필드는 비워지고 `diagnostic_summary` 만 채워집니다.

---

## 📚 추가 참고

- 에이전트별 상세 설계: `<각 폴더>/README.md`
- Agent 1 (기존) 의 인터랙티브 웹 데모: `tech_analysis_agent/DEMO.md`
- TRM 평가 원칙 / 프롬프트 전문: `orchestration_agent/agents/orchestrator.py` 의 `REVIEW_SYSTEM_PROMPT`
