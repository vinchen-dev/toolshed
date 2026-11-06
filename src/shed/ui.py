"""Local dashboard over http.server: job cards, results as tables, run history, run-now, a tool catalog.

Binds to localhost only and has no authentication, so it must never be exposed on another interface.
With `schedule=True` it also runs the due jobs every minute, which is how the launchd agent uses it.
"""

from __future__ import annotations

import html
import importlib
import os
import re
import sys
import threading
import webbrowser
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import TCPServer
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit

from shed.args import describe_flags
from shed.cron import (
    AGENT_INTERVAL_S,
    CRON_FILE,
    STATE_DIR,
    Job,
    RunRecord,
    RunStore,
    append_log,
    cron_lock,
    health,
    job_summary,
    load_jobs,
    local_now,
    next_due,
    run_due,
    run_summary,
)
from shed.lib.errors import ToolError, UsageError
from shed.lib.output import to_json
from shed.registry import TOOLS_DIR, list_groups, load_all_tools, load_tool_module
from shed.tunnels import TunnelManager
from shed.version import current as shed_version

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
REFRESH_S = 30
BUSY_REFRESH_S = 3
_FENCE = re.compile(r"^```.*$", re.MULTILINE)
_ISO = re.compile(r"^(\d{4}-\d\d-\d\d)T(\d\d:\d\d:\d\d)(?:\.\d+)?(?:[+-]\d\d:\d\d|Z)?$")
_TITLE_KEYS = ("name", "env", "id", "title", "label", "job", "kind")
FAVICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><path d="M4 14 16 4l12 10v14H4z" fill="#b45309"/>'
    '<path d="M12 28V18h8v10" fill="#fde68a"/><path d="M2 15 16 3l14 12" fill="none" stroke="#78350f" stroke-width="3"/></svg>'
)
FONT_FILE = Path(__file__).parent / "static" / "inter.woff2"
"""Inter (Rasmus Andersson, SIL Open Font License), latin subset of the variable font as served by Google Fonts v20."""
THEME_ICON = (
    '<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">'
    '<circle cx="8" cy="8" r="6"/><path d="M8 2a6 6 0 0 1 0 12z" fill="currentColor" stroke="none"/></svg>'
)
_THEME_BOOT = (
    "try { const t = localStorage.getItem('shed-theme'); "
    "if (t === 'light' || t === 'dark') document.documentElement.dataset.theme = t; } catch (e) {}"
)
SAMPLE_CONFIG = """[jobs.repo-status]
tool = "git-context"
every = "10m"
args = { commits = 5 }
cwd = "~/Desktop/Projects/tool-shed"
keep = 300"""

