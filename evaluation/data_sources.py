"""
data_sources.py
───────────────
외부 특허/시장 DB 커넥터 인터페이스 + Holdout Extractor 설계 골격.

핵심 설계 원칙:
  1. Agent와 Back Test가 동일한 커넥터 인터페이스 사용 (DB 교란변수 제거)
  2. 모든 쿼리는 as_of_date 필수 (시점 분리)
  3. Provider 추상화 (KIPRIS / Google Patents / USPTO 교체 가능)

현재 상태:
  - 인터페이스와 구조만 정의됨
  - 실제 API 호출은 NotImplementedError 또는 Mock 데이터 반환
  - Agent DB 커넥터가 확정되면 각 Connector의 _fetch_* 메서드만 구현하면 됨

사용 예시 (미래):
    # Agent 쪽
    source = PatentDataSource(connector=KiprisConnector(api_key="..."))
    agent_input = source.query(
        keywords=["edge ai chip", "npu"],
        as_of_date="2020-12-31",   # Agent는 과거 시점만
        window_years=5
    )

    # Back test 쪽 (같은 커넥터, 다른 시점)
    holdout = source.query(
        keywords=["edge ai chip", "npu"],
        as_of_date="2025-12-31",   # Back test는 현재 시점
        window_years=5
    )
"""

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import List, Dict, Optional, Any

def _stable_hash(text: str) -> int:
    """프로세스 무관 결정론적 hash (Python hash()는 PYTHONHASHSEED로 매번 달라짐)."""
    import hashlib
    return int(hashlib.md5(text.encode("utf-8")).hexdigest(), 16)



# ═══════════════════════════════════════════════════════════════
#  1. Data Container Classes
# ═══════════════════════════════════════════════════════════════

@dataclass
class PatentQueryResult:
    """특허 DB 쿼리 결과"""
    keywords: List[str]
    as_of_date: str            # "YYYY-MM-DD" — 이 시점까지의 누적 데이터
    window_years: int          # 조회 윈도우 (e.g. 5 → [as_of-5yr, as_of])
    total_patents: int         # 윈도우 내 출원 건수
    yearly_counts: Dict[int, int] = field(default_factory=dict)  # {2018: 45, 2019: 72, ...}
    top_assignees: List[Dict[str, Any]] = field(default_factory=list)  # [{"name": "TSMC", "count": 23}]
    source: str = "unknown"    # 어느 커넥터가 반환했나


@dataclass
class MarketQueryResult:
    """시장 규모 DB 쿼리 결과"""
    keywords: List[str]
    as_of_date: str
    window_years: int
    market_size_m: float       # as_of_date 시점의 시장 규모 (백만 달러)
    cagr: Optional[float] = None            # 윈도우 내 CAGR
    yearly_sizes: Dict[int, float] = field(default_factory=dict)  # {2018: 2800, 2019: 3100, ...}
    source: str = "unknown"


# ═══════════════════════════════════════════════════════════════
#  2. Connector Interface (Provider 추상화)
# ═══════════════════════════════════════════════════════════════

class PatentConnector(ABC):
    """
    특허 DB 커넥터 인터페이스.
    모든 구현체 (KIPRIS, Google Patents, USPTO)는 이 인터페이스를 구현.
    """
    name: str = "base"

    @abstractmethod
    def fetch_patents(self, keywords: List[str], as_of_date: str,
                      window_years: int = 5) -> PatentQueryResult:
        """
        주어진 키워드로 as_of_date 시점까지의 특허 정보 조회.

        CRITICAL: as_of_date 이후의 출원은 절대 포함되면 안 됨 (data leakage 방지).
        """
        pass


class MarketConnector(ABC):
    """시장 데이터 DB 커넥터 인터페이스 (Statista/IDC/Gartner 등)."""
    name: str = "base"

    @abstractmethod
    def fetch_market_size(self, keywords: List[str], as_of_date: str,
                          window_years: int = 5) -> MarketQueryResult:
        pass


# ═══════════════════════════════════════════════════════════════
#  3. Concrete Connector Stubs (실제 구현은 나중)
# ═══════════════════════════════════════════════════════════════

