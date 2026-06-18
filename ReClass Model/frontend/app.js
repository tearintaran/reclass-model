/** ReClass clinician reviewer — drives the existing API workflow.
 *
 * Design notes for maintainers:
 *  - No build step, no framework, no dependencies. Plain DOM only.
 *  - All values that come from the API or the user are written with textContent
 *    (never innerHTML), so a hostile variant key / signer name can't inject markup.
 *  - Every API action runs through `runAction`, which renders an explicit
 *    loading → (ready | empty | error) state into its region. No action leaves a
 *    region blank.
 *  - The bearer token is held in memory only (see DEV_PERSIST_TOKEN below).
 *  - Provider choices are discovered from the resolve response, not hardcoded.
 *  - The testable surface is exported on `window.ReClass`; auto-init is skipped
 *    when `window.__RECLASS_TEST__` is set (see frontend/tests/test.html).
 */

// --------------------------------------------------------------------------- //
// Session / token handling                                                    //
// --------------------------------------------------------------------------- //

// Only non-sensitive session fields are persisted to localStorage.
const STORAGE_KEY = "reclass.reviewer.session";

// DEVELOPMENT-ONLY flag. When false (the production-safe default) the bearer
// token is kept in memory only: it is never written to localStorage and must be
// re-entered after a reload. Set to true ONLY on a trusted dev machine to keep
// the token across reloads — never enable this for a real deployment, because
// localStorage is readable by any script on the origin and survives logout.
const DEV_PERSIST_TOKEN = false;

let state = {
  apiBase: "",
  tenantId: "",
  bearerToken: "",          // in-memory only unless DEV_PERSIST_TOKEN is set
  providers: null,          // null => all configured providers; else a subset
  discoveredProviders: [],  // provider names: configured set + any seen in a resolve
  providerCatalog: [],      // [{name, version}] from GET /evidence/providers (if reachable)
  resolvedEvents: [],
  resolvedWarnings: [],
  resolvedBundle: null,
  classificationId: null,
};

/** The subset of session state that is safe to persist. The token is included
 *  only when the development flag is explicitly enabled. */
function persistableSession() {
  const out = { apiBase: state.apiBase, tenantId: state.tenantId };
  if (DEV_PERSIST_TOKEN) out.bearerToken = state.bearerToken;
  return out;
}

function loadSession() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const saved = JSON.parse(raw);
      // Only adopt the allow-listed fields, never a token unless dev-gated.
      if (typeof saved.apiBase === "string") state.apiBase = saved.apiBase;
      if (typeof saved.tenantId === "string") state.tenantId = saved.tenantId;
      if (DEV_PERSIST_TOKEN && typeof saved.bearerToken === "string") {
        state.bearerToken = saved.bearerToken;
      }
    }
  } catch (_) { /* ignore malformed storage */ }
  if (!state.apiBase && typeof window !== "undefined") {
    state.apiBase = window.location.origin.replace(/\/reviewer\/?$/, "");
  }
  if ($("api-base")) $("api-base").value = state.apiBase;
  if ($("tenant-id")) $("tenant-id").value = state.tenantId;
  if ($("bearer-token")) $("bearer-token").value = state.bearerToken;
  renderTokenHint();
}

function saveSession() {
  state.apiBase = $("api-base").value.trim().replace(/\/$/, "");
  state.tenantId = $("tenant-id").value.trim();
  state.bearerToken = $("bearer-token").value.trim();
  localStorage.setItem(STORAGE_KEY, JSON.stringify(persistableSession()));
  const status = $("session-status");
  status.textContent = "Session saved.";
  status.className = "status ok";
  renderTokenHint();
}

function renderTokenHint() {
  const hint = $("token-hint");
  if (!hint) return;
  hint.textContent = DEV_PERSIST_TOKEN
    ? "Dev mode: the bearer token is persisted in localStorage (development-only — do not use in production)."
    : "The bearer token is kept in memory only and is cleared on reload. It is never written to storage.";
}

// --------------------------------------------------------------------------- //
// DOM helpers                                                                 //
// --------------------------------------------------------------------------- //
function $(id) {
  return document.getElementById(id);
}

/** Tiny element builder. `attrs.text` sets textContent; `attrs.class` sets the
 *  class; `attrs.onclick` wires a handler; everything else becomes an attribute
 *  or dataset entry. Children may be nodes or strings. */
