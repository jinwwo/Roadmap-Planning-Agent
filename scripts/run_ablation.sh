#!/usr/bin/env bash
# run_ablation.sh
# ────────────────
# Orchestration Agent 를 여러 `--agent` 조합으로 돌려
# "에이전트 유무에 따른 보고서 성능 차이" 를 한 번에 비교할 수 있게 합니다.
#
# outputs/ 폴더에 각 실험의 결과가 prefix 별로 쌓입니다:
#   full_*.json    : Agent 1+2+3 (baseline)
#   noA3_*.json    : Agent 1+2
#   noA2_*.json    : Agent 1+3
#   noA1_*.json    : Agent 2+3
#   onlyA1_*.json  : Agent 1 단독
#
# 사용법:
#   bash scripts/run_ablation.sh                # 모든 조합 실행
#   bash scripts/run_ablation.sh full noA3      # 선택 조합만

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$ROOT"

# venv 활성화
if [ -f .venv/bin/activate ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
else
  echo "❌ venv 가 없습니다. 먼저 'bash scripts/setup.sh' 실행하세요."
  exit 1
fi

# ── 조합 정의 ────────────────────────────────────────────────
declare -A COMBOS=(
  [full]="1 2 3"
  [noA3]="1 2"
  [noA2]="1 3"
  [noA1]="2 3"
  [onlyA1]="1"
  [onlyA2]="2"
  [onlyA3]="3"
)

# 인자 없으면 기본 4종
if [ $# -eq 0 ]; then
  SELECTED=(full noA3 noA2 noA1)
else
  SELECTED=("$@")
fi

cd orchestration_agent

for name in "${SELECTED[@]}"; do
  agents="${COMBOS[$name]:-}"
  if [ -z "$agents" ]; then
    echo "⚠️  알 수 없는 조합: $name (skip)"
    continue
  fi
  echo ""
  echo "════════════════════════════════════════════════════"
  echo "  ▶ [$name]  --agent \"$agents\""
  echo "════════════════════════════════════════════════════"
  python main.py --agent "$agents" --out-prefix "${name}_" || {
    echo "   ❌ [$name] 실패 (이어서 진행)"
  }
done

echo ""
echo "════════════════════════════════════════════════════"
echo "  ✅ Ablation 완료"
echo "════════════════════════════════════════════════════"
echo "  산출물: orchestration_agent/outputs/"
ls -lh outputs/ 2>/dev/null | grep -v "^total" | awk '{print "    "$9"   ("$5")"}'
