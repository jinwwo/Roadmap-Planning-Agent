"""
config.py
─────────
Investment Strategist Agent 의 환경 변수 및 전역 설정값.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── LLM Provider 선택 ────────────────────────────────────────
LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "ollama").lower()

# ── Anthropic Claude ─────────────────────────────────────────
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL: str = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")

# ── Ollama (Local LLM) ───────────────────────────────────────
OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

# ── Stage 분류 경계 (start_q 기준, phase_name 이 없을 때의 폴백) ─
# short-term : 2 년 이내 시작
# mid-term   : 2–4 년 이내 시작
# long-term  : 그 이후
SHORT_TERM_MAX_QUARTERS: int = 8    # 시작 기준으로 8분기(=2년) 이내면 short-term
MID_TERM_MAX_QUARTERS:   int = 16   # 16분기(=4년) 이내면 mid-term

# ── 기본 Investment Policy (CLI override 가능) ───────────────
DEFAULT_INVESTMENT_POLICY: dict = {
    "risk_appetite": "medium",           # low | medium | high
    "investment_horizon": "balanced",    # short | balanced | long
    "total_budget": 0.0,                 # 전체 예산 (USD) — 0 이면 명시 안 됨
    "strategic_priority": [
        "market entry",
        "core capability building",
    ],
}

# ── 출력 파일명 기본값 ────────────────────────────────────────
DEFAULT_INPUT_ROADMAP: str = "../roadmap_planner_agent/output_planned_roadmap.json"
DEFAULT_OUTPUT_STRATEGY: str = "output_investment_strategy.json"


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
