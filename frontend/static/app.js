const API = "/api/v1";
let tenant = localStorage.getItem("capos_tenant") || "demo";

const $ = (sel) => document.querySelector(sel);
const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path, opts = {}) {
  const res = await fetch(API + path, {
    headers: { "Content-Type": "application/json", "X-Tenant": tenant, ...(opts.headers || {}) },
    ...opts,
  });
  if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
  return res.json();
}

/* ---------------------------------------------------------------- tabs -- */
document.querySelectorAll("#tabs button").forEach((btn) =>
  btn.addEventListener("click", () => {
    document.querySelectorAll("#tabs button, .tab").forEach((el) => el.classList.remove("active"));
    btn.classList.add("active");
    $("#tab-" + btn.dataset.tab).classList.add("active");
  })
);

/* ------------------------------------------------------- tenant/context -- */
async function loadTenants() {
  // No list endpoint by design (tenant isolation); offer known demo tenants.
  const known = ["demo", "acme-insurance", "nova-bank"];
  $("#tenant-select").innerHTML = known
    .map((t) => `<option ${t === tenant ? "selected" : ""}>${t}</option>`)
    .join("");
  const contexts = await api("/contexts");
  const me = await api("/tenants/me");
  $("#context-select").innerHTML = Object.entries(contexts)
    .map(([k, c]) => `<option value="${k}" ${k === me.industry_context ? "selected" : ""}>${esc(c.label)}</option>`)
    .join("");
}
$("#tenant-select").addEventListener("change", (e) => {
  tenant = e.target.value;
  localStorage.setItem("capos_tenant", tenant);
  refreshAll();
});
$("#context-select").addEventListener("change", async (e) => {
  await api("/tenants/me/context", { method: "PUT", body: JSON.stringify({ industry_context: e.target.value }) });
  refreshAll();
});

/* ------------------------------------------------------------- pipeline -- */
const FUNNEL_STAGES = [
  ["signals", "Signals"], ["insight_cards", "Insight Cards"], ["clusters", "Clusters"],
  ["hypotheses", "Hypotheses"], ["validated_hypotheses", "Validated"],
  ["briefs", "Briefs"], ["ujms", "UJMs"], ["feature_cards", "Feature Cards"],
];

async function loadPipeline() {
  const s = await api("/pipeline/summary");
  $("#funnel").innerHTML = FUNNEL_STAGES.map(
    ([key, label]) => `
    <div class="stage ${s.bottleneck && s.bottleneck.stage === key ? "bottleneck" : ""}">
      <div class="count">${s.counts[key]}</div>
      <div class="label">${label}</div>
    </div>`
  ).join("");
  const run = s.latest_run;
  $("#latest-run").innerHTML = !run
    ? `<p class="status-na">No runs yet — ingest signals and hit Run Pipeline.</p>`
    : `
      <span class="chip ${run.status === "completed" ? "ok" : run.status === "gated" ? "warn" : "bad"}">${run.status}</span>
      <span class="chip">stage: ${run.current_stage}</span>
      <span class="chip">${run.latency_seconds}s</span>
      ${run.gated ? `<p class="status-fail" style="margin-top:8px">⚑ ${esc(run.gate_reason)} — flagged for human review</p>` : ""}
      <ul class="stage-log">
        ${(run.stage_log || []).map((l) => `<li><b>${l.stage}</b> — ${Object.entries(l).filter(([k]) => !["stage", "at"].includes(k)).map(([k, v]) => `${k}: ${esc(JSON.stringify(v))}`).join(" · ")}</li>`).join("")}
      </ul>`;
}

$("#run-pipeline").addEventListener("click", async () => {
  $("#run-pipeline").disabled = true;
  try {
    await api("/pipeline/run", { method: "POST", body: JSON.stringify({ auto_validate: true, enforce_gates: true }) });
  } catch (e) {
    alert(e.message);
  }
  $("#run-pipeline").disabled = false;
  refreshAll();
});

