#!/usr/bin/env bash
# tunnel.sh
# ──────────
# Cloudflare Quick Tunnel 을 띄워 로컬 서버를 외부에 노출합니다.
# (계정 불필요 · 임시 HTTPS URL 발급)
#
#   bash tunnel.sh               # 포트 8000 → 공개 URL
#   bash tunnel.sh --port 9000
#
# 이미 돌고 있는 서버(bash run.sh)에 덧붙여 실행하세요.
# URL 이 나오면 복사해서 외부에 공유하면 됩니다.

set -e

PORT="${PORT:-8000}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    -h|--help) echo "Usage: bash tunnel.sh [--port PORT]"; exit 0 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"
mkdir -p .run

log() { printf "\033[1;36m[tunnel]\033[0m %s\n" "$*"; }
die() { printf "\033[1;31m[tunnel]\033[0m %s\n" "$*"; exit 1; }

# ── 사전 체크: 로컬 서버 떠있는가 ───────────────────────
if ! curl -fsS --max-time 2 "http://localhost:$PORT/api/status" >/dev/null 2>&1; then
  die "로컬 서버(http://localhost:$PORT)에 응답이 없습니다. 먼저 'bash run.sh' 로 서버를 기동하세요."
fi
log "로컬 서버 확인 완료 (localhost:$PORT)"

# ── cloudflared 설치 체크 ──────────────────────────────
if ! command -v cloudflared >/dev/null 2>&1; then
  log "cloudflared 가 없습니다. 설치 중…"
  curl -fsSL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
    -o /usr/local/bin/cloudflared || die "cloudflared 다운로드 실패"
  chmod +x /usr/local/bin/cloudflared
fi
log "cloudflared 버전: $(cloudflared --version 2>&1 | head -1)"

# ── 기존 터널 종료 ─────────────────────────────────────
pkill -f "cloudflared tunnel" 2>/dev/null && sleep 1 || true

# ── 터널 기동 (HTTP/2 로 SSE 안정화) ───────────────────
LOG=".run/tunnel.log"
: > "$LOG"
nohup cloudflared tunnel --url "http://localhost:$PORT" --protocol http2 --no-autoupdate \
  > "$LOG" 2>&1 &
PID=$!
echo "$PID" > .run/tunnel.pid
log "cloudflared 기동 (pid=$PID, 로그=$LOG)"

# ── URL 대기 ───────────────────────────────────────────
log "공개 URL 발급 대기 중…"
URL=""
for i in $(seq 1 40); do
  URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" | head -1 || true)
  [[ -n "$URL" ]] && break
  sleep 1
done
[[ -z "$URL" ]] && die "URL 을 발급받지 못했습니다. $LOG 확인."

# ── 외부 접근 가능해질 때까지 대기 ────────────────────
log "DNS/엣지 전파 대기 (최대 60초)…"
for i in $(seq 1 30); do
  CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$URL/api/status" 2>/dev/null || echo "000")
  if [[ "$CODE" == "200" ]]; then
    break
  fi
  sleep 2
done

echo
printf "\033[1;32m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\033[0m\n"
printf "  🌐  공개 URL:  \033[1;33m%s\033[0m\n" "$URL"
printf "\033[1;32m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\033[0m\n"
echo
echo "  로그       : tail -f $SCRIPT_DIR/$LOG"
echo "  종료       : bash stop_tunnel.sh  (또는 kill $PID)"
echo
echo "  주의:"
echo "  - 이 URL 은 임시입니다. 터널을 재시작하면 주소가 바뀝니다."
echo "  - LLM 추론이 1~3분씩 걸리므로 접속자에게 인내심을 안내하세요."
