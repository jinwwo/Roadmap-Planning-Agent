"""
tools/patent_tools.py
──────────────────────
USPTO PatentsView API 래퍼 (API 키 불필요, 완전 무료)
https://api.patentsview.org/

제공 기능
- 키워드 기반 특허 검색
- 연도별 출원 건수 트렌드 조회
- 주요 출원인(assignee) 조회
- 피인용 수(citedby) 집계
"""

import time
import requests
from typing import Optional
from config import USPTO_BASE_URL, USPTO_TIMEOUT, USPTO_MAX_RESULTS, USE_MOCK_PATENT
from tools.mock_data import mock_patent_signal


class USPTOPatentTool:
    """USPTO PatentsView REST API 클라이언트"""

    HEADERS = {"Content-Type": "application/json"}
    CALL_INTERVAL = 1.0  # 초당 1회 제한 (rate limit 예방)

    def __init__(self):
        self._last_call = 0.0

    def _throttle(self) -> None:
        """Rate limit 보호용 호출 간격 조절"""
        elapsed = time.time() - self._last_call
        if elapsed < self.CALL_INTERVAL:
            time.sleep(self.CALL_INTERVAL - elapsed)
        self._last_call = time.time()

    def _post(self, payload: dict) -> dict:
        """API POST 요청 공통 처리"""
        self._throttle()
        try:
            resp = requests.post(
                USPTO_BASE_URL,
                json=payload,
                headers=self.HEADERS,
                timeout=USPTO_TIMEOUT,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.Timeout:
            return {"error": "USPTO API 타임아웃", "patents": [], "total_patent_count": 0}
        except requests.exceptions.RequestException as e:
            return {"error": str(e), "patents": [], "total_patent_count": 0}

    # ── 공개 메서드 ──────────────────────────────────────────

    def search_patents(self, keyword: str) -> dict:
        """
        키워드로 최신 특허를 검색합니다.

        Returns
        -------
        dict  {patents: [...], total_patent_count: int}
        """
        payload = {
            "q": {"_text_any": {"patent_title": keyword}},
            "f": [
                "patent_id",
                "patent_title",
                "patent_date",
                "patent_abstract",
                "assignee_organization",
                "patent_num_cited_by_us_patents",  # 피인용 수
                "patent_num_claims",
            ],
            "o": {"per_page": USPTO_MAX_RESULTS, "page": 1},
            "s": [{"patent_date": "desc"}],
        }
        return self._post(payload)

    def get_yearly_filing_count(self, keyword: str, year: int) -> int:
        """
        특정 연도의 키워드 관련 특허 출원 건수를 반환합니다.
        (total_patent_count 만 사용 → 빠른 조회)
        """
        payload = {
            "q": {
                "_and": [
                    {"_text_any": {"patent_title": keyword}},
                    {"_gte": {"patent_date": f"{year}-01-01"}},
                    {"_lte": {"patent_date": f"{year}-12-31"}},
                ]
            },
            "f": ["patent_id"],
            "o": {"per_page": 1, "page": 1},
        }
        result = self._post(payload)
        return result.get("total_patent_count", 0)

    def get_filing_trend(self, keyword: str, years: Optional[list] = None) -> dict:
        """
        3개년 출원 트렌드를 반환합니다.

        Returns
        -------
        dict  {2022: int, 2023: int, 2024: int, cagr_pct: float}
        """
        if years is None:
            years = [2022, 2023, 2024]

        counts = {}
        for y in years:
            counts[y] = self.get_yearly_filing_count(keyword, y)

        # CAGR 계산 (시작 연도 0 방지)
        start = counts.get(years[0], 0) or 1
        end = counts.get(years[-1], 0)
        n = len(years) - 1
        cagr = ((end / start) ** (1 / n) - 1) * 100 if n > 0 else 0.0

        return {**counts, "cagr_pct": round(cagr, 2)}

    def get_top_assignees(self, keyword: str, top_n: int = 10) -> list:
        """
        키워드 관련 상위 출원인 목록을 반환합니다.
        """
        payload = {
            "q": {"_text_any": {"patent_title": keyword}},
            "f": ["assignee_organization", "patent_num_cited_by_us_patents"],
            "o": {"per_page": top_n, "page": 1},
            "s": [{"patent_num_cited_by_us_patents": "desc"}],
        }
        result = self._post(payload)
        patents = result.get("patents", [])

        assignees = {}
        for p in patents:
            orgs = p.get("assignees", [])
            for org in orgs:
                name = org.get("assignee_organization", "Unknown")
                if name:
                    assignees[name] = assignees.get(name, 0) + 1

        return sorted(assignees.items(), key=lambda x: x[1], reverse=True)[:top_n]

    def get_citation_summary(self, keyword: str) -> dict:
        """
        키워드 관련 특허의 인용 통계를 반환합니다.

        Returns
        -------
        dict  {total_patents, avg_citations, max_citations, high_citation_ratio}
        """
        payload = {
            "q": {"_text_any": {"patent_title": keyword}},
            "f": ["patent_id", "patent_num_cited_by_us_patents"],
            "o": {"per_page": USPTO_MAX_RESULTS, "page": 1},
            "s": [{"patent_num_cited_by_us_patents": "desc"}],
        }
        result = self._post(payload)
        patents = result.get("patents", [])
        total_count = result.get("total_patent_count", 0)

        citations = [
            int(p.get("patent_num_cited_by_us_patents") or 0)
            for p in patents
        ]

        if not citations:
            return {
                "total_patents": total_count,
                "avg_citations": 0,
                "max_citations": 0,
                "high_citation_ratio": 0.0,
            }

        total_cit = sum(citations)
        avg_cit = total_cit / len(citations)
        top10_threshold = sorted(citations, reverse=True)[max(0, len(citations) // 10)]
        top10_sum = sum(c for c in citations if c >= top10_threshold)
        high_ratio = (top10_sum / total_cit * 100) if total_cit > 0 else 0.0

        return {
            "total_patents": total_count,
            "avg_citations": round(avg_cit, 2),
            "max_citations": max(citations),
            "high_citation_ratio": round(high_ratio, 2),  # 상위 10% 특허의 인용 점유율
        }

    def collect_full_signal(self, keyword: str) -> dict:
        """
        Patent Data Agent 가 필요로 하는 모든 신호를 한 번에 수집합니다.

        USE_MOCK_PATENT=true 이거나 API 호출이 실패하면 합성 데이터로 폴백합니다.

        Returns
        -------
        dict  {keyword, recent_patents, filing_trend, citation_summary, top_assignees}
        """
        if USE_MOCK_PATENT:
            return mock_patent_signal(keyword)

        try:
            recent = self.search_patents(keyword)
            # API 응답이 에러를 품고 있으면 mock 으로 폴백
            if recent.get("error"):
                return mock_patent_signal(keyword)
            trend = self.get_filing_trend(keyword)
            citation = self.get_citation_summary(keyword)
            assignees = self.get_top_assignees(keyword, top_n=5)
        except Exception:
            return mock_patent_signal(keyword)

        return {
            "keyword": keyword,
            "recent_patents": [
                {
                    "title": p.get("patent_title", ""),
                    "date": p.get("patent_date", ""),
                    "assignee": (p.get("assignees") or [{}])[0].get(
                        "assignee_organization", "N/A"
                    ),
                    "cited_by": p.get("patent_num_cited_by_us_patents", 0),
                }
                for p in recent.get("patents", [])[:10]
            ],
            "filing_trend": trend,
            "citation_summary": citation,
            "top_assignees": assignees,
        }
