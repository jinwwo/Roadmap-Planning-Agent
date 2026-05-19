# 환경 설정 가이드

4개 sibling 에이전트를 하나의 Python 환경에서 돌리기 위한 공용 세팅입니다.

```
Roadmap-Planning-Agent/                ← 레포 루트 (이 폴더)
├── .venv/                              ← 공용 venv (아래 가이드로 생성)
├── .env                                ← 공용 환경변수 (symlink 으로 각 폴더에 공유)
├── .env.example
├── requirements.txt                    ← 통합 의존성 (4개 에이전트 공용)
├── tech_analysis_agent/
├── roadmap_planner_agent/
├── investment_strategist_agent/
└── orchestration_agent/
```

레포 루트의 `container_bootstrap.sh` 와 `run_container.sh` 는 Docker 컨테이너용 자동화 스크립트입니다 (아래 [Docker 컨테이너 셋업](#docker-컨테이너-셋업-권장) 참고).

---

## 요구사항

| 항목 | 권장값 |
|------|--------|
| OS | Linux (Ubuntu 20.04+) · macOS · WSL2 |
| Python | **3.10 이상** (LangChain 0.3 최소 요건) |
| Python 패키지 매니저 | `uv` (권장) 또는 `pip` |
| GPU | Ollama 모드에서 권장 (CUDA 자동 감지) |
| 디스크 | 모델별: `llama3.1:8b` ~5GB, **`qwen3.5:27b` ~17GB** |

---

## Docker 컨테이너 셋업 (권장)

GPU 가 있는 호스트에서 가장 빠르고 깔끔한 방법. **호스트 환경을 건드리지 않습니다.**

### 1) 호스트에서 컨테이너 진입

```bash
# 레포 폴더 안
cd /path/to/Roadmap-Planning-Agent
bash run_container.sh
```

`run_container.sh` 는 `nvcr.io/nvidia/pytorch` 이미지를 띄우고 GPU + 마운트 볼륨을 연결합니다.

### 2) 컨테이너 안에서 부트스트랩 (한 줄)

```bash
bash /workspace/26-tech-roadmap/Roadmap-Planning-Agent/container_bootstrap.sh
```

부트스트랩이 자동으로 처리하는 것:
1. **uv 설치** (Python 패키지 매니저, 빠른 venv 생성)
2. **ollama 설치** (zstd 의존성 자동 처리)
3. **`OLLAMA_MODELS=/workspace/26-tech-roadmap/.ollama_models`** 마운트 영역에 모델 저장
   → 컨테이너 재생성해도 17GB 모델 살아남음
4. **멀티 GPU 환경변수** (호스트에 GPU 2개 이상 있을 때 자동 분산):
   ```
   OLLAMA_SCHED_SPREAD=1        # 모델을 GPU 여러 개에 분산
   OLLAMA_NUM_PARALLEL=4        # 한 runner 가 동시 요청 4개 처리
   OLLAMA_MAX_LOADED_MODELS=1   # 한 모델만 메모리 유지 (재로딩 방지)
   OLLAMA_KEEP_ALIVE=24h
   ```
5. **ollama 데몬 기동**
6. **`scripts/setup.sh` 실행** — venv + requirements + .env symlink + 모델 pull

부트스트랩은 **idempotent** — 다시 실행해도 안전 (이미 깔린 건 스킵).

### 3) 서버 실행

```bash
source /workspace/26-tech-roadmap/Roadmap-Planning-Agent/.venv/bin/activate
cd /workspace/26-tech-roadmap/Roadmap-Planning-Agent/orchestration_agent
bash scripts/run.sh
```

브라우저로 컨테이너 IP 의 8000 포트 접속.

---

## 직접 설치 (Docker 안 쓸 때)

### LLM provider — Ollama (로컬, 무료)

권장 모델 — 한국어/JSON 안정성 우선:

| 모델 | 크기 | GPU 메모리 | 적합한 경우 |
|---|---|---|---|
| **`qwen3.5:27b`** | ~17GB | 단일 24GB+ 또는 멀티 GPU 분산 (47GB+ 권장) | **권장** — 한국어 + 복잡한 JSON 안정 |
| `qwen3.5:9b` | ~5GB | 12GB+ | 가벼운 GPU |
| `qwen2.5:14b` | ~9GB | 16GB+ | non-reasoning, JSON 안정 |
| `llama3.1:8b` | ~5GB | 8GB+ | 한국어/JSON 약함 — 기본 데모용 |

```bash
# 공식 설치 (sudo 필요)
curl -fsSL https://ollama.com/install.sh | sh

# 데몬 + 모델 pull
ollama serve > /tmp/ollama.log 2>&1 &
ollama pull qwen3.5:27b   # 또는 다른 모델
```

### LLM provider — Anthropic Claude (클라우드, 유료)

```bash
# .env 에 다음 두 줄
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
```

### 원샷 설치

```bash
cd Roadmap-Planning-Agent
bash scripts/setup.sh                  # ollama 기본
# 또는
bash scripts/setup.sh --anthropic      # Ollama 설치 스킵
```

`setup.sh` 가 하는 일:
1. `uv venv --python 3.10 .venv`
2. 루트 `requirements.txt` 설치 (4개 에이전트 공용)
3. `.env` 없으면 `.env.example` 에서 복사
4. **`.env` 의 `OLLAMA_MODEL` 을 자동으로 source 해서 모델 pull**
5. 각 sibling 폴더에 `.env -> ../.env` symlink 생성

### 수동 설치

```bash
cd Roadmap-Planning-Agent

# 1. venv (Python 3.10)
uv venv --python 3.10 .venv && source .venv/bin/activate
# 또는: python3.10 -m venv .venv && source .venv/bin/activate

# 2. 공용 requirements
uv pip install -r requirements.txt       # 또는 pip install -r requirements.txt

# 3. 공용 .env (루트에 하나만 두고 각 폴더에서 symlink)
cp .env.example .env
for d in tech_analysis_agent roadmap_planner_agent \
         investment_strategist_agent orchestration_agent; do
  ln -sf ../.env "$d/.env"
done
```

---

## `.env` 키 레퍼런스

전체 항목은 [.env.example](.env.example) 참고. 핵심:

### 기본

| 키 | 의미 | 기본값 |
|----|------|--------|
| `LLM_PROVIDER` | `ollama` 또는 `anthropic` | `ollama` |
| `ANTHROPIC_API_KEY` | Claude 사용 시 필수 | (비어있음) |
| `CLAUDE_MODEL` | Claude 모델 | `claude-sonnet-4-20250514` |
| `OLLAMA_MODEL` | 로컬 모델 | `qwen3.5:27b` (권장) |
| `OLLAMA_BASE_URL` | Ollama 서버 주소 | `http://localhost:11434` |
| `TAVILY_API_KEY` | 시장 데이터 (선택) | (비어있음) |
| `USE_MOCK_PATENT` | USPTO mock 사용 | `1` |
| `USE_MOCK_MARKET` | Tavily mock 사용 | `1` |
| `MAX_ORCHESTRATOR_ITERATIONS` | Review REVISE 루프 상한 | `2` |
| `SUBPROCESS_TIMEOUT_SEC` | sibling 호출 timeout | `900` |

### Ollama 고급 — Qwen3.5 사용 시 거의 필수

| 키 | 의미 | 권장값 |
|----|------|--------|
| `OLLAMA_NUM_CTX` | 컨텍스트 윈도우 | `16384` (47GB+ GPU) / `4096` (24GB GPU) |
| **`OLLAMA_NO_THINK`** | Qwen3 thinking 모드 끄기 (`reasoning=False` 자동 적용) | **`1` (Qwen3.5 사용 시 필수)** |
| **`OLLAMA_FORMAT_JSON`** | Ollama 의 `format=json` 강제 grammar | `1` (NO_THINK=1 과 함께면 안전) |
| **`OLLAMA_NUM_PREDICT`** | num_predict 의 **최소 보장 floor**. 호출자가 더 큰 max_tokens 요청하면 그대로 사용 | `2048` (Qwen3.5) |
| `STRATEGIST_LLM_STRATEGY` | Strategist 강도 강제 (`single_call` / `per_stage`) | (auto) |

### Roadmap Planner 옵션

| 키 | 의미 | 기본값 |
|----|------|--------|
| `ROADMAP_TECH_K_MIN` | tech_selector 의 **최소 보존 후보 수** | `3` |
| `ROADMAP_DESIGN_MODE` | Roadmap 설계 모드 — `holistic` (spec LLM 통합) / `hybrid` (Python 결정성) | `holistic` |
| `PATENT_ANALYSIS_METHOD` | Patent prompt variant (`A_current` / `B_lee2009`) | `A_current` |

#### `ROADMAP_DESIGN_MODE` 자세히

- **`holistic`** (default): spec 의 System Prompt 그대로 — `roadmap_designer` 가 LLM 한 번 호출로 dependency tree + TRL lead_time + backcasting + phase_name + justification 모두 종합 판단.
  - 자연스러운 horizon 분포, 의미적 단계 흐름
  - 단점: 결정성 ↓ (LLM 비결정성), TRL lead_time 정확성 약화
- **`hybrid`**: 옛 모드 — `dependency_analyzer (LLM)` + `timeline_calculator (Python)` + `roadmap_builder (LLM)` 3 노드 분리
  - 결정성 ↑ (TRL 1-3=5분기 등 강제, Zero-slack 검증 코드)
  - 단점: chain sparse 시 timeline 이 한 시점 몰리는 경향

### Qwen3.5 사용 시 짚어둘 점

Qwen3 / Qwen3.5 는 hybrid reasoning 모델 (답변 전 `<think>...</think>` 출력). Ollama 의 `format=json` 과 충돌해 **빈 응답 버그** 가 발생할 수 있습니다. 우리 시스템은 자동으로 다음을 처리:

- **`OLLAMA_NO_THINK=1`** → ChatOllama 에 `reasoning=False` 전달 → thinking 자체를 끔
- **`OLLAMA_FORMAT_JSON=1`** → JSON grammar 강제 (thinking 꺼있으면 안전)

→ 이 두 변수가 **`.env.example` 에 이미 권장값으로 적혀 있으니** 그대로 쓰면 OK.

---

## 실행 엔트리

### CLI (원샷 orchestration)
```bash
source .venv/bin/activate
cd orchestration_agent
python main.py --agent "1 2 3"
```

### Agent 유무 A/B 비교
```bash
bash scripts/run_ablation.sh           # full / noA3 / noA2 / noA1 한 번에
```

### 웹 데모 (Orchestration 인터랙티브)
```bash
source .venv/bin/activate
cd orchestration_agent
bash scripts/run.sh                    # http://localhost:8000
```

### 웹 데모 (Tech Analysis 단독)
```bash
cd tech_analysis_agent && bash scripts/run.sh
```

---

## 트러블슈팅

| 증상 | 원인 / 해결 |
|---|---|
| `ModuleNotFoundError: No module named 'langchain_ollama'` | venv 활성화 안 됨 — `source .venv/bin/activate` |
| `ollama: command not found` | 미설치 또는 PATH 누락 — `export PATH=$HOME/.local/ollama/bin:$PATH` |
| `ConnectionError: http://localhost:11434` | `ollama serve &` 로 데몬 기동 |
| `Could not find Python 3.10` | `uv python install 3.10` |
| sibling 호출 timeout | `.env` 의 `SUBPROCESS_TIMEOUT_SEC` 증가 |
| **Qwen3.5 가 빈 응답 (`len=0`)** | thinking + format=json 충돌 — `.env` 에 **`OLLAMA_NO_THINK=1`** 설정 (이미 default) |
| **Qwen3.5 가 잘린 JSON 응답 (`Expecting ',' delimiter`)** | num_predict 부족 — `.env` 의 `OLLAMA_NUM_PREDICT=2048` 확인 |
| GPU 1개만 쓰고 다른 GPU 가 놀음 | ollama 데몬 띄울 때 `OLLAMA_SCHED_SPREAD=1` 환경변수 필요 — `container_bootstrap.sh` 가 자동 설정 |
| 모델이 호출마다 재로딩 (`load_duration` 매번 30s+) | 동시 호출이 GPU 메모리 한계 초과 — `OLLAMA_MAX_LOADED_MODELS=1` + `OLLAMA_NUM_PARALLEL=4` |
| zstd 의존성 누락으로 ollama install 실패 | `apt-get install -y zstd` (`container_bootstrap.sh` 가 자동 처리) |
| `JSONDecodeError` 다른 LLM | 작은 모델 한계 — 큰 모델 (`qwen2.5:14b`, `qwen3.5:27b`) 또는 Anthropic 전환 |
| 컨테이너 재생성 시 모델 17GB 다시 받기 | `OLLAMA_MODELS=/workspace/.../.ollama_models` 으로 마운트 영역 사용 (`container_bootstrap.sh` default) |
