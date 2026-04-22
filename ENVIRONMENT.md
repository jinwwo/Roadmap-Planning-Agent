# 환경 설정 가이드

4개 sibling 에이전트를 하나의 Python 환경에서 돌리기 위한 공용 세팅입니다.

```
Tech-Analysis-Agent/                 ← 레포 루트 (이 폴더)
├── .venv/                            ← 공용 venv (아래 가이드로 생성)
├── .env                              ← 공용 환경변수 (symlink 으로 각 폴더에 공유)
├── .env.example
├── requirements.txt                  ← 4개 폴더 requirements 합집합
├── tech_analysis_agent/
├── roadmap_planner_agent/
├── investment_strategist_agent/
└── orchestration_agent/
```

## 요구사항

| 항목 | 권장값 |
|------|--------|
| OS | Linux (Ubuntu 20.04+) · macOS · WSL2 |
| Python | **3.10 이상** (LangChain 0.3 최소 요건) |
| Python 패키지 매니저 | `uv` (권장) 또는 `pip` |
| GPU | 선택 (Ollama 가 CUDA 자동 감지) |
| 디스크 | 모델 저장 공간 약 5GB (`llama3.1:8b`) |

## LLM provider (둘 중 택 1)

### A) Ollama — 로컬, 무료
`llama3.1:8b` 기준 모델 ~4.9GB. GPU 있으면 자동 활용.

```bash
# 공식 설치 (sudo 필요)
curl -fsSL https://ollama.com/install.sh | sh

# 또는 sudo 없이 user-local 설치
mkdir -p ~/.local/ollama
curl -L https://github.com/ollama/ollama/releases/download/v0.21.0/ollama-linux-amd64.tar.zst \
  -o /tmp/ollama.tar.zst
python -c "import zstandard,tarfile; tarfile.open(fileobj=zstandard.ZstdDecompressor().stream_reader(open('/tmp/ollama.tar.zst','rb')), mode='r|').extractall('$HOME/.local/ollama')"
export PATH="$HOME/.local/ollama/bin:$PATH"

# 공통
ollama serve > /tmp/ollama.log 2>&1 &
ollama pull llama3.1:8b
```

### B) Anthropic Claude — 클라우드, 유료
```bash
# .env 에 다음 두 줄
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
```

## 원샷 설치 (권장)

```bash
cd Tech-Analysis-Agent
bash scripts/setup.sh                  # ollama 기본
# 또는
bash scripts/setup.sh --anthropic      # Ollama 설치 스킵, API 키는 수동 입력
```

`setup.sh` 가 하는 일:
1. `uv venv --python 3.10 .venv`
2. 루트의 `requirements.txt` 설치 (4개 폴더 합집합)
3. `.env` 없으면 `.env.example` 에서 복사
4. 각 sibling 폴더에 `.env -> ../.env` symlink 생성
5. (Ollama 모드) 데몬 기동 + 모델 pull

## 수동 설치

```bash
cd Tech-Analysis-Agent

# 1. venv (Python 3.10)
uv venv --python 3.10 .venv && source .venv/bin/activate
# 또는: python3.10 -m venv .venv && source .venv/bin/activate

# 2. 공용 requirements
pip install -r requirements.txt       # 또는 uv pip install -r requirements.txt

# 3. 공용 .env (루트에 하나만 두고 각 폴더에서 symlink)
cp .env.example .env
for d in tech_analysis_agent roadmap_planner_agent \
         investment_strategist_agent orchestration_agent; do
  ln -sf ../.env "$d/.env"
done
```

## `.env` 키 레퍼런스

`.env.example` 참고. 주요 항목:

| 키 | 의미 | 기본값 |
|----|------|--------|
| `LLM_PROVIDER` | `ollama` 또는 `anthropic` | `ollama` |
| `ANTHROPIC_API_KEY` | Claude 사용 시 필수 | (비어있음) |
| `CLAUDE_MODEL` | Claude 모델 | `claude-sonnet-4-20250514` |
| `OLLAMA_MODEL` | 로컬 모델 | `llama3.1:8b` |
| `OLLAMA_BASE_URL` | Ollama 서버 주소 | `http://localhost:11434` |
| `TAVILY_API_KEY` | 시장 데이터 (선택) | (비어있음) |
| `USE_MOCK_PATENT` | USPTO mock 사용 | `1` |
| `USE_MOCK_MARKET` | Tavily mock 사용 | `1` |
| `MAX_ORCHESTRATOR_ITERATIONS` | Review REVISE 루프 상한 | `2` |
| `SUBPROCESS_TIMEOUT_SEC` | sibling 호출 timeout | `900` |

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
python server.py                       # http://localhost:8000
```

### 웹 데모 (Tech Analysis 만, 기존 친구 구현)
```bash
cd tech_analysis_agent && bash scripts/run.sh
```

## 트러블슈팅

| 증상 | 원인 / 해결 |
|---|---|
| `ModuleNotFoundError: No module named 'langchain_ollama'` | venv 활성화 안 됨 — `source .venv/bin/activate` |
| `ollama: command not found` | 미설치 또는 PATH 누락 — `export PATH=$HOME/.local/ollama/bin:$PATH` |
| `ConnectionError: http://localhost:11434` | `ollama serve &` 로 데몬 기동 |
| `Could not find Python 3.10` | `uv python install 3.10` |
| sibling 호출 timeout | `.env` 의 `SUBPROCESS_TIMEOUT_SEC` 증가 |
| JSON 파싱 실패 (Ollama) | 7B 모델 한계 — 큰 모델 사용 (`qwen2.5:14b`, `llama3.1:70b`) 또는 Anthropic 전환 |
