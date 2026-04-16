#!/usr/bin/env bash
# run.sh
# ──────
# 서버(+터널) 통합 실행 스크립트.
#
#   bash run.sh                     # 서버만 (로컬)
#   bash run.sh --public            # 서버 + Cloudflare 터널 (포그라운드, Ctrl+C 로 둘 다 종료)
#   bash run.sh --tmux              # tmux 세션에 백그라운드로 기동
#   bash run.sh --tmux --public     # tmux + 터널  ← 장기 공개 데모용
#
# 옵션:
#   --port PORT    서버 포트 (기본 8000)
#   --host HOST    바인딩 호스트 (기본 0.0.0.0)
#
# 로그: .run/server.log, .run/tunnel.log
# 종료: bash stop.sh

set -e

PORT="${PORT:-8000}"
HOST="${HOST:-0.0.0.0}"
PUBLIC=false
USE_TMUX=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port)    PORT="$2";  shift 2 ;;
    --host)    HOST="$2";  shift 2 ;;
    --public)  PUBLIC=true; shift ;;
    --tmux)    USE_TMUX=true; PUBLIC=true; shift ;;  # tmux 모드는 공개가 기본 의미
    --no-public) PUBLIC=false; shift ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"
mkdir -p .run

log() { printf "\033[1;36m[run]\033[0m %s\n" "$*"; }
die() { printf "\033[1;31m[err]\033[0m %s\n" "$*"; exit 1; }

PY="$(command -v python3 || command -v python)"

# ── 사전 체크 ──────────────────────────────────────────
[[ -f .env ]] || die ".env 가 없습니다. 먼저 'bash setup.sh' 를 실행하세요."

# Ollama 데몬 자동 기동
if command -v ollama >/dev/null 2>&1; then
  if ! curl -fsS --max-time 2 http://localhost:11434/api/tags >/dev/null 2>&1; then
    log "Ollama 데몬 기동 중…"
    nohup ollama serve > .run/ollama.log 2>&1 &
    echo $! > .run/ollama.pid
    for i in $(seq 1 20); do
      curl -fsS --max-time 2 http://localhost:11434/api/tags >/dev/null 2>&1 && break
      sleep 1
    done
  fi
fi

# 포트 충돌 체크
if curl -fsS --max-time 1 "http://localhost:$PORT/api/status" >/dev/null 2>&1; then
  log "⚠️  포트 $PORT 에 이미 서버가 떠있습니다. 기존 서버에 터널만 붙일까요?"
  read -p "    계속하려면 Enter, 중단은 Ctrl+C: " _
fi

# ── tmux 모드 ──────────────────────────────────────────
if $USE_TMUX; then
  command -v tmux >/dev/null 2>&1 || die "tmux 가 없습니다. 'apt-get install -y tmux' 후 재시도."

  SOCK="/tmp/roadmap.sock"
  # 기존 세션 정리
  tmux -S "$SOCK" kill-session -t roadmap 2>/dev/null || true
  pkill -f "cloudflared tunnel" 2>/dev/null || true
  sleep 1

  log "tmux 세션 'roadmap' 생성 중 (소켓: $SOCK)…"
  unset TMUX
  TMUX_TMPDIR=/tmp tmux -S "$SOCK" new-session -d -s roadmap -n server -c "$SCRIPT_DIR" \
    "exec $PY -m uvicorn server:app --host $HOST --port $PORT &> .run/server.log"

  log "서버 기동 대기…"
  for i in $(seq 1 60); do
    curl -fsS --max-time 2 "http://localhost:$PORT/api/status" >/dev/null 2>&1 && break
    sleep 1
    [[ $i -eq 60 ]] && die "서버 기동 실패 (.run/server.log 확인)"
  done

  if $PUBLIC; then
    command -v cloudflared >/dev/null 2>&1 || {
      log "cloudflared 설치 중…"
      curl -fsSL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
        -o /usr/local/bin/cloudflared
      chmod +x /usr/local/bin/cloudflared
    }
    tmux -S "$SOCK" new-window -t roadmap: -n tunnel -c "$SCRIPT_DIR" \
      "exec cloudflared tunnel --url http://localhost:$PORT --protocol http2 --no-autoupdate &> .run/tunnel.log"
    log "Cloudflare 터널 기동 중…"
    URL=""
    for i in $(seq 1 40); do
      URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' .run/tunnel.log 2>/dev/null | head -1 || true)
      [[ -n "$URL" ]] && break
      sleep 1
    done
    [[ -z "$URL" ]] && die "터널 URL 발급 실패 (.run/tunnel.log 확인)"
    log "외부 접근 전파 대기 (최대 60초)…"
    for i in $(seq 1 30); do
      CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$URL/api/status" 2>/dev/null || echo 000)
      [[ "$CODE" == "200" ]] && break
      sleep 2
    done
  fi

  echo
  printf "\033[1;32m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\033[0m\n"
  printf "  ✅  tmux 백그라운드 기동 완료\n"
  printf "  📡  로컬 주소  : \033[1;33mhttp://localhost:%s\033[0m\n" "$PORT"
  if $PUBLIC && [[ -n "${URL:-}" ]]; then
    printf "  🌐  공개 URL   : \033[1;33m%s\033[0m\n" "$URL"
  fi
  printf "\033[1;32m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\033[0m\n"
  echo
  echo "  세션 확인     : tmux -S $SOCK ls"
  echo "  세션 접속     : tmux -S $SOCK attach -t roadmap     (detach: Ctrl+B, D)"
  echo "  서버 로그     : tail -f $SCRIPT_DIR/.run/server.log"
  echo "  터널 로그     : tail -f $SCRIPT_DIR/.run/tunnel.log"
  echo "  전체 종료     : bash stop.sh"
  echo
  exit 0
