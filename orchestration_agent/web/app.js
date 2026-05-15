// ── State ─────────────────────────────────────────────────────
let sessionId = null;
let eventSource = null;
let currentStepBlock = null;

// 누적되는 데이터 (I/O 카드 렌더링용)
const ctx = {
  intake: null,              // {domain, reference_year, category_hints, ...}
  activeAgents: [],
  problemFrame: null,
  market_context: null,
  tech_candidates: null,
  planned_roadmap: null,
  stages: null,
  investment_strategy: null,
  iterations: { agent1: 0, agent2: 0, agent3: 0, review: 0 },
};

const $ = (sel) => document.querySelector(sel);
const messages = () => $("#messages");
const panel = () => $("#panel");

// ── Boot ──────────────────────────────────────────────────────
(async function init() {
  try {
    const r = await fetch("/api/status");
    const s = await r.json();
    $("#llm-badge").textContent = "LLM: " + s.llm;
  } catch (e) {
    $("#llm-badge").textContent = "LLM: (status unavailable)";
  }
})();

$("#send-btn").addEventListener("click", onSend);
$("#input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) onSend();
});

// Investment Policy 는 자연어 텍스트로 받아서 서버에서 LLM 추출 (예전 라디오/드롭다운 제거됨)

// ── Actions ───────────────────────────────────────────────────
async function onSend() {
  const ta = $("#input");
  const text = ta.value.trim();
  if (!text) return;
  if (eventSource) { eventSource.close(); eventSource = null; }

  // active_agents 수집
  const activeAgents = [];
  if ($("#agent1").checked) activeAgents.push("1");
  if ($("#agent2").checked) activeAgents.push("2");
  if ($("#agent3").checked) activeAgents.push("3");
  if (activeAgents.length === 0) {
    alert("최소 하나의 Agent 는 켜주세요.");
    return;
  }

  // Investment Policy 는 메인 textarea 안에 자연어로 함께 들어옴 (별도 수집 X)

  ta.value = "";
  $("#send-btn").disabled = true;
  resetCtx();

  addUserMessage(text);
  clearPanel();

  // 세션 생성
  let sid;
  try {
    const payload = { request: text, active_agents: activeAgents };
    // 메인 텍스트가 정책 정보를 함께 담고 있으므로 그대로 전달 (서버에서 LLM 으로 추출)
    if (text) payload.investment_policy_text = text;

    const resp = await fetch("/api/session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: "세션 생성 실패" }));
      addAgentMessage("❌ " + (err.detail || "세션 생성 실패"));
      $("#send-btn").disabled = false;
      return;
    }
    const data = await resp.json();
    sid = data.session_id;
    sessionId = sid;
    ctx.activeAgents = data.active_agents;
    renderAgentsBar();
  } catch (e) {
    addAgentMessage("❌ 서버 연결 실패: " + e.message);
    $("#send-btn").disabled = false;
    return;
  }

  connectStream(sid);
}

function resetCtx() {
  Object.assign(ctx, {
    intake: null, activeAgents: [], problemFrame: null,
    market_context: null, tech_candidates: null,
    planned_roadmap: null, stages: null, investment_strategy: null,
    iterations: { agent1: 0, agent2: 0, agent3: 0, review: 0 },
  });
}

function connectStream(sid) {
  eventSource = new EventSource(`/api/stream/${sid}`);

  const handlers = {
    session_started: (d) => addAgentMessage(`🔌 세션 시작 · ${d.payload.llm}`),
    step_start: (d) => beginStep(d.payload.step, d.payload.label),
    step_end:   (d) => endStep(d.payload.step, d.payload),
    log:        (d) => appendLog(d.payload.message, d.payload.source),

    intake_ready: (d) => {
      ctx.intake = d.payload;
      ctx.activeAgents = d.payload.active_agents || ctx.activeAgents;
      renderAgentsBar();

      const p = d.payload;
      // Company Scenario (새 형식 — 위에 표시)
      const cs = p.company_scenario || {};
      const companyLines = [];
      if (cs.company_name && cs.company_name !== "(unknown)") companyLines.push(`• Company: <b>${escapeHtml(cs.company_name)}</b>`);
      if (cs.industry && cs.industry !== "(unknown)") companyLines.push(`• Industry: ${escapeHtml(cs.industry)}`);
      else if (p.industry) companyLines.push(`• Industry: ${escapeHtml(p.industry)}`);
      if (cs.annual_revenue) companyLines.push(`• Annual Revenue: <b>$${Number(cs.annual_revenue).toLocaleString()}</b>`);
      if (cs.rd_budget_ratio) companyLines.push(`• R&D Budget Ratio: <b>${(Number(cs.rd_budget_ratio) * 100).toFixed(0)}%</b>`);
      if (cs.annual_rd_budget) companyLines.push(`• Annual R&D Budget: <b>$${Number(cs.annual_rd_budget).toLocaleString()}</b>`);
      if (cs.planning_horizon && cs.planning_horizon !== "(unknown)") companyLines.push(`• Planning Horizon: ${escapeHtml(cs.planning_horizon)}`);
      else if (p.time_horizon) companyLines.push(`• Planning Horizon: ${escapeHtml(p.time_horizon)}`);

      // Strategic Direction (LLM 생성)
      const direction = p.strategic_direction || [];
      const directionHtml = direction.length
        ? direction.map((d, i) => `  ${i + 1}. ${escapeHtml(d)}`).join("<br/>")
        : "";

      // 분석 컨텍스트 (Tech 측 추출 결과)
      const techLines = [
        `• 도메인: ${escapeHtml(p.domain || "-")}`,
        `• 기준연도: ${p.reference_year || "-"}`,
        `• 카테고리: ${(p.category_hints || []).join(", ") || "-"}`,
      ];
      if (p.objective) techLines.push(`• 목표: ${escapeHtml(p.objective)}`);

      let html = `📥 <b>입력 해석 완료</b><br/>`;
      if (companyLines.length) {
        html += `<b>🏢 Company Scenario</b><br/>` + companyLines.join("<br/>");
      }
      if (directionHtml) {
        html += `<br/><br/><b>🎯 Strategic Direction</b><br/>` + directionHtml;
      }
      html += `<br/><br/><b>🔍 분석 컨텍스트</b><br/>` + techLines.join("<br/>");
      addAgentMessage(html, true);
    },

    setup_done: (d) => {
      ctx.problemFrame = d.payload.problem_frame;
      ctx.activeAgents = d.payload.active_agents || ctx.activeAgents;
      renderProblemFrame();
      renderAgentsBar();
    },

    candidates_ready: (d) => {
      ctx.iterations.agent1++;
      ctx.tech_candidates = d.payload.candidates;
      ctx.market_context = d.payload.market_context;
      renderAgent1Section();
    },

    roadmap_ready: (d) => {
      ctx.iterations.agent2++;
      ctx.planned_roadmap = d.payload.planned_roadmap;
      renderAgent2Section();
    },

    strategy_ready: (d) => {
      ctx.iterations.agent3++;
      ctx.stages = d.payload.stages;
      ctx.investment_strategy = d.payload.investment_strategy;
      renderAgent3Section();
      // Agent 3 결과로 Agent 2 간트도 다시 그림 (예산 badge 추가 표시)
      renderAgent2Section();
    },

    review_done: (d) => {
      ctx.iterations.review++;
      renderReviewSection(d.payload.review, d.payload.iteration);
    },

    refine: (d) => {
      const msg = `↻ REVISE → 재실행: ${(d.payload.rerun_agents || []).join(", ")}`;
      addAgentMessage(msg);
      appendRefineBanner(d.payload.rerun_agents, d.payload.feedback);
    },

    final: (d) => {
      // 마지막 요약
      const r = d.payload.result || {};
      addAgentMessage(`✅ 파이프라인 완료 — ${r?.review?.decision || "?"} (iter=${r.iteration}, 후보 ${r.counts?.tech_candidates}, 로드맵 ${r.counts?.planned_roadmap}, stage ${r.counts?.stages})`);
    },

    error: (d) => addAgentMessage("❌ 오류: " + escapeHtml(d.payload.message)),

    done: (d) => {
      if (!d.payload.ok) addAgentMessage("⚠️ 파이프라인 종료 (에러)");
      eventSource.close();
      eventSource = null;
      $("#send-btn").disabled = false;
    },
    ping: () => {},
  };

  ["session_started","step_start","step_end","log","intake_ready",
   "setup_done","candidates_ready","roadmap_ready","strategy_ready",
   "review_done","refine","final","error","done","ping"].forEach((t) => {
    eventSource.addEventListener(t, (ev) => {
      try {
        const d = JSON.parse(ev.data);
        handlers[t] && handlers[t](d);
      } catch (e) { console.warn("bad event", t, e); }
    });
  });

  eventSource.onerror = () => {
    if (eventSource) { eventSource.close(); eventSource = null; }
    $("#send-btn").disabled = false;
  };
}

