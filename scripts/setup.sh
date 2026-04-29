#!/usr/bin/env bash
# setup.sh
# ─────────
# Tech-Analysis-Agent 4개 sibling 에이전트 통합 세팅.
#
# 수행 단계:
#   1) uv 로 공용 venv 생성 (Python 3.10)
#   2) 루트 requirements.txt 의존성 venv 에 설치
#   3) .env 없으면 .env.example 에서 복사 + 각 폴더 symlink 생성
#   4) LLM provider 안내 (Ollama 자동 설치는 선택)
#
# 사용법:
#   bash scripts/setup.sh              # 전체 설치 (기본 ollama)
#   bash scripts/setup.sh --anthropic  # ollama 설치 스킵, API key 입력 유도

set -e

# ── 경로 ──────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

PROVIDER="${LLM_PROVIDER:-ollama}"
INSTALL_OLLAMA=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --anthropic) PROVIDER="anthropic"; INSTALL_OLLAMA=0; shift ;;
    --ollama)    PROVIDER="ollama";    INSTALL_OLLAMA=1; shift ;;
    --no-ollama) INSTALL_OLLAMA=0; shift ;;
    -h|--help)
      echo "Usage: bash setup.sh [--anthropic | --ollama | --no-ollama]"; exit 0 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

echo "════════════════════════════════════════════════════"
echo "  Tech-Analysis-Agent · 공용 세팅"
echo "  root     : $ROOT"
echo "  provider : $PROVIDER"
echo "════════════════════════════════════════════════════"

# ── 1. uv / venv ──────────────────────────────────────────────
if ! command -v uv >/dev/null 2>&1; then
  echo "❌ 'uv' 가 필요합니다. 설치: curl -LsSf https://astral.sh/uv/install.sh | sh"
  exit 1
fi

if [ ! -d .venv ]; then
  echo "[1/4] venv 생성 (python 3.10)"
  uv venv --python 3.10 .venv
else
  echo "[1/4] venv 이미 존재 — 스킵"
fi

# shellcheck disable=SC1091
source .venv/bin/activate

# ── 2. 의존성 설치 ───────────────────────────────────────────
echo "[2/4] requirements 설치 (루트 통합)"
uv pip install -r requirements.txt >/dev/null
echo "   ✅ 설치 완료"

# ── 3. .env 세팅 ──────────────────────────────────────────────
echo "[3/4] .env 구성"
if [ ! -f .env ]; then
  cp .env.example .env
  echo "   📄 .env 생성 (.env.example 복사)"
fi

# 각 sibling 폴더에 .env -> ../.env symlink
for DIR in tech_analysis_agent roadmap_planner_agent \
           investment_strategist_agent orchestration_agent; do
  TARGET="$DIR/.env"
  if [ -e "$TARGET" ] && [ ! -L "$TARGET" ]; then
    echo "   ⚠️  $DIR/.env 이미 존재 (symlink 아님) — 수동 정리 필요"
  else
    rm -f "$TARGET"
    ln -s ../.env "$TARGET"
    echo "   🔗 $DIR/.env -> ../.env"
  fi
done

# provider 설정 반영
if [ "$PROVIDER" = "anthropic" ]; then
  if grep -q "^LLM_PROVIDER=" .env; then
    sed -i 's|^LLM_PROVIDER=.*|LLM_PROVIDER=anthropic|' .env
  else
    echo "LLM_PROVIDER=anthropic" >> .env
  fi
  echo "   📝 .env 의 LLM_PROVIDER=anthropic 설정"
fi

# ── 4. Ollama (optional) ─────────────────────────────────────
if [ "$PROVIDER" = "ollama" ] && [ "$INSTALL_OLLAMA" = "1" ]; then
  echo "[4/4] Ollama 세팅"
  if ! command -v ollama >/dev/null 2>&1; then
    echo "   ❌ ollama 미설치. 수동 설치 필요:"
    echo "      curl -fsSL https://ollama.com/install.sh | sh"
    echo "      이후 'bash scripts/setup.sh --ollama' 재실행"
  else
    # 데몬 기동 확인
    if ! curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
      echo "   ▶ Ollama 데몬 기동"
      nohup ollama serve > /tmp/ollama.log 2>&1 &
      sleep 2
    fi
    # 모델 pull
    MODEL="${OLLAMA_MODEL:-llama3.1:8b}"
    if ! ollama list 2>/dev/null | grep -q "$MODEL"; then
      echo "   ⬇️  모델 pull: $MODEL"
      ollama pull "$MODEL"
    fi
    echo "   ✅ Ollama 준비 완료 ($MODEL)"
  fi
else
  echo "[4/4] Ollama 설치 스킵"
fi

# ── 완료 안내 ─────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════"
echo "  ✅ 세팅 완료"
echo "════════════════════════════════════════════════════"
echo "  venv 활성화  : source .venv/bin/activate"
echo "  실행 예      : cd orchestration_agent && python main.py"
echo "  A/B 비교     : bash scripts/run_ablation.sh"
echo ""
if [ "$PROVIDER" = "anthropic" ]; then
  echo "  📝 .env 에 ANTHROPIC_API_KEY 를 입력하세요."
fi
echo "════════════════════════════════════════════════════"
