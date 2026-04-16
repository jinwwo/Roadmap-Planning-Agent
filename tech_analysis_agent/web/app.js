// ── State ─────────────────────────────────────────────────────
let sessionId = null;
let eventSource = null;
let currentStepBlock = null;
let pendingDrops = new Set();
let pendingShifts = {}; // { tech_id: "YYYY QX" }

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

// ── Actions ───────────────────────────────────────────────────
async function onSend() {
  const ta = $("#input");
  const text = ta.value.trim();
  if (!text) return;
  if (eventSource) { eventSource.close(); eventSource = null; }

  ta.value = "";
  $("#send-btn").disabled = true;

  // 메시지 추가
  addUserMessage(text);
  clearPanel();

  // 세션 생성
  let sid;
  try {
    const resp = await fetch("/api/session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ request: text }),
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
  } catch (e) {
    addAgentMessage("❌ 서버 연결 실패: " + e.message);
    $("#send-btn").disabled = false;
    return;
  }

  // SSE 연결
  connectStream(sid);
}

function connectStream(sid) {
  eventSource = new EventSource(`/api/stream/${sid}`);

  const handlers = {
    session_started: (d) => addAgentMessage(`🔌 세션 시작 · ${d.payload.llm}`),
    step_start: (d) => beginStep(d.payload.step, d.payload.label),
    step_end: (d) => endStep(d.payload.step, d.payload),
    log: (d) => appendLog(d.payload.message, d.payload.source),
    intake_ready: (d) => {
      addAgentMessage(
        `📥 <b>입력 해석 완료</b><br/>` +
        `• 도메인: ${escapeHtml(d.payload.domain)}<br/>` +
        `• 기준연도: ${d.payload.reference_year}<br/>` +
        `• 카테고리: ${d.payload.category_hints.join(", ")}`,
        true
      );
    },
    candidates_ready: (d) => {
      renderCandidates(d.payload.candidates, d.payload.market_context);
    },
    hitl_request: (d) => {
      addAgentMessage("✋ <b>사용자 확인 필요</b> — 오른쪽 패널에서 후보를 검토하고 진행/drop/shift 를 선택하세요.", true);
      enableHitlControls();
    },
    roadmap_ready: (d) => {
      renderRoadmap(d.payload.planned_roadmap, d.payload.dependency_tree);
    },
    error: (d) => {
      addAgentMessage("❌ 오류: " + escapeHtml(d.payload.message));
    },
    done: (d) => {
      addAgentMessage(d.payload.ok ? "✅ 파이프라인 완료" : "⚠️ 파이프라인 종료 (에러)");
      eventSource.close();
      eventSource = null;
      $("#send-btn").disabled = false;
    },
    ping: () => {},
  };

  ["session_started","step_start","step_end","log","intake_ready","candidates_ready",
   "hitl_request","roadmap_ready","error","done","ping"].forEach((t) => {
    eventSource.addEventListener(t, (ev) => {
      try {
        const d = JSON.parse(ev.data);
        handlers[t] && handlers[t](d);
      } catch (e) { console.warn("bad event", t, e); }
    });
  });

  eventSource.onerror = () => {
    // SSE 자동 재연결 방지
    if (eventSource) { eventSource.close(); eventSource = null; }
    $("#send-btn").disabled = false;
  };
}

// ── Message helpers ───────────────────────────────────────────
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
const _stepTimers = new Map(); // step → {interval, startedAt}

function beginStep(step, label) {
  const el = document.createElement("div");
  el.className = "msg agent";
  el.dataset.step = step;
  el.innerHTML = `
    <div class="bubble" style="width:95%;">
      <div class="step-header">
        <span class="dot"></span>
        <span class="title-text">${escapeHtml(label || step)}</span>
        <span class="meta">
          <span class="spinner">⠋</span>
          <span class="elapsed">0.0s</span>
          <span class="status"><span class="thinking">실행 중<span>.</span><span>.</span><span>.</span></span></span>
        </span>
      </div>
      <div class="log-block"></div>
    </div>`;
  messages().appendChild(el);
  currentStepBlock = el;
  scrollBottom();

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
  if (t) clearInterval(t.interval);
  const hdr = el.querySelector(".step-header");
  hdr.classList.add("done");
  const secs = t ? ((performance.now() - t.startedAt) / 1000) : 0;
  const elapsedStr = secs < 10 ? secs.toFixed(1) + "s" : Math.floor(secs) + "s";
  hdr.querySelector(".spinner").textContent = "✓";
  hdr.querySelector(".elapsed").textContent = elapsedStr;
  const status = hdr.querySelector(".status");
  const extra = payload && payload.count != null ? `완료 · ${payload.count}건` : "완료";
  status.innerHTML = `<span style="color:var(--success);">${escapeHtml(extra)}</span>`;
  _stepTimers.delete(step);
  if (currentStepBlock === el) currentStepBlock = null;
}
function appendLog(line, source) {
  const target = currentStepBlock
    ? currentStepBlock.querySelector(".log-block")
    : ensureFloatingLog();
  const row = document.createElement("div");
  row.className = "log-line highlight";
  if (/Agent|Orchestrator/.test(line)) row.classList.add("step");
  if (/오류|error|❌/.test(line)) row.classList.add("error");
  row.textContent = line;
  target.appendChild(row);
  target.scrollTop = target.scrollHeight;
  // 0.9s 후 하이라이트 제거 (새 라인 강조)
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

// ── Panel: candidates + HITL ──────────────────────────────────
function clearPanel() {
  panel().innerHTML = "";
  pendingDrops = new Set();
  pendingShifts = {};
}

function renderCandidates(candidates, ctx) {
  clearPanel();
  const sec = document.createElement("div");
  sec.className = "panel-section";
  sec.innerHTML = `
    <h3>후보 기술 (${candidates.length})</h3>
    ${ctx && ctx.target_market ? `<div style="color:var(--text-dim);font-size:12px;margin-bottom:10px;">🎯 ${escapeHtml(ctx.target_market)} · 개화 예상 ${escapeHtml(ctx.expected_boom_quarter||"N/A")}</div>` : ""}
  `;
  candidates.forEach((c) => sec.appendChild(makeCandidateCard(c)));

  const actions = document.createElement("div");
  actions.className = "hitl-actions";
  actions.id = "hitl-actions";
  actions.style.display = "none";
  actions.innerHTML = `
    <button class="btn-secondary" id="btn-proceed">그대로 진행</button>
    <button class="btn-primary" id="btn-apply">적용 후 진행</button>
  `;
  sec.appendChild(actions);
  panel().appendChild(sec);

  $("#btn-proceed").addEventListener("click", () => submitFeedback(null));
  $("#btn-apply").addEventListener("click", () => {
    const fb = buildFeedback();
    submitFeedback(fb);
  });
}

function makeCandidateCard(c) {
  const el = document.createElement("div");
  el.className = "candidate";
  el.dataset.tid = c.tech_id;
  el.innerHTML = `
    <div class="candidate-header">
      <div><span class="candidate-id">${escapeHtml(c.tech_id)}</span> · <span class="candidate-title">${escapeHtml(c.name)}</span></div>
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
    <div class="candidate-rationale">${escapeHtml(c.rationale || "")}</div>
    <div class="candidate-ctrl" data-role="ctrl">
      <button data-act="drop">drop</button>
      <input type="text" data-act="shift-input" placeholder="2026 Q1" />
      <button data-act="shift">shift</button>
      <button data-act="reset">reset</button>
    </div>
  `;
  const ctrl = el.querySelector("[data-role=ctrl]");
  ctrl.addEventListener("click", (ev) => {
    const act = ev.target.dataset.act;
    if (!act) return;
    if (act === "drop") {
      pendingDrops.add(c.tech_id);
      el.classList.add("dropped");
    } else if (act === "shift") {
      const input = ctrl.querySelector("[data-act=shift-input]");
      const v = input.value.trim();
      if (!/^\d{4}\s*Q[1-4]$/.test(v)) { input.style.borderColor = "var(--danger)"; return; }
      input.style.borderColor = "";
      pendingShifts[c.tech_id] = v;
      ctrl.style.background = "rgba(126,194,126,0.12)";
    } else if (act === "reset") {
      pendingDrops.delete(c.tech_id);
      delete pendingShifts[c.tech_id];
      el.classList.remove("dropped");
      ctrl.style.background = "";
    }
  });
  return el;
}

function buildFeedback() {
  const drop = [...pendingDrops];
  const shift = Object.entries(pendingShifts).map(([tech_id, new_start_q]) => ({ tech_id, new_start_q }));
  if (drop.length === 0 && shift.length === 0) return null;
  return { drop, shift };
}

function enableHitlControls() {
  const a = $("#hitl-actions");
  if (a) a.style.display = "flex";
}

async function submitFeedback(feedback) {
  if (!sessionId) return;
  const a = $("#hitl-actions");
  if (a) a.style.display = "none";
  try {
    await fetch(`/api/feedback/${sessionId}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ feedback }),
    });
    addAgentMessage(
      feedback
        ? `✓ 피드백 적용: drop=${feedback.drop.length}건, shift=${feedback.shift.length}건`
        : "✓ 피드백 없이 그대로 진행"
    );
  } catch (e) {
    addAgentMessage("❌ 피드백 전송 실패: " + e.message);
  }
}

// ── Roadmap rendering ─────────────────────────────────────────
function renderRoadmap(roadmap, depTree) {
  if (!roadmap || roadmap.length === 0) {
    const sec = document.createElement("div");
    sec.className = "panel-section";
    sec.innerHTML = `<h3>최종 로드맵</h3><div style="color:var(--text-dim);">항목이 없습니다.</div>`;
    panel().appendChild(sec);
    return;
  }

  // 분기 레인지 계산
  const toIdx = (q) => {
    const m = /^(\d{4})\s*Q([1-4])$/.exec((q || "").trim());
    if (!m) return null;
    return parseInt(m[1]) * 4 + (parseInt(m[2]) - 1);
  };
  const fromIdx = (i) => `${Math.floor(i / 4)} Q${(i % 4) + 1}`;

  let minI = Infinity, maxI = -Infinity;
  roadmap.forEach((it) => {
    const a = toIdx(it.start_q), b = toIdx(it.target_q);
    if (a != null) minI = Math.min(minI, a);
    if (b != null) maxI = Math.max(maxI, b);
  });
  if (!isFinite(minI)) { minI = 0; maxI = 3; }
  const span = Math.max(1, maxI - minI);

  const sec = document.createElement("div");
  sec.className = "panel-section";
  sec.innerHTML = `<h3>최종 로드맵 (${roadmap.length}건)</h3>
    <div style="color:var(--text-dim);font-size:12px;margin-bottom:8px;">${fromIdx(minI)} → ${fromIdx(maxI)}</div>`;

  roadmap.forEach((it) => {
    const a = toIdx(it.start_q) ?? minI;
    const b = toIdx(it.target_q) ?? a;
    const left = ((a - minI) / span) * 100;
    const width = Math.max(3, ((b - a + 1) / span) * 100);
    const row = document.createElement("div");
    row.className = "timeline-row";
    row.innerHTML = `
      <div class="timeline-label">
        <div><span class="id">${escapeHtml(it.tech_id)}</span></div>
        <div class="name">${escapeHtml(it.name)}</div>
        <div class="cat">${escapeHtml(it.phase_name || "")}</div>
      </div>
      <div class="timeline-bar-wrap">
        <div class="timeline-bar" style="left:${left}%;width:${width}%;">
          ${escapeHtml(it.start_q)} → ${escapeHtml(it.target_q)}
        </div>
      </div>
    `;
    sec.appendChild(row);
  });

  // justification 접이식
  const det = document.createElement("div");
  det.className = "panel-section";
  det.innerHTML = `<h3>상세 (Justification)</h3>`;
  roadmap.forEach((it) => {
    const d = document.createElement("details");
    d.className = "roadmap-item";
    d.innerHTML = `
      <summary>${escapeHtml(it.tech_id)} · ${escapeHtml(it.name)}
        <span class="badge">${escapeHtml(it.start_q)} → ${escapeHtml(it.target_q)}</span>
      </summary>
      <div class="just">${escapeHtml(it.justification || "")}</div>
      ${it.prerequisites && it.prerequisites.length
        ? `<div class="just">⬅ 선행: ${it.prerequisites.map(escapeHtml).join(", ")}</div>` : ""}
    `;
    det.appendChild(d);
  });

  panel().appendChild(sec);
  panel().appendChild(det);
}

// ── Utils ─────────────────────────────────────────────────────
function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));
}
