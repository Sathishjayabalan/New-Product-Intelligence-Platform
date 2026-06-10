/* CapabilityOS SaaS frontend — hash-routed SPA, zero build step. */

const API = "/api/v1";
let tenant = localStorage.getItem("capos_tenant") || "demo";

const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path, opts = {}) {
  const res = await fetch(API + path, {
    headers: { "Content-Type": "application/json", "X-Tenant": tenant, ...(opts.headers || {}) },
    ...opts,
  });
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch (_) { /* not json */ }
    throw new Error(msg);
  }
  return res.json();
}

function toast(msg, type = "") {
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), 4200);
}

/* ------------------------------------------------------------ ui helpers */
const badge = (text, type = "") => `<span class="badge ${type ? "badge-" + type : ""}">${esc(text)}</span>`;
const statusBadge = (s) =>
  badge(s, { completed: "ok", validated: "ok", accepted: "ok", ready: "ok", processed: "ok",
             gated: "warn", needs_review: "warn", collecting: "warn", proposed: "",
             failed: "danger", rejected: "danger", running: "accent" }[s] || "");
const bar = (pct) => `<div class="bar"><div style="width:${Math.min(100, pct)}%"></div></div>`;
const kpi = (label, value, sub = "") => `
  <div class="kpi"><div class="kpi-label">${esc(label)}</div>
  <div class="kpi-value">${esc(String(value))}</div>
  ${sub ? `<div class="kpi-sub">${esc(sub)}</div>` : ""}</div>`;
const empty = (title, hint) => `<div class="empty"><b>${esc(title)}</b>${esc(hint)}</div>`;

function modal(title, bodyHtml, footHtml) {
  $("#modal-root").innerHTML = `
    <div class="modal-backdrop" id="modal-backdrop">
      <div class="modal">
        <div class="modal-head"><h3>${esc(title)}</h3>
          <button class="btn btn-ghost btn-sm" id="modal-close">✕</button></div>
        <div class="modal-body">${bodyHtml}</div>
        <div class="modal-foot">${footHtml}</div>
      </div>
    </div>`;
  $("#modal-close").onclick = closeModal;
  $("#modal-backdrop").onclick = (e) => { if (e.target.id === "modal-backdrop") closeModal(); };
}
const closeModal = () => { $("#modal-root").innerHTML = ""; };

/* ------------------------------------------------------------ topbar */
async function loadTopbar() {
  const known = ["demo", "acme-insurance", "nova-bank"];
  if (!known.includes(tenant)) known.push(tenant);
  $("#tenant-select").innerHTML = known
    .map((t) => `<option ${t === tenant ? "selected" : ""}>${esc(t)}</option>`).join("");
  try {
    const [contexts, me] = await Promise.all([api("/contexts"), api("/tenants/me")]);
    $("#ws-tenant").textContent = me.name;
    $("#plan-badge").textContent = me.plan_tier;
    $("#context-select").innerHTML = Object.entries(contexts)
      .map(([k, c]) => `<option value="${k}" ${k === me.industry_context ? "selected" : ""}>${esc(c.label)}</option>`)
      .join("");
  } catch (e) { toast(e.message, "danger"); }
}

$("#tenant-select").addEventListener("change", (e) => {
  tenant = e.target.value;
  localStorage.setItem("capos_tenant", tenant);
  loadTopbar();
  router();
});

$("#context-select").addEventListener("change", async (e) => {
  try {
    await api("/tenants/me/context", { method: "PUT", body: JSON.stringify({ industry_context: e.target.value }) });
    toast(`Industry context switched to ${e.target.value}`, "ok");
    router();
  } catch (err) { toast(err.message, "danger"); }
});

$("#run-pipeline").addEventListener("click", async () => {
  const btn = $("#run-pipeline");
  btn.disabled = true;
  try {
    const run = await api("/pipeline/run", { method: "POST", body: JSON.stringify({ auto_validate: true, enforce_gates: true }) });
    if (run.status === "completed") toast(`Pipeline run #${run.id} completed in ${run.latency_seconds}s`, "ok");
    else if (run.status === "gated") toast(`Run #${run.id} gated: ${run.gate_reason}`, "warn");
    else toast(`Run #${run.id}: ${run.gate_reason || run.status}`, "danger");
  } catch (e) { toast(e.message, "danger"); }
  btn.disabled = false;
  router();
});