_STYLE = """
@font-face { font-family: Inter; font-style: normal; font-weight: 100 900; font-display: swap;
             src: url(/static/inter.woff2) format("woff2");
             unicode-range: U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, U+0308, U+0329,
                            U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD; }
:root, :root[data-theme="light"] { color-scheme: light;
  --bg: #fafafa; --surface: #ffffff; --surface-2: #f4f4f5; --text: #18181b; --muted: #6b6b76;
  --line: #e4e4e7; --line-strong: #d4d4d8; --accent: #5e6ad2; --accent-strong: #4f5bc4; --accent-bg: #eef0fb;
  --on-accent: #ffffff; --ok: #15803d; --ok-bg: #dcfce7; --fail: #b91c1c; --fail-bg: #fee2e2;
  --warn: #b45309; --warn-bg: #fef3c7;
  --shadow-1: 0 1px 2px rgba(0, 0, 0, .05); --shadow-2: 0 4px 14px rgba(0, 0, 0, .08);
  --s1: 4px; --s2: 8px; --s3: 12px; --s4: 16px; --s6: 24px; --s8: 32px; --radius: 8px;
  --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { color-scheme: dark;
  --bg: #09090b; --surface: #121214; --surface-2: #1c1c1f; --text: #ededf0; --muted: #8b8b93;
  --line: #232326; --line-strong: #2e2e32; --accent: #7c86ff; --accent-strong: #929aff; --accent-bg: #7c86ff1f;
  --on-accent: #0b0b14; --ok: #4ade80; --ok-bg: #14532d55; --fail: #f87171; --fail-bg: #7f1d1d55;
  --warn: #fbbf24; --warn-bg: #78350f55;
  --shadow-1: 0 1px 2px rgba(0, 0, 0, .5); --shadow-2: 0 4px 14px rgba(0, 0, 0, .5); } }
:root[data-theme="dark"] { color-scheme: dark;
  --bg: #09090b; --surface: #121214; --surface-2: #1c1c1f; --text: #ededf0; --muted: #8b8b93;
  --line: #232326; --line-strong: #2e2e32; --accent: #7c86ff; --accent-strong: #929aff; --accent-bg: #7c86ff1f;
  --on-accent: #0b0b14; --ok: #4ade80; --ok-bg: #14532d55; --fail: #f87171; --fail-bg: #7f1d1d55;
  --warn: #fbbf24; --warn-bg: #78350f55;
  --shadow-1: 0 1px 2px rgba(0, 0, 0, .5); --shadow-2: 0 4px 14px rgba(0, 0, 0, .5); }
* { box-sizing: border-box; scrollbar-width: thin; scrollbar-color: var(--line-strong) transparent; }
::-webkit-scrollbar { width: 10px; height: 10px; } ::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--line-strong); border: 3px solid transparent; border-radius: 8px; background-clip: padding-box; }
::selection { background: color-mix(in srgb, var(--accent) 28%, transparent); }
html { scroll-behavior: smooth; }
body { margin: 0; background: var(--bg); color: var(--text); caret-color: var(--accent); -webkit-font-smoothing: antialiased;
       font: 13.5px/1.5 Inter, system-ui, -apple-system, "Segoe UI", sans-serif; font-feature-settings: "cv11", "ss01"; }
main { max-width: 1440px; margin: 0 auto; padding: var(--s4) var(--s6) var(--s8); }
nav, .strip { padding-left: max(var(--s6), calc((100% - 1440px) / 2 + var(--s6))); padding-right: max(var(--s6), calc((100% - 1440px) / 2 + var(--s6))); }
nav { position: sticky; top: 0; z-index: 2; display: flex; flex-wrap: wrap; gap: 0 var(--s1); align-items: stretch; min-height: 48px;
      border-bottom: 1px solid var(--line); background: color-mix(in srgb, var(--surface) 86%, transparent); backdrop-filter: blur(10px); }
nav a { display: inline-flex; align-items: center; color: var(--muted); text-decoration: none; padding: 0 var(--s3); margin: 0;
        border-bottom: 2px solid transparent; transition: color .15s, border-color .15s; }
nav a:hover { color: var(--text); }
nav a[aria-current] { color: var(--text); border-bottom-color: var(--accent); }
nav .brand { color: var(--text); font-weight: 600; font-size: 14px; gap: 8px; padding-left: 0; margin-right: 0; border-bottom: 0; letter-spacing: -.01em; }
nav .brand svg { width: 18px; height: 18px; }
nav .version { align-self: center; margin-right: var(--s3); padding: 1px 8px; border: 1px solid var(--line); border-radius: 999px;
               font-size: 11.5px; color: var(--muted); font-variant-numeric: tabular-nums; white-space: nowrap; }
nav .right { margin-left: auto; display: inline-flex; align-items: center; gap: var(--s3); color: var(--muted); font-size: 12.5px; font-variant-numeric: tabular-nums; }
button.theme { display: inline-flex; align-items: center; justify-content: center; width: 28px; height: 28px; min-height: 0; padding: 0; color: var(--muted); }
button.theme:hover { color: var(--text); }
.strip { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s2) var(--s4); padding-top: 7px; padding-bottom: 7px;
         border-bottom: 1px solid var(--line); background: var(--surface); font-size: 13px; color: var(--muted); font-variant-numeric: tabular-nums; }
.strip b { color: var(--text); font-weight: 600; }
.strip .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--muted); box-shadow: 0 0 0 3px color-mix(in srgb, currentColor 18%, transparent); color: var(--muted); }
.strip.ok .dot { background: var(--ok); color: var(--ok); } .strip.fail .dot { background: var(--fail); color: var(--fail); }
.strip.running .dot { background: var(--warn); color: var(--warn); animation: pulse 1.2s ease-in-out infinite; }
.strip.fail b { color: var(--fail); }
@media (max-width: 640px) { main { padding: var(--s3) var(--s4) var(--s6); } nav, .strip { padding-left: var(--s4); padding-right: var(--s4); }
                            nav { min-height: 44px; } nav a { padding: 0 var(--s2); } nav .version { margin-right: var(--s1); }
                            nav .right { margin-left: 0; width: 100%; padding: 6px 0 8px; }
                            .strip { font-size: 12.5px; gap: var(--s1) var(--s3); } .strip b { flex-basis: calc(100% - 20px); } .scroll { max-height: none; } }
h1 { font-size: 18px; font-weight: 600; letter-spacing: -.012em; margin: var(--s2) 0 var(--s1); }
h1 small { font-size: 13px; font-weight: 500; color: var(--muted); margin-left: var(--s1); letter-spacing: 0; }
h2 { font-size: 14px; font-weight: 600; letter-spacing: -.005em; margin: var(--s6) 0 var(--s2); }
h2 small { font-size: 12.5px; font-weight: 400; color: var(--muted); margin-left: var(--s1); }
h3 { font-size: 13px; font-weight: 600; margin: var(--s4) 0 var(--s1); }
h4 { font-size: 12.5px; font-weight: 600; margin: var(--s3) 0 var(--s1); color: var(--muted); }
small, .muted { color: var(--muted); font-weight: normal; }
a { color: inherit; } main a { text-decoration-color: var(--line-strong); text-underline-offset: 3px; transition: text-decoration-color .15s; }
main a:hover { text-decoration-color: currentColor; }
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 4px; }
.pill { display: inline-flex; align-items: center; gap: 6px; padding: 1px 8px 1px 7px; border-radius: 999px; font-size: 11.5px;
        font-weight: 600; white-space: nowrap; vertical-align: middle; line-height: 1.6; letter-spacing: .01em; }
.pill::before { content: ""; width: 6px; height: 6px; border-radius: 50%; background: currentColor; flex: none; }
.pill.ok { color: var(--ok); background: var(--ok-bg); }
.pill.fail { color: var(--fail); background: var(--fail-bg); }
.pill.running { color: var(--warn); background: var(--warn-bg); }
.pill.running::before { animation: pulse 1.2s ease-in-out infinite; }
.pill.never, .pill.off { color: var(--muted); background: var(--surface-2); }
@keyframes pulse { 50% { opacity: .25; } }
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: var(--s3); }
.card { border: 1px solid var(--line); border-radius: 10px; padding: var(--s3) var(--s4); background: var(--surface); box-shadow: var(--shadow-1);
        transition: border-color .15s, box-shadow .15s; }
.card:hover { border-color: var(--line-strong); box-shadow: var(--shadow-2); }
.card header { display: flex; justify-content: space-between; align-items: center; gap: var(--s2); }
.card .name { font-size: 14px; font-weight: 600; text-decoration: none; letter-spacing: -.005em; }
.card .summary { margin: var(--s2) 0 0; }
.card footer { display: flex; justify-content: space-between; align-items: center; gap: var(--s2); margin-top: var(--s3);
               color: var(--muted); font-size: 12.5px; flex-wrap: wrap; font-variant-numeric: tabular-nums; }
.stats { display: grid; grid-template-columns: repeat(2, 1fr); gap: var(--s2) var(--s3); margin: var(--s3) 0 var(--s2); }
.stats div { display: flex; flex-direction: column; gap: 1px; }
.stats b { font-size: 16px; font-weight: 600; letter-spacing: -.01em; font-variant-numeric: tabular-nums; }
.stats span { font-size: 12px; color: var(--muted); }
button { font: inherit; font-size: 13px; font-weight: 500; min-height: 28px; padding: 3px 11px; border-radius: 6px; cursor: pointer;
         border: 1px solid var(--line-strong); background: var(--surface); color: inherit; box-shadow: var(--shadow-1);
         transition: background .15s, border-color .15s, box-shadow .15s; }
button:hover { background: var(--surface-2); }
button:active { box-shadow: none; }
button.primary { background: var(--accent); color: var(--on-accent); border-color: transparent; }
button.primary:hover { background: var(--accent-strong); }
button.copy { min-height: 0; padding: 1px 8px; font-size: 11.5px; margin-left: var(--s2); box-shadow: none; }
form { display: inline; }
.seg { display: inline-flex; padding: 2px; border: 1px solid var(--line); border-radius: 7px; background: var(--surface-2); vertical-align: middle; margin: 0 var(--s1); }
.seg > * { padding: 1px 9px; border-radius: 5px; text-decoration: none; color: var(--muted); font-weight: 500; font-size: 12.5px; line-height: 1.5; transition: color .15s; }
.seg > a:hover { color: var(--text); }
.seg > b { background: var(--surface); color: var(--text); box-shadow: var(--shadow-1); }
.meta { display: flex; flex-wrap: wrap; gap: var(--s1) var(--s4); align-items: center; color: var(--muted);
        margin: var(--s1) 0 var(--s4); font-variant-numeric: tabular-nums; }
.meta b { color: var(--text); font-weight: 600; }
dl.facts { display: flex; flex-wrap: wrap; gap: var(--s1) var(--s6); margin: var(--s2) 0; padding: 0; }
dl.facts div { display: flex; gap: 6px; } dl.facts dt { color: var(--muted); }
dl.facts dd { margin: 0; font-weight: 500; font-variant-numeric: tabular-nums; }
.scroll { overflow: auto; max-width: 100%; max-height: calc(100vh - 150px); border: 1px solid var(--line);
          border-radius: var(--radius); background: var(--surface); box-shadow: var(--shadow-1); }
table.data { border-collapse: separate; border-spacing: 0; font-size: 13px; min-width: 100%; line-height: 1.4; }
.data th, .data td { border-bottom: 1px solid var(--line); padding: 6px var(--s3); text-align: left; vertical-align: top; }
.data th { position: sticky; top: 0; z-index: 1; background: var(--surface-2); color: var(--muted); font-weight: 500;
           font-size: 12px; white-space: nowrap; }
.data tr:last-child td { border-bottom: 0; }
.data td.num { text-align: right; font-variant-numeric: tabular-nums; }
.data td.time { white-space: nowrap; font-variant-numeric: tabular-nums; }
.data td.key { white-space: nowrap; font-weight: 500; }
.data tr:hover td { background: var(--surface-2); }
.data tr:has(.pill.off) td { color: var(--muted); }
.block { margin: var(--s2) 0 var(--s4); }
.item { margin: var(--s2) 0 var(--s4); padding-left: var(--s3); border-left: 1px solid var(--line-strong); }
pre { background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); padding: 10px 12px; box-shadow: var(--shadow-1);
      overflow: auto; font: 12.5px/1.45 var(--mono); margin: var(--s2) 0; }
code { background: var(--surface-2); border: 1px solid var(--line); padding: 0 5px; border-radius: 4px; font: 12px var(--mono); }
.error, .notice { border: 1px solid; border-radius: var(--radius); padding: 10px 12px; margin: var(--s3) 0; }
.error { border-color: color-mix(in srgb, var(--fail) 40%, transparent); background: var(--fail-bg); }
.notice { border-color: color-mix(in srgb, var(--warn) 40%, transparent); background: var(--warn-bg); }
.empty { border: 1px dashed var(--line-strong); border-radius: 10px; padding: var(--s4) var(--s6); max-width: 760px; background: var(--surface); }
details > summary { cursor: pointer; color: var(--muted); margin: var(--s3) 0 var(--s1); list-style: none; display: flex; align-items: center; gap: 6px; }
details > summary::-webkit-details-marker { display: none; }
details > summary::before { content: ""; flex: none; width: 14px; height: 14px; background: currentColor; transition: transform .15s;
  mask: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16' fill='none' stroke='%23000' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M6 4l4 4-4 4'/%3E%3C/svg%3E") center / contain no-repeat;
  -webkit-mask: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16' fill='none' stroke='%23000' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M6 4l4 4-4 4'/%3E%3C/svg%3E") center / contain no-repeat; }
details[open] > summary::before { transform: rotate(90deg); }
details > summary:hover { color: var(--text); }
.section { margin-top: var(--s3); }
details.tool { border: 1px solid var(--line); border-radius: 10px; padding: var(--s2) var(--s4); margin: var(--s2) 0;
               background: var(--surface); box-shadow: var(--shadow-1); transition: border-color .15s, box-shadow .15s; }
details.tool > summary { color: inherit; margin: 2px 0; } details.tool[open] > summary { margin-bottom: var(--s2); }
details.tool[open], details.tool:target { border-color: var(--line-strong); box-shadow: var(--shadow-2); }
.copyable h4 { display: flex; align-items: center; }
@media (prefers-reduced-motion: reduce) { html { scroll-behavior: auto; }
                                          *, ::before, ::after { transition: none !important; animation: none !important; } }
"""
_SCRIPT = """
const REFRESH_MS = %d;
async function refresh() {
  try {
    const r = await fetch(location.pathname + location.search, {cache: 'no-store'});
    const d = new DOMParser().parseFromString(await r.text(), 'text/html');
    const open = [...document.querySelectorAll('#main details')].map(x => x.open);
    document.getElementById('main').innerHTML = d.getElementById('main').innerHTML;
    document.querySelectorAll('#main details').forEach((x, i) => { if (open[i] !== undefined) x.open = open[i]; });
    const right = d.querySelector('nav .right');
    if (right) document.querySelector('nav .right').innerHTML = right.innerHTML;
    const version = d.getElementById('version');
    if (version && document.getElementById('version')) document.getElementById('version').textContent = version.textContent;
    const strip = d.getElementById('strip');
    if (strip && document.getElementById('strip')) document.getElementById('strip').outerHTML = strip.outerHTML;
  } catch (e) {}
}
setInterval(refresh, REFRESH_MS);
function untilText(s) {
  if (s <= 0) return 'due now';
  if (s < 3600) { const m = Math.floor(s / 60), r = s - m * 60; return m ? `in ${m} min ${r} s` : `in ${r} s`; }
  return s < 129600 ? `in ${Math.round(s / 3600)} h` : `in ${Math.round(s / 86400)} d`;
}
function tick() {
  document.querySelectorAll('.countdown').forEach(el => {
    el.textContent = untilText(Math.round(el.dataset.due - Date.now() / 1000));
  });
}
setInterval(tick, 1000);
function openTool(id) {
  const el = document.getElementById(id);
  if (el && el.tagName === 'DETAILS') el.open = true;
}
document.addEventListener('click', e => {
  const a = e.target.closest('a[href^="#tool-"]');
  if (a) openTool(a.getAttribute('href').slice(1));
});
if (location.hash) openTool(location.hash.slice(1));
document.querySelectorAll('nav a:not(.brand)').forEach(a => {
  const href = a.getAttribute('href'), here = location.pathname;
  const current = href === here || (href === '/' ? here.startsWith('/jobs/') : here.startsWith(href + '/'));
  if (current) a.setAttribute('aria-current', 'page');
});
document.addEventListener('click', e => {
  const b = e.target.closest('button.theme');
  if (!b) return;
  const order = ['system', 'light', 'dark'];
  const next = order[(order.indexOf(document.documentElement.dataset.theme || 'system') + 1) %% 3];
  if (next === 'system') delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = next;
  try { next === 'system' ? localStorage.removeItem('shed-theme') : localStorage.setItem('shed-theme', next); } catch (err) {}
  b.title = 'Theme: ' + next;
});
function copyText(button) {
  const text = button.dataset.copy || button.closest('details, .copyable').querySelector('pre').innerText;
  navigator.clipboard.writeText(text);
  button.textContent = 'copied';
  setTimeout(() => { button.textContent = 'copy'; }, 1200);
}
"""