function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null) continue;
    if (k === "text") node.textContent = v;
    else if (k === "class") node.className = v;
    else if (k === "onclick") node.onclick = v;
    else if (k.startsWith("data-")) node.setAttribute(k, v);
    else node.setAttribute(k, v);
  }
  for (const c of [].concat(children)) {
    if (c == null) continue;
    node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return node;
}

function clear(node) {
  if (node) while (node.firstChild) node.removeChild(node.firstChild);
}

function dash(v) {
  return v === null || v === undefined || v === "" ? "—" : String(v);
}

// --------------------------------------------------------------------------- //
// Region state: loading / error / empty / ready                               //
// --------------------------------------------------------------------------- //
function setRegionLoading(region, label = "Loading…") {
  if (!region) return;
  clear(region);
  region.dataset.state = "loading";
  region.appendChild(el("div", { class: "region-state loading" }, [
    el("span", { class: "spinner", "aria-hidden": "true" }),
    el("span", { text: label }),
  ]));
}

function setRegionError(region, message) {
  if (!region) return;
  clear(region);
  region.dataset.state = "error";
  region.appendChild(el("div", { class: "region-state error", role: "alert" }, [
    el("strong", { text: "Error" }),
    el("div", { class: "region-msg", text: message }),
  ]));
}

function setRegionEmpty(region, message = "No data.") {
  if (!region) return;
  clear(region);
  region.dataset.state = "empty";
  region.appendChild(el("div", { class: "region-state empty", text: message }));
}

/** Prepare a region for content: clears it and marks it ready. Caller appends. */
function beginRegion(region) {
  if (!region) return region;
  clear(region);
  region.dataset.state = "ready";
  return region;
}

/** Wrap an async action so its region always shows loading then resolves to a
 *  content / empty / error state. The action renders its own content on success;
 *  any throw is surfaced as a readable error in `region`. */
async function runAction(region, fn, { loadingLabel } = {}) {
  setRegionLoading(region, loadingLabel);
  try {
    await fn();
  } catch (e) {
    setRegionError(region, e && e.message ? e.message : String(e));
  }
}

// --------------------------------------------------------------------------- //
// API client (fetch is injectable for tests)                                  //
// --------------------------------------------------------------------------- //
let _fetch = (typeof window !== "undefined" && window.fetch)
  ? window.fetch.bind(window) : null;

function setFetch(fn) { _fetch = fn; }

function variantPayload() {
  return {
    chrom: $("chrom").value.trim(),
    pos: parseInt($("pos").value, 10),
    ref: $("ref").value.trim(),
    alt: $("alt").value.trim(),
    build: $("build").value.trim() || "GRCh38",
  };
}

function headers() {
  const h = { "Content-Type": "application/json" };
  if (state.tenantId) h["X-Tenant-Id"] = state.tenantId;
  if (state.bearerToken) h["Authorization"] = `Bearer ${state.bearerToken}`;
  return h;
}