class KiprisConnector(PatentConnector):
    """
    한국 KIPRIS API 커넥터.
    실제 API: http://plus.kipris.or.kr/
    """
    name = "kipris"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key
        # TODO: Agent의 Technology Analysis Agent가 이미 KIPRIS를 쓰고 있다면,
        #       그 커넥터 코드를 여기로 import해서 재사용할 것.

    def fetch_patents(self, keywords: List[str], as_of_date: str,
                      window_years: int = 5) -> PatentQueryResult:
        # ─────────────────────────────────────────────
        # TODO: 실제 KIPRIS API 호출 구현
        #
        # 필수 요구사항:
        #   1. application_date <= as_of_date 조건 강제 (leakage 방지)
        #   2. application_date >= (as_of_date - window_years) 로 윈도우 제한
        #   3. keywords는 OR 쿼리로 결합
        #
        # 예시 구조:
        #   start_date = _subtract_years(as_of_date, window_years)
        #   response = self._call_kipris_api(
        #       query=" OR ".join(keywords),
        #       date_from=start_date,
        #       date_to=as_of_date
        #   )
        #   return PatentQueryResult(
        #       keywords=keywords,
        #       as_of_date=as_of_date,
        #       window_years=window_years,
        #       total_patents=response.total,
        #       yearly_counts=response.by_year,
        #       top_assignees=response.assignees,
        #       source=self.name
        #   )
        # ─────────────────────────────────────────────
        import os
        api_key = self.api_key or os.environ.get("KIPRIS_API_KEY", "")
        start_date = str(int(as_of_date[:4]) - window_years) + as_of_date[4:]

        if not api_key:
            # API 키 없으면 Mock 반환
            import hashlib
            h = int(hashlib.md5(f"{keywords}{as_of_date}".encode()).hexdigest()[:8], 16)
            total = 30 + (h % 300)
            return PatentQueryResult(
                keywords=keywords, as_of_date=as_of_date,
                window_years=window_years, total_patents=total,
                yearly_counts={}, top_assignees=[], source="kipris_mock"
            )

        # 실제 KIPRIS Plus API 호출
        try:
            import requests
            query = " OR ".join(keywords)
            url = "http://plus.kipris.or.kr/openapi/rest/patUtiModInfoSearchSevice/freeSearchInfo"
            params = {
                "word": query,
                "ServiceKey": api_key,
                "patent": "true",
                "utility": "false",
                "lastvalue": "1",
                "listcount": "500",
                "startDate": start_date.replace("-", ""),
                "endDate": as_of_date.replace("-", ""),
            }
            resp = requests.get(url, params=params, timeout=30)
            total = 0
            if resp.status_code == 200:
                import xml.etree.ElementTree as ET
                root = ET.fromstring(resp.text)
                # totalCount 또는 item 수로 카운트
                tc = root.find(".//totalCount")
                if tc is not None and tc.text:
                    total = int(tc.text)
                else:
                    items = root.findall(".//{http://plus.kipris.or.kr}item") or root.findall(".//item")
                    total = len(items)
            else:
                print(f"  ⚠ KIPRIS API HTTP {resp.status_code}")

            return PatentQueryResult(
                keywords=keywords, as_of_date=as_of_date,
                window_years=window_years, total_patents=total,
                yearly_counts={}, top_assignees=[], source="kipris"
            )
        except Exception as e:
            print(f"  ⚠ KIPRIS API 에러: {e}")
            # 실패 시 Mock
            import hashlib
            h = int(hashlib.md5(f"{keywords}{as_of_date}".encode()).hexdigest()[:8], 16)
            return PatentQueryResult(
                keywords=keywords, as_of_date=as_of_date,
                window_years=window_years, total_patents=30 + (h % 300),
                yearly_counts={}, top_assignees=[], source="kipris_mock_fallback"
            )


class GooglePatentsConnector(PatentConnector):
    """Google Patents Public Datasets (BigQuery)."""
    name = "google_patents"

    def __init__(self, gcp_credentials_path: Optional[str] = None):
        self.gcp_credentials_path = gcp_credentials_path

    def fetch_patents(self, keywords: List[str], as_of_date: str,
                      window_years: int = 5) -> PatentQueryResult:
        # TODO: BigQuery `patents-public-data.patents.publications` 쿼리
        #       WHERE filing_date <= as_of_date
        #         AND filing_date >= DATE_SUB(as_of_date, INTERVAL window_years YEAR)
        raise NotImplementedError("Google Patents BigQuery 연동 미구현.")