def esc(text: object) -> str:
    return html.escape(str(text), quote=True)


def plain(text: str) -> str:
    """Drop the Markdown fences and bold markers tools put in terminal output; they read badly in a <pre>."""
    return _FENCE.sub("", text).replace("**", "").strip("\n")


def when(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else "-"


def ago(value: datetime, now: datetime) -> str:
    seconds = (now - value).total_seconds()
    if seconds < 45:
        return "just now"
    if seconds < 3600:
        return f"{max(1, round(seconds / 60))} min ago"
    if seconds < 36 * 3600:
        return f"{round(seconds / 3600)} h ago"
    return f"{round(seconds / 86400)} d ago"


def until(value: datetime, now: datetime) -> str:
    seconds = round((value - now).total_seconds())
    if seconds <= 0:
        return "due now"
    if seconds < 3600:
        minutes, rest = divmod(seconds, 60)
        return f"in {minutes} min {rest} s" if minutes else f"in {rest} s"
    if seconds < 36 * 3600:
        return f"in {round(seconds / 3600)} h"
    return f"in {round(seconds / 86400)} d"


def countdown(due: datetime | None, now: datetime) -> str:
    """The 'next ...' text; the page script ticks it every second until the next refresh replaces it."""
    if due is None:
        return "due now"
    return f'<span class="countdown" data-due="{int(due.timestamp())}">{esc(until(due, now))}</span>'


def took(ms: int) -> str:
    if ms < 1000:
        return f"{ms} ms"
    if ms < 90_000:
        return f"{ms / 1000:.1f} s"
    return f"{ms // 60000} min {round(ms % 60000 / 1000)} s"


def span(seconds: int) -> str:
    """A stretch of time in words: "under a minute", "14 min", "2 h 05 min"."""
    if seconds < 60:
        return "under a minute"
    minutes = round(seconds / 60)
    return f"{minutes} min" if minutes < 60 else f"{minutes // 60} h {minutes % 60:02d} min"


def pill(kind: str, text: str) -> str:
    return f'<span class="pill {kind}">{esc(text)}</span>'


def status_pill(record: RunRecord | None, running: bool = False) -> str:
    if running:
        return pill("running", "running")
    if record is None:
        return pill("never", "never run")
    return pill("ok", "ok") if record.ok else pill("fail", "failed")


# --- rendering result data as tables -------------------------------------------------------------


def _is_scalar(value: object) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _is_simple(value: object) -> bool:
    """Scalar, or a list or dict of scalars: things that fit in one table cell."""
    if _is_scalar(value):
        return True
    items = value.values() if isinstance(value, dict) else value if isinstance(value, list) else None
    return items is not None and all(_is_scalar(v) for v in items)


def fmt_scalar(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, str):
        m = _ISO.match(value)
        return f"{m.group(1)} {m.group(2)}" if m else value
    return str(value)


def _cell(value: object) -> str:
    if _is_scalar(value):
        return esc(fmt_scalar(value))
    if _is_simple(value):
        if isinstance(value, dict):
            joined = ", ".join(f"{_title(str(k))} {fmt_scalar(v)}" for k, v in value.items())
        else:
            joined = ", ".join(fmt_scalar(v) for v in cast(list[object], value))
        return esc(joined) if joined else '<span class="muted">none</span>'
    text = to_json(value, compact=True)
    return f"<code>{esc(text[:80] + ('...' if len(text) > 80 else ''))}</code>"


def _title(key: str) -> str:
    return key.replace("_", " ")


def _item_title(item: dict[str, Any], index: int) -> str:
    for key in _TITLE_KEYS:
        if key in item and _is_scalar(item[key]):
            value = fmt_scalar(item[key])
            return value.upper() if key == "env" else value
    return f"#{index + 1}"


def _facts(values: dict[str, Any]) -> str:
    items = "".join(f"<div><dt>{esc(_title(k))}</dt><dd>{_cell(v)}</dd></div>" for k, v in values.items())
    return f'<dl class="facts">{items}</dl>'


def _table(rows: Sequence[dict[str, Any]]) -> str:
    columns: list[str] = []
    for row in rows:
        columns += [k for k in row if k not in columns]
    head = "".join(f"<th>{esc(_title(c))}</th>" for c in columns)
    body = []
    for row in rows:
        cells = []
        for c in columns:
            value = row.get(c)
            css = ' class="num"' if isinstance(value, (int, float)) and not isinstance(value, bool) else ""
            cells.append(f"<td{css}>{_cell(value)}</td>")
        body.append(f"<tr>{''.join(cells)}</tr>")
    return f'<div class="scroll"><table class="data"><tr>{head}</tr>{"".join(body)}</table></div>'


def render_data(value: Any, depth: int = 0) -> str:
    """Turn a tool's JSON result into facts, tables and sections; nothing here is tool-specific."""
    tag = "h3" if depth == 0 else "h4"
    if _is_scalar(value):
        return f"<p>{_cell(value)}</p>"
    if isinstance(value, list):
        if not value:
            return '<p class="muted">none</p>'
        if all(isinstance(v, dict) for v in value):
            if all(_is_simple(x) for v in value for x in v.values()):
                return _table(value)
            return "".join(
                f'<section class="item"><{tag}>{esc(_item_title(v, i))}</{tag}>{render_data(v, depth + 1)}</section>'
                for i, v in enumerate(value)
            )
        if all(_is_scalar(v) for v in value):
            return f"<p>{_cell(value)}</p>"
        return "".join(render_data(v, depth + 1) for v in value)
    if isinstance(value, dict):
        simple = {k: v for k, v in value.items() if _is_simple(v) or v in ({}, [])}
        nested = {k: v for k, v in value.items() if k not in simple}
        parts = [_facts(simple)] if simple else []
        for key, item in nested.items():
            count = f" <small>{len(item)}</small>" if isinstance(item, list) else ""
            parts.append(
                f'<div class="block"><{tag}>{esc(_title(key))}{count}</{tag}>{render_data(item, depth + 1)}</div>'
            )
        return "".join(parts)
    return f"<p>{esc(value)}</p>"


def result_view(record: RunRecord) -> str:
    parts: list[str] = []
    if not record.ok:
        parts.append(f'<div class="error"><strong>Failed:</strong> {esc(record.error)}</div>')
    data = record.result
    structured = isinstance(data, (dict, list)) and bool(data)
    if structured:
        parts.append(f'<div class="result">{render_data(data)}</div>')
    text = plain(record.rendered)
    if text:
        state = "" if structured else " open"
        parts.append(
            f"<details{state}><summary>Text output <small>as the CLI prints it</small>"
            f'<button class="copy" onclick="copyText(this)">copy</button></summary><pre>{esc(text)}</pre></details>'
        )
    if record.log:
        parts.append(
            f"<details><summary>Tool log <small>{len(record.log)} lines</small></summary>"
            f"<pre>{esc(chr(10).join(record.log))}</pre></details>"
        )
    parts.append(
        '<details><summary>Result as JSON<button class="copy" onclick="copyText(this)">copy</button></summary>'
        f"<pre>{esc(to_json(data, compact=False))}</pre></details>"
    )
    return "\n".join(parts)


# --- pages ----------------------------------------------------------------------------------------


def page(
    title: str,
    body: str,
    now: datetime,
    *,
    scheduler: bool,
    refresh_s: int = REFRESH_S,
    views: Mapping[str, str] | None = None,
    strip: str = "",
) -> str:
    sched = pill("ok", "scheduler on") if scheduler else pill("off", "scheduler off")
    links = "".join(f'<a href="/view/{esc(job)}">{esc(label)}</a>' for job, label in (views or {}).items())
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<link rel="icon" href="/favicon.svg" type="image/svg+xml">\n'
        f"<title>{esc(title)} - tool-shed</title>\n<script>{_THEME_BOOT}</script>\n<style>{_STYLE}</style>\n</head>\n<body>\n"
        f'<nav><a class="brand" href="/">{FAVICON}tool-shed</a>'
        f'<span class="version" id="version" title="shed version">v{esc(shed_version())}</span>'
        '<a href="/">Jobs</a><a href="/tools">Tools</a>'
        f'<a href="/tunnels">Tunnels</a>{links}'
        f'<span class="right">{sched}<span>refreshed {now:%H:%M:%S}, every {refresh_s} s</span>'
        f'<button class="theme" type="button" aria-label="Switch theme" title="Theme: system">{THEME_ICON}</button></span></nav>\n'
        f'{strip}<main id="main">\n{body}\n</main>\n<script>{_SCRIPT % (refresh_s * 1000)}</script>\n</body>\n</html>\n'
    )


