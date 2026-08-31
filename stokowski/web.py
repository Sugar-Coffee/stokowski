"""Optional web dashboard and API (requires fastapi + uvicorn)."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from collections import deque
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .orchestrator import MultiOrchestrator

try:
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
except ImportError:
    raise ImportError("Install web extras: pip install stokowski[web]")


class LogBuffer:
    """Circular buffer of captured log entries with pub/sub for SSE."""

    def __init__(self, maxlen: int = 500) -> None:
        self._entries: deque[dict[str, Any]] = deque(maxlen=maxlen)
        self._seq = 0
        # list of (loop, queue) pairs — one per active SSE subscriber
        self._subscribers: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []

    def append(self, entry: dict[str, Any]) -> None:
        self._seq += 1
        entry["seq"] = self._seq
        self._entries.append(entry)
        for loop, q in self._subscribers:
            try:
                loop.call_soon_threadsafe(q.put_nowait, entry)
            except Exception:
                pass

    def subscribe(self) -> asyncio.Queue:
        """Register a new SSE subscriber. Must be called from a running event loop."""
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.append((loop, q))
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers = [(l, sq) for l, sq in self._subscribers if sq is not q]

    def all_entries(self) -> list[dict[str, Any]]:
        return list(self._entries)

    @property
    def latest_seq(self) -> int:
        return self._seq


class LogCaptureHandler(logging.Handler):
    """Logging handler that feeds records into a LogBuffer.

    Drops records whose message starts with 'HTTP Request' (uvicorn access noise).
    Picks up extra= fields (e.g. capture=True, linked_to='SYN-123') as attributes.
    """

    _SKIP_PREFIXES = ("HTTP Request",)

    def __init__(self, buffer: LogBuffer) -> None:
        super().__init__()
        self._buf = buffer

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
            for prefix in self._SKIP_PREFIXES:
                if msg.startswith(prefix):
                    return

            attrs: dict[str, Any] = {}
            for key in ("capture", "linked_to"):
                if hasattr(record, key):
                    attrs[key] = getattr(record, key)

            # Strip redundant "[<linked_to>] " prefix — the tag chip renders it visually
            linked_to = attrs.get("linked_to")
            if linked_to:
                prefix = f"[{linked_to}] "
                if msg.startswith(prefix):
                    msg = msg[len(prefix):]

            self._buf.append(
                {
                    "ts": record.created,
                    "level": record.levelname,
                    "logger": record.name,
                    "msg": msg,
                    "attrs": attrs,
                }
            )
        except Exception:
            self.handleError(record)


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
    --muted:     #bbbbbb;
    --dim:       #888880;
    --amber:     #e8b84b;
    --amber-dim: #9b6230;
    --green:     #4cba6e;
    --red:       #d95f52;
    --blue:      #5b9cf6;
    --font:      'IBM Plex Mono', monospace;
    --font-size: 15px;
  }

  @media (min-width: 900px) {
    :root {
        --font-size: 20px;
    }
  }

  html, body {
    background: var(--bg);
    color: var(--text);
    font-family: var(--font);
    font-size: var(--font-size);
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
    font-size: 1.5rem;
    font-weight: 600;
    letter-spacing: -0.5px;
    color: var(--text);
  }

  .logo-tag {
    font-size: 0.8rem;
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
    font-size: 0.8rem;
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
    font-size: 0.6rem;
    font-weight: 500;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--muted);
    margin-bottom: 8px;
  }

  .metric-value {
    font-size: 2rem;
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
    font-size: 0.7rem;
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
    font-size: 0.6rem;
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
    font-size: 0.6rem;
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
    grid-template-columns: 100px minmax(0, 1fr) auto;
    gap: 16px;
    align-items: start;
    transition: background 0.15s;
  }

  .agent-card:hover {
    background: #141414;
  }

  .agent-id {
    font-size: 0.8rem;
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
    font-size: 0.6rem;
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

  .agent-activity {
    display: flex;
    align-items: baseline;
    gap: 10px;
  }

  .agent-msg {
    font-size: 0.9rem;
    color: var(--muted);
    font-weight: 300;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .agent-elapsed {
    font-size: 0.7rem;
    color: var(--dim);
    font-weight: 300;
    white-space: nowrap;
    flex-shrink: 0;
  }

  .agent-card { cursor: pointer; }

  .agent-card.expanded { background: #131313; }

  .agent-sub {
    font-size: 0.7rem;
    color: var(--dim);
    font-weight: 300;
    margin-top: 3px;
  }

  .agent-cost {
    font-size: 0.7rem;
    color: var(--muted);
    font-weight: 300;
  }

  .agent-warn { color: var(--red); }

  /* Timeline — the per-agent activity trail, revealed on click. */
  .timeline {
    grid-column: 1 / -1;
    margin-top: 14px;
    padding-top: 14px;
    border-top: 1px solid var(--border);
    max-height: 320px;
    overflow-y: auto;
  }

  .tl-row {
    display: grid;
    grid-template-columns: 62px 118px minmax(0, 1fr);
    gap: 12px;
    padding: 3px 0;
    font-size: 0.72rem;
    line-height: 1.5;
    border-left: 2px solid transparent;
    padding-left: 10px;
  }

  .tl-row.tool        { border-left-color: var(--blue); }
  .tl-row.tool_result { border-left-color: var(--red); }
  .tl-row.text        { border-left-color: var(--amber-dim); }
  .tl-row.thinking    { border-left-color: var(--border-hi); }
  .tl-row.result      { border-left-color: var(--green); }
  .tl-row.rate_limit,
  .tl-row.warning     { border-left-color: var(--red); }

  .tl-time  { color: var(--dim); }
  .tl-label {
    color: var(--text);
    font-weight: 500;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .tl-row.thinking .tl-label { color: var(--dim); font-weight: 300; font-style: italic; }
  .tl-detail {
    color: var(--muted);
    font-weight: 300;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .tl-row.tool_result .tl-detail,
  .tl-row.warning .tl-detail { color: var(--red); }

  .tl-empty { color: var(--dim); font-size: 0.72rem; font-weight: 300; }

  .tl-tools {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-bottom: 10px;
  }

  .tl-chip {
    font-size: 0.62rem;
    color: var(--muted);
    border: 1px solid var(--border-hi);
    border-radius: 2px;
    padding: 1px 6px;
    font-weight: 300;
  }

  .rl-chip {
    font-size: 0.62rem;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    padding: 2px 8px;
    border-radius: 2px;
    border: 1px solid var(--border-hi);
    color: var(--dim);
    font-weight: 400;
  }
  .rl-chip.warn { color: var(--red); border-color: rgba(217,95,82,.4); }

  .wf-bar { display:flex; flex-wrap:wrap; align-items:center; gap:8px;
            padding:14px 24px; background:var(--surface);
            border:1px solid var(--border); margin-bottom:20px; }
  .wf-tab { padding:5px 12px; border:1px solid var(--border-hi); border-radius:2px;
            font-size:.72rem; color:var(--muted); cursor:pointer; background:none;
            font-family:var(--font); }
  .wf-tab:hover { color:var(--text); }
  .wf-tab.on { color:var(--amber); border-color:var(--amber); }
  .wf-tab .wf-def { font-size:.55rem; color:var(--dim); margin-left:6px;
                    text-transform:uppercase; letter-spacing:.08em; }
  .wf-routes { font-size:.65rem; color:var(--dim); margin-left:auto; text-align:right;
               line-height:1.7; }
  .wf-routes code { color:var(--muted); }

  .agent-meta {
    text-align: right;
    white-space: nowrap;
  }

  .agent-tokens {
    font-size: 0.9rem;
    color: var(--text);
    font-weight: 500;
    margin-bottom: 3px;
  }

  .agent-turns {
    font-size: 0.7rem;
    color: var(--muted);
    font-weight: 300;
  }

  /* ── Projects tiles ── */
  .projects-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    gap: 1px;
    background: var(--border);
    border: 1px solid var(--border);
    margin-bottom: 32px;
  }

  .project-tile {
    background: var(--surface);
    padding: 16px 18px;
    display: flex;
    flex-direction: column;
    gap: 10px;
    transition: background 0.15s;
  }

  .project-tile:hover {
    background: #141414;
  }

  .project-tile.paused {
    opacity: 0.55;
  }

  .project-tile-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
  }

  .project-tile-name {
    font-size: 0.8rem;
    font-weight: 600;
    color: var(--amber);
    letter-spacing: 0.02em;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 140px;
  }

  .pause-btn {
    background: transparent;
    border: 1px solid var(--border-hi);
    color: var(--muted);
    font-family: var(--font);
    font-size: 0.6rem;
    font-weight: 500;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    padding: 3px 8px;
    border-radius: 2px;
    cursor: pointer;
    transition: all 0.15s;
  }

  .pause-btn:hover {
    border-color: var(--amber-dim);
    color: var(--amber);
  }

  .pause-btn.paused {
    border-color: var(--red);
    color: var(--red);
  }

  .project-tile-stats {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 6px;
    font-size: 0.7rem;
  }

  .project-stat {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .project-stat-label {
    font-size: 0.6rem;
    color: var(--muted);
    letter-spacing: 0.1em;
    text-transform: uppercase;
  }

  .project-stat-value {
    color: var(--text);
    font-weight: 500;
    font-size: 0.8rem;
  }

  /* ── Filter dropdown ── */
  .filter-select {
    background: var(--surface);
    border: 1px solid var(--border-hi);
    color: var(--text);
    font-family: var(--font);
    font-size: 0.7rem;
    padding: 4px 8px;
    border-radius: 2px;
    cursor: pointer;
  }

  .filter-select:focus {
    outline: none;
    border-color: var(--amber-dim);
  }

  /* ── Queue panel ── */
  .queue-card {
    background: var(--surface);
    padding: 12px 18px;
    display: grid;
    grid-template-columns: 100px 1fr auto;
    gap: 14px;
    align-items: center;
    border-bottom: 1px solid var(--border);
    font-size: 0.9rem;
  }

  .queue-card:last-child {
    border-bottom: none;
  }

  .queue-id {
    color: var(--amber);
    font-weight: 600;
    font-size: 0.9rem;
  }

  .queue-title {
    color: var(--muted);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 600px;
  }

  .queue-reason {
    font-size: 0.6rem;
    color: var(--muted);
    letter-spacing: 0.08em;
    text-transform: uppercase;
    padding: 2px 8px;
    border: 1px solid var(--border-hi);
    border-radius: 2px;
  }

  .queue-reason.paused {
    color: var(--red);
    border-color: var(--red);
  }

  .agent-project {
    font-size: 0.6rem;
    color: var(--muted);
    letter-spacing: 0.05em;
    margin-top: 2px;
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
    font-size: 0.8rem;
    color: var(--dim);
    margin-bottom: 6px;
    font-weight: 300;
    letter-spacing: 0.06em;
  }

  .empty-sub {
    font-size: 0.7rem;
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
    font-size: 0.6rem;
    color: var(--muted);
    font-weight: 300;
    letter-spacing: 0.1em;
    text-transform: uppercase;
  }

  .stat-value {
    font-size: 0.9rem;
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

  /* ── Log panel ── */
  .log-panel {
    background: var(--surface);
    border: 1px solid var(--border);
    margin-bottom: 32px;
  }

  .log-toolbar {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 10px 16px;
    border-bottom: 1px solid var(--border);
  }

  .log-filter {
    background: var(--surface);
    border: 1px solid var(--border-hi);
    color: var(--text);
    font-family: var(--font);
    font-size: 0.65rem;
    padding: 3px 8px;
    border-radius: 2px;
    cursor: pointer;
    min-width: 160px;
  }

  .log-filter:focus { outline: none; border-color: var(--amber-dim); }

  .log-clear-btn {
    background: transparent;
    border: 1px solid var(--border-hi);
    color: var(--muted);
    font-family: var(--font);
    font-size: 0.6rem;
    font-weight: 500;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    padding: 3px 10px;
    border-radius: 2px;
    cursor: pointer;
    transition: all 0.15s;
    margin-left: auto;
  }

  .log-clear-btn:hover { border-color: var(--amber-dim); color: var(--amber); }

  .log-scroll {
    height: 260px;
    overflow-y: auto;
    font-size: 0.72rem;
    line-height: 1.6;
    padding: 8px 0;
    scroll-behavior: smooth;
  }

  .log-scroll::-webkit-scrollbar { width: 4px; }
  .log-scroll::-webkit-scrollbar-track { background: var(--surface); }
  .log-scroll::-webkit-scrollbar-thumb { background: var(--border-hi); border-radius: 2px; }

  .log-entry {
    display: grid;
    grid-template-columns: 76px 52px 1fr;
    gap: 12px;
    padding: 2px 16px;
    transition: background 0.1s;
  }

  .log-entry:hover { background: #141414; }

  .log-ts { color: var(--dim); font-weight: 300; white-space: nowrap; }

  .log-lvl {
    font-weight: 500;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    font-size: 0.6rem;
    padding-top: 2px;
  }

  .log-lvl.DEBUG    { color: var(--dim); }
  .log-lvl.INFO     { color: var(--blue); }
  .log-lvl.WARNING  { color: var(--amber); }
  .log-lvl.ERROR    { color: var(--red); }
  .log-lvl.CRITICAL { color: var(--red); font-weight: 600; }

  .log-msg { color: var(--muted); word-break: break-all; }

  .log-tag {
    display: inline-block;
    font-size: 0.55rem;
    font-weight: 500;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    padding: 0 5px;
    border-radius: 2px;
    margin-right: 6px;
    border: 1px solid var(--amber-dim);
    color: var(--amber);
    vertical-align: middle;
    line-height: 1.6;
  }

  .log-empty {
    padding: 32px;
    text-align: center;
    font-size: 0.7rem;
    color: var(--dim);
    font-weight: 300;
  }

  .log-autoscroll-btn {
    background: transparent;
    border: 1px solid var(--border-hi);
    color: var(--muted);
    font-family: var(--font);
    font-size: 0.6rem;
    font-weight: 500;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    padding: 3px 10px;
    border-radius: 2px;
    cursor: pointer;
    transition: all 0.15s;
  }

  .log-autoscroll-btn.on { border-color: var(--amber-dim); color: var(--amber); }
  .log-autoscroll-btn:hover { border-color: var(--amber-dim); color: var(--amber); }

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
    font-size: 0.7rem;
    color: var(--dim);
    font-weight: 300;
  }

  .footer-right {
    font-size: 0.7rem;
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
      <a href="/studio" class="rl-chip" style="text-decoration:none">workflow &rarr;</a>
      <span id="rate-limit" class="rl-chip" style="display:none">—</span>
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

  <div id="projects-section" style="display:none">
    <div class="section-header">
      <span class="section-title">Projects</span>
      <div class="section-line"></div>
      <span class="section-count" id="project-count">0</span>
    </div>
    <div id="projects-grid" class="projects-grid"></div>
  </div>

  <div class="section-header">
    <span class="section-title">Active Agents</span>
    <div class="section-line"></div>
    <select id="project-filter" class="filter-select" onchange="window.__stokowskiSetFilter(this.value)">
      <option value="">All projects</option>
    </select>
    <span class="section-count" id="agent-count">0</span>
  </div>

  <div id="agents-container"></div>

  <div id="queue-section" style="display:none">
    <div class="section-header">
      <span class="section-title">Queued (eligible, waiting)</span>
      <div class="section-line"></div>
      <span class="section-count" id="queue-count">0</span>
    </div>
    <div id="queue-container"></div>
  </div>

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
    <div class="stat-item">
      <span class="stat-label">Cache r/w</span>
      <span class="stat-value" id="s-cache">—</span>
    </div>
    <div class="stat-divider"></div>
    <div id="progress-container" style="display:none; flex:1; align-items:center; gap:12px;">
      <span class="stat-label">Working</span>
      <div class="progress-wrap"><div class="progress-bar"></div></div>
    </div>
  </div>


  <div class="section-header" style="margin-top:8px">
    <span class="section-title">Log</span>
    <div class="section-line"></div>
    <span class="section-count" id="log-count">0</span>
  </div>
  <div class="log-panel">
    <div class="log-toolbar">
      <select id="log-issue-filter" class="log-filter" onchange="window.__logSetFilter(this.value)">
        <option value="">All issues</option>
      </select>
      <button class="log-autoscroll-btn on" id="log-autoscroll-btn" onclick="window.__logToggleAutoscroll()">&#8593; Auto-scroll</button>
      <button class="log-clear-btn" onclick="window.__logClear()">Clear</button>
    </div>
    <div class="log-scroll" id="log-scroll">
      <div class="log-empty" id="log-empty">No log entries yet</div>
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

  function fmtElapsed(isoStr) {
    if (!isoStr) return '';
    const diffMs = Date.now() - new Date(isoStr).getTime();
    const s = Math.floor(diffMs / 1000);
    if (s < 5)  return 'just now';
    if (s < 60) return s + 's ago';
    if (s < 3600) return Math.floor(s / 60) + 'm ago';
    return Math.floor(s / 3600) + 'h ago';
  }

  function statusPill(status) {
    const cls = ['streaming','succeeded','failed','retrying','pending','gate'].includes(status) ? status : 'pending';
    const label = status === 'streaming' ? 'live' : status === 'gate' ? 'awaiting gate' : status;
    return `<span class="status-pill ${cls}">${label}</span>`;
  }

  // Filter state — null means "all projects". Persisted across refreshes.
  let activeFilter = '';
  window.__stokowskiSetFilter = (val) => { activeFilter = val || ''; refresh(); };

  function projectMatches(item) {
    if (!activeFilter) return true;
    return (item.project_name || '') === activeFilter;
  }

  async function togglePause(name) {
    try {
      await fetch('/api/v1/projects/' + encodeURIComponent(name) + '/toggle', { method: 'POST' });
      refresh();
    } catch (e) { /* ignore */ }
  }
  window.__stokowskiTogglePause = togglePause;

  function renderProjects(data) {
    const projects = data.projects || [];
    const section = document.getElementById('projects-section');
    if (projects.length <= 1) {
      // Hide the projects section for single-project setups — keeps the
      // dashboard clean when there's no multi-project context to surface.
      section.style.display = 'none';
    } else {
      section.style.display = '';
    }
    document.getElementById('project-count').textContent = projects.length;

    // Update filter dropdown options (preserve current selection)
    const sel = document.getElementById('project-filter');
    const current = sel.value;
    const wantedNames = projects.map(p => p.name);
    const existingOpts = Array.from(sel.options).map(o => o.value);
    const same = wantedNames.length === existingOpts.length - 1 &&
      wantedNames.every((n, i) => existingOpts[i + 1] === n);
    if (!same) {
      sel.innerHTML = '<option value="">All projects</option>' +
        wantedNames.map(n => `<option value="${esc(n)}">${esc(n)}</option>`).join('');
      sel.value = wantedNames.includes(current) ? current : '';
      activeFilter = sel.value;
    }

    document.getElementById('projects-grid').innerHTML = projects.map(p => {
      const tokens = p.totals?.total_tokens || 0;
      const pauseLabel = p.paused ? 'Resume' : 'Pause';
      const pauseClass = p.paused ? 'pause-btn paused' : 'pause-btn';
      return `
        <div class="project-tile ${p.paused ? 'paused' : ''}">
          <div class="project-tile-head">
            <span class="project-tile-name" title="${esc(p.name)}">${esc(p.name)}</span>
            <button class="${pauseClass}" onclick="window.__stokowskiTogglePause('${esc(p.name)}')">${pauseLabel}</button>
          </div>
          <div class="project-tile-stats">
            <div class="project-stat">
              <span class="project-stat-label">Run</span>
              <span class="project-stat-value">${p.counts?.running || 0}</span>
            </div>
            <div class="project-stat">
              <span class="project-stat-label">Gates</span>
              <span class="project-stat-value">${p.counts?.gates || 0}</span>
            </div>
            <div class="project-stat">
              <span class="project-stat-label">Queue</span>
              <span class="project-stat-value">${p.counts?.queued || 0}</span>
            </div>
            <div class="project-stat">
              <span class="project-stat-label">Tokens</span>
              <span class="project-stat-value">${fmt(tokens)}</span>
            </div>
          </div>
        </div>`;
    }).join('');
  }

  function renderQueue(data) {
    const queue = (data.queued || []).filter(projectMatches);
    const section = document.getElementById('queue-section');
    if (queue.length === 0) {
      section.style.display = 'none';
      return;
    }
    section.style.display = '';
    document.getElementById('queue-count').textContent = queue.length;
    document.getElementById('queue-container').innerHTML =
      `<div class="agents">` + queue.map(q => {
        const pausedReason = (q.reason || '').toLowerCase().includes('paused');
        return `
          <div class="queue-card">
            <div>
              <div class="queue-id">${esc(q.issue_identifier)}</div>
              ${q.project_name ? `<div class="agent-project">${esc(q.project_name)}</div>` : ''}
            </div>
            <div class="queue-title">${esc(q.title || '—')}</div>
            <div class="queue-reason ${pausedReason ? 'paused' : ''}">${esc(q.reason || '')}</div>
          </div>`;
      }).join('') + `</div>`;
  }

  // Which agent cards are expanded. Kept outside the render so a card stays
  // open across the 3s poll.
  const expandedAgents = new Set();

  window.__toggleAgent = function (key) {
    if (expandedAgents.has(key)) expandedAgents.delete(key);
    else expandedAgents.add(key);
    if (window.__lastData) renderAgents(window.__lastData);
  };

  function renderTimeline(r) {
    const acts = r.activity || [];
    const counts = r.tool_counts || {};
    const chips = Object.keys(counts)
      .sort((a, b) => counts[b] - counts[a])
      .map(k => `<span class="tl-chip">${esc(k)} ${counts[k]}</span>`)
      .join('');

    if (!acts.length) {
      return `<div class="timeline">${chips ? `<div class="tl-tools">${chips}</div>` : ''}<div class="tl-empty">No activity recorded yet</div></div>`;
    }

    // Newest last, matching the order the agent did the work.
    const rows = acts.map(a => {
      const t = a.at ? new Date(a.at).toLocaleTimeString('en-GB', { hour12: false }) : '';
      const mark = a.status === 'error' ? '\u2717 ' : (a.status === 'warn' ? '\u26a0 ' : '');
      return `<div class="tl-row ${esc(a.kind)}">
        <span class="tl-time">${esc(t)}</span>
        <span class="tl-label" title="${esc(a.label)}">${mark}${esc(a.label)}</span>
        <span class="tl-detail" title="${esc(a.detail || '')}">${esc(a.detail || '')}</span>
      </div>`;
    }).join('');

    return `<div class="timeline">${chips ? `<div class="tl-tools">${chips}</div>` : ''}${rows}</div>`;
  }

  function renderAgents(data) {
    const all = [
      ...(data.running || []),
      ...(data.retrying || []).map(r => ({
        issue_identifier: r.issue_identifier,
        project_name: r.project_name,
        status: 'retrying',
        turn_count: r.attempt,
        tokens: { total_tokens: 0 },
        last_message: r.error || 'waiting to retry...',
        session_id: null,
      })),
      ...(data.gates || []).map(g => ({
        issue_identifier: g.issue_identifier,
        project_name: g.project_name,
        status: 'gate',
        state_name: g.gate_state,
        turn_count: g.run,
        tokens: { total_tokens: 0 },
        last_message: 'Awaiting human review',
        session_id: null,
      })),
    ].filter(projectMatches);

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
      const projTag = r.project_name ? `<div class="agent-project">${esc(r.project_name)}</div>` : '';
      const key = (r.project_name || '') + '/' + r.issue_identifier;
      const open = expandedAgents.has(key);

      // Tool count and error count give an at-a-glance sense of whether the
      // agent is making progress or thrashing.
      const toolBits = [];
      if (r.tool_call_count) toolBits.push(`${r.tool_call_count} tools`);
      if (r.tool_error_count) toolBits.push(`<span class="agent-warn">${r.tool_error_count} err</span>`);
      if (r.compaction_count) toolBits.push(`${r.compaction_count}\u00d7 compact`);
      if (r.artifact_count) toolBits.push(`${r.artifact_count} artifacts`);
      const sub = toolBits.length ? `<div class="agent-sub">${toolBits.join(' \u00b7 ')}</div>` : '';
      const cost = r.cost_usd ? `<div class="agent-cost">$${r.cost_usd.toFixed(2)}</div>` : '';

      return `
      <div class="agent-card ${open ? 'expanded' : ''}" onclick="window.__toggleAgent('${esc(key)}')">
        <div>
          <div class="agent-id">${esc(r.issue_identifier)}</div>
          ${projTag}
        </div>
        <div>
          <div class="agent-status-row">
            ${statusPill(r.status)}${stateInfo}
          </div>
          <div class="agent-activity">
            <span class="agent-msg">${esc(r.last_message || '\u2014')}</span>
            ${r.last_event_at ? `<span class="agent-elapsed">${fmtElapsed(r.last_event_at)}</span>` : ''}
          </div>
          ${sub}
        </div>
        <div class="agent-meta">
          <div class="agent-tokens">${fmt(r.tokens?.total_tokens || 0)} tok</div>
          <div class="agent-turns">turn ${r.turn_count || 0}</div>
          ${cost}
        </div>
        ${open ? renderTimeline(r) : ''}
      </div>`;
    }).join('');

    document.getElementById('agents-container').innerHTML =
      `<div class="agents">${rows}</div>`;
  }

  async function refresh() {
    try {
      const res = await fetch('/api/v1/state');
      const data = await res.json();
      window.__lastData = data;

      const running  = data.counts?.running  || 0;
      const retrying = data.counts?.retrying || 0;
      const active   = running > 0;

      // Metrics
      document.getElementById('v-running').textContent  = running;
      const gates = data.counts?.gates || 0;
      document.getElementById('v-retrying').textContent = retrying + gates;
      document.getElementById('v-tokens').textContent   = fmt(data.totals?.total_tokens || 0);
      document.getElementById('v-runtime').textContent  = fmtSecs(data.totals?.seconds_running || 0);

      // Cost is the number that actually tells you what a run was worth, so it
      // rides alongside the token count rather than being buried.
      const cost = data.totals?.cost_usd || 0;
      const toolCalls = data.totals?.tool_calls || 0;
      document.getElementById('v-tokens-sub').textContent =
        '$' + cost.toFixed(2) + (toolCalls ? ' \u00b7 ' + fmt(toolCalls) + ' tool calls' : '');

      document.getElementById('m-running').className  = 'metric' + (active ? ' active' : '');
      document.getElementById('m-tokens').className   = 'metric' + (data.totals?.total_tokens > 0 ? ' active' : '');

      // Stats bar. Cache reads are shown separately because they typically
      // dwarf fresh input and are billed at a tenth of the rate — collapsing
      // them into one figure hides where the spend actually goes.
      document.getElementById('s-in').textContent  = fmt(data.totals?.input_tokens  || 0);
      document.getElementById('s-out').textContent = fmt(data.totals?.output_tokens || 0);
      const sCache = document.getElementById('s-cache');
      if (sCache) {
        const cw = data.totals?.cache_creation_tokens || 0;
        const cr = data.totals?.cache_read_tokens || 0;
        sCache.textContent = fmt(cr) + ' / ' + fmt(cw);
      }

      // Rate-limit window
      const rl = data.rate_limit;
      const rlEl = document.getElementById('rate-limit');
      if (rl && rl.status) {
        const ok = rl.status === 'allowed';
        let label = (rl.type || 'limit').replace('_', '-') + ' ' + rl.status;
        if (rl.resets_at) {
          const mins = Math.max(0, Math.round((rl.resets_at * 1000 - Date.now()) / 60000));
          label += mins > 90 ? ' \u00b7 ' + Math.round(mins / 60) + 'h' : ' \u00b7 ' + mins + 'm';
        }
        rlEl.textContent = label;
        rlEl.className = 'rl-chip' + (ok ? '' : ' warn');
        rlEl.style.display = '';
      } else {
        rlEl.style.display = 'none';
      }

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

      renderProjects(data);
      renderAgents(data);
      renderQueue(data);
    } catch(e) {
      document.getElementById('status-dot').className = 'status-dot idle';
    }
  }

  refresh();
  setInterval(refresh, 3000);

  // ── Log panel ──────────────────────────────────────────────────────────────
  let logEntries = [];
  let logFilter = '';
  let logAutoScroll = true;
  let logKnownIssues = new Set();
  let logClearedSeq = 0;
  let logLastRenderedFilter = null;

  window.__logSetFilter = (val) => { logFilter = val || ''; renderLog(); };
  window.__logClear = () => {
    const last = logEntries.length > 0 ? logEntries[logEntries.length - 1].seq : 0;
    logClearedSeq = last;
    logEntries = [];
    renderLog();
  };
  window.__logToggleAutoscroll = () => {
    logAutoScroll = !logAutoScroll;
    const btn = document.getElementById('log-autoscroll-btn');
    btn.className = 'log-autoscroll-btn' + (logAutoScroll ? ' on' : '');
    if (logAutoScroll) scrollLogToTop();
  };

  function fmtLogTs(epochSecs) {
    const d = new Date(epochSecs * 1000);
    return String(d.getHours()).padStart(2,'0') + ':' +
           String(d.getMinutes()).padStart(2,'0') + ':' +
           String(d.getSeconds()).padStart(2,'0');
  }

  function scrollLogToTop() {
    const el = document.getElementById('log-scroll');
    el.scrollTop = 0;
  }

  function makeLogRow(e) {
    const row = document.createElement('div');
    row.className = 'log-entry';
    const tag = (e.attrs && e.attrs.linked_to)
      ? `<span class="log-tag">${esc(e.attrs.linked_to)}</span>` : '';
    row.innerHTML =
      `<span class="log-ts">${fmtLogTs(e.ts)}</span>` +
      `<span class="log-lvl ${esc(e.level)}">${esc(e.level)}</span>` +
      `<span class="log-msg">${tag}${esc(e.msg)}</span>`;
    return row;
  }

  function renderLog() {
    const visible = logFilter
      ? logEntries.filter(e => e.attrs && e.attrs.linked_to === logFilter)
      : logEntries;

    document.getElementById('log-count').textContent = visible.length;

    const scroll = document.getElementById('log-scroll');
    const empty  = document.getElementById('log-empty');

    if (visible.length === 0) {
      empty.style.display = '';
      Array.from(scroll.children).forEach(c => { if (c !== empty) c.remove(); });
      return;
    }
    empty.style.display = 'none';

    const filterChanged = logLastRenderedFilter !== logFilter;
    if (filterChanged) {
      Array.from(scroll.children).forEach(c => { if (c !== empty) c.remove(); });
    }
    logLastRenderedFilter = logFilter;

    const renderedCount = scroll.querySelectorAll('.log-entry').length;
    if (visible.length - renderedCount <= 0) return;

    // Newest entries at end of visible[] — prepend in reverse so most recent is at top
    const firstEntry = scroll.querySelector('.log-entry');
    for (let i = visible.length - 1; i >= renderedCount; i--) {
      scroll.insertBefore(makeLogRow(visible[i]), firstEntry);
    }

    if (logAutoScroll) scrollLogToTop();
  }

  function ingestEntry(entry) {
    if (entry.seq <= logClearedSeq) return;
    logEntries.push(entry);
    if (logEntries.length > 1000) logEntries = logEntries.slice(-1000);

    if (entry.attrs && entry.attrs.linked_to) {
      const id = entry.attrs.linked_to;
      if (!logKnownIssues.has(id)) {
        logKnownIssues.add(id);
        const sel = document.getElementById('log-issue-filter');
        const opt = document.createElement('option');
        opt.value = id;
        opt.textContent = id;
        sel.appendChild(opt);
      }
    }

    if (!logFilter || (entry.attrs && entry.attrs.linked_to === logFilter)) {
      renderLog();
    }
  }

  function connectLogStream() {
    const es = new EventSource('/api/v1/logs/stream');
    es.onmessage = (ev) => {
      try { ingestEntry(JSON.parse(ev.data)); } catch(e) {}
    };
    es.onerror = () => {
      es.close();
      setTimeout(connectLogStream, 3000);
    };
  }

  connectLogStream();
</script>
</body>
</html>
"""



