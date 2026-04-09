"""Optional web dashboard and API (requires fastapi + uvicorn)."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .orchestrator import Orchestrator

try:
    from fastapi import FastAPI, Request
    from fastapi.responses import HTMLResponse, JSONResponse
except ImportError:
    raise ImportError("Install web extras: pip install stokowski[web]")

logger = logging.getLogger(__name__)

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Stokowski</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --bg:        #080808;
    --surface:   #0f0f0f;
    --border:    #1c1c1c;
    --border-hi: #2a2a2a;
    --text:      #e8e8e0;
    --muted:     #555550;
    --dim:       #333330;
    --amber:     #e8b84b;
    --amber-dim: #6b5220;
    --green:     #4cba6e;
    --red:       #d95f52;
    --blue:      #5b9cf6;
    --font:      'IBM Plex Mono', monospace;
  }

  html, body {
    background: var(--bg);
    color: var(--text);
    font-family: var(--font);
    font-size: 13px;
    line-height: 1.5;
    min-height: 100vh;
    -webkit-font-smoothing: antialiased;
  }

  /* Subtle grid background */
  body::before {
    content: '';
    position: fixed;
    inset: 0;
    background-image:
      linear-gradient(var(--border) 1px, transparent 1px),
      linear-gradient(90deg, var(--border) 1px, transparent 1px);
    background-size: 40px 40px;
    opacity: 0.35;
    pointer-events: none;
    z-index: 0;
  }

  .shell {
    position: relative;
    z-index: 1;
    max-width: 1280px;
    margin: 0 auto;
    padding: 0 24px 60px;
  }

  /* ── Header ── */
  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 28px 0 24px;
    border-bottom: 1px solid var(--border);
    margin-bottom: 32px;
  }

  .logo {
    display: flex;
    align-items: baseline;
    gap: 12px;
  }

  .logo-name {
    font-size: 22px;
    font-weight: 600;
    letter-spacing: -0.5px;
    color: var(--text);
  }

  .logo-tag {
    font-size: 11px;
    font-weight: 300;
    color: var(--muted);
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }

  .header-right {
    display: flex;
    align-items: center;
    gap: 24px;
  }

  .status-dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--green);
    box-shadow: 0 0 8px var(--green);
    animation: pulse-green 2.5s ease-in-out infinite;
  }

  .status-dot.idle {
    background: var(--muted);
    box-shadow: none;
    animation: none;
  }

  @keyframes pulse-green {
    0%, 100% { opacity: 1; box-shadow: 0 0 6px var(--green); }
    50%       { opacity: 0.5; box-shadow: 0 0 12px var(--green); }
  }

  .timestamp {
    font-size: 11px;
    color: var(--muted);
    font-weight: 300;
    letter-spacing: 0.04em;
  }

  /* ── Metrics row ── */
  .metrics {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 1px;
    background: var(--border);
    border: 1px solid var(--border);
    margin-bottom: 32px;
  }

  .metric {
    background: var(--surface);
    padding: 20px 24px;
    position: relative;
    overflow: hidden;
  }

  .metric::after {
    content: '';
    position: absolute;
    bottom: 0; left: 0; right: 0;
    height: 2px;
    background: var(--border-hi);
    transition: background 0.3s;
  }

  .metric.active::after {
    background: var(--amber);
  }

  .metric-label {
    font-size: 10px;
    font-weight: 500;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--muted);
    margin-bottom: 8px;
  }

  .metric-value {
    font-size: 32px;
    font-weight: 600;
    color: var(--text);
    line-height: 1;
    letter-spacing: -1px;
    transition: color 0.3s;
  }

  .metric.active .metric-value {
    color: var(--amber);
  }

  .metric-sub {
    font-size: 11px;
    color: var(--muted);
    margin-top: 6px;
    font-weight: 300;
  }

  /* ── Section headers ── */
  .section-header {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 12px;
  }

  .section-title {
    font-size: 10px;
    font-weight: 500;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--muted);
  }

  .section-line {
    flex: 1;
    height: 1px;
    background: var(--border);
  }

  .section-count {
    font-size: 10px;
    color: var(--dim);
    font-weight: 300;
  }

  /* ── Agent cards ── */
  .agents {
    display: flex;
    flex-direction: column;
    gap: 1px;
    background: var(--border);
    border: 1px solid var(--border);
    margin-bottom: 32px;
  }

  .agent-card {
    background: var(--surface);
    padding: 18px 24px;
    display: grid;
    grid-template-columns: 100px 1fr auto;
    gap: 16px;
    align-items: start;
    transition: background 0.15s;
  }

  .agent-card:hover {
    background: #141414;
  }

  .agent-id {
    font-size: 13px;
    font-weight: 600;
    color: var(--amber);
    letter-spacing: 0.02em;
  }

  .agent-status-row {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 6px;
  }

  .status-pill {
    font-size: 10px;
    font-weight: 500;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    padding: 2px 8px;
    border-radius: 2px;
  }

  .status-pill.streaming {
    background: rgba(232, 184, 75, 0.12);
    color: var(--amber);
    border: 1px solid var(--amber-dim);
  }

  .status-pill.streaming::before {
    content: '▶ ';
    animation: blink 1.2s step-end infinite;
  }

  @keyframes blink {
    0%, 100% { opacity: 1; }
    50%       { opacity: 0; }
  }

  .status-pill.succeeded  { background: rgba(76,186,110,.1); color: var(--green); border: 1px solid rgba(76,186,110,.25); }
  .status-pill.failed     { background: rgba(217,95,82,.1);  color: var(--red);   border: 1px solid rgba(217,95,82,.25); }
  .status-pill.retrying   { background: rgba(91,156,246,.1); color: var(--blue);  border: 1px solid rgba(91,156,246,.25); }
  .status-pill.pending    { background: transparent;          color: var(--muted); border: 1px solid var(--border-hi); }
  .status-pill.gate { background: rgba(232, 184, 75, 0.08); color: var(--amber-dim); border: 1px solid var(--amber-dim); }

  .agent-msg {
    font-size: 12px;
    color: var(--muted);
    font-weight: 300;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    max-width: 620px;
  }

  .agent-meta {
    text-align: right;
    white-space: nowrap;
  }

  .agent-tokens {
    font-size: 12px;
    color: var(--text);
    font-weight: 500;
    margin-bottom: 3px;
  }

  .agent-turns {
    font-size: 11px;
    color: var(--muted);
    font-weight: 300;
  }

  /* ── Empty state ── */
  .empty {
    background: var(--surface);
    border: 1px solid var(--border);
    padding: 48px 24px;
    text-align: center;
    margin-bottom: 32px;
  }

  .empty-title {
    font-size: 13px;
    color: var(--dim);
    margin-bottom: 6px;
    font-weight: 300;
    letter-spacing: 0.06em;
  }

  .empty-sub {
    font-size: 11px;
    color: var(--border-hi);
    font-weight: 300;
  }

  /* ── Stats bar ── */
  .stats-bar {
    display: flex;
    align-items: center;
    gap: 24px;
    padding: 14px 0;
    border-top: 1px solid var(--border);
    margin-top: 8px;
  }

  .stat-item {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .stat-label {
    font-size: 10px;
    color: var(--muted);
    font-weight: 300;
    letter-spacing: 0.1em;
    text-transform: uppercase;
  }

  .stat-value {
    font-size: 12px;
    color: var(--text);
    font-weight: 500;
  }

  .stat-divider {
    width: 1px;
    height: 16px;
    background: var(--border);
  }

  /* ── Progress bar ── */
  .progress-wrap {
    flex: 1;
    height: 2px;
    background: var(--border);
    overflow: hidden;
    border-radius: 1px;
  }

  .progress-bar {
    height: 100%;
    background: var(--amber);
    animation: scan 3s linear infinite;
    transform-origin: left;
  }

  @keyframes scan {
    0%   { transform: scaleX(0) translateX(0); }
    50%  { transform: scaleX(1) translateX(0); }
    100% { transform: scaleX(0) translateX(100%); }
  }

  /* ── Footer ── */
  footer {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 20px 0 0;
    border-top: 1px solid var(--border);
    margin-top: 32px;
  }

  .footer-left {
    font-size: 11px;
    color: var(--dim);
    font-weight: 300;
  }

  .footer-right {
    font-size: 11px;
    color: var(--dim);
    font-weight: 300;
  }
</style>
</head>
<body>
<div class="shell">

  <header>
    <div class="logo">
      <span class="logo-name">STOKOWSKI</span>
      <span class="logo-tag">Claude Code Orchestrator</span>
    </div>
    <div class="header-right">
      <div id="status-dot" class="status-dot idle"></div>
      <span id="ts" class="timestamp">—</span>
    </div>
  </header>

  <div class="metrics">
    <div class="metric" id="m-running">
      <div class="metric-label">Running</div>
      <div class="metric-value" id="v-running">—</div>
      <div class="metric-sub">active agents</div>
    </div>
    <div class="metric" id="m-retrying">
      <div class="metric-label">Queued</div>
      <div class="metric-value" id="v-retrying">—</div>
      <div class="metric-sub">retry / waiting</div>
    </div>
    <div class="metric" id="m-tokens">
      <div class="metric-label">Tokens</div>
      <div class="metric-value" id="v-tokens">—</div>
      <div class="metric-sub" id="v-tokens-sub">total consumed</div>
    </div>
    <div class="metric" id="m-runtime">
      <div class="metric-label">Runtime</div>
      <div class="metric-value" id="v-runtime">—</div>
      <div class="metric-sub">cumulative seconds</div>
    </div>
  </div>

  <div class="section-header">
    <span class="section-title">Active Agents</span>
    <div class="section-line"></div>
    <span class="section-count" id="agent-count">0</span>
  </div>

  <div id="agents-container"></div>

  <div class="stats-bar">
    <div class="stat-item">
      <span class="stat-label">In</span>
      <span class="stat-value" id="s-in">—</span>
    </div>
    <div class="stat-divider"></div>
    <div class="stat-item">
      <span class="stat-label">Out</span>
      <span class="stat-value" id="s-out">—</span>
    </div>
    <div class="stat-divider"></div>
    <div id="progress-container" style="display:none; flex:1; align-items:center; gap:12px;">
      <span class="stat-label">Working</span>
      <div class="progress-wrap"><div class="progress-bar"></div></div>
    </div>
  </div>

  <footer>
    <span class="footer-left">Refreshes every 3s</span>
    <span class="footer-right" id="footer-gen">—</span>
  </footer>

</div>

<script>
  function esc(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function fmt(n) {
    if (n >= 1000000) return (n/1000000).toFixed(1) + 'M';
    if (n >= 1000)    return (n/1000).toFixed(1) + 'K';
    return n.toString();
  }

  function fmtSecs(s) {
    if (s < 60)   return Math.round(s) + 's';
    if (s < 3600) return Math.floor(s/60) + 'm ' + Math.round(s%60) + 's';
    return Math.floor(s/3600) + 'h ' + Math.floor((s%3600)/60) + 'm';
  }

  function statusPill(status) {
    const cls = ['streaming','succeeded','failed','retrying','pending','gate'].includes(status) ? status : 'pending';
    const label = status === 'streaming' ? 'live' : status === 'gate' ? 'awaiting gate' : status;
    return `<span class="status-pill ${cls}">${label}</span>`;
  }

  function renderAgents(data) {
    const all = [
      ...(data.running || []),
      ...(data.retrying || []).map(r => ({
        issue_identifier: r.issue_identifier,
        status: 'retrying',
        turn_count: r.attempt,
        tokens: { total_tokens: 0 },
        last_message: r.error || 'waiting to retry...',
        session_id: null,
      })),
      ...(data.gates || []).map(g => ({
        issue_identifier: g.issue_identifier,
        status: 'gate',
        state_name: g.gate_state,
        turn_count: g.run,
        tokens: { total_tokens: 0 },
        last_message: 'Awaiting human review',
        session_id: null,
      })),
    ];

    document.getElementById('agent-count').textContent = all.length;

    if (all.length === 0) {
      document.getElementById('agents-container').innerHTML = `
        <div class="empty">
          <div class="empty-title">No active agents</div>
          <div class="empty-sub">Move a Linear issue to Todo or In Progress to start</div>
        </div>`;
      return;
    }

    const rows = all.map(r => {
      const stateInfo = r.state_name ? `<span style="color:var(--muted);font-size:11px;margin-left:8px">${esc(r.state_name)}</span>` : '';
      return `
      <div class="agent-card">
        <div>
          <div class="agent-id">${esc(r.issue_identifier)}</div>
        </div>
        <div>
          <div class="agent-status-row">
            ${statusPill(r.status)}${stateInfo}
          </div>
          <div class="agent-msg">${esc(r.last_message || '—')}</div>
        </div>
        <div class="agent-meta">
          <div class="agent-tokens">${fmt(r.tokens?.total_tokens || 0)} tok</div>
          <div class="agent-turns">turn ${r.turn_count || 0}</div>
        </div>
      </div>`;
    }).join('');

    document.getElementById('agents-container').innerHTML =
      `<div class="agents">${rows}</div>`;
  }

  async function refresh() {
    try {
      const res = await fetch('/api/v1/state');
      const data = await res.json();

      const running  = data.counts?.running  || 0;
      const retrying = data.counts?.retrying || 0;
      const active   = running > 0;

      // Metrics
      document.getElementById('v-running').textContent  = running;
      const gates = data.counts?.gates || 0;
      document.getElementById('v-retrying').textContent = retrying + gates;
      document.getElementById('v-tokens').textContent   = fmt(data.totals?.total_tokens || 0);
      document.getElementById('v-runtime').textContent  = fmtSecs(data.totals?.seconds_running || 0);

      document.getElementById('m-running').className  = 'metric' + (active ? ' active' : '');
      document.getElementById('m-tokens').className   = 'metric' + (data.totals?.total_tokens > 0 ? ' active' : '');

      // Stats bar
      document.getElementById('s-in').textContent  = fmt(data.totals?.input_tokens  || 0);
      document.getElementById('s-out').textContent = fmt(data.totals?.output_tokens || 0);

      // Progress bar
      const pc = document.getElementById('progress-container');
      pc.style.display = active ? 'flex' : 'none';

      // Status dot
      const dot = document.getElementById('status-dot');
      dot.className = 'status-dot' + (active ? '' : ' idle');

      // Timestamp
      const now = new Date();
      document.getElementById('ts').textContent =
        now.toLocaleTimeString('en-US', { hour12: false }) + ' local';
      document.getElementById('footer-gen').textContent =
        'last sync ' + now.toLocaleTimeString('en-US', { hour12: false });

      renderAgents(data);
    } catch(e) {
      document.getElementById('status-dot').className = 'status-dot idle';
    }
  }

  refresh();
  setInterval(refresh, 3000);
</script>
</body>
</html>
"""