// ── Message helpers (tech_analysis_agent 와 동일) ────────────
function addUserMessage(text) {
  const el = document.createElement("div");
  el.className = "msg user";
  el.innerHTML = `<div class="bubble">${escapeHtml(text)}</div>`;
  messages().appendChild(el);
  scrollBottom();
}
function addAgentMessage(html, rawHtml = false) {
  const el = document.createElement("div");
  el.className = "msg agent";
  el.innerHTML = `<div class="bubble">${rawHtml ? html : escapeHtml(html)}</div>`;
  messages().appendChild(el);
  scrollBottom();
}
const SPINNER_FRAMES = ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"];
const _stepTimers = new Map();

function beginStep(step, label) {
  const off = /\(OFF\)\s*$/.test(label || "");
  const el = document.createElement("div");
  el.className = "msg agent";
  el.dataset.step = step;
  el.innerHTML = `
    <div class="bubble" style="width:95%;">
      <div class="step-header ${off ? "off" : ""}">
        <span class="dot"></span>
        <span class="title-text">${escapeHtml(label || step)}</span>
        <span class="meta">
          <span class="spinner">${off ? "–" : "⠋"}</span>
          <span class="elapsed">0.0s</span>
          <span class="status">${off ? "<span style='color:var(--text-dim)'>비활성화</span>" : "<span class='thinking'>실행 중<span>.</span><span>.</span><span>.</span></span>"}</span>
        </span>
      </div>
      <div class="log-block"></div>
    </div>`;
  messages().appendChild(el);
  currentStepBlock = el;
  scrollBottom();

  if (off) {
    _stepTimers.set(step, { interval: null, startedAt: performance.now(), el });
    return;
  }

  const startedAt = performance.now();
  const spinnerEl = el.querySelector(".spinner");
  const elapsedEl = el.querySelector(".elapsed");
  let frame = 0;
  const interval = setInterval(() => {
    frame = (frame + 1) % SPINNER_FRAMES.length;
    spinnerEl.textContent = SPINNER_FRAMES[frame];
    const secs = (performance.now() - startedAt) / 1000;
    elapsedEl.textContent = secs < 10 ? secs.toFixed(1) + "s" : Math.floor(secs) + "s";
  }, 90);
  _stepTimers.set(step, { interval, startedAt, el });
}

function endStep(step, payload) {
  const t = _stepTimers.get(step);
  const el = t?.el || [...document.querySelectorAll(`.msg.agent[data-step="${step}"]`)].pop();
  if (!el) return;
  if (t?.interval) clearInterval(t.interval);
  const hdr = el.querySelector(".step-header");
  if (!hdr.classList.contains("off")) hdr.classList.add("done");
  const secs = t ? ((performance.now() - t.startedAt) / 1000) : 0;
  const elapsedStr = secs < 10 ? secs.toFixed(1) + "s" : Math.floor(secs) + "s";
  hdr.querySelector(".spinner").textContent = hdr.classList.contains("off") ? "–" : "✓";
  hdr.querySelector(".elapsed").textContent = elapsedStr;
  const status = hdr.querySelector(".status");
  const extra = payload && payload.count != null ? `완료 · ${payload.count}건` : "완료";
  if (!hdr.classList.contains("off")) {
    status.innerHTML = `<span style="color:var(--success);">${escapeHtml(extra)}</span>`;
  }
  _stepTimers.delete(step);
  if (currentStepBlock === el) currentStepBlock = null;
}
function appendLog(line, source) {
  const target = currentStepBlock
    ? currentStepBlock.querySelector(".log-block")
    : ensureFloatingLog();
  const row = document.createElement("div");
  row.className = "log-line highlight";
  if (/Agent|Orchestrator|Pipeline/.test(line)) row.classList.add("step");
  if (/오류|error|❌|Traceback/.test(line)) row.classList.add("error");
  row.textContent = line;
  target.appendChild(row);
  target.scrollTop = target.scrollHeight;
  setTimeout(() => row.classList.remove("highlight"), 900);
}
function ensureFloatingLog() {
  let last = document.querySelector(".messages .log-block.floating");
  if (last) return last;
  const el = document.createElement("div");
  el.className = "msg agent";
  el.innerHTML = `<div class="bubble" style="width:95%;"><div class="log-block floating"></div></div>`;
  messages().appendChild(el);
  return el.querySelector(".log-block");
}
function scrollBottom() {
  const m = messages();
  m.scrollTop = m.scrollHeight;
}

// ── Panel ──────────────────────────────────────────────────────
function clearPanel() {
  panel().innerHTML = "";
}

function renderAgentsBar() {
  let sec = panel().querySelector(".agents-bar");
  if (!sec) {
    sec = document.createElement("div");
    sec.className = "panel-section agents-bar";
    sec.innerHTML = `
      <h3>Active Agents</h3>
      <div class="agent-status-grid">
        <div class="agent-status" data-agent="1"><div class="num">Agent 1</div><div class="nm">Tech Analyst</div></div>
        <div class="agent-status" data-agent="2"><div class="num">Agent 2</div><div class="nm">Roadmap Planner</div></div>
        <div class="agent-status" data-agent="3"><div class="num">Agent 3</div><div class="nm">Investment Strategist</div></div>
      </div>`;
    panel().appendChild(sec);
  }
  ["1","2","3"].forEach((k) => {
    const cell = sec.querySelector(`[data-agent="${k}"]`);
    cell.classList.toggle("on",  ctx.activeAgents.includes(k));
    cell.classList.toggle("off", !ctx.activeAgents.includes(k));
  });
}