def health_strip(jobs: Sequence[Job], store: RunStore, running: set[str], now: datetime) -> str:
    """One line under the nav: whether anything is failing, then the stats behind that answer."""
    h = health(jobs, store, running)
    if not jobs:
        return (
            f'<div id="strip" class="strip off"><span class="dot"></span><b>{esc(h["headline"])}</b></div>\n'
        )
    stats = [f"{h['jobs']} job" + ("s" if h["jobs"] != 1 else "")]
    if last := h["last_run"]:
        stats.append(f"last run {ago(last['started'], now)} ({took(last['duration_ms'])})")
    if due := h["next_due"]:
        stats.append(f"next {countdown(due, now)}")
    return (
        f'<div id="strip" class="strip {h["state"]}"><span class="dot"></span><b>{esc(h["headline"])}</b>'
        + "".join(f"<span>{stat}</span>" for stat in stats)
        + "</div>\n"
    )


def run_now_form(job: str) -> str:
    return (
        f'<form method="post" action="/jobs/{esc(job)}/run"><button class="primary">Run now</button></form>'
    )


def view_link(job: str, views: Mapping[str, str] | None) -> str:
    label = (views or {}).get(job)
    return f' &middot; <a href="/view/{esc(job)}">{esc(label)}</a>' if label else ""


