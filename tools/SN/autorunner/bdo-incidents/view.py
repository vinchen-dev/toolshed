"""Dashboard page for jobs that run bdo-incidents: status per environment, the latest incident table
and a timeline stitched from every kept run. The dashboard serves it at /view/<job>."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta

from pydantic import BaseModel, ValidationError

from shed.cron import RunRecord
from shed.lib.manila import MANILA
from shed.ui import ago, esc, pill, took, when

from .incidents import Incident, dur, session_state
from .tool import EnvReport, Report, clock

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


def rough(seconds: float) -> str:
    """A duration people read at a glance: 38 min, 1 h 05 min, 2 d 3 h."""
    minutes = int(seconds // 60)
    if minutes < 1:
        return "under a minute"
    if minutes < 60:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    if hours < 48:
        return f"{hours} h {minutes:02d} min"
    days, hours = divmod(hours, 24)
    return f"{days} d {hours} h"


def hours_label(seconds: float) -> str:
    return f"{seconds / 3600:.1f}".rstrip("0").rstrip(".") + " h"


def _at(value: datetime, now: datetime) -> str:
    """A time with its relative age on hover, e.g. 14:25:14 (38 min ago)."""
    return f'<span title="{esc(when(value))}">{esc(clock(value, now))} ({esc(ago(value, now))})</span>'


def _reason(entry: Entry) -> str:
    return esc(entry.text.removeprefix("relogin FAILED: "))


def session_lines(r: EnvReport, history: Sequence[Entry], now: datetime) -> tuple[str, list[str]]:
    """The status pill and the sentences under it; `history` is this environment's events, newest first."""
    state, since, created = r.state, r.state_since, r.session_created
    if since is None and history:  # a run recorded before the tool reported the state
        state, since = session_state(list(reversed(history)))
        created = next((e.time for e in history if e.kind == "SAVED" and e.time >= r.since), None)
    if since is None:
        return pill("off", "no signal"), ["No session events recorded yet."]
    if state == "down":
        lines = [f"Down since {_at(since, now)}."]
        stops = [s for s in r.scraper_stops if s >= since]
        if stops:
            lines.append(f"Scraper stopped at {_at(stops[-1], now)} after 20 min without a session.")
        fails = [e for e in history if e.kind == "FAIL" and e.time >= since]
        if fails:
            n = len(fails)
            attempts = "1 relogin attempt failed" if n == 1 else f"{n} relogin attempts failed"
            lines.append(f"{attempts}, last at {_at(fails[0].time, now)}: {_reason(fails[0])}.")
        return pill("fail", f"down {rough((now - since).total_seconds())}"), lines
    if created is None:
        window = hours_label((r.until - r.since).total_seconds())
        badge = pill("ok", "logged in")
        lines = [
            f"Up since {_at(since, now)}. Session age unknown: no login saved in the last {esc(window)}."
        ]
    else:
        life = rough((now - created).total_seconds())
        badge = pill("ok", f"logged in {life}")
        lines = [f"Up since {_at(since, now)}. Session life {esc(life)}, saved at {_at(created, now)}."]
    fails = [e for e in history if e.kind == "FAIL" and e.time > since]
    if fails:
        last = f"{_at(fails[0].time, now)}: {_reason(fails[0])}"
        tries = (
            f"1 attempt at {last}"
            if len(fails) == 1
            else f"{len(fails)} attempts since {_at(fails[-1].time, now)}, last at {last}"
        )
        lines.append(f"Relogin still failing in the background: {tries}.")
    return badge, lines


def env_card(r: EnvReport, full: bool, history: Sequence[Entry], now: datetime) -> str:
    """One environment: whether the scraper is logged in right now, the window totals, the last event."""
    if r.error:
        badge, body = pill("off", "pull failed"), f'<p class="summary">{esc(r.error)}</p>'
    else:
        badge, lines = session_lines(r, history, now)
        body = f'<p class="summary">{" ".join(lines)}</p>'
        if not full:
            body += (
                '<p class="summary muted">events only; add <code>full = true</code> to the job args '
                "for the incident totals</p>"
            )
        else:
            window = hours_label((r.until - r.since).total_seconds())
            body += (
                f'<div class="muted">totals for the last {esc(window)}</div>'
                '<div class="stats">'
                f"<div><b>{len(r.incidents)}</b><span>incidents</span></div>"
                f"<div><b>{esc(dur(r.total_outage_seconds))}</b>"
                f"<span>downtime, {r.downtime_percent}%</span></div>"
                f"<div><b>{r.failed_attempts}</b><span>failed relogins</span></div>"
                f"<div><b>{len(r.scraper_stops)}</b><span>scraper stops</span></div></div>"
            )
    last = history[0] if history else None
    recent = (
        f'<div class="muted">last event {_at(last.time, now)}: {esc(last.text)}</div>'
        if last
        else '<div class="muted">no events recorded yet</div>'
    )
    return (
        f'<article class="card"><header><span class="name">{esc(r.env.upper())}</span>{badge}</header>'
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
    page_now = now.astimezone(MANILA).replace(tzinfo=None)
    since = page_now - timedelta(hours=hours)
    found = reports(runs)
    latest = runs[0] if runs else None
    history = timeline(found, datetime.min)
    entries = [e for e in history if e.time >= since and (env is None or e.env == env)]

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
    cards = "".join(
        env_card(r, current.full, [e for e in history if e.env == r.env], page_now) for r in shown
    )
    parts.append(f'<div class="cards">{cards}</div>')

    first = shown[0] if shown else current.envs[0]
    span = f"last {hours_label((first.until - first.since).total_seconds())}, up to {when(first.until)}"
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
