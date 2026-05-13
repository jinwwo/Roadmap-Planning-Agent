# Interactive Demo — 실행 가이드

Claude Code 스타일의 대화형 웹 데모입니다.

- 🧠 **로컬 LLM** (Ollama) 기본값 — API 키 불필요
- 📡 **Server-Sent Events** 로 에이전트 진행 상황 실시간 스트리밍
- ✋ **HITL 체크포인트** — Agent 1 완료 후 후보 기술을 검토하고 drop/shift 적용 가능
- 🛰️ **완전 오프라인 모드** — USPTO/Tavily 없어도 합성 데이터로 동작

---

## 🚀 한 줄 실행 (Linux/macOS)

```bash
cd tech_roadmap_agent
bash setup.sh        # Ollama 설치 + 모델 pull + pip install + .env 생성
bash run.sh          # 서버 기동 → http://localhost:8000
```

- 다른 모델을 쓰고 싶으면: `bash setup.sh --model qwen2.5:7b-instruct`
- 종료: `bash stop.sh` (백그라운드로 띄운 Ollama 종료)

---

## 수동 설치 (스크립트 없이)

### 1. 설치

```bash
# 의존성은 루트 requirements.txt 에 통합되어 있음
cd Tech-Analysis-Agent
pip install -r requirements.txt
```

### 2. 로컬 LLM 준비 (Ollama)

```bash
# Ollama 설치: https://ollama.com
curl -fsSL https://ollama.com/install.sh | sh   # Linux
# macOS: brew install ollama  또는 https://ollama.com/download

ollama pull llama3.1:8b
ollama serve &                                   # 백그라운드
```

### 3. 환경변수 (오프라인 데모용 — 가장 간단)

`.env` 파일 생성:

```bash
LLM_PROVIDER=ollama
OLLAMA_MODEL=llama3.1:8b
PATENTSVIEW_API_KEY=...
TAVILY_API_KEY=...
USE_MOCK_PATENT=false
USE_MOCK_MARKET=false
```

> 실제 PatentsView/Tavily API 를 쓰려면 `USE_MOCK_*=false` 로 두고 `PATENTSVIEW_API_KEY`, `TAVILY_API_KEY` 를 설정.
> Claude 를 쓰고 싶다면 `LLM_PROVIDER=anthropic`, `ANTHROPIC_API_KEY=sk-ant-...`.

### 4. 서버 실행

```bash
python server.py
# 또는
uvicorn server:app --port 8000
```

브라우저에서 **http://localhost:8000** 열기.

---

## 사용 흐름

1. 좌측 입력창에 자연어 요청:
   - *"2030년까지의 2nm 파운드리 로드맵을 그려줘"*
   - *"차세대 AI 가속기 패키징 기술 로드맵"*
2. 에이전트가 입력을 파싱해 `domain / reference_year / category_hints` 로 변환 → 우측 패널 표시
3. **Agent 1** 실행 — USPTO/Tavily 로 데이터 수집 후 Claude/Ollama 분석. 진행 로그가 터미널 스타일로 실시간 표시됨
4. **HITL 체크포인트** — 후보 기술 카드가 표시됨:
   - `drop` 버튼: 해당 기술 제외
   - `shift` 입력(예: `2026 Q1`) + 버튼: 착수 시점 조정
   - `그대로 진행` 또는 `적용 후 진행`
5. **Agent 2** 실행 — 의존성 트리 → 역산 타임라인 → justification 생성
6. 최종 로드맵이 **분기별 간트 차트** 로 시각화. 각 항목을 펼치면 justification/선행 기술 확인

---

## 아키텍처

```
web/ (vanilla JS)
  │  ↑ SSE
  ▼
server.py (FastAPI)
  │
  ▼
interactive/session.py   ← 파이프라인 orchestrator (백그라운드 스레드)
  ├─ intake (LLM)
  ├─ AnalysisGraph        ← 기존 코드 그대로
  ├─ HITL wait            ← threading.Event 로 대기
  └─ RoadmapGraph         ← orchestrator_feedback 적용
        │
        ├─ llm_factory.get_llm()   ← Ollama/Anthropic 공용 팩토리
        └─ tools/ (USPTO·Tavily·mock)
```

핵심 설계 결정:

- **기존 agent 코드는 거의 수정 없음** — `ChatAnthropic(...)` → `get_llm(...)` 한 줄씩만 변경
- **stdout 캡처** (`interactive/event_bus.py`) 로 agent 의 `print()` 를 SSE 이벤트로 자동 변환 → agent 내부에 훅 주입 불필요
- **HITL 은 LangGraph interrupt 를 쓰지 않고** 세션 레벨에서 두 서브그래프를 분리 호출 → 단순하고 디버그 쉬움
- **Ollama `format="json"`** 자동 활성화 — 소형 로컬 모델의 JSON 파싱 실패 방지

---

## 트러블슈팅

| 증상 | 원인/해결 |
|---|---|
| 422 / 400 on `/api/session` | `LLM_PROVIDER=anthropic` 인데 `ANTHROPIC_API_KEY` 미설정 |
| Ollama 연결 실패 | `ollama serve` 실행 여부, `OLLAMA_BASE_URL` 확인 |
| JSON 파싱 에러 | 더 큰 모델로 교체 (`qwen2.5:14b-instruct`, `llama3.1:70b`) — 7B 급은 복잡한 스키마에서 실패 가능 |
| PatentsView 인증/검색 실패 | `PATENTSVIEW_API_KEY` 확인 또는 임시로 `USE_MOCK_PATENT=true` |
| SSE 끊김 | 프록시(nginx 등)의 버퍼링 — 응답 헤더에 `X-Accel-Buffering: no` 포함돼 있음 |

---

## CLI 모드 (기존 `main.py`) 는 그대로 사용 가능

```bash
python main.py
```

하드코딩된 도메인으로 일괄 실행 → `output_*.json` 저장. 데모와 무관하게 유지됩니다.
