// ── State ─────────────────────────────────────────────────────
let sessionId = null;
let eventSource = null;
let currentStepBlock = null;

// 누적되는 데이터 (I/O 카드 렌더링용)
const ctx = {
  intake: null,              // {domain, reference_year, category_hints, ...}
  activeAgents: [],
  scenarios: [],
  scenarioId: "",
  usePatentMap: true,
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

  try {
    const r = await fetch("/api/scenarios");
    const data = await r.json();
    ctx.scenarios = data.scenarios || [];
    populateScenarioSelect();
  } catch (e) {
    console.warn("scenario list unavailable", e);
  }
})();

$("#send-btn").addEventListener("click", onSend);
$("#input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) onSend();
});
$("#scenario-select").addEventListener("change", onScenarioChange);

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
  const scenarioId = $("#scenario-select").value || "";
  const usePatentMap = $("#use-patent-map").checked;

  ta.value = "";
  $("#send-btn").disabled = true;
  resetCtx();
  ctx.scenarioId = scenarioId;
  ctx.usePatentMap = usePatentMap;

  addUserMessage(text);
  clearPanel();

  // 세션 생성
  let sid;
  try {
    const payload = {
      request: text,
      active_agents: activeAgents,
      scenario_id: scenarioId || null,
      use_patent_map: usePatentMap,
    };
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
    scenarioId: "", usePatentMap: true,
    market_context: null, tech_candidates: null,
    planned_roadmap: null, stages: null, investment_strategy: null,
    iterations: { agent1: 0, agent2: 0, agent3: 0, review: 0 },
  });
}

function populateScenarioSelect() {
  const select = $("#scenario-select");
  if (!select) return;
  const existing = select.value;
  select.innerHTML = `<option value="">Custom natural-language prompt</option>`;
  ctx.scenarios.forEach((s) => {
    const opt = document.createElement("option");
    opt.value = s.scenario_id;
    opt.textContent = s.name || s.scenario_id;
    select.appendChild(opt);
  });
  select.value = existing;
}