function renderProblemFrame() {
  const pf = ctx.problemFrame;
  if (!pf) return;
  let sec = panel().querySelector(".pf-sec");
  if (!sec) {
    sec = document.createElement("div");
    sec.className = "panel-section pf-sec";
    panel().appendChild(sec);
  }
  const fmtUsd = (v) => v ? "$" + Number(v).toLocaleString() : "-";
  const fmtPct = (v) => v ? (Number(v) * 100).toFixed(0) + "%" : "-";
  const direction = pf.strategic_direction || [];
  const directionHtml = direction.length
    ? `<ol class="pf-direction">${direction.map(d => `<li>${escapeHtml(d)}</li>`).join("")}</ol>`
    : `<span class="v">-</span>`;

  sec.innerHTML = `
    <h3>Problem Frame (Orchestrator Setup)</h3>
    <div class="problem-frame">
      <div class="pf-row"><span class="k">Company</span><span class="v">${escapeHtml(pf.company_name || "-")}</span></div>
      <div class="pf-row"><span class="k">Industry</span><span class="v">${escapeHtml(pf.industry || "-")}</span></div>
      <div class="pf-row"><span class="k">Annual Revenue</span><span class="v">${fmtUsd(pf.annual_revenue)}</span></div>
      <div class="pf-row"><span class="k">R&D Budget Ratio</span><span class="v">${fmtPct(pf.rd_budget_ratio)}</span></div>
      <div class="pf-row"><span class="k">Annual R&D Budget</span><span class="v">${fmtUsd(pf.annual_rd_budget)}</span></div>
      <div class="pf-row"><span class="k">Planning Horizon</span><span class="v">${escapeHtml(pf.time_horizon || "-")}</span></div>
      <div class="pf-row pf-row-block">
        <span class="k">Strategic Direction</span>
        <div class="v">${directionHtml}</div>
      </div>
    </div>`;
}

// ── Agent 1 section (I/O + Candidates) ───────────────────────
function renderAgent1Section() {
  const tc = ctx.tech_candidates || [];
  const mc = ctx.market_context || {};
  const iter = ctx.iterations.agent1 > 1 ? ` (iter ${ctx.iterations.agent1})` : "";

  let sec = panel().querySelector(".a1-sec");
  if (!sec) {
    sec = document.createElement("div");
    sec.className = "panel-section a1-sec";
    panel().appendChild(sec);
  }

  const preview = tc.slice(0, 5).map(c =>
    `<li>${escapeHtml(c.tech_id)} · ${escapeHtml(c.name || "")} (TRL ${c.trl ?? "?"}, score ${(c.final_score ?? 0).toFixed(1)})</li>`
  ).join("");

  sec.innerHTML = `
    <h3>Agent 1 · Technology Analyst${iter}</h3>
    <div class="io-card">
      <div class="io-title">Tech Candidates <span class="arrow">→</span> Agent 2 <span class="badge">${tc.length}건</span></div>
      <div class="io-row"><span class="tag in">IN</span>
        <div class="content">
          <span class="k">domain</span> <span class="v">${escapeHtml(ctx.intake?.domain || "-")}</span> ·
          <span class="k">year</span> <span class="v">${ctx.intake?.reference_year ?? "-"}</span> ·
          <span class="k">categories</span> <span class="v">${escapeHtml((ctx.intake?.category_hints || []).join(", "))}</span>
        </div>
      </div>
      <div class="io-row"><span class="tag out">OUT</span>
        <div class="content">
          <span class="k">tech_candidates</span> <span class="v">${tc.length}건</span>
          ${mc.target_market ? ` · <span class="k">target_market</span> <span class="v">${escapeHtml(mc.target_market)}</span>` : ""}
          ${mc.expected_boom_quarter ? ` · <span class="k">boom</span> <span class="v">${escapeHtml(mc.expected_boom_quarter)}</span>` : ""}
          ${preview ? `<ul class="preview-list">${preview}${tc.length > 5 ? `<li>… ${tc.length - 5} more</li>` : ""}</ul>` : ""}
        </div>
      </div>
    </div>
    <div class="a1-candidates"></div>`;

  const list = sec.querySelector(".a1-candidates");
  tc.forEach((c) => list.appendChild(makeCandidateCard(c)));
}

function makeCandidateCard(c) {
  const el = document.createElement("div");
  el.className = "candidate";
  el.dataset.tid = c.tech_id;
  el.innerHTML = `
    <div class="candidate-header">
      <div><span class="candidate-id">${escapeHtml(c.tech_id)}</span> · <span class="candidate-title">${escapeHtml(c.name || "")}</span></div>
      <div>
        <span class="badge">${escapeHtml(c.category || "")}</span>
        <span class="badge">TRL ${c.trl ?? "?"}</span>
        <span class="badge score">${(c.final_score ?? 0).toFixed(1)}</span>
      </div>
    </div>
    <div class="candidate-meta">
      <span>특허 <b>${(c.patent_score ?? 0).toFixed(1)}</b></span>
      <span>시장 <b>${(c.market_score ?? 0).toFixed(1)}</b></span>
      <span>개화 <b>${escapeHtml(c.expected_market_boom_quarter || "N/A")}</b></span>
    </div>
    <div class="candidate-rationale">${escapeHtml(c.rationale || "")}</div>`;
  return el;
}