async function api(path, options = {}) {
  if (!_fetch) throw new Error("No fetch implementation available.");
  let resp;
  try {
    resp = await _fetch(`${state.apiBase}${path}`, {
      ...options, headers: { ...headers(), ...options.headers },
    });
  } catch (e) {
    // Network failure / unreachable API — make the failure legible.
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

async function apiText(path, options = {}) {
  if (!_fetch) throw new Error("No fetch implementation available.");
  let resp;
  try {
    resp = await _fetch(`${state.apiBase}${path}`, {
      ...options, headers: { ...headers(), ...options.headers },
    });
  } catch (e) {
    throw new Error(`Cannot reach API at ${state.apiBase}${path} (${e.message || e}).`);
  }
  const text = await resp.text();
  if (!resp.ok) throw new Error(`${resp.status}: ${resp.statusText || ("HTTP " + resp.status)}`);
  return text;
}

function showRaw(id, data) {
  const node = $(id);
  if (node) node.textContent = typeof data === "string" ? data : JSON.stringify(data, null, 2);
}

// --------------------------------------------------------------------------- //
// Provider discovery (no hardcoded provider list)                             //
// --------------------------------------------------------------------------- //
// Providers are discovered two ways, neither hardcoded:
//
//   1. Preferred: `GET /evidence/providers` lists the *configured* providers +
//      source versions, so the panel is populated BEFORE the first resolve. See
//      `loadProviders()`.
//   2. Fallback: when that endpoint is unreachable (older backend, offline), we
//      discover from the resolve response the page already loads — the
//      per-provider breakdown, the provider_versions map, and each event's
//      source. The union is the set of providers that actually contributed.
//
// `discoverProviders` unions the configured catalog (1) with whatever a resolve
// surfaced (2), so a provider that is configured but contributed nothing still
// appears, and a source seen in events but absent from the catalog is not lost.
function discoverProviders(resolveResponse) {
  const names = new Set((state.providerCatalog || []).map((p) => p.name));
  if (resolveResponse) {
    const per = resolveResponse.per_provider || {};
    Object.keys(per).forEach((n) => names.add(n));
    const versions = resolveResponse.provider_versions || {};
    Object.keys(versions).forEach((n) => names.add(n));
    (resolveResponse.events || []).forEach((ev) => {
      if (ev && ev.source) names.add(ev.source);
    });
  }
  return Array.from(names).sort();
}

// Fetch the configured providers from the backend. Degrades gracefully: any
// failure (endpoint absent, API unreachable, malformed body) leaves the catalog
// empty and the UI falls back to post-resolve discovery — it never surfaces an
// error or blocks the workflow. Returns the catalog array (possibly empty).
async function loadProviders() {
  try {
    const data = await api("/evidence/providers");
    const list = (data && Array.isArray(data.providers)) ? data.providers : [];
    state.providerCatalog = list
      .filter((p) => p && typeof p.name === "string")
      .map((p) => ({ name: p.name, version: p.version || null }));
  } catch (_) {
    state.providerCatalog = []; // unreachable/absent -> fall back to resolve discovery
  }
  // Merge configured names into the discovered set without dropping resolve hits.
  state.discoveredProviders = discoverProviders(state.resolvedBundle);
  renderProviderPanel();
  return state.providerCatalog;
}

function renderProviderPanel() {
  const panel = $("provider-panel");
  if (!panel) return;
  beginRegion(panel);
  const discovered = state.discoveredProviders;
  if (!discovered.length) {
    panel.appendChild(el("div", { class: "region-state empty",
      text: "No providers configured — they will appear here once the backend lists them or a resolve returns evidence." }));
    return;
  }
  const selected = state.providers; // null => all
  const catalogVersion = {};
  (state.providerCatalog || []).forEach((p) => { if (p.version) catalogVersion[p.name] = p.version; });
  const list = el("div", { class: "provider-list" });
  discovered.forEach((name) => {
    const checked = selected === null || selected.includes(name);
    const cb = el("input", { type: "checkbox" });
    cb.checked = checked;
    cb.dataset.provider = name;
    cb.onchange = onProviderToggle;
    // Version from the live resolve if present, else the configured catalog.
    const version = (state.resolvedBundle && state.resolvedBundle.provider_versions
      && state.resolvedBundle.provider_versions[name]) || catalogVersion[name] || null;
    list.appendChild(el("label", { class: "provider-item" }, [
      cb,
      el("span", { class: "provider-name", text: name }),
      version ? el("span", { class: "provider-version", text: version }) : null,
    ]));
  });
  panel.appendChild(list);
}

function onProviderToggle() {
  const boxes = Array.from(document.querySelectorAll("#provider-panel input[type=checkbox]"));
  const checked = boxes.filter((b) => b.checked).map((b) => b.dataset.provider);
  // All checked => fall back to "all configured" (null) so newly-added backend
  // providers are still picked up on the next resolve.
  state.providers = checked.length === state.discoveredProviders.length ? null : checked;
}

// --------------------------------------------------------------------------- //
// Structured renderers                                                        //
// --------------------------------------------------------------------------- //
function tableWrap(headers, rows) {
  const thead = el("thead", {}, [
    el("tr", {}, headers.map((h) => el("th", { text: h }))),
  ]);
  const tbody = el("tbody", {}, rows.map((cells) =>
    el("tr", {}, cells.map((c) =>
      (c && c.node) ? el("td", {}, c.node) : el("td", { text: dash(c) })))));
  return el("div", { class: "table-wrap" }, [el("table", {}, [thead, tbody])]);
}

/** Evidence bundle table: provider, criterion, direction, strength, version. */
function renderEvidenceEvents(region, events) {
  beginRegion(region);
  events = events || [];
  if (!events.length) {
    setRegionEmpty(region, "No evidence events were returned for this variant.");
    return;
  }
  const rows = events.map((ev) => [
    ev.source,
    ev.acmg_criterion,
    { node: directionChip(ev.evidence_direction) },
    ev.applied_strength,
    ev.source_version,
  ]);
  region.appendChild(el("div", { class: "evidence-table" },
    tableWrap(["Provider", "Criterion", "Direction", "Strength", "Source version"], rows)));
}

function directionChip(direction) {
  const cls = direction === "pathogenic" ? "chip path"
    : direction === "benign" ? "chip benign" : "chip neutral";
  return el("span", { class: cls, text: dash(direction) });
}

/** Point-contribution table with a running total and any overrides. */
function renderContributions(region, contributions, totalPoints, overrides) {
  beginRegion(region);
  contributions = contributions || [];
  const rows = contributions.map((c) => [
    c.acmg_criterion,
    { node: directionChip(c.evidence_direction) },
    c.applied_strength,
    { node: el("span", { class: "points", text: dash(c.points) }) },
    c.source,
    c.source_version,
  ]);
  if (rows.length) {
    region.appendChild(el("div", { class: "contrib-table" },
      tableWrap(["Criterion", "Direction", "Strength", "Points", "Source", "Version"], rows)));
  } else {
    region.appendChild(el("div", { class: "region-state empty",
      text: "No point contributions (no scored evidence)." }));
  }
  if (totalPoints !== undefined && totalPoints !== null) {
    region.appendChild(el("div", { class: "total-points" }, [
      el("span", { text: "Total points: " }),
      el("strong", { text: String(totalPoints) }),
    ]));
  }
  if (overrides && overrides.length) {
    region.appendChild(el("div", { class: "overrides" }, [
      el("div", { class: "overrides-title", text: "Overrides applied" }),
      el("ul", {}, overrides.map((o) => el("li", { text: o }))),
    ]));
  }
}

function tierBadge(tier) {
  const key = String(tier || "").toLowerCase();
  let cls = "tier-badge";
  if (key.includes("pathogenic")) cls += " path";
  else if (key.includes("benign")) cls += " benign";
  else cls += " vus";
  return el("span", { class: cls, text: dash(tier) });
}

/** Sign-off / release state. Accepts a reviewer-report `release_status` block or
 *  a raw receipt; never presents an unsigned draft as final. */
function renderReleaseStatus(region, src) {
  beginRegion(region);
  const isDraft = src.is_draft !== undefined
    ? src.is_draft
    : (src.signed_off_by === null || src.signed_off_by === undefined || src.signed_off_by === "");
  const badge = el("div", { class: "release-badge " + (isDraft ? "draft" : "signed") }, [
    el("strong", { text: isDraft ? "DRAFT — not for clinical use" : "SIGNED OFF" }),
  ]);
  region.appendChild(badge);
  const meta = el("dl", { class: "kv" });
  const add = (k, v) => {
    meta.appendChild(el("dt", { text: k }));
    meta.appendChild(el("dd", { text: dash(v) }));
  };
  if (!isDraft) {
    add("Signed off by", src.signed_off_by);
    add("Signed off at", src.signed_off_at);
  } else {
    add("Status", "Awaiting credentialed human sign-off");
  }
  if (state.classificationId) add("Classification ID", state.classificationId);
  region.appendChild(meta);
}

function renderWarnings(region, warnings) {
  beginRegion(region);
  warnings = warnings || [];
  if (!warnings.length) {
    setRegionEmpty(region, "No warnings.");
    return;
  }
  region.appendChild(el("ul", { class: "warnings-list" },
    warnings.map((w) => el("li", { class: "warning-item", text: w }))));
}

/** Tier + identity summary header for a classification payload. */
function renderClassificationHeader(region, payload) {
  const clf = payload.classification || {};
  const header = el("div", { class: "clf-header" }, [
    tierBadge(clf.tier),
    el("span", { class: "clf-points", text: "Total: " + dash(clf.total_points) }),
  ]);
  region.appendChild(header);
  const meta = el("dl", { class: "kv" });
  const add = (k, v) => {
    meta.appendChild(el("dt", { text: k }));
    meta.appendChild(el("dd", { text: dash(v) }));
  };
  add("Engine version", clf.engine_version || payload.engine_version);
  add("Reconstruction hash", clf.reconstruction_hash || payload.reconstruction_hash);
  region.appendChild(meta);
}

// --------------------------------------------------------------------------- //
// Workflow handlers                                                           //
// --------------------------------------------------------------------------- //
async function resolveEvidence() {
  const variant = variantPayload();
  // No hardcoded provider list: send the user's selection, or omit `providers`
  // entirely so the backend resolves across ALL configured providers.
  const body = { variant };
  if (state.providers !== null) body.providers = state.providers;
  const data = await api("/evidence/resolve", { method: "POST", body: JSON.stringify(body) });

  state.resolvedEvents = data.events || [];
  state.resolvedWarnings = data.warnings || [];
  state.resolvedBundle = data;
  state.discoveredProviders = discoverProviders(data);

  renderEvidenceEvents($("evidence-region"), state.resolvedEvents);
  renderWarnings($("warnings-region"), state.resolvedWarnings);
  renderProviderPanel();
  showRaw("evidence-raw", data);
}

async function classifyPreview() {
  const variant = variantPayload();
  const evidence = state.resolvedEvents.length
    ? { events: state.resolvedEvents }
    : { resolve: { variant } }; // omit providers => all configured
  const data = await api("/classify", {
    method: "POST", body: JSON.stringify({ variant, evidence }),
  });
  const region = beginRegion($("classification-region"));
  // A stateless preview is ALWAYS a draft — make that explicit.
  region.appendChild(el("div", { class: "preview-note",
    text: "Preview only — not persisted and never a clinical release (always a draft)." }));
  renderClassificationHeader(region, data);
  const contribHost = el("div");
  region.appendChild(contribHost);
  renderContributions(contribHost, (data.classification || {}).contributions,
    (data.classification || {}).total_points, (data.classification || {}).overrides);
  renderWarnings($("warnings-region"), data.warnings);
  showRaw("classification-raw", data);
}

async function persistDraft() {
  const variant = variantPayload();
  const mrn = $("patient-mrn").value.trim();
  const evidence = state.resolvedEvents.length
    ? { events: state.resolvedEvents }
    : { resolve: { variant } };
  const body = { variant, evidence };
  if (mrn) body.patient_mrn = mrn;
  const data = await api("/classifications", { method: "POST", body: JSON.stringify(body) });
  state.classificationId = (data.receipt && data.receipt.classification_id) || null;

  const region = beginRegion($("classification-region"));
  renderClassificationHeader(region, data);
  const contribHost = el("div");
  region.appendChild(contribHost);
  renderContributions(contribHost, (data.classification || {}).contributions,
    (data.classification || {}).total_points, (data.classification || {}).overrides);
  renderWarnings($("warnings-region"), data.warnings);
  if (data.receipt) renderReleaseStatus($("signoff-release"), data.receipt);
  showRaw("classification-raw", data);
}

async function loadReviewerReport(format) {
  if (!state.classificationId) throw new Error("Persist a draft first to load its reviewer report.");
  const region = $("reviewer-region");
  if (format === "markdown") {
    const md = await apiText(
      `/classifications/${state.classificationId}/report/reviewer?format=markdown`);
    beginRegion(region);
    region.appendChild(el("pre", { class: "output md", text: md }));
    showRaw("reviewer-raw", md);
    return;
  }
  const report = await api(`/classifications/${state.classificationId}/report/reviewer`);
  renderReviewerReport(region, report);
  showRaw("reviewer-raw", report);
}

async function loadSummary() {
  if (!state.classificationId) throw new Error("Persist a draft first to load its summary.");
  const report = await api(`/classifications/${state.classificationId}/report/summary`);
  renderPatientSummary($("reviewer-region"), report);
  showRaw("reviewer-raw", report);
}

/** Full structured technical reviewer report. */
function renderReviewerReport(region, report) {
  beginRegion(region);
  region.appendChild(el("div", { class: "report-title", text: "Technical reviewer report" }));

  // Release / sign-off state up top — never bury draft-vs-signed.
  if (report.release_status) {
    const host = el("div", { class: "report-block release-block" });
    region.appendChild(host);
    renderReleaseStatus(host, report.release_status);
  }

  // Identity + classification summary.
  const clf = report.classification || {};
  const summary = el("div", { class: "report-block" }, [
    el("h3", { text: "Classification" }),
  ]);
  summary.appendChild(el("div", { class: "clf-header" }, [
    tierBadge(clf.tier),
    el("span", { class: "clf-points", text: "Total: " + dash(clf.total_points) }),
  ]));
  const idmeta = el("dl", { class: "kv" });
  const add = (host, k, v) => {
    host.appendChild(el("dt", { text: k }));
    host.appendChild(el("dd", { text: dash(v) }));
  };
  add(idmeta, "Variant", (report.identity || {}).variant_key || (report.identity || {}).variant_id);
  add(idmeta, "Engine version", clf.engine_version);
  add(idmeta, "Reconstruction hash", clf.reconstruction_hash);
  summary.appendChild(idmeta);
  if (clf.overrides && clf.overrides.length) {
    summary.appendChild(el("div", { class: "overrides" }, [
      el("div", { class: "overrides-title", text: "Overrides applied" }),
      el("ul", {}, clf.overrides.map((o) => el("li", { text: o }))),
    ]));
  }
  region.appendChild(summary);

  // Per-criterion contributions (criterion, direction, strength, points, source, version).
  const critBlock = el("div", { class: "report-block" }, [el("h3", { text: "Criteria" })]);
  const critRows = (report.criteria || []).map((c) => [
    c.criterion,
    { node: directionChip(c.direction) },
    c.strength,
    { node: el("span", { class: "points", text: dash(c.points) }) },
    c.source,
    c.source_version,
  ]);
  if (critRows.length) {
    critBlock.appendChild(el("div", { class: "contrib-table" },
      tableWrap(["Criterion", "Direction", "Strength", "Points", "Source", "Version"], critRows)));
  } else {
    critBlock.appendChild(el("div", { class: "region-state empty", text: "No criteria recorded." }));
  }
  region.appendChild(critBlock);

  // Evidence grouped by source.
  const bySource = report.evidence_by_source || {};
  const sources = Object.keys(bySource);
  if (sources.length) {
    const evBlock = el("div", { class: "report-block evidence-by-source" },
      [el("h3", { text: "Evidence by source" })]);
    sources.forEach((src) => {
      evBlock.appendChild(el("div", { class: "source-name", text: src }));
      const host = el("div");
      renderEvidenceEvents(host, bySource[src]);
      evBlock.appendChild(host);
    });
    region.appendChild(evBlock);
  }

  // Provenance: provider versions + warnings.
  const prov = report.evidence_provenance || {};
  const provBlock = el("div", { class: "report-block" }, [el("h3", { text: "Provenance" })]);
  const pv = prov.provider_versions || {};
  if (Object.keys(pv).length) {
    const dl = el("dl", { class: "kv" });
    Object.entries(pv).forEach(([k, v]) => add(dl, k, v));
    provBlock.appendChild(dl);
  } else {
    provBlock.appendChild(el("div", { class: "region-state empty", text: "No provider versions recorded." }));
  }
  region.appendChild(provBlock);

  // History: prior classifications, reanalysis events, alerts.
  region.appendChild(renderHistoryBlock(report.history || {}));

  // Warnings + limitations.
  const warnBlock = el("div", { class: "report-block" }, [el("h3", { text: "Warnings" })]);
  const warnHost = el("div");
  renderWarnings(warnHost, report.warnings);
  warnBlock.appendChild(warnHost);
  region.appendChild(warnBlock);

  if (report.limitations && report.limitations.length) {
    region.appendChild(el("div", { class: "report-block limitations" }, [
      el("h3", { text: "Limitations" }),
      el("ul", {}, report.limitations.map((l) => el("li", { text: l }))),
    ]));
  }
}

function renderHistoryBlock(history) {
  const block = el("div", { class: "report-block history-section" }, [
    el("h3", { text: "History" }),
  ]);
  const prior = history.previous_classifications || [];
  if (prior.length) {
    block.appendChild(el("div", { class: "history-sub", text: "Prior classifications" }));
    const rows = prior.map((p) => [
      { node: tierBadge(p.tier) },
      dash(p.total_points),
      p.signed_off_by ? "signed" : "draft",
      p.created_at,
    ]);
    block.appendChild(tableWrap(["Tier", "Points", "State", "Created"], rows));
  } else {
    block.appendChild(el("div", { class: "region-state empty",
      text: "No prior classifications for this variant." }));
  }
  const alerts = history.alerts || [];
  if (alerts.length) {
    block.appendChild(el("div", { class: "history-sub", text: "Tier-crossing alerts" }));
    block.appendChild(el("ul", {}, alerts.map((a) =>
      el("li", { text: `${dash(a.old_tier)} → ${dash(a.new_tier)} (${dash(a.state)})${a.serious ? " · SERIOUS" : ""}` }))));
  }
  return block;
}

function renderPatientSummary(region, report) {
  beginRegion(region);
  region.appendChild(el("div", { class: "report-title", text: "Patient summary" }));
  if (report.release_status) {
    const host = el("div", { class: "report-block release-block" });
    region.appendChild(host);
    renderReleaseStatus(host, report.release_status);
  }
  const result = report.result || {};
  region.appendChild(el("div", { class: "report-block patient-result" }, [
    tierBadge(result.classification),
    el("p", { class: "plain-language", text: dash(result.plain_language) }),
  ]));
  if (report.what_this_means) {
    region.appendChild(el("div", { class: "report-block", text: report.what_this_means }));
  }
  if (report.next_steps) {
    region.appendChild(el("div", { class: "report-block next-steps", text: report.next_steps }));
  }
  if (report.limitations && report.limitations.length) {
    region.appendChild(el("div", { class: "report-block limitations" }, [
      el("h3", { text: "Limitations" }),
      el("ul", {}, report.limitations.map((l) => el("li", { text: l }))),
    ]));
  }
}

async function signOff() {
  if (!state.classificationId) throw new Error("No classification selected — persist or select a draft first.");
  const signed_off_by = $("signer").value.trim();
  const credential = $("credential").value.trim();
  if (!signed_off_by) throw new Error("Signer name is required.");
  const body = { signed_off_by };
  if (credential) body.credential = credential;
  const receipt = await api(`/classifications/${state.classificationId}/sign-off`, {
    method: "POST", body: JSON.stringify(body),
  });
  renderReleaseStatus($("signoff-release"), receipt);
  const status = $("signoff-status");
  status.textContent = receipt.is_draft ? "Still a draft." : "Signed off.";
  status.className = receipt.is_draft ? "status err" : "status ok";
}

async function listDrafts() {
  const region = $("draft-region");
  const rows = await api("/classifications");
  const drafts = (rows || []).filter((r) => r.is_draft);
  beginRegion(region);
  if (!drafts.length) {
    setRegionEmpty(region, "No draft classifications.");
    return;
  }
  const ul = el("ul", { class: "draft-list" });
  drafts.forEach((d) => {
    const li = el("li", { class: "draft-row" }, [
      tierBadge(d.tier),
      el("span", { class: "draft-key", text: dash(d.variant_key) }),
      el("span", { class: "draft-id", text: dash(d.classification_id) }),
    ]);
    li.onclick = () => {
      state.classificationId = d.classification_id;
      renderReleaseStatus($("signoff-release"), d);
      const status = $("signoff-status");
      if (status) {
        status.textContent = `Selected draft ${d.classification_id}.`;
        status.className = "status ok";
      }
    };
    ul.appendChild(li);
  });
  region.appendChild(ul);
}

async function listAlerts() {
  const region = $("alert-region");
  const alerts = await api("/alerts");
  beginRegion(region);
  if (!alerts || !alerts.length) {
    setRegionEmpty(region, "No tier-crossing alerts.");
    return;
  }
  alerts.forEach((a) => {
    const card = el("div", { class: "alert-card" + (a.serious ? " serious" : "") }, [
      el("div", { class: "tier-change" }, [
        tierBadge(a.old_tier),
        el("span", { text: " → " }),
        tierBadge(a.new_tier),
      ]),
      el("div", { class: "meta",
        text: `${dash(a.variant_key || a.variant_id)} · state: ${dash(a.state)}${a.serious ? " · SERIOUS" : ""}` }),
    ]);
    const actions = el("div", { class: "alert-actions" });
    [["acknowledged", "Acknowledge"], ["in_review", "In review"],
     ["resolved", "Resolve"], ["dismissed", "Dismiss"]].forEach(([st, label]) => {
      const btn = el("button", { text: label });
      btn.onclick = () => runAction(region, async () => {
        await api(`/alerts/${a.alert_id}/state`, {
          method: "POST", body: JSON.stringify({ state: st }),
        });
        await listAlerts();
      });
      actions.appendChild(btn);
    });
    card.appendChild(actions);
    region.appendChild(card);
  });
}

// --------------------------------------------------------------------------- //
// Wiring                                                                       //
// --------------------------------------------------------------------------- //
function bindTabs() {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.onclick = () => {
      document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
      document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
      tab.classList.add("active");
      $(`tab-${tab.dataset.tab}`).classList.add("active");
    };
  });
}

