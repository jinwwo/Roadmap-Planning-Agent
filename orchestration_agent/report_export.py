"""
report_export.py
─────────────────
Orchestrator 의 최종 결과 (problem_frame + 3 agents output + review) 를
Markdown / HTML 두 형식으로 export.

용도:
  - 협업자 공유 (Markdown — GitHub/Notion 붙여넣기)
  - 단일 파일 이메일 첨부 (HTML — 스타일 인라인)

각 에이전트의 최종 출력을 그대로 보여주는 표 / 카드 형식.
LLM 호출 없이 코드로만 렌더 — 순수 데이터 변환.
"""

from __future__ import annotations
import html
import json
from typing import Any, Dict, List


# ── 유틸 ─────────────────────────────────────────────────────

def _fmt_usd(n) -> str:
    try:
        n = float(n or 0)
    except Exception:
        return "-"
    if not n:
        return "-"
    if n >= 1e9:
        return f"${n / 1e9:.2f}B"
    if n >= 1e6:
        return f"${n / 1e6:.0f}M"
    return f"${n:,.0f}"


def _fmt_pct(v) -> str:
    try:
        v = float(v or 0)
    except Exception:
        return "-"
    return f"{v * 100:.0f}%" if v else "-"


def _safe(s: Any) -> str:
    return str(s) if s is not None else ""


def _esc_html(s: Any) -> str:
    return html.escape(_safe(s))


def _tier_class(tier: str) -> str:
    n = "".join(c for c in (tier or "") if c.isdigit())
    return f"tier-{n}" if n else "tier-x"


# ── Markdown export ─────────────────────────────────────────