// ── Agent 2 section (I/O + Timeline) ──────────────────────────
function renderAgent2Section() {
  const rm = ctx.planned_roadmap || [];
  const iter = ctx.iterations.agent2 > 1 ? ` (iter ${ctx.iterations.agent2})` : "";

  let sec = panel().querySelector(".a2-sec");
  if (!sec) {
    sec = document.createElement("div");
    sec.className = "panel-section a2-sec";
    panel().appendChild(sec);
  }

  // 차년도 단위 (1차년도, 2차년도, ...) — year_idx_start / year_idx_target 사용
  // 분기 단위는 fallback (옛 데이터 호환)
  const toIdx = (q) => { const m = /^(\d{4})\s*Q([1-4])$/.exec((q||"").trim()); return m ? parseInt(m[1]) * 4 + (parseInt(m[2]) - 1) : null; };
  const fromIdx = (i) => `${Math.floor(i / 4)} Q${(i % 4) + 1}`;

  // year_idx 범위 산정
  let minY = Infinity, maxY = -Infinity;
  rm.forEach((it) => {
    const ys = it.year_idx_start, yt = it.year_idx_target;
    if (typeof ys === "number" && ys > 0) minY = Math.min(minY, ys);
    if (typeof yt === "number" && yt > 0) maxY = Math.max(maxY, yt);
  });
  if (!isFinite(minY)) { minY = 1; maxY = 5; }
  const totalYears = Math.max(1, maxY - minY + 1);

  // 분기 범위 (보조 표시용)
  let minI = Infinity, maxI = -Infinity;
  rm.forEach((it) => { const a = toIdx(it.start_q), b = toIdx(it.target_q); if (a != null) minI = Math.min(minI, a); if (b != null) maxI = Math.max(maxI, b); });
  if (!isFinite(minI)) { minI = 0; maxI = 3; }

  const preview = rm.slice(0, 4).map(r =>
    `<li>${escapeHtml(r.tech_id)} · ${escapeHtml(r.phase_name || "")} — ${r.year_idx_start || "?"}차년도 → ${r.year_idx_target || "?"}차년도</li>`
  ).join("");

  // 차년도 헤더 (1차년도, 2차년도, ...)
  const yearHeaders = [];
  for (let y = minY; y <= maxY; y++) yearHeaders.push(`<div class="year-col-label">${y}차년도</div>`);

  sec.innerHTML = `
    <h3>Agent 2 · Roadmap Planner${iter}</h3>
    <div class="io-card">
      <div class="io-title">Planned Roadmap <span class="arrow">→</span> Agent 3 <span class="badge">${rm.length}건</span></div>
      <div class="io-row"><span class="tag in">IN</span>
        <div class="content">
          <span class="k">tech_candidates</span> <span class="v">${(ctx.tech_candidates || []).length}건</span> ·
          <span class="k">market_context.expected_boom</span> <span class="v">${escapeHtml(ctx.market_context?.expected_boom_quarter || "-")}</span>
        </div>
      </div>
      <div class="io-row"><span class="tag out">OUT</span>
        <div class="content">
          <span class="k">planned_roadmap</span> <span class="v">${rm.length}건</span> ·
          <span class="k">range</span> <span class="v">${minY}차년도 → ${maxY}차년도</span>
          ${preview ? `<ul class="preview-list">${preview}${rm.length > 4 ? `<li>… ${rm.length - 4} more</li>` : ""}</ul>` : ""}
        </div>
      </div>
    </div>
    <div class="a2-year-header">
      <div class="timeline-label-spacer"></div>
      <div class="year-col-row">${yearHeaders.join("")}</div>
    </div>
    <div class="a2-timeline"></div>`;

  // Agent 3 가 끝났으면 tech_id → {budget, tier, reasoning} 매핑 빌드
  const invByTech = {};
  (ctx.investment_strategy || []).forEach(s => {
    (s.tech_investments || []).forEach(t => { if (t.tech_id) invByTech[t.tech_id] = t; });
  });
  const fmtUsd = (v) => {
    const n = Number(v) || 0;
    if (n >= 1e9) return `$${(n / 1e9).toFixed(2)}B`;
    if (n >= 1e6) return `$${(n / 1e6).toFixed(0)}M`;
    return `$${n.toLocaleString()}`;
  };

  const tl = sec.querySelector(".a2-timeline");
  rm.forEach((it) => {
    const ys = (typeof it.year_idx_start === "number" && it.year_idx_start > 0) ? it.year_idx_start : minY;
    const yt = (typeof it.year_idx_target === "number" && it.year_idx_target > 0) ? it.year_idx_target : ys;
    const left = ((ys - minY) / totalYears) * 100;
    const width = Math.max(3, ((yt - ys + 1) / totalYears) * 100);

    const reasoning = it.reasoning || {};
    const inv = invByTech[it.tech_id];
    const invReasoning = (inv && inv.reasoning) || {};

    // Designer reasoning + Strategist reasoning 둘 다 표시
    const reasoningParts = [];
    if (reasoning.year_placement) reasoningParts.push(`<div class="reason-block"><b>📅 차년도 배치 이유:</b><br/>${escapeHtml(reasoning.year_placement)}</div>`);
    if (reasoning.tech_execution) reasoningParts.push(`<div class="reason-block"><b>🛠️ 기술 수행 이유:</b><br/>${escapeHtml(reasoning.tech_execution)}</div>`);
    if (reasoning.investment_selection) reasoningParts.push(`<div class="reason-block"><b>🎯 투자 선정 이유 (Designer):</b><br/>${escapeHtml(reasoning.investment_selection)}</div>`);
    if (invReasoning.market_evaluation) reasoningParts.push(`<div class="reason-block strategist"><b>📊 시장 평가 (Strategist):</b><br/>${escapeHtml(invReasoning.market_evaluation)}</div>`);
    if (invReasoning.tech_evaluation) reasoningParts.push(`<div class="reason-block strategist"><b>⚙️ 기술 평가 (Strategist):</b><br/>${escapeHtml(invReasoning.tech_evaluation)}</div>`);
    if (invReasoning.investment_decision) reasoningParts.push(`<div class="reason-block strategist"><b>💰 투자 결정 (Strategist):</b><br/>${escapeHtml(invReasoning.investment_decision)}</div>`);
    if (inv && inv.tech_budget_rationale) reasoningParts.push(`<div class="reason-block strategist"><b>💵 예산 결정 근거:</b><br/>${escapeHtml(inv.tech_budget_rationale)}</div>`);

    const reasoningHtml = reasoningParts.length
      ? `<details class="reasoning-box"><summary>📋 Reasoning</summary>${reasoningParts.join("")}</details>`
      : "";

    // 예산 badge — Agent 3 결과 있으면 표시
    const budgetBadge = inv && inv.tech_budget_usd
      ? `<span class="budget-badge tier-${(inv.recommended_investment_tier || "").replace(/[^0-9]/g, "")}" title="${escapeHtml(inv.recommended_investment_tier || "")}">${fmtUsd(inv.tech_budget_usd)}</span>`
      : "";

    const wrapper = document.createElement("div");
    wrapper.className = "a2-row";
    wrapper.innerHTML = `
      <div class="timeline-row">
        <div class="timeline-label">
          <div><span class="id">${escapeHtml(it.tech_id)}</span></div>
          <div class="name">${escapeHtml(it.name || "")}</div>
        </div>
        <div class="timeline-bar-wrap a2-bar-wrap" style="--cols:${totalYears};">
          <div class="timeline-bar" style="left:${left}%;width:${width}%;">
            ${ys}차년도 → ${yt}차년도
          </div>
          ${budgetBadge ? `<div class="budget-overlay">${budgetBadge}</div>` : ""}
        </div>
      </div>
      ${reasoningHtml}`;
    tl.appendChild(wrapper);
  });
}

// ── Agent 3 section (I/O + Stage cards) ───────────────────────
function renderAgent3Section() {
  const strategy = ctx.investment_strategy || [];
  const iter = ctx.iterations.agent3 > 1 ? ` (iter ${ctx.iterations.agent3})` : "";

  let sec = panel().querySelector(".a3-sec");
  if (!sec) {
    sec = document.createElement("div");
    sec.className = "panel-section a3-sec";
    panel().appendChild(sec);
  }

  // 새 구조: stage 그룹핑 제거. 모든 tech_investments 를 flat 으로 펼침.
  const allTechs = [];
  strategy.forEach(s => (s.tech_investments || []).forEach(ti => allTechs.push(ti)));

  // Tier 분포
  const tierCounts = { "Tier 1": 0, "Tier 2": 0, "Tier 3": 0 };
  allTechs.forEach(ti => {
    const t = ti.recommended_investment_tier || "Tier 2";
    if (tierCounts[t] !== undefined) tierCounts[t]++;
  });

  // 전체 예산 — Problem Frame 의 total_budget (예산 한도) + 실제 배분 합
  const totalBudgetCap = Number(ctx.problemFrame?.total_budget) || 0;
  const sumAllocated = allTechs.reduce((sum, ti) => sum + (Number(ti.tech_budget_usd) || 0), 0);
  const totalBudget = totalBudgetCap > 0 ? totalBudgetCap : sumAllocated;  // makeTechInvestmentCard 의 % 계산용
  const fmtUsd = (n) => {
    if (!n || isNaN(n)) return "-";
    if (n >= 1e9) return `$${(n / 1e9).toFixed(2)}B`;
    if (n >= 1e6) return `$${(n / 1e6).toFixed(0)}M`;
    return `$${Math.round(n).toLocaleString()}`;
  };

  const preview = allTechs.slice(0, 3).map(ti =>
    `<li>${escapeHtml(ti.tech_id || "")} · <b>${escapeHtml(ti.recommended_investment_tier || "-")}</b> — ${fmtUsd(ti.tech_budget_usd)}</li>`
  ).join("");

  sec.innerHTML = `
    <h3>Agent 3 · Investment Strategist${iter}</h3>
    <div class="io-card">
      <div class="io-title">Investment Strategy <span class="arrow">→</span> Orchestrator <span class="badge">${allTechs.length} techs</span></div>
      <div class="io-row"><span class="tag in">IN</span>
        <div class="content">
          <span class="k">planned_roadmap</span> <span class="v">${(ctx.planned_roadmap || []).length}건</span> ·
          <span class="k">tech_candidates</span> <span class="v">${(ctx.tech_candidates || []).length}건</span>
        </div>
      </div>
      <div class="io-row"><span class="tag out">OUT</span>
        <div class="content">
          <span class="k">tier 분포</span> <span class="v">T1=${tierCounts["Tier 1"]} · T2=${tierCounts["Tier 2"]} · T3=${tierCounts["Tier 3"]}</span> ·
          <span class="k">예산 한도</span> <span class="v">${fmtUsd(totalBudgetCap)}</span> ·
          <span class="k">배분 합</span> <span class="v">${fmtUsd(sumAllocated)}</span>
          ${preview ? `<ul class="preview-list">${preview}</ul>` : ""}
        </div>
      </div>
    </div>
    <div class="a3-techs"></div>`;

  const wrap = sec.querySelector(".a3-techs");
  allTechs.forEach(ti => wrap.appendChild(makeTechInvestmentCard(ti, totalBudget)));
}

