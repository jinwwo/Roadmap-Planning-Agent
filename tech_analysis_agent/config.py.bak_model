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
OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "gemma3:27b")
OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

# ── Tavily (시장 데이터 검색) ─────────────────────────────────
TAVILY_API_KEY: str = os.getenv("TAVILY_API_KEY", "")
# Tavily 키가 없으면 mock 데이터를 사용 (toy/offline 데모용)
USE_MOCK_MARKET: bool = os.getenv("USE_MOCK_MARKET", "").lower() in ("1", "true", "yes")

# 특허 API 장애 시 mock 데이터 사용
USE_MOCK_PATENT: bool = os.getenv("USE_MOCK_PATENT", "").lower() in ("1", "true", "yes")

# Patent Agent가 생성한 actor_similarity_map을 후속 Market Agent가 사용할지 여부
# A/B 비교 실험용: Patent Agent는 항상 후보군+map을 만들고, true일 때만 시장조사에 map을 사용
USE_PATENT_MAP: bool = os.getenv("USE_PATENT_MAP", "true").lower() not in (
    "0",
    "false",
    "no",
    "off",
)

# ── Patent data provider ─────────────────────────────────────
#   mock        : local example/mock data
#   kipris      : KIPRIS Plus patent/publication API
PATENT_DATA_PROVIDER: str = os.getenv("PATENT_DATA_PROVIDER", "mock").lower()
PATENT_SCOPE: str = os.getenv("PATENT_SCOPE", "domestic").lower()
KIPRIS_API_KEY: str = os.getenv("KIPRIS_API_KEY", "")
KIPRIS_BASE_URL: str = os.getenv("KIPRIS_BASE_URL", "https://plus.kipris.or.kr")
KIPRIS_TIMEOUT: int = int(os.getenv("KIPRIS_TIMEOUT", "30") or 30)
KIPRIS_MAX_RESULTS: int = int(os.getenv("KIPRIS_MAX_RESULTS", "30") or 30)
KIPRIS_FOREIGN_COUNTRIES: list[str] = [
    item.strip().upper()
    for item in os.getenv("KIPRIS_FOREIGN_COUNTRIES", "US,EP,JP,CN,WO").split(",")
    if item.strip()
]
KIPRIS_FOREIGN_MAX_RESULTS_PER_COUNTRY: int = int(
    os.getenv("KIPRIS_FOREIGN_MAX_RESULTS_PER_COUNTRY", "10") or 10
)

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

# Final candidate selection after patent/market aggregation.
# 0 means auto: keep roughly 75% of the pool, with at least 3 candidates.
TECH_CANDIDATE_TARGET_K: int = int(os.getenv("TECH_CANDIDATE_TARGET_K", "0") or 0)
TECH_CANDIDATE_KEEP_RATIO: float = float(os.getenv("TECH_CANDIDATE_KEEP_RATIO", "0.75") or 0.75)

# LLM based shortlist selector. The selector may use actor_similarity_map as
# evidence context when USE_PATENT_MAP=true, but it must not add numeric map
# bonuses. If it fails, Aggregator falls back to the common deterministic ranker.
USE_LLM_CANDIDATE_SELECTOR: bool = os.getenv("USE_LLM_CANDIDATE_SELECTOR", "true").lower() not in (
    "0",
    "false",
    "no",
    "off",
)

# Market Agent LLM batching. Smaller batches reduce JSON truncation with local LLMs.
MARKET_ANALYSIS_BATCH_SIZE: int = int(os.getenv("MARKET_ANALYSIS_BATCH_SIZE", "4") or 4)
MARKET_ANALYSIS_MAX_TOKENS: int = int(os.getenv("MARKET_ANALYSIS_MAX_TOKENS", "4096") or 4096)

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