# ── Studio page ──────────────────────────────────────────────────────────────
# Same shell and palette as the dashboard, no build step, no dependencies —
# the config file remains the source of truth and this is a view onto it.

STUDIO_HTML = DASHBOARD_HTML.split("<body>")[0] + """<body>
<div class="shell">
  <div class="header">
    <div class="logo">
      <span class="logo-mark">STOKOWSKI</span>
      <span class="logo-sub">WORKFLOW STUDIO</span>
    </div>
    <div class="header-right">
      <a href="/" class="rl-chip" style="text-decoration:none">&larr; dashboard</a>
      <span id="wf-path" class="timestamp">—</span>
    </div>
  </div>

  <div id="banner" style="display:none"></div>

  <div id="workflow-bar"></div>

  <div class="section-header">
    <span class="section-title">PIPELINE</span>
    <div class="section-line"></div>
    <span class="section-count" id="stage-count">0</span>
  </div>
  <div id="pipeline"></div>

  <div class="section-header" style="margin-top:8px">
    <span class="section-title">STAGES</span>
    <div class="section-line"></div>
  </div>
  <div id="action-bar"></div>
  <div id="stages"></div>

  <div class="section-header" style="margin-top:8px">
    <span class="section-title">GLOBAL</span>
    <div class="section-line"></div>
  </div>
  <div id="root-fields" class="agents"></div>

  <div class="section-header" style="margin-top:8px">
    <span class="section-title">PROMPTS</span>
    <div class="section-line"></div>
    <span class="section-count" id="prompt-count">0</span>
  </div>
  <div id="prompts"></div>

  <div class="footer">
    <span>Edits are validated before they are written &mdash; an invalid config is never saved</span>
    <span class="footer-right" id="saved">—</span>
  </div>
</div>

<style>
  .flow { display:flex; flex-wrap:wrap; align-items:center; gap:8px; padding:18px 24px;
          background:var(--surface); border:1px solid var(--border); margin-bottom:28px; }
  .node { padding:6px 12px; border:1px solid var(--border-hi); border-radius:2px;
          font-size:.75rem; white-space:nowrap; }
  .node.agent    { color:var(--text);  border-color:var(--blue); }
  .node.gate     { color:var(--amber); border-color:var(--amber-dim); }
  .node.terminal { color:var(--green); border-color:rgba(76,186,110,.4); }
  .node-sub { display:block; font-size:.6rem; color:var(--dim); margin-top:2px; }
  .arrow { color:var(--dim); font-size:.8rem; }

  .stage { background:var(--surface); border:1px solid var(--border); margin-bottom:1px;
           padding:16px 24px; }
  .stage-head { display:flex; align-items:baseline; gap:10px; margin-bottom:12px; }
  .stage-name { color:var(--amber); font-weight:600; font-size:.85rem; }
  .stage-kind { font-size:.6rem; text-transform:uppercase; letter-spacing:.1em;
                color:var(--dim); border:1px solid var(--border-hi); padding:1px 6px; }
  .stage-flow { font-size:.7rem; color:var(--dim); margin-left:auto; }

  .fields { display:grid; grid-template-columns:repeat(auto-fill,minmax(200px,1fr)); gap:12px; }
  .field label { display:block; font-size:.6rem; text-transform:uppercase;
                 letter-spacing:.08em; color:var(--dim); margin-bottom:4px; }
  .field input, .field select {
    width:100%; background:var(--bg); color:var(--text); font-family:var(--font);
    font-size:.75rem; border:1px solid var(--border-hi); border-radius:2px; padding:5px 7px; }
  .field input:focus, .field select:focus { outline:none; border-color:var(--amber); }
  .field.dirty input, .field.dirty select { border-color:var(--amber); }
  .field .inherited { font-size:.55rem; color:var(--dim); margin-top:3px; }

  .bar { display:flex; gap:10px; align-items:center; padding:14px 24px;
         background:var(--surface); border:1px solid var(--border); margin-bottom:28px; }
  button.act { background:var(--amber); color:#111; border:none; border-radius:2px;
               padding:6px 14px; font-family:var(--font); font-size:.7rem; font-weight:600;
               cursor:pointer; }
  button.act[disabled] { background:var(--border-hi); color:var(--dim); cursor:default; }
  button.ghost { background:transparent; color:var(--muted); border:1px solid var(--border-hi); }

  .banner { padding:12px 24px; margin-bottom:20px; font-size:.75rem; border:1px solid; }
  .banner.err { color:var(--red); border-color:rgba(217,95,82,.4); background:rgba(217,95,82,.07); }
  .banner.ok  { color:var(--green); border-color:rgba(76,186,110,.4); background:rgba(76,186,110,.07); }

  .prompt-row { display:flex; align-items:center; gap:12px; padding:10px 24px;
                background:var(--surface); border:1px solid var(--border); margin-bottom:1px;
                cursor:pointer; font-size:.75rem; }
  .prompt-row:hover { background:#141414; }
  .prompt-row .p-name { color:var(--text); }
  .prompt-row .p-size { margin-left:auto; color:var(--dim); font-size:.65rem; }
  .editor { width:100%; min-height:420px; background:var(--bg); color:var(--text);
            font-family:var(--font); font-size:.75rem; line-height:1.6; padding:14px;
            border:1px solid var(--border-hi); border-radius:2px; resize:vertical; }
</style>

<script>
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

  let DATA = null;
  const pending = new Map();   // key -> update object

  function banner(msg, kind) {
    const el = document.getElementById('banner');
    if (!msg) { el.style.display = 'none'; return; }
    el.className = 'banner ' + kind;
    el.textContent = msg;
    el.style.display = '';
    if (kind === 'ok') setTimeout(() => { el.style.display = 'none'; }, 4000);
  }

  // ── Pipeline strip: the "what does this actually do" view ───────────────
  function renderFlow(d) {
    const order = [];
    const seen = new Set();
    let cur = d.entry_state;
    while (cur && !seen.has(cur)) {
      seen.add(cur); order.push(cur);
      const s = d.states.find(x => x.name === cur);
      cur = s && (s.transitions.complete || s.transitions.approve);
    }
    // Anything unreachable still deserves showing — silently hiding a state
    // is how an orphaned stage goes unnoticed.
    d.states.forEach(s => { if (!seen.has(s.name)) order.push(s.name); });

    document.getElementById('stage-count').textContent = d.states.length;
    document.getElementById('pipeline').innerHTML = '<div class="flow">' +
      order.map((name, i) => {
        const s = d.states.find(x => x.name === name);
        if (!s) return '';
        const detail = s.type === 'agent'
          ? [s.model, s.effort, s.session,
             s.max_budget_usd ? '$' + s.max_budget_usd : null].filter(Boolean).join(' · ')
          : (s.type === 'gate' ? 'human · rework → ' + esc(s.rework_to || '?') : 'end');
        const orphan = seen.has(name) ? '' : ' (unreachable)';
        return (i ? '<span class="arrow">→</span>' : '') +
          `<span class="node ${esc(s.type)}">${esc(name)}${orphan}
             <span class="node-sub">${esc(detail)}</span></span>`;
      }).join('') + '</div>';
  }

  function fieldHtml(scope, state, key, value, spec, inherited) {
    const id = `${scope}:${state || ''}:${key}`;
    const label = key.split('.').pop().replace(/_/g, ' ');
    let control;
    if (spec.type === 'model') {
      // Grouped by provider, but backed by a datalist rather than a fixed
      // select: the catalogue will always lag real model lineups, so an
      // unlisted model has to remain typeable.
      const listId = 'models-' + id.replace(/[^a-z0-9]/gi, '-');
      const groups = (DATA && DATA.model_catalogue) || [];
      const opts = groups.map(g =>
        `<optgroup label="${esc(g.label)}">` +
        g.models.map(m => `<option value="${esc(m)}"></option>`).join('') +
        `</optgroup>`
      ).join('');
      control = `<input list="${esc(listId)}" data-id="${esc(id)}"
                   value="${esc(value ?? '')}" placeholder="${esc(inherited ?? 'inherits default')}"
                   autocomplete="off">
                 <datalist id="${esc(listId)}">${opts}</datalist>`;
    } else if (spec.choices) {
      control = `<select data-id="${esc(id)}">` +
        ['', ...spec.choices].map(c =>
          `<option value="${esc(c)}"${String(value ?? '') === c ? ' selected' : ''}>${esc(c || '—')}</option>`
        ).join('') + '</select>';
    } else {
      control = `<input data-id="${esc(id)}" value="${esc(value ?? '')}"
                   placeholder="${esc(inherited ?? '')}">`;
    }
    const note = (value == null && inherited != null)
      ? `<div class="inherited">inherits ${esc(inherited)}</div>` : '';
    return `<div class="field" id="f-${esc(id)}"><label>${esc(label)}</label>${control}${note}</div>`;
  }

  function renderStages(d) {
    document.getElementById('stages').innerHTML = d.states.map(s => {
      const applicable = Object.entries(d.state_fields).filter(([k]) => {
        if (s.type === 'gate') return ['rework_to', 'max_rework'].includes(k);
        if (s.type === 'terminal') return false;
        return !['rework_to', 'max_rework'].includes(k);
      });
      const fields = applicable.map(([k, spec]) =>
        fieldHtml('state', s.name, k, s[k], spec,
                  k === 'model' ? d.root['claude.model']
                  : k === 'effort' ? (d.root['claude.effort'] || 'high (CLI default)')
                  : k === 'max_budget_usd' ? d.root['claude.max_budget_usd'] : null)
      ).join('');
      const flow = Object.entries(s.transitions)
        .map(([t, target]) => `${esc(t)} → ${esc(target)}`).join('  ·  ');
      const conc = s.concurrency != null ? ` · max ${s.concurrency} at once` : '';
      return `<div class="stage">
        <div class="stage-head">
          <span class="stage-name">${esc(s.name)}</span>
          <span class="stage-kind">${esc(s.type)}</span>
          <span class="stage-flow">${flow}${esc(conc)}</span>
        </div>
        ${fields ? `<div class="fields">${fields}</div>` : ''}
      </div>`;
    }).join('');
  }

  function renderRoot(d) {
    document.getElementById('root-fields').innerHTML =
      '<div class="stage"><div class="fields">' +
      Object.entries(d.root_fields).map(([k, spec]) =>
        fieldHtml('root', null, k, d.root[k], spec, null)).join('') +
      '</div></div>';
  }

  function renderWorkflows(d) {
    const host = document.getElementById('workflow-bar');
    if (!d.workflows || d.workflows.length < 2) { host.innerHTML = ''; return; }

    const tabs = d.workflows.map(w =>
      `<button class="wf-tab ${w === d.selected_workflow ? 'on' : ''}"
               onclick="window.__selectWorkflow('${esc(w)}')">${esc(w)}${
        w === (d.routing && d.routing.default) ? '<span class="wf-def">default</span>' : ''
      }</button>`).join('');

    // The routing table is the answer to "why did this ticket run that
    // pipeline", so it belongs next to the pipeline itself.
    const rules = ((d.routing && d.routing.rules) || [])
      .map(r => `<code>${esc(r.label)}</code> &rarr; ${esc(r.workflow)}`).join('<br>');
    const fallback = d.routing && d.routing.default
      ? `<code>anything else</code> &rarr; ${esc(d.routing.default)}` : '';

    host.innerHTML = `<div class="wf-bar">${tabs}
      <div class="wf-routes">${rules}${rules && fallback ? '<br>' : ''}${fallback}</div>
    </div>`;
  }

  window.__selectWorkflow = function (name) { load(name); };

  function renderPrompts(d) {
    document.getElementById('prompt-count').textContent = d.prompts.length;
    document.getElementById('prompts').innerHTML = d.prompts.map(p =>
      `<div class="prompt-row" onclick="openPrompt('${esc(p.path)}')">
         <span class="p-name">${esc(p.path)}</span>
         <span class="p-size">${(p.bytes / 1024).toFixed(1)} KB</span>
       </div>`).join('');
  }

  document.addEventListener('input', e => {
    const id = e.target.dataset && e.target.dataset.id;
    if (!id) return;
    const [scope, state, ...rest] = id.split(':');
    const field = rest.join(':');
    pending.set(id, { scope, state: state || null, field, value: e.target.value });
    const box = document.getElementById('f-' + id);
    if (box) box.classList.add('dirty');
    document.getElementById('save').disabled = false;
    document.getElementById('save').textContent = `Save ${pending.size} change${pending.size > 1 ? 's' : ''}`;
  });

  async function save() {
    const btn = document.getElementById('save');
    btn.disabled = true; btn.textContent = 'Saving…';
    try {
      const res = await fetch('/api/v1/studio/apply', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          updates: [...pending.values()],
          workflow: DATA && DATA.selected_workflow,
        }),
      });
      const body = await res.json();
      if (!res.ok) throw new Error(body.error ? body.error.message : 'save failed');
      const showing = DATA && DATA.selected_workflow;
      pending.clear();
      banner('Saved. Stokowski re-reads config on the next poll tick.', 'ok');
      document.getElementById('saved').textContent =
        'saved ' + new Date().toLocaleTimeString('en-GB', { hour12: false });
      await load(showing);
    } catch (err) {
      // The config on disk is untouched when a save is rejected.
      banner(String(err.message || err), 'err');
      btn.disabled = false;
      btn.textContent = `Save ${pending.size} change${pending.size > 1 ? 's' : ''}`;
    }
  }

  window.openPrompt = async function (path) {
    const res = await fetch('/api/v1/studio/prompt?path=' + encodeURIComponent(path));
    const body = await res.json();
    if (!res.ok) return banner(body.error.message, 'err');
    const host = document.getElementById('prompts');
    host.innerHTML = `
      <div class="bar">
        <strong style="font-size:.75rem">${esc(path)}</strong>
        <span style="flex:1"></span>
        <button class="act" onclick="savePrompt('${esc(path)}')">Save prompt</button>
        <button class="act ghost" onclick="load()">Back</button>
      </div>
      <textarea class="editor" id="prompt-body"></textarea>`;
    document.getElementById('prompt-body').value = body.body;
  };

  window.savePrompt = async function (path) {
    const res = await fetch('/api/v1/studio/prompt', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path, body: document.getElementById('prompt-body').value }),
    });
    const body = await res.json();
    if (!res.ok) return banner(body.error.message, 'err');
    banner('Prompt saved. It applies to the next run that uses it.', 'ok');
  };

  async function load(workflow) {
    const url = '/api/v1/studio' + (workflow ? '?workflow=' + encodeURIComponent(workflow) : '');
    const res = await fetch(url);
    const d = await res.json();
    if (!res.ok) return banner(d.error.message, 'err');
    DATA = d;
    pending.clear();
    document.getElementById('wf-path').textContent = d.workflow_path;
    renderWorkflows(d); renderFlow(d); renderStages(d); renderRoot(d); renderPrompts(d);
    const scope = d.selected_workflow ? ` in <strong>${esc(d.selected_workflow)}</strong>` : '';
    document.getElementById('action-bar').innerHTML = `<div class="bar">
        <button class="act" id="save" disabled onclick="save()">No changes</button>
        <button class="act ghost" onclick="load(DATA && DATA.selected_workflow)">Reload from disk</button>
        <span style="color:var(--dim);font-size:.65rem">
          Editing${scope}. Structural changes — adding states, rewiring
          transitions — stay in the file.
        </span>
      </div>`;
  }

  window.save = save;
  window.load = load;
  load();
</script>
</body>
</html>
"""

