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
    """Tavily 기반 시장 인텔리전스 수집 클라이언트."""

    def __init__(self):
        self.use_mock = USE_MOCK_MARKET
        self.init_error = ""
        self.client = None
        if not self.use_mock:
            if not TAVILY_API_KEY:
                self.init_error = "TAVILY_API_KEY is required when USE_MOCK_MARKET is false"
                return
            try:
                from tavily import TavilyClient
                self.client = TavilyClient(api_key=TAVILY_API_KEY)
            except Exception as e:
                self.init_error = str(e)

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
            if isinstance(result, dict):
                result["_query"] = query
            return result
        except Exception as e:
            return {"error": str(e), "results": [], "_query": query}

    # ── 공개 메서드 ──────────────────────────────────────────

    def _actor_query_context(self, actor_context: Optional[dict] = None) -> str:
        if not actor_context:
            return ""
        actors = actor_context.get("related_actors") or []
        areas = actor_context.get("shared_technology_areas") or []
        center = actor_context.get("center_actor") or ""
        parts = [center, *actors[:4], *areas[:6]]
        return " ".join(str(part).strip() for part in parts if str(part).strip())

    def _extract_snippets(self, result: dict) -> list:
        """검색 결과에서 스니펫만 추출"""
        items = result.get("results", [])
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "content": r.get("content", "")[:650],
                "score": r.get("score", 0),
            }
            for r in items
        ]

    def search_market_reports(
        self,
        tech_name: str,
        domain: str,
        actor_context: Optional[dict] = None,
    ) -> dict:
        """
        시장 보고서/산업 리서치 자료를 우선 탐색합니다.
        """
        context = self._actor_query_context(actor_context)
        query = (
            f"{tech_name} {context} market report industry analysis forecast "
            f"TAM SAM SOM CAGR {domain} 2025 2026 2027 2028 2030"
        )
        return self._search(
            query,
            max_results=6,
            include_domains=[
                "grandviewresearch.com", "marketsandmarkets.com",
                "mordorintelligence.com", "precedenceresearch.com",
                "fortunebusinessinsights.com", "idc.com", "gartner.com",
                "semianalysis.com", "yolegroup.com", "techinsights.com",
                "globenewswire.com", "businesswire.com", "prnewswire.com",
            ],
        )

    def search_tam_sam_som(
        self,
        tech_name: str,
        domain: str,
        actor_context: Optional[dict] = None,
    ) -> dict:
        """
        TAM/SAM/SOM 추정을 위한 수치 근거를 검색합니다.
        """
        context = self._actor_query_context(actor_context)
        query = (
            f"{tech_name} {context} TAM SAM SOM serviceable obtainable market "
            f"revenue opportunity {domain} billion forecast"
        )
        return self._search(query, max_results=5, search_depth="advanced")

    def search_cagr_forecast(
        self,
        tech_name: str,
        domain: str,
        actor_context: Optional[dict] = None,
    ) -> dict:
        """
        CAGR 및 성장 전망 수치를 검색합니다.
        """
        context = self._actor_query_context(actor_context)
        query = (
            f"{tech_name} {context} CAGR growth forecast market outlook "
            f"2025 2030 {domain}"
        )
        return self._search(query, max_results=5, search_depth="advanced")

    def search_market_size(
        self,
        tech_name: str,
        domain: str,
        actor_context: Optional[dict] = None,
    ) -> dict:
        """
        기술 관련 시장 규모(TAM/CAGR) 데이터를 검색합니다.
        """
        context = self._actor_query_context(actor_context)
        query = (
            f"{tech_name} {context} market size TAM CAGR forecast 2025 2026 2027 2028 "
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

    def search_investment_trends(
        self,
        tech_name: str,
        actor_context: Optional[dict] = None,
    ) -> dict:
        """
        주요 기업 투자 동향 및 VC 활동을 검색합니다.
        """
        context = self._actor_query_context(actor_context)
        query = (
            f"{tech_name} {context} investment funding venture capital corporate R&D "
            f"2024 2025 semiconductor tech"
        )
        return self._search(query, max_results=5)

    def search_policy_signals(
        self,
        tech_name: str,
        domain: str,
        actor_context: Optional[dict] = None,
    ) -> dict:
        """
        정부 정책, 보조금, 규제 신호를 검색합니다.
        """
        context = self._actor_query_context(actor_context)
        query = (
            f"{tech_name} {context} government policy subsidy CHIPS Act K-Chips "
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

    def search_competitive_landscape(
        self,
        tech_name: str,
        actor_context: Optional[dict] = None,
    ) -> dict:
        """
        경쟁 구도 및 주요 플레이어를 검색합니다.
        """
        context = self._actor_query_context(actor_context)
        query = (
            f"{tech_name} {context} leading companies players market share competition "
            f"NVIDIA AMD Intel TSMC Samsung Qualcomm 2024 2025"
        )
        return self._search(query, max_results=5)

    def search_tech_timeline(
        self,
        tech_name: str,
        actor_context: Optional[dict] = None,
    ) -> dict:
        """
        기술 상용화/양산 예상 시점을 검색합니다.
        """
        context = self._actor_query_context(actor_context)
        query = (
            f"{tech_name} {context} commercialization timeline roadmap mass production "
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

    def collect_full_signal(
        self,
        tech_name: str,
        domain: str,
        actor_context: Optional[dict] = None,
    ) -> dict:
        """
        Market Size Agent 가 필요로 하는 모든 시장 신호를 수집합니다.
        Tavily 키가 없거나 USE_MOCK_MARKET=true 면 합성 데이터를 반환합니다.

        Returns
        -------
        dict  {tech_name, market_size, investment, policy, competitive, timeline}
        """
        if self.use_mock:
            data = mock_market_signal(tech_name, domain)
            data["actor_context"] = actor_context or {}
            data["market_reports"] = []
            data["tam_sam_som_data"] = []
            data["cagr_forecast_data"] = []
            return data
        if not self.client:
            return {
                "tech_name": tech_name,
                "domain": domain,
                "actor_context": actor_context or {},
                "market_reports": [],
                "tam_sam_som_data": [],
                "cagr_forecast_data": [],
                "market_size_data": [],
                "investment_data": [],
                "policy_data": [],
                "competitive_data": [],
                "timeline_data": [],
                "error": self.init_error or "Tavily client is not initialized",
                "_mock": False,
            }

        market_reports = self.search_market_reports(tech_name, domain, actor_context)
        tam_sam_som = self.search_tam_sam_som(tech_name, domain, actor_context)
        cagr_forecast = self.search_cagr_forecast(tech_name, domain, actor_context)
        market_size = self.search_market_size(tech_name, domain, actor_context)
        investment = self.search_investment_trends(tech_name, actor_context)
        policy = self.search_policy_signals(tech_name, domain, actor_context)
        competitive = self.search_competitive_landscape(tech_name, actor_context)
        timeline = self.search_tech_timeline(tech_name, actor_context)

        return {
            "tech_name": tech_name,
            "domain": domain,
            "actor_context": actor_context or {},
            "queries": {
                "market_reports": market_reports.get("_query"),
                "tam_sam_som": tam_sam_som.get("_query"),
                "cagr_forecast": cagr_forecast.get("_query"),
                "market_size": market_size.get("_query"),
                "investment": investment.get("_query"),
                "policy": policy.get("_query"),
                "competitive": competitive.get("_query"),
                "timeline": timeline.get("_query"),
            },
            "market_reports": self._extract_snippets(market_reports),
            "tam_sam_som_data": self._extract_snippets(tam_sam_som),
            "cagr_forecast_data": self._extract_snippets(cagr_forecast),
            "market_size_data": self._extract_snippets(market_size),
            "investment_data": self._extract_snippets(investment),
            "policy_data": self._extract_snippets(policy),
            "competitive_data": self._extract_snippets(competitive),
            "timeline_data": self._extract_snippets(timeline),
            "_mock": False,
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
