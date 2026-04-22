"""
config.py
─────────
Orchestration Agent 전용 환경 변수 및 설정값.

Orchestrator 는 sibling 에이전트들을 **subprocess** 로 호출합니다.
각 sibling 의 위치는 이 파일의 SIBLING_* 상수에서 관리.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── LLM Provider 선택 (Orchestrator 자신의 LLM) ───────────────
LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "ollama").lower()

# ── Anthropic Claude ─────────────────────────────────────────
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL: str = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")

# ── Ollama (Local LLM) ───────────────────────────────────────
OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

# ── Sibling 에이전트 폴더 경로 (이 파일 기준 상대경로를 절대경로로 해석) ─
_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)   # Tech-Analysis-Agent/

SIBLING_TECH_ANALYST: str = os.path.join(_PARENT, "tech_analysis_agent")
SIBLING_ROADMAP_PLANNER: str = os.path.join(_PARENT, "roadmap_planner_agent")
SIBLING_INVESTMENT_STRATEGIST: str = os.path.join(_PARENT, "investment_strategist_agent")

# ── 중간/최종 산출물 저장 폴더 ────────────────────────────────
OUTPUTS_DIR: str = os.path.join(_HERE, "outputs")
os.makedirs(OUTPUTS_DIR, exist_ok=True)

# 파일명 (prefix 는 CLI 의 --out-prefix 로 override 가능)
FILE_TECH_CANDIDATES: str = "tech_candidates.json"
FILE_PLANNED_ROADMAP: str = "planned_roadmap.json"
FILE_INVESTMENT_STRATEGY: str = "investment_strategy.json"
FILE_ORCHESTRATOR_REPORT: str = "orchestrator_report.json"

# ── Orchestrator / TRM 평가 설정 ──────────────────────────────
# REVISE 루프 상한 (도달 시 강제 ACCEPT)
MAX_ORCHESTRATOR_ITERATIONS: int = int(os.getenv("MAX_ORCHESTRATOR_ITERATIONS", "2"))

# subprocess timeout (초). 각 에이전트 호출별 최대 대기 시간.
SUBPROCESS_TIMEOUT_SEC: int = int(os.getenv("SUBPROCESS_TIMEOUT_SEC", "900"))

# ── Problem Frame 기본값 (CLI 로 override 가능) ───────────────
DEFAULT_INDUSTRY: str = "AI / Semiconductor"
DEFAULT_COMPANY_TYPE: str = "Tier-1 IDM / Foundry"
DEFAULT_TIME_HORIZON: str = "2025-2030"
DEFAULT_TOTAL_BUDGET: float = 5_000_000_000.0   # USD
DEFAULT_OBJECTIVE: str = (
    "Build a 5-year technology roadmap that secures leadership in "
    "next-generation sub-2nm foundry and AI accelerator markets."
)
DEFAULT_PRIORITIES: list = [
    "Short-term commercialization readiness",
    "Enabling-technology foundation (materials / equipment)",
    "Balanced long-term exploratory bets",
]
DEFAULT_FUTURE_TREND_SUMMARY: str = (
    "Global AI compute demand (GenAI, HPC) accelerates HBM/packaging and sub-2nm foundry investments. "
    "GAA transistor structure and BSPDN become mainstream in 2027-2028 horizon. "
    "Material/equipment bottlenecks (High-NA EUV, EUV PR, ALD precursors) gate process timelines."
)


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

    # sibling 폴더 존재 확인
    for name, path in [
        ("tech_analysis_agent", SIBLING_TECH_ANALYST),
        ("roadmap_planner_agent", SIBLING_ROADMAP_PLANNER),
        ("investment_strategist_agent", SIBLING_INVESTMENT_STRATEGIST),
    ]:
        if not os.path.isdir(path):
            raise EnvironmentError(f"sibling 폴더를 찾을 수 없습니다: {name} → {path}")
