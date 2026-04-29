# Orchestration Agent — Interactive Web Demo

4-에이전트 오케스트레이션을 **브라우저에서 실시간**으로 볼 수 있는 데모입니다.
`tech_analysis_agent/DEMO.md` 의 인터랙티브 웹 데모 패턴(FastAPI + SSE +
vanilla JS) 을 그대로 확장했습니다.

- 🧠 **로컬 LLM (Ollama) 기본** — API 키 불필요
- 🧩 **Agent on/off 토글** — 체크박스로 `--agent "1 2 3"` 와 동일한 ablation 실험
- 📡 **Server-Sent Events** — 각 에이전트 로그/진행 상황 터미널 스타일로 실시간 스트리밍
- 🧪 **데이터 흐름 시각화** — 각 에이전트별 **IN → OUT** 카드로 입출력 표시
- 🔁 **ACCEPT / REVISE 루프 시각화** — REVISE 시 재실행 대상을 배너로 표시, 각 iter 는 별도 블록
- 🛰️ **완전 오프라인 모드** — USPTO / Tavily 없이도 mock 데이터로 끝까지 동작

---

## 🚀 한 줄 실행

먼저 루트에서 공용 세팅 한번 (`.venv` + `.env` + Ollama):

```bash
cd Tech-Analysis-Agent
bash scripts/setup.sh              # (첫 세팅, 약 5분)
```

세팅 끝났으면:

```bash
cd orchestration_agent
bash scripts/run.sh                 # 포트 8000
```

브라우저에서 **http://localhost:8000**

> 포트 변경: `PORT=9000 bash scripts/run.sh` 또는 `bash scripts/run.sh --port 9000`

---

## 수동 실행

```bash
cd Tech-Analysis-Agent
source .venv/bin/activate
export PATH="$HOME/.local/ollama/bin:$PATH"   # user-local ollama 설치 시

# Ollama 데몬이 꺼져있다면
ollama serve > /tmp/ollama.log 2>&1 &

cd orchestration_agent
python server.py                               # 포트 8000
# 또는: uvicorn server:app --host 0.0.0.0 --port 8000 --reload
```

---

## 🖥️ UI 구조

```
┌────────────────────────────────────────────────────────────────────┐
│  Technology Roadmap · Orchestration Agent      LLM: Ollama @ ...   │
├────────────────────────────────────────────────────────────────────┤
│ 좌측 (채팅 + 진행로그)                 │ 우측 (결과 패널)          │
│                                        │                            │
│  사용자: "2nm 파운드리 로드맵 ..."    │  ┌─ Active Agents ──────┐ │
│                                        │  │ ✅ A1  ✅ A2  ✅ A3  │ │
│  🤖 세션 시작                          │  └──────────────────────┘ │
│  ▶ 요청 분석   (spinner · 1.4s)       │  ┌─ Problem Frame ──────┐ │
│  ✓ 요청 분석                           │  │ industry : ...       │ │
│                                        │  │ budget   : ...       │ │
│  ▶ Orchestrator · Problem Setup        │  │ ...                  │ │
│  ✓ Orchestrator · Problem Setup        │  └──────────────────────┘ │
│                                        │  ┌─ Agent 1 · Tech Analyst│
│  ▶ Agent 1 · Technology Analyst        │  │ IN: domain + categories│
│    [Agent 1] USPTO 조회 ...            │  │ OUT: 6 후보 · 목록 … │ │
│    [Agent 1] Claude 분석 ...           │  │ (6 후보 카드 렌더)    │ │
│  ✓ Agent 1 · 6건 완료                 │  └──────────────────────┘ │
│                                        │  ┌─ Agent 2 · Roadmap ──┐ │
│  ▶ Agent 2 · Roadmap Planner           │  │ IN: 6 후보           │ │
│    [Timeline Calculator] ...           │  │ OUT: 6 로드맵 항목   │ │
│  ✓ Agent 2 · 6건 완료                 │  │ (간트 차트)          │ │
│                                        │  └──────────────────────┘ │
│  ▶ Agent 3 · Investment Strategist     │  ┌─ Agent 3 · Investment┐ │
│  ✓ Agent 3 · 3건 완료                 │  │ (Stage 카드 × 3)     │ │
│                                        │  │ Tier 배지 + 5-score  │ │
│  ▶ Orchestrator · Review (iter 1)      │  └──────────────────────┘ │
│  ✓ decision = REVISE                  │  ┌─ Orchestrator Review ─┐ │
│  ↻ rerun: [Roadmap Planner, ...]       │  │ REVISE (iter 1)      │ │
│                                        │  │ TRM 5-grid 평가      │ │
│  ▶ Agent 2 · iter 2                    │  │ Issues / Refine ...  │ │
│  ...                                   │  └──────────────────────┘ │
│  ✓ decision = ACCEPT                  │  ┌─ Report 7-Section ────┐ │
│  ✅ 파이프라인 완료                    │  │ 1. Executive Summary │ │
│                                        │  │ 2. Technology Strategy│ │
│                                        │  │ ... (접이식)          │ │
├────────────────────────────────────────┤  └──────────────────────┘ │
│ [Agent 1] [Agent 2] [Agent 3]  (toggle)│                            │
│ ┌──────────────────────────────┐ [전송]│                            │
│ │ 분석하고 싶은 기술 도메인...  │       │                            │
│ └──────────────────────────────┘       │                            │
└────────────────────────────────────────┴────────────────────────────┘
```

