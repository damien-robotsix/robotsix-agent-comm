"""Monitoring dashboard — self-contained HTML page with inline CSS/JS.

Served by the broker at ``GET /dashboard`` (and ``GET /``) when
``dashboard_enabled`` is ``True``.
"""

from __future__ import annotations

DASHBOARD_HTML: str = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Broker Dashboard — robotsix-agent-comm</title>
<style>
  :root {
    color-scheme: dark;
    --bg: #0f172a;
    --card: #1e293b;
    --text: #e2e8f0;
    --muted: #94a3b8;
    --border: #334155;
    --accent: #3b82f6;
    --accent-hover: #2563eb;
    --green: #4ade80;
    --red: #f87171;
    --amber: #fbbf24;
    --radius: 6px;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
      Helvetica, Arial, sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.5;
    padding: 24px;
  }
  h1 { font-size: 1.5rem; margin-bottom: 4px; }
  .subtitle { color: var(--muted); font-size: 0.875rem; margin-bottom: 24px; }

  /* cards */
  .card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 16px;
    margin-bottom: 20px;
  }
  .card h2 {
    font-size: 1.1rem;
    margin-bottom: 12px;
    display: flex;
    align-items: center;
    gap: 8px;
  }

  /* controls */
  .controls {
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    margin-bottom: 12px;
  }
  .controls label { font-size: 0.8125rem; color: var(--muted); }
  .controls input, .controls select {
    padding: 6px 10px;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    font-size: 0.875rem;
    background: var(--bg);
    color: var(--text);
  }
  .controls button {
    padding: 6px 16px;
    background: var(--accent);
    color: #fff;
    border: none;
    border-radius: var(--radius);
    font-size: 0.875rem;
    cursor: pointer;
  }
  .controls button:hover { background: var(--accent-hover); }

  /* tables */
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.8125rem;
  }
  th, td {
    text-align: left;
    padding: 6px 8px;
    border-bottom: 1px solid var(--border);
    white-space: nowrap;
  }
  th { color: var(--muted); font-weight: 600; }
  tr:hover { background: rgba(59, 130, 246, 0.1); }

  /* badges */
  .badge {
    display: inline-block;
    padding: 1px 7px;
    border-radius: 10px;
    font-size: 0.75rem;
    font-weight: 600;
  }
  .badge-active { background: rgba(74, 222, 128, 0.15); color: var(--green); }
  .badge-stale { background: rgba(251, 191, 36, 0.15); color: var(--amber); }
  .badge-unknown { background: rgba(148, 163, 184, 0.15); color: var(--muted); }
  .badge-queued { background: rgba(59, 130, 246, 0.15); color: var(--accent); }
  .badge-rejected { background: rgba(248, 113, 113, 0.15); color: var(--red); }
  .badge-error { background: rgba(248, 113, 113, 0.15); color: var(--red); }

  /* error banner */
  .error-banner {
    background: rgba(248, 113, 113, 0.12);
    color: var(--red);
    border: 1px solid rgba(248, 113, 113, 0.4);
    border-radius: var(--radius);
    padding: 10px 14px;
    margin-bottom: 16px;
    display: none;
    font-size: 0.875rem;
  }
  .error-banner.visible { display: block; }

  /* loading */
  .spinner {
    display: inline-block;
    width: 14px; height: 14px;
    border: 2px solid var(--border);
    border-top-color: var(--accent);
    border-radius: 50%;
    animation: spin 0.6s linear infinite;
    vertical-align: middle;
    margin-left: 6px;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  /* footer */
  .footer {
    text-align: center;
    color: var(--muted);
    font-size: 0.75rem;
    margin-top: 24px;
  }
</style>
</head>
<body>

<h1>&#x1f4e1; Broker Dashboard</h1>
<p class="subtitle">robotsix-agent-comm monitoring</p>

<div id="error-banner" class="error-banner"></div>

<!-- Agents card -->
<div class="card">
  <h2>&#x1f465; Registered Agents <span id="agents-spinner"></span></h2>
  <table>
    <thead>
      <tr>
        <th>Name</th>
        <th>Mailbox</th>
        <th>Status</th>
        <th>Last Seen</th>
        <th>Capabilities</th>
      </tr>
    </thead>
    <tbody id="agents-tbody"></tbody>
  </table>
</div>

<!-- Traffic card -->
<div class="card">
  <h2>&#x1f4e8; Message Traffic <span id="traffic-spinner"></span></h2>
  <div class="controls">
    <label>
      Agent<br>
      <input id="filter-agent" type="text" placeholder="alice" size="14">
    </label>
    <label>
      Topic<br>
      <input id="filter-topic" type="text" placeholder="orders" size="14">
    </label>
    <label>
      Time window<br>
      <select id="filter-window">
        <option value="">all</option>
        <option value="1">last 1 min</option>
        <option value="5" selected>last 5 min</option>
        <option value="15">last 15 min</option>
        <option value="60">last 1 h</option>
      </select>
    </label>
    <label>&nbsp;<br><button id="filter-apply">Apply</button></label>
  </div>
  <table>
    <thead>
      <tr>
        <th>Timestamp</th>
        <th>Source</th>
        <th>Destination</th>
        <th>Topic</th>
        <th>Type</th>
        <th>Size</th>
        <th>Disposition</th>
      </tr>
    </thead>
    <tbody id="traffic-tbody"></tbody>
  </table>
</div>

<p class="footer">Auto-refreshes every 4 seconds</p>

<script>
// ---- helpers -----------------------------------------------------------
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

// Read the auth token from the page URL (?token=...)
const params = new URLSearchParams(window.location.search);
const AUTH_TOKEN = params.get("token") || "";
const BASE = window.location.origin;

// Auth helper: append ?token= to fetch URLs when token is present
function authUrl(path, extraParams) {
  const url = new URL(path, BASE);
  if (AUTH_TOKEN) url.searchParams.set("token", AUTH_TOKEN);
  if (extraParams) {
    Object.entries(extraParams).forEach(([k, v]) => {
      if (v !== "" && v !== null && v !== undefined) url.searchParams.set(k, v);
    });
  }
  return url.toString();
}

function showError(msg) {
  const el = $("#error-banner");
  el.textContent = msg;
  el.classList.add("visible");
  setTimeout(() => el.classList.remove("visible"), 8000);
}

// ---- agents table ------------------------------------------------------
async function loadAgents() {
  const spinner = $("#agents-spinner");
  spinner.innerHTML = '<span class="spinner"></span>';
  try {
    const resp = await fetch(authUrl("/agents"));
    if (!resp.ok) {
      const text = await resp.text().catch(() => "unknown");
      showError("GET /agents returned " + resp.status + ": " + text);
      return;
    }
    const data = await resp.json();
    renderAgents(data.agents || []);
  } catch (err) {
    showError("Failed to fetch /agents: " + err.message);
  } finally {
    spinner.innerHTML = "";
  }
}

function renderAgents(agents) {
  const tbody = $("#agents-tbody");
  if (agents.length === 0) {
    tbody.innerHTML = '<tr><td colspan="5" style="color:var(--muted)">No agents registered.</td></tr>';
    return;
  }
  tbody.innerHTML = agents.map(function(a) {
    var statusCls = "badge-unknown";
    if (a.status === "active") statusCls = "badge-active";
    else if (a.status === "stale") statusCls = "badge-stale";
    var lastSeen = (a.last_seen_seconds_ago != null)
      ? formatAge(a.last_seen_seconds_ago)
      : "—";
    var caps = a.capabilities ? Object.keys(a.capabilities).join(", ") : "—";
    var mailbox = a.mailbox ? "&#x2709; yes" : "no";
    return '<tr>' +
      '<td><strong>' + esc(a.agent_id) + '</strong></td>' +
      '<td>' + mailbox + '</td>' +
      '<td><span class="badge ' + statusCls + '">' + esc(a.status || "unknown") + '</span></td>' +
      '<td>' + lastSeen + '</td>' +
      '<td>' + esc(caps) + '</td>' +
      '</tr>';
  }).join("");
}

function formatAge(seconds) {
  if (seconds < 2) return "just now";
  if (seconds < 60) return Math.floor(seconds) + "s ago";
  var mins = Math.floor(seconds / 60);
  if (mins < 60) return mins + "m ago";
  var hours = Math.floor(mins / 60);
  return hours + "h ago";
}

// ---- traffic table -----------------------------------------------------
async function loadTraffic() {
  var extra = {};
  var agent = $("#filter-agent").value.trim();
  var topic = $("#filter-topic").value.trim();
  var windowVal = $("#filter-window").value;
  if (agent) extra.agent = agent;
  if (topic) extra.topic = topic;
  if (windowVal) {
    var since = (Date.now() / 1000) - (parseInt(windowVal, 10) * 60);
    extra.since = since.toFixed(3);
  }

  const spinner = $("#traffic-spinner");
  spinner.innerHTML = '<span class="spinner"></span>';
  try {
    const resp = await fetch(authUrl("/traffic", extra));
    if (!resp.ok) {
      const text = await resp.text().catch(() => "unknown");
      showError("GET /traffic returned " + resp.status + ": " + text);
      return;
    }
    const data = await resp.json();
    renderTraffic(data.traffic || []);
  } catch (err) {
    showError("Failed to fetch /traffic: " + err.message);
  } finally {
    spinner.innerHTML = "";
  }
}

function renderTraffic(records) {
  const tbody = $("#traffic-tbody");
  if (records.length === 0) {
    tbody.innerHTML = '<tr><td colspan="7" style="color:var(--muted)">No traffic recorded yet.</td></tr>';
    return;
  }
  tbody.innerHTML = records.map(function(r) {
    var ts = r.timestamp ? new Date(r.timestamp * 1000).toLocaleTimeString() : "—";
    var dispCls = "badge-unknown";
    if (r.disposition === "queued") dispCls = "badge-queued";
    else if (r.disposition === "rejected") dispCls = "badge-rejected";
    else if (r.disposition && r.disposition.indexOf("error") !== -1) dispCls = "badge-error";
    var size = (r.body_size_bytes != null) ? r.body_size_bytes + " B" : "—";
    return '<tr>' +
      '<td>' + esc(ts) + '</td>' +
      '<td>' + esc(r.source || "—") + '</td>' +
      '<td>' + esc(r.destination || "—") + '</td>' +
      '<td>' + esc(r.topic || "—") + '</td>' +
      '<td>' + esc(r.type || "—") + '</td>' +
      '<td>' + esc(size) + '</td>' +
      '<td><span class="badge ' + dispCls + '">' + esc(r.disposition || r.status || "—") + '</span></td>' +
      '</tr>';
  }).join("");
}

function esc(s) {
  if (s == null) return "—";
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

// ---- filter ------------------------------------------------------------
$("#filter-apply").addEventListener("click", function() { loadTraffic(); });

// ---- init --------------------------------------------------------------
loadAgents();
loadTraffic();
setInterval(loadAgents, 4000);
setInterval(loadTraffic, 4000);
</script>
</body>
</html>"""