$("#signal-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const sig = await api("/signals", {
      method: "POST",
      body: JSON.stringify({ content: $("#signal-content").value, source_type: $("#signal-source").value, source_name: "dashboard" }),
    });
    $("#signal-result").textContent = sig.is_duplicate
      ? "Duplicate blocked by dedup engine."
      : `Ingested — tagged [${sig.tags.join(", ")}], quality ${sig.quality_score}`;
    $("#signal-content").value = "";
    loadPipeline();
  } catch (err) {
    $("#signal-result").textContent = err.message;
  }
});

/* -------------------------------------------------------------- signals -- */
async function loadInsights() {
  const cards = await api("/insight-cards");
  $("#insight-cards").innerHTML = cards.map((c) => `
    <div class="card">
      <h3>${esc(c.title)}</h3>
      <p>${esc(c.summary)}</p>
      <div class="meta">
        <span class="chip accent">${esc(c.category)}</span>
        <span class="chip">trend: ${esc(c.trend_vector)}</span>
        <span class="chip">strength ${c.strength_score}</span>
        <span class="chip">→ ${esc(c.engine_route)}</span>
      </div>
    </div>`).join("") || "<p class='status-na'>No insight cards yet.</p>";
}

/* ----------------------------------------------------------- behavioral -- */
async function loadBehavioral() {
  const clusters = await api("/clusters");
  $("#clusters").innerHTML = clusters.map((c) => `
    <div class="card">
      <h3>${esc(c.name)}</h3>
      <p><b>Need states:</b></p>
      <p>${c.need_states.map(esc).join("<br>")}</p>
      <p style="margin-top:6px"><b>Persona:</b> ${esc(c.personas[0]?.name || "—")}</p>
      <div class="meta">
        <span class="chip">${c.size} signals</span>
        <span class="chip ${c.stability_score >= 0.75 ? "ok" : "warn"}">stability ${c.stability_score}</span>
        ${c.attributes.slice(0, 4).map((a) => `<span class="chip">${esc(a)}</span>`).join("")}
      </div>
    </div>`).join("") || "<p class='status-na'>Run the pipeline to generate clusters.</p>";

  const hyps = await api("/hypotheses");
  $("#hypotheses").innerHTML = hyps.map((h) => `
    <div class="card">
      <h3>#${h.rank} ${esc(h.title)}</h3>
      <p>${esc(h.description)}</p>
      <p style="margin-top:6px"><i>${esc(h.rationale)}</i></p>
      <div class="bar"><div style="width:${h.confidence * 100}%"></div></div>
      <div class="meta">
        <span class="chip accent">confidence ${(h.confidence * 100).toFixed(0)}%</span>
        <span class="chip ${h.status === "validated" ? "ok" : h.status === "rejected" ? "bad" : ""}">${h.status}</span>
        ${(h.risk_flags || []).map((r) => `<span class="chip warn">${esc(r)}</span>`).join("")}
      </div>
    </div>`).join("") || "<p class='status-na'>No hypotheses yet.</p>";
}

/* ------------------------------------------------------------ prototype -- */
async function loadPrototype() {
  const briefs = await api("/briefs");
  $("#briefs").innerHTML = briefs.map((b) => `
    <div class="card">
      <h3>${esc(b.fields.objective || "Brief #" + b.id)}</h3>
      <p>${esc(b.fields.hypothesis_statement || "")}</p>
      <p style="margin-top:6px"><b>Method:</b> ${esc(b.fields.test_method || "—")} · <b>Sample:</b> ${esc(String(b.fields.sample_size ?? "—"))} · <b>${esc(String(b.fields.duration_weeks ?? "—"))} weeks</b></p>
      <div class="meta">
        <span class="chip ${b.completeness_score >= 0.9 ? "ok" : "warn"}">completeness ${(b.completeness_score * 100).toFixed(0)}%</span>
        <span class="chip">${esc(b.status)}</span>
        <span class="chip">owner: ${esc(b.fields.owner_role || "—")}</span>
      </div>
    </div>`).join("") || "<p class='status-na'>No briefs yet.</p>";

  const surveys = await api("/surveys");
  $("#surveys").innerHTML = surveys.map((s) => `
    <div class="card">
      <h3>Survey #${s.id} — cohort: ${esc(s.cohort.persona || "core")}</h3>
      <p>${s.questions.slice(0, 3).map((q) => "• " + esc(q.text)).join("<br>")}${s.questions.length > 3 ? "<br>…" : ""}</p>
      <div class="meta">
        <span class="chip">${s.questions.length} questions</span>
        <span class="chip accent">min sample ${s.min_sample_size}</span>
        <span class="chip">${s.responses_collected} responses</span>
        <span class="chip ${s.statistically_significant ? "ok" : "warn"}">${s.statistically_significant ? "significant" : "collecting"}</span>
      </div>
    </div>`).join("") || "<p class='status-na'>No surveys yet.</p>";
}