function onScenarioChange() {
  const id = $("#scenario-select").value;
  const scenario = ctx.scenarios.find((s) => s.scenario_id === id);
  if (scenario && scenario.prompt) {
    $("#input").value = scenario.prompt;
  }
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
      // 자연어 해석 (Tech 측)
      const techLines = [
        `• 도메인: ${escapeHtml(p.domain || "-")}`,
        `• 기준연도: ${p.reference_year || "-"}`,
        `• 카테고리: ${(p.category_hints || []).join(", ") || "-"}`,
      ];
      if (p.scenario_id) techLines.unshift(`• 시나리오: <code>${escapeHtml(p.scenario_id)}</code>`);
      if (p.company_name) techLines.push(`• 기업: <b>${escapeHtml(p.company_name)}</b>`);
      if (p.industry) techLines.push(`• 산업: ${escapeHtml(p.industry)}`);
      if (p.objective) techLines.push(`• 목표: ${escapeHtml(p.objective)}`);
      if (p.time_horizon) techLines.push(`• 시간 범위: ${escapeHtml(p.time_horizon)}`);
      techLines.push(`• Actor Similarity Map: <b>${p.use_patent_map === false ? "OFF" : "ON"}</b>`);

      // 자연어에서 추출된 Investment Policy (Agent 3 가 사용)
      const policyLines = [];
      if (p.risk_appetite) policyLines.push(`• Risk Appetite: <b>${escapeHtml(p.risk_appetite)}</b>`);
      if (p.investment_horizon) policyLines.push(`• Investment Horizon: <b>${escapeHtml(p.investment_horizon)}</b>`);
      if (p.total_budget) {
        const budgetFmt = Number(p.total_budget).toLocaleString();
        policyLines.push(`• Total Budget: <b>${budgetFmt} USD</b>`);
      }
      if (p.strategic_priority && p.strategic_priority.length) {
        policyLines.push(`• Strategic Priority: ${p.strategic_priority.map(x => `<code>${escapeHtml(x)}</code>`).join(", ")}`);
      }

      let html = `📥 <b>입력 해석 완료</b><br/>` + techLines.join("<br/>");
      if (policyLines.length) {
        html += `<br/><br/><b>👤 투자 정책 (자연어에서 추출)</b><br/>` + policyLines.join("<br/>");
      }
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
  sec.innerHTML = `
    <h3>Problem Frame (Orchestrator Setup)</h3>
    <div class="problem-frame">
      <div class="pf-row"><span class="k">industry</span><span class="v">${escapeHtml(pf.industry || "-")}</span></div>
      <div class="pf-row"><span class="k">company</span><span class="v">${escapeHtml(pf.company_type || "-")}</span></div>
      <div class="pf-row"><span class="k">horizon</span><span class="v">${escapeHtml(pf.time_horizon || "-")}</span></div>
      <div class="pf-row"><span class="k">budget</span><span class="v">${pf.total_budget ? Number(pf.total_budget).toLocaleString() + " USD" : "-"}</span></div>
      <div class="pf-row"><span class="k">objective</span><span class="v">${escapeHtml(pf.objective || "-")}</span></div>
      <div class="pf-row"><span class="k">priorities</span>
        <div class="v"><div class="pf-chips">${
          (pf.strategic_priorities || []).map(p => `<span class="pf-chip">${escapeHtml(p)}</span>`).join("")
        }</div></div>
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

  const toIdx = (q) => { const m = /^(\d{4})\s*Q([1-4])$/.exec((q||"").trim()); return m ? parseInt(m[1]) * 4 + (parseInt(m[2]) - 1) : null; };
  const fromIdx = (i) => `${Math.floor(i / 4)} Q${(i % 4) + 1}`;
  let minI = Infinity, maxI = -Infinity;
  rm.forEach((it) => { const a = toIdx(it.start_q), b = toIdx(it.target_q); if (a != null) minI = Math.min(minI, a); if (b != null) maxI = Math.max(maxI, b); });
  if (!isFinite(minI)) { minI = 0; maxI = 3; }
  const span = Math.max(1, maxI - minI);

  const preview = rm.slice(0, 4).map(r =>
    `<li>${escapeHtml(r.tech_id)} · ${escapeHtml(r.phase_name || "")} — ${escapeHtml(r.start_q || "")} → ${escapeHtml(r.target_q || "")}</li>`
  ).join("");

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
          <span class="k">range</span> <span class="v">${fromIdx(minI)} → ${fromIdx(maxI)}</span>
          ${preview ? `<ul class="preview-list">${preview}${rm.length > 4 ? `<li>… ${rm.length - 4} more</li>` : ""}</ul>` : ""}
        </div>
      </div>
    </div>
    <div class="a2-timeline"></div>`;

  const tl = sec.querySelector(".a2-timeline");
  rm.forEach((it) => {
    const a = toIdx(it.start_q) ?? minI;
    const b = toIdx(it.target_q) ?? a;
    const left = ((a - minI) / span) * 100;
    const width = Math.max(3, ((b - a + 1) / span) * 100);
    const row = document.createElement("div");
    row.className = "timeline-row";
    row.innerHTML = `
      <div class="timeline-label">
        <div><span class="id">${escapeHtml(it.tech_id)}</span></div>
        <div class="name">${escapeHtml(it.name || "")}</div>
        <div class="cat">${escapeHtml(it.phase_name || "")}</div>
      </div>
      <div class="timeline-bar-wrap">
        <div class="timeline-bar" style="left:${left}%;width:${width}%;">
          ${escapeHtml(it.start_q || "")} → ${escapeHtml(it.target_q || "")}
        </div>
      </div>`;
    tl.appendChild(row);
  });
}

