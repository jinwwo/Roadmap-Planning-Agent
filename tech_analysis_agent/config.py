"""
config.py
─────────
환경 변수 및 전역 설정값을 관리합니다.
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
OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

# ── Tavily (시장 데이터 검색) ─────────────────────────────────
TAVILY_API_KEY: str = os.getenv("TAVILY_API_KEY", "")
# Tavily 키가 없으면 mock 데이터를 사용 (toy/offline 데모용)
USE_MOCK_MARKET: bool = os.getenv("USE_MOCK_MARKET", "").lower() in ("1", "true", "yes")

# ── USPTO PatentsView / PatentSearch API ─────────────────────
# Legacy `api.patentsview.org/patents/query` 는 2026년 현재 sunset 되어
# HTML portal 로 redirect 되므로 PatentSearch API 를 사용합니다.
USPTO_BASE_URL: str = os.getenv(
    "USPTO_BASE_URL",
    "https://search.patentsview.org/api/v1/patent/",
)
PATENTSVIEW_API_KEY: str = os.getenv("PATENTSVIEW_API_KEY", "")
USPTO_TIMEOUT: int = 30
USPTO_MAX_RESULTS: int = 25
# USPTO API 장애 시 mock 데이터 사용
USE_MOCK_PATENT: bool = os.getenv("USE_MOCK_PATENT", "").lower() in ("1", "true", "yes")

# ── Patent Agent prompt / method selection ───────────────────
#   A_current : 기존 특허 signal 기반 후보 기술 추출 방식
#   B_lee2009 : Lee et al. (2009) technology-driven roadmapping 모듈 반영 방식
#   C_company_portfolio : 우리 기업 중심 관련 기업 특허 포트폴리오 분석 방식
PATENT_ANALYSIS_METHOD: str = os.getenv("PATENT_ANALYSIS_METHOD", "C_company_portfolio")

# ── 에이전트 공통 설정 ────────────────────────────────────────
MAX_TECH_CANDIDATES: int = 10
MIN_FINAL_SCORE: float = 50.0
PATENT_WEIGHT: float = 0.45
MARKET_WEIGHT: float = 0.55

# ── 오류 처리 ─────────────────────────────────────────────────
MAX_RETRY: int = 2


def validate_config() -> None:
    """필수 환경 변수 검증 (provider 별)"""
    missing = []
    if LLM_PROVIDER == "anthropic":
        if not ANTHROPIC_API_KEY:
            missing.append("ANTHROPIC_API_KEY")
    elif LLM_PROVIDER == "ollama":
        # Ollama는 로컬 서버만 돌고 있으면 됨 — 별도 키 불요
        pass
    else:
        raise EnvironmentError(
            f"지원하지 않는 LLM_PROVIDER: {LLM_PROVIDER!r} (ollama 또는 anthropic)"
        )

    if missing:
        raise EnvironmentError(
            f"필수 환경 변수가 설정되지 않았습니다: {', '.join(missing)}\n"
            ".env.example 을 참고하여 .env 파일을 생성해 주세요."
        )
