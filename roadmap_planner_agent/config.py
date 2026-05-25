"""
config.py
─────────
Roadmap Planner Agent 의 환경 변수 및 전역 설정값.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── LLM Provider 선택 ────────────────────────────────────────
#   "ollama"    : 로컬 Ollama 서버 (기본값, 데모용)
#   "anthropic" : Claude API
LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "ollama").lower()

# ── Anthropic Claude ─────────────────────────────────────────
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL: str = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")

# ── Ollama (Local LLM) ───────────────────────────────────────
OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "gemma3:27b")
OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

# ── 카테고리 → 개발 레이어 매핑 ──────────────────────────────
# 낮을수록 먼저 개발되어야 하는 기반 기술
# Hierarchy Alignment: [Material/Equipment] → [Unit Process] → [Packaging/Architecture]
CATEGORY_LAYER: dict = {
    "Material": 0,
    "Equipment": 0,
    "Process": 1,
    "Architecture": 2,
    "Packaging": 2,
}

# ── TRL 구간별 표준 리드 타임 (단위: 분기) ────────────────────
#   TRL 1-3 : 기초 연구, 4-6 분기 이상
#   TRL 4-6 : 프로토타이핑, 2-4 분기
#   TRL 7-8 : 최적화/양산 준비, 1-2 분기
# (정책: (min + max) // 2 를 사용하되, 시장 역산에서 prerequisites 체인이 길어지면
#        timeline_calculator 에서 자동 조정)
TRL_LEAD_TIME_QUARTERS: dict = {
    # 계획서 스펙 그대로 — band 단위 lead time:
    #   TRL 1-3 : 5 분기 (4-6 분기 band 의 중앙값)
    #   TRL 4-6 : 3 분기 (2-4 분기 band 의 중앙값)
    #   TRL 7-8 : 2 분기 (1-2 분기 band 의 중앙값)
    #   TRL 9   : 1 분기 (이미 양산 가능)
    1: 5, 2: 5, 3: 5,
    4: 3, 5: 3, 6: 3,
    7: 2, 8: 2,
    9: 1,
}

# ── 기본 시장 목표 분기 (market_context 미제공 시) ────────────
DEFAULT_TARGET_BOOM_QUARTER: str = "2028 Q1"

# ── 출력 포맷 ─────────────────────────────────────────────────
# Zero-slack 검증 시 prerequisite 완료 ≤ 후행 시작 을 강제
ENFORCE_ZERO_SLACK: bool = True


def validate_config() -> None:
    """필수 환경 변수 검증 (provider 별)"""
    missing = []
    if LLM_PROVIDER == "anthropic":
        if not ANTHROPIC_API_KEY:
            missing.append("ANTHROPIC_API_KEY")
    elif LLM_PROVIDER == "ollama":
        pass
    else:
        raise EnvironmentError(
            f"지원하지 않는 LLM_PROVIDER: {LLM_PROVIDER!r} (ollama 또는 anthropic)"
        )

    if missing:
        raise EnvironmentError(
            f"필수 환경 변수가 설정되지 않았습니다: {', '.join(missing)}"
        )
