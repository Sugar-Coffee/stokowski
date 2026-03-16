"""Optional web dashboard and API (requires fastapi + uvicorn)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from . import __version__

if TYPE_CHECKING:
    from .orchestrator import Orchestrator

try:
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse, JSONResponse
except ImportError:
    raise ImportError("Install web extras: pip install stokowski[web]")

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
    --purple:    #a78bfa;
    --font:      'IBM Plex Mono', monospace;
  }

  .light {
    --bg:        #f5f5f4;
    --surface:   #ffffff;
    --border:    #e5e5e4;
    --border-hi: #d6d6d4;
    --text:      #1a1a1a;
    --muted:     #767672;
    --dim:       #a8a8a4;
    --amber:     #b5880d;
    --amber-dim: #e8d9a8;
    --green:     #2d8a4e;
    --red:       #c44d42;
    --blue:      #3b7ad9;
    --purple:    #7c5cd6;
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
    gap: 16px;
  }

  .theme-toggle {
    background: var(--surface);
    border: 1px solid var(--border);
    color: var(--muted);
    padding: 4px 8px;
    border-radius: 3px;
    font-size: 11px;
    font-family: var(--font);
    cursor: pointer;
    transition: all 0.2s;
  }

  .theme-toggle:hover {
    border-color: var(--amber-dim);
    color: var(--text);
  }

  .theme-toggle .icon {
    font-size: 12px;
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
    50% { opacity: 0.5; box-shadow: 0 0 12px var(--green); }
  }

  .timestamp {
    font-size: 11px;
    color: var(--muted);
    font-weight: 300;
    letter-spacing: 0.04em;
  }

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
    bottom: 0;
    left: 0;
    right: 0;
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
  }

  .agent-header {
    padding: 18px 24px;
    display: grid;
    grid-template-columns: 100px 1fr auto;
    gap: 16px;
    align-items: start;
    transition: background 0.15s;
    cursor: pointer;
    user-select: none;
  }

  .agent-header:hover {
    background: var(--dim);
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
    content: '\\25b6 ';
    animation: blink 1.2s step-end infinite;
  }

  @keyframes blink {
    0%, 100% { opacity: 1; }
    50% { opacity: 0; }
  }

  .status-pill.succeeded { background: rgba(76,186,110,.1); color: var(--green); border: 1px solid rgba(76,186,110,.25); }
  .status-pill.failed { background: rgba(217,95,82,.1); color: var(--red); border: 1px solid rgba(217,95,82,.25); }
  .status-pill.retrying { background: rgba(91,156,246,.1); color: var(--blue); border: 1px solid rgba(91,156,246,.25); }
  .status-pill.pending { background: transparent; color: var(--muted); border: 1px solid var(--border-hi); }
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

  .expand-hint {
    font-size: 10px;
    color: var(--dim);
    margin-top: 4px;
  }

  .agent-log {
    display: none;
    max-height: 600px;
    overflow-y: auto;
    border-top: 1px solid var(--border);
    padding: 0;
    background: var(--bg);
  }

  .agent-log.open {
    display: block;
  }

  .log-entry {
    padding: 8px 24px;
    border-bottom: 1px solid var(--border);
    font-size: 12px;
    line-height: 1.6;
  }

  .log-entry:last-child {
    border-bottom: none;
  }

  .log-entry.log-assistant {
    color: var(--text);
    white-space: pre-wrap;
    word-break: break-word;
  }

  .log-entry.log-tool {
    color: var(--blue);
    font-weight: 500;
  }

  .log-tool-name {
    color: var(--purple);
    font-weight: 600;
  }

  .log-tool-summary {
    color: var(--muted);
    font-weight: 300;
    margin-left: 8px;
    font-size: 11px;
  }

  .log-entry.log-tool-result {
    color: var(--dim);
    white-space: pre-wrap;
    word-break: break-word;
    font-size: 11px;
    max-height: 200px;
    overflow-y: auto;
    background: var(--bg);
    border-left: 2px solid var(--border-hi);
    margin-left: 12px;
    padding-left: 12px;
  }

  .log-entry.log-result {
    color: var(--green);
    font-weight: 500;
    border-left: 2px solid var(--green);
    padding-left: 12px;
    margin-left: 12px;
    white-space: pre-wrap;
    word-break: break-word;
  }

  .log-entry.log-error {
    color: var(--red);
    font-weight: 500;
    border-left: 2px solid var(--red);
    padding-left: 12px;
    margin-left: 12px;
  }

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
    0% { transform: scaleX(0) translateX(0); }
    50% { transform: scaleX(1) translateX(0); }
    100% { transform: scaleX(0) translateX(100%); }
  }

  .completed-header .agent-id {
    color: var(--muted);
  }

  .completed-time {
    font-size: 11px;
    color: var(--dim);
    font-weight: 300;
  }

  footer {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 20px 0 0;
    border-top: 1px solid var(--border);
    margin-top: 32px;
  }

  .footer-left,
  .footer-right {
    font-size: 11px;
    color: var(--dim);
    font-weight: 300;
  }

  .agent-log::-webkit-scrollbar,
  .log-entry.log-tool-result::-webkit-scrollbar {
    width: 4px;
  }

  .agent-log::-webkit-scrollbar-track,
  .log-entry.log-tool-result::-webkit-scrollbar-track {
    background: transparent;
  }

  .agent-log::-webkit-scrollbar-thumb,
  .log-entry.log-tool-result::-webkit-scrollbar-thumb {
    background: var(--border-hi);
    border-radius: 2px;
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
      <button id="theme-btn" class="theme-toggle" onclick="toggleTheme('light')">
        <span class="icon">&#9790;</span>
      </button>
      <div id="status-dot" class="status-dot idle"></div>
      <span id="ts" class="timestamp">&mdash;</span>
    </div>
  </header>

  <div class="metrics">
    <div class="metric" id="m-running">
      <div class="metric-label">Running</div>
      <div class="metric-value" id="v-running">&mdash;</div>
      <div class="metric-sub">active agents</div>
    </div>
    <div class="metric" id="m-retrying">
      <div class="metric-label">Queued</div>
      <div class="metric-value" id="v-retrying">&mdash;</div>
      <div class="metric-sub">retry / waiting</div>
    </div>
    <div class="metric" id="m-tokens">
      <div class="metric-label">Tokens</div>
      <div class="metric-value" id="v-tokens">&mdash;</div>
      <div class="metric-sub" id="v-tokens-sub">total consumed</div>
    </div>
    <div class="metric" id="m-runtime">
      <div class="metric-label">Runtime</div>
      <div class="metric-value" id="v-runtime">&mdash;</div>
      <div class="metric-sub">cumulative seconds</div>
    </div>
  </div>

  <div class="section-header">
    <span class="section-title">Active Agents</span>
    <div class="section-line"></div>
    <span class="section-count" id="agent-count">0</span>
  </div>

  <div id="agents-container"></div>

  <div class="section-header" id="completed-section" style="display:none">
    <span class="section-title">Completed Runs</span>
    <div class="section-line"></div>
    <span class="section-count" id="completed-count">0</span>
  </div>

  <div id="completed-container"></div>

  <div class="stats-bar">
    <div class="stat-item">
      <span class="stat-label">In</span>
      <span class="stat-value" id="s-in">&mdash;</span>
    </div>
    <div class="stat-divider"></div>
    <div class="stat-item">
      <span class="stat-label">Out</span>
      <span class="stat-value" id="s-out">&mdash;</span>
    </div>
    <div class="stat-divider"></div>
    <div id="progress-container" style="display:none; flex:1; align-items:center; gap:12px;">
      <span class="stat-label">Working</span>
      <div class="progress-wrap"><div class="progress-bar"></div></div>
    </div>
  </div>

  <footer>
    <span class="footer-left">Refreshes every 3s</span>
    <span class="footer-right" id="footer-gen">&mdash;</span>
  </footer>
</div>

<script>
  function getTheme() {
    return localStorage.getItem('theme') || 'dark';
  }

  function setTheme(theme) {
    document.documentElement.classList.toggle('light', theme === 'light');
    localStorage.setItem('theme', theme);
    var btn = document.getElementById('theme-btn');
    if (theme === 'light') {
      btn.innerHTML = '<span class="icon">&#9788;</span>';
      btn.setAttribute('onclick', "toggleTheme('dark')");
    } else {
      btn.innerHTML = '<span class="icon">&#9790;</span>';
      btn.setAttribute('onclick', "toggleTheme('light')");
    }
  }

  function toggleTheme(mode) {
    setTheme(mode);
  }

  (function() {
    setTheme(getTheme());
  })();

  const expandedLogs = new Set();
  const logScrollState = new Map();

  function esc(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function fmt(n) {
    if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
    if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
    return n.toString();
  }

  function fmtSecs(s) {
    if (s < 60) return Math.round(s) + 's';
    if (s < 3600) return Math.floor(s / 60) + 'm ' + Math.round(s % 60) + 's';
    return Math.floor(s / 3600) + 'h ' + Math.floor((s % 3600) / 60) + 'm';
  }

  function safeId(value) {
    return String(value || '').replace(/[^a-zA-Z0-9_-]/g, '-');
  }

  function statusPill(status) {
    var cls = ['streaming', 'succeeded', 'failed', 'retrying', 'pending', 'gate'].includes(status)
      ? status
      : 'pending';
    var label = status === 'streaming' ? 'live' : status === 'gate' ? 'awaiting gate' : status;
    return '<span class="status-pill ' + cls + '">' + label + '</span>';
  }

  function renderLogEntries(log) {
    if (!log || log.length === 0) {
      return '<div class="log-entry" style="color:var(--dim)">No output recorded yet</div>';
    }
    return log.map(function(entry) {
      if (entry.type === 'assistant') {
        return '<div class="log-entry log-assistant">' + esc(entry.text) + '</div>';
      }
      if (entry.type === 'tool_use') {
        var summary = entry.summary
          ? '<span class="log-tool-summary">' + esc(entry.summary) + '</span>'
          : '';
        return '<div class="log-entry log-tool"><span class="log-tool-name">' +
          esc(entry.tool) + '</span>' + summary + '</div>';
      }
      if (entry.type === 'tool_result') {
        return '<div class="log-entry log-tool-result">' + esc(entry.text) + '</div>';
      }
      if (entry.type === 'result') {
        return '<div class="log-entry log-result">' + esc(entry.text) + '</div>';
      }
      return '';
    }).join('');
  }

  function captureLogScrollState() {
    document.querySelectorAll('.agent-log.open').forEach(function(el) {
      var cardId = el.getAttribute('data-log-id');
      if (!cardId) {
        return;
      }
      var maxScrollTop = Math.max(el.scrollHeight - el.clientHeight, 0);
      var distanceFromBottom = maxScrollTop - el.scrollTop;
      logScrollState.set(cardId, {
        scrollTop: el.scrollTop,
        stickToBottom: distanceFromBottom <= 24,
      });
    });
  }

  function restoreLogScroll(cardId) {
    var el = document.getElementById('log-' + cardId);
    if (!el) {
      return;
    }
    var state = logScrollState.get(cardId);
    if (!state) {
      if (expandedLogs.has(cardId)) {
        el.scrollTop = el.scrollHeight;
      }
      return;
    }

    if (state.stickToBottom) {
      el.scrollTop = el.scrollHeight;
    } else {
      var maxScrollTop = Math.max(el.scrollHeight - el.clientHeight, 0);
      el.scrollTop = Math.min(state.scrollTop, maxScrollTop);
    }
  }

  function toggleLog(id) {
    var el = document.getElementById('log-' + id);
    if (!el) {
      return;
    }
    if (el.classList.contains('open')) {
      el.classList.remove('open');
      expandedLogs.delete(id);
      logScrollState.delete(id);
    } else {
      el.classList.add('open');
      expandedLogs.add(id);
      logScrollState.set(id, { scrollTop: 0, stickToBottom: true });
      el.scrollTop = el.scrollHeight;
    }
  }

  function renderAgentCard(r, cardId, isCompleted) {
    var stateInfo = r.state_name
      ? '<span style="color:var(--muted);font-size:11px;margin-left:8px">' + esc(r.state_name) + '</span>'
      : '';
    var isOpen = expandedLogs.has(cardId);
    var logCount = (r.message_log || []).length;
    var expandLabel = logCount > 0
      ? (isOpen ? '\\u25b4 collapse' : '\\u25be ' + logCount + ' events')
      : '';
    var completedClass = isCompleted ? ' completed-header' : '';
    var completedTime = '';
    if (isCompleted && r.completed_at) {
      var d = new Date(r.completed_at);
      completedTime = '<div class="completed-time">' +
        d.toLocaleTimeString('en-US', { hour12: false }) +
        '</div>';
    }
    var errorLine = '';
    if (r.error) {
      errorLine = '<div class="log-entry log-error">' + esc(r.error) + '</div>';
    }

    return '<div class="agent-card' + completedClass + '">' +
      '<div class="agent-header" onclick="toggleLog(\\'' + cardId + '\\')">' +
        '<div>' +
          '<div class="agent-id">' + esc(r.issue_identifier) + '</div>' +
          (expandLabel ? '<div class="expand-hint">' + expandLabel + '</div>' : '') +
        '</div>' +
        '<div>' +
          '<div class="agent-status-row">' + statusPill(r.status) + stateInfo + '</div>' +
          '<div class="agent-msg">' + esc(r.last_message || r.error || '\\u2014') + '</div>' +
        '</div>' +
        '<div class="agent-meta">' +
          '<div class="agent-tokens">' + fmt(r.tokens?.total_tokens || 0) + ' tok</div>' +
          '<div class="agent-turns">turn ' + (r.turn_count || 0) + '</div>' +
          completedTime +
        '</div>' +
      '</div>' +
      '<div id="log-' + cardId + '" data-log-id="' + cardId + '" class="agent-log' +
        (isOpen ? ' open' : '') + '">' +
        renderLogEntries(r.message_log) +
        errorLine +
      '</div>' +
    '</div>';
  }

  function renderAgents(data) {
    captureLogScrollState();

    var all = [].concat(
      (data.running || []),
      (data.retrying || []).map(function(r) {
        return {
          issue_identifier: r.issue_identifier,
          status: 'retrying',
          turn_count: r.attempt,
          tokens: { total_tokens: 0 },
          last_message: r.error || 'waiting to retry...',
          message_log: [],
          session_id: null,
        };
      }),
      (data.gates || []).map(function(g) {
        return {
          issue_identifier: g.issue_identifier,
          status: 'gate',
          state_name: g.gate_state,
          turn_count: g.run,
          tokens: { total_tokens: 0 },
          last_message: 'Awaiting human review',
          message_log: [],
          session_id: null,
        };
      })
    );

    document.getElementById('agent-count').textContent = all.length;

    if (all.length === 0) {
      document.getElementById('agents-container').innerHTML =
        '<div class="empty">' +
          '<div class="empty-title">No active agents</div>' +
          '<div class="empty-sub">Move a Linear issue to Todo or In Progress to start</div>' +
        '</div>';
    } else {
      var rows = all.map(function(r) {
        var cardId = 'active-' + safeId(r.issue_identifier);
        return renderAgentCard(r, cardId, false);
      }).join('');
      document.getElementById('agents-container').innerHTML = '<div class="agents">' + rows + '</div>';

      all.forEach(function(r) {
        var cardId = 'active-' + safeId(r.issue_identifier);
        if (expandedLogs.has(cardId)) {
          restoreLogScroll(cardId);
        }
      });
    }

    var completed = data.completed || [];
    var completedSection = document.getElementById('completed-section');
    var completedContainer = document.getElementById('completed-container');

    if (completed.length === 0) {
      completedSection.style.display = 'none';
      completedContainer.innerHTML = '';
    } else {
      completedSection.style.display = 'flex';
      document.getElementById('completed-count').textContent = completed.length;
      var reversed = completed.slice().reverse();
      var cRows = reversed.map(function(r, i) {
        var cardId = 'completed-' + safeId((r.issue_id || r.issue_identifier || i) + '-' + (r.completed_at || i));
        return renderAgentCard(r, cardId, true);
      }).join('');
      completedContainer.innerHTML = '<div class="agents">' + cRows + '</div>';

      reversed.forEach(function(r, i) {
        var cardId = 'completed-' + safeId((r.issue_id || r.issue_identifier || i) + '-' + (r.completed_at || i));
        if (expandedLogs.has(cardId)) {
          restoreLogScroll(cardId);
        }
      });
    }
  }

  async function refresh() {
    try {
      var res = await fetch('/api/v1/state');
      var data = await res.json();

      var running = data.counts?.running || 0;
      var retrying = data.counts?.retrying || 0;
      var active = running > 0;

      document.getElementById('v-running').textContent = running;
      var gates = data.counts?.gates || 0;
      document.getElementById('v-retrying').textContent = retrying + gates;
      document.getElementById('v-tokens').textContent = fmt(data.totals?.total_tokens || 0);
      document.getElementById('v-runtime').textContent = fmtSecs(data.totals?.seconds_running || 0);

      document.getElementById('m-running').className = 'metric' + (active ? ' active' : '');
      document.getElementById('m-tokens').className = 'metric' + ((data.totals?.total_tokens || 0) > 0 ? ' active' : '');

      document.getElementById('s-in').textContent = fmt(data.totals?.input_tokens || 0);
      document.getElementById('s-out').textContent = fmt(data.totals?.output_tokens || 0);

      var pc = document.getElementById('progress-container');
      pc.style.display = active ? 'flex' : 'none';

      var dot = document.getElementById('status-dot');
      dot.className = 'status-dot' + (active ? '' : ' idle');

      var now = new Date();
      document.getElementById('ts').textContent =
        now.toLocaleTimeString('en-US', { hour12: false }) + ' local';
      document.getElementById('footer-gen').textContent =
        'last sync ' + now.toLocaleTimeString('en-US', { hour12: false });

      renderAgents(data);
    } catch (e) {
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
    app = FastAPI(title="Stokowski", version=__version__)

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
        for r in snap.get("completed", []):
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

    return app
