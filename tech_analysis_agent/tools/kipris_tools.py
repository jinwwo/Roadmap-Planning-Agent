"""
tools/kipris_tools.py
─────────────────────
KIPRIS Plus patent/publication API adapter.

The Patent Agent expects a provider-neutral portfolio shape. This adapter
collects domestic Korean patent/utility publication records and, when enabled,
foreign patent publication records by applicant, then normalizes KIPRIS XML
responses into that shape.
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from collections import Counter
from typing import Any

import requests

from config import (
    KIPRIS_API_KEY,
    KIPRIS_BASE_URL,
    KIPRIS_FOREIGN_COUNTRIES,
    KIPRIS_FOREIGN_MAX_RESULTS_PER_COUNTRY,
    KIPRIS_MAX_RESULTS,
    KIPRIS_TIMEOUT,
    PATENT_SCOPE,
)


KIPRIS_APPLICANT_ALIASES = {
    "amd": "어드밴스드 마이크로 디바이시즈",
    "advanced micro devices": "어드밴스드 마이크로 디바이시즈",
    "advanced micro devices inc": "어드밴스드 마이크로 디바이시즈",
    "nvidia": "엔비디아",
    "nvidia corporation": "엔비디아",
    "intel": "인텔",
    "intel corporation": "인텔",
    "google": "구글",
    "google llc": "구글",
    "broadcom": "브로드컴",
    "broadcom corporation": "브로드컴",
    "qualcomm": "퀄컴",
    "qualcomm incorporated": "퀄컴",
    "tsmc": "타이완 세미콘덕터 매뉴팩쳐링",
    "taiwan semiconductor manufacturing": "타이완 세미콘덕터 매뉴팩쳐링",
    "arm": "에이알엠 리미티드",
    "arm limited": "에이알엠 리미티드",
    # ── 자율주행 플랫폼 ──
    "hyundai mobis": "현대모비스",
    "hl mando": "만도",
    "mando": "만도",
    "stradvision": "스트라드비젼",
    # ── 이차전지 양극재 ──
    "lg chem": "엘지화학",
    "lg chemical": "엘지화학",
    "ecopro bm": "에코프로비엠",
    "ecopro": "에코프로비엠",
    "cosmo am&t": "코스모신소재",
    "cosmo amt": "코스모신소재",
    "cosmo new material": "코스모신소재",
    # ── AI LLM ──
    "naver": "네이버",
    "naver corporation": "네이버",
    "saltlux": "솔트룩스",
    "upstage": "업스테이지",
}


KIPRIS_FOREIGN_APPLICANT_ALIASES = {
    "amd": "Advanced Micro Devices",
    "advanced micro devices": "Advanced Micro Devices",
    "advanced micro devices inc": "Advanced Micro Devices",
    "어드밴스드 마이크로 디바이시즈": "Advanced Micro Devices",
    "nvidia": "NVIDIA",
    "nvidia corporation": "NVIDIA",
    "엔비디아": "NVIDIA",
    "intel": "Intel",
    "intel corporation": "Intel",
    "인텔": "Intel",
    "google": "Google",
    "google llc": "Google",
    "구글": "Google",
    "broadcom": "Broadcom",
    "broadcom corporation": "Broadcom",
    "브로드컴": "Broadcom",
    "qualcomm": "Qualcomm",
    "qualcomm incorporated": "Qualcomm",
    "퀄컴": "Qualcomm",
    "tsmc": "Taiwan Semiconductor Manufacturing",
    "taiwan semiconductor manufacturing": "Taiwan Semiconductor Manufacturing",
    "타이완 세미콘덕터 매뉴팩쳐링": "Taiwan Semiconductor Manufacturing",
    "arm": "ARM",
    "arm limited": "ARM",
    "에이알엠 리미티드": "ARM",
    "samsung electronics": "Samsung Electronics",
    "삼성전자": "Samsung Electronics",
    # ── 자율주행 ──
    "hyundai mobis": "Hyundai Mobis",
    "현대모비스": "Hyundai Mobis",
    "hl mando": "HL Mando",
    "mando": "HL Mando",
    "HL만도": "HL Mando",
    "stradvision": "STRADVISION",
    "스트라드비젼": "STRADVISION",
    # ── 이차전지 ──
    "lg chem": "LG Chem",
    "LG화학": "LG Chem",
    "ecopro bm": "EcoPro BM",
    "에코프로비엠": "EcoPro BM",
    "cosmo am&t": "Cosmo AM&T",
    "코스모신소재": "Cosmo AM&T",
    # ── AI LLM ──
    "naver": "NAVER",
    "네이버": "NAVER",
    "saltlux": "Saltlux",
    "솔트룩스": "Saltlux",
    "upstage": "Upstage",
    "업스테이지": "Upstage",
}


KIPRIS_EXPECTED_ASSIGNEE_TOKENS = {
    "amd": ["어드밴스드", "마이크로", "디바이시즈"],
    "advanced micro devices": ["어드밴스드", "마이크로", "디바이시즈"],
    "advanced micro devices inc": ["어드밴스드", "마이크로", "디바이시즈"],
    "어드밴스드 마이크로 디바이시즈": ["어드밴스드", "마이크로", "디바이시즈"],
    "nvidia": ["엔비디아"],
    "nvidia corporation": ["엔비디아"],
    "엔비디아": ["엔비디아"],
    "intel": ["인텔"],
    "intel corporation": ["인텔"],
    "인텔": ["인텔"],
    "google": ["구글"],
    "google llc": ["구글"],
    "구글": ["구글"],
    "broadcom": ["브로드컴"],
    "broadcom corporation": ["브로드컴"],
    "브로드컴": ["브로드컴"],
    "qualcomm": ["퀄컴"],
    "qualcomm incorporated": ["퀄컴"],
    "퀄컴": ["퀄컴"],
    "tsmc": ["타이완", "세미콘덕터"],
    "taiwan semiconductor manufacturing": ["타이완", "세미콘덕터"],
    "타이완 세미콘덕터 매뉴팩쳐링": ["타이완", "세미콘덕터"],
    "arm": ["에이알엠"],
    "arm limited": ["에이알엠"],
    "에이알엠 리미티드": ["에이알엠"],
}


KIPRIS_FOREIGN_EXPECTED_ASSIGNEE_TOKENS = {
    "amd": ["advanced", "micro", "devices"],
    "advanced micro devices": ["advanced", "micro", "devices"],
    "advanced micro devices inc": ["advanced", "micro", "devices"],
    "어드밴스드 마이크로 디바이시즈": ["advanced", "micro", "devices"],
    "nvidia": ["nvidia"],
    "nvidia corporation": ["nvidia"],
    "엔비디아": ["nvidia"],
    "intel": ["intel"],
    "intel corporation": ["intel"],
    "인텔": ["intel"],
    "google": ["google"],
    "google llc": ["google"],
    "구글": ["google"],
    "broadcom": ["broadcom"],
    "broadcom corporation": ["broadcom"],
    "브로드컴": ["broadcom"],
    "qualcomm": ["qualcomm"],
    "qualcomm incorporated": ["qualcomm"],
    "퀄컴": ["qualcomm"],
    "tsmc": ["taiwan", "semiconductor"],
    "taiwan semiconductor manufacturing": ["taiwan", "semiconductor"],
    "타이완 세미콘덕터 매뉴팩쳐링": ["taiwan", "semiconductor"],
    "arm": ["arm"],
    "arm limited": ["arm"],
    "에이알엠 리미티드": ["arm"],
    "samsung electronics": ["samsung"],
    "삼성전자": ["samsung"],
}


SUPPORTED_FOREIGN_COUNTRIES = {
    "US", "EP", "WO", "JP", "PJ", "CP", "CN", "TW", "RU", "CO", "SE", "ES", "IL"
}


class KIPRISPatentTool:
    """KIPRIS Plus REST client for patent/utility publication search."""

    CALL_INTERVAL = 0.35

    def __init__(self):
        self._last_call = 0.0
        self.base_url = KIPRIS_BASE_URL.rstrip("/")

    def _throttle(self) -> None:
        elapsed = time.time() - self._last_call
        if elapsed < self.CALL_INTERVAL:
            time.sleep(self.CALL_INTERVAL - elapsed)
        self._last_call = time.time()

    def _request(self, path: str, params: dict[str, Any]) -> tuple[str, str]:
        if not KIPRIS_API_KEY:
            return "", "KIPRIS_API_KEY is required when PATENT_DATA_PROVIDER=kipris"
        self._throttle()
        url = f"{self.base_url}{path}"
        try:
            resp = requests.get(url, params=params, timeout=KIPRIS_TIMEOUT)
            resp.raise_for_status()
            text = resp.text.strip()
            if not text:
                return "", "KIPRIS returned an empty response"
            return text, ""
        except requests.exceptions.RequestException as exc:
            return "", str(exc)

    def _request_with_auth_variants(
        self,
        method_name: str,
        params: dict[str, Any],
    ) -> tuple[str, str]:
        """Try both documented KIPRIS auth styles without exposing the key."""
        variants = (
            (
                f"/openapi/rest/patUtiModInfoSearchSevice/{method_name}",
                {**params, "accessKey": KIPRIS_API_KEY},
            ),
            (
                f"/kipo-api/kipi/patUtiModInfoSearchSevice/{method_name}",
                {"ServiceKey": KIPRIS_API_KEY, **params},
            ),
        )
        errors = []
        for path, payload in variants:
            text, err = self._request(path, payload)
            if err:
                errors.append(err)
                continue
            if self._looks_like_error(text):
                errors.append(self._extract_error_message(text))
                continue
            return text, ""
        return "", " | ".join(e for e in errors if e) or "KIPRIS request failed"

    def _request_foreign(
        self,
        method_name: str,
        params: dict[str, Any],
    ) -> tuple[str, str]:
        """Call KIPRIS Plus foreign patent REST endpoints."""
        payload = {**params, "accessKey": KIPRIS_API_KEY}
        text, err = self._request(
            f"/openapi/rest/ForeignPatentAdvencedSearchService/{method_name}",
            payload,
        )
        if err:
            return "", err
        if self._looks_like_error(text):
            return "", self._extract_error_message(text)
        return text, ""

    def _looks_like_error(self, xml_text: str) -> bool:
        lowered = xml_text.lower()
        if any(token in lowered for token in ("<error", "<fault", "servicekey", "accesskey")):
            return True
        try:
            root = ET.fromstring(xml_text)
            success = root.find(".//successYN")
            if success is not None and (success.text or "").strip().upper() == "N":
                return True
            result_code = root.find(".//resultCode")
            if result_code is not None and (result_code.text or "").strip() not in ("", "00", "0"):
                return True
        except ET.ParseError:
            pass
        return False

    def _extract_error_message(self, xml_text: str) -> str:
        try:
            root = ET.fromstring(xml_text)
            for tag in ("resultMsg", "returnAuthMsg", "errMsg", "message", "errorMsg", "faultstring"):
                found = root.find(f".//{tag}")
                if found is not None and found.text:
                    return found.text.strip()
        except ET.ParseError:
            pass
        return xml_text[:220]

    def _parse_xml_items(self, xml_text: str) -> list[dict[str, Any]]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return []

        candidates = root.findall(".//item")
        if not candidates:
            candidates = root.findall(".//docs")
        if not candidates:
            candidates = [
                node for node in root.iter()
                if any(child.tag for child in list(node))
                and self._node_text(node, "inventionTitle", "title", "applicationNumber")
            ]

        items = []
        seen = set()
        for node in candidates:
            title = self._node_text(node, "inventionTitle", "title", "inventionName")
            app_no = self._node_text(node, "applicationNumber", "appNumber", "applicationNo")
            open_no = self._node_text(node, "openNumber", "publicationNumber", "pubNumber")
            reg_no = self._node_text(node, "registerNumber", "registrationNumber", "regNumber")
            identifier = app_no or open_no or reg_no or title
            if not identifier or identifier in seen:
                continue
            seen.add(identifier)
            abstract = self._node_text(node, "astrtCont", "abstract", "summary")
            date = self._node_text(
                node,
                "applicationDate",
                "openDate",
                "publicationDate",
                "registerDate",
                "registrationDate",
            )
            applicant = self._node_text(node, "applicantName", "applicant", "rightHolder", "assignee")
            ipc = self._node_text(node, "ipcNumber", "ipc", "ipcCode")
            cpc = self._node_text(node, "cpcNumber", "cpc", "cpcCode")

            items.append({
                "id": identifier,
                "title": title,
                "abstract": abstract[:900] if abstract else "",
                "date": self._format_date(date),
                "assignee": applicant,
                "cited_by": 0,
                "num_claims": 0,
                "ipc": ipc,
                "cpc": cpc,
                "jurisdiction": "KR",
                "source": "kipris_domestic",
                "_source": "kipris",
            })
        return items

    def _parse_foreign_xml_items(self, xml_text: str, country: str) -> list[dict[str, Any]]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return []

        candidates = root.findall(".//searchResult")
        if not candidates:
            candidates = root.findall(".//item") or root.findall(".//docs")

        items = []
        seen = set()
        for node in candidates:
            country_code = self._node_text(node, "countryCode") or country
            title = self._node_text(node, "inventionName", "inventionTitle", "title")
            ltrtno = self._node_text(node, "ltrtno")
            app_no = self._node_text(node, "applicationNo", "applicationNumber", "appNumber")
            open_no = self._node_text(node, "openNumber", "publishrNo", "publicationNumber")
            reg_no = self._node_text(node, "registerNo", "registrationNumber")
            identifier = ltrtno or open_no or app_no or reg_no or title
            dedupe_key = f"{country_code}:{identifier}"
            if not identifier or dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            applicant = self._node_text(node, "applicant", "applicantName", "assignee")
            date = self._node_text(
                node,
                "applicationDate",
                "openDate",
                "registerDate",
                "priorityDate",
                "internationalApplicationDate",
                "internationalOpenDate",
            )
            abstract = self._node_text(node, "abstract", "astrtCont", "summary", "colString")
            ipc = self._node_text(node, "ipc", "ipcNumber")
            cpc = self._node_text(node, "cpc", "cpcNumber", "epc")

            items.append({
                "id": identifier,
                "title": title,
                "abstract": abstract[:900] if abstract else "",
                "date": self._format_date(date),
                "assignee": applicant,
                "cited_by": 0,
                "num_claims": 0,
                "ipc": ipc,
                "cpc": cpc,
                "jurisdiction": country_code,
                "source": "kipris_foreign",
                "_source": "kipris_foreign",
            })
        return items

    def _node_text(self, node, *names: str) -> str:
        wanted = {name.lower() for name in names}
        for child in node.iter():
            if child.tag.lower().split("}")[-1] in wanted and child.text:
                return child.text.strip()
        return ""

    def _format_date(self, value: str) -> str:
        digits = "".join(ch for ch in str(value or "") if ch.isdigit())
        if len(digits) == 8:
            return f"{digits[:4]}-{digits[4:6]}-{digits[6:]}"
        return value or ""

    def _filing_trend(self, patents: list[dict[str, Any]]) -> dict:
        counts = Counter()
        for item in patents:
            date = item.get("date") or ""
            if len(date) >= 4 and date[:4].isdigit():
                counts[int(date[:4])] += 1
        years = [2022, 2023, 2024]
        trend = {year: counts.get(year, 0) for year in years}
        start = trend[years[0]] or 1
        end = trend[years[-1]]
        trend["cagr_pct"] = round(((end / start) ** 0.5 - 1) * 100, 2) if end else 0.0
        return trend

    def search_by_applicant(
        self,
        applicant: str,
        docs_start: int = 1,
        docs_count: int | None = None,
    ) -> dict:
        docs_count = docs_count or KIPRIS_MAX_RESULTS
        query_applicant = KIPRIS_APPLICANT_ALIASES.get(
            (applicant or "").strip().lower(),
            applicant,
        )
        params = {
            "applicant": query_applicant,
            "docsStart": docs_start,
            "docsCount": min(max(docs_count, 1), 500),
        }
        text, err = self._request_with_auth_variants("applicantNameSearchInfo", params)
        if err:
            return {"error": err, "patents": [], "total_patent_count": 0}
        patents = self._parse_xml_items(text)
        patents = self._filter_expected_assignee(applicant, patents)
        return {
            "patents": patents,
            "total_patent_count": len(patents),
            "_raw_xml_preview": text[:500],
        }

    def search_foreign_by_applicant(
        self,
        applicant: str,
        country: str,
        docs_count: int | None = None,
    ) -> dict:
        country = (country or "").strip().upper()
        if country not in SUPPORTED_FOREIGN_COUNTRIES:
            return {
                "error": f"Unsupported KIPRIS foreign country code: {country}",
                "patents": [],
                "total_patent_count": 0,
            }

        docs_count = docs_count or KIPRIS_FOREIGN_MAX_RESULTS_PER_COUNTRY
        query_applicant = KIPRIS_FOREIGN_APPLICANT_ALIASES.get(
            (applicant or "").strip().lower(),
            applicant,
        )
        params = {
            "applicant": query_applicant,
            "currentPage": "1",
            "sortField": "AD",
            "sortState": "true",
            "collectionValues": country,
            "docsCount": min(max(docs_count, 1), 500),
        }
        text, err = self._request_foreign("applicantSearch", params)
        if err:
            return {
                "error": err,
                "patents": [],
                "total_patent_count": 0,
                "country": country,
            }

        patents = self._parse_foreign_xml_items(text, country)
        filtered = self._filter_expected_assignee(applicant, patents, foreign=True)
        # Some foreign collections localize applicant names, e.g. Japanese
        # transliterations. The endpoint itself is applicant-specific, so keep
        # the search result if token filtering would otherwise erase everything.
        patents = filtered or patents
        total = self._node_text(ET.fromstring(text), "totalSearchCount") if text else ""
        return {
            "patents": patents,
            "total_patent_count": int(total) if str(total).isdigit() else len(patents),
            "country": country,
            "_raw_xml_preview": text[:500],
        }

    def _filter_expected_assignee(
        self,
        applicant: str,
        patents: list[dict[str, Any]],
        *,
        foreign: bool = False,
    ) -> list[dict[str, Any]]:
        key = (applicant or "").strip().lower()
        token_map = KIPRIS_FOREIGN_EXPECTED_ASSIGNEE_TOKENS if foreign else KIPRIS_EXPECTED_ASSIGNEE_TOKENS
        alias_map = KIPRIS_FOREIGN_APPLICANT_ALIASES if foreign else KIPRIS_APPLICANT_ALIASES
        tokens = token_map.get(key)
        if not tokens:
            query = alias_map.get(key, applicant)
            tokens = [part for part in str(query).replace(",", " ").split() if len(part) >= 2]
        if not tokens:
            return patents

        filtered = []
        for item in patents:
            assignee = (item.get("assignee") or "").lower()
            if all(token.lower() in assignee for token in tokens):
                filtered.append(item)
        return filtered

    def collect_company_portfolio(
        self,
        company_name: str,
        domain_keywords: str = "",
    ) -> dict:
        scope = PATENT_SCOPE if PATENT_SCOPE in {"domestic", "foreign", "both"} else "domestic"
        domestic_result = {"patents": [], "total_patent_count": 0}
        foreign_results: dict[str, dict[str, Any]] = {}
        source_errors = {}

        if scope in {"domestic", "both"}:
            domestic_result = self.search_by_applicant(
                company_name,
                docs_start=1,
                docs_count=KIPRIS_MAX_RESULTS,
            )
            if domestic_result.get("error"):
                source_errors["domestic"] = domestic_result["error"]

        if scope in {"foreign", "both"}:
            for country in KIPRIS_FOREIGN_COUNTRIES:
                result = self.search_foreign_by_applicant(
                    company_name,
                    country,
                    docs_count=KIPRIS_FOREIGN_MAX_RESULTS_PER_COUNTRY,
                )
                foreign_results[country] = result
                if result.get("error"):
                    source_errors[f"foreign:{country}"] = result["error"]

        if source_errors and not (domestic_result.get("patents") or any(r.get("patents") for r in foreign_results.values())):
            return {
                "company_name": company_name,
                "domain_keywords": domain_keywords,
                "recent_patents": [],
                "filing_trend": {},
                "citation_summary": {},
                "error": source_errors,
                "source_breakdown": {
                    "scope": scope,
                    "domestic": {"num_patents": 0, "total_patent_count": 0},
                    "foreign": {
                        country: {"num_patents": 0, "total_patent_count": 0, "error": data.get("error")}
                        for country, data in foreign_results.items()
                    },
                },
                "_source": f"kipris:{scope}",
                "_mock": False,
            }

        patents = []
        patents.extend(domestic_result.get("patents") or [])
        for country in KIPRIS_FOREIGN_COUNTRIES:
            patents.extend((foreign_results.get(country) or {}).get("patents") or [])

        patents = self._dedupe_patents(patents)
        if domain_keywords:
            patents = self._rank_by_domain(patents, domain_keywords)

        return {
            "company_name": company_name,
            "domain_keywords": domain_keywords,
            "recent_patents": patents,
            "filing_trend": self._filing_trend(patents),
            "citation_summary": {
                "total_patents": (
                    domestic_result.get("total_patent_count", 0)
                    + sum((data or {}).get("total_patent_count", 0) for data in foreign_results.values())
                ),
                "avg_citations": 0,
                "max_citations": 0,
                "high_citation_ratio": 0.0,
            },
            "source_breakdown": {
                "scope": scope,
                "domestic": {
                    "num_patents": len(domestic_result.get("patents") or []),
                    "total_patent_count": domestic_result.get("total_patent_count", 0),
                    "error": domestic_result.get("error"),
                },
                "foreign": {
                    country: {
                        "num_patents": len((data or {}).get("patents") or []),
                        "total_patent_count": (data or {}).get("total_patent_count", 0),
                        "error": (data or {}).get("error"),
                    }
                    for country, data in foreign_results.items()
                },
            },
            "source_errors": source_errors,
            "_source": f"kipris:{scope}",
            "_mock": False,
        }

    def _dedupe_patents(self, patents: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deduped = []
        seen = set()
        for item in patents:
            key = (
                item.get("jurisdiction") or "",
                item.get("id") or item.get("title") or "",
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    def _rank_by_domain(self, patents: list[dict[str, Any]], domain_keywords: str) -> list[dict[str, Any]]:
        keyword_tokens = [
            token.lower()
            for token in domain_keywords.replace("/", " ").replace(",", " ").split()
            if len(token.strip()) >= 2
        ]
        scored = []
        for item in patents:
            haystack = (
                f"{item.get('title', '')} {item.get('abstract', '')} "
                f"{item.get('ipc', '')} {item.get('cpc', '')}"
            ).lower()
            score = sum(1 for token in keyword_tokens if token in haystack)
            scored.append((score, item.get("date") or "", item))
        scored.sort(key=lambda pair: (pair[0], pair[1]), reverse=True)
        return [item for _, _, item in scored]
