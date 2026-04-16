"""
tools/market_tools.py
──────────────────────
Tavily Search API 래퍼 (무료: 1,000 searches/month)
https://app.tavily.com

시장 인텔리전스 수집을 위한 특화 검색 기능을 제공합니다.
- TAM/시장 규모 검색
- 경쟁사/투자 동향 검색
- 정책/규제 신호 검색
- 기술 개화 시점 예측 검색
"""

from typing import Optional
from config import TAVILY_API_KEY, USE_MOCK_MARKET
from tools.mock_data import mock_market_signal


class MarketIntelligenceTool:
    """Tavily 기반 시장 인텔리전스 수집 클라이언트 (키 없으면 mock 모드)"""

    def __init__(self):
        self.use_mock = USE_MOCK_MARKET or not TAVILY_API_KEY
        self.client = None
        if not self.use_mock:
            try:
                from tavily import TavilyClient
                self.client = TavilyClient(api_key=TAVILY_API_KEY)
            except Exception:
                # Tavily 패키지 미설치 또는 초기화 실패 → mock 으로
                self.use_mock = True

    def _search(
        self,
        query: str,
        max_results: int = 5,
        search_depth: str = "advanced",
        include_domains: Optional[list] = None,
    ) -> dict:
        """
        Tavily 검색 수행.

        Parameters
        ----------
        query         : 검색 쿼리
        max_results   : 반환 결과 수 (최대 10)
        search_depth  : "basic" (빠름) | "advanced" (정확, 2 credit 소모)
        include_domains : 신뢰 도메인 필터
        """
        kwargs = dict(
            query=query,
            max_results=max_results,
            search_depth=search_depth,
        )
        if include_domains:
            kwargs["include_domains"] = include_domains

        try:
            result = self.client.search(**kwargs)
            return result
        except Exception as e:
            return {"error": str(e), "results": []}

    # ── 공개 메서드 ──────────────────────────────────────────

    def search_market_size(self, tech_name: str, domain: str) -> dict:
        """
        기술 관련 시장 규모(TAM/CAGR) 데이터를 검색합니다.
        """
        query = (
            f"{tech_name} market size TAM CAGR forecast 2025 2026 2027 2028 "
            f"{domain} billion growth rate"
        )
        return self._search(
            query,
            max_results=5,
            include_domains=[
                "statista.com", "grandviewresearch.com", "marketsandmarkets.com",
                "mordorintelligence.com", "precedenceresearch.com",
                "globenewswire.com", "businesswire.com", "prnewswire.com",
            ],
        )

    def search_investment_trends(self, tech_name: str) -> dict:
        """
        주요 기업 투자 동향 및 VC 활동을 검색합니다.
        """
        query = (
            f"{tech_name} investment funding venture capital corporate R&D "
            f"2024 2025 semiconductor tech"
        )
        return self._search(query, max_results=5)

    def search_policy_signals(self, tech_name: str, domain: str) -> dict:
        """
        정부 정책, 보조금, 규제 신호를 검색합니다.
        """
        query = (
            f"{tech_name} government policy subsidy CHIPS Act K-Chips "
            f"regulation support program {domain}"
        )
        return self._search(
            query,
            max_results=4,
            include_domains=[
                "semiconductors.org", "nist.gov", "commerce.gov",
                "eetimes.com", "anandtech.com", "semiengineering.com",
            ],
        )

    def search_competitive_landscape(self, tech_name: str) -> dict:
        """
        경쟁 구도 및 주요 플레이어를 검색합니다.
        """
        query = (
            f"{tech_name} leading companies players market share competition "
            f"TSMC Samsung Intel ASML 2024 2025"
        )
        return self._search(query, max_results=5)

    def search_tech_timeline(self, tech_name: str) -> dict:
        """
        기술 상용화/양산 예상 시점을 검색합니다.
        """
        query = (
            f"{tech_name} commercialization timeline roadmap mass production "
            f"volume manufacturing 2026 2027 2028"
        )
        return self._search(
            query,
            max_results=5,
            include_domains=[
                "eetimes.com", "techinsights.com", "semianalysis.com",
                "anandtech.com", "tomshardware.com", "ieee.org",
            ],
        )

    def collect_full_signal(self, tech_name: str, domain: str) -> dict:
        """
        Market Size Agent 가 필요로 하는 모든 시장 신호를 수집합니다.
        Tavily 키가 없거나 USE_MOCK_MARKET=true 면 합성 데이터를 반환합니다.

        Returns
        -------
        dict  {tech_name, market_size, investment, policy, competitive, timeline}
        """
        if self.use_mock:
            return mock_market_signal(tech_name, domain)

        def _extract_snippets(result: dict) -> list:
            """검색 결과에서 스니펫만 추출"""
            items = result.get("results", [])
            return [
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "content": r.get("content", "")[:500],  # 500자 제한
                    "score": r.get("score", 0),
                }
                for r in items
            ]

        return {
            "tech_name": tech_name,
            "domain": domain,
            "market_size_data": _extract_snippets(
                self.search_market_size(tech_name, domain)
            ),
            "investment_data": _extract_snippets(
                self.search_investment_trends(tech_name)
            ),
            "policy_data": _extract_snippets(
                self.search_policy_signals(tech_name, domain)
            ),
            "competitive_data": _extract_snippets(
                self.search_competitive_landscape(tech_name)
            ),
            "timeline_data": _extract_snippets(
                self.search_tech_timeline(tech_name)
            ),
        }

    def search_domain_overview(self, domain: str) -> dict:
        """
        도메인 전체 개요 및 시장 전망을 검색합니다.
        (초기 기술 후보 탐색용)
        """
        query = (
            f"{domain} key technologies trends 2025 2026 2027 2028 "
            f"emerging technology roadmap"
        )
        return self._search(query, max_results=7, search_depth="advanced")

    def identify_candidate_technologies(self, domain: str) -> dict:
        """
        도메인에서 유망 후보 기술을 탐색합니다.
        (Market Size Agent의 초기 분석용)
        """
        query = (
            f"most promising technologies {domain} 2025 investment opportunity "
            f"critical breakthrough emerging"
        )
        return self._search(query, max_results=7, search_depth="advanced")