def generate_markdown_report(report_data: Dict[str, Any]) -> str:
    """
    report_data 구조:
      {
        "problem_frame": {company_name, industry, ...},
        "review": {decision, ..., report: {executive_summary, ..., artifacts_summary}},
        "active_agents": [...],
        "iteration": int,
        "tech_candidates": [...],     # optional - if not in report_data, try review.report.artifacts_summary
        "planned_roadmap": [...],
        "investment_strategy": [...],
      }
    """
    pf = report_data.get("problem_frame") or {}
    review = report_data.get("review") or {}
    report = review.get("report") or {}
    artifacts = report.get("artifacts_summary") or {}

    # 데이터 소스 — direct 먼저, 없으면 artifacts_summary 폴백
    tech_candidates = report_data.get("tech_candidates") or artifacts.get("agent1_tech_candidates") or []
    planned_roadmap = report_data.get("planned_roadmap") or artifacts.get("agent2_planned_roadmap") or []
    investment_strategy = report_data.get("investment_strategy") or artifacts.get("agent3_investment_strategy") or []
    year_matrix = artifacts.get("year_tech_matrix") or {}

    md = []

    # ── Header ──
    company = pf.get("company_name") or "(unknown)"
    industry = pf.get("industry") or "-"
    horizon = pf.get("time_horizon") or "-"
    md.append(f"# 기술 로드맵 보고서 — {company}")
    md.append("")
    md.append(f"- **산업**: {industry}")
    md.append(f"- **계획 기간**: {horizon}")
    if pf.get("annual_revenue"):
        md.append(f"- **연 매출**: {_fmt_usd(pf.get('annual_revenue'))}")
    if pf.get("annual_rd_budget"):
        md.append(f"- **연 R&D 예산**: {_fmt_usd(pf.get('annual_rd_budget'))} (비중 {_fmt_pct(pf.get('rd_budget_ratio'))})")
    if pf.get("total_budget"):
        md.append(f"- **총 예산 (5년 envelope)**: {_fmt_usd(pf.get('total_budget'))}")
    md.append(f"- **평가 결정**: `{review.get('decision', '?')}` (iter {report_data.get('iteration', '?')})")
    md.append("")

    direction = pf.get("strategic_direction") or []
    if direction:
        md.append("### 🎯 Strategic Direction")
        md.append("")
        for i, d in enumerate(direction, 1):
            md.append(f"{i}. {d}")
        md.append("")

    # ── Agent 1 — Technology Candidates ──
    if tech_candidates:
        md.append(f"## 🔬 Agent 1 · Technology Candidates ({len(tech_candidates)})")
        md.append("")
        md.append("| tech_id | name | category | TRL | final | market | patent | boom |")
        md.append("|---|---|---|---:|---:|---:|---:|---|")
        for t in tech_candidates:
            md.append(
                f"| `{t.get('tech_id', '')}` "
                f"| {t.get('name', '')[:30]} "
                f"| {t.get('category', '')} "
                f"| {t.get('trl', '')} "
                f"| {t.get('final_score', '')} "
                f"| {t.get('market_score', '')} "
                f"| {t.get('patent_score', '')} "
                f"| {t.get('expected_market_boom_quarter', '')} |"
            )
        md.append("")

    # ── Agent 2 — Planned Roadmap ──
    if planned_roadmap:
        md.append(f"## 🛣️ Agent 2 · Planned Roadmap ({len(planned_roadmap)})")
        md.append("")
        for r in planned_roadmap:
            tid = r.get("tech_id", "")
            name = r.get("name", "")
            ys = r.get("year_idx_start", "?")
            yt = r.get("year_idx_target", "?")
            prereq = ", ".join(r.get("prerequisites") or []) or "(none)"
            reasoning = r.get("reasoning") or {}
            md.append(f"### `{tid}` · {name}")
            md.append("")
            md.append(f"- **차년도**: {ys}차년도 → {yt}차년도")
            md.append(f"- **Prerequisites**: {prereq}")
            if reasoning.get("year_placement"):
                md.append(f"- **📅 차년도 배치 이유**: {reasoning['year_placement']}")
            if reasoning.get("tech_execution"):
                md.append(f"- **🛠️ 기술 수행 이유**: {reasoning['tech_execution']}")
            if reasoning.get("investment_selection"):
                md.append(f"- **🎯 투자 선정 이유**: {reasoning['investment_selection']}")
            md.append("")

    # ── Agent 3 — Investment Strategy ──
    all_invs = []
    for s in (investment_strategy or []):
        for ti in (s.get("tech_investments") or []):
            all_invs.append(ti)
    if all_invs:
        md.append(f"## 💰 Agent 3 · Investment Strategy ({len(all_invs)} techs)")
        md.append("")
        total_alloc = sum(float(ti.get("tech_budget_usd") or 0) for ti in all_invs)
        cap = pf.get("total_budget") or 0
        md.append(f"- **예산 한도**: {_fmt_usd(cap)} · **배분 합**: {_fmt_usd(total_alloc)}"
                  + (f" (편차 {((total_alloc - cap) / cap * 100):.1f}%)" if cap else ""))
        md.append("")
        for ti in all_invs:
            tid = ti.get("tech_id", "")
            name = ti.get("name", "")
            tier = ti.get("recommended_investment_tier", "")
            budget = ti.get("tech_budget_usd", 0)
            scores = ti.get("evaluation_scores") or {}
            reasoning = ti.get("reasoning") or {}
            md.append(f"### `{tid}` · {name} — **{tier}** · {_fmt_usd(budget)}")
            md.append("")
            md.append(
                f"- **5축 점수**: TAM={scores.get('market_size_growth', '?')} · "
                f"TRL={scores.get('tech_readiness', '?')} · "
                f"RISK={scores.get('tech_risk', '?')} · "
                f"COMP={scores.get('competitive_advantage', '?')} · "
                f"URG={scores.get('development_urgency', '?')}"
            )
            if ti.get("recommended_action"):
                md.append(f"- **권장 액션**: {ti['recommended_action']}")
            if ti.get("tech_budget_rationale"):
                md.append(f"- **💵 예산 결정 근거**: {ti['tech_budget_rationale']}")
            if reasoning.get("market_evaluation"):
                md.append(f"- **📊 시장 평가**: {reasoning['market_evaluation']}")
            if reasoning.get("tech_evaluation"):
                md.append(f"- **⚙️ 기술 평가**: {reasoning['tech_evaluation']}")
            if reasoning.get("investment_decision"):
                md.append(f"- **💰 투자 결정**: {reasoning['investment_decision']}")
            risks = ti.get("major_risks") or []
            if risks:
                md.append(f"- **⚠️ 리스크**:")
                for r in risks:
                    md.append(f"  - {r}")
            resources = ti.get("resource_focus") or []
            if resources:
                md.append(f"- **🔧 자원 집중**:")
                for r in resources:
                    md.append(f"  - {r}")
            md.append("")

    # ── 차년도별 활동 요약 ──
    if planned_roadmap:
        max_y = max((r.get("year_idx_target") or 0) for r in planned_roadmap) or 1
        md.append(f"## 📅 차년도별 활동 요약 ({max_y}년 계획)")
        md.append("")
        for y in range(1, max_y + 1):
            # 해당 차년도에 active 한 기술 목록 (year_idx_start ≤ y ≤ year_idx_target)
            active_techs = [r for r in planned_roadmap
                            if (r.get("year_idx_start") or 1) <= y <= (r.get("year_idx_target") or 1)]
            if not active_techs:
                continue
            year_total = 0
            md.append(f"### {y}차년도")
            md.append("")
            for r in active_techs:
                tid = r.get("tech_id", "")
                name = r.get("name", "")
                ys = r.get("year_idx_start", 1)
                yt = r.get("year_idx_target", ys)
                is_start = (y == ys)
                inv = next((ti for ti in all_invs if ti.get("tech_id") == tid), None)
                budget = (inv.get("tech_budget_usd") if inv else 0) or 0
                tier = inv.get("recommended_investment_tier", "") if inv else ""
                # 시작 차년도면 예산 표시, 진행 중이면 표시 X
                if is_start:
                    year_total += budget
                stage_label = "시작" if y == ys else ("완료" if y == yt else "진행")
                budget_str = f" — {_fmt_usd(budget)}" if is_start else ""
                md.append(f"- **`{tid}` · {name}** ({tier}) — {stage_label}{budget_str}")
                # 효과 / 타겟 마켓: reasoning.tech_execution + investment_decision 중 1줄 요약
                if is_start and inv:
                    inv_reason = (inv.get("reasoning") or {}).get("investment_decision") or ""
                    tech_reason = (r.get("reasoning") or {}).get("tech_execution") or ""
                    why = tech_reason or inv_reason
                    if why:
                        md.append(f"  - 수행 이유 / 효과: {why}")
            md.append("")
            md.append(f"**1차년도 시작 예산 합계: {_fmt_usd(year_total)}**" if y == 1 else f"**{y}차년도 시작 예산 합계: {_fmt_usd(year_total)}**")
            md.append("")

    # ── Final Review (decision / issues) ──
    md.append("## ✅ Final Review")
    md.append("")
    md.append(f"- **Decision**: `{review.get('decision', '?')}`")
    issues = review.get("issues") or []
    if issues:
        md.append(f"- **Issues ({len(issues)})**:")
        for it in issues:
            if isinstance(it, dict):
                md.append(f"  - `[{it.get('axis', '?')}]` {it.get('text', '')}")
            else:
                md.append(f"  - {it}")
    if review.get("diagnostic_summary"):
        md.append("")
        md.append(f"> {review['diagnostic_summary']}")

    return "\n".join(md) + "\n"


