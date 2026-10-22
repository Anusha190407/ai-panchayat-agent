/* AI Panchayat front end — vanilla JS, no build step. */
(() => {
  "use strict";

  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const state = {
    config: null,
    status: null,
    pending: null,       // follow-up context when the agent asked a question
    image: null,         // {mime, data, url}
    lastTestResults: [],
    loaded: {},
  };

  const SAMPLES = [
    { label: "💧 No water, in English", text: "No drinking water in Periyar Nagar, ward 3 for the last 3 days. The pipeline near the water tank is leaking." },
    { label: "💡 Street light, in Tamil", text: "காமராஜர் தெருவில் தெரு விளக்கு ஒரு வாரமாக எரியவில்லை. இரவில் பெண்கள் நடக்க பயமாக உள்ளது." },
    { label: "⚡ Fallen live wire", text: "Electric wire has fallen on the road near the school in Gandhi Street. Children walk here, very dangerous!" },
    { label: "🔁 Repeat drainage report", text: "Drainage is overflowing on Kamarajar Street, dirty water entering houses." },
    { label: "❓ Vague complaint", text: "Water is not coming properly" },
    { label: "🗑️ Garbage, in Tanglish", text: "Market road la kuppai oru vaaram ah edukkala, romba naaththam adikuthu" },
    { label: "☣️ Sewage in drinking water", text: "Sewage water is mixing with drinking water pipe in Indira Colony. Many people have fever." },
  ];

  // ------------------------------------------------------------ helpers
  async function api(path, opts = {}) {
    const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
    let data = {};
    try { data = await res.json(); } catch (_) { /* empty body */ }
    if (!res.ok) throw new Error(data.error || `Request failed (${res.status}).`);
    return data;
  }

  async function streamPost(path, body, onEvent) {
    const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!res.ok) {
      let msg = `Request failed (${res.status}).`;
      try { msg = (await res.json()).error || msg; } catch (_) { /* ignore */ }
      throw new Error(msg);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, i).trim();
        buf = buf.slice(i + 1);
        if (line) onEvent(JSON.parse(line));
      }
    }
    if (buf.trim()) onEvent(JSON.parse(buf));
  }

  const IST = { timeZone: "Asia/Kolkata" };
  function fmtDate(iso, withYear = false) {
    if (!iso) return "";
    const opts = { ...IST, day: "numeric", month: "short", hour: "numeric", minute: "2-digit" };
    if (withYear) opts.year = "numeric";
    return new Date(iso).toLocaleString("en-IN", opts);
  }
  const fmtMs = (ms) => (ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.max(1, Math.round(ms))} ms`);
  function fmtHours(h) {
    const a = Math.abs(h);
    if (a >= 48) return `${(a / 24).toFixed(1)} days`;
    if (a >= 1) return `${Math.round(a)} h`;
    return `${Math.max(1, Math.round(a * 60))} min`;
  }
  const priorityBadge = (p) => {
    const info = state.config?.priorities?.[p];
    return `<span class="badge b-${esc(p)}">${esc(p)}${info ? " " + esc(info.label) : ""}</span>`;
  };
  const statusBadge = (s, label) => `<span class="badge b-${esc(s)}">${esc(label || s)}</span>`;
  function engineChip(engine) {
    if (!engine) return "";
    if (engine === "tool") return `<span class="engine engine-tool">Tool</span>`;
    if (engine === "guardrail") return `<span class="engine engine-guard">Safety guardrail</span>`;
    if (engine === "offline rules") return `<span class="engine engine-offline">Offline rules</span>`;
    return `<span class="engine engine-llm">Gemini</span> <span>${esc(engine)}</span>`;
  }
  function showError(el, msg) { el.textContent = msg; el.hidden = !msg; }

  // ------------------------------------------------------------ tabs
  const TABS = ["report", "desk", "escalations", "insights", "tests", "how"];
  function showTab(name) {
    if (!TABS.includes(name)) name = "report";
    $$(".tab-panel").forEach((p) => { p.hidden = p.dataset.panel !== name; });
    $$(".tab").forEach((t) => t.setAttribute("aria-current", t.dataset.tab === name ? "page" : "false"));
    if (name === "desk") loadDesk();
    if (name === "escalations") loadEscalations();
    if (name === "insights") loadInsights();
    if (name === "tests" && !state.loaded.tests) loadTests();
  }
  window.addEventListener("hashchange", () => showTab(location.hash.slice(1)));

  // ------------------------------------------------------------ status
  async function refreshStatus() {
    try {
      state.status = await api("/api/status");
    } catch (_) { return; }
    const s = state.status.llm;
    const pill = $("#engine-pill");
    const banner = $("#engine-banner");
    if (s.mode === "gemini") {
      pill.className = "pill pill-live";
      pill.textContent = `Gemini connected: ${s.model}`;
      banner.hidden = true;
    } else if (s.mode === "cooldown") {
      pill.className = "pill pill-warn";
      pill.textContent = "Gemini busy, using offline engine for a minute";
      banner.hidden = true;
    } else {
      pill.className = "pill pill-warn";
      pill.textContent = "Offline engine";
      banner.innerHTML = s.note.startsWith("No GEMINI_API_KEY")
        ? `The agents are using the offline rule engine because no Gemini API key is set. Add <code>GEMINI_API_KEY</code> in the server settings to switch them to Gemini.`
        : `Gemini is not available (${esc(s.note)}), so the offline rule engine is handling complaints.`;
      banner.hidden = false;
    }
    const clock = $("#clock-pill");
    const off = state.status.clock.offset_hours;
    clock.hidden = !off;
    clock.className = "pill pill-clock";
    clock.textContent = off ? `Demo clock ${off > 0 ? "+" : ""}${fmtHours(off)}` : "";
  }

  // ------------------------------------------------------------ report tab
  const thread = $("#thread");
  const steps = $("#register-steps");
  const textarea = $("#complaint");
  const errorEl = $("#composer-error");

  function addMessage(who, text, photoUrl) {
    const div = document.createElement("div");
    div.className = `msg msg-${who}`;
    const label = who === "citizen" ? "You" : "AI Panchayat";
    div.innerHTML = `<span class="msg-who">${label}</span>${esc(text)}` +
      (photoUrl ? `<img class="msg-photo" src="${esc(photoUrl)}" alt="Photo you attached">` : "");
    thread.appendChild(div);
    div.scrollIntoView({ block: "nearest", behavior: "smooth" });
    return div;
  }

  function resetRegister() {
    steps.innerHTML = "";
    $("#stamp-slot").innerHTML = "";
    $("#messages-slot").innerHTML = "";
    $("#register-meta").textContent = "Agents are working on it.";
  }

  const pendingRows = {};
  function onAgentEvent(ev) {
    if (ev.type === "start") {
      const li = document.createElement("li");
      li.className = "step-pending";
      li.innerHTML = `<div class="step-agent"><span class="icon" aria-hidden="true">${esc(ev.icon)}</span>${esc(ev.agent)}</div>
        <p class="step-summary"><span class="dots">Working</span></p>`;
      steps.appendChild(li);
      pendingRows[ev.node] = li;
    } else if (ev.type === "step") {
      let li = pendingRows[ev.node];
      if (!li || !li.classList.contains("step-pending")) {
        li = document.createElement("li");
        steps.appendChild(li);
      }
      li.className = "";
      const ms = ev.ms ? `<span>${fmtMs(ev.ms)}</span>` : "";
      const tokens = ev.tokens ? `<span>${ev.tokens} tokens</span>` : "";
      const fb = ev.fallback ? `<p class="step-fallback">Gemini unavailable for this step (${esc(ev.fallback)}), so the offline engine decided.</p>` : "";
      li.innerHTML = `<div class="step-agent"><span class="icon" aria-hidden="true">${esc(ev.icon)}</span>${esc(ev.agent)}</div>
        <p class="step-summary">${esc(ev.summary)}</p>
        <div class="step-meta">${engineChip(ev.engine)}${ms}${tokens}</div>${fb}`;
    } else if (ev.type === "error") {
      const li = document.createElement("li");
      li.innerHTML = `<p class="step-summary form-error">${esc(ev.message)}</p>`;
      steps.appendChild(li);
    }
  }

  function renderResult(r) {
    const t = r.ticket;
    const cfg = state.config;
    if ((r.outcome === "created" || r.outcome === "merged") && t) {
      const merged = r.outcome === "merged";
      $("#stamp-slot").innerHTML = `<div class="stamp-wrap"><div class="stamp${merged ? " stamp-merged" : ""}" role="img"
          aria-label="${merged ? "Linked to existing ticket" : "Registered as ticket"} ${esc(t.id)}">
          <span class="stamp-kicker">${merged ? "Linked to existing complaint" : "Registered"} <span lang="ta">${merged ? "இணைக்கப்பட்டது" : "பதிவு செய்யப்பட்டது"}</span></span>
          <span class="stamp-id">${esc(t.id)}</span>
          <span class="stamp-date">${esc(fmtDate(t.updated_at || t.created_at, true))}</span></div></div>`;
      const cat = cfg.categories[t.category];
      const officer = cfg.officers[t.category];
      const pri = cfg.priorities[t.priority];
      const guard = r.triage && r.triage.guardrail ? `<dt>Guardrail</dt><dd>${esc(r.triage.guardrail)}</dd>` : "";
      const change = r.priority_change ? `<dt>Priority change</dt><dd>${esc(r.priority_change.from)} to ${esc(r.priority_change.to)}: ${esc(r.priority_change.reasons.join("; "))}</dd>` : "";
      $("#messages-slot").innerHTML = `
        <dl class="facts">
          <dt>Problem</dt><dd>${esc(cat.icon)} ${esc(cat.en)}${t.emergency ? ` <span class="badge b-emergency">Emergency</span>` : ""}</dd>
          <dt>Place</dt><dd>${esc(t.location || "Not given")}${t.ward ? `, Ward ${esc(t.ward)}` : ""}</dd>
          <dt>Priority</dt><dd>${priorityBadge(t.priority)} act within ${esc(pri.sla_en)}</dd>
          ${change}${guard}
          <dt>Officer</dt><dd>${esc(officer.name)}, ${esc(officer.role)}</dd>
          <dt>Citizens</dt><dd>${esc(t.report_count)} reported this</dd>
          <dt>Deadline</dt><dd>${esc(fmtDate(t.sla_due_at, true))}</dd>
        </dl>
        <div class="sms">
          <h3>SMS sent to the citizen</h3><div class="sms-bubble">${esc(r.messages.citizen || "")}</div>
          <h3>Work order sent to the officer</h3><div class="sms-bubble">${esc(r.messages.officer || "")}</div>
        </div>`;
    } else {
      $("#messages-slot").innerHTML = r.messages?.citizen
        ? `<div class="sms"><h3>Reply to the citizen</h3><div class="sms-bubble">${esc(r.messages.citizen)}</div></div>` : "";
    }
    const calls = r.llm_calls;
    const took = fmtMs(r.total_ms);
    const fb = r.fallbacks.length ? ` Offline fallback used for ${r.fallbacks.length} step${r.fallbacks.length > 1 ? "s" : ""}.` : "";
    const meta = calls ? `${calls} Gemini call${calls > 1 ? "s" : ""}, ${took} in total.${fb}` : `Decided by the offline engine in ${took}.${fb}`;
    $("#messages-slot").insertAdjacentHTML("beforeend", `<p class="run-meta">${esc(meta)}</p>`);
    $("#register-meta").textContent = r.outcome === "needs_info" ? "Waiting for the citizen's answer."
      : r.outcome === "rejected" ? "Nothing registered." : "Done. The officer has the work order.";
  }

  function setPending(text) {
    state.pending = text;
    $("#followup-banner").hidden = !text;
    $("#followup-text").textContent = text || "";
    $("#send-btn").textContent = text ? "Send answer" : "Send complaint";
  }

  async function sendComplaint(text) {
    text = (text || "").trim();
    showError(errorEl, "");
    if (!text) { showError(errorEl, "Describe the problem first."); textarea.focus(); return; }
    const btn = $("#send-btn");
    btn.disabled = true;
    const body = {
      text,
      name: $("#c-name").value.trim(),
      phone: $("#c-phone").value.trim(),
      ward: $("#c-ward").value || null,
    };
    if (state.image) body.image = { mime: state.image.mime, data: state.image.data };
    if (state.pending) body.followup_context = state.pending;
    addMessage("citizen", text, state.image?.url);
    const thinking = addMessage("agent", "Reading your complaint");
    thinking.classList.add("msg-thinking");
    resetRegister();
    const narrow = window.innerWidth < 980;
    let result = null;
    try {
      await streamPost("/api/complaints/stream", body, (ev) => {
        if (ev.type === "result") result = ev.result;
        else onAgentEvent(ev);
      });
      if (!result) throw new Error("The agent stopped before finishing. Please try again.");
      thinking.remove();
      addMessage("agent", result.messages.citizen || "Done.");
      renderResult(result);
      if (narrow) $(".register").scrollIntoView({ behavior: "smooth", block: "start" });
      if (result.outcome === "needs_info") {
        setPending(state.pending ? `${state.pending} ${text}` : text);
      } else {
        setPending(null);
      }
      textarea.value = "";
      clearPhoto();
      state.loaded.desk = false;
      refreshStatus();
    } catch (err) {
      thinking.remove();
      showError(errorEl, err.message);
      $("#register-meta").textContent = "Stopped.";
      onAgentEvent({ type: "error", message: err.message });
    } finally {
      btn.disabled = false;
    }
  }

  $("#composer").addEventListener("submit", (e) => { e.preventDefault(); sendComplaint(textarea.value); });
  textarea.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); sendComplaint(textarea.value); }
  });
  $("#followup-cancel").addEventListener("click", () => { setPending(null); textarea.focus(); });

  function renderSamples() {
    const box = $("#sample-chips");
    box.innerHTML = SAMPLES.map((s, i) => `<button type="button" class="chip" data-i="${i}">${esc(s.label)}</button>`).join("");
    box.addEventListener("click", (e) => {
      const b = e.target.closest(".chip");
      if (!b) return;
      setPending(null);
      const s = SAMPLES[Number(b.dataset.i)];
      textarea.value = s.text;
      sendComplaint(s.text);
    });
  }

  // voice input (browser speech recognition)
  (function setupVoice() {
    const btn = $("#mic-btn");
    const label = $("#mic-label");
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    let rec = null;
    let listening = false;
    btn.addEventListener("click", () => {
      showError(errorEl, "");
      if (!SR) { showError(errorEl, "Voice input works in Chrome or Edge. You can still type the complaint."); return; }
      if (listening) { rec.stop(); return; }
      rec = new SR();
      rec.lang = $("#mic-lang").value;
      rec.interimResults = true;
      rec.continuous = true;
      const base = textarea.value ? textarea.value.trimEnd() + " " : "";
      rec.onresult = (e) => {
        let txt = "";
        for (let i = 0; i < e.results.length; i++) txt += e.results[i][0].transcript;
        textarea.value = base + txt;
      };
      rec.onerror = (e) => {
        const msg = e.error === "not-allowed" ? "Microphone permission was blocked. Allow it in the browser and try again."
          : e.error === "no-speech" ? "No speech heard. Tap Speak and talk close to the microphone." : "Voice input stopped. Please try again or type.";
        showError(errorEl, msg);
      };
      rec.onend = () => { listening = false; btn.setAttribute("aria-pressed", "false"); label.textContent = "Speak"; };
      rec.start();
      listening = true;
      btn.setAttribute("aria-pressed", "true");
      label.textContent = "Stop";
    });
  })();

  // photo attach (resized in the browser before upload)
  function clearPhoto() {
    state.image = null;
    $("#photo-chip").hidden = true;
    $("#photo-input").value = "";
  }
  function resizeImage(file, max = 1024, quality = 0.82) {
    return new Promise((resolve, reject) => {
      const img = new Image();
      const url = URL.createObjectURL(file);
      img.onload = () => {
        const scale = Math.min(1, max / Math.max(img.width, img.height));
        const c = document.createElement("canvas");
        c.width = Math.round(img.width * scale);
        c.height = Math.round(img.height * scale);
        c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
        const dataUrl = c.toDataURL("image/jpeg", quality);
        URL.revokeObjectURL(url);
        resolve({ url: dataUrl, data: dataUrl.split(",")[1], mime: "image/jpeg" });
      };
      img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("That file could not be read as an image.")); };
      img.src = url;
    });
  }
  $("#photo-input").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    try {
      state.image = await resizeImage(file);
      $("#photo-preview").src = state.image.url;
      $("#photo-chip").hidden = false;
    } catch (err) { showError(errorEl, err.message); }
  });
  $("#photo-remove").addEventListener("click", clearPhoto);

  // track a ticket
  $("#track-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const id = $("#track-id").value.trim();
    const out = $("#track-result");
    if (!id) return;
    try {
      const d = await api(`/api/tickets/${encodeURIComponent(id)}`);
      const t = d.ticket;
      out.innerHTML = `<div class="track-card">
        <p><strong>${esc(t.id)}</strong> ${statusBadge(t.status, t.status_label)} ${priorityBadge(t.priority)}</p>
        <p class="t-title">${esc(t.title)}</p>
        <p class="t-sub">${esc(t.location)}${t.ward ? `, Ward ${esc(t.ward)}` : ""}. Officer: ${esc(t.officer.name)}. ${esc(t.report_count)} citizen(s) reported it.</p>
        <ul class="timeline">${d.events.map((ev) => `<li><span class="who">${esc(ev.actor)}</span>: ${esc(ev.action)}<br><span class="when">${esc(fmtDate(ev.ts))}</span></li>`).join("")}</ul>
      </div>`;
    } catch (err) {
      out.innerHTML = `<p class="form-error">${esc(err.message)}</p>`;
    }
  });

  // ------------------------------------------------------------ desk tab
  function renderStats(el, items) {
    el.innerHTML = items.map(([label, value, warn]) =>
      `<div class="${warn ? "warn" : ""}"><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join("");
  }
  function dueText(t) {
    if (t.status === "RESOLVED") return `<span class="t-sub">Resolved ${esc(fmtDate(t.resolved_at))}</span>`;
    if (t.overdue) return `<span class="due-late">Overdue by ${esc(fmtHours(t.due_in_hours))}</span>`;
    if (t.due_in_hours < 24) return `<span class="due-soon">Due in ${esc(fmtHours(t.due_in_hours))}</span>`;
    return `Due in ${esc(fmtHours(t.due_in_hours))}`;
  }

  async function loadDesk() {
    const params = new URLSearchParams();
    for (const [k, v] of new FormData($("#desk-filters"))) if (v) params.set(k, v);
    const rows = $("#desk-rows");
    try {
      const [st, data] = await Promise.all([api("/api/status"), api(`/api/tickets?${params}`)]);
      const c = st.counts;
      renderStats($("#desk-stats"), [["Open", c.open], ["Critical open", c.critical, c.critical > 0], ["Overdue", c.overdue, c.overdue > 0], ["Citizens heard", c.citizens], ["Resolved", c.resolved]]);
      if (!data.tickets.length) {
        rows.innerHTML = `<tr><td colspan="8" class="empty">No complaints match these filters. Clear a filter to see more.</td></tr>`;
        return;
      }
      rows.innerHTML = data.tickets.map((t) => `
        <tr tabindex="0" data-id="${esc(t.id)}">
          <td class="t-id">${esc(t.id)}${t.emergency ? `<br><span class="badge b-emergency">Emergency</span>` : ""}</td>
          <td><div class="t-title">${esc(t.category_icon)} ${esc(t.title)}</div><div class="t-sub">${esc(t.location)}</div></td>
          <td>${t.ward ? esc(t.ward) : "?"}</td>
          <td>${priorityBadge(t.priority)}</td>
          <td>${esc(t.report_count)}</td>
          <td>${esc(t.officer.name)}<div class="t-sub">${esc(t.officer.role)}</div></td>
          <td>${dueText(t)}</td>
          <td>${statusBadge(t.status, t.status_label)}${t.escalation_level ? `<div class="t-sub">Level ${esc(t.escalation_level)}</div>` : ""}</td>
        </tr>`).join("");
      state.loaded.desk = true;
    } catch (err) {
      rows.innerHTML = `<tr><td colspan="8" class="empty form-error">${esc(err.message)}</td></tr>`;
    }
  }
  let filterTimer = null;
  $("#desk-filters").addEventListener("input", () => { clearTimeout(filterTimer); filterTimer = setTimeout(loadDesk, 250); });
  $("#desk-filters").addEventListener("submit", (e) => e.preventDefault());
  $("#desk-rows").addEventListener("click", (e) => { const tr = e.target.closest("tr[data-id]"); if (tr) openTicket(tr.dataset.id); });
  $("#desk-rows").addEventListener("keydown", (e) => {
    const tr = e.target.closest("tr[data-id]");
    if (tr && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); openTicket(tr.dataset.id); }
  });
  $("#reset-demo").addEventListener("click", async () => {
    if (!confirm("Reset all complaints to the original demo data?")) return;
    try {
      await api("/api/admin/reset", { method: "POST", body: "{}" });
      await api("/api/clock", { method: "POST", body: JSON.stringify({ reset: true }) });
      refreshStatus();
      loadDesk();
    } catch (err) { alert(err.message); }
  });

  // ------------------------------------------------------------ drawer
  let lastFocus = null;
  function closeDrawer() {
    $("#drawer").hidden = true;
    if (lastFocus) lastFocus.focus();
  }
  $("#drawer").addEventListener("click", (e) => { if (e.target.closest("[data-close]")) closeDrawer(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#drawer").hidden) closeDrawer(); });

  async function openTicket(id) {
    lastFocus = document.activeElement;
    const body = $("#drawer-body");
    body.innerHTML = `<p class="dots">Loading</p>`;
    $("#drawer").hidden = false;
    try {
      renderTicket(await api(`/api/tickets/${encodeURIComponent(id)}`));
      $(".drawer-close").focus();
    } catch (err) { body.innerHTML = `<p class="form-error">${esc(err.message)}</p>`; }
  }

  function renderTicket(d) {
    const t = d.ticket;
    const cfg = state.config;
    const pri = cfg.priorities[t.priority];
    const isOpen = t.status !== "RESOLVED";
    const actions = isOpen
      ? `<div class="actions">
           ${t.status !== "IN_PROGRESS" ? `<button class="btn" data-status="IN_PROGRESS" type="button">Start work</button>` : ""}
           <button class="btn btn-primary" data-status="RESOLVED" type="button">Mark resolved</button>
         </div>
         <label class="sr-only" for="resolve-note">Resolution note</label>
         <textarea id="resolve-note" class="note-input" maxlength="500" placeholder="What was done? (sent to the citizens when you mark it resolved)"></textarea>`
      : `<div class="actions"><button class="btn" data-status="OPEN" type="button">Reopen</button></div>`;
    $("#drawer-body").innerHTML = `
      <p class="t-sub">${esc(t.id)} ${statusBadge(t.status, t.status_label)} ${priorityBadge(t.priority)} ${t.emergency ? `<span class="badge b-emergency">Emergency</span>` : ""}</p>
      <h2 id="drawer-title">${esc(t.category_icon)} ${esc(t.title)}</h2>
      <div class="summary-pair"><p>${esc(t.summary_en)}</p><p lang="ta">${esc(t.summary_ta)}</p></div>
      <dl class="kv">
        <dt>Place</dt><dd>${esc(t.location || "Not given")}${t.ward ? `, Ward ${esc(t.ward)}` : ""}</dd>
        <dt>Problem type</dt><dd>${esc(t.category_label)}</dd>
        <dt>Priority</dt><dd>${esc(t.priority)} ${esc(pri.label)}, act within ${esc(pri.sla_en)}${t.priority_reason ? `<br><span class="t-sub">${esc(t.priority_reason)}</span>` : ""}</dd>
        <dt>Officer</dt><dd>${esc(t.officer.name)}, ${esc(t.officer.role)}</dd>
        <dt>Deadline</dt><dd>${esc(fmtDate(t.sla_due_at, true))}${isOpen ? ` (${t.overdue ? "overdue by " : "in "}${esc(fmtHours(t.due_in_hours))})` : ""}</dd>
        <dt>Escalation</dt><dd>${t.escalation_level ? `Level ${esc(t.escalation_level)}` : "Not escalated"}</dd>
        <dt>Citizens</dt><dd>${esc(t.report_count)} reported this</dd>
        <dt>Registered</dt><dd>${esc(fmtDate(t.created_at, true))}</dd>
        <dt>Decided by</dt><dd>${esc(t.engine || "–")}</dd>
      </dl>
      ${t.officer_instructions ? `<h3>Steps for the officer</h3><p>${esc(t.officer_instructions)}</p>` : ""}
      ${t.resolution_note ? `<h3>Resolution note</h3><p>${esc(t.resolution_note)}</p>` : ""}
      ${actions}
      <p id="drawer-error" class="form-error" hidden></p>
      <h3>Citizen reports (${d.reports.length})</h3>
      <div class="note-list">${d.reports.map((r) => `<div class="note-item"><span class="when">${esc(r.citizen_name || "Citizen")}${r.phone ? ` ${esc(r.phone)}` : ""}, ${esc(fmtDate(r.created_at))}${r.has_photo ? ", with photo" : ""}</span>${esc(r.text)}${r.photo_observation ? `\nPhoto check: ${esc(r.photo_observation)}` : ""}</div>`).join("")}</div>
      <h3>Timeline</h3>
      <ul class="timeline">${d.events.map((ev) => `<li><span class="who">${esc(ev.actor)}</span>: ${esc(ev.action)}${ev.detail ? `<br>${esc(ev.detail)}` : ""}<br><span class="when">${esc(fmtDate(ev.ts))}</span></li>`).join("")}</ul>
      <h3>Messages sent (${d.notifications.length})</h3>
      <div class="note-list">${d.notifications.map((n) => `<div class="note-item"><span class="when">${esc(n.channel)} to ${esc(n.recipient)}, ${esc(fmtDate(n.ts))}</span>${esc(n.message)}</div>`).join("") || `<p class="t-sub">None yet.</p>`}</div>`;
    $$("#drawer-body [data-status]").forEach((b) => b.addEventListener("click", async () => {
      const note = $("#resolve-note")?.value.trim() || "";
      b.disabled = true;
      try {
        const updated = await api(`/api/tickets/${encodeURIComponent(t.id)}/status`, {
          method: "POST", body: JSON.stringify({ status: b.dataset.status, note, actor: t.officer.name }),
        });
        renderTicket(updated);
        loadDesk();
      } catch (err) {
        showError($("#drawer-error"), err.message);
        b.disabled = false;
      }
    }));
  }

  // ------------------------------------------------------------ escalations
  async function loadEscalations() {
    await refreshStatus();
    const c = state.status.clock;
    $("#clock-now").textContent = fmtDate(c.now, true);
    $("#clock-offset").textContent = c.offset_hours ? `${fmtHours(c.offset_hours)} ahead of real time (demo)` : "Real time";
    try {
      const log = await api("/api/escalation/log");
      $("#esc-log").innerHTML = log.events.length
        ? log.events.map((e) => `<li><span class="when">${esc(fmtDate(e.ts))}</span><strong>${esc(e.ticket_id)}</strong> ${esc(e.action)}${e.detail ? `: ${esc(e.detail)}` : ""}</li>`).join("")
        : `<li>No escalations yet. Move the clock forward and run the check.</li>`;
    } catch (err) { $("#esc-log").innerHTML = `<li class="form-error">${esc(err.message)}</li>`; }
  }
  $$("[data-advance]").forEach((b) => b.addEventListener("click", async () => {
    try {
      await api("/api/clock", { method: "POST", body: JSON.stringify({ advance_hours: Number(b.dataset.advance) }) });
      await loadEscalations();
    } catch (err) { alert(err.message); }
  }));
  $("#clock-reset").addEventListener("click", async () => {
    try {
      await api("/api/clock", { method: "POST", body: JSON.stringify({ reset: true }) });
      await loadEscalations();
    } catch (err) { alert(err.message); }
  });
  $("#sweep-btn").addEventListener("click", async () => {
    const btn = $("#sweep-btn");
    const out = $("#sweep-result");
    btn.disabled = true;
    out.innerHTML = `<p class="sweep dots">The Escalation Agent is checking every deadline</p>`;
    try {
      const r = await api("/api/escalation/sweep", { method: "POST", body: "{}" });
      const esc_items = r.escalated.map((e) => `<div class="esc-item">
          <p><strong>${esc(e.ticket_id)}</strong> ${priorityBadge(e.priority)} ${esc(e.title)}</p>
          <p>Overdue by ${esc(e.overdue)}, reported by ${esc(e.citizens)} citizen(s). Escalated to level ${esc(e.level)}: <strong>${esc(e.to.name)}</strong>, ${esc(e.to.role)}.</p>
          <p>${esc(e.note)}</p><p class="t-sub">Recommended action: ${esc(e.action)}</p></div>`).join("");
      const rem = r.reminders.map((m) => `<div class="esc-item reminder"><p><strong>${esc(m.ticket_id)}</strong> ${esc(m.title)}</p><p class="t-sub">Reminder sent to ${esc(m.officer)}: due in ${esc(m.due_in)}.</p></div>`).join("");
      out.innerHTML = `<div class="sweep">
        <p class="sweep-summary">${esc(r.summary)}.</p>
        <p class="step-meta">${engineChip(r.engine)}${r.fallback ? `<span class="step-fallback">Offline fallback: ${esc(r.fallback)}</span>` : ""}</p>
        <div class="esc-list">${esc_items}${rem}</div>
        ${!r.escalated.length && !r.reminders.length ? `<p>Nothing is late right now. Move the clock forward to see escalations.</p>` : ""}
      </div>`;
      loadEscalations();
      state.loaded.desk = false;
    } catch (err) {
      out.innerHTML = `<p class="form-error">${esc(err.message)}</p>`;
    } finally { btn.disabled = false; }
  });

  // ------------------------------------------------------------ insights
  function bars(el, rows) {
    const max = Math.max(1, ...rows.map((r) => r.value));
    el.innerHTML = `<div class="bars">${rows.map((r) => `
      <div class="bar-row"><span class="bar-label" title="${esc(r.label)}">${esc(r.label)}</span>
        <span class="bar-track"><span class="bar-fill" style="width:${(r.value / max) * 100}%"></span></span>
        <span class="bar-num">${esc(r.value)}</span></div>`).join("")}</div>`;
  }
  async function loadInsights() {
    try {
      const s = await api("/api/insights");
      const cfg = state.config;
      renderStats($("#insight-stats"), [
        ["Complaints", s.total_tickets], ["Open", s.open], ["Overdue", s.overdue, s.overdue > 0],
        ["Citizens heard", s.citizens_reached],
        ["Average time to resolve", s.avg_resolution_hours != null ? fmtHours(s.avg_resolution_hours) : "–"],
      ]);
      bars($("#chart-cat"), Object.entries(s.by_category)
        .map(([k, v]) => ({ label: `${cfg.categories[k].icon} ${cfg.categories[k].en}`, value: v.citizens }))
        .sort((a, b) => b.value - a.value));
      bars($("#chart-ward"), Object.entries(s.by_ward).map(([k, v]) => ({ label: k === "0" ? "Ward unknown" : `Ward ${k}`, value: v.citizens })));
      const cats = Object.keys(cfg.categories).filter((c) => Object.values(s.ward_category_citizens).some((w) => w[c]));
      const wards = Object.keys(s.ward_category_citizens);
      const max = Math.max(1, ...wards.flatMap((w) => Object.values(s.ward_category_citizens[w])));
      $("#heatmap").innerHTML = `<table class="heat"><thead><tr><th></th>${cats.map((c) => `<th title="${esc(cfg.categories[c].en)}">${esc(cfg.categories[c].icon)}<br>${esc(cfg.categories[c].short)}</th>`).join("")}</tr></thead>
        <tbody>${wards.map((w) => `<tr><th class="row-h">${w === "0" ? "Unknown" : `Ward ${esc(w)}`}</th>${cats.map((c) => {
          const n = s.ward_category_citizens[w][c] || 0;
          const a = n ? 0.12 + 0.75 * (n / max) : 0;
          return `<td style="background:rgba(30,91,60,${a.toFixed(2)});color:${a > 0.5 ? "#fff" : "var(--ink)"}">${n || ""}</td>`;
        }).join("")}</tr>`).join("")}</tbody></table>`;
      $("#hotspots").innerHTML = s.hotspots.length
        ? `<div class="hot-list">${s.hotspots.map((h) => `<div class="hot"><strong>${esc(h.street)}</strong><span class="t-sub">Ward ${esc(h.ward)}: ${esc(h.citizens)} citizens, ${esc(h.tickets)} ticket(s)</span><br>${h.categories.map((c) => esc(cfg.categories[c].icon + " " + cfg.categories[c].en)).join(", ")}</div>`).join("")}</div>`
        : `<p class="t-sub">No repeat hotspots yet.</p>`;
    } catch (err) { $("#insight-stats").innerHTML = `<p class="form-error">${esc(err.message)}</p>`; }
  }
  $("#brief-btn").addEventListener("click", async () => {
    const btn = $("#brief-btn");
    const slot = $("#brief-slot");
    btn.disabled = true;
    slot.innerHTML = `<div class="brief"><p class="dots">The Insights Agent is reading every complaint</p></div>`;
    try {
      const r = await api("/api/insights/brief", { method: "POST", body: "{}" });
      const b = r.brief;
      slot.innerHTML = `<article class="brief">
        <h2>${esc(b.headline)}</h2>
        <p class="meta">Gram Sabha brief, written by the Insights Agent. ${engineChip(r.engine)} ${esc(r.note)}</p>
        <h3>Priority works</h3>
        <div class="table-wrap"><table><thead><tr><th>Ward</th><th>Work</th><th>Why</th><th>Possible funding</th><th>When</th></tr></thead>
        <tbody>${b.priority_works.map((w) => `<tr><td>${w.ward ? esc(w.ward) : "All"}</td><td><strong>${esc(w.work)}</strong></td><td>${esc(w.reason)}</td><td>${esc(w.funding)}</td><td>${esc(w.timeline)}</td></tr>`).join("")}</tbody></table></div>
        <h3>Agenda for the Gram Sabha</h3><ol>${b.gram_sabha_agenda.map((a) => `<li>${esc(a)}</li>`).join("")}</ol>
        ${b.risks.length ? `<h3>Risks to watch</h3><ul>${b.risks.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
      </article>`;
    } catch (err) {
      slot.innerHTML = `<p class="form-error">${esc(err.message)}</p>`;
    } finally { btn.disabled = false; }
  });

  // ------------------------------------------------------------ test lab
  async function loadTests() {
    try {
      const d = await api("/api/scenarios");
      $("#test-rows").innerHTML = d.scenarios.map((s) => `
        <tr data-id="${esc(s.id)}" tabindex="0">
          <td class="t-id">${esc(s.id)}</td>
          <td><div class="t-title">${esc(s.name)}</div><div class="t-sub">${esc(s.input || "")}</div></td>
          <td class="res"><span class="t-sub">Not run</span></td><td class="out"></td><td class="eng"></td><td class="ms"></td>
        </tr>`).join("");
      state.loaded.tests = true;
    } catch (err) { $("#test-rows").innerHTML = `<tr><td colspan="6" class="form-error">${esc(err.message)}</td></tr>`; }
  }
  function toggleTestDetail(tr) {
    const next = tr.nextElementSibling;
    if (next && next.classList.contains("test-detail")) { next.remove(); return; }
    const r = state.lastTestResults.find((x) => x.id === tr.dataset.id);
    if (!r) return;
    const checks = r.checks.map((c) => `<div class="${c.ok ? "check-ok" : "check-bad"}">${c.ok ? "Pass" : "Fail"}: ${esc(c.field)} expected ${esc(JSON.stringify(c.expected))}, got ${esc(JSON.stringify(c.actual))}</div>`).join("");
    const trace = r.trace.map((s) => `<li>${esc(s.agent)}: ${esc(s.summary)}</li>`).join("");
    tr.insertAdjacentHTML("afterend", `<tr class="test-detail"><td></td><td colspan="5"><div class="checks">${checks}</div>${r.error ? `<p class="form-error">${esc(r.error)}</p>` : ""}<ol class="mini-trace">${trace}</ol></td></tr>`);
  }
  $("#test-rows").addEventListener("click", (e) => { const tr = e.target.closest("tr[data-id]"); if (tr) toggleTestDetail(tr); });
  $("#test-rows").addEventListener("keydown", (e) => {
    const tr = e.target.closest("tr[data-id]");
    if (tr && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); toggleTestDetail(tr); }
  });
  $("#run-tests").addEventListener("click", async () => {
    const btn = $("#run-tests");
    btn.disabled = true;
    if (!state.loaded.tests) await loadTests();
    $$(".test-detail").forEach((r) => r.remove());
    $$("#test-rows tr[data-id]").forEach((tr) => {
      $(".res", tr).innerHTML = `<span class="t-sub dots">Waiting</span>`;
      $(".out", tr).textContent = ""; $(".eng", tr).textContent = ""; $(".ms", tr).textContent = "";
    });
    const results = [];
    const started = performance.now();
    $("#test-summary").textContent = "Running the scenarios through the agents.";
    try {
      await streamPost("/api/scenarios/run", { mode: $("#test-mode").value }, (ev) => {
        if (ev.type !== "result") return;
        const r = ev.result;
        results.push(r);
        const tr = $(`#test-rows tr[data-id="${CSS.escape(r.id)}"]`);
        if (!tr) return;
        $(".res", tr).innerHTML = `<span class="badge ${r.passed ? "b-pass" : "b-fail"}">${r.passed ? "Pass" : "Fail"}</span>`;
        $(".out", tr).textContent = r.outcome || r.error;
        $(".eng", tr).innerHTML = r.engines.map(engineChip).join(" ");
        $(".ms", tr).innerHTML = `<span class="nowrap">${fmtMs(r.ms)}</span>`;
        $("#test-summary").textContent = `${results.filter((x) => x.passed).length} of ${results.length} passed so far.`;
      });
      state.lastTestResults = results;
      const passed = results.filter((x) => x.passed).length;
      const engines = [...new Set(results.flatMap((r) => r.engines))].join(", ");
      $("#test-summary").textContent = `${passed} of ${results.length} tests passed in ${((performance.now() - started) / 1000).toFixed(1)} seconds. Engines used: ${engines}. Click a row to see the checks and the agent trace.`;
      $("#download-tests").hidden = false;
      refreshStatus();
    } catch (err) {
      $("#test-summary").textContent = err.message;
    } finally { btn.disabled = false; }
  });
  $("#download-tests").addEventListener("click", () => {
    const blob = new Blob([JSON.stringify({ ran_at: new Date().toISOString(), results: state.lastTestResults }, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "ai-panchayat-test-results.json";
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  });

  // ------------------------------------------------------------ init
  async function init() {
    try {
      state.config = await api("/api/config");
    } catch (err) {
      $("#engine-pill").textContent = "Server not reachable";
      return;
    }
    const wardOpts = Object.keys(state.config.wards).map((w) => `<option value="${esc(w)}">Ward ${esc(w)} (${esc(state.config.wards[w].join(", "))})</option>`).join("");
    $("#c-ward").insertAdjacentHTML("beforeend", wardOpts);
    $("#desk-filters select[name=ward]").insertAdjacentHTML("beforeend", Object.keys(state.config.wards).map((w) => `<option value="${esc(w)}">Ward ${esc(w)}</option>`).join(""));
    $("#desk-filters select[name=category]").insertAdjacentHTML("beforeend",
      Object.entries(state.config.categories).map(([k, v]) => `<option value="${esc(k)}">${esc(v.en)}</option>`).join(""));
    renderSamples();
    await refreshStatus();
    showTab(location.hash.slice(1) || "report");
    setInterval(refreshStatus, 30000);
  }
  init();
})();