def dashboard(
    jobs: Sequence[Job],
    store: RunStore,
    running: set[str],
    cron_file: Path,
    now: datetime,
    views: Mapping[str, str] | None = None,
) -> str:
    cards = []
    for job in jobs:
        latest = store.latest(job.name)
        due = next_due(job, latest)
        last = f"last {ago(latest.started, now)} ({took(latest.duration_ms)})" if latest else "not run yet"
        nxt = f"next {countdown(due, now)}" if job.enabled else "disabled"
        cards.append(
            f'<article class="card"><header><a class="name" href="/jobs/{esc(job.name)}">{esc(job.name)}</a>'
            f"{status_pill(latest, job.name in running)}</header>"
            f'<div class="muted">{esc(job.tool)} &middot; every {esc(job.every)}{view_link(job.name, views)}</div>'
            f'<p class="summary">{esc(latest.summary) if latest else "<span class=muted>no result yet</span>"}</p>'
            f'<footer><span title="{when(latest.started) if latest else ""}">{esc(last)} &middot; {nxt}</span>'
            f"{run_now_form(job.name)}</footer></article>"
        )
    if cards:
        body = f'<div class="cards">{"".join(cards)}</div>'
    else:
        body = (
            '<div class="empty"><h2 style="margin-top:0">No jobs yet</h2>'
            f"<p>Add one <code>[jobs.&lt;name&gt;]</code> table per job to <code>{esc(cron_file)}</code>, for example:</p>"
            f'<pre>{esc(SAMPLE_CONFIG)}</pre><p>Any tool from the <a href="/tools">catalog</a> works; '
            "<code>args</code> are its flags in snake_case. The page picks the job up on its next refresh.</p></div>"
        )
    orphans = sorted(set(store.jobs()) - {job.name for job in jobs})
    extra = (
        f'<p class="muted">runs kept for jobs no longer configured: {esc(", ".join(orphans))}</p>'
        if orphans
        else ""
    )
    return f"<h1>Jobs <small>{len(jobs)}</small></h1>\n{body}\n{extra}"


def history_table(job: str, entries: Sequence[tuple[str, RunRecord]], now: datetime) -> str:
    rows = []
    for stem, r in entries:
        rows.append(
            f'<tr><td><a href="/jobs/{esc(job)}/runs/{esc(stem)}" title="{when(r.started)}">{ago(r.started, now)}</a></td>'
            f'<td>{when(r.started)}</td><td>{status_pill(r)}</td><td class="num">{took(r.duration_ms)}</td>'
            f"<td>{esc(r.summary)}</td></tr>"
        )
    head = "<tr><th>run</th><th>started</th><th>status</th><th>took</th><th>summary</th></tr>"
    return f'<div class="scroll"><table class="data">{head}{"".join(rows)}</table></div>'


def job_page(
    job: Job,
    store: RunStore,
    running: bool,
    notice: str | None,
    now: datetime,
    history: int = 50,
    view_label: str | None = None,
) -> str:
    latest = store.latest(job.name)
    due = next_due(job, latest)
    meta = [f"<span>tool <b>{esc(job.tool)}</b></span>", f"<span>every <b>{esc(job.every)}</b></span>"]
    if view_label:
        meta.append(f'<span><a href="/view/{esc(job.name)}">{esc(view_label)} page</a></span>')
    if latest:
        meta.append(
            f'<span title="{when(latest.started)}">last run <b>{ago(latest.started, now)}</b> ({took(latest.duration_ms)})</span>'
        )
    meta.append(f"<span>next <b>{countdown(due, now)}</b></span>" if job.enabled else pill("off", "disabled"))
    meta.append(run_now_form(job.name))
    parts = [
        f"<h1>{esc(job.name)} {status_pill(latest, running)}</h1>",
        f'<div class="meta">{"".join(meta)}</div>',
    ]
    if running:
        parts.append('<p class="notice">Running now. This page updates when it finishes.</p>')
    elif notice:
        parts.append(f'<p class="notice">{esc(notice)}</p>')
    if latest is None:
        parts.append(
            '<div class="empty"><p>No run recorded yet. Press <b>Run now</b>, or wait for the scheduler.</p></div>'
        )
    else:
        parts.append(f"<h2>Latest result <small>{when(latest.started)}</small></h2>{result_view(latest)}")
        parts.append(f"<h2>History <small>{store.count(job.name)} runs</small></h2>")
        parts.append(history_table(job.name, store.entries(job.name, history), now))
    settings = {
        "tool": job.tool,
        "every": job.every,
        "keep": job.keep,
        "cwd": job.cwd or "~",
        "enabled": job.enabled,
    }
    parts.append(f"<h2>Settings</h2>{_facts(settings)}")
    parts.append(f"<h4>args</h4>{_facts(job.args) if job.args else '<p class=muted>none</p>'}")
    parts.append(
        f'<p class="muted">Edit <code>{esc(CRON_FILE)}</code> to change these; the scheduler reads it on every tick.</p>'
    )
    return "\n".join(parts)