fi

# ── 포그라운드 모드 (tmux 없이 직접 실행) ──────────────
log "FastAPI 서버 시작: http://$HOST:$PORT"

# 서버를 백그라운드로 띄우고, 터널도 띄운 뒤, 양쪽 로그를 tail 로 이어붙이는 방식
: > .run/server.log
: > .run/tunnel.log

"$PY" -m uvicorn server:app --host "$HOST" --port "$PORT" &> .run/server.log &
SERVER_PID=$!

# 종료 시 자식 프로세스 정리
cleanup() {
  log "종료 중…"
  kill "$SERVER_PID" 2>/dev/null || true
  [[ -n "${TUNNEL_PID:-}" ]] && kill "$TUNNEL_PID" 2>/dev/null || true
  wait 2>/dev/null || true
  exit 0
}
trap cleanup INT TERM

# 서버 기동 대기
for i in $(seq 1 60); do
  curl -fsS --max-time 2 "http://localhost:$PORT/api/status" >/dev/null 2>&1 && break
  sleep 1
  [[ $i -eq 60 ]] && { cat .run/server.log; die "서버 기동 실패"; }
done
log "서버 기동 완료 (pid=$SERVER_PID)"

TUNNEL_PID=""
URL=""
if $PUBLIC; then
  command -v cloudflared >/dev/null 2>&1 || {
    log "cloudflared 설치 중…"
    curl -fsSL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
      -o /usr/local/bin/cloudflared
    chmod +x /usr/local/bin/cloudflared
  }
  # 기존 터널 정리
  pkill -f "cloudflared tunnel" 2>/dev/null || true
  sleep 1

  cloudflared tunnel --url "http://localhost:$PORT" --protocol http2 --no-autoupdate \
    &> .run/tunnel.log &
  TUNNEL_PID=$!
  log "Cloudflare 터널 기동 중 (pid=$TUNNEL_PID)…"

  for i in $(seq 1 40); do
    URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' .run/tunnel.log 2>/dev/null | head -1 || true)
    [[ -n "$URL" ]] && break
    sleep 1
  done
  if [[ -z "$URL" ]]; then
    log "⚠️  터널 URL 발급 실패 — .run/tunnel.log 확인"
  else
    log "외부 접근 전파 대기…"
    for i in $(seq 1 30); do
      CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$URL/api/status" 2>/dev/null || echo 000)
      [[ "$CODE" == "200" ]] && break
      sleep 2
    done
  fi
fi

echo
printf "\033[1;32m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\033[0m\n"
printf "  📡  로컬 주소  : \033[1;33mhttp://localhost:%s\033[0m\n" "$PORT"
if [[ -n "$URL" ]]; then
  printf "  🌐  공개 URL   : \033[1;33m%s\033[0m\n" "$URL"
fi
printf "\033[1;32m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\033[0m\n"
echo "  서버 로그 스트리밍 중…  Ctrl+C 로 전체 종료"
echo

# 서버 로그를 포그라운드에 tail — Ctrl+C 시 trap 이 자식들 정리
tail -f .run/server.log &
TAIL_PID=$!
# 서버가 죽으면 종료
wait $SERVER_PID
kill $TAIL_PID 2>/dev/null || true
cleanup
