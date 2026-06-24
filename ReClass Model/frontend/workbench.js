// ReClass Evidence Workbench — standalone page (job1).
//
// A dependency-free client over the evidence-workbench endpoints in
// api/routers/evidence.py: reviewer-entered evidence, coverage roll-ups, the curation
// queue, and dry-run VCF/CSV import. It shares styles.css with the reviewer app but is
// otherwise independent (it does not touch index.html / app.js).
//
// The bearer token is kept in memory only; only the non-sensitive API base + tenant id
// are persisted to localStorage (matching the reviewer app's posture).
"use strict";

const STORAGE_KEY = "reclass_workbench_session";
const state = { apiBase: "", tenantId: "", bearerToken: "" };

function $(id) { return document.getElementById(id); }

function loadSession() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const s = JSON.parse(raw);
      state.apiBase = s.apiBase || "";
      state.tenantId = s.tenantId || "";
    }
  } catch { /* ignore malformed storage */ }
  if (!state.apiBase) state.apiBase = window.location.origin;
  $("api-base").value = state.apiBase;
  $("tenant-id").value = state.tenantId;
}

function saveSession() {
  state.apiBase = $("api-base").value.trim() || window.location.origin;
  state.tenantId = $("tenant-id").value.trim();
  state.bearerToken = $("bearer-token").value.trim();
  localStorage.setItem(STORAGE_KEY, JSON.stringify({
    apiBase: state.apiBase, tenantId: state.tenantId,
  }));
  $("session-status").textContent = "Session saved (token in memory only).";
}

function headers() {
  const h = { "Content-Type": "application/json" };
  if (state.tenantId) h["X-Tenant-Id"] = state.tenantId;
  if (state.bearerToken) h["Authorization"] = `Bearer ${state.bearerToken}`;
  return h;
}

async function api(path, options = {}) {
  let resp;
  try {
    resp = await fetch(`${state.apiBase}${path}`, {
      ...options, headers: { ...headers(), ...options.headers },
    });
  } catch (e) {
    throw new Error(`Cannot reach API at ${state.apiBase}${path} (${e.message || e}).`);
  }
  const text = await resp.text();
  let body;
  try { body = text ? JSON.parse(text) : null; } catch { body = text; }
  if (!resp.ok) {
    const detail = (body && body.detail) || resp.statusText || `HTTP ${resp.status}`;
    throw new Error(`${resp.status}: ${typeof detail === "object" ? JSON.stringify(detail) : detail}`);
  }
  return body;
}

function show(regionId, data) {
  const region = $(regionId);
  region.innerHTML = "";
  const pre = document.createElement("pre");
  pre.className = "output";
  pre.textContent = typeof data === "string" ? data : JSON.stringify(data, null, 2);
  region.appendChild(pre);
}

function setStatus(id, message, ok = true) {
  const node = $(id);
  node.textContent = message;
  node.className = ok ? "status ok" : "status error";
}

function variantFrom(prefix) {
  return {
    chrom: $(`${prefix}-chrom`).value.trim(),
    pos: parseInt($(`${prefix}-pos`).value, 10),
    ref: $(`${prefix}-ref`).value.trim(),
    alt: $(`${prefix}-alt`).value.trim(),
    build: ($(`${prefix}-build`) ? $(`${prefix}-build`).value.trim() : "") || "GRCh38",
  };
}

function variantKeyFrom(prefix) {
  const v = variantFrom(prefix);
  return `${v.build}-${v.chrom}-${v.pos}-${v.ref}-${v.alt}`;
}

// -- Reviewer evidence ------------------------------------------------------ //
async function loadCriteria() {
  const select = $("ev-criterion");
  try {
    const criteria = await api("/evidence/workbench/criteria");
    select.innerHTML = "";
    Object.entries(criteria).forEach(([code, label]) => {
      const opt = document.createElement("option");
      opt.value = code;
      opt.textContent = `${code} — ${label}`;
      select.appendChild(opt);
    });
  } catch (e) {
    // Fall back to a static list if the session is not yet configured.
    ["PVS1", "PS3", "BS3", "PM3", "PP1", "BS4", "PP4", "PS4", "BA1", "BS1"].forEach((c) => {
      const opt = document.createElement("option");
      opt.value = c; opt.textContent = c; select.appendChild(opt);
    });
  }
}

async function submitEvidence() {
  const payload = {
    variant: variantFrom("ev"),
    acmg_criterion: $("ev-criterion").value,
    evidence_direction: $("ev-direction").value,
    applied_strength: $("ev-strength").value || null,
    source: $("ev-source").value.trim() || "reviewer",
    source_version: $("ev-source-version").value.trim() || null,
    access_date: $("ev-access-date").value || null,
    reviewer: $("ev-reviewer").value.trim() || null,
    expires_at: $("ev-expires").value ? `${$("ev-expires").value}T00:00:00+00:00` : null,
    notes: $("ev-notes").value.trim() || null,
  };
  try {
    const row = await api("/evidence/workbench/evidence", {
      method: "POST", body: JSON.stringify(payload),
    });
    setStatus("evidence-status", `Saved evidence ${row.reviewer_evidence_id} (checksum ${row.checksum.slice(0, 12)}…).`);
    show("evidence-region", row);
  } catch (e) { setStatus("evidence-status", e.message, false); }
}