class USPTOConnector(PatentConnector):
    """
    USPTO PatentsView API 커넥터.
    Agent 1의 patent_tools.py::USPTOPatentTool을 래핑.

    직접 USPTO API 호출 (API 키 불필요, 무료).
    as_of_date 기반 시점 필터링 지원 (back test용).
    """
    name = "uspto"

    HEADERS = {"Content-Type": "application/json"}
    BASE_URL = "https://api.patentsview.org/patents/query"
    TIMEOUT = 30
    MAX_RESULTS = 50
    CALL_INTERVAL = 1.0

    def __init__(self):
        self._last_call = 0.0

    def _throttle(self):
        import time
        elapsed = time.time() - self._last_call
        if elapsed < self.CALL_INTERVAL:
            time.sleep(self.CALL_INTERVAL - elapsed)
        self._last_call = time.time()

    def _post(self, payload: dict) -> dict:
        import time
        import requests
        self._throttle()
        try:
            resp = requests.post(
                self.BASE_URL, json=payload,
                headers=self.HEADERS, timeout=self.TIMEOUT,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            return {"error": str(e), "patents": [], "total_patent_count": 0}

    def fetch_patents(self, keywords: List[str], as_of_date: str,
                      window_years: int = 5) -> PatentQueryResult:
        """
        USPTO PatentsView API로 특허 데이터 조회.
        as_of_date 이후의 특허는 포함하지 않음 (leakage 방지).
        """
        # 시작일 계산
        year = int(as_of_date[:4])
        start_year = year - window_years
        start_date = f"{start_year}-01-01"
        end_date = as_of_date  # as_of_date 까지만

        # 키워드 OR 쿼리
        keyword_query = " ".join(keywords)

        # 연도별 건수 조회
        yearly_counts = {}
        total = 0
        for y in range(start_year, year + 1):
            payload = {
                "q": {
                    "_and": [
                        {"_text_any": {"patent_title": keyword_query}},
                        {"_gte": {"patent_date": f"{y}-01-01"}},
                        {"_lte": {"patent_date": f"{y}-12-31" if y < year else as_of_date}},
                    ]
                },
                "f": ["patent_id"],
                "o": {"per_page": 1, "page": 1},
            }
            result = self._post(payload)
            count = result.get("total_patent_count", 0)
            yearly_counts[y] = count
            total += count

        # top assignees 조회
        payload = {
            "q": {
                "_and": [
                    {"_text_any": {"patent_title": keyword_query}},
                    {"_gte": {"patent_date": start_date}},
                    {"_lte": {"patent_date": as_of_date}},
                ]
            },
            "f": ["assignee_organization", "patent_num_cited_by_us_patents"],
            "o": {"per_page": 10, "page": 1},
            "s": [{"patent_num_cited_by_us_patents": "desc"}],
        }
        result = self._post(payload)
        assignees = {}
        for p in result.get("patents", []):
            for org in p.get("assignees", []):
                name = org.get("assignee_organization", "")
                if name:
                    assignees[name] = assignees.get(name, 0) + 1
        top = sorted(assignees.items(), key=lambda x: x[1], reverse=True)[:5]
        top_list = [{"name": n, "count": c} for n, c in top]

        return PatentQueryResult(
            keywords=keywords,
            as_of_date=as_of_date,
            window_years=window_years,
            total_patents=total,
            yearly_counts=yearly_counts,
            top_assignees=top_list,
            source=self.name,
        )


class MockPatentConnector(PatentConnector):
    """
    테스트용 Mock 커넥터.
    실제 API 연결 전까지 파이프라인 검증용으로 사용.
    """
    name = "mock_patent"

    def __init__(self, fixture_data: Optional[Dict[str, Any]] = None):
        """
        fixture_data: {"edge ai chip": {"2020-12-31": {total: 120, ...}, "2025-12-31": {total: 340, ...}}}
        """
        self.fixture = fixture_data or {}

    def fetch_patents(self, keywords: List[str], as_of_date: str,
                      window_years: int = 5) -> PatentQueryResult:
        # 키워드 hash 기반 결정론적 mock 데이터 생성
        key = "|".join(sorted(keywords))
        if key in self.fixture and as_of_date in self.fixture[key]:
            data = self.fixture[key][as_of_date]
        else:
            # 기본 mock: 키워드 개수에 비례한 가짜 값
            total = len(keywords) * 50 + _stable_hash(key + as_of_date) % 200
            data = {"total": abs(total), "yearly_counts": {}}

        return PatentQueryResult(
            keywords=keywords,
            as_of_date=as_of_date,
            window_years=window_years,
            total_patents=data["total"],
            yearly_counts=data.get("yearly_counts", {}),
            top_assignees=data.get("top_assignees", []),
            source=self.name,
        )


class MockMarketConnector(MarketConnector):
    """시장 데이터 Mock 커넥터."""
    name = "mock_market"

    def __init__(self, fixture_data: Optional[Dict[str, Any]] = None):
        self.fixture = fixture_data or {}

    def fetch_market_size(self, keywords: List[str], as_of_date: str,
                          window_years: int = 5) -> MarketQueryResult:
        key = "|".join(sorted(keywords))
        if key in self.fixture and as_of_date in self.fixture[key]:
            data = self.fixture[key][as_of_date]
        else:
            size = abs(_stable_hash(key + as_of_date)) % 10000 + 500
            data = {"market_size_m": float(size), "cagr": 0.15}

        return MarketQueryResult(
            keywords=keywords,
            as_of_date=as_of_date,
            window_years=window_years,
            market_size_m=data["market_size_m"],
            cagr=data.get("cagr"),
            yearly_sizes=data.get("yearly_sizes", {}),
            source=self.name,
        )


class AgentPatentConnector(PatentConnector):
    """patent_agent raw(company_portfolios.filing_trend)로 시점별 특허 건수 재사용.
    KIPRIS 직접호출 미사용 — Agent가 수집한 객관 카운트만 (patent_score 등 LLM 판단 제외).
    회사 단위 filing_trend를 모든 기술에 적용."""
    name = "agent_patent"
    AGENT_BASE = "/root/Roadmap-Planning-Agent/orchestration_agent/outputs/patent_agent"

    def __init__(self, company: str = "", eval_year: int = 2030):
        self.company = company or ""
        self.eval_year = int(eval_year)
        self._trend = {}
        self._cagr = None
        self._total = 0
        self._load()

    def _load(self):
        import os, glob, json as _json
        if not self.company:
            return
        safe = self.company.replace(" ", "_").replace("&", "_")
        cands = glob.glob(os.path.join(self.AGENT_BASE, "batch_*", "*" + safe + "*patent_agent_log.json"))
        cands += glob.glob(os.path.join(self.AGENT_BASE, "batch_*", "*patent_agent_log.json"))
        if not cands:
            return
        try:
            d = _json.load(open(cands[0], encoding="utf-8"))
        except Exception:
            return
        cp = (d.get("patent_raw_data", {}) or {}).get("company_portfolios", {}) or {}
        port = cp.get(self.company)
        if port is None:
            for k, v in cp.items():
                if k.lower().replace(" ", "") == self.company.lower().replace(" ", ""):
                    port = v; break
        if port is None:
            return
        ft = port.get("filing_trend", {}) or {}
        for k, v in ft.items():
            if k == "cagr_pct":
                try: self._cagr = float(v) / 100.0
                except Exception: pass
            else:
                try: self._trend[int(k)] = int(v)
                except Exception: pass
        cs = port.get("citation_summary", {}) or {}
        self._total = int(cs.get("total_patents", 0) or 0)

    def fetch_patents(self, keywords, as_of_date, window_years: int = 5):
        try:
            year = int(str(as_of_date)[:4])
        except Exception:
            year = self.eval_year
        lo = year - window_years
        if self._trend:
            yearly = {y: c for y, c in self._trend.items() if lo < y <= year}
            total = sum(yearly.values())
            max_y = max(self._trend.keys())
            if year > max_y and self._cagr and self._cagr > -1:
                base = sum(c for y, c in self._trend.items() if lo < y <= max_y)
                total = int(round(base * ((1.0 + self._cagr) ** (year - max_y))))
            return PatentQueryResult(
                keywords=keywords, as_of_date=as_of_date, window_years=window_years,
                total_patents=max(0, total), yearly_counts=yearly,
                top_assignees=[], source=self.name)
        return PatentQueryResult(
            keywords=keywords, as_of_date=as_of_date, window_years=window_years,
            total_patents=0, yearly_counts={}, top_assignees=[], source=self.name + "_miss")

class AgentMarketConnector(MarketConnector):
    """Agent가 LLM으로 정제한 market_analysis(객관 수치만)를 holdout으로 재사용.
    Tavily/regex 미사용. tam_usd_b/cagr_pct만 사용(market_score 등 판단치는 제외)."""
    name = "agent_market"
    AGENT_DIR = "/root/Roadmap-Planning-Agent/orchestration_agent/outputs/market_agent/run_01"

    def __init__(self, company: str = "", eval_year: int = 2030):
        self.company = company or ""
        self.eval_year = int(eval_year)
        self._index = {}  # {frozenset(tokens): {"tam_m": float, "cagr": float|None}}
        self._load()

    @staticmethod
    def _tok(name: str):
        import re
        toks = re.sub(r"[^a-zA-Z0-9\uac00-\ud7a3\s]", " ", (name or "").lower()).split()
        return frozenset(w for w in toks if len(w) > 1)

    def _load(self):
        import os, glob
        if not self.company:
            return
        # 회사명 정규화: 공백·& → _  (예: 'Cosmo AM&T' → 'Cosmo_AM_T')
        safe = self.company.replace(" ", "_").replace("&", "_")
        base = os.path.dirname(self.AGENT_DIR)  # .../market_agent
        # run_01(구) + batch_*/(신) 전부 탐색
        cands = (
            glob.glob(os.path.join(self.AGENT_DIR, safe + "_market_agent_log.json"))
            + glob.glob(os.path.join(base, "batch_*", safe + "_market_agent_log.json"))
        )
        if not cands:
            return  # 파일 없음 → 빈 인덱스 → 전부 결측
        try:
            with open(cands[0], encoding="utf-8") as fh:
                d = json.load(fh)
        except Exception:
            return
        for m in d.get("market_analysis", []):
            toks = self._tok(m.get("name", ""))
            if not toks:
                continue
            t = m.get("tam_sam_som", {}) or {}
            c = m.get("cagr_forecast", {}) or {}
            # V3.3: SAM(서비스 가능 시장) 사용 — TAM은 과대, SAM이 회사 공략 가능 시장
            sam_b = t.get("sam_usd_b")
            tam_b = t.get("tam_usd_b")
            mkt_b = sam_b if sam_b else tam_b   # SAM 우선, 없으면 TAM fallback
            cagr_pct = c.get("cagr_pct")
            rec = {
                "tam_m": float(mkt_b) * 1000.0 if mkt_b else 0.0,  # 십억$ → 백만$ (SAM 기준)
                "cagr": (float(cagr_pct) / 100.0) if cagr_pct else None,
            }
            self._index[toks] = rec

    def _match(self, keywords):
        q = frozenset(keywords or [])
        best, best_ov = None, 0.0
        for toks, rec in self._index.items():
            # 정확 교집합 + 부분 문자열 매칭 (고체전지 ⊂ 고체전지용, 양극 ⊂ 양극재)
            ov = 0.0
            for qw in q:
                if qw in toks:
                    ov += 1.0          # 정확 일치
                else:
                    for tw in toks:
                        if (qw in tw or tw in qw) and min(len(qw), len(tw)) >= 2:
                            ov += 0.6   # 부분 일치 (접미사/조사 차이 흡수)
                            break
            if ov > best_ov:
                best, best_ov = rec, ov
        return best

    def fetch_market_size(self, keywords, as_of_date, window_years=5):
        rec = self._match(keywords)
        if not rec or rec["tam_m"] <= 0:
            # 결측: past=future=0 → mg=0, ms=0 → min-max 최하단
            return MarketQueryResult(
                keywords=keywords, as_of_date=as_of_date, window_years=window_years,
                market_size_m=0.0, cagr=None, yearly_sizes={}, source=self.name + "_miss")
        tam_m = rec["tam_m"]
        cagr = rec["cagr"]
        try:
            yr = int(str(as_of_date)[:4])
        except Exception:
            yr = self.eval_year
        # eval_year의 TAM을 기준으로 cagr 역산 → 과거 시점은 작게
        if cagr and cagr > -1:
            size_at = tam_m / ((1.0 + cagr) ** max(0, self.eval_year - yr))
        else:
            size_at = tam_m
        return MarketQueryResult(
            keywords=keywords, as_of_date=as_of_date, window_years=window_years,
            market_size_m=size_at, cagr=cagr, yearly_sizes={yr: size_at}, source=self.name)


class CSVMarketConnector(MarketConnector):
    """CSV 파일 기반 시장 데이터 커넥터. as_of_date 시점 필터 지원."""
    name = "csv_market"

    def __init__(self, csv_path: str = None):
        import os
        if csv_path is None:
            csv_path = os.path.join(os.path.dirname(__file__), "data", "market_data.csv")
        self.csv_path = csv_path
        self._data = None

    def _load(self):
        if self._data is not None:
            return
        import csv
        self._data = []
        try:
            with open(self.csv_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    self._data.append({
                        "keyword": row["keyword"].strip().lower(),
                        "year": int(row["year"]),
                        "market_size_m": float(row["market_size_m"]),
                        "cagr_pct": float(row.get("cagr_pct", 0)),
                        "source": row.get("source", ""),
                    })
        except FileNotFoundError:
            self._data = []

    def _match(self, csv_kw, search_kws):
        for sk in search_kws:
            if sk.lower() in csv_kw or csv_kw in sk.lower():
                return True
        return False

    def fetch_market_size(self, keywords, as_of_date, window_years=5):
        self._load()
        cutoff = int(as_of_date[:4])
        start = cutoff - window_years
        matched = [r for r in self._data
                   if self._match(r["keyword"], keywords) and start <= r["year"] <= cutoff]
        if not matched:
            return MarketQueryResult(keywords=keywords, as_of_date=as_of_date,
                                    window_years=window_years, market_size_m=0.0, source=self.name)
        matched.sort(key=lambda r: r["year"])
        latest = matched[-1]
        return MarketQueryResult(
            keywords=keywords, as_of_date=as_of_date, window_years=window_years,
            market_size_m=latest["market_size_m"], cagr=latest["cagr_pct"],
            yearly_sizes={r["year"]: r["market_size_m"] for r in matched},
            source=f"{self.name}:{latest['source']}",
        )


class TavilyMarketConnector(MarketConnector):
    """Tavily Search API 기반. Agent 1의 MarketIntelligenceTool과 동일한 API."""
    name = "tavily"

    TRUSTED_DOMAINS = [
        "statista.com", "grandviewresearch.com", "marketsandmarkets.com",
        "mordorintelligence.com", "precedenceresearch.com",
        "globenewswire.com", "businesswire.com", "prnewswire.com",
    ]

    def __init__(self, api_key=None):
        import os
        self.api_key = api_key or os.environ.get("TAVILY_API_KEY")
        self._client = None

    def _get_client(self):
        if self._client is None:
            if not self.api_key:
                raise RuntimeError("TAVILY_API_KEY not set")
            from tavily import TavilyClient
            self._client = TavilyClient(api_key=self.api_key)
        return self._client

    def fetch_market_size(self, keywords, as_of_date, window_years=5):
        import re as _re
        cutoff = int(as_of_date[:4])
        tech_name = " ".join(keywords[:3])
        query = (f"{tech_name} market size TAM CAGR forecast "
                 f"{cutoff-1} {cutoff} {cutoff+1} billion growth rate semiconductor")
        try:
            result = self._get_client().search(
                query=query, max_results=5, search_depth="advanced",
                include_domains=self.TRUSTED_DOMAINS)
        except Exception as e:
            return MarketQueryResult(keywords=keywords, as_of_date=as_of_date,
                                    window_years=window_years, market_size_m=0.0,
                                    source=f"{self.name}:error")
        all_text = " ".join(s.get("content", "") for s in result.get("results", []))
        yearly = {}
        for m in _re.finditer(r'\$\s*([\d,.]+)\s*(?:billion|B)\s*(?:in|by|for)\s*(\d{4})', all_text, _re.I):
            try:
                amt = float(m.group(1).replace(",", "")) * 1000
                yr = int(m.group(2))
                if cutoff - window_years <= yr <= cutoff:
                    yearly[yr] = amt
            except ValueError:
                pass
        cagr_m = _re.search(r'CAGR\s+(?:of\s+)?([\d.]+)\s*%', all_text, _re.I)
        cagr = float(cagr_m.group(1)) if cagr_m else None
        ms = yearly[max(yearly.keys())] if yearly else 0.0
        return MarketQueryResult(keywords=keywords, as_of_date=as_of_date,
                                 window_years=window_years, market_size_m=ms,
                                 cagr=cagr, yearly_sizes=yearly, source=self.name)


# ═══════════════════════════════════════════════════════════════
#  4. DataSource Facade (Agent와 Back Test가 공유)
# ═══════════════════════════════════════════════════════════════

class PatentDataSource:
    """
    특허 데이터 접근 파사드.
    Agent와 Back Test가 이걸 똑같이 씀 → 시점만 다름.
    """

    def __init__(self, connector: PatentConnector):
        self.connector = connector

    def query(self, keywords: List[str], as_of_date: str,
              window_years: int = 5) -> PatentQueryResult:
        """
        Args:
            keywords: 검색 키워드 리스트
            as_of_date: "YYYY-MM-DD" — 이 시점까지의 데이터만
            window_years: 조회 윈도우 (기본 5년)
        """
        return self.connector.fetch_patents(keywords, as_of_date, window_years)


class TavilyMarketConnector(MarketConnector):
    """
    Tavily Search API 기반 시장 데이터 커넥터.
    Agent 1의 MarketIntelligenceTool과 동일한 API를 사용.

    Tavily는 실시간 웹 검색이라 "2020년 기준" 정밀 필터링은 불가하지만,
    검색 결과에서 연도별 시장 규모를 LLM 없이 정규식으로 추출.

    TAVILY_API_KEY 환경변수 필요. 없으면 에러.
    """
    name = "tavily"

    TRUSTED_DOMAINS = [
        "statista.com", "grandviewresearch.com", "marketsandmarkets.com",
        "mordorintelligence.com", "precedenceresearch.com",
        "globenewswire.com", "businesswire.com", "prnewswire.com",
    ]

    def __init__(self, api_key: Optional[str] = None):
        import os
        self.api_key = api_key or os.environ.get("TAVILY_API_KEY")
        self._client = None

    def _get_client(self):
        if self._client is None:
            if not self.api_key:
                raise RuntimeError("TAVILY_API_KEY not set")
            from tavily import TavilyClient
            self._client = TavilyClient(api_key=self.api_key)
        return self._client

    def fetch_market_size(self, keywords: List[str], as_of_date: str,
                          window_years: int = 5) -> MarketQueryResult:
        import re as _re

        cutoff_year = int(as_of_date[:4])
        tech_name = " ".join(keywords[:3])

        # Agent 1과 동일한 쿼리 패턴
        _queries = [
            f"{tech_name} market size TAM {cutoff_year} billion USD",
            f"{tech_name} market CAGR forecast {cutoff_year-1} {cutoff_year+1}",
            f"{tech_name} industry revenue market value billion",
        ]

        try:
            client = self._get_client()
            _snips = []
            for _q in _queries:
                try:
                    _r = client.search(
                        query=_q,
                        max_results=4,
                        search_depth="advanced",
                        include_domains=self.TRUSTED_DOMAINS,
                    )
                    _snips.extend(_r.get("results", []))
                except Exception:
                    continue
            result = {"results": _snips}
        except Exception as e:
            print(f"  ⚠ Tavily 검색 실패: {e}")
            return MarketQueryResult(
                keywords=keywords, as_of_date=as_of_date,
                window_years=window_years, market_size_m=0.0,
                source=f"{self.name}:error",
            )

        # 검색 결과에서 시장 규모 숫자 추출 (정규식, LLM 없이)
        snippets = result.get("results", [])
        all_text = " ".join(s.get("content", "") for s in snippets)

        yearly_sizes = {}
        market_size = 0.0
        cagr = None

        # "$X.X billion" 패턴 + 연도 매칭
        # 예: "reached $6.5 billion in 2020", "expected to reach $18B by 2025"
        patterns = [
            # "$X.X billion in YYYY" / "$X.XB in YYYY"
            r'\$\s*([\d,.]+)\s*(?:billion|B)\s*(?:in|by|for)\s*(\d{4})',
            # "YYYY ... $X.X billion"
            r'(\d{4})\s*(?:was|is|at|to)\s*(?:about|approximately|roughly)?\s*\$\s*([\d,.]+)\s*(?:billion|B)',
            # "market size of $X.X billion (YYYY)"
            r'market\s+size\s+(?:of\s+)?\$\s*([\d,.]+)\s*(?:billion|B)\s*\(?(\d{4})',
        ]

        for pattern in patterns:
            for match in _re.finditer(pattern, all_text, _re.IGNORECASE):
                groups = match.groups()
                if groups[0][0].isdigit() and len(groups) == 2:
                    # pattern 1 or 3: (amount, year)
                    try:
                        amount = float(groups[0].replace(",", "")) * 1000  # B → M
                        year = int(groups[1])
                    except ValueError:
                        continue
                elif len(groups) == 2:
                    # pattern 2: (year, amount)
                    try:
                        year = int(groups[0])
                        amount = float(groups[1].replace(",", "")) * 1000
                    except ValueError:
                        continue
                else:
                    continue

                if cutoff_year - window_years <= year <= cutoff_year:
                    yearly_sizes[year] = amount

        # CAGR 추출
        cagr_match = _re.search(r'CAGR\s+(?:of\s+)?([\d.]+)\s*%', all_text, _re.IGNORECASE)
        if cagr_match:
            try:
                cagr = float(cagr_match.group(1))
            except ValueError:
                pass

        # 가장 최근 연도의 값 사용
        if yearly_sizes:
            latest_year = max(yearly_sizes.keys())
            market_size = yearly_sizes[latest_year]

        return MarketQueryResult(
            keywords=keywords,
            as_of_date=as_of_date,
            window_years=window_years,
            market_size_m=market_size,
            cagr=cagr,
            yearly_sizes=yearly_sizes,
            source=self.name,
        )


class MarketDataSource:
    """시장 데이터 접근 파사드."""

    def __init__(self, connector: MarketConnector):
        self.connector = connector

    def query(self, keywords: List[str], as_of_date: str,
              window_years: int = 5) -> MarketQueryResult:
        return self.connector.fetch_market_size(keywords, as_of_date, window_years)


# ═══════════════════════════════════════════════════════════════
#  5. Holdout Extractor
# ═══════════════════════════════════════════════════════════════

class HoldoutExtractor:
    """
    Agent가 선택한 기술들에 대해 baseline/realized 시점의 데이터를 수집하여
    trm_evaluation의 input_pack["holdout_data"] 포맷으로 변환.

    두 가지 모드:
      - global: 모든 기술에 동일한 baseline/evaluation 시점 적용 (단순)
      - per-tech: 각 기술의 start_q→baseline, target_q→evaluation으로 개별 시점 (정밀)

    사용 예시:
        # Global 모드
        extractor = HoldoutExtractor(patent_source, market_source,
                                     baseline_date="2020-12-31",
                                     evaluation_date="2025-12-31")

        # Per-tech 모드
        extractor = HoldoutExtractor(patent_source, market_source,
                                     mode="per-tech")
    """

    def __init__(self,
                 patent_source: PatentDataSource,
                 market_source: MarketDataSource,
                 baseline_date: Optional[str] = None,
                 evaluation_date: Optional[str] = None,
                 window_years: int = 5,
                 mode: str = "global"):
        """
        Args:
            mode: "global" — 모든 기술에 baseline_date/evaluation_date 고정 적용
                  "per-tech" — 각 기술의 start_q→baseline, target_q→evaluation 사용
        """
        self.patent_source = patent_source
        self.market_source = market_source
        self.baseline_date = baseline_date or "2020-12-31"
        self.evaluation_date = evaluation_date or "2025-12-31"
        self.window_years = window_years
        self.mode = mode

    @staticmethod
    def _q_to_date(q_str: str) -> str:
        """'2028-Q1' → '2027-12-31' (해당 분기 시작 직전), '2028-Q4' → '2028-09-30'"""
        import re
        if not q_str:
            return ""
        m = re.match(r"(\d{4})-?Q(\d)", q_str, re.IGNORECASE)
        if not m:
            return q_str
        year, q = int(m.group(1)), int(m.group(2))
        # 해당 분기의 마지막 날
        end_months = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}
        return f"{year}-{end_months[q]}"

    @staticmethod
    def _q_start_to_baseline(q_str: str) -> str:
        """start_q의 직전 시점을 baseline으로. '2026-Q4' → '2026-09-30'"""
        import re
        if not q_str:
            return ""
        m = re.match(r"(\d{4})-?Q(\d)", q_str, re.IGNORECASE)
        if not m:
            return q_str
        year, q = int(m.group(1)), int(m.group(2))
        # start_q 직전 분기의 마지막 날
        if q == 1:
            return f"{year-1}-12-31"
        prev_months = {2: "03-31", 3: "06-30", 4: "09-30"}
        return f"{year}-{prev_months[q]}"

    def extract(self, tech_candidates: List[Dict[str, Any]],
                roadmap: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Dict[str, Any]]:
        """
        Args:
            tech_candidates: 기술 목록 (keywords 필드 필요)
            roadmap: planned_roadmap 목록 (per-tech 모드에서 start_q/target_q 참조)
        """
        # per-tech 모드용 roadmap 매핑
        roadmap_map = {}
        if roadmap:
            for r in roadmap:
                roadmap_map[r.get("tech_id", "")] = r

        holdout = {}

        for tech in tech_candidates:
            tid = tech["tech_id"]
            keywords = tech.get("keywords", [])
            if not keywords:
                # keywords가 없으면 tech_name에서 생성
                import re
                name = tech.get("tech_name", "")
                keywords = [w for w in re.sub(r"[^a-zA-Z0-9가-힣\s]", " ", name.lower()).split() if len(w) > 1][:5]

            # 시점 결정
            if self.mode == "per-tech" and tid in roadmap_map:
                rm = roadmap_map[tid]
                baseline = self._q_start_to_baseline(rm.get("start_q", ""))
                evaluation = self._q_to_date(rm.get("target_q", ""))
                if not baseline:
                    baseline = self.baseline_date
                if not evaluation:
                    evaluation = self.evaluation_date
            else:
                baseline = self.baseline_date
                evaluation = self.evaluation_date

            # DB 조회
            baseline_patents = self.patent_source.query(keywords, baseline, self.window_years)
            baseline_market = self.market_source.query(keywords, baseline, self.window_years)
            realized_patents = self.patent_source.query(keywords, evaluation, self.window_years)
            realized_market = self.market_source.query(keywords, evaluation, self.window_years)

            holdout[tid] = {
                "baseline_patents": baseline_patents.total_patents,
                "realized_patents": realized_patents.total_patents,
                "baseline_market_m": baseline_market.market_size_m,
                "realized_market_m": realized_market.market_size_m,
                # 메타
                "baseline_date": baseline,
                "evaluation_date": evaluation,
                "mode": self.mode,
                "patent_source": baseline_patents.source,
                "market_source": baseline_market.source,
                "keywords_used": keywords,
            }

        return holdout

    def extract_and_assemble(self, input_pack: Dict[str, Any]) -> Dict[str, Any]:
        """input_pack에 holdout_data를 채워 넣은 완성본 반환."""
        roadmap = input_pack.get("planned_roadmap", [])
        holdout = self.extract(input_pack["tech_candidates"], roadmap)
        result = dict(input_pack)
        result["holdout_data"] = holdout

        if "metadata" in result:
            result["metadata"]["holdout_mode"] = self.mode
            result["metadata"]["global_baseline_date"] = self.baseline_date
            result["metadata"]["global_evaluation_date"] = self.evaluation_date
            result["metadata"]["backtest_window_years"] = self.window_years

        return result


# ═══════════════════════════════════════════════════════════════
#  6. 통합 시나리오: Agent DB 재사용
# ═══════════════════════════════════════════════════════════════
#
# 실제 운용 시 이런 흐름으로 작성하게 됨:
#
# ┌─────────────────────────────────────────────────────────────┐
# │                                                             │
# │  공통 DB Connector (KiprisConnector 등)                     │
# │         ↓                        ↓                          │
# │    Technology Analysis       HoldoutExtractor               │
# │    Agent가 사용              Back Test 준비용               │
# │    (as_of=2020)              (as_of=2020 & 2025)            │
# │         ↓                        ↓                          │
# │    tech_candidates +       holdout_data 생성                │
# │    planned_roadmap                                          │
# │    investment_strategy                                      │
# │                 ↘            ↙                              │
# │                  input_pack 조립                            │
# │                         ↓                                   │
# │                  TRMEvaluationSuite.evaluate()              │
# │                                                             │
# └─────────────────────────────────────────────────────────────┘
#
# 핵심: 같은 Connector 인스턴스를 Agent와 Extractor가 공유하면
#       DB 교란변수가 구조적으로 제거됨.


# ═══════════════════════════════════════════════════════════════
#  7. Demo: Mock 커넥터로 파이프라인 검증
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import json

    # Mock fixture (실제 값 대신 테스트용)
    patent_fixture = {
        "edge ai|edge ai chip|npu": {
            "2020-12-31": {"total": 120, "yearly_counts": {2018: 20, 2019: 35, 2020: 65}},
            "2025-12-31": {"total": 340, "yearly_counts": {2023: 85, 2024: 110, 2025: 145}},
        },
        "edge inference|on-device llm|small language model": {
            "2020-12-31": {"total": 80},
            "2025-12-31": {"total": 310},
        },
    }
    market_fixture = {
        "edge ai|edge ai chip|npu": {
            "2020-12-31": {"market_size_m": 2800, "cagr": 0.22},
            "2025-12-31": {"market_size_m": 8500, "cagr": 0.25},
        },
        "edge inference|on-device llm|small language model": {
            "2020-12-31": {"market_size_m": 1200},
            "2025-12-31": {"market_size_m": 6800},
        },
    }

    # Mock 커넥터로 DataSource 구성
    patent_source = PatentDataSource(MockPatentConnector(patent_fixture))
    market_source = MarketDataSource(MockMarketConnector(market_fixture))

    # Holdout Extractor 구성
    extractor = HoldoutExtractor(
        patent_source=patent_source,
        market_source=market_source,
        baseline_date="2020-12-31",
        evaluation_date="2025-12-31",
        window_years=5,
    )

    # Agent 출력 시뮬레이션 (tech_candidates만 발췌)
    agent_output_techs = [
        {
            "tech_id": "T001",
            "tech_name": "Edge AI Chip",
            "keywords": ["edge ai", "edge ai chip", "npu"],
        },
        {
            "tech_id": "T002",
            "tech_name": "On-Device LLM",
            "keywords": ["edge inference", "on-device llm", "small language model"],
        },
    ]

    # Holdout 추출
    holdout = extractor.extract(agent_output_techs)

    print("━" * 60)
    print("  Holdout Extractor Demo (Mock 데이터)")
    print("━" * 60)
    print()
    print(json.dumps(holdout, indent=2, ensure_ascii=False))
    print()
    print("  ↑ 이 dict가 input_pack['holdout_data']에 그대로 들어갑니다.")
    print("  ↑ 실제 API 연결 시: KiprisConnector 등으로 교체만 하면 끝.")