// ── Agent 3 section (I/O + Stage cards) ───────────────────────
function renderAgent3Section() {
  const stages = ctx.stages || [];
  const strategy = ctx.investment_strategy || [];
  const iter = ctx.iterations.agent3 > 1 ? ` (iter ${ctx.iterations.agent3})` : "";

  let sec = panel().querySelector(".a3-sec");
  if (!sec) {
    sec = document.createElement("div");
    sec.className = "panel-section a3-sec";
    panel().appendChild(sec);
  }

  // 새 구조: 각 stage 의 tech_investments 안에 per-tech tier 가 있음
  const tierSummary = (s) => {
    const tis = s.tech_investments || [];
    if (!tis.length) return "?";
    const counts = { "Tier 1": 0, "Tier 2": 0, "Tier 3": 0 };
    tis.forEach(ti => {
      const t = ti.recommended_investment_tier || "Tier 2";
      if (counts[t] !== undefined) counts[t]++;
    });
    return `T1=${counts["Tier 1"]} T2=${counts["Tier 2"]} T3=${counts["Tier 3"]}`;
  };
  const preview = strategy.slice(0, 3).map(s =>
    `<li>${escapeHtml(s.stage || "")} — <b>${escapeHtml(tierSummary(s))}</b> · ${(s.tech_investments || []).length}개 기술</li>`
  ).join("");

  sec.innerHTML = `
    <h3>Agent 3 · Investment Strategist${iter}</h3>
    <div class="io-card">
      <div class="io-title">Investment Strategy <span class="arrow">→</span> Orchestrator <span class="badge">${strategy.length} stages</span></div>
      <div class="io-row"><span class="tag in">IN</span>
        <div class="content">
          <span class="k">planned_roadmap</span> <span class="v">${(ctx.planned_roadmap || []).length}건</span> ·
          <span class="k">tech_candidates</span> <span class="v">${(ctx.tech_candidates || []).length}건</span> ·
          <span class="k">stages(집계)</span> <span class="v">${stages.length}</span>
        </div>
      </div>
      <div class="io-row"><span class="tag out">OUT</span>
        <div class="content">
          <span class="k">investment_strategy</span> <span class="v">${strategy.length} stages</span>
          ${preview ? `<ul class="preview-list">${preview}</ul>` : ""}
        </div>
      </div>
    </div>
    <div class="a3-stages"></div>`;

  const wrap = sec.querySelector(".a3-stages");
  strategy.forEach((s) => wrap.appendChild(makeStageCard(s)));
}

// 새 구조: stage 컨테이너 (header + stage_assessment) + 그 안에 per-tech 카드들
function makeStageCard(s) {
  const techInvs = s.tech_investments || [];

  // tier 분포 (stage header 에 요약 표시)
  const tierCounts = { "Tier 1": 0, "Tier 2": 0, "Tier 3": 0 };
  techInvs.forEach(ti => {
    const t = ti.recommended_investment_tier || "Tier 2";
    if (tierCounts[t] !== undefined) tierCounts[t]++;
  });

  // stage-level 예산 (ratio + 추정 USD)
  const ratio = Number(s.stage_budget_ratio ?? 0);
  const estUsd = Number(s.stage_estimated_usd ?? 0);
  const fmtUsd = (n) => {
    if (!n || isNaN(n)) return "-";
    if (n >= 1e9) return `$${(n / 1e9).toFixed(2)}B`;
    if (n >= 1e6) return `$${(n / 1e6).toFixed(0)}M`;
    return `$${Math.round(n).toLocaleString()}`;
  };
  const ratioPct = ratio > 0 ? `${(ratio * 100).toFixed(1)}%` : "-";
  const stageBudgetLine = (ratio > 0 || estUsd > 0)
    ? `<div style="font-size:12px;color:var(--accent-soft); margin:4px 0 6px 0;">
         💰 단계 예산: <b>${ratioPct}</b> · 추정 <b>${fmtUsd(estUsd)}</b>
       </div>`
    : "";

  const el = document.createElement("div");
  el.className = "stage-card";
  el.innerHTML = `
    <div class="stage-card-header">
      <div>
        <div class="stage-name">${escapeHtml(s.stage || "")}</div>
        <div class="stage-period">${escapeHtml(s.period || "")}</div>
      </div>
      <span style="font-size:11px;color:var(--text-dim);">
        T1=<b>${tierCounts["Tier 1"]}</b> · T2=<b>${tierCounts["Tier 2"]}</b> · T3=<b>${tierCounts["Tier 3"]}</b>
      </span>
    </div>
    ${stageBudgetLine}
    ${s.stage_assessment ? `<div class="stage-action" style="margin-bottom:6px;"><b>Stage 통합 판단:</b> ${escapeHtml(s.stage_assessment)}</div>` : ""}
    <div class="tech-invs"></div>
  `;

  const wrap = el.querySelector(".tech-invs");
  techInvs.forEach(ti => wrap.appendChild(makeTechInvestmentCard(ti)));
  return el;
}