# ── HTML export ─────────────────────────────────────────────

_HTML_CSS = """
body { font-family: "Noto Sans CJK KR", "Noto Sans KR", "Malgun Gothic",
       -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       background: #0e1014; color: #e6e8ee; max-width: 1080px; margin: 0 auto;
       padding: 24px; line-height: 1.6; }
h1 { color: #c08a4a; border-bottom: 2px solid #c08a4a; padding-bottom: 8px; }
h2 { color: #d6b58c; margin-top: 32px; border-bottom: 1px solid #2a303c; padding-bottom: 6px; }
h3 { color: #d6b58c; margin-top: 20px; }
code, .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; color: #c08a4a;
              background: #1d222c; padding: 2px 5px; border-radius: 3px; }
table { width: 100%; border-collapse: collapse; margin: 8px 0; font-size: 13px; }
th, td { border: 1px solid #2a303c; padding: 6px 8px; text-align: left; }
th { background: #1d222c; color: #d6b58c; font-weight: 600; }
tr:nth-child(even) { background: rgba(255,255,255,0.02); }
.tier { display: inline-block; padding: 1px 8px; border-radius: 3px; font-size: 11px; font-weight: 700; font-family: ui-monospace, monospace; }
.tier-1 { background: rgba(215,154,90,0.2); color: #d79a5a; border: 1px solid #d79a5a; }
.tier-2 { background: rgba(138,173,217,0.2); color: #8aadd9; border: 1px solid #8aadd9; }
.tier-3 { background: rgba(154,154,154,0.2); color: #9a9a9a; border: 1px solid #9a9a9a; }
.tier-x { background: #1d222c; color: #9aa3b2; border: 1px solid #2a303c; }
.budget { font-family: ui-monospace, monospace; font-weight: 700; color: #d6b58c; }
.matrix-cell { text-align: center; min-width: 70px; }
.matrix-cell.active { background: #d79a5a; color: #1a1a1a; font-weight: 700; }
.matrix-cell.tier-2.active { background: #8aadd9; }
.matrix-cell.tier-3.active { background: #9a9a9a; }
.reason { background: #161a21; border-left: 2px solid #c08a4a; padding: 6px 12px;
          margin: 4px 0; border-radius: 0 4px 4px 0; font-size: 13px; }
.reason b { color: #d6b58c; }

/* Gantt-style TRM visualization */
.gantt-wrap { margin: 16px 0; }
.gantt-header { display: grid; gap: 8px; padding: 6px 0; margin-left: 220px;
                border-bottom: 1px solid #2a303c; }
.gantt-header > div { text-align: center; font-family: ui-monospace, monospace;
                      font-size: 11px; color: #d6b58c; border-left: 1px dashed #2a303c;
                      padding: 4px 2px; }
.gantt-header > div:first-child { border-left: none; }
.gantt-row { display: grid; grid-template-columns: 220px 1fr 120px; gap: 10px;
             align-items: center; padding: 10px 0;
             border-bottom: 1px dashed #2a303c; }
.gantt-label .id { color: #c08a4a; font-family: ui-monospace, monospace; font-size: 11px; }
.gantt-label .name { font-weight: 600; font-size: 13px; }
.gantt-period { font-family: ui-monospace, monospace; font-size: 10px;
                color: #9aa3b2; margin-top: 2px; letter-spacing: 0.2px; }
.gantt-bar-q { font-size: 10px; font-weight: 400; opacity: 0.85; margin-left: 4px; }
.gantt-bar-wrap { position: relative; height: 24px; background: #0e1014;
                  border: 1px solid #2a303c; border-radius: 4px; overflow: hidden; }
.gantt-bar { position: absolute; top: 0; bottom: 0;
             background: linear-gradient(90deg, #c08a4a 0%, #d6b58c 100%);
             border-radius: 3px; display: flex; align-items: center;
             padding: 0 8px; font-size: 11px; color: #1a1a1a; font-weight: 700;
             font-family: ui-monospace, monospace; }
.gantt-bar.tier-2 { background: linear-gradient(90deg, #8aadd9 0%, #b0c5e0 100%); }
.gantt-bar.tier-3 { background: linear-gradient(90deg, #9a9a9a 0%, #c0c0c0 100%); }
.gantt-budget-col { text-align: center; font-family: ui-monospace, monospace;
                    font-size: 13px; font-weight: 700; color: #d6b58c;
                    padding: 4px 12px; border: 1px solid #c08a4a;
                    border-radius: 6px; background: rgba(215,154,90,0.08); }
.gantt-budget-col.tier-1 { border-color: #d79a5a; color: #d79a5a; background: rgba(215,154,90,0.12); }
.gantt-budget-col.tier-2 { border-color: #8aadd9; color: #8aadd9; background: rgba(138,173,217,0.12); }
.gantt-budget-col.tier-3 { border-color: #9a9a9a; color: #9a9a9a; background: rgba(154,154,154,0.12); }
.gantt-header.has-budget-col { margin-left: 0; gap: 10px; }
.gantt-header.has-budget-col > .gantt-budget-head {
  text-align: center; font-family: ui-monospace, monospace;
  font-size: 11px; color: #d6b58c; padding: 4px 12px;
  border-left: none; }
.tech-card { background: #161a21; border: 1px solid #2a303c; border-radius: 8px;
             padding: 12px 16px; margin: 10px 0; }
.tech-card .head { display: flex; justify-content: space-between; align-items: center; }
.tech-card h3 { margin: 0; }
.issue { background: rgba(224,133,133,0.1); border: 1px solid #e08585; border-radius: 4px;
         padding: 6px 10px; margin: 4px 0; }
.decision-accept { color: #7ec27e; font-weight: 700; }
.decision-revise { color: #e0b060; font-weight: 700; }
ul, ol { padding-left: 22px; }
.dim { color: #9aa3b2; font-size: 12px; }
"""


