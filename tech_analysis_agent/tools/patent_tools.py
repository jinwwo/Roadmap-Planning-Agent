"""
tools/patent_tools.py
──────────────────────
USPTO PatentsView PatentSearch API 래퍼
https://search.patentsview.org/api/v1/patent/

제공 기능
- 키워드 기반 특허 검색
- 연도별 출원 건수 트렌드 조회
- 주요 출원인(assignee) 조회
- 피인용 수(citedby) 집계
"""

import json
import time
import requests
from typing import Optional
from config import (
    PATENTSVIEW_API_KEY,
    USPTO_BASE_URL,
    USPTO_TIMEOUT,
    USPTO_MAX_RESULTS,
    USE_MOCK_PATENT,
)
from tools.mock_data import mock_company_portfolio, mock_patent_signal


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
        if not PATENTSVIEW_API_KEY:
            return {
                "error": "PATENTSVIEW_API_KEY is required for real PatentsView PatentSearch API",
                "patents": [],
                "total_patent_count": 0,
            }
        self._throttle()
        headers = {**self.HEADERS, "X-Api-Key": PATENTSVIEW_API_KEY}
        try:
            resp = requests.post(
                USPTO_BASE_URL,
                json=payload,
                headers=headers,
                timeout=USPTO_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            return self._normalize_response(data)
        except requests.exceptions.Timeout:
            return {"error": "USPTO API 타임아웃", "patents": [], "total_patent_count": 0}
        except requests.exceptions.RequestException as e:
            return {"error": str(e), "patents": [], "total_patent_count": 0}
        except json.JSONDecodeError as e:
            return {"error": f"USPTO API JSON 파싱 실패: {e}", "patents": [], "total_patent_count": 0}

    def _normalize_response(self, data: dict) -> dict:
        """PatentSearch API 응답을 기존 내부 포맷과 호환되게 정규화."""
        if "patents" in data:
            return data
        rows = data.get("patent") or data.get("data") or []
        if isinstance(rows, dict):
            rows = [rows]
        return {
            "patents": rows,
            "total_patent_count": data.get("total_hits", data.get("count", len(rows))),
            "count": data.get("count", len(rows)),
            "error": data.get("error") if isinstance(data.get("error"), str) else None,
        }

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
                "assignees.assignee_organization",
                "patent_num_times_cited_by_us_patents",
                "patent_num_claims",
            ],
            "o": {"size": USPTO_MAX_RESULTS},
            "s": [{"patent_date": "desc"}],
        }
        return self._post(payload)

    def search_patents_by_assignee(
        self,
        assignee: str,
        domain_keywords: str = "",
    ) -> dict:
        """
        출원인(assignee) 기준으로 특허를 검색합니다.

        domain_keywords 가 있으면 title/abstract 텍스트 조건을 약하게 추가해
        회사의 전체 포트폴리오 중 현재 분석 도메인과 가까운 특허를 우선 수집합니다.
        """
        conditions = [{"_text_any": {"assignees.assignee_organization": assignee}}]
        if domain_keywords:
            conditions.append({
                "_or": [
                    {"_text_any": {"patent_title": domain_keywords}},
                    {"_text_any": {"patent_abstract": domain_keywords}},
                ]
            })

        payload = {
            "q": {"_and": conditions} if len(conditions) > 1 else conditions[0],
            "f": [
                "patent_id",
                "patent_title",
                "patent_date",
                "patent_abstract",
                "assignees.assignee_organization",
                "patent_num_times_cited_by_us_patents",
                "patent_num_claims",
            ],
            "o": {"size": USPTO_MAX_RESULTS},
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
            "o": {"size": 1},
        }
        result = self._post(payload)
        return result.get("total_patent_count", 0)

    def get_yearly_assignee_count(
        self,
        assignee: str,
        year: int,
        domain_keywords: str = "",
    ) -> int:
        """특정 출원인의 연도별 특허 건수."""
        conditions = [
            {"_text_any": {"assignees.assignee_organization": assignee}},
            {"_gte": {"patent_date": f"{year}-01-01"}},
            {"_lte": {"patent_date": f"{year}-12-31"}},
        ]
        if domain_keywords:
            conditions.append({
                "_or": [
                    {"_text_any": {"patent_title": domain_keywords}},
                    {"_text_any": {"patent_abstract": domain_keywords}},
                ]
            })
        payload = {
            "q": {"_and": conditions},
            "f": ["patent_id"],
            "o": {"size": 1},
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

    def get_assignee_filing_trend(
        self,
        assignee: str,
        years: Optional[list] = None,
        domain_keywords: str = "",
    ) -> dict:
        """출원인 기준 3개년 출원 트렌드."""
        if years is None:
            years = [2022, 2023, 2024]

        counts = {}
        for y in years:
            counts[y] = self.get_yearly_assignee_count(assignee, y, domain_keywords)

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
            "f": ["assignees.assignee_organization", "patent_num_times_cited_by_us_patents"],
            "o": {"size": top_n},
            "s": [{"patent_num_times_cited_by_us_patents": "desc"}],
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
            "f": ["patent_id", "patent_num_times_cited_by_us_patents"],
            "o": {"size": USPTO_MAX_RESULTS},
            "s": [{"patent_num_times_cited_by_us_patents": "desc"}],
        }
        result = self._post(payload)
        patents = result.get("patents", [])
        total_count = result.get("total_patent_count", 0)

        citations = [
            int(p.get("patent_num_times_cited_by_us_patents") or p.get("patent_num_cited_by_us_patents") or 0)
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

        USE_MOCK_PATENT=true 이면 합성 데이터를 반환합니다.
        실제 API 모드에서 API 호출이 실패하면 error 를 포함한 빈 신호를 반환합니다.

        Returns
        -------
        dict  {keyword, recent_patents, filing_trend, citation_summary, top_assignees}
        """
        if USE_MOCK_PATENT:
            return mock_patent_signal(keyword)

        try:
            recent = self.search_patents(keyword)
            # 실제 API 모드에서는 mock 으로 숨기지 않고 error 를 상위로 전달
            if recent.get("error"):
                return {
                    "keyword": keyword,
                    "recent_patents": [],
                    "filing_trend": {},
                    "citation_summary": {},
                    "top_assignees": [],
                    "error": recent.get("error"),
                    "_mock": False,
                }
            trend = self.get_filing_trend(keyword)
            citation = self.get_citation_summary(keyword)
            assignees = self.get_top_assignees(keyword, top_n=5)
        except Exception as e:
            return {
                "keyword": keyword,
                "recent_patents": [],
                "filing_trend": {},
                "citation_summary": {},
                "top_assignees": [],
                "error": str(e),
                "_mock": False,
            }

        return {
            "keyword": keyword,
            "recent_patents": [
                {
                    "title": p.get("patent_title", ""),
                    "date": p.get("patent_date", ""),
                    "assignee": (p.get("assignees") or [{}])[0].get(
                        "assignee_organization", "N/A"
                    ),
                    "cited_by": p.get("patent_num_times_cited_by_us_patents", p.get("patent_num_cited_by_us_patents", 0)),
                }
                for p in recent.get("patents", [])[:10]
            ],
            "filing_trend": trend,
            "citation_summary": citation,
            "top_assignees": assignees,
        }

    def collect_company_portfolio(
        self,
        company_name: str,
        domain_keywords: str = "",
    ) -> dict:
        """
        특정 기업의 특허 포트폴리오를 수집합니다.

        Patent Agent 의 company-driven 분석에서 사용합니다. API 실패/무응답 시에도
        mock 포트폴리오로 폴백해 오프라인 데모 경로를 유지합니다.
        """
        if USE_MOCK_PATENT:
            return mock_company_portfolio(company_name, domain_keywords)

        try:
            recent = self.search_patents_by_assignee(company_name, domain_keywords)
            if recent.get("error") or not recent.get("patents"):
                return {
                    "company_name": company_name,
                    "domain_keywords": domain_keywords,
                    "recent_patents": [],
                    "filing_trend": {},
                    "citation_summary": {},
                    "error": recent.get("error") or "No patents returned from PatentsView",
                    "_mock": False,
                }
            trend = self.get_assignee_filing_trend(company_name, domain_keywords=domain_keywords)
        except Exception as e:
            return {
                "company_name": company_name,
                "domain_keywords": domain_keywords,
                "recent_patents": [],
                "filing_trend": {},
                "citation_summary": {},
                "error": str(e),
                "_mock": False,
            }

        patents = recent.get("patents", [])[:USPTO_MAX_RESULTS]
        citations = [
            int(p.get("patent_num_times_cited_by_us_patents") or p.get("patent_num_cited_by_us_patents") or 0)
            for p in patents
        ]
        total_cit = sum(citations)
        citation_summary = {
            "total_patents": recent.get("total_patent_count", len(patents)),
            "avg_citations": round(total_cit / len(citations), 2) if citations else 0,
            "max_citations": max(citations) if citations else 0,
            "high_citation_ratio": 0.0,
        }
        if total_cit > 0:
            threshold = sorted(citations, reverse=True)[max(0, len(citations) // 10)]
            top_sum = sum(c for c in citations if c >= threshold)
            citation_summary["high_citation_ratio"] = round(top_sum / total_cit * 100, 2)

        return {
            "company_name": company_name,
            "domain_keywords": domain_keywords,
            "recent_patents": [
                {
                    "id": p.get("patent_id", ""),
                    "title": p.get("patent_title", ""),
                    "abstract": (p.get("patent_abstract") or "")[:600],
                    "date": p.get("patent_date", ""),
                    "assignee": (p.get("assignees") or [{}])[0].get(
                        "assignee_organization", company_name
                    ),
                    "cited_by": p.get("patent_num_times_cited_by_us_patents", p.get("patent_num_cited_by_us_patents", 0)),
                    "num_claims": p.get("patent_num_claims", 0),
                }
                for p in patents
            ],
            "filing_trend": trend,
            "citation_summary": citation_summary,
            "_mock": False,
        }