function bindActions() {
  $("save-session").onclick = saveSession;
  $("btn-resolve").onclick = () => runAction($("evidence-region"), resolveEvidence, { loadingLabel: "Resolving evidence…" });
  $("btn-classify").onclick = () => runAction($("classification-region"), classifyPreview, { loadingLabel: "Classifying…" });
  $("btn-persist").onclick = () => runAction($("classification-region"), persistDraft, { loadingLabel: "Persisting draft…" });
  $("btn-reviewer-json").onclick = () => runAction($("reviewer-region"), () => loadReviewerReport("json"), { loadingLabel: "Loading report…" });
  $("btn-reviewer-md").onclick = () => runAction($("reviewer-region"), () => loadReviewerReport("markdown"), { loadingLabel: "Loading report…" });
  $("btn-summary").onclick = () => runAction($("reviewer-region"), loadSummary, { loadingLabel: "Loading summary…" });
  $("btn-signoff").onclick = () => runAction($("signoff-release"), signOff, { loadingLabel: "Signing off…" });
  $("btn-list-drafts").onclick = () => runAction($("draft-region"), listDrafts, { loadingLabel: "Loading drafts…" });
  $("btn-list-alerts").onclick = () => runAction($("alert-region"), listAlerts, { loadingLabel: "Loading alerts…" });
}