def run_page(job: str, stem: str, record: RunRecord, now: datetime) -> str:
    return "\n".join(
        [
            f'<h1><a href="/jobs/{esc(job)}">{esc(job)}</a> <small>run {esc(stem)}</small> {status_pill(record)}</h1>',
            f'<div class="meta"><span>started <b>{when(record.started)}</b> ({ago(record.started, now)})</span>'
            f"<span>took <b>{took(record.duration_ms)}</b></span><span>tool <b>{esc(record.tool)}</b>"
            f"{' v' + esc(record.tool_version) if record.tool_version else ''}</span>"
            f"{'<span>shed <b>v' + esc(record.version) + '</b></span>' if record.version else ''}</div>",
            result_view(record),
            f"<h2>Arguments</h2>{_facts(record.args) if record.args else '<p class=muted>none</p>'}",
        ]
    )


def first_sentence(text: str) -> str:
    return re.split(r"(?<=[.!?])\s", text.strip(), maxsplit=1)[0]


def tools_page(tools_dir: Path) -> str:
    tools = list(load_all_tools(tools_dir))
    group = {name: folder for folder, names in list_groups(tools_dir).items() for name in names}
    index: list[str] = []
    details: list[str] = []
    for tool in tools:
        example = f"<code>{esc(tool.examples[0])}</code>" if tool.examples else ""
        index.append(
            f'<tr><td class="key"><a href="#tool-{esc(tool.name)}">{esc(tool.name)}</a></td>'
            f'<td class="muted">{esc(group[tool.name])}</td>'
            f"<td>{esc(first_sentence(tool.description))}</td><td>{example}</td></tr>"
        )
        rows = []
        for flag in describe_flags(tool.input):
            default = (
                "required"
                if flag.required
                else ("" if flag.default in (None, [], ()) else fmt_scalar(flag.default))
            )
            choices = f" <small>one of {esc(', '.join(flag.choices))}</small>" if flag.choices else ""
            rows.append(
                f"<tr><td><code>{esc(flag.option)}</code></td><td>{esc(flag.kind)}</td><td>{esc(default)}</td>"
                f"<td>{esc(flag.description)}{choices}</td></tr>"
            )
        table = (
            f'<div class="scroll"><table class="data"><tr><th>flag</th><th>type</th><th>default</th><th>description</th></tr>'
            f"{''.join(rows)}</table></div>"
            if rows
            else '<p class="muted">no flags</p>'
        )
        examples = ""
        if tool.examples:
            examples = (
                '<div class="copyable"><h4>examples<button class="copy" onclick="copyText(this)">copy</button></h4>'
                f"<pre>{esc(chr(10).join(tool.examples))}</pre></div>"
            )
        details.append(
            f'<details class="tool" id="tool-{esc(tool.name)}"><summary><b>{esc(tool.name)}</b> '
            f'<span class="muted">{len(rows)} flag{"s" if len(rows) != 1 else ""}, '
            f"{len(tool.examples)} example{'s' if len(tool.examples) != 1 else ''}</span></summary>"
            f"<p>{esc(tool.description)}</p>{table}{examples}</details>"
        )
    return (
        f"<h1>Tools <small>{len(tools)}</small></h1>"
        '<p class="muted">Every tool is also a CLI command and an MCP tool; the flags are its inputs. '
        "Click a name for its flags and examples.</p>"
        '<div class="scroll"><table class="data">'
        "<tr><th>tool</th><th>folder</th><th>what it does</th><th>example</th></tr>"
        f"{''.join(index)}</table></div>"
        f'<h2 class="section">Details</h2>{"".join(details)}'
    )


# --- tool views -----------------------------------------------------------------------------------

ViewFn = Callable[[str, Sequence[RunRecord], datetime, Mapping[str, str]], str]


def tool_view(tool: str, tools_dir: Path) -> tuple[str, ViewFn] | None:
    """(title, view function) from tools/<tool>/view.py, which gives that tool's jobs their own page."""
    try:
        module = load_tool_module(tool, tools_dir)
    except UsageError:
        return None
    name = f"{module.__package__}.view"
    try:
        found = importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name == name:
            return None
        raise
    return str(getattr(found, "TITLE", tool)), cast(ViewFn, found.view)


def tunnels_page(
    manager: TunnelManager, dashboard_port: int, now: datetime, notice: str | None = None
) -> str:
    """The Tunnels page: install state, an add form, and one row per tunnel with its public URL and status."""
    parts = [
        "<h1>Tunnels <small>Cloudflare quick tunnels to local ports, alive while this dashboard runs</small></h1>"
    ]
    if notice:
        parts.append(f'<p class="error">{esc(notice)}</p>')
    install = manager.install_state()
    if install == "installing":
        parts.append('<p class="summary">Installing cloudflared with Homebrew, this takes a minute...</p>')
    elif install == "missing":
        parts.append(
            '<form method="post" action="/tunnels/install" class="meta"><span>cloudflared is not installed.</span>'
            '<button class="primary" type="submit">Install with Homebrew</button></form>'
        )
    elif install == "failed":
        parts.append(f'<p class="error">cloudflared install failed: {esc(manager.install_error() or "")}</p>')
    elif install == "no-brew":
        parts.append(
            '<p class="error">cloudflared is not installed and Homebrew is missing; install it from '
            '<a href="https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/'
            'installation/">Cloudflare</a> and reload.</p>'
        )
    parts.append(
        '<form method="post" action="/tunnels" class="meta">'
        '<input name="target" placeholder="http://localhost:8000" size="28" required>'
        '<input name="label" placeholder="label (optional)" size="18">'
        '<button class="primary" type="submit" name="start" value="1">Add and start</button>'
        '<button type="submit">Add</button></form>'
    )
    statuses = manager.statuses()
    if not statuses:
        parts.append('<div class="empty"><p>No tunnels yet. Add a local URL above.</p></div>')
        return "\n".join(parts)
    rows = []
    for t in statuses:
        port = urlsplit(t.target).port
        warn = (
            ' <span class="muted" title="anyone with the link can use this dashboard">(this dashboard, no login)</span>'
            if port == dashboard_port
            else ""
        )
        if t.state in ("running", "unreachable") and t.public_url:
            public = (
                f'<a href="{esc(t.public_url)}" target="_blank" rel="noopener">{esc(t.public_url)}</a>'
                f'<button class="copy" type="button" data-copy="{esc(t.public_url)}" onclick="copyText(this)">copy'
                "</button>"
            )
            if t.state == "unreachable":
                why = [f"no Cloudflare connection since {esc(when(t.since))}, cloudflared is reconnecting"]
                if t.slept_from and t.slept_until and t.asleep_s is not None:
                    why.append(
                        f"Since it was last connected the Mac was asleep for {span(t.asleep_s)}, between "
                        f"{t.slept_from:%H:%M} and {t.slept_until:%H:%M}. After a long sleep Cloudflare may not "
                        "take the tunnel back; restart it for a new URL."
                    )
                if t.last_error:
                    why.append(f"cloudflared: {esc(t.last_error)}")
                state = pill("fail", "unreachable") + f" <span class=muted>{'<br>'.join(why)}</span>"
            else:
                unknown = ", health check not answering" if t.connections is None else ""
                state = (
                    pill("ok", "running") + f" <span class=muted>since {esc(when(t.since))}{unknown}</span>"
                )
        elif t.state == "starting":
            waiting = (
                "waiting for Cloudflare to accept the connection"
                if t.public_url
                else "waiting for cloudflared"
            )
            public, state = f'<span class="muted">{waiting}...</span>', pill("running", "starting")
        elif t.state == "failed":
            public, state = "-", pill("fail", "failed") + f" <span class=muted>{esc(t.error or '')}</span>"
        else:
            public, state = "-", pill("off", "stopped")
        action = "stop" if t.state in ("running", "starting", "unreachable") else "start"
        restart = (
            f'<form method="post" action="/tunnels/{t.id}/restart" style="display:inline">'
            '<button class="primary" type="submit" title="new quick tunnel, new URL">restart</button></form> '
            if t.state == "unreachable"
            else ""
        )
        actions = restart + (
            f'<form method="post" action="/tunnels/{t.id}/{action}" style="display:inline">'
            f'<button type="submit">{action}</button></form> '
            f'<form method="post" action="/tunnels/{t.id}/remove" style="display:inline">'
            '<button type="submit" title="stop and forget">remove</button></form>'
        )
        label = f"<b>{esc(t.label)}</b><br>" if t.label else ""
        rows.append(
            f"<tr><td>{label}<code>{esc(t.target)}</code>{warn}</td><td>{public}</td><td>{state}</td>"
            f"<td>{actions}</td></tr>"
        )
    parts.append(
        '<div class="scroll"><table class="data"><tr><th>local target</th><th>public URL</th><th>status</th>'
        f"<th>actions</th></tr>{''.join(rows)}</table></div>"
    )
    return "\n".join(parts)


