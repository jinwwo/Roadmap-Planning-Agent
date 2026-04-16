#!/usr/bin/env bash
# stop_tunnel.sh — cloudflared 터널 종료
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [[ -f .run/tunnel.pid ]]; then
  PID="$(cat .run/tunnel.pid)"
  if kill -0 "$PID" 2>/dev/null; then
    echo "[stop_tunnel] 종료 중 (pid=$PID)"
    kill "$PID" || true
  fi
  rm -f .run/tunnel.pid
fi
pkill -f "cloudflared tunnel" 2>/dev/null || true
echo "[stop_tunnel] 완료"
