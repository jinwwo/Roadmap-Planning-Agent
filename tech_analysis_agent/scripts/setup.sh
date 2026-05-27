#!/usr/bin/env bash
# setup.sh
# ─────────
# Tech Roadmap Agent - Interactive Demo 자동 설치 스크립트 (Linux/macOS)
#
# 수행 단계:
#   1) Ollama 설치 (없으면)
#   2) Ollama 데몬 기동 (백그라운드)
#   3) LLM 모델 pull (기본: gemma3:27b)
#   4) Python 패키지 설치
#   5) .env 생성 (없으면, 오프라인 데모 프리셋)
#
# 사용법:
#   bash setup.sh            # 전체 설치
#   bash setup.sh --model qwen2.5:7b-instruct
#
# 설치 후 실행:
#   bash run.sh

set -e

MODEL="${OLLAMA_MODEL:-gemma3:27b}"
PORT="${PORT:-8000}"

# ── 인자 파싱 ──────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --model) MODEL="$2"; shift 2 ;;
    --port)  PORT="$2";  shift 2 ;;
    -h|--help)
      echo "Usage: bash setup.sh [--model MODEL] [--port PORT]"
      exit 0 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

log() { printf "\033[1;36m[setup]\033[0m %s\n" "$*"; }
warn(){ printf "\033[1;33m[warn ]\033[0m %s\n" "$*"; }
die() { printf "\033[1;31m[error]\033[0m %s\n" "$*"; exit 1; }

# ── 사전 도구: curl 또는 wget ─────────────────────────────
have() { command -v "$1" >/dev/null 2>&1; }
download() {
  # download URL OUT
  if have curl;   then curl -fsSL "$1" -o "$2"
  elif have wget; then wget -q "$1" -O "$2"
  else return 1; fi
}

ensure_downloader() {
  if have curl || have wget; then return 0; fi
  log "curl/wget 가 없습니다. 설치를 시도합니다…"
  if have apt-get; then
    SUDO=""; [[ $EUID -ne 0 ]] && SUDO="sudo"
    $SUDO apt-get update -qq && $SUDO apt-get install -y -qq curl ca-certificates \
      || die "curl 자동 설치 실패. 'apt-get install -y curl' 를 수동 실행하세요."
  elif have yum; then
    SUDO=""; [[ $EUID -ne 0 ]] && SUDO="sudo"
    $SUDO yum install -y curl ca-certificates || die "curl 자동 설치 실패"
  elif have apk; then
    apk add --no-cache curl ca-certificates || die "curl 자동 설치 실패"
  else
    die "curl 또는 wget 이 필요합니다. 수동으로 설치 후 재실행."
  fi
}

# ── 1. Ollama 설치 ────────────────────────────────────────
if ! have ollama; then
  ensure_downloader
  log "Ollama 가 설치되어 있지 않습니다. 공식 설치 스크립트를 실행합니다…"
  OS="$(uname -s)"
  if [[ "$OS" == "Linux" ]]; then
    TMP="$(mktemp)"
    download "https://ollama.com/install.sh" "$TMP" || die "install.sh 다운로드 실패"
    sh "$TMP" || die "Ollama 설치 실패 (install.sh)"
    rm -f "$TMP"
  elif [[ "$OS" == "Darwin" ]]; then
    if have brew; then
      brew install ollama || die "Ollama 설치 실패 (brew)"
    else
      die "macOS 에서 Homebrew 없이 자동 설치 불가. https://ollama.com/download 에서 수동 설치 후 재실행."
    fi
  else
    die "지원하지 않는 OS: $OS. https://ollama.com/download 참고."
  fi
else
  log "Ollama 이미 설치됨: $(ollama --version 2>/dev/null || echo 'unknown')"
fi

# ── 2. Ollama 데몬 기동 ──────────────────────────────────
if ! (have curl && curl -fsS --max-time 2 http://localhost:11434/api/tags >/dev/null 2>&1) || (have wget && wget -q --timeout=2 -O /dev/null http://localhost:11434/api/tags); then
  log "Ollama 데몬이 실행 중이 아닙니다. 백그라운드로 기동합니다…"
  mkdir -p .run
  nohup ollama serve > .run/ollama.log 2>&1 &
  echo $! > .run/ollama.pid
  # 최대 20초 대기
  for i in $(seq 1 20); do
    if (have curl && curl -fsS --max-time 2 http://localhost:11434/api/tags >/dev/null 2>&1) || (have wget && wget -q --timeout=2 -O /dev/null http://localhost:11434/api/tags); then
      log "Ollama 기동 완료 (pid=$(cat .run/ollama.pid))"
      break
    fi
    sleep 1
    if [[ $i -eq 20 ]]; then die "Ollama 데몬 기동 실패 (.run/ollama.log 확인)"; fi
  done
else
  log "Ollama 데몬 이미 실행 중 (http://localhost:11434)"
fi

# ── 3. 모델 pull ──────────────────────────────────────────
if ollama list 2>/dev/null | awk 'NR>1{print $1}' | grep -qx "$MODEL"; then
  log "모델 이미 존재: $MODEL"
else
  log "모델 pull 중: $MODEL (수 GB, 시간 걸릴 수 있음)"
  ollama pull "$MODEL" || die "모델 pull 실패: $MODEL"
fi

# ── 4. Python 패키지 ──────────────────────────────────────
if ! command -v python3 >/dev/null 2>&1 && ! command -v python >/dev/null 2>&1; then
  die "Python 이 설치되어 있지 않습니다."
fi
PY="$(command -v python3 || command -v python)"
log "Python 패키지 설치 중 ($PY)…"
"$PY" -m pip install --quiet --disable-pip-version-check -r ../requirements.txt \
  || die "pip install 실패 — 가상환경 사용을 권장합니다"

# ── 5. .env 생성 ──────────────────────────────────────────
if [[ ! -f .env ]]; then
  log ".env 파일 생성 (실제 API 모드 프리셋 — API key 입력 필요)"
  cat > .env <<EOF
LLM_PROVIDER=ollama
OLLAMA_MODEL=$MODEL
OLLAMA_BASE_URL=http://localhost:11434
TAVILY_API_KEY=
PATENT_DATA_PROVIDER=kipris
KIPRIS_API_KEY=
USE_MOCK_PATENT=false
USE_MOCK_MARKET=false
EOF
else
  warn ".env 가 이미 존재합니다 — 덮어쓰지 않습니다. 필요시 .env.example 참고."
fi

log "✅ 설치 완료!"
log "   서버 실행: bash run.sh    (또는 python server.py)"
log "   브라우저 : http://localhost:$PORT"