---

## 🎛️ Agent on/off 토글로 A/B 비교

전송 버튼 위 체크박스로 각 Agent 를 끄고 킬 수 있어 **동일한 질의 + 다른 에이전트 조합**을
브라우저에서 직접 비교 가능합니다.

| 조합 | 예상 보고서 품질 변화 |
|------|----------------------|
| A1 · A2 · A3 (전부 ON)  | Baseline |
| A1 · A2 만 (A3 OFF)     | 투자 전략 섹션이 비어있음 · `investment_rationality` 평가 불가 |
| A1 · A3 만 (A2 OFF)     | 로드맵이 flat (단일 phase) · `sequencing.dependency_valid=false` |
| A2 · A3 만 (A1 OFF)     | 후보 기술이 dummy placeholder → `technology_strategy` / `trend_alignment` 얕음 |
| A1 만                    | 로드맵/투자 모두 폴백 → 보고서가 거의 공란, 문제 지점 명확히 드러남 |

서로 다른 조합의 `outputs/web_<세션id>_*.json` 을 `diff` 해서 각 에이전트의 기여를 정량 분석 가능.

### Ablation 에서 무한 REVISE 방지

Agent 를 OFF 한 채 돌리면 Review LLM 이 "Missing investment strategy" 같은
부재 자체를 issue 로 잡고 REVISE 를 낼 수 있어. 그러면 OFF 된 agent 는 rerun
해도 여전히 비어있어 무의미한 loop 가 될 수 있음. 이를 막는 **이중 안전장치**:

1. **시스템 프롬프트 `[SCOPE RULE · DISABLED AGENTS]`** — Review LLM 에게
   "비활성 agent 의 부재를 issue 로 올리지 말 것, `rerun_agents` 에 포함하지
   말 것, 평가 불가 축은 `N/A (agent disabled)` 로 표기" 라고 명시.

2. **파이프라인 가드레일** — LLM 이 규칙을 무시해도 자동 보정:
   - `rerun_agents` 에서 OFF agent 자동 필터링
   - 필터링 후 rerun 대상 없는 REVISE → **ACCEPT 로 강제 전환**
   - ACCEPT 전환 시 `generate_final_report()` 로 7-섹션 보고서 생성

결과: `--agent "1 2"` 든 `--agent "1"` 이든 **반드시 유한 시간 안에 수렴** →
최종 보고서 JSON 생성 → 조합 간 비교 가능.

---

## 🧩 데이터 흐름 시각화

각 에이전트 결과 상단에 **IN → OUT 카드** 가 표시됩니다:

```
┌─ Agent 1 · Technology Analyst ─────────────────────────────┐
│  Tech Candidates → Agent 2                           [6건] │
│  ┌───┬────────────────────────────────────────────────┐   │
│  │IN │ domain: 2nm 파운드리 · year: 2025 ·             │   │
│  │   │ categories: Equipment, Material, Process, ...  │   │
│  ├───┼────────────────────────────────────────────────┤   │
│  │OUT│ tech_candidates: 6건 ·                          │   │
│  │   │ target_market: ... · boom: 2028 Q1              │   │
│  │   │  • T01 · EUV 리소그래피 (TRL 7, score 83.2)     │   │
│  │   │  • T02 · DSA 패터닝 장비 (TRL 6, score 80.3)    │   │
│  │   │  ... 4 more                                      │   │
│  └───┴────────────────────────────────────────────────┘   │
└───────────────────────────────────────────────────────────┘
```

Agent 2, 3 도 동일 포맷으로, **앞 단계의 OUT 이 다음 단계의 IN** 이 되는 흐름이 자연스럽게
시각화됩니다. Orchestrator Review 카드는 모든 에이전트 결과 + ProblemFrame 을 집계해서
decision + 보고서를 산출하는 구조를 표시합니다.

---

## 🏗️ 아키텍처

```
web/app.js (vanilla JS)
  │  ↑ SSE 이벤트 스트림
  ▼
server.py (FastAPI)
  │
  ▼
interactive/session.py         ← 세션 스레드 (한 세션 = 한 대화)
  ├─ intake (LLM: 자연어 → domain/year/categories/industry/objective)
  ├─ events_to(bus.emit)        ← pipeline.py 구조화 이벤트 훅
  ├─ stream_subprocess_stdout(bus.log)  ← sibling stdout 스트리밍
  └─ capture_stdout_to(bus)     ← pipeline 자체 print() 캡처
        │
        ▼
pipeline.py · run_orchestration(...)
  ├─ Orchestrator Setup           ← ProblemFrame 구조화
  ├─ subprocess: tech_analysis_agent → tech_candidates.json
  ├─ subprocess: roadmap_planner_agent → planned_roadmap.json
  ├─ subprocess: investment_strategist_agent → investment_strategy.json
  └─ Orchestrator Review (LLM)    ← ACCEPT / REVISE
        └─ REVISE 시 rerun_agents 재실행 → loop (최대 MAX_ITERATIONS)
```