function makeTechInvestmentCard(ti) {
  const tier = (ti.recommended_investment_tier || "").replace(/\s+/g, "").toLowerCase();
  const tierClass = tier === "tier1" ? "t1" : tier === "tier2" ? "t2" : "t3";
  const es = ti.evaluation_scores || {};
  const scoreKeys = [
    ["market_opportunity", "MO"],
    ["strategic_fit", "SF"],
    ["executability", "EX"],
    ["uncertainty", "UN"],
    ["urgency", "UR"],
  ];
  const bars = scoreKeys.map(([k, short]) => {
    const v = es[k] ?? 0;
    const pct = Math.max(0, Math.min(100, (v / 5) * 100));
    return `<div class="score-item">
      <div class="score-label">${short}</div>
      <div class="score-value">${v}</div>
      <div class="score-bar"><span style="width:${pct}%"></span></div>
    </div>`;
  }).join("");

  const listBlock = (title, items) => {
    if (!items || !items.length) return "";
    return `<div class="stage-small-list"><b>${title}</b>
      <ul>${items.map(i => `<li>${escapeHtml(i)}</li>`).join("")}</ul></div>`;
  };

  const el = document.createElement("div");
  el.className = "tech-inv-card";
  el.style.cssText = "border-left:2px solid var(--border); padding:6px 8px; margin:6px 0;";
  el.innerHTML = `
    <div class="stage-card-header">
      <div>
        <div class="stage-name" style="font-size:12px;">${escapeHtml(ti.tech_id || "")} · ${escapeHtml(ti.name || "")}</div>
      </div>
      <span class="tier-badge ${tierClass}">${escapeHtml(ti.recommended_investment_tier || "-")}</span>
    </div>
    <div style="font-size:11px;color:var(--text-dim);">
      attract=<b>${escapeHtml(ti.investment_attractiveness || "-")}</b> ·
      urgency=<b>${escapeHtml(ti.investment_urgency || "-")}</b> ·
      scope=<b>${escapeHtml(ti.investment_scope || "-")}</b>
    </div>
    <div class="score-bars">${bars}</div>
    ${ti.recommended_action ? `<div class="stage-action">${escapeHtml(ti.recommended_action)}</div>` : ""}
    ${listBlock("근거", ti.rationale)}
    ${listBlock("리스크", ti.major_risks)}
    ${listBlock("자원 집중", ti.resource_focus)}
  `;
  return el;
}

// ── Review section (decision + TRM + report) ──────────────────
function renderReviewSection(review, iteration) {
  if (!review) return;
  let sec = panel().querySelector(".review-sec");
  if (!sec) {
    sec = document.createElement("div");
    sec.className = "panel-section review-sec";
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

  const trmGrid = `<div class="trm-grid">
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
  </div>`;

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

  sec.innerHTML = `
    <h3>Orchestrator · Review${iteration ? ` (iter ${iteration})` : ""}</h3>
    ${ioCard}
    ${banner}
    ${trmGrid}
    ${issuesHtml}
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

  // Agent 2 표 — tech_id / phase / start → target / prereq / lead
  const a2Rows = a2.map(r => `
    <tr>
      <td><b>${escapeHtml(r.tech_id || "")}</b></td>
      <td>${escapeHtml(r.phase_name || "")}</td>
      <td>${escapeHtml(r.start_q || "")} → ${escapeHtml(r.target_q || "")}</td>
      <td>${escapeHtml((r.prerequisites || []).join(", "))}</td>
      <td>${escapeHtml(String(r.lead_time_quarters ?? ""))}</td>
    </tr>`).join("");
  const a2Html = a2.length ? `
    <details class="artifact-sub">
      <summary>[A2] Agent 2 · Planned Roadmap (${a2.length})</summary>
      <div class="body">
        <table class="artifact-table">
          <thead><tr>
            <th>tech_id</th><th>phase</th><th>start → target</th>
            <th>prerequisites</th><th>lead (Q)</th>
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

  return `
    <details class="report-section artifact-section">
      <summary>8. Artifacts Summary <span style="color:var(--text-dim);font-weight:normal;">— [A1]/[A2]/[A3] 출처 데이터</span></summary>
      <div class="body" style="padding:8px 4px;">
        ${a1Html}${a2Html}${a3Html}${insightsHtml}
      </div>
    </details>`;
}


// ── Utils ─────────────────────────────────────────────────────
function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));
}