# --- server ---------------------------------------------------------------------------------------


class UIServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def server_bind(self) -> None:
        """Bind without http.server's reverse-DNS lookup of the address, which can hang behind a VPN."""
        TCPServer.server_bind(self)
        host, port = self.socket.getsockname()[:2]
        self.server_name = str(host)
        self.server_port = int(port)

    def __init__(
        self,
        address: tuple[str, int],
        *,
        cron_file: Path = CRON_FILE,
        state_dir: Path = STATE_DIR,
        tools_dir: Path = TOOLS_DIR,
        schedule: bool = False,
        tunnels: TunnelManager | None = None,
    ) -> None:
        super().__init__(address, Handler)
        self.cron_file = cron_file
        self.tools_dir = tools_dir
        self.store = RunStore(state_dir / "runs")
        self.tunnels = tunnels or TunnelManager(state_dir / "tunnels.json")
        self.tunnel_notice: str | None = None
        self.lock_file = state_dir / "cron.lock"
        self.log_file = state_dir / "cron.log"
        self.schedule = schedule
        self.notices: dict[str, str] = {}
        self.running: set[str] = set()
        self._views: dict[str, tuple[str, ViewFn] | None] = {}
        self._stop = threading.Event()
        self._scheduler: threading.Thread | None = None

    def url(self) -> str:
        host, port = self.server_address[0], self.server_address[1]
        return f"http://{host}:{port}"

    def jobs(self) -> list[Job]:
        return load_jobs(self.cron_file)

    def view_for(self, tool: str) -> tuple[str, ViewFn] | None:
        if tool not in self._views:
            self._views[tool] = tool_view(tool, self.tools_dir)
        return self._views[tool]

    def views(self, jobs: Sequence[Job] | None = None) -> dict[str, str]:
        """Job name -> page label for every job whose tool ships a view; the job name is added on a clash."""
        found = {
            job.name: v[0]
            for job in (self.jobs() if jobs is None else jobs)
            if (v := self.view_for(job.tool))
        }
        dupes = {title for title, n in Counter(found.values()).items() if n > 1}
        return {job: f"{title} ({job})" if title in dupes else title for job, title in found.items()}

    def run_jobs(self, *, force: bool = False, only: Sequence[str] = ()) -> list[RunRecord]:
        with cron_lock(self.lock_file):
            records = run_due(self.jobs(), self.store, force=force, only=only, tools_dir=self.tools_dir)
        append_log(self.log_file, records)
        return records

    def run_async(self, name: str) -> None:
        """Run one job on a thread; the page the browser refreshes shows the result when it lands."""

        def work() -> None:
            try:
                self.run_jobs(force=True, only=[name])
                self.notices.pop(name, None)
            except ToolError as exc:
                self.notices[name] = str(exc)
            finally:
                self.running.discard(name)

        self.running.add(name)
        self.notices.pop(name, None)
        threading.Thread(target=work, daemon=True, name=f"run-{name}").start()

    def start_scheduler(self) -> None:
        def loop() -> None:
            while True:
                try:
                    self.run_jobs()
                except (ToolError, OSError) as exc:  # keep the loop alive; the next tick retries
                    print(f"cron: {exc}", file=sys.stderr)
                if self._stop.wait(AGENT_INTERVAL_S):
                    return

        self._scheduler = threading.Thread(target=loop, daemon=True, name="shed-cron")
        self._scheduler.start()

    def stop(self) -> None:
        self._stop.set()
        self.tunnels.stop_all()
        self.shutdown()
        self.server_close()


