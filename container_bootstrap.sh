#!/usr/bin/env bash
# container_bootstrap.sh
# ──────────────────────
# Docker 컨테이너 안에서 처음(또는 다시) 들어왔을 때 환경을 끝까지 셋업.
#
# 흐름:
#   호스트:  cd /path/to/Roadmap-Planning-Agent && bash run_container.sh
#   컨테이너 안:
#       bash /workspace/26-tech-roadmap/Roadmap-Planning-Agent/container_bootstrap.sh
#
# 수행 단계:
#   1) uv 설치 (한 번만)
#   2) ollama 설치 (한 번만)
#   3) ollama 모델 디렉토리를 마운트 볼륨으로 — 컨테이너 재생성해도 모델 살아남음
#   4) ollama 데몬 기동 (이미 떠있으면 스킵)
#   5) Roadmap-Planning-Agent/scripts/setup.sh 실행
#       (uv venv, requirements, .env symlink, .env 의 OLLAMA_MODEL 자동 pull)
#
# idempotent — 다시 실행해도 안전. 이미 설치/설정된 항목은 스킵.

set -e

ROOT="/workspace/26-tech-roadmap"
PROJECT="$ROOT/Roadmap-Planning-Agent"
MODELS_DIR="$ROOT/.ollama_models"

cd "$ROOT"

echo "═══════════════════════════════════════════════════════"
echo "  Container bootstrap"
echo "  root     : $ROOT"
echo "  project  : $PROJECT"
echo "  models   : $MODELS_DIR  (mounted — survives container rm)"
echo "═══════════════════════════════════════════════════════"

# ── 0. 시스템 패키지 (zstd — ollama 설치 시 필수) ─────────────
if ! command -v zstd >/dev/null 2>&1; then
  echo "[0/5] zstd 설치 (ollama install 의 의존성)"
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update -qq
    apt-get install -y -qq zstd
  else
    echo "  ⚠️  apt-get 없음 — 수동으로 zstd 설치 필요"
  fi
fi

# ── 1. uv ─────────────────────────────────────────────────────
if ! command -v uv >/dev/null 2>&1; then
  echo "[1/5] uv 설치"
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
# uv 가 ~/.local/bin 에 들어가는데 PATH 에 없을 수 있음
if ! command -v uv >/dev/null 2>&1; then
  export PATH="$HOME/.local/bin:$PATH"
fi
echo "  ✅ uv : $(command -v uv)"

# ── 2. ollama ─────────────────────────────────────────────────
if ! command -v ollama >/dev/null 2>&1; then
  echo "[2/5] ollama 설치"
  curl -fsSL https://ollama.com/install.sh | sh
fi
echo "  ✅ ollama : $(command -v ollama)"

# ── 3. Ollama 환경변수 (모델 위치 + 멀티 GPU + 재로딩 방지) ──
mkdir -p "$MODELS_DIR"
export OLLAMA_MODELS="$MODELS_DIR"
export OLLAMA_SCHED_SPREAD=1         # 모델을 GPU 여러 개에 분산 (A6000 × 2)
export OLLAMA_NUM_PARALLEL=4         # 한 runner 가 동시 요청 4개 처리
export OLLAMA_MAX_LOADED_MODELS=1    # 한 모델만 메모리에 유지 (재로딩 방지)
export OLLAMA_KEEP_ALIVE=24h         # 24시간 메모리 유지

# ~/.bashrc 에 영속화 (새 셸 / 재진입 시 자동 적용)
BASHRC_MARK="# Roadmap-Planning-Agent (container_bootstrap.sh)"
if ! grep -qF "$BASHRC_MARK" "$HOME/.bashrc" 2>/dev/null; then
  {
    echo ""
    echo "$BASHRC_MARK"
    echo "export PATH=\"\$HOME/.local/bin:\$PATH\""
    echo "export OLLAMA_MODELS=$MODELS_DIR"
    echo "export OLLAMA_SCHED_SPREAD=1"
    echo "export OLLAMA_NUM_PARALLEL=4"
    echo "export OLLAMA_MAX_LOADED_MODELS=1"
    echo "export OLLAMA_KEEP_ALIVE=24h"
  } >> "$HOME/.bashrc"
  echo "  📝 ~/.bashrc 에 ollama env exports 추가"
fi
echo "  ✅ OLLAMA_MODELS         = $OLLAMA_MODELS"
echo "  ✅ OLLAMA_SCHED_SPREAD   = $OLLAMA_SCHED_SPREAD  (멀티 GPU 분산)"
echo "  ✅ OLLAMA_NUM_PARALLEL   = $OLLAMA_NUM_PARALLEL  (동시 요청)"
echo "  ✅ OLLAMA_MAX_LOADED_MODELS = $OLLAMA_MAX_LOADED_MODELS"

# ── 4. ollama 데몬 ────────────────────────────────────────────
EXISTING_PIDS="$(pgrep -f 'ollama serve' 2>/dev/null || true)"
if [ -n "$EXISTING_PIDS" ]; then
  echo "[4/5] 기존 ollama 데몬 종료 (env 적용 위해 재시작)"
  for pid in $EXISTING_PIDS; do
    kill "$pid" 2>/dev/null || true
  done
  # 종료 대기
  for i in {1..10}; do
    sleep 1
    pgrep -f 'ollama serve' >/dev/null 2>&1 || break
  done
fi
echo "[4/5] ollama 데몬 기동 (백그라운드, 새 env 로)"
nohup ollama serve > /tmp/ollama.log 2>&1 &
for i in {1..20}; do
  sleep 1
  if curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
    break
  fi
done
if ! curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
  echo "  ❌ ollama 데몬 기동 실패 — /tmp/ollama.log 확인"
  exit 1
fi
echo "  ✅ ollama 데몬 응답 OK"

# ── 5. 프로젝트 setup.sh ──────────────────────────────────────
# setup.sh 안에서 .env 의 OLLAMA_MODEL 을 읽어 자동 pull 까지 진행.
# 모델 다운로드 (gemma3:27b ≈ 17GB) 는 시간이 걸릴 수 있음.
echo "[5/5] Roadmap-Planning-Agent/scripts/setup.sh 실행"
bash "$PROJECT/scripts/setup.sh"

echo ""
echo "═══════════════════════════════════════════════════════"
echo "  ✅ Bootstrap 완료"
echo ""
echo "  다음 단계 (서버 기동):"
echo "    source $PROJECT/.venv/bin/activate"
echo "    cd $PROJECT/orchestration_agent"
echo "    bash scripts/run.sh"
echo "═══════════════════════════════════════════════════════"