def generate_html_report(report_data: Dict[str, Any]) -> str:
    pf = report_data.get("problem_frame") or {}
    review = report_data.get("review") or {}
    report = review.get("report") or {}
    artifacts = report.get("artifacts_summary") or {}

    tech_candidates = report_data.get("tech_candidates") or artifacts.get("agent1_tech_candidates") or []
    planned_roadmap = report_data.get("planned_roadmap") or artifacts.get("agent2_planned_roadmap") or []
    investment_strategy = report_data.get("investment_strategy") or artifacts.get("agent3_investment_strategy") or []
    year_matrix = artifacts.get("year_tech_matrix") or {}

    company = _esc_html(pf.get("company_name") or "(unknown)")
    decision = (review.get("decision") or "?").upper()
    decision_cls = "decision-accept" if decision == "ACCEPT" else "decision-revise"

    parts = []
    parts.append(f"<!DOCTYPE html><html lang='ko'><head><meta charset='UTF-8'>")
    parts.append(f"<title>Tech Roadmap Report — {company}</title>")
    parts.append(f"<style>{_HTML_CSS}</style></head><body>")

    # Header
    parts.append(f"<h1>📋 기술 로드맵 보고서 — {company}</h1>")
    parts.append("<ul>")
    parts.append(f"<li><b>산업</b>: {_esc_html(pf.get('industry') or '-')}</li>")
    parts.append(f"<li><b>계획 기간</b>: {_esc_html(pf.get('time_horizon') or '-')}</li>")
    if pf.get("annual_revenue"):
        parts.append(f"<li><b>연 매출</b>: <span class='budget'>{_fmt_usd(pf.get('annual_revenue'))}</span></li>")
    if pf.get("annual_rd_budget"):
        parts.append(f"<li><b>연 R&amp;D 예산</b>: <span class='budget'>{_fmt_usd(pf.get('annual_rd_budget'))}</span> (비중 {_fmt_pct(pf.get('rd_budget_ratio'))})</li>")
    if pf.get("total_budget"):
        parts.append(f"<li><b>총 예산 (5년 envelope)</b>: <span class='budget'>{_fmt_usd(pf.get('total_budget'))}</span></li>")
    parts.append(f"<li><b>평가 결정</b>: <span class='{decision_cls}'>{decision}</span> (iter {report_data.get('iteration', '?')})</li>")
    parts.append("</ul>")

    direction = pf.get("strategic_direction") or []
    if direction:
        parts.append("<h3>🎯 Strategic Direction</h3><ol>")
        for d in direction:
            parts.append(f"<li>{_esc_html(d)}</li>")
        parts.append("</ol>")

    # Agent 1
    if tech_candidates:
        parts.append(f"<h2>🔬 Agent 1 · Technology Candidates ({len(tech_candidates)})</h2>")
        parts.append("<table><thead><tr>")
        for h in ["tech_id", "name", "category", "TRL", "final", "market", "patent", "boom"]:
            parts.append(f"<th>{h}</th>")
        parts.append("</tr></thead><tbody>")
        for t in tech_candidates:
            parts.append("<tr>")
            parts.append(f"<td><code>{_esc_html(t.get('tech_id', ''))}</code></td>")
            parts.append(f"<td>{_esc_html((t.get('name') or '')[:40])}</td>")
            parts.append(f"<td>{_esc_html(t.get('category', ''))}</td>")
            parts.append(f"<td>{_esc_html(t.get('trl', ''))}</td>")
            parts.append(f"<td>{_esc_html(t.get('final_score', ''))}</td>")
            parts.append(f"<td>{_esc_html(t.get('market_score', ''))}</td>")
            parts.append(f"<td>{_esc_html(t.get('patent_score', ''))}</td>")
            parts.append(f"<td>{_esc_html(t.get('expected_market_boom_quarter', ''))}</td>")
            parts.append("</tr>")
        parts.append("</tbody></table>")

    # Agent 2
    if planned_roadmap:
        parts.append(f"<h2>🛣️ Agent 2 · Planned Roadmap ({len(planned_roadmap)})</h2>")
        for r in planned_roadmap:
            tid = _esc_html(r.get("tech_id", ""))
            name = _esc_html(r.get("name", ""))
            reasoning = r.get("reasoning") or {}
            parts.append(f"<div class='tech-card'>")
            parts.append(f"<h3><code>{tid}</code> · {name}</h3>")
            parts.append(f"<div class='dim'>{r.get('year_idx_start', '?')}차년도 → {r.get('year_idx_target', '?')}차년도 · "
                         f"prereq: {_esc_html(', '.join(r.get('prerequisites') or []) or '(none)')}</div>")
            for key, label in [("year_placement", "📅 차년도 배치 이유"),
                               ("tech_execution", "🛠️ 기술 수행 이유"),
                               ("investment_selection", "🎯 투자 선정 이유")]:
                if reasoning.get(key):
                    parts.append(f"<div class='reason'><b>{label}:</b> {_esc_html(reasoning[key])}</div>")
            parts.append("</div>")

    # Agent 3
    all_invs = []
    for s in (investment_strategy or []):
        for ti in (s.get("tech_investments") or []):
            all_invs.append(ti)
    if all_invs:
        total_alloc = sum(float(ti.get("tech_budget_usd") or 0) for ti in all_invs)
        cap = pf.get("total_budget") or 0
        parts.append(f"<h2>💰 Agent 3 · Investment Strategy ({len(all_invs)} techs)</h2>")
        parts.append(f"<p><b>예산 한도</b>: <span class='budget'>{_fmt_usd(cap)}</span> · "
                     f"<b>배분 합</b>: <span class='budget'>{_fmt_usd(total_alloc)}</span>"
                     + (f" · 편차 {((total_alloc - cap) / cap * 100):.1f}%" if cap else "") + "</p>")
        for ti in all_invs:
            tid = _esc_html(ti.get("tech_id", ""))
            name = _esc_html(ti.get("name", ""))
            tier = ti.get("recommended_investment_tier", "") or ""
            tcls = _tier_class(tier)
            scores = ti.get("evaluation_scores") or {}
            reasoning = ti.get("reasoning") or {}
            parts.append(f"<div class='tech-card'>")
            parts.append(
                f"<div class='head'>"
                f"<h3><code>{tid}</code> · {name}</h3>"
                f"<span><span class='tier {tcls}'>{_esc_html(tier)}</span> "
                f"<span class='budget' style='margin-left:8px;'>{_fmt_usd(ti.get('tech_budget_usd'))}</span></span>"
                f"</div>"
            )
            parts.append(
                f"<div class='dim'>TAM={scores.get('market_size_growth', '?')} · "
                f"TRL={scores.get('tech_readiness', '?')} · "
                f"RISK={scores.get('tech_risk', '?')} · "
                f"COMP={scores.get('competitive_advantage', '?')} · "
                f"URG={scores.get('development_urgency', '?')}</div>"
            )
            if ti.get("recommended_action"):
                parts.append(f"<div class='reason'><b>권장 액션:</b> {_esc_html(ti['recommended_action'])}</div>")
            if ti.get("tech_budget_rationale"):
                parts.append(f"<div class='reason'><b>💵 예산 결정 근거:</b> {_esc_html(ti['tech_budget_rationale'])}</div>")
            for key, label in [("market_evaluation", "📊 시장 평가"),
                               ("tech_evaluation", "⚙️ 기술 평가"),
                               ("investment_decision", "💰 투자 결정")]:
                if reasoning.get(key):
                    parts.append(f"<div class='reason'><b>{label}:</b> {_esc_html(reasoning[key])}</div>")
            risks = ti.get("major_risks") or []
            if risks:
                parts.append("<div class='reason'><b>⚠️ 리스크:</b><ul>")
                for r in risks:
                    parts.append(f"<li>{_esc_html(r)}</li>")
                parts.append("</ul></div>")
            resources = ti.get("resource_focus") or []
            if resources:
                parts.append("<div class='reason'><b>🔧 자원 집중:</b><ul>")
                for r in resources:
                    parts.append(f"<li>{_esc_html(r)}</li>")
                parts.append("</ul></div>")
            parts.append("</div>")

    # ── Gantt 차트 (TRM 시각화) — 차년도 bar + 예산 badge + reasoning ──
    cells = year_matrix.get("cells") or {}
    if planned_roadmap and cells:
        max_year = year_matrix.get("max_year") or 5
        parts.append("<h2>📅 TRM Gantt — 차년도별 로드맵 + 예산</h2>")
        parts.append("<div class='gantt-wrap'>")
        # 헤더: [label spacer] [차년도 컬럼들] [예산 컬럼]
        parts.append(
            f"<div class='gantt-header has-budget-col' "
            f"style='grid-template-columns: 220px repeat({max_year}, 1fr) 120px;'>"
        )
        parts.append("<div></div>")  # 라벨 공간 spacer
        for y in range(1, max_year + 1):
            parts.append(f"<div>{y}차년도</div>")
        parts.append("<div class='gantt-budget-head'>예산</div>")
        parts.append("</div>")

        for r in planned_roadmap:
            tid = _esc_html(r.get("tech_id", ""))
            name = _esc_html(r.get("name", ""))
            ys = r.get("year_idx_start", 1) or 1
            yt = r.get("year_idx_target", ys) or ys
            inv = next((ti for ti in all_invs if ti.get("tech_id") == r.get("tech_id")), None)
            budget = inv.get("tech_budget_usd") if inv else 0
            tier = inv.get("recommended_investment_tier", "") if inv else ""
            tcls = _tier_class(tier)
            # bar 위치 계산 (%)
            left_pct = ((ys - 1) / max_year) * 100
            width_pct = max(3, ((yt - ys + 1) / max_year) * 100)
            # 행 (3 컬럼: label / bar / 예산)
            parts.append("<div class='gantt-row'>")
            parts.append(
                f"<div class='gantt-label'>"
                f"<div class='id'>{tid}</div>"
                f"<div class='name'>{name}</div>"
                f"</div>"
            )
            parts.append(
                f"<div class='gantt-bar-wrap'>"
                f"<div class='gantt-bar {tcls}' style='left:{left_pct:.2f}%;width:{width_pct:.2f}%;'>"
                f"{ys}차년도 → {yt}차년도"
                f"</div>"
                f"</div>"
            )
            parts.append(
                f"<div class='gantt-budget-col {tcls}'>{_fmt_usd(budget)}</div>"
            )
            parts.append("</div>")
        parts.append("</div>")  # gantt-wrap

    # Year × Tech matrix 표 형식 제거 — Gantt 차트 + 차년도별 활동 요약으로 대체
    if False and cells:
        max_year = year_matrix.get("max_year") or 5
        parts.append("<h2>📊 Year × Tech 매트릭스</h2>")
        parts.append("<table><thead><tr><th>기술</th>")
        for y in range(1, max_year + 1):
            parts.append(f"<th class='matrix-cell'>{y}차년도</th>")
        parts.append("</tr></thead><tbody>")
        for r in planned_roadmap:
            tid = r.get("tech_id", "")
            ys = r.get("year_idx_start", 1)
            yt = r.get("year_idx_target", ys)
            inv = next((ti for ti in all_invs if ti.get("tech_id") == tid), None)
            budget = inv.get("tech_budget_usd") if inv else 0
            tier = inv.get("recommended_investment_tier", "") if inv else ""
            tcls = _tier_class(tier)
            parts.append(f"<tr><td><code>{_esc_html(tid)}</code> <span class='tier {tcls}'>{_esc_html(tier)}</span></td>")
            for y in range(1, max_year + 1):
                if ys <= y <= yt:
                    content = _fmt_usd(budget) if y == ys else "■"
                    parts.append(f"<td class='matrix-cell {tcls} active'>{content}</td>")
                else:
                    parts.append("<td class='matrix-cell'></td>")
            parts.append("</tr>")
        parts.append("</tbody></table>")

    # ── 차년도별 활동 요약 ──
    if planned_roadmap:
        max_y = max((r.get("year_idx_target") or 0) for r in planned_roadmap) or 1
        parts.append(f"<h2>📅 차년도별 활동 요약 ({max_y}년 계획)</h2>")
        for y in range(1, max_y + 1):
            active_techs = [r for r in planned_roadmap
                            if (r.get("year_idx_start") or 1) <= y <= (r.get("year_idx_target") or 1)]
            if not active_techs:
                continue
            year_total = 0
            parts.append(f"<div class='tech-card'>")
            parts.append(f"<h3>{y}차년도</h3>")
            parts.append("<ul>")
            for r in active_techs:
                tid = _esc_html(r.get("tech_id", ""))
                name = _esc_html(r.get("name", ""))
                ys = r.get("year_idx_start", 1)
                yt = r.get("year_idx_target", ys)
                is_start = (y == ys)
                inv = next((ti for ti in all_invs if ti.get("tech_id") == r.get("tech_id")), None)
                budget = (inv.get("tech_budget_usd") if inv else 0) or 0
                tier = inv.get("recommended_investment_tier", "") if inv else ""
                tcls = _tier_class(tier)
                if is_start:
                    year_total += budget
                stage_label = "시작" if y == ys else ("완료" if y == yt else "진행")
                budget_str = f" — <span class='budget'>{_fmt_usd(budget)}</span>" if is_start else ""
                parts.append(
                    f"<li><code>{tid}</code> <b>{name}</b> "
                    f"<span class='tier {tcls}'>{_esc_html(tier)}</span> "
                    f"— {stage_label}{budget_str}"
                )
                if is_start and inv:
                    inv_reason = (inv.get("reasoning") or {}).get("investment_decision") or ""
                    tech_reason = (r.get("reasoning") or {}).get("tech_execution") or ""
                    why = tech_reason or inv_reason
                    if why:
                        parts.append(f"<div class='dim' style='margin-top:4px;'>수행 이유 / 효과: {_esc_html(why)}</div>")
                parts.append("</li>")
            parts.append("</ul>")
            parts.append(f"<p class='dim'><b>{y}차년도 시작 예산 합계:</b> <span class='budget'>{_fmt_usd(year_total)}</span></p>")
            parts.append("</div>")

    # Final Review
    parts.append("<h2>✅ Final Review</h2>")
    parts.append(f"<p><b>Decision:</b> <span class='{decision_cls}'>{decision}</span></p>")
    issues = review.get("issues") or []
    if issues:
        parts.append(f"<p><b>Issues ({len(issues)}):</b></p>")
        for it in issues:
            if isinstance(it, dict):
                parts.append(f"<div class='issue'><code>[{_esc_html(it.get('axis', '?'))}]</code> {_esc_html(it.get('text', ''))}</div>")
            else:
                parts.append(f"<div class='issue'>{_esc_html(it)}</div>")
    if review.get("diagnostic_summary"):
        parts.append(f"<blockquote>{_esc_html(review['diagnostic_summary'])}</blockquote>")

    # Narrative 섹션 제거 — 각 에이전트의 raw 출력만 표시

    parts.append("</body></html>")
    return "".join(parts)


# PDF export 는 제거 — 필요 시 브라우저에서 HTML 열고 "PDF 로 인쇄" 사용
