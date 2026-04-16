#!/usr/bin/env bash
# stop.sh
# ────────
# run.sh(--tmux) 로 띄운 서버 + 터널 + Ollama 데몬을 모두 정리합니다.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

say() { printf "\033[1;36m[stop]\033[0m %s\n" "$*"; }

# tmux roadmap 세션
SOCK="/tmp/roadmap.sock"
if tmux -S "$SOCK" has-session -t roadmap 2>/dev/null; then
  say "tmux 세션 'roadmap' 종료"
  tmux -S "$SOCK" kill-session -t roadmap 2>/dev/null || true
fi

# cloudflared
if pgrep -f "cloudflared tunnel" >/dev/null 2>&1; then
  say "cloudflared 터널 종료"
  pkill -f "cloudflared tunnel" 2>/dev/null || true
fi

# uvicorn (포트 8000)
if pgrep -f "uvicorn server:app" >/dev/null 2>&1; then
  say "uvicorn 서버 종료"
  pkill -f "uvicorn server:app" 2>/dev/null || true
fi

# 이전 방식(.run/ollama.pid) 로 띄운 Ollama
if [[ -f .run/ollama.pid ]]; then
  PID="$(cat .run/ollama.pid)"
  if kill -0 "$PID" 2>/dev/null; then
    say "Ollama 데몬 종료 (pid=$PID)"
    kill "$PID" || true
  fi
  rm -f .run/ollama.pid
fi

rm -f .run/tunnel.pid
say "✓ 정리 완료"