### 핵심 설계 결정

- **subprocess 기반 오케스트레이션 유지** — sibling 에이전트들은 각자 자기 이름의
  `config.py` / `state.py` 를 가져서 단일 프로세스 import 는 네임 충돌. 별도 Python 프로세스로
  완전 격리해서 호출.
- **stdout 훅 추가** — subprocess 의 `Popen(stdout=PIPE)` 로 라인별로 받아서 SSE 로 포워딩
  (Python-level `sys.stdout` 교체로는 자식 프로세스 출력 못 잡음).
- **구조화 이벤트 훅** — pipeline.py 전환 시점에서 `_emit()` 호출, 웹에서는 이를 SSE 로 받아
  Agent I/O 카드 / TRM 평가 그리드 / 7-섹션 보고서 등을 렌더.
- **HITL 없음** — Orchestrator 가 ACCEPT/REVISE 를 자체 판단 (spec 그대로). 사용자는 시작 전에
  체크박스로 `active_agents` 선택.
- **사용자 정책 입력** — 자연어 요청 외에 시작 폼에서 다음을 직접 지정:
  - `active_agents` 토글 (Agent 1/2/3 ON/OFF)
  - **투자 정책 (Investment Policy)** — Agent 3 입력으로 들어감
    - `risk_appetite` : low / medium / high
    - `investment_horizon` : short / balanced / long
    - `total_budget` : USD 숫자
    - `strategic_priority` : preset (균형/공격적/보수적) 또는 직접 입력
  - 미입력 시 기본값 (medium / balanced / problem_frame.strategic_priorities) 자동 적용

---

## 📤 SSE 이벤트 목록 (클라이언트 구독용)

| type | 발생 시점 | payload 주요 필드 |
|------|-----------|-------------------|
| `session_started` | 세션 생성 직후 | `session_id`, `llm`, `active_agents` |
| `step_start`      | 단계 진입 | `step`, `label` |
| `step_end`        | 단계 종료 | `step`, `count?` |
| `log`             | print() 라인 | `message`, `source` |
| `intake_ready`    | 자연어 파싱 완료 | `domain`, `reference_year`, `category_hints`, `industry`, `objective`, `active_agents` |
| `setup_done`      | ProblemFrame 구조화 완료 | `problem_frame`, `active_agents` |
| `candidates_ready`| Agent 1 완료 | `candidates`, `market_context` |
| `roadmap_ready`   | Agent 2 완료 | `planned_roadmap` |
| `strategy_ready`  | Agent 3 완료 | `stages`, `investment_strategy` |
| `review_done`     | Orchestrator Review 완료 | `review`, `iteration` |
| `refine`          | REVISE 결정 | `rerun_agents`, `feedback`, `iteration` |
| `final`           | 파이프라인 끝 | `result` (slim) |
| `error`           | 예외 발생 | `message`, `traceback` |
| `done`            | 세션 종료 | `ok` |
| `ping`            | 유휴 heartbeat | - |

---

## 🧯 트러블슈팅

| 증상 | 원인 / 해결 |
|---|---|
| 브라우저에서 `LLM: (status unavailable)` | 서버 미기동 또는 포트 다름 — `bash scripts/run.sh` 재시도 |
| `ConnectionError: http://localhost:11434` | Ollama 데몬이 안 떠있음 — `ollama serve &` |
| SSE 끊김 (아무 메시지 없음) | 프록시 버퍼링 — 응답 헤더 `X-Accel-Buffering: no` 이미 포함. 앞단 nginx 가 있다면 `proxy_buffering off;` 필요 |
| `JSONDecodeError` 로 파이프라인 실패 | 7B 모델이 복잡 스키마 JSON 실패 — 큰 모델(`qwen2.5:14b`, `llama3.1:70b`)로 교체하거나 Anthropic 전환 |
| 진행이 너무 느림 | CPU 추론 중일 가능성. `/tmp/ollama.log` 확인 — GPU 감지 로그 있는지 |
| 첫 요청이 유독 오래 걸림 | 모델 VRAM 로딩 시간 (첫 호출만 40초 내외). 이후 호출은 빠름 |
| 복수 사용자 동시 접속 시 지연 | Ollama 기본 num_parallel=1. `.env` 에 `OLLAMA_NUM_PARALLEL=2` (VRAM 충분 시) |

---

## CLI 모드 (기존 `main.py`) 는 그대로 사용 가능

```bash
python main.py --agent "1 2 3" --out-prefix "full_"
bash ../scripts/run_ablation.sh         # 여러 --agent 조합 한 번에
```

웹 데모와 CLI 모드는 같은 `pipeline.run_orchestration()` 을 호출하므로 결과는 동일합니다.
웹은 실시간 가시성 + I/O 시각화 + 체크박스 UX 를, CLI 는 스크립트 자동화 / 재현 실험에
적합합니다.
