"""
tools/mock_data.py
───────────────────
오프라인 / toy 데모용 Mock 데이터 생성기.

실제 USPTO / Tavily API가 불가용(키 없음 / 네트워크 차단 / 엔드포인트 장애)일 때
에이전트가 의미 있는 입력으로 동작하도록 합성 데이터를 반환합니다.

형식은 실제 tool의 `collect_full_signal()` 반환값과 동일해야 합니다.
"""

import hashlib
import random
from typing import List


def _seed_from(text: str) -> random.Random:
    """텍스트로부터 결정적 난수 발생기 생성 (같은 입력 → 같은 출력)"""
    h = hashlib.md5(text.encode("utf-8")).hexdigest()
    return random.Random(int(h[:8], 16))


# ── Patent Mock ──────────────────────────────────────────────
#
# 카테고리별 실제 기술 개념 기반 템플릿.
# `title_en` 은 특허 제목처럼 보이도록 영문으로, `concept_ko` 는 한국어
# 기술 개념 명칭 (LLM 이 name 필드에 바로 활용 가능).
#
# 키워드의 꼬리 단어로 카테고리를 식별합니다:
#   "... equipment tool"   → Equipment
#   "... material"         → Material
#   "... process method"   → Process
#   "... architecture ..." → Architecture
#   "... packaging ..."    → Packaging

_CATEGORY_CONCEPTS = {
    "Equipment": [
        ("EUV lithography source module with high-brightness plasma",          "EUV 리소그래피",       ["EUV","리소그래피","노광"]),
        ("High-NA EUV scanner optics assembly",                                 "High-NA EUV 스캐너",   ["High-NA","EUV","스캐너"]),
        ("Atomic layer deposition reactor for sub-3nm nodes",                   "ALD 장비",            ["ALD","박막증착"]),
        ("Directed self-assembly patterning tool",                              "DSA 패터닝 장비",     ["DSA","패터닝"]),
        ("Cryogenic etch chamber with dual-frequency RF",                       "극저온 에칭 장비",    ["Cryo-Etch","식각"]),
        ("Multi-beam e-beam inspection system",                                 "멀티빔 e-beam 검사",  ["e-beam","검사"]),
    ],
    "Material": [
        ("High-k metal gate dielectric composition",                            "High-k 게이트 유전체", ["High-k","게이트"]),
        ("Cobalt interconnect precursor material",                              "Co 인터커넥트 소재",   ["Cobalt","인터커넥트"]),
        ("Novel EUV photoresist with low line-edge roughness",                  "EUV 포토레지스트",    ["EUV","포토레지스트"]),
        ("Ruthenium-based barrier layer for BEOL",                              "Ru 배리어 메탈",       ["Ruthenium","BEOL"]),
        ("Low-k porous dielectric for advanced nodes",                          "Low-k 유전체",        ["Low-k","유전체"]),
        ("2D channel material (MoS2) for post-silicon FET",                     "2D 채널 소재",        ["2D","MoS2"]),
    ],
    "Process": [
        ("Gate-all-around nanosheet FET fabrication flow",                      "GAA 나노시트 공정",   ["GAA","Nanosheet"]),
        ("Backside power delivery network process",                             "Backside PDN 공정",   ["BS-PDN","후면전력"]),
        ("Selective epitaxial growth for source/drain",                         "선택적 에피 성장",    ["Epitaxy","S/D"]),
        ("Self-aligned double patterning (SADP)",                               "자기정합 이중 패터닝", ["SADP","패터닝"]),
        ("Atomic layer etching for 3D structures",                              "ALE 공정",            ["ALE","식각"]),
        ("Area-selective deposition of dielectrics",                            "영역선택 증착(ASD)",   ["ASD","증착"]),
    ],
    "Architecture": [
        ("Chiplet-based heterogeneous integration architecture",                "칩렛 헤테로지니어스", ["Chiplet","SoC"]),
        ("Wafer-scale AI accelerator with in-memory compute",                   "웨이퍼 스케일 AI",     ["Wafer-Scale","IMC"]),
        ("Near-threshold computing for low-power design",                       "초저전력 회로",       ["NTC","저전력"]),
        ("3D stacked SRAM with through-silicon via",                            "3D 스택 SRAM",        ["3D","SRAM","TSV"]),
        ("CFET complementary FET logic cell",                                   "CFET 로직 셀",        ["CFET","로직"]),
        ("Silicon photonics co-packaged I/O",                                   "Co-Packaged 실리콘 포토닉스", ["Photonics","CPO"]),
    ],
    "Packaging": [
        ("Hybrid bonding for 3D IC wafer stacking",                             "하이브리드 본딩",     ["Hybrid-Bonding","3D"]),
        ("Fan-out wafer-level packaging (FOWLP)",                               "FOWLP",               ["Fan-Out","WLP"]),
        ("2.5D silicon interposer with fine-pitch TSV",                         "2.5D 실리콘 인터포저", ["Interposer","TSV"]),
        ("Through-silicon via with high aspect ratio",                          "High-AR TSV",         ["TSV"]),
        ("Thermal compression bonding for HBM stacks",                          "TCB 본딩",            ["TCB","HBM"]),
        ("Embedded multi-die interconnect bridge",                              "EMIB 브리지",          ["EMIB"]),
    ],
}


def _detect_category(keyword: str) -> str:
    kw = keyword.lower()
    if "equipment" in kw or "tool" in kw:           return "Equipment"
    if "material" in kw:                            return "Material"
    if "process" in kw or "method" in kw:           return "Process"
    if "architecture" in kw or "circuit" in kw or "design" in kw: return "Architecture"
    if "packaging" in kw or "bonding" in kw or "stacking" in kw:  return "Packaging"
    return "Process"  # fallback