function makeTechInvestmentCard(ti, totalBudget) {
  const tier = (ti.recommended_investment_tier || "").replace(/\s+/g, "").toLowerCase();
  const tierClass = tier === "tier1" ? "t1" : tier === "tier2" ? "t2" : "t3";
  const es = ti.evaluation_scores || {};
  const scoreKeys = [
    ["market_size_growth",   "TAM",  "market_opportunity"],
    ["tech_readiness",       "TRL",  "executability"],
    ["tech_risk",            "RISK", "uncertainty"],
    ["competitive_advantage","COMP", "strategic_fit"],
    ["development_urgency",  "URG",  "urgency"],
  ];
  const bars = scoreKeys.map(([k, short, oldK]) => {
    const v = es[k] ?? es[oldK] ?? 0;
    const pct = Math.max(0, Math.min(100, (v / 5) * 100));
    return `<div class="score-item">
      <div class="score-label">${short}</div>
      <div class="score-value">${v}</div>
      <div class="score-bar"><span style="width:${pct}%"></span></div>
    </div>`;
  }).join("");

  const fmtUsd = (n) => {
    if (!n || isNaN(n)) return "-";
    if (n >= 1e9) return `$${(n / 1e9).toFixed(2)}B`;
    if (n >= 1e6) return `$${(n / 1e6).toFixed(0)}M`;
    return `$${Math.round(n).toLocaleString()}`;
  };
  const budget = Number(ti.tech_budget_usd) || 0;
  const budgetPct = (totalBudget > 0 && budget > 0) ? ` (${(budget / totalBudget * 100).toFixed(1)}%)` : "";

  const listBlock = (title, items) => {
    if (!items || !items.length) return "";
    return `<div class="stage-small-list"><b>${title}</b>
      <ul>${items.map(i => `<li>${escapeHtml(i)}</li>`).join("")}</ul></div>`;
  };

  // 새 reasoning 3분리 (있을 때만)
  const r = ti.reasoning || {};
  const bulletsHtml = (items) =>
    `<ul style="margin:4px 0 0 16px;padding:0;font-size:11.5px;color:var(--text);line-height:1.55;">${items.map(i => `<li>${escapeHtml(i)}</li>`).join("")}</ul>`;
  const reasoningBlocks = [];
  if (r.market_evaluation) reasoningBlocks.push(`<div class="reason-block strategist"><b>📊 시장 평가:</b><br/>${escapeHtml(r.market_evaluation)}</div>`);
  if (r.tech_evaluation) reasoningBlocks.push(`<div class="reason-block strategist"><b>⚙️ 기술 평가:</b><br/>${escapeHtml(r.tech_evaluation)}</div>`);
  if (r.investment_decision) reasoningBlocks.push(`<div class="reason-block strategist"><b>💰 투자 결정:</b><br/>${escapeHtml(r.investment_decision)}</div>`);
  if (ti.tech_budget_rationale) reasoningBlocks.push(`<div class="reason-block strategist"><b>💵 예산 결정 근거:</b><br/>${escapeHtml(ti.tech_budget_rationale)}</div>`);
  if (ti.major_risks && ti.major_risks.length) reasoningBlocks.push(`<div class="reason-block strategist"><b>⚠️ 리스크:</b>${bulletsHtml(ti.major_risks)}</div>`);
  if (ti.resource_focus && ti.resource_focus.length) reasoningBlocks.push(`<div class="reason-block strategist"><b>🔧 자원 집중:</b>${bulletsHtml(ti.resource_focus)}</div>`);
  const reasoningHtml = reasoningBlocks.length
    ? `<details class="reasoning-box" open><summary>📋 Reasoning</summary>${reasoningBlocks.join("")}</details>`
    : "";

  const el = document.createElement("div");
  el.className = "tech-inv-card stage-card";
  el.innerHTML = `
    <div class="stage-card-header">
      <div>
        <div class="stage-name">${escapeHtml(ti.tech_id || "")} · ${escapeHtml(ti.name || "")}</div>
        <div class="stage-period">attract=<b>${escapeHtml(ti.investment_attractiveness || "-")}</b> · urgency=<b>${escapeHtml(ti.investment_urgency || "-")}</b> · scope=<b>${escapeHtml(ti.investment_scope || "-")}</b></div>
      </div>
      <div style="text-align:right;">
        <span class="tier-badge ${tierClass}">${escapeHtml(ti.recommended_investment_tier || "-")}</span>
        <div style="font-family:var(--mono);font-size:13px;font-weight:700;color:var(--accent-soft);margin-top:4px;">${fmtUsd(budget)}${budgetPct}</div>
      </div>
    </div>
    <div class="score-bars">${bars}</div>
    ${ti.recommended_action ? `<div class="stage-action">${escapeHtml(ti.recommended_action)}</div>` : ""}
    ${reasoningHtml}
  `;
  return el;
}