def create_app(orchestrator: Orchestrator) -> FastAPI:
    app = FastAPI(title="Stokowski", version="0.1.0")

    @app.get("/", response_class=HTMLResponse)
    async def dashboard():
        return HTMLResponse(DASHBOARD_HTML)

    @app.get("/api/v1/state")
    async def api_state():
        return JSONResponse(orchestrator.get_state_snapshot())

    @app.get("/api/v1/{issue_identifier}")
    async def api_issue(issue_identifier: str):
        snap = orchestrator.get_state_snapshot()
        for r in snap["running"]:
            if r["issue_identifier"] == issue_identifier:
                return JSONResponse(r)
        for r in snap["retrying"]:
            if r["issue_identifier"] == issue_identifier:
                return JSONResponse(r)
        return JSONResponse(
            {"error": {"code": "issue_not_found", "message": f"Unknown: {issue_identifier}"}},
            status_code=404,
        )

    @app.post("/api/v1/refresh")
    async def api_refresh():
        asyncio.create_task(orchestrator._tick())
        return JSONResponse({"ok": True})

    @app.post("/api/v1/webhook/linear")
    async def webhook_linear(request: Request):
        body = await request.body()

        # Signature verification
        secret = orchestrator.cfg.webhook.secret
        if secret:
            signature = request.headers.get("linear-signature", "")
            expected = hmac.new(
                secret.encode(), body, hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(signature, expected):
                logger.warning("Webhook signature mismatch")
                return JSONResponse(
                    {"error": "invalid signature"}, status_code=401
                )

        try:
            payload = request.scope.get("_json") or await request.json()
        except Exception:
            return JSONResponse({"error": "invalid json"}, status_code=400)

        # Only react to issue state changes and new issues
        event_type = payload.get("type")
        action = payload.get("action")
        if event_type != "Issue" or action not in ("update", "create"):
            return JSONResponse({"ok": True, "action": "ignored"})

        if action == "update" and "stateId" not in (
            payload.get("updatedFrom") or {}
        ):
            return JSONResponse({"ok": True, "action": "ignored"})

        identifier = payload.get("data", {}).get("identifier", "?")
        logger.info(f"Webhook: {action} {identifier}")
        asyncio.create_task(orchestrator.webhook_tick())
        return JSONResponse({"ok": True, "action": "tick_scheduled"})

    @app.post("/api/v1/webhook/github")
    async def webhook_github(request: Request):
        body = await request.body()

        # Signature verification (X-Hub-Signature-256)
        secret = orchestrator.cfg.webhook.secret
        if secret:
            signature = request.headers.get("x-hub-signature-256", "")
            expected = "sha256=" + hmac.new(
                secret.encode(), body, hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(signature, expected):
                logger.warning("GitHub webhook signature mismatch")
                return JSONResponse(
                    {"error": "invalid signature"}, status_code=401
                )

        event = request.headers.get("x-github-event", "")
        if event == "ping":
            return JSONResponse({"ok": True, "action": "pong"})

        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid json"}, status_code=400)

        # PR review events → gate transitions via pr_triggers
        if event == "pull_request_review":
            review_state = payload.get("review", {}).get("state", "")
            pr = payload.get("pull_request", {})
            branch = pr.get("head", {}).get("ref", "")
            pr_number = pr.get("number", 0)
            pr_url = pr.get("html_url", "")
            if review_state and branch:
                logger.info(f"GitHub PR review: {review_state} on {branch}")
                asyncio.create_task(
                    orchestrator.handle_pr_event(review_state, branch, pr_number, pr_url)
                )
            return JSONResponse({"ok": True, "action": "pr_review_handled"})

        # PR merged/closed events
        if event == "pull_request":
            action = payload.get("action", "")
            pr = payload.get("pull_request", {})
            branch = pr.get("head", {}).get("ref", "")
            pr_number = pr.get("number", 0)
            pr_url = pr.get("html_url", "")
            if action == "closed" and pr.get("merged"):
                logger.info(f"GitHub PR merged: #{pr_number} on {branch}")
                asyncio.create_task(
                    orchestrator.handle_pr_event("merged", branch, pr_number, pr_url)
                )
                return JSONResponse({"ok": True, "action": "pr_merged_handled"})

        # Issue events → trigger tick
        if event == "issues":
            action = payload.get("action", "")
            if action in ("opened", "labeled", "unlabeled", "closed", "reopened"):
                number = payload.get("issue", {}).get("number", "?")
                logger.info(f"GitHub webhook: {action} #{number}")
                asyncio.create_task(orchestrator.webhook_tick())
                return JSONResponse({"ok": True, "action": "tick_scheduled"})

        return JSONResponse({"ok": True, "action": "ignored"})

    return app