_ASSIGNEES = [
    "Taiwan Semiconductor Manufacturing Company",
    "ASML Netherlands B.V.",
    "Samsung Electronics Co., Ltd.",
    "Intel Corporation",
    "Applied Materials, Inc.",
    "Tokyo Electron Limited",
    "SK hynix Inc.",
    "Lam Research Corporation",
    "IBM Corporation",
    "GlobalFoundries Inc.",
]


def mock_patent_signal(keyword: str) -> dict:
    """USPTOPatentTool.collect_full_signal() 모사 — 카테고리별 실제 기술 개념 기반"""
    rng = _seed_from(keyword)
    category = _detect_category(keyword)
    concepts = list(_CATEGORY_CONCEPTS[category])
    rng.shuffle(concepts)

    base = rng.randint(400, 2500)
    cagr = round(rng.uniform(5.0, 45.0), 2)
    trend = {
        2022: int(base * 0.7),
        2023: int(base * 0.85),
        2024: base,
        "cagr_pct": cagr,
    }

    total_patents = base + rng.randint(1000, 5000)
    high_ratio = round(rng.uniform(25.0, 75.0), 2)
    citation_summary = {
        "total_patents": total_patents,
        "avg_citations": round(rng.uniform(3.0, 18.0), 2),
        "max_citations": rng.randint(50, 500),
        "high_citation_ratio": high_ratio,
    }

    rng.shuffle(_ASSIGNEES)
    top_assignees = [(a, rng.randint(5, 80)) for a in _ASSIGNEES[:5]]

    # 카테고리별 4~5개 concept 을 선택하여 특허 묶음 생성
    recent = []
    concept_tags_agg = []
    picked = concepts[:5]
    for (title_en, concept_ko, tags) in picked:
        concept_tags_agg.append({"concept_ko": concept_ko, "tags": tags})
        # 같은 concept 으로 1~2건의 특허 생성
        n = rng.choice([1, 2])
        for _ in range(n):
            recent.append({
                "title": title_en,
                "date": f"2024-{rng.randint(1,12):02d}-{rng.randint(1,28):02d}",
                "assignee": rng.choice(_ASSIGNEES),
                "cited_by": rng.randint(0, 120),
                "concept_ko": concept_ko,   # LLM 이 name 으로 바로 활용 가능
                "concept_tags": tags,
            })

    return {
        "keyword": keyword,
        "category": category,
        "recent_patents": recent,
        "concept_candidates": concept_tags_agg,   # 요약: 이 카테고리에서 관찰된 주요 기술 개념
        "filing_trend": trend,
        "citation_summary": citation_summary,
        "top_assignees": top_assignees,
        "_mock": True,
    }


# ── Market Mock ──────────────────────────────────────────────

_MARKET_SNIPPETS = {
    "market_size": [
        "The global market for {t} is projected to reach ${b}B by 2028, growing at a CAGR of {c}%.",
        "{t} TAM expanded to ${b}B in 2024 with analyst consensus forecasting {c}% CAGR through 2030.",
        "Leading research houses value the {t} segment at ${b}B, citing strong demand from AI/HPC workloads.",
    ],
    "investment": [
        "Tier-1 foundries committed over ${b}B in capex toward {t} capacity expansion in 2024-2025.",
        "Venture funding for {t} startups crossed ${b}B in the trailing twelve months, led by hyperscaler backers.",
        "Consortium of corporate R&D groups pledged ${b}B for {t} development as part of supply-chain de-risking.",
    ],
    "policy": [
        "The US CHIPS Act earmarks subsidies for domestic {t} fabrication with ${b}B allocated in 2025.",
        "Korean K-Chips Act expands tax credits to {t}-related equipment and process investments.",
        "EU Chips Act designates {t} as a strategic technology eligible for accelerated review.",
    ],
    "competitive": [
        "TSMC, Samsung, and Intel each announced {t} pilot lines, signaling an intensifying three-way race.",
        "ASML's monopoly on key {t} equipment continues to gate industry-wide ramp schedules.",
        "Specialty players (Applied Materials, TEL, Lam) dominate upstream {t} process equipment share.",
    ],
    "timeline": [
        "Mass production of {t}-enabled nodes is expected to begin ramp in 2026, reaching volume by 2027.",
        "Industry roadmaps suggest {t} commercialization window opens Q2 2026 with broader adoption by 2028.",
        "Analysts at SemiAnalysis project {t} HVM (high-volume manufacturing) readiness in 2027-2028.",
    ],
}


def _mock_snippets(kind: str, tech_name: str, rng: random.Random) -> list:
    items = []
    for tmpl in _MARKET_SNIPPETS[kind]:
        items.append({
            "title": f"{tech_name} — {kind.replace('_',' ').title()} Brief",
            "url": f"https://mock.local/{kind}/{abs(hash(tech_name))%9999}",
            "content": tmpl.format(
                t=tech_name,
                b=round(rng.uniform(5, 120), 1),
                c=round(rng.uniform(8, 35), 1),
            ),
            "score": round(rng.uniform(0.6, 0.95), 2),
        })
    return items


def mock_market_signal(tech_name: str, domain: str) -> dict:
    """MarketIntelligenceTool.collect_full_signal() 모사"""
    rng = _seed_from(f"{tech_name}|{domain}")
    return {
        "tech_name": tech_name,
        "domain": domain,
        "market_size_data":  _mock_snippets("market_size", tech_name, rng),
        "investment_data":   _mock_snippets("investment", tech_name, rng),
        "policy_data":       _mock_snippets("policy", tech_name, rng),
        "competitive_data":  _mock_snippets("competitive", tech_name, rng),
        "timeline_data":     _mock_snippets("timeline", tech_name, rng),
        "_mock": True,
    }