// ── Review section (decision + TRM + report) ──────────────────
// 각 iteration 마다 별도 섹션 (.review-sec-iterN) 생성 — 누적 표시
function renderReviewSection(review, iteration) {
  if (!review) return;
  const iterN = iteration || 1;
  const cls = `review-sec review-sec-iter${iterN}`;
  let sec = panel().querySelector(`.review-sec-iter${iterN}`);
  if (!sec) {
    sec = document.createElement("div");
    sec.className = `panel-section ${cls}`;
    panel().appendChild(sec);
  }
  const decision = (review.decision || "ACCEPT").toUpperCase();
  const banner = `<div class="decision-banner ${decision === "ACCEPT" ? "accept" : "revise"}">
    ${decision}${iteration ? ` · iteration ${iteration}` : ""}
  </div>`;

  const trm = review.trm_assessment || {};
  const trmCell = (k, title, inner) =>
    `<div class="trm-cell"><div class="k">${title}</div><div class="v">${inner}</div></div>`;

  const feas = trm.feasibility || {};
  const seq  = trm.sequencing || {};
  const al   = trm.strategic_alignment || {};
  const ir   = trm.investment_rationality || {};
  const pb   = trm.portfolio_balance || {};

  // 3-state flag: true=OK / false=FAIL / undefined·null=n/a (LLM 이 값 누락)
  const flag = (b) => {
    if (b === true)  return `<span class="flag ok">OK</span>`;
    if (b === false) return `<span class="flag bad">FAIL</span>`;
    return `<span class="flag" style="border-color:var(--text-dim);color:var(--text-dim);">n/a</span>`;
  };

  // 점수 표시: undefined/null/NaN → "n/a" (LLM 이 점수 안 채운 경우)
  const fmtScore = (v) => {
    if (v === undefined || v === null || v === "") {
      return `<span style="color:var(--text-dim);">n/a</span>`;
    }
    const n = Number(v);
    if (Number.isNaN(n)) {
      return `<span style="color:var(--text-dim);">n/a</span>`;
    }
    return n.toFixed(2);
  };

  const listBlock = (title, items) => {
    if (!items || !items.length) return "";
    return `<div style="margin-top:4px;"><b>${title}</b>: ${items.map(i => `<code>${escapeHtml(i)}</code>`).join(", ")}</div>`;
  };

  // ── 예산 & 분배 sanity 1차 점검 (UI side computed) ──
  const strategy = ctx.investment_strategy || [];
  const pf = ctx.problemFrame || {};
  const totalBudget = Number(pf.total_budget) || 0;
  const allTechs = [];
  strategy.forEach(s => (s.tech_investments || []).forEach(ti => allTechs.push(ti)));
  const sumBudget = allTechs.reduce((acc, ti) => acc + (Number(ti.tech_budget_usd) || 0), 0);
  // 예산 초과는 hard fail. 단 rounding 오차 허용 (0.1%, $1M 중 큰 값)
  const overflowTol = totalBudget > 0 ? Math.max(totalBudget * 0.001, 1_000_000) : 0;
  const budgetOverflow = totalBudget > 0 && (sumBudget - totalBudget) > overflowTol;
  // OK 범위: total 이하 + -25% 이내 미달
  const budgetOk = totalBudget > 0 && !budgetOverflow && (totalBudget - sumBudget) / totalBudget <= 0.25;

  const tierCounts = { "Tier 1": 0, "Tier 2": 0, "Tier 3": 0 };
  allTechs.forEach(ti => {
    const t = ti.recommended_investment_tier || "Tier 2";
    if (tierCounts[t] !== undefined) tierCounts[t]++;
  });
  const tierTotal = tierCounts["Tier 1"] + tierCounts["Tier 2"] + tierCounts["Tier 3"];
  const tierBalanced = tierTotal > 0 && Math.max(...Object.values(tierCounts)) / tierTotal < 0.80;

  // year_idx 별 budget 분포 (각 기술의 시작 차년도에 할당)
  const rm = ctx.planned_roadmap || [];
  const yearMap = {};
  let maxY = 0;
  rm.forEach(r => {
    const ys = r.year_idx_start || 0;
    if (!ys) return;
    maxY = Math.max(maxY, r.year_idx_target || ys);
    const inv = allTechs.find(t => t.tech_id === r.tech_id);
    yearMap[ys] = (yearMap[ys] || 0) + (Number(inv?.tech_budget_usd) || 0);
  });
  const yearBudgets = Object.values(yearMap);
  const maxYearShare = yearBudgets.length && sumBudget > 0 ? Math.max(...yearBudgets) / sumBudget : 0;
  const yearBalanced = maxYearShare < 0.80;

  const fmtUsdBig = (n) => {
    if (!n || isNaN(n)) return "-";
    if (n >= 1e9) return `$${(n / 1e9).toFixed(2)}B`;
    if (n >= 1e6) return `$${(n / 1e6).toFixed(0)}M`;
    return `$${Math.round(n).toLocaleString()}`;
  };
  const tierBar = `T1=<b>${tierCounts["Tier 1"]}</b> · T2=<b>${tierCounts["Tier 2"]}</b> · T3=<b>${tierCounts["Tier 3"]}</b>`;

  const sanityGrid = `<div class="trm-grid" style="margin-bottom:6px;">
    ${trmCell("budget", "💰 예산 점검",
      `${budgetOverflow ? `<span class="flag bad">OVERFLOW</span>` : (budgetOk ? `<span class="flag ok">OK</span>` : `<span class="flag bad">UNDER</span>`)} sum vs total
       <div style="margin-top:4px;color:var(--text-dim);">
         ${fmtUsdBig(sumBudget)} / ${fmtUsdBig(totalBudget)}
         ${totalBudget > 0 ? `· 편차 ${((sumBudget - totalBudget) / totalBudget * 100).toFixed(1)}%` : ""}
         ${budgetOverflow ? "<br/><b style=\"color:var(--danger);\">⚠️ 예산 초과 — REVISE 발동</b>" : ""}
       </div>`)}
    ${trmCell("tier", "🎯 Tier 분포",
      `${tierBalanced ? `<span class="flag ok">OK</span>` : `<span class="flag bad">SKEWED</span>`}
       <div style="margin-top:4px;color:var(--text-dim);">${tierBar} (총 ${tierTotal})</div>`)}
    ${trmCell("yearly", "📅 차년도 분포",
      `${yearBalanced ? `<span class="flag ok">OK</span>` : `<span class="flag bad">SKEWED</span>`}
       <div style="margin-top:4px;color:var(--text-dim);">max 단일 차년도 비중 ${(maxYearShare * 100).toFixed(0)}% · horizon 1~${maxY}차년도</div>`)}
  </div>`;

  // ── TRM 5-axis 세부 (접기) ──
  const trmGrid = `<details class="report-section" style="margin-bottom:6px;">
    <summary>TRM 5축 세부 평가 (LLM)</summary>
    <div class="body" style="padding:8px;">
      <div class="trm-grid">
        ${trmCell("feasibility", "Feasibility",
          `${flag(feas.budget_feasible)} budget · ${flag(feas.schedule_feasible)} schedule
           <div style="margin-top:4px;color:var(--text-dim);">${escapeHtml(feas.comment || "")}</div>`)}
        ${trmCell("sequencing", "Sequencing",
          `${flag(seq.dependency_valid)} dependency
           <div style="margin-top:4px;color:var(--text-dim);">${escapeHtml(seq.comment || "")}</div>`)}
        ${trmCell("alignment", "Strategic Alignment",
          `<span class="score">fit ${fmtScore(al.company_fit)}</span>
           <span class="score">trend ${fmtScore(al.future_trend_alignment)}</span>
           <div style="margin-top:4px;color:var(--text-dim);">${escapeHtml(al.comment || "")}</div>`)}
        ${trmCell("investment", "Investment Rationality",
          `<div style="color:var(--text-dim);">${escapeHtml(ir.comment || "")}</div>
           ${listBlock("over_invested", ir.over_invested)}
           ${listBlock("under_invested", ir.under_invested)}`)}
        ${trmCell("portfolio", "Portfolio Balance",
          `<span class="score">short/long ${fmtScore(pb.short_long_balance)}</span>
           <span class="score">risk ${fmtScore(pb.risk_balance)}</span>
           <div style="margin-top:4px;color:var(--text-dim);">${escapeHtml(pb.comment || "")}</div>`)}
      </div>
    </div>
  </details>`;

  // 합쳐서 trmGrid 변수로 사용
  const allChecksHtml = sanityGrid + trmGrid;

  const issues = review.issues || [];
  const issuesHtml = issues.length
    ? `<div class="issues-block"><h4>Issues (${issues.length})</h4>
         <ul>${issues.map(i => `<li>${escapeHtml(i)}</li>`).join("")}</ul></div>`
    : "";

  const report = review.report || {};
  const reportSections = [
    ["executive_summary", "1. Executive Summary"],
    ["technology_strategy", "2. Technology Strategy"],
    ["roadmap_structure", "3. Roadmap Structure"],
    ["investment_strategy", "4. Investment Strategy"],
    ["trend_alignment", "5. Trend Alignment"],
    ["feasibility_and_risk", "6. Feasibility & Risk"],
    ["expected_outcomes", "7. Expected Outcomes"],
  ];
  const reportHtml = decision === "ACCEPT"
    ? reportSections
        .filter(([k]) => (report[k] || "").trim())
        .map(([k, label], idx) => `
          <details class="report-section" ${idx === 0 ? "open" : ""}>
            <summary>${escapeHtml(label)}</summary>
            <div class="body">${escapeHtml(report[k])}</div>
          </details>`)
        .join("") + renderArtifactsSummary(report.artifacts_summary)
    : (review.diagnostic_summary
         ? `<div class="report-section"><div class="body" style="padding:10px 12px;">${escapeHtml(review.diagnostic_summary)}</div></div>`
         : "");

  // IO 카드 — Orchestrator Review
  const ioCard = `<div class="io-card">
    <div class="io-title">Orchestrator · Review <span class="arrow">(integrate + evaluate)</span></div>
    <div class="io-row"><span class="tag in">IN</span>
      <div class="content">
        <span class="k">problem_frame</span> · <span class="k">tech_candidates</span>(${(ctx.tech_candidates || []).length})
         · <span class="k">planned_roadmap</span>(${(ctx.planned_roadmap || []).length})
         · <span class="k">investment_strategy</span>(${(ctx.investment_strategy || []).length} stages)
         · <span class="k">active_agents</span> <span class="v">${ctx.activeAgents.join(", ")}</span>
      </div>
    </div>
    <div class="io-row"><span class="tag out">OUT</span>
      <div class="content">
        <span class="k">decision</span> <span class="v"><b>${escapeHtml(decision)}</b></span> ·
        <span class="k">issues</span> <span class="v">${issues.length}</span> ·
        <span class="k">report sections</span> <span class="v">${Object.values(report).filter(s => (s||"").trim()).length}/7</span>
      </div>
    </div>
  </div>`;

  // ACCEPT 일 때 — 최종 보고서임을 명확히 알리는 헤더 + 다운로드 링크
  const isFinalAccept = (decision === "ACCEPT");
  const reportBase = sessionId ? `/outputs/web_${sessionId}_orchestrator_report` : null;
  const downloadButtons = reportBase
    ? `<div style="margin:8px 0; display:flex; gap:8px; flex-wrap:wrap;">
         <a href="${reportBase}.html" target="_blank"
            style="padding:6px 12px;background:var(--accent);color:#1a1a1a;
                   text-decoration:none;border-radius:6px;font-weight:600;font-size:12px;">
           📋 HTML 보고서 새 탭에서 보기
         </a>
         <a href="${reportBase}.md" target="_blank" download
            style="padding:6px 12px;background:var(--bg-elev);color:var(--accent-soft);
                   text-decoration:none;border-radius:6px;font-weight:600;font-size:12px;
                   border:1px solid var(--border);">
           ⬇️ Markdown
         </a>
         <a href="${reportBase}.json" target="_blank" download
            style="padding:6px 12px;background:var(--bg-elev);color:var(--accent-soft);
                   text-decoration:none;border-radius:6px;font-weight:600;font-size:12px;
                   border:1px solid var(--border);">
           ⬇️ JSON
         </a>
       </div>`
    : "";
  const finalReportHeader = isFinalAccept && reportHtml
    ? `<div style="margin:10px 0 6px 0;padding:8px 12px;background:rgba(126,194,126,0.10);
                   border-left:3px solid var(--success);border-radius:4px;
                   font-weight:600;color:var(--success);">
         ✅ 최종 보고서 (Final Report) — 7-섹션 + Year × Tech 매트릭스
       </div>
       ${downloadButtons}`
    : "";

  sec.innerHTML = `
    <h3>Orchestrator · Review${iteration ? ` (iter ${iteration})` : ""}</h3>
    ${ioCard}
    ${banner}
    ${allChecksHtml}
    ${issuesHtml}
    ${finalReportHeader}
    ${reportHtml}
  `;
}

