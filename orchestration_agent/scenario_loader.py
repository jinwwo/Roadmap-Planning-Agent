"""
scenario_loader.py
──────────────────
Predefined demo scenarios for orchestration runs.

Scenarios live as JSON files under orchestration_agent/scenarios. They provide
the structured fields that Patent Agent needs, avoiding natural-language intake
drift during demos and A/B experiments.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).parent
SCENARIO_DIR = ROOT / "scenarios"


def _scenario_path(scenario_id: str) -> Path:
    safe_id = "".join(ch for ch in str(scenario_id or "") if ch.isalnum() or ch in ("_", "-"))
    return SCENARIO_DIR / f"{safe_id}.json"


def load_scenario(scenario_id: str) -> dict[str, Any]:
    path = _scenario_path(scenario_id)
    if not path.exists():
        raise FileNotFoundError(f"scenario not found: {scenario_id}")
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("scenario_id", path.stem)
    data.setdefault("prompt", scenario_to_prompt(data))
    return data


def list_scenarios() -> list[dict[str, Any]]:
    scenarios = []
    for path in sorted(SCENARIO_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        data.setdefault("scenario_id", path.stem)
        data.setdefault("prompt", scenario_to_prompt(data))
        scenarios.append({
            "scenario_id": data["scenario_id"],
            "name": data.get("name") or data["scenario_id"],
            "company_name": data.get("company_name"),
            "domain": data.get("domain"),
            "reference_year": data.get("reference_year"),
            "prompt": data.get("prompt"),
        })
    return scenarios


def scenario_to_prompt(data: dict[str, Any]) -> str:
    priorities = data.get("strategic_priorities") or data.get("priorities") or []
    related = data.get("related_companies") or []
    category_hints = data.get("category_hints") or []
    lines = [
        "[Company Scenario]",
        f"Company: {data.get('company_name', '')}",
        f"Industry: {data.get('industry') or data.get('domain', '')}",
        f"Domain: {data.get('domain', '')}",
        f"Company Type: {data.get('company_type', '')}",
        f"Annual Revenue: {data.get('annual_revenue', '')}",
        f"R&D Budget Ratio: {data.get('rd_budget_ratio', '')}",
        f"Annual R&D Budget: {data.get('annual_rd_budget', '')}",
        f"Planning Horizon: {data.get('time_horizon', '')}",
        f"Reference Year: {data.get('reference_year', '')}",
        f"Total Budget: {data.get('total_budget', '')} USD",
        f"Category Hints: {', '.join(category_hints)}",
        "",
        "[Strategic Direction]",
        *[f"- {item}" for item in priorities],
    ]
    if related:
        lines.extend(["", "[Related Companies]", ", ".join(related)])
    return "\n".join(line for line in lines if line is not None).strip()