def create_app(orchestrator: "MultiOrchestrator") -> FastAPI:
    app = FastAPI(title="Stokowski", version="0.1.0")

    log_buffer = LogBuffer(maxlen=500)
    _handler = LogCaptureHandler(log_buffer)
    _handler.setLevel(logging.DEBUG)
    logging.getLogger().addHandler(_handler)

    @app.get("/", response_class=HTMLResponse)
    async def dashboard():
        return HTMLResponse(DASHBOARD_HTML)

    @app.get("/api/v1/state")
    async def api_state():
        return JSONResponse(orchestrator.get_state_snapshot())

    @app.get("/api/v1/logs/stream")
    async def api_logs_stream():
        async def generate():
            # Drain buffered entries first
            for entry in log_buffer.all_entries():
                yield f"data: {json.dumps(entry)}\n\n"

            q = log_buffer.subscribe()
            try:
                while True:
                    try:
                        entry = await asyncio.wait_for(q.get(), timeout=25)
                        yield f"data: {json.dumps(entry)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
            finally:
                log_buffer.unsubscribe(q)

        return StreamingResponse(generate(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ── Studio: read and edit the workflow config ────────────────────────
    #
    # The config file stays the source of truth; this is a view onto it that
    # can write back. Every write is validated before it lands (see
    # studio.py), so the UI cannot leave the orchestrator unable to start.

    def _studio() -> "Studio":
        from .studio import Studio
        return Studio(Path(orchestrator.workflow_path))

    def _studio_error(e: Exception, status: int = 400) -> JSONResponse:
        return JSONResponse(
            {"error": {"code": "studio_error", "message": str(e)}},
            status_code=status,
        )

    @app.get("/studio", response_class=HTMLResponse)
    async def studio_page():
        return HTMLResponse(STUDIO_HTML)

    @app.get("/api/v1/studio")
    async def api_studio(workflow: str | None = None):
        from .studio import StudioError
        try:
            return JSONResponse(_studio().describe(workflow))
        except (StudioError, OSError) as e:
            return _studio_error(e, 500)

    @app.post("/api/v1/studio/default-workflow")
    async def api_studio_default_workflow(payload: dict):
        from .studio import StudioError
        try:
            return JSONResponse(_studio().set_default_workflow(payload.get("workflow") or ""))
        except (StudioError, OSError) as e:
            return _studio_error(e)

    @app.get("/api/v1/studio/raw")
    async def api_studio_raw():
        try:
            return JSONResponse({"text": _studio().raw()})
        except OSError as e:
            return _studio_error(e, 500)

    @app.post("/api/v1/studio/raw")
    async def api_studio_write_raw(payload: dict):
        from .studio import StudioError
        try:
            return JSONResponse(_studio().write_raw(payload.get("text") or ""))
        except (StudioError, OSError) as e:
            return _studio_error(e)

    @app.post("/api/v1/studio/apply")
    async def api_studio_apply(payload: dict):
        from .studio import StudioError
        try:
            return JSONResponse(_studio().apply(
                payload.get("updates") or [], workflow=payload.get("workflow")
            ))
        except (StudioError, OSError) as e:
            return _studio_error(e)

    @app.get("/api/v1/studio/prompt")
    async def api_studio_prompt(path: str):
        from .studio import StudioError
        try:
            return JSONResponse({"path": path, "body": _studio().read_prompt(path)})
        except (StudioError, OSError) as e:
            return _studio_error(e)

    @app.post("/api/v1/studio/prompt")
    async def api_studio_write_prompt(payload: dict):
        from .studio import StudioError
        try:
            _studio().write_prompt(payload.get("path") or "", payload.get("body") or "")
            return JSONResponse({"ok": True})
        except (StudioError, OSError) as e:
            return _studio_error(e)

    @app.get("/api/v1/{issue_identifier}")
    async def api_issue(issue_identifier: str):
        snap = orchestrator.get_state_snapshot()
        for r in snap["running"]:
            if r["issue_identifier"] == issue_identifier:
                return JSONResponse(r)
        for r in snap["retrying"]:
            if r["issue_identifier"] == issue_identifier:
                return JSONResponse(r)
        for g in snap["gates"]:
            if g["issue_identifier"] == issue_identifier:
                return JSONResponse(g)
        return JSONResponse(
            {"error": {"code": "issue_not_found", "message": f"Unknown: {issue_identifier}"}},
            status_code=404,
        )

    @app.post("/api/v1/refresh")
    async def api_refresh():
        asyncio.create_task(orchestrator.force_tick())
        return JSONResponse({"ok": True})

    @app.post("/api/v1/projects/{project_name}/pause")
    async def api_project_pause(project_name: str):
        if not orchestrator.pause(project_name):
            return JSONResponse(
                {"error": {"code": "project_not_found", "message": project_name}},
                status_code=404,
            )
        return JSONResponse({"ok": True, "project": project_name, "paused": True})

    @app.post("/api/v1/projects/{project_name}/resume")
    async def api_project_resume(project_name: str):
        if not orchestrator.resume(project_name):
            return JSONResponse(
                {"error": {"code": "project_not_found", "message": project_name}},
                status_code=404,
            )
        return JSONResponse({"ok": True, "project": project_name, "paused": False})

    @app.post("/api/v1/projects/{project_name}/toggle")
    async def api_project_toggle(project_name: str):
        if project_name not in orchestrator.project_names:
            return JSONResponse(
                {"error": {"code": "project_not_found", "message": project_name}},
                status_code=404,
            )
        now_paused = orchestrator.toggle(project_name)
        return JSONResponse({"ok": True, "project": project_name, "paused": now_paused})

    return app