/* ================================================================= views */
const VIEWS = {
  /* ------------------------------------------------------- dashboard */
  dashboard: {
    title: "Dashboard",
    async render(el) {
      const [summary, evals] = await Promise.all([api("/pipeline/summary"), api("/evals/dashboard")]);
      const c = summary.counts;
      const evalEntries = Object.entries(evals).filter(([, e]) => e.passed !== null);
      const passRate = evalEntries.length
        ? Math.round((evalEntries.filter(([, e]) => e.passed).length / evalEntries.length) * 100) : 0;

      const FUNNEL = [
        ["signals", "Signals"], ["insight_cards", "Insights"], ["clusters", "Clusters"],
        ["hypotheses", "Hypotheses"], ["validated_hypotheses", "Validated"], ["feature_cards", "Cards"],
      ];
      const max = Math.max(1, ...FUNNEL.map(([k]) => c[k]));
      const run = summary.latest_run;

      el.innerHTML = `
        <div class="kpis">
          ${kpi("Active signals", c.signals, `${c.duplicates_blocked} duplicates blocked`)}
          ${kpi("Behavioral clusters", c.clusters, `${c.hypotheses} hypotheses generated`)}
          ${kpi("Validated features", c.validated_hypotheses, `${c.briefs} briefs · ${c.surveys} surveys`)}
          ${kpi("Backlog-ready cards", c.feature_cards, `${c.ujms} journey maps`)}
        </div>

        <div class="panel">
          <div class="panel-head"><h2>Signal-to-feature funnel</h2>
            <span class="hint">${summary.bottleneck ? "⚠ bottleneck at " + summary.bottleneck.stage : "flow healthy"}</span></div>
          <div class="funnel">
            ${FUNNEL.map(([k, label]) => `
              <div class="fstage ${summary.bottleneck && summary.bottleneck.stage === k ? "bottleneck" : ""}">
                <div class="fv">${c[k]}</div><div class="fl">${label}</div>
                <div class="fbar"><div style="width:${(c[k] / max) * 100}%"></div></div>
              </div>`).join("")}
          </div>
        </div>

        <div class="row-2">
          <div class="panel">
            <div class="panel-head"><h2>Latest pipeline run</h2>
              ${run ? statusBadge(run.status) : ""}</div>
            ${!run ? empty("No runs yet", "Ingest signals, then hit Run pipeline.") : `
              <div class="timeline">
                ${(run.stage_log || []).map((l, i, arr) => `
                  <div class="tl-item">
                    <div class="tl-rail"><div class="tl-dot ${l.eval_failures && l.eval_failures.length ? "fail" : ""}"></div>
                      ${i < arr.length - 1 ? '<div class="tl-line"></div>' : ""}</div>
                    <div class="tl-body"><b>${esc(l.stage)}</b>
                      <span class="muted">${Object.entries(l).filter(([k]) => !["stage", "at", "eval_failures"].includes(k))
                        .map(([k, v]) => `${k.replace(/_/g, " ")}: ${esc(JSON.stringify(v))}`).join(" · ")}</span>
                      ${(l.eval_failures || []).map((f) => `<span class="badge badge-danger">${esc(f)}</span>`).join(" ")}
                    </div>
                  </div>`).join("")}
              </div>
              ${run.gated ? `<p style="margin-top:8px">${badge("flagged for human review", "warn")} <span class="muted">${esc(run.gate_reason)}</span></p>` : ""}
              <p class="muted" style="margin-top:10px">Run #${run.id} · ${run.latency_seconds}s end-to-end</p>`}
          </div>

          <div class="panel">
            <div class="panel-head"><h2>Quality intelligence</h2>
              <a href="#/evals" class="btn btn-ghost btn-sm">View all →</a></div>
            <div class="ring-wrap">
              <div class="ring" style="background: conic-gradient(var(${passRate === 100 ? "--ok" : passRate >= 75 ? "--accent" : "--warn"}) ${passRate * 3.6}deg, var(--surface-3) 0deg)">
                <span>${passRate}%</span>
              </div>
              <div>
                <p style="font-weight:600">${evalEntries.filter(([, e]) => e.passed).length} / ${evalEntries.length} evals passing</p>
                <p class="muted" style="font-size:12px">Gate evals block pipeline progression below threshold.</p>
                ${evalEntries.filter(([, e]) => !e.passed).slice(0, 3)
                  .map(([id, e]) => `<p style="margin-top:5px">${badge(id + " " + e.metric, "danger")}</p>`).join("")}
              </div>
            </div>
          </div>
        </div>`;
    },
  },

  /* --------------------------------------------------------- signals */
  signals: {
    title: "Signals",
    async render(el) {
      const signals = await api("/signals?include_duplicates=true&limit=200");
      el.innerHTML = `
        <div class="panel" style="margin-top:0">
          <div class="panel-head"><h2>Signal Ingestion Hub</h2>
            <div style="display:flex;gap:8px">
              <button class="btn btn-sm" id="btn-upload">⬆ Upload file</button>
              <button class="btn btn-primary btn-sm" id="btn-ingest">+ New signal</button>
            </div></div>
          <p class="muted" style="font-size:12px">8 ingestion methods available — REST, batch, file upload, webhooks, NDJSON streaming, connector sync (Salesforce, Zendesk, SAP…), email, feed pull. See <a href="/docs" style="color:#9f93ff">API docs</a>.</p>
        </div>
        ${signals.length ? `
        <div class="table-wrap" style="margin-top:16px">
          <table><thead><tr><th>Signal</th><th>Source</th><th>Tags</th><th>Quality</th><th>Status</th></tr></thead>
          <tbody>${signals.map((s) => `
            <tr>
              <td style="max-width:430px">${esc(s.content.slice(0, 160))}${s.content.length > 160 ? "…" : ""}</td>
              <td>${badge(s.source_type, "accent")}<div class="muted" style="font-size:11px;margin-top:3px">${esc(s.source_name || "—")}</div></td>
              <td>${(s.tags || []).slice(0, 3).map((t) => badge(t)).join(" ")}</td>
              <td style="min-width:90px">${(s.quality_score * 100).toFixed(0)}%${bar(s.quality_score * 100)}</td>
              <td>${s.is_duplicate ? badge("duplicate", "warn") : statusBadge(s.status)}</td>
            </tr>`).join("")}</tbody></table>
        </div>` : empty("No signals yet", "Ingest your first customer signal to start the pipeline.")}`;

      $("#btn-ingest").onclick = () => {
        modal("Ingest signal", `
          <label>Source type
            <select id="m-source">${["voc", "crm", "support_ticket", "survey", "api", "stream", "conference", "social", "email"]
              .map((s) => `<option>${s}</option>`).join("")}</select></label>
          <label>Source name <input type="text" id="m-name" placeholder="e.g. NPS verbatims" /></label>
          <label>Content <textarea id="m-content" rows="4" placeholder="Paste the customer signal…"></textarea></label>`,
          `<button class="btn" id="m-cancel">Cancel</button>
           <button class="btn btn-primary" id="m-save">Ingest</button>`);
        $("#m-cancel").onclick = closeModal;
        $("#m-save").onclick = async () => {
          try {
            const sig = await api("/signals", { method: "POST", body: JSON.stringify({
              content: $("#m-content").value, source_type: $("#m-source").value, source_name: $("#m-name").value }) });
            toast(sig.is_duplicate ? "Duplicate blocked by dedup engine" : `Ingested — tagged [${sig.tags.join(", ")}]`, sig.is_duplicate ? "warn" : "ok");
            closeModal(); router();
          } catch (e) { toast(e.message, "danger"); }
        };
      };

      $("#btn-upload").onclick = () => {
        modal("Upload signal file", `
          <p class="muted" style="font-size:12px">Supported: .csv (content column), .json, .jsonl, .ndjson, .txt (one signal per line)</p>
          <input type="file" id="m-file" accept=".csv,.json,.jsonl,.ndjson,.txt" />`,
          `<button class="btn" id="m-cancel">Cancel</button>
           <button class="btn btn-primary" id="m-save">Upload</button>`);
        $("#m-cancel").onclick = closeModal;
        $("#m-save").onclick = async () => {
          const file = $("#m-file").files[0];
          if (!file) return toast("Choose a file first", "warn");
          const fd = new FormData();
          fd.append("file", file);
          try {
            const res = await fetch(API + "/signals/upload", { method: "POST", headers: { "X-Tenant": tenant }, body: fd });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || res.statusText);
            toast(`${data.ingested} ingested, ${data.duplicates} duplicates, ${data.errors.length} errors`, "ok");
            closeModal(); router();
          } catch (e) { toast(e.message, "danger"); }
        };
      };
    },
  },

  /* -------------------------------------------------------- insights */
  insights: {
    title: "Insight Cards",
    async render(el) {
      const cards = await api("/insight-cards?limit=200");
      el.innerHTML = !cards.length
        ? empty("No insight cards", "Ingest signals — each one becomes a grounded insight card.")
        : `<div class="grid">${cards.map((c) => `
            <div class="card">
              <h3>${esc(c.title)}</h3>
              <p>${esc(c.summary)}</p>
              <div class="card-meta">
                ${badge(c.category, "accent")}
                ${badge("trend: " + c.trend_vector, c.trend_vector === "rising" ? "warn" : "")}
                ${badge("strength " + c.strength_score)}
                ${badge("→ " + c.engine_route)}
              </div>
            </div>`).join("")}</div>`;
    },
  },

  /* ------------------------------------------------------ behavioral */
  behavioral: {
    title: "Clusters & Hypotheses",
    async render(el) {
      const [clusters, hyps] = await Promise.all([api("/clusters"), api("/hypotheses")]);
      el.innerHTML = `
        <div class="section-title">Behavioral clusters
          <span class="hint">segmentation with need states &amp; personas</span>
          <button class="btn btn-sm" id="btn-recluster" style="margin-left:auto">↻ Re-cluster</button></div>
        ${!clusters.length ? empty("No clusters yet", "Run the pipeline to segment your signal base.") : `
        <div class="grid">${clusters.map((c) => `
          <div class="card">
            <h3>${esc(c.name)}</h3>
            <p class="sub">Persona: <b>${esc(c.personas[0]?.name || "—")}</b></p>
            <p style="margin-top:7px">${c.need_states.map((n) => "· " + esc(n)).join("<br>")}</p>
            <div class="card-meta">
              ${badge(c.size + " signals")}
              ${badge("stability " + c.stability_score, c.stability_score >= 0.75 ? "ok" : "warn")}
              ${c.attributes.slice(0, 3).map((a) => badge(a)).join("")}
            </div>
          </div>`).join("")}</div>`}

        <div class="section-title">Feature hypotheses
          <span class="hint">ranked &amp; confidence-scored — decisions gate the Building engine</span></div>
        ${!hyps.length ? empty("No hypotheses yet", "Clusters generate ranked feature hypotheses.") : `
        <div class="grid">${hyps.map((h) => `
          <div class="card">
            <h3>#${h.rank} ${esc(h.title)}</h3>
            <p>${esc(h.description)}</p>
            <p class="sub"><i>${esc(h.rationale)}</i></p>
            ${bar(h.confidence * 100)}
            <div class="card-meta">
              ${badge("confidence " + (h.confidence * 100).toFixed(0) + "%", "accent")}
              ${statusBadge(h.status)}
              ${(h.risk_flags || []).map((r) => badge(r, "warn")).join("")}
            </div>
            ${h.status === "proposed" ? `
            <div class="card-actions">
              <button class="btn btn-ok btn-sm" data-decide="${h.id}" data-ok="1">✓ Validate</button>
              <button class="btn btn-danger btn-sm" data-decide="${h.id}" data-ok="0">✕ Reject</button>
            </div>` : h.status === "validated" ? `
            <div class="card-actions">
              <button class="btn btn-sm" data-ujm="${h.id}">⤵ Generate UJM</button>
            </div>` : ""}
          </div>`).join("")}</div>`}`;

      $("#btn-recluster")?.addEventListener("click", async () => {
        try { await api("/clusters/run", { method: "POST", headers: { "X-Role": "behavioral_scientist" } });
          await api("/hypotheses/generate", { method: "POST", headers: { "X-Role": "behavioral_scientist" } });
          toast("Clusters and hypotheses regenerated", "ok"); router();
        } catch (e) { toast(e.message, "danger"); }
      });
      el.querySelectorAll("[data-decide]").forEach((b) => b.onclick = async () => {
        try { await api(`/hypotheses/${b.dataset.decide}/decision`, { method: "POST", body: JSON.stringify({ accepted: b.dataset.ok === "1" }) });
          toast(b.dataset.ok === "1" ? "Hypothesis validated" : "Hypothesis rejected", "ok"); router();
        } catch (e) { toast(e.message, "danger"); }
      });
      el.querySelectorAll("[data-ujm]").forEach((b) => b.onclick = async () => {
        try { await api(`/ujms/generate/${b.dataset.ujm}`, { method: "POST" });
          toast("UJM generated — see UJMs & Feature Cards", "ok");
        } catch (e) { toast(e.message, "danger"); }
      });
    },
  },

  /* ------------------------------------------------------- prototype */
  prototype: {
    title: "Briefs & Surveys",
    async render(el) {
      const [briefs, surveys] = await Promise.all([api("/briefs"), api("/surveys")]);
      el.innerHTML = `
        <div class="section-title">Prototype briefs <span class="hint">10-field template, E-08 audited</span></div>
        ${!briefs.length ? empty("No briefs yet", "Top hypotheses generate prototype briefs on pipeline runs.") : `
        <div class="grid">${briefs.map((b) => `
          <div class="card">
            <h3>${esc(b.fields.objective || "Brief #" + b.id)}</h3>
            <p>${esc(b.fields.hypothesis_statement || "")}</p>
            <dl class="kv">
              <dt>Method</dt><dd>${esc(b.fields.test_method || "—")}</dd>
              <dt>Sample size</dt><dd>${esc(String(b.fields.sample_size ?? "—"))}</dd>
              <dt>Duration</dt><dd>${esc(String(b.fields.duration_weeks ?? "—"))} weeks</dd>
              <dt>Owner</dt><dd>${esc(b.fields.owner_role || "—")}</dd>
            </dl>
            <div class="card-meta">
              ${badge("completeness " + (b.completeness_score * 100).toFixed(0) + "%", b.completeness_score >= 0.9 ? "ok" : "warn")}
              ${statusBadge(b.status)}
            </div>
          </div>`).join("")}</div>`}

        <div class="section-title">Validation surveys <span class="hint">power-checked cohort tests</span></div>
        ${!surveys.length ? empty("No surveys yet", "Each brief gets an auto-generated validation survey.") : `
        <div class="grid">${surveys.map((s) => `
          <div class="card">
            <h3>Survey #${s.id} — ${esc(s.cohort.persona || "core cohort")}</h3>
            <p>${s.questions.slice(0, 3).map((q) => "· " + esc(q.text)).join("<br>")}${s.questions.length > 3 ? "<br><span class='muted'>+" + (s.questions.length - 3) + " more</span>" : ""}</p>
            ${bar(Math.min(100, (s.responses_collected / s.min_sample_size) * 100))}
            <div class="card-meta">
              ${badge(s.responses_collected + " / " + s.min_sample_size + " responses")}
              ${s.statistically_significant ? badge("significant", "ok") : badge("collecting", "warn")}
            </div>
            <div class="card-actions">
              <button class="btn btn-sm" data-resp="${s.id}" data-n="50">+50 responses</button>
              <button class="btn btn-sm" data-resp="${s.id}" data-n="${s.min_sample_size}">Reach sample</button>
            </div>
          </div>`).join("")}</div>`}`;

      el.querySelectorAll("[data-resp]").forEach((b) => b.onclick = async () => {
        try { await api(`/surveys/${b.dataset.resp}/responses`, { method: "POST", body: JSON.stringify({ count: Number(b.dataset.n) }) });
          toast("Responses recorded", "ok"); router();
        } catch (e) { toast(e.message, "danger"); }
      });
    },
  },

  /* -------------------------------------------------------- building */
  building: {
    title: "UJMs & Feature Cards",
    async render(el) {
      const [ujms, cards] = await Promise.all([api("/ujms"), api("/feature-cards")]);
      el.innerHTML = `
        <div class="section-title">User journey maps <span class="hint">generated on industry journey stages</span></div>
        ${!ujms.length ? empty("No journey maps yet", "Validated hypotheses generate UJMs in the Building engine.") : ujms.map((u) => `
          <div class="panel" style="margin-top:12px">
            <div class="panel-head"><h2>UJM #${u.id} — ${esc(u.persona)}</h2>
              <div style="display:flex;gap:7px;align-items:center">
                ${badge("completeness " + (u.completeness_score * 100).toFixed(0) + "%", u.completeness_score >= 0.85 ? "ok" : "warn")}
                ${statusBadge(u.status)}
                ${u.status === "generated" ? `<button class="btn btn-ok btn-sm" data-accept="${u.id}">✓ Accept</button>` : ""}
                <button class="btn btn-sm" data-cards="${u.id}">⤵ Cards</button>
                <button class="btn btn-ghost btn-sm" data-export="${u.id}">Export ▾</button>
              </div></div>
            <div class="ujm-lanes">
              ${u.lanes.map((l) => `
                <div class="lane"><h4>${esc(l.stage)}</h4><ul>
                  ${(l.touchpoints || []).slice(0, 2).map((t) => `<li>${esc(t)}</li>`).join("")}
                  ${(l.pain_points || []).map((p) => `<li class="pain">⚠ ${esc(p)}</li>`).join("")}
                  ${(l.feature_interventions || []).map((f) => `<li class="feature">★ ${esc(f)}</li>`).join("")}
                </ul></div>`).join("")}
            </div>
          </div>`).join("")}

        <div class="section-title">Feature cards <span class="hint">backlog-ready, E-13 quality-scored</span></div>
        ${!cards.length ? empty("No feature cards yet", "UJM intervention lanes become Agile feature cards.") : `
        <div class="grid">${cards.map((c) => `
          <div class="card">
            <h3>${esc(c.title)}</h3>
            <p>${esc(c.user_story)}</p>
            <p class="sub">${c.acceptance_criteria.map((a) => "✓ " + esc(a)).join("<br>")}</p>
            <div class="card-meta">
              ${badge(c.story_points + " pts", "accent")}
              ${badge("quality " + c.quality_score + "/5", c.quality_score >= 4 ? "ok" : "warn")}
              ${badge(c.status)}
            </div>
          </div>`).join("")}</div>`}`;

      el.querySelectorAll("[data-accept]").forEach((b) => b.onclick = async () => {
        try { await api(`/ujms/${b.dataset.accept}/review`, { method: "POST", body: JSON.stringify({ accepted: true }) });
          toast("UJM accepted (feeds E-12)", "ok"); router();
        } catch (e) { toast(e.message, "danger"); }
      });
      el.querySelectorAll("[data-cards]").forEach((b) => b.onclick = async () => {
        try { const cards = await api(`/feature-cards/generate/${b.dataset.cards}`, { method: "POST" });
          toast(`${cards.length} feature cards generated`, "ok"); router();
        } catch (e) { toast(e.message, "danger"); }
      });
      el.querySelectorAll("[data-export]").forEach((b) => b.onclick = () => {
        modal("Export UJM #" + b.dataset.export, `
          <p class="muted" style="font-size:12px">Connector payloads (F-08):</p>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            ${["miro", "figma", "pdf", "jira"].map((f) => `<button class="btn" data-fmt="${f}">${f}</button>`).join("")}
          </div><pre id="m-export" style="max-height:240px;overflow:auto;font-size:11px;background:var(--surface-2);border-radius:7px;padding:10px"></pre>`,
          `<button class="btn" id="m-cancel">Close</button>`);
        $("#m-cancel").onclick = closeModal;
        document.querySelectorAll("[data-fmt]").forEach((fb) => fb.onclick = async () => {
          const payload = await api(`/ujms/${b.dataset.export}/export/${fb.dataset.fmt}`);
          $("#m-export").textContent = JSON.stringify(payload, null, 2);
        });
      });
    },
  },

  /* ----------------------------------------------------------- evals */
  evals: {
    title: "Eval Catalogue",
    async render(el) {
      const dash = await api("/evals/dashboard");
      el.innerHTML = `
        <div class="panel" style="margin-top:0">
          <div class="panel-head"><h2>Quality Intelligence — E-01 … E-16</h2>
            <button class="btn btn-primary btn-sm" id="btn-runall">▶ Run all evals</button></div>
          <p class="muted" style="font-size:12px">Gate evals (⛔) block pipeline progression below threshold and flag the run for human review. Judge model is configured separately from the generation model.</p>
        </div>
        <div class="table-wrap" style="margin-top:16px">
          <table><thead><tr><th>Eval</th><th>Engine</th><th>Metric</th><th>Method</th><th>Target</th><th>Latest</th><th>Gate</th><th>Status</th></tr></thead>
          <tbody>${Object.entries(dash).map(([id, e]) => `
            <tr>
              <td><b>${id}</b></td>
              <td>${badge(e.engine)}</td>
              <td>${esc(e.metric)}</td>
              <td class="muted">${esc(e.method)}</td>
              <td class="muted">${esc(e.target)}</td>
              <td>${e.latest_score ?? "—"}</td>
              <td>${e.gate ? "⛔" : '<span class="muted">monitor</span>'}</td>
              <td>${e.passed === null ? badge("not run") : e.passed ? badge("PASS", "ok") : badge("FAIL", "danger")}</td>
            </tr>`).join("")}</tbody></table>
        </div>`;
      $("#btn-runall").onclick = async () => {
        $("#btn-runall").disabled = true;
        try {
          for (const engine of ["listening", "behavioral", "prototype", "building", "platform"])
            await api(`/evals/run-engine/${engine}`, { method: "POST" });
          toast("All 16 evals executed", "ok");
        } catch (e) { toast(e.message, "danger"); }
        router();
      };
    },
  },

  /* ---------------------------------------------------------- models */
  models: {
    title: "Model Qualification",
    async render(el) {
      const [cat, board] = await Promise.all([api("/model-evals/catalogue"), api("/model-evals/scoreboard")]);
      const suiteIds = Object.keys(cat.suites);
      el.innerHTML = `
        <div class="panel" style="margin-top:0">
          <div class="panel-head"><h2>LLM layer benchmarks — S-CLS … S-JDG</h2>
            <button class="btn btn-primary btn-sm" id="btn-benchmark">▶ Benchmark all models</button></div>
          <p class="muted" style="font-size:12px">Generation model: ${badge(cat.generation_model, "accent")} · Judge model: ${badge(cat.judge_model, "accent")} — remote suites are <i>skipped</i> (never silently passed) without an API key.</p>
        </div>
        ${!Object.keys(board).length ? empty("No benchmarks yet", "Run the benchmark to qualify models against the 7 task suites.") : `
        <div class="table-wrap" style="margin-top:16px">
          <table><thead><tr><th>Model</th>${suiteIds.map((s) => `<th title="${esc(cat.suites[s].name)}">${s}</th>`).join("")}<th>Qualified</th></tr></thead>
          <tbody>${Object.entries(board).map(([model, suites]) => `
            <tr><td><b>${esc(model)}</b></td>
              ${suiteIds.map((s) => {
                const r = suites[s];
                if (!r) return "<td>—</td>";
                if (r.status === "skipped") return `<td>${badge("skip", "warn")}</td>`;
                if (r.status === "error") return `<td>${badge("err", "danger")}</td>`;
                return `<td>${badge(r.score, r.passed ? "ok" : "danger")}</td>`;
              }).join("")}
              <td>${suites.qualified ? badge("QUALIFIED", "ok") : badge("not qualified", "warn")}</td>
            </tr>`).join("")}</tbody></table>
        </div>`}
        <div class="section-title">Suite definitions</div>
        <div class="table-wrap">
          <table><thead><tr><th>Suite</th><th>Engine</th><th>Name</th><th>Checks</th><th>Pass bar</th></tr></thead>
          <tbody>${Object.entries(cat.suites).map(([id, s]) => `
            <tr><td><b>${id}</b></td><td>${badge(s.engine)}</td><td>${esc(s.name)}</td>
            <td class="muted">${esc(s.checks)}</td><td>≥ ${s.threshold}</td></tr>`).join("")}</tbody></table>
        </div>`;
      $("#btn-benchmark").onclick = async () => {
        $("#btn-benchmark").disabled = true;
        try { await api("/model-evals/run", { method: "POST", body: JSON.stringify({}) });
          toast("Benchmark complete", "ok");
        } catch (e) { toast(e.message, "danger"); }
        router();
      };
    },
  },

  /* -------------------------------------------------------- settings */
  settings: {
    title: "Settings",
    async render(el) {
      const [me, contexts, roles, connectors] = await Promise.all([
        api("/tenants/me"), api("/contexts"), api("/roles"), api("/connectors")]);
      el.innerHTML = `
        <div class="kpis">
          ${kpi("Workspace", me.name, "slug: " + me.slug)}
          ${kpi("Plan", me.plan_tier, "Annual subscription per seat")}
          ${kpi("Industry context", contexts[me.industry_context]?.label || me.industry_context, "switch in the top bar")}
        </div>
        <div class="section-title">Industry contexts <span class="hint">F-09 — domain schema, taxonomy &amp; prompt context per industry</span></div>
        <div class="grid">${Object.entries(contexts).map(([k, c]) => `
          <div class="card ${k === me.industry_context ? "active" : ""}">
            <h3>${esc(c.label)} ${k === me.industry_context ? badge("active", "ok") : ""}</h3>
            <p class="sub">Journey: ${c.journey_stages.join(" → ")}</p>
            <div class="card-meta">${badge(c.priority + " priority", c.priority === "primary" ? "accent" : "")}
              ${c.signal_taxonomy.slice(0, 4).map((t) => badge(t)).join("")}</div>
          </div>`).join("")}</div>
        <div class="section-title">NS19 roles <span class="hint">F-11 — RBAC per engine, sent via X-Role header</span></div>
        <div class="table-wrap">
          <table><thead><tr><th>Role</th><th>Label</th><th>Engines</th></tr></thead>
          <tbody>${Object.entries(roles).map(([k, r]) => `
            <tr><td><code>${esc(k)}</code></td><td>${esc(r.label)}</td>
            <td>${r.engines.map((e) => badge(e)).join(" ")}</td></tr>`).join("")}</tbody></table>
        </div>
        <div class="section-title">Connectors <span class="hint">native-shape sync endpoints</span></div>
        <div class="grid">${Object.entries(connectors).map(([k, c]) => `
          <div class="card"><h3>${esc(k)}</h3>
            <p class="sub">POST /api/v1/connectors/${esc(k)}/sync — expects list under <code>${esc(c.expects_list_under)}</code></p>
            <div class="card-meta">${badge(c.kind, "accent")}</div></div>`).join("")}</div>`;
    },
  },

  /* ----------------------------------------------------------- audit */
  audit: {
    title: "Audit Log",
    async render(el) {
      const log = await api("/audit-log?limit=100");
      el.innerHTML = !log.length
        ? empty("Audit log is empty", "Engine actions, context switches and pipeline runs are recorded here.")
        : `<div class="table-wrap" style="margin-top:0">
          <table><thead><tr><th>When</th><th>Actor</th><th>Action</th><th>Detail</th></tr></thead>
          <tbody>${log.map((a) => `
            <tr><td class="muted" style="white-space:nowrap">${new Date(a.created_at + "Z").toLocaleString()}</td>
            <td>${badge(a.actor)}</td><td><b>${esc(a.action)}</b></td>
            <td class="muted">${esc(a.detail)}</td></tr>`).join("")}</tbody></table>
        </div>`;
    },
  },
};

/* ---------------------------------------------------------------- router */
async function router() {
  const view = (location.hash.replace(/^#\//, "") || "dashboard").split("?")[0];
  const route = VIEWS[view] || VIEWS.dashboard;
  document.querySelectorAll("#nav a").forEach((a) =>
    a.classList.toggle("active", a.dataset.view === view));
  $("#page-title").textContent = route.title;
  const el = $("#view");
  try {
    await route.render(el);
  } catch (e) {
    el.innerHTML = empty("Failed to load", e.message);
  }
}

window.addEventListener("hashchange", router);
loadTopbar().then(router);