_JOB = re.compile(r"^/jobs/([^/]+)$")
_RUN = re.compile(r"^/jobs/([^/]+)/runs/([^/]+)$")
_RUN_NOW = re.compile(r"^/jobs/([^/]+)/run$")
_TUNNEL_ACTION = re.compile(r"^/tunnels/([0-9a-f]+)/(start|stop|restart|remove)$")
_VIEW = re.compile(r"^/view/([^/]+)$")
_API_LATEST = re.compile(r"^/api/jobs/([^/]+)/latest$")
_API_RUNS = re.compile(r"^/api/jobs/([^/]+)/runs$")
_API_RUN = re.compile(r"^/api/jobs/([^/]+)/runs/([^/]+)$")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        if os.environ.get("SHED_DEBUG"):
            super().log_message(format, *args)

    @property
    def ui(self) -> UIServer:
        return cast(UIServer, self.server)

    def _send(self, body: str, content_type: str = "text/html; charset=utf-8", status: int = 200) -> None:
        self._send_bytes(body.encode("utf-8"), content_type, status)

    def _send_bytes(self, data: bytes, content_type: str, status: int = 200, cache: str = "no-store") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(data)

    def _page(self, title: str, body: str, status: int = 200, refresh_s: int = REFRESH_S) -> None:
        self._send(
            page(
                title,
                body,
                local_now(),
                scheduler=self.ui.schedule,
                refresh_s=refresh_s,
                **self._chrome(),
            ),
            status=status,
        )

    def _chrome(self) -> dict[str, Any]:
        """Nav links and the health strip; an unreadable cron file leaves them empty rather than failing the page."""
        try:
            jobs = self.ui.jobs()
        except ToolError:
            return {}
        strip = health_strip(jobs, self.ui.store, self.ui.running, local_now())
        return {"views": self.ui.views(jobs), "strip": strip}

    def _json(self, payload: object, status: int = 200) -> None:
        self._send(to_json(payload, compact=True), "application/json", status)

    def _not_found(self, what: str) -> None:
        self._page("not found", f"<h1>Not found</h1><p>{esc(what)}</p>", status=404)

    def _job(self, name: str) -> Job | None:
        return next((job for job in self.ui.jobs() if job.name == name), None)

    def do_GET(self) -> None:
        url = urlsplit(self.path)
        path = url.path.rstrip("/") or "/"
        query = parse_qs(url.query)
        ui = self.ui
        now = local_now()
        try:
            if path in ("/favicon.svg", "/favicon.ico"):
                self._send(FAVICON, "image/svg+xml")
            elif path == "/static/inter.woff2":
                self._send_bytes(
                    FONT_FILE.read_bytes(), "font/woff2", cache="public, max-age=31536000, immutable"
                )
            elif path == "/health":
                self._json(
                    {
                        "ok": True,
                        "schedule": ui.schedule,
                        "running": sorted(ui.running),
                        "pid": os.getpid(),
                        "version": shed_version(),
                    }
                )
            elif path == "/":
                busy = bool(ui.running)
                jobs = ui.jobs()
                body = dashboard(jobs, ui.store, ui.running, ui.cron_file, now, views=ui.views(jobs))
                self._page("jobs", body, refresh_s=BUSY_REFRESH_S if busy else REFRESH_S)
            elif path == "/tools":
                self._page("tools", tools_page(ui.tools_dir))
            elif path == "/tunnels":
                busy = ui.tunnels.install_state() == "installing" or any(
                    t.state in ("starting", "unreachable") for t in ui.tunnels.statuses()
                )
                notice, ui.tunnel_notice = ui.tunnel_notice, None
                body = tunnels_page(ui.tunnels, ui.server_address[1], now, notice)
                self._page("tunnels", body, refresh_s=BUSY_REFRESH_S if busy else REFRESH_S)
            elif path == "/api/tunnels":
                self._json({"cloudflared": ui.tunnels.install_state(), "tunnels": ui.tunnels.statuses()})
            elif path == "/api/jobs":
                self._json([job_summary(job, ui.store, ui.running) for job in ui.jobs()])
            elif path == "/api/tools":
                tools = load_all_tools(ui.tools_dir)
                self._json(
                    [
                        {"name": t.name, "description": t.description, "input": t.input.model_json_schema()}
                        for t in tools
                    ]
                )
            elif m := _JOB.match(path):
                job = self._job(m.group(1))
                if job is None:
                    return self._not_found(f"job {m.group(1)}")
                running = job.name in ui.running
                label = ui.views([job]).get(job.name)
                body = job_page(job, ui.store, running, ui.notices.get(job.name), now, view_label=label)
                self._page(job.name, body, refresh_s=BUSY_REFRESH_S if running else REFRESH_S)
            elif m := _VIEW.match(path):
                job = self._job(m.group(1))
                if job is None:
                    return self._not_found(f"job {m.group(1)}")
                found = ui.view_for(job.tool)
                if found is None:
                    return self._not_found(f"{job.tool} has no dashboard page")
                title, view = found
                runs = [record for _, record in ui.store.entries(job.name, job.keep)]
                self._page(title, view(job.name, runs, now, {k: v[0] for k, v in query.items() if v}))
            elif m := _RUN.match(path):
                record = ui.store.get(m.group(1), m.group(2))
                if record is None:
                    return self._not_found(f"run {m.group(2)} of {m.group(1)}")
                self._page(f"{m.group(1)} {m.group(2)}", run_page(m.group(1), m.group(2), record, now))
            elif m := _API_LATEST.match(path):
                record = ui.store.latest(m.group(1))
                self._json(record, 200) if record else self._json({"error": "no runs"}, 404)
            elif m := _API_RUNS.match(path):
                limit = int(query.get("limit", ["20"])[0])
                self._json(
                    [{"id": stem, **run_summary(r)} for stem, r in ui.store.entries(m.group(1), limit)]
                )
            elif m := _API_RUN.match(path):
                record = ui.store.get(m.group(1), m.group(2))
                self._json(record, 200) if record else self._json({"error": "no such run"}, 404)
            else:
                self._not_found(path)
        except ToolError as exc:
            self._page("error", f"<h1>Error</h1><pre>{esc(str(exc))}</pre>", status=500)

    def _form(self) -> dict[str, str]:
        length = int(self.headers.get("Content-Length") or 0)
        data = parse_qs(self.rfile.read(length).decode("utf-8", "replace")) if length else {}
        return {k: v[0] for k, v in data.items() if v}

    def _tunnel_action(self, path: str) -> bool:
        """Handle the Tunnels page's forms; every outcome lands back on /tunnels, errors as a notice."""
        ui = self.ui
        try:
            if path == "/tunnels":
                form = self._form()
                row = ui.tunnels.add(form.get("target", ""), form.get("label"))
                if form.get("start"):
                    ui.tunnels.start(row.id)
            elif path == "/tunnels/install":
                ui.tunnels.install()
            elif m := _TUNNEL_ACTION.match(path):
                tunnel_id, action = m.group(1), m.group(2)
                if action == "start":
                    ui.tunnels.start(tunnel_id)
                elif action == "stop":
                    ui.tunnels.stop(tunnel_id)
                elif action == "restart":
                    ui.tunnels.restart(tunnel_id)
                else:
                    ui.tunnels.remove(tunnel_id)
            else:
                return False
        except (ValueError, KeyError, RuntimeError) as exc:
            ui.tunnel_notice = str(exc) if not isinstance(exc, KeyError) else f"no tunnel {exc}"
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", "/tunnels")
        self.end_headers()
        return True

    def do_POST(self) -> None:
        path = urlsplit(self.path).path.rstrip("/") or "/"
        if self._tunnel_action(path):
            return
        if m := _RUN_NOW.match(path):
            if self._job(m.group(1)) is None:
                return self._not_found(f"job {m.group(1)}")
            self.ui.run_async(m.group(1))
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", f"/jobs/{m.group(1)}")
            self.end_headers()
            return
        self._not_found(path)


def serve_ui(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    *,
    schedule: bool = False,
    open_browser: bool = False,
    cron_file: Path = CRON_FILE,
    state_dir: Path = STATE_DIR,
) -> None:
    """Serve the dashboard until interrupted. With `schedule`, also run due jobs every minute."""
    try:
        server = UIServer((host, port), cron_file=cron_file, state_dir=state_dir, schedule=schedule)
    except OSError as exc:
        raise ToolError(
            f"cannot listen on {host}:{port}: {exc.strerror or exc}. If the launchd agent is installed it is "
            f"already serving there; run `shed cron` to check, or pick another --port."
        ) from exc
    if schedule:
        server.start_scheduler()
    print(
        f"shed ui at {server.url()}" + (", running due jobs every minute" if schedule else ""),
        file=sys.stderr,
    )
    if open_browser:
        webbrowser.open(server.url())
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
