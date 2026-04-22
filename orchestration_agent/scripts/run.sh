#!/usr/bin/env bash
# orchestration_agent/scripts/run.sh
# ───────────────────────────────────
# Orchestration Agent 웹 데모 서버 기동.
# (4-에이전트 오케스트레이션 + SSE 기반 실시간 UI)
#
# 수행 단계:
#   1) venv 확인 (없으면 안내)
#   2) Ollama 데몬 기동 (user-local 우선, 이미 떠있으면 스킵)
#   3) uvicorn 으로 server.py 실행 (port 8000)
#
# 사용법:
#   bash scripts/run.sh           # 포그라운드
#   bash scripts/run.sh --port 9000
#   PORT=9000 bash scripts/run.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT="$(cd "$AGENT_DIR/.." && pwd)"

PORT="${PORT:-8000}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: bash scripts/run.sh [--port PORT]"; exit 0 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

# ── 1. venv ──────────────────────────────────────────────────
if [ ! -f "$ROOT/.venv/bin/activate" ]; then
  echo "❌ 공용 venv 가 없습니다: $ROOT/.venv"
  echo "   먼저 'bash ../scripts/setup.sh' 를 실행해 주세요."
  exit 1
fi
# shellcheck disable=SC1091
source "$ROOT/.venv/bin/activate"
echo "✅ venv 활성화: $VIRTUAL_ENV"

# ── 2. Ollama (user-local 우선) ──────────────────────────────
OLLAMA_BIN=""
if [ -x "$HOME/.local/ollama/bin/ollama" ]; then
  OLLAMA_BIN="$HOME/.local/ollama/bin/ollama"
  export PATH="$HOME/.local/ollama/bin:$PATH"
elif command -v ollama >/dev/null 2>&1; then
  OLLAMA_BIN="$(command -v ollama)"
fi

LLM_PROVIDER_VAL="$(grep -E '^LLM_PROVIDER=' "$ROOT/.env" 2>/dev/null | head -1 | cut -d= -f2 | tr -d '"')"
if [ "${LLM_PROVIDER_VAL:-ollama}" = "ollama" ] && [ -n "$OLLAMA_BIN" ]; then
  if ! curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
    echo "▶ Ollama 데몬 기동 (백그라운드)"
    nohup "$OLLAMA_BIN" serve > /tmp/ollama.log 2>&1 &
    sleep 2
  fi
  echo "✅ Ollama 준비 완료 · $($OLLAMA_BIN list 2>&1 | tail -n +2 | wc -l)개 모델"
fi

# ── 3. 서버 ──────────────────────────────────────────────────
cd "$AGENT_DIR"
echo ""
echo "════════════════════════════════════════════════════"
echo "  Orchestration Agent — Web Demo"
echo "  URL: http://localhost:$PORT"
echo "════════════════════════════════════════════════════"
echo ""
exec uvicorn server:app --host 0.0.0.0 --port "$PORT"
