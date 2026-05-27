"""
tools/patent_tools.py
──────────────────────
Patent provider router for Patent Agent.

Current path:
- KIPRIS Plus API for real Korean patent/publication data
- local mock/example data for offline tests
"""

from config import PATENT_DATA_PROVIDER, USE_MOCK_PATENT
from tools.kipris_tools import KIPRISPatentTool
from tools.mock_data import mock_company_portfolio


class PatentPortfolioTool:
    """Provider router used by Patent Agent company-portfolio mode."""

    def __init__(self):
        if USE_MOCK_PATENT or PATENT_DATA_PROVIDER == "mock":
            self.provider = "mock"
            self.client = None
        elif PATENT_DATA_PROVIDER == "kipris":
            self.provider = "kipris"
            self.client = KIPRISPatentTool()
        else:
            raise ValueError(
                f"Unsupported PATENT_DATA_PROVIDER={PATENT_DATA_PROVIDER!r}. "
                "Use 'kipris' or 'mock'."
            )

    def collect_company_portfolio(
        self,
        company_name: str,
        domain_keywords: str = "",
    ) -> dict:
        if self.provider == "mock":
            return mock_company_portfolio(company_name, domain_keywords)
        return self.client.collect_company_portfolio(company_name, domain_keywords)