function init() {
  loadSession();
  bindTabs();
  bindActions();
  renderProviderPanel();
  // Populate the provider panel from the configured set before the first resolve.
  // Fire-and-forget: degrades to post-resolve discovery if the endpoint is absent.
  loadProviders();
  // Empty states up front so no region starts blank.
  setRegionEmpty($("evidence-region"), "Run “Resolve evidence” to load the evidence bundle.");
  setRegionEmpty($("classification-region"), "Run “Classify preview” or “Persist draft” to see the classification.");
  setRegionEmpty($("warnings-region"), "No warnings.");
  setRegionEmpty($("reviewer-region"), "Persist a draft, then load its reviewer report.");
  setRegionEmpty($("signoff-release"), "No classification selected yet.");
  setRegionEmpty($("draft-region"), "Refresh to list draft classifications.");
  setRegionEmpty($("alert-region"), "Refresh to list tier-crossing alerts.");
}

// --------------------------------------------------------------------------- //
// Exports + auto-init                                                          //
// --------------------------------------------------------------------------- //
if (typeof window !== "undefined") {
  window.ReClass = {
    // config
    DEV_PERSIST_TOKEN, STORAGE_KEY,
    // lifecycle / wiring
    init, bindTabs, bindActions, loadSession, saveSession, persistableSession,
    // state + injection
    state, setFetch, renderTokenHint,
    // api
    api, apiText, headers, variantPayload,
    // discovery
    discoverProviders, loadProviders, renderProviderPanel, onProviderToggle,
    // region helpers
    setRegionLoading, setRegionError, setRegionEmpty, beginRegion, runAction,
    // renderers
    renderEvidenceEvents, renderContributions, renderReleaseStatus, renderWarnings,
    renderClassificationHeader, renderReviewerReport, renderPatientSummary,
    renderHistoryBlock, tierBadge, directionChip,
    // handlers
    resolveEvidence, classifyPreview, persistDraft, loadReviewerReport, loadSummary,
    signOff, listDrafts, listAlerts,
    // dom helpers (handy for tests)
    el, clear, $,
  };

  if (!window.__RECLASS_TEST__ && typeof document !== "undefined"
      && document.getElementById("save-session")) {
    init();
  }
}
