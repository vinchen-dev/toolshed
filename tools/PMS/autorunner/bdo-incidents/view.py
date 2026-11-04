"""Dashboard page for jobs that run bdo-incidents: status per environment, the latest incident table
and a timeline stitched from every kept run. The dashboard serves it at /view/<job>."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta

from pydantic import BaseModel, ValidationError

from shed.cron import RunRecord
from shed.lib.manila import MANILA
from shed.ui import ago, esc, pill, took, when

from .incidents import Incident, dur
from .tool import EnvReport, Report

TITLE = "BDO sessions"
WINDOWS = (1, 6, 24, 48)
ENVS = ("qat", "prod")
MAX_ROWS = 400
KINDS: dict[str, tuple[str, str]] = {
    "FLAG": ("fail", "invalidated"),
    "FAIL": ("fail", "relogin failed"),
    "SCRAPER_STOP": ("fail", "scraper stop"),
    "LOC": ("running", "first failure"),
    "STALE": ("running", "stale grid"),
    "RETRY_OK": ("ok", "retry ok"),
    "SAVED": ("ok", "relogin saved"),
    "VISIT": ("off", "visit"),
}


class Entry(BaseModel):
    env: str
    time: datetime
    kind: str
    text: str


def reports(runs: Sequence[RunRecord]) -> list[Report]:
    """Results of the successful runs, newest first; a result this version cannot read is skipped."""
    out: list[Report] = []
    for record in runs:
        if record.ok and record.result is not None:
            try:
                out.append(Report.model_validate(record.result))
            except ValidationError:
                continue
    return out


def timeline(found: Sequence[Report], since: datetime) -> list[Entry]:
    """Events after `since` from every run, newest first, de-duplicated across overlapping run windows."""
    seen: set[tuple[str, datetime, str, str]] = set()
    out: list[Entry] = []
    for report in found:
        for env in report.envs:
            for e in env.events:
                key = (env.env, e.time, e.kind, e.text)
                if e.time >= since and key not in seen:
                    seen.add(key)
                    out.append(Entry(env=env.env, time=e.time, kind=e.kind, text=e.text))
    out.sort(key=lambda e: e.time, reverse=True)
    return out


def kind_pill(kind: str) -> str:
    cls, label = KINDS.get(kind, ("off", kind.lower()))
    return pill(cls, label)


def _choose(text: str | None, choices: Sequence[int], default: int) -> int:
    try:
        value = int(text or "")
    except ValueError:
        return default
    return value if value in choices else default


def _links(param: str, current: str, choices: Sequence[tuple[str, str]], base: Mapping[str, str]) -> str:
    """A segmented control of (value, label) pairs; the current value is the pressed segment."""
    out = []
    for value, label in choices:
        if value == current:
            out.append(f"<b>{esc(label)}</b>")
        else:
            query = "&".join(f"{k}={v}" for k, v in {**base, param: value}.items())
            out.append(f'<a href="?{esc(query)}">{esc(label)}</a>')
    return f'<span class="seg">{"".join(out)}</span>'


def env_card(r: EnvReport, full: bool, last: Entry | None) -> str:
    if r.error:
        status, body = pill("fail", "pull failed"), f'<p class="summary">{esc(r.error)}</p>'
    elif not full:
        status = pill("ok", "ok")
        body = (
            '<p class="summary muted">events only; add <code>full = true</code> to the job args '
            "for the incident totals</p>"
        )
    else:
        status = pill("fail", "open incident") if any(i.open for i in r.incidents) else pill("ok", "ok")
        body = (
            '<div class="stats">'
            f"<div><b>{len(r.incidents)}</b><span>incidents</span></div>"
            f"<div><b>{esc(dur(r.total_outage_seconds))}</b><span>down, {r.downtime_percent}%</span></div>"
            f"<div><b>{r.failed_attempts}</b><span>failed relogins</span></div>"
            f"<div><b>{len(r.scraper_stops)}</b><span>scraper stops</span></div></div>"
        )
    recent = (
        f'<div class="muted">last event {esc(when(last.time))}: {esc(last.text)}</div>'
        if last
        else '<div class="muted">no events in this window</div>'
    )
    return (
        f'<article class="card"><header><span class="name">{esc(r.env.upper())}</span>{status}</header>'
        f"{body}{recent}</article>"
    )


def incident_table(rows: Sequence[Incident]) -> str:
    if not rows:
        return '<p class="muted">none</p>'
    head = (
        "<tr><th>flag</th><th>life before</th><th>hang location</th><th>retry recovered</th>"
        "<th>attempts</th><th>failed steps</th><th>saved</th><th>outage</th></tr>"
    )
    body = []
    for i in rows:
        steps = ", ".join(f"{n}x {w}" for w, n in i.failed_steps.items()) or "-"
        flag = esc(when(i.flag)) + (" " + pill("fail", "open") if i.open else "")
        body.append(
            f'<tr><td class="time">{flag}</td><td>{esc(i.life_before_flag)}</td>'
            f"<td>{esc(i.hang_location)}</td><td>{esc(i.retry_recovered)}</td><td>{esc(i.attempts)}</td><td>{esc(steps)}</td>"
            f'<td>{esc(i.saved)}</td><td class="num">{esc(i.outage)}</td></tr>'
        )
    return f'<div class="scroll"><table class="data">{head}{"".join(body)}</table></div>'


def timeline_table(entries: Sequence[Entry]) -> str:
    if not entries:
        return '<p class="muted">no events in this window</p>'
    rows = "".join(
        f'<tr><td class="time">{e.time:%m-%d %H:%M:%S}</td><td>{esc(e.env.upper())}</td>'
        f"<td>{kind_pill(e.kind)}</td><td>{esc(e.text)}</td></tr>"
        for e in entries[:MAX_ROWS]
    )
    more = (
        f'<p class="muted">showing the newest {MAX_ROWS} of {len(entries)} events; '
        "pick a shorter window for the rest</p>"
        if len(entries) > MAX_ROWS
        else ""
    )
    head = "<tr><th>time</th><th>env</th><th>kind</th><th>event</th></tr>"
    return f'<div class="scroll"><table class="data">{head}{rows}</table></div>{more}'


def view(job: str, runs: Sequence[RunRecord], now: datetime, query: Mapping[str, str]) -> str:
    hours = _choose(query.get("hours"), WINDOWS, 24)
    env = query.get("env") if query.get("env") in ENVS else None
    base = {"hours": str(hours), "env": env or "both"}
    since = now.astimezone(MANILA).replace(tzinfo=None) - timedelta(hours=hours)
    found = reports(runs)
    latest = runs[0] if runs else None
    entries = [e for e in timeline(found, since) if env is None or e.env == env]

    controls = [
        f"<span>window {_links('hours', str(hours), [(str(w), f'{w}h') for w in WINDOWS], base)}</span>",
        f"<span>env {_links('env', env or 'both', [(e, e) for e in ('both', *ENVS)], base)}</span>",
    ]
    if latest:
        state = "ok" if latest.ok else "failed"
        controls.append(
            f'<span title="{when(latest.started)}">last pull <b>{ago(latest.started, now)}</b> '
            f"({took(latest.duration_ms)}, {state})</span>"
        )
    parts = [
        f'<h1>{esc(TITLE)} <small><a href="/jobs/{esc(job)}">{esc(job)}</a></small></h1>',
        f'<div class="meta">{"".join(controls)}</div>',
    ]
    if latest and not latest.ok:
        parts.append(
            f'<p class="error">The latest run failed: {esc(latest.error or latest.summary)}. '
            "Everything below comes from the last good run.</p>"
        )
    if not found:
        parts.append(
            '<div class="empty"><p>No successful run yet. Run the job once and this page fills itself.'
            "</p></div>"
        )
        return "\n".join(parts)

    current = found[0]
    shown = [r for r in current.envs if env is None or r.env == env]
    last_by_env: dict[str, Entry] = {}
    for e in entries:
        last_by_env.setdefault(e.env, e)
    cards = "".join(env_card(r, current.full, last_by_env.get(r.env)) for r in shown)
    parts.append(f'<div class="cards">{cards}</div>')

    first = shown[0] if shown else current.envs[0]
    span = f"last {dur((first.until - first.since).total_seconds())} to {when(first.until)}"
    parts.append(f'<h2 class="section">Incidents <small>{esc(span)}</small></h2>')
    if not current.full:
        parts.append(
            '<p class="muted">This job records events only. Add <code>full = true</code> to its args '
            "to get the incident table from the same pull.</p>"
        )
    for r in shown:
        if not r.error:
            parts.append(f"<h3>{esc(r.env.upper())}</h3>{incident_table(r.incidents)}")
    parts.append(
        f'<h2 class="section">Timeline <small>{len(entries)} events in the last {hours} h</small></h2>'
        f"{timeline_table(entries)}"
    )
    oldest = runs[-1]
    parts.append(
        f'<p class="muted">{len(runs)} runs kept; the timeline reaches back to '
        f"{esc(when(oldest.started))}.</p>"
    )
    return "\n".join(parts)