function appendRefineBanner(rerunAgents, feedback) {
  const sec = panel().querySelector(".review-sec");
  if (!sec) return;

  const wrap = document.createElement("div");
  wrap.className = "refine-block";

  const banner = document.createElement("div");
  banner.className = "refine-line";
  banner.textContent = `↻ REVISE — rerun: ${(rerunAgents || []).join(", ")} | feedback: ${(feedback || []).length}건`;
  wrap.appendChild(banner);

  // feedback 내용 리스트 출력 — 다음 iteration 의 각 agent prompt 에 박힐 텍스트
  const items = (feedback || []).filter(t => typeof t === "string" && t.trim());
  if (items.length) {
    const list = document.createElement("details");
    list.className = "refine-feedback";
    list.open = true;
    list.innerHTML = `
      <summary>피드백 ${items.length}건 — 다음 iteration 의 재실행 Agent prompt 에 전달됨</summary>
      <ul>${items.map(t => `<li>${escapeHtml(t)}</li>`).join("")}</ul>`;
    wrap.appendChild(list);
  }

  sec.appendChild(wrap);
}

// ── 8. Artifacts Summary 렌더 ──────────────────────────────────
// review.report.artifacts_summary 는 dict 형태 (string 이 아닌 raw 데이터).
// 7개 string narrative 섹션과 다른 렌더링 — 표 형식으로 [A1]/[A2]/[A3] 출처 추적용.
function renderArtifactsSummary(artifacts) {
  if (!artifacts || typeof artifacts !== "object") return "";
  const a1 = artifacts.agent1_tech_candidates || [];
  const a2 = artifacts.agent2_planned_roadmap || [];
  const a3 = artifacts.agent3_investment_strategy || [];
  const insights = artifacts.insights || {};

  // Agent 1 표 — tech_id / name / category / TRL / scores / boom
  const a1Rows = a1.map(t => `
    <tr>
      <td><b>${escapeHtml(t.tech_id || "")}</b></td>
      <td>${escapeHtml(t.name || "")}</td>
      <td>${escapeHtml(t.category || "")}</td>
      <td>${escapeHtml(String(t.trl ?? ""))}</td>
      <td>${escapeHtml(String(t.final_score ?? ""))}</td>
      <td>${escapeHtml(String(t.market_score ?? ""))}</td>
      <td>${escapeHtml(String(t.patent_score ?? ""))}</td>
      <td>${escapeHtml(t.expected_market_boom_quarter || "")}</td>
    </tr>`).join("");
  const a1Html = a1.length ? `
    <details class="artifact-sub" open>
      <summary>[A1] Agent 1 · Tech Candidates (${a1.length})</summary>
      <div class="body">
        <table class="artifact-table">
          <thead><tr>
            <th>tech_id</th><th>name</th><th>category</th><th>TRL</th>
            <th>final</th><th>market</th><th>patent</th><th>boom</th>
          </tr></thead>
          <tbody>${a1Rows}</tbody>
        </table>
      </div>
    </details>` : "";

  // Agent 2 표 — tech_id / 차년도 / prereq
  const a2Rows = a2.map(r => `
    <tr>
      <td><b>${escapeHtml(r.tech_id || "")}</b></td>
      <td>${r.year_idx_start ?? "?"}차년도 → ${r.year_idx_target ?? "?"}차년도</td>
      <td>${escapeHtml((r.prerequisites || []).join(", "))}</td>
    </tr>`).join("");
  const a2Html = a2.length ? `
    <details class="artifact-sub">
      <summary>[A2] Agent 2 · Planned Roadmap (${a2.length})</summary>
      <div class="body">
        <table class="artifact-table">
          <thead><tr>
            <th>tech_id</th><th>차년도</th><th>prerequisites</th>
          </tr></thead>
          <tbody>${a2Rows}</tbody>
        </table>
      </div>
    </details>` : "";

  // Agent 3 — stage 별로 그룹
  const a3Html = a3.length ? `
    <details class="artifact-sub">
      <summary>[A3] Agent 3 · Investment Strategy (${a3.length} stages)</summary>
      <div class="body">
        ${a3.map(s => {
          const techRows = (s.tech_investments || []).map(ti => `
            <tr>
              <td><b>${escapeHtml(ti.tech_id || "")}</b></td>
              <td>${escapeHtml(ti.tier || "")}</td>
              <td>${escapeHtml(String(ti.investment_attractiveness ?? ""))}</td>
              <td>${escapeHtml(String(ti.investment_urgency ?? ""))}</td>
              <td>${escapeHtml(ti.recommended_action || "")}</td>
              <td>${escapeHtml((ti.major_risks || []).join("; "))}</td>
            </tr>`).join("");
          return `
            <div class="artifact-stage">
              <div class="artifact-stage-title">
                <b>${escapeHtml(s.stage || "")}</b>
                <span style="color:var(--text-dim);">· ${escapeHtml(s.period || "")}</span>
              </div>
              ${s.stage_assessment ? `<div class="artifact-stage-assess">${escapeHtml(s.stage_assessment)}</div>` : ""}
              <table class="artifact-table">
                <thead><tr>
                  <th>tech_id</th><th>tier</th><th>attract</th><th>urgency</th>
                  <th>action</th><th>risks</th>
                </tr></thead>
                <tbody>${techRows}</tbody>
              </table>
            </div>`;
        }).join("")}
      </div>
    </details>` : "";

  // Insights — tier 분포 / 카테고리 / 평균 TRL / 의존성 엣지
  const insightsHtml = Object.keys(insights).length ? `
    <details class="artifact-sub">
      <summary>Insights (집계 통계)</summary>
      <div class="body">
        <pre class="artifact-insights">${escapeHtml(JSON.stringify(insights, null, 2))}</pre>
      </div>
    </details>` : "";

  // Year × Tech 매트릭스 + 종합 Reasoning
  const matrixHtml = renderYearTechMatrix(artifacts.year_tech_matrix, a2, a3);

  return `
    <details class="report-section artifact-section">
      <summary>8. Artifacts Summary <span style="color:var(--text-dim);font-weight:normal;">— [A1]/[A2]/[A3] 출처 데이터</span></summary>
      <div class="body" style="padding:8px 4px;">
        ${matrixHtml}${a1Html}${a2Html}${a3Html}${insightsHtml}
      </div>
    </details>`;
}