/* ------------------------------------------------------------- building -- */
async function loadBuilding() {
  const ujms = await api("/ujms");
  $("#ujms").innerHTML = ujms.map((u) => `
    <div class="ujm panel">
      <h3>UJM #${u.id} — ${esc(u.persona)}
        <span class="chip ${u.completeness_score >= 0.85 ? "ok" : "warn"}">completeness ${(u.completeness_score * 100).toFixed(0)}%</span>
        <span class="chip">${esc(u.status)}</span>
      </h3>
      <div class="ujm-lanes" style="margin-top:10px">
        ${u.lanes.map((l) => `
          <div class="lane">
            <h4>${esc(l.stage)}</h4>
            <ul>
              ${(l.touchpoints || []).slice(0, 2).map((t) => `<li>${esc(t)}</li>`).join("")}
              ${(l.pain_points || []).map((p) => `<li class="pain">${esc(p)}</li>`).join("")}
              ${(l.feature_interventions || []).map((f) => `<li class="feature">${esc(f)}</li>`).join("")}
            </ul>
          </div>`).join("")}
      </div>
    </div>`).join("") || "<p class='status-na'>No UJMs yet — validate hypotheses first.</p>";

  const cards = await api("/feature-cards");
  $("#feature-cards").innerHTML = cards.map((c) => `
    <div class="card">
      <h3>${esc(c.title)}</h3>
      <p>${esc(c.user_story)}</p>
      <p style="margin-top:6px">${c.acceptance_criteria.map((a) => "✓ " + esc(a)).join("<br>")}</p>
      <div class="meta">
        <span class="chip accent">${c.story_points} pts</span>
        <span class="chip ${c.quality_score >= 4 ? "ok" : "warn"}">quality ${c.quality_score}/5</span>
        <span class="chip">${esc(c.status)}</span>
      </div>
    </div>`).join("") || "<p class='status-na'>No feature cards yet.</p>";
}

/* ---------------------------------------------------------------- evals -- */
async function loadEvals() {
  const dash = await api("/evals/dashboard");
  $("#evals-table tbody").innerHTML = Object.entries(dash).map(([id, e]) => `
    <tr>
      <td><b>${id}</b></td>
      <td>${esc(e.engine)}</td>
      <td>${esc(e.metric)}</td>
      <td>${esc(e.method)}</td>
      <td>${esc(e.target)}</td>
      <td>${e.latest_score ?? "—"}</td>
      <td>${e.gate ? "⛔ gates" : "monitor"}</td>
      <td class="${e.passed === null ? "status-na" : e.passed ? "status-pass" : "status-fail"}">
        ${e.passed === null ? "not run" : e.passed ? "PASS" : "FAIL"}
      </td>
    </tr>`).join("");
}

/* ---------------------------------------------------------------- init -- */
async function refreshAll() {
  try {
    await loadTenants();
    await Promise.all([loadPipeline(), loadInsights(), loadBehavioral(), loadPrototype(), loadBuilding(), loadEvals()]);
  } catch (e) {
    console.error(e);
  }
}
refreshAll();
setInterval(loadPipeline, 5000); // F-10: dashboard refresh < 2s requirement relaxed to 5s polling for demo
