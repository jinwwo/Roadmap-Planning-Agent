"""
adapters/roadmap_planning_agent.py
───────────────────────────────────
Roadmap-Planning-Agent 레포의 Agent 출력 JSON → evaluation input_pack 변환.

매핑 근거: 각 Agent의 state.py TypedDict 정의 + mock_data.py 반환 포맷

사용법:
    from adapters.roadmap_planning_agent import AgentOutputAdapter
    adapter = AgentOutputAdapter()
    input_pack = adapter.from_files(
        tech_path="output_tech_candidates.json",
        roadmap_path="output_planned_roadmap.json",
        investment_path="output_investment_strategy.json",
    )
"""

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional


class AgentOutputAdapter:
    """
    3개 Agent + Orchestrator 출력 → evaluation input_pack 변환.

    스키마 매핑 상세:
    ─────────────────────────────────────────────────────────────
    Agent 1 (tech_analysis_agent/state.py::TechCandidate)
      필드명: name (not tech_name), trl (not TRL), category,
              patent_score, market_score, final_score,
              expected_market_boom_quarter ("YYYY QX" 형식),
              dependency_hints, rationale

    Agent 2 (roadmap_planner_agent/state.py::RoadmapItem)
      필드명: name, phase_name, start_q ("YYYY QX"), target_q ("YYYY QX"),
              prerequisites, lead_time_quarters, justification

    Agent 3 (investment_strategist_agent/state.py::InvestmentStrategy)
      구조: stage 단위. 5지표가 evaluation_scores 안에 nested:
        evaluation_scores: {market_opportunity, strategic_fit,
                           executability, uncertainty, urgency}
      Tier: recommended_investment_tier ("Tier 1"/"Tier 2"/"Tier 3")
      stage에 포함된 tech: stages[].tech_ids / stages[].technologies
    ─────────────────────────────────────────────────────────────
    """

    # Agent 3 Tier → evaluation tier 매핑
    TIER_MAP = {
        "Tier 1": "Strategic",
        "Tier 2": "High",
        "Tier 3": "Medium",
        "tier 1": "Strategic",
        "tier 2": "High",
        "tier 3": "Medium",
        1: "Strategic",
        2: "High",
        3: "Medium",
    }

    def from_files(
        self,
        tech_path: str,
        roadmap_path: str,
        investment_path: str,
        report_path: Optional[str] = None,
        holdout_path: Optional[str] = None,
        scenario_label: str = "multi-agent",
    ) -> dict:
        """파일 경로로부터 input_pack 생성."""
        tech_data = self._load(tech_path)
        roadmap_data = self._load(roadmap_path)
        investment_data = self._load(investment_path)
        report_data = self._load(report_path) if report_path else None
        holdout_data = self._load(holdout_path) if holdout_path else None

        return self.convert(
            tech_data, roadmap_data, investment_data,
            report_data, holdout_data, scenario_label
        )

    def from_bundle(self, bundle_path: str) -> dict:
        """evaluation_bundle.json 1개에서 input_pack 생성."""
        bundle = self._load(bundle_path)
        return self.convert_bundle(bundle)

    def convert_bundle(self, bundle: dict) -> dict:
        """번들 dict → input_pack."""
        tech_data = {"tech_candidates": bundle.get("tech_candidates", [])}
        if "market_context" in bundle:
            tech_data["market_context"] = bundle["market_context"]
        roadmap_data = {"planned_roadmap": bundle.get("planned_roadmap", [])}
        investment_data = {
            "investment_strategy": bundle.get("investment_strategy", []),
            "stages": bundle.get("stages", []),
        }
        report_data = bundle.get("orchestrator_report")
        active = bundle.get("active_agents", [])
        if set(active) == {"1", "2", "3"}:
            scenario = "multi-agent (full)"
        elif active:
            off = {"1", "2", "3"} - set(active)
            scenario = "noA" + ",".join(sorted(off))
        else:
            scenario = "unknown"
        pack = self.convert(tech_data, roadmap_data, investment_data,
                            report_data, scenario_label=scenario)
        if bundle.get("holdout_data"):
            pack["holdout_data"] = bundle["holdout_data"]
        return pack

    def from_auto_detect(self, outputs_dir: str) -> dict:
        """Orchestrator outputs/ 에서 파일 4개 탐지 → 번들 생성 → input_pack."""
        from datetime import datetime
        base = Path(outputs_dir)
        file_map = {
            "tech": ["tech_candidates.json", "output_tech_candidates.json"],
            "roadmap": ["planned_roadmap.json", "output_planned_roadmap.json"],
            "investment": ["investment_strategy.json", "output_investment_strategy.json"],
            "report": ["orchestrator_report.json"],
        }
        found = {}
        for key, candidates in file_map.items():
            for fname in candidates:
                p = base / fname
                if p.exists():
                    found[key] = p; break
                for f in base.glob(f"*_{fname}"):
                    found[key] = f; break
        missing = {"tech", "roadmap", "investment"} - set(found.keys())
        if missing:
            raise FileNotFoundError(f"필수 파일 미발견: {missing} (경로: {base})")
        print("  ── 자동 탐지 ──")
        for k, v in found.items():
            print(f"    ✓ {k}: {v}")
        tech_data = self._load(str(found["tech"]))
        roadmap_data = self._load(str(found["roadmap"]))
        investment_data = self._load(str(found["investment"]))
        report_data = self._load(str(found["report"])) if "report" in found else None
        bundle = {
            "generated_at": datetime.now().isoformat(),
            "orchestrator_report": report_data,
            "tech_candidates": tech_data.get("tech_candidates", []),
            "planned_roadmap": roadmap_data.get("planned_roadmap", []),
            "investment_strategy": investment_data.get("investment_strategy", []),
            "stages": investment_data.get("stages", []),
            "market_context": tech_data.get("market_context", {}),
            "active_agents": report_data.get("active_agents", ["1","2","3"]) if report_data else ["1","2","3"],
        }
        bundle_path = base / "evaluation_bundle.json"
        with open(bundle_path, "w", encoding="utf-8") as f:
            json.dump(bundle, f, indent=2, ensure_ascii=False)
        print(f"    ✓ 번들 저장: {bundle_path}")
        return self.convert_bundle(bundle)

    def convert(
        self,
        tech_data: dict,
        roadmap_data: dict,
        investment_data: dict,
        report_data: Optional[dict] = None,
        holdout_data: Optional[dict] = None,
        scenario_label: str = "multi-agent",
    ) -> dict:
        """Agent 출력 dict들 → evaluation input_pack."""

        metadata = self._build_metadata(report_data, scenario_label)
        tech_candidates = self._convert_tech_candidates(tech_data)
        planned_roadmap = self._convert_roadmap(roadmap_data)
        investment_strategy = self._convert_investment(
            investment_data, tech_candidates, planned_roadmap
        )
        constraints = self._build_constraints(report_data)

        # dependency: roadmap의 prerequisites를 tech_candidates에 역주입
        dep_map = {r["tech_id"]: r["prerequisites"] for r in planned_roadmap}
        for tech in tech_candidates:
            if tech["tech_id"] in dep_map:
                tech["dependency_hints"] = dep_map[tech["tech_id"]]

        pack = {
            "metadata": metadata,
            "tech_candidates": tech_candidates,
            "planned_roadmap": planned_roadmap,
            "investment_strategy": investment_strategy,
            "constraints": constraints,
        }
        if holdout_data:
            pack["holdout_data"] = holdout_data
        return pack

    # ─────────────────────────────────────────
    #  metadata
    # ─────────────────────────────────────────

    def _build_metadata(self, report_data: Optional[dict], scenario_label: str) -> dict:
        meta = {
            "scenario_label": scenario_label,
            "domain": "Unknown",
            "evaluation_cutoff_year": 2025,
        }
        if report_data and "problem_frame" in report_data:
            pf = report_data["problem_frame"]
            meta["domain"] = pf.get("industry", "Unknown")
            years = re.findall(r"20\d{2}", str(pf.get("time_horizon", "")))
            if years:
                meta["evaluation_cutoff_year"] = int(years[-1])
            meta["company_type"] = pf.get("company_type", "")
            meta["objective"] = pf.get("objective", "")
            meta["strategic_priorities"] = pf.get("strategic_priorities", [])
        return meta

    # ─────────────────────────────────────────
    #  tech_candidates (Agent 1)
    # ─────────────────────────────────────────

    def _convert_tech_candidates(self, tech_data: dict) -> List[dict]:
        """
        Agent 1 출력 포맷:
        {
          "market_context": {...},
          "tech_candidates": [
            {
              "tech_id": "T01",
              "name": "GAA 나노시트 공정",    ← "name" not "tech_name"
              "category": "Process",
              "trl": 5,                        ← 소문자 "trl" not "TRL"
              "patent_score": 72.5,
              "market_score": 81.0,
              "final_score": 77.2,
              "expected_market_boom_quarter": "2027 Q3",  ← 공백 구분
              "dependency_hints": ["T03"],
              "rationale": "..."
            }
          ]
        }
        """
        raw = tech_data.get("tech_candidates", [])
        if not raw and isinstance(tech_data, list):
            raw = tech_data

        result = []
        for t in raw:
            # "name" → "tech_name" 변환
            name = t.get("name", t.get("tech_name", ""))
            boom_q = t.get("expected_market_boom_quarter", "")

            tech = {
                "tech_id": t.get("tech_id", ""),
                "tech_name": name,
                "category": t.get("category", "General"),
                "TRL": t.get("trl", t.get("TRL", 5)),  # 소문자 trl 우선
                "patent_score": t.get("patent_score", 0),
                "market_score": t.get("market_score", 0),
                "final_score": t.get("final_score", 0),
                "expected_market_boom_quarter": self._norm_q(boom_q),
                "dependency_hints": t.get("dependency_hints", []),
                "keywords": self._gen_keywords(name),
            }
            result.append(tech)
        return result

    # ─────────────────────────────────────────
    #  planned_roadmap (Agent 2)
    # ─────────────────────────────────────────

    def _convert_roadmap(self, roadmap_data: dict) -> List[dict]:
        """
        Agent 2 출력 포맷:
        {
          "market_context": {...},
          "planned_roadmap": [
            {
              "tech_id": "T01",
              "name": "...",
              "phase_name": "1단계: 기반 R&D",
              "start_q": "2025 Q3",        ← 공백 구분
              "target_q": "2027 Q2",
              "prerequisites": ["T03"],
              "lead_time_quarters": 7,
              "justification": "..."
            }
          ]
        }
        """
        raw = roadmap_data.get("planned_roadmap", [])
        if not raw and isinstance(roadmap_data, list):
            raw = roadmap_data

        result = []
        for r in raw:
            # DROPPED 항목 제외
            if (r.get("phase_name") or "") == "DROPPED":
                continue
            entry = {
                "tech_id": r.get("tech_id", ""),
                "tech_name": r.get("name", r.get("tech_name", "")),
                "start_q": self._norm_q(r.get("start_q", "")),
                "target_q": self._norm_q(r.get("target_q", "")),
                "prerequisites": r.get("prerequisites", []),
            }
            result.append(entry)
        return result

    # ─────────────────────────────────────────
    #  investment_strategy (Agent 3)
    # ─────────────────────────────────────────

    def _convert_investment(
        self, investment_data: dict, techs: List[dict], roadmap: List[dict]
    ) -> List[dict]:
        """
        Agent 3 출력 포맷:
        {
          "market_context": {...},
          "stages": [
            {
              "stage": "1단계: 기반 R&D",
              "period": "2025 Q1 - 2027 Q2",
              "goal": "...",
              "technologies": ["GAA 나노시트 공정", ...],  ← 이름으로
              "tech_ids": ["T01", "T03", ...],             ← ID로
              "num_items": 3
            }
          ],
          "investment_strategy": [
            {
              "stage": "1단계: 기반 R&D",
              "period": "...",
              "evaluation_scores": {       ← NESTED 구조 (핵심 차이점)
                "market_opportunity": 4,
                "strategic_fit": 4,
                "executability": 3,
                "uncertainty": 3,
                "urgency": 4
              },
              "investment_attractiveness": "high",
              "investment_urgency": "high",
              "recommended_investment_tier": "Tier 1",
              "investment_scope": "...",
              "recommended_action": "...",
              "rationale": [...],
              "major_risks": [...],
              "resource_focus": [...]
            }
          ]
        }
        """
        strategies = investment_data.get("investment_strategy", [])
        stages = investment_data.get("stages", [])

        # stage name → strategy 매핑
        strat_by_stage = {}
        for s in strategies:
            sname = s.get("stage", s.get("stage_name", s.get("phase_name", "")))
            strat_by_stage[sname] = s

        # stage name → tech_ids 매핑
        stage_tids: Dict[str, List[str]] = {}
        for st in stages:
            sname = st.get("stage", st.get("stage_name", st.get("phase_name", "")))
            tids = st.get("tech_ids", [])
            if not tids:
                tech_names = st.get("technologies", [])
                name_to_id = {t["tech_name"]: t["tech_id"] for t in techs}
                tids = [name_to_id.get(n, "") for n in tech_names if n in name_to_id]
            stage_tids[sname] = tids

        # tech_investments에서 개별 기술 tier/scores 매핑 (우선순위 1)
        tech_invest_map: Dict[str, dict] = {}
        for s in strategies:
            for ti in s.get("tech_investments", []):
                tid = ti.get("tech_id", "")
                if tid:
                    tech_invest_map[tid] = ti

        # tech_id 단위로 풀어내기
        result = []
        covered = set()

        for stage_name, tids in stage_tids.items():
            strat = strat_by_stage.get(stage_name, {})

            # stage 레벨 기본값
            stage_scores = strat.get("evaluation_scores", {})
            stage_tier_raw = strat.get("recommended_investment_tier",
                                       strat.get("tier", "Tier 2"))

            for tid in tids:
                if not tid or tid in covered:
                    continue
                covered.add(tid)

                # tech_investments에 개별 데이터가 있으면 우선 사용
                ti = tech_invest_map.get(tid, {})
                scores = ti.get("evaluation_scores", stage_scores)
                tier_raw = ti.get("recommended_investment_tier",
                             ti.get("tier", stage_tier_raw))
                investment_tier = self.TIER_MAP.get(tier_raw, "Medium")

                result.append({
                    "tech_id": tid,
                    "tech_name": self._find_name(tid, techs),
                    "investment_tier": investment_tier,
                    "market_opportunity": float(scores.get("market_opportunity", 3)),
                    "strategic_fit": float(scores.get("strategic_fit", 3)),
                    "executability": float(scores.get("executability", 3)),
                    "uncertainty": float(scores.get("uncertainty", 3)),
                    "urgency": float(scores.get("urgency", 3)),
                })

        # stage에 없는 tech → 기본값
        for t in techs:
            if t["tech_id"] not in covered:
                result.append({
                    "tech_id": t["tech_id"],
                    "tech_name": t["tech_name"],
                    "investment_tier": "Medium",
                    "market_opportunity": 3.0,
                    "strategic_fit": 3.0,
                    "executability": 3.0,
                    "uncertainty": 3.0,
                    "urgency": 3.0,
                })

        return result

    # ─────────────────────────────────────────
    #  constraints
    # ─────────────────────────────────────────

    def _build_constraints(self, report_data: Optional[dict]) -> dict:
        constraints = {
            "total_budget": 100,
            "budget_allocation": {},
            "planning_horizon_end": "2030-Q4",
        }
        if report_data and "problem_frame" in report_data:
            pf = report_data["problem_frame"]
            constraints["total_budget"] = pf.get("total_budget", 100)
            years = re.findall(r"20\d{2}", str(pf.get("time_horizon", "")))
            if years:
                constraints["planning_horizon_end"] = f"{years[-1]}-Q4"
        return constraints

    # ─────────────────────────────────────────
    #  Helpers
    # ─────────────────────────────────────────

    def _find_name(self, tech_id: str, techs: List[dict]) -> str:
        for t in techs:
            if t.get("tech_id") == tech_id:
                return t.get("tech_name", "")
        return ""

    def _norm_q(self, q: str) -> str:
        """'2028 Q1' → '2028-Q1'"""
        if not q:
            return ""
        m = re.match(r"(\d{4})\s*[-]?\s*Q(\d)", str(q).strip(), re.IGNORECASE)
        return f"{m.group(1)}-Q{m.group(2)}" if m else str(q).strip()

    def _gen_keywords(self, name: str) -> List[str]:
        if not name:
            return []
        clean = re.sub(r"[^a-zA-Z0-9가-힣\s]", " ", name.lower())
        return [w for w in clean.split() if len(w) > 1][:5]

    @staticmethod
    def _load(path: str) -> dict:
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"파일 없음: {path}")
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)


# ── CLI ──────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Agent 출력 → input_pack 변환")
    parser.add_argument("--tech", required=True)
    parser.add_argument("--roadmap", required=True)
    parser.add_argument("--investment", required=True)
    parser.add_argument("--report")
    parser.add_argument("--output", default="input_pack.json")
    parser.add_argument("--scenario", default="multi-agent")
    args = parser.parse_args()

    adapter = AgentOutputAdapter()
    pack = adapter.from_files(
        args.tech, args.roadmap, args.investment,
        report_path=args.report, scenario_label=args.scenario,
    )
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(pack, f, indent=2, ensure_ascii=False)
    print(f"✓ {args.output} ({len(pack['tech_candidates'])} techs, "
          f"{len(pack['planned_roadmap'])} roadmap, "
          f"{len(pack['investment_strategy'])} investment)")