// Year × Tech 매트릭스 — 차년도 별 기술 배치 + 예산 + 종합 reasoning
function renderYearTechMatrix(matrix, a2, a3) {
  if (!matrix || !matrix.cells) return "";
  const cells = matrix.cells || {};
  const maxYear = matrix.max_year || 5;
  const yearlyTotal = matrix.yearly_budget_total || {};

  const fmtUsd = (v) => {
    const n = Number(v) || 0;
    if (n >= 1e9) return `$${(n / 1e9).toFixed(2)}B`;
    if (n >= 1e6) return `$${(n / 1e6).toFixed(0)}M`;
    return n ? `$${n.toLocaleString()}` : "-";
  };
  const tierClass = (t) => `tier-${String(t || "").replace(/[^0-9]/g, "") || "x"}`;

  // 헤더: 1차년도 | 2차년도 | ... | N차년도
  const headerCells = [];
  for (let y = 1; y <= maxYear; y++) {
    headerCells.push(`<th>${y}차년도<br/><span class="dim">${fmtUsd(yearlyTotal[y] || 0)}</span></th>`);
  }

  // 행: 각 기술의 매트릭스 — year_idx_start~target 동안 색칠된 셀
  const a3ByTid = {};
  (a3 || []).forEach(s => (s.tech_investments || []).forEach(ti => { if (ti.tech_id) a3ByTid[ti.tech_id] = ti; }));

  const rows = (a2 || []).map(r => {
    const tid = r.tech_id;
    const inv = a3ByTid[tid] || {};
    const ys = r.year_idx_start || 1, yt = r.year_idx_target || ys;
    const cellsHtml = [];
    for (let y = 1; y <= maxYear; y++) {
      if (y >= ys && y <= yt) {
        const isStart = y === ys;
        cellsHtml.push(`<td class="matrix-cell active ${tierClass(inv.tier)}">${isStart ? fmtUsd(inv.tech_budget_usd) : "■"}</td>`);
      } else {
        cellsHtml.push(`<td class="matrix-cell"></td>`);
      }
    }
    return `
      <tr>
        <td class="matrix-tech-label">
          <b>${escapeHtml(tid)}</b><br/>
          <span class="dim">${escapeHtml((r.name || "").slice(0, 25))}</span><br/>
          <span class="badge tier ${tierClass(inv.tier)}">${escapeHtml(inv.tier || "-")}</span>
        </td>
        ${cellsHtml.join("")}
      </tr>`;
  }).join("");

  // 종합 Reasoning — Designer + Strategist 모두
  const reasoningRows = (a2 || []).map(r => {
    const tid = r.tech_id;
    const inv = a3ByTid[tid] || {};
    const dr = r.reasoning || {};
    const ir = inv.reasoning || {};
    const blocks = [];
    if (dr.year_placement) blocks.push(`<div class="reason-block"><b>📅 차년도 배치 (Designer):</b><br/>${escapeHtml(dr.year_placement)}</div>`);
    if (dr.tech_execution) blocks.push(`<div class="reason-block"><b>🛠️ 기술 수행 (Designer):</b><br/>${escapeHtml(dr.tech_execution)}</div>`);
    if (dr.investment_selection) blocks.push(`<div class="reason-block"><b>🎯 투자 선정 (Designer):</b><br/>${escapeHtml(dr.investment_selection)}</div>`);
    if (ir.market_evaluation) blocks.push(`<div class="reason-block strategist"><b>📊 시장 평가 (Strategist):</b><br/>${escapeHtml(ir.market_evaluation)}</div>`);
    if (ir.tech_evaluation) blocks.push(`<div class="reason-block strategist"><b>⚙️ 기술 평가 (Strategist):</b><br/>${escapeHtml(ir.tech_evaluation)}</div>`);
    if (ir.investment_decision) blocks.push(`<div class="reason-block strategist"><b>💰 투자 결정 (Strategist):</b><br/>${escapeHtml(ir.investment_decision)}</div>`);
    if (inv.tech_budget_rationale) blocks.push(`<div class="reason-block strategist"><b>💵 예산 근거:</b><br/>${escapeHtml(inv.tech_budget_rationale)}</div>`);
    if (!blocks.length) return "";
    return `
      <details class="matrix-reasoning">
        <summary><b>${escapeHtml(tid)}</b> · ${escapeHtml(r.name || "")} <span class="dim">(${r.year_idx_start || "?"}차 → ${r.year_idx_target || "?"}차, ${fmtUsd(inv.tech_budget_usd)})</span></summary>
        <div class="reasoning-blocks">${blocks.join("")}</div>
      </details>`;
  }).join("");

  return `
    <details class="artifact-sub" open>
      <summary>📊 Year × Tech 매트릭스 — 로드맵 + 투자 종합</summary>
      <div class="body">
        <table class="artifact-table matrix-table">
          <thead><tr><th>기술</th>${headerCells.join("")}</tr></thead>
          <tbody>${rows}</tbody>
        </table>
        <div style="margin-top:14px;">
          <div style="font-weight:600;color:var(--accent-soft);margin-bottom:6px;">📋 종합 Reasoning (Designer + Strategist)</div>
          ${reasoningRows}
        </div>
      </div>
    </details>`;
}


// ── Utils ─────────────────────────────────────────────────────
function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));
}