async function listEvidence() {
  try {
    const key = variantKeyFrom("ev");
    const rows = await api(`/evidence/workbench/evidence?variant_key=${encodeURIComponent(key)}`);
    setStatus("evidence-status", `${rows.length} record(s) for ${key}.`);
    show("evidence-region", rows);
  } catch (e) { setStatus("evidence-status", e.message, false); }
}

// -- Coverage --------------------------------------------------------------- //
async function recordCoverage() {
  const key = $("cov-key").value.trim();
  const parts = key.split("-");
  const variant = parts.length === 5
    ? { build: parts[0], chrom: parts[1], pos: parseInt(parts[2], 10), ref: parts[3], alt: parts[4] }
    : { chrom: parts[0], pos: parseInt(parts[1], 10), ref: parts[2], alt: parts[3], build: "GRCh38" };
  const payload = {
    variant,
    present_criteria: $("cov-present").value.split(",").map((s) => s.trim()).filter(Boolean),
    gene: $("cov-gene").value.trim() || null,
    vcep: $("cov-vcep").value.trim() || null,
    disease: $("cov-disease").value.trim() || null,
    variant_class: $("cov-class").value.trim() || null,
    provider: $("cov-provider").value.trim() || null,
  };
  try {
    const row = await api("/evidence/coverage", { method: "POST", body: JSON.stringify(payload) });
    setStatus("coverage-status", `Recorded coverage — blocked: ${row.blocked}.`);
    show("coverage-region", row);
  } catch (e) { setStatus("coverage-status", e.message, false); }
}

async function coverageSummary() {
  try {
    const by = $("cov-by").value;
    const path = by ? `/evidence/coverage?by=${by}` : "/evidence/coverage";
    const summary = await api(path);
    setStatus("coverage-status", "Coverage loaded.");
    show("coverage-region", summary);
  } catch (e) { setStatus("coverage-status", e.message, false); }
}

// -- Curation --------------------------------------------------------------- //
async function scanCuration() {
  const payload = {
    variant: variantFrom("cur"),
    enqueue: $("cur-enqueue").checked,
  };
  try {
    const result = await api("/evidence/curation/scan", { method: "POST", body: JSON.stringify(payload) });
    setStatus("curation-status", `${result.items.length} gap(s) found, ${result.enqueued_count} enqueued.`);
    show("curation-region", result);
  } catch (e) { setStatus("curation-status", e.message, false); }
}

async function listCuration() {
  try {
    const rows = await api("/evidence/curation");
    setStatus("curation-status", `${rows.length} item(s) in the queue.`);
    renderCuration(rows);
  } catch (e) { setStatus("curation-status", e.message, false); }
}

function renderCuration(rows) {
  const region = $("curation-region");
  region.innerHTML = "";
  if (!rows.length) { region.textContent = "Queue is empty."; return; }
  rows.forEach((row) => {
    const card = document.createElement("div");
    card.className = "card";
    const title = document.createElement("strong");
    title.textContent = `${row.kind} — ${row.variant_key || "(no key)"} [${row.state}]`;
    card.appendChild(title);
    ["in_review", "resolved", "dismissed"].forEach((next) => {
      const btn = document.createElement("button");
      btn.textContent = next;
      btn.onclick = async () => {
        try {
          await api(`/evidence/curation/${row.curation_id}/state`, {
            method: "POST", body: JSON.stringify({ state: next }),
          });
          listCuration();
        } catch (e) { setStatus("curation-status", e.message, false); }
      };
      card.appendChild(btn);
    });
    region.appendChild(card);
  });
}

// -- Import ----------------------------------------------------------------- //
async function importPreview() {
  const payload = {
    format: $("imp-format").value,
    content: $("imp-content").value,
    resolve: $("imp-resolve").checked,
  };
  try {
    const report = await api("/evidence/import/preview", { method: "POST", body: JSON.stringify(payload) });
    const t = report.totals;
    setStatus("import-status", `${t.unique_variants} unique, ${t.duplicate_rows} dup, ${t.invalid} invalid (dry-run).`);
    show("import-region", report);
  } catch (e) { setStatus("import-status", e.message, false); }
}

// -- Tabs + wiring ---------------------------------------------------------- //
function initTabs() {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
      document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
      tab.classList.add("active");
      $(`tab-${tab.dataset.tab}`).classList.add("active");
    });
  });
}

function init() {
  loadSession();
  initTabs();
  loadCriteria();
  $("save-session").addEventListener("click", () => { saveSession(); loadCriteria(); });
  $("btn-submit-evidence").addEventListener("click", submitEvidence);
  $("btn-list-evidence").addEventListener("click", listEvidence);
  $("btn-record-coverage").addEventListener("click", recordCoverage);
  $("btn-coverage-summary").addEventListener("click", coverageSummary);
  $("btn-scan-curation").addEventListener("click", scanCuration);
  $("btn-list-curation").addEventListener("click", listCuration);
  $("btn-import-preview").addEventListener("click", importPreview);
}

if (typeof document !== "undefined") {
  document.addEventListener("DOMContentLoaded", init);
}
