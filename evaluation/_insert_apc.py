# AgentPatentConnector를 data_sources.py의 AgentMarketConnector 앞에 삽입
APC = r'''class AgentPatentConnector(PatentConnector):
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

'''

src = open("data_sources.py", encoding="utf-8").read()
anchor = "class AgentMarketConnector(MarketConnector):"
if "class AgentPatentConnector" in src:
    print("이미 AgentPatentConnector 존재 — skip")
else:
    assert anchor in src, "AgentMarketConnector 못 찾음"
    src = src.replace(anchor, APC + anchor)
    open("data_sources.py", "w", encoding="utf-8").write(src)
    print("AgentPatentConnector 삽입 완료")

import py_compile
try:
    py_compile.compile("data_sources.py", doraise=True)
    print("data_sources.py 문법 OK")
except Exception as e:
    print("문법 에러:", e)
    import shutil; shutil.copy("data_sources.py.bak_4api", "data_sources.py")
    print("롤백함")
