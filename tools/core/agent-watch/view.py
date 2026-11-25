"""Dashboard page for jobs that run agent-watch: what needs attention, every session as a tree, the state
changes across the kept runs, and the sessions that ended. Built from stored runs only; it never scans."""

from __future__ import annotations

import itertools
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from shed.cron import RunRecord
from shed.lib.manila import MANILA
from shed.ui import ago, esc, pill, span

from . import states
from .models import CommandItem, Report, SessionItem, SubagentItem, keep_states, subagent_counts

TITLE = "Agents"
SHOW = ("all", "attention", "working", "idle")
MAX_CHANGES = 200
_PILL = {"stalled": "fail", "needs-input": "fail", "gone": "fail", "failed": "fail", "working": "running"}
_PILL |= {"serving": "ok", "done": "ok", "idle": "off", "ended": "off"}


@dataclass(frozen=True, slots=True)
class Row:
    """One item of a snapshot, flattened out of its tree."""

    id: str
    kind: str
    label: str
    owner: str
    depth: int
    item: SessionItem | SubagentItem | CommandItem


def _state(state: str) -> str:
    return pill(_PILL.get(state, "off"), state)


def _clock(when: datetime, now: datetime) -> str:
    local = when.astimezone(MANILA)
    return f"{local:%H:%M}" if local.date() == now.astimezone(MANILA).date() else f"{local:%m-%d %H:%M}"


def _rows(session: SessionItem, owner: str = "", depth: int = 0) -> Iterator[Row]:
    """The session, then everything it started, depth first."""
    yield Row(session.id, "session", session.name, owner, depth, session)

    def agents(found: list[SubagentItem], level: int) -> Iterator[Row]:
        for agent in found:
            yield Row(agent.id, "subagent", f"{agent.type}: {agent.description}", session.name, level, agent)
            yield from agents(agent.subagents, level + 1)

    yield from agents(session.subagents, depth + 1)
    for command in session.commands:
        yield Row(command.id, "command", command.command, session.name, depth + 1, command)
    for child in session.children:
        yield from _rows(child, session.name, depth + 1)


def _marks(result: Any, owner: str = "") -> Iterator[tuple[str, str, str, str]]:
    """(id, state, label, where) for every item of a stored result, read without validating it."""
    sessions = result.get("sessions") if isinstance(result, dict) and not owner else result
    for session in sessions if isinstance(sessions, list) else []:
        name = str(session.get("name") or "")
        yield (
            str(session.get("id")),
            str(session.get("state")),
            name,
            f"session of {owner}" if owner else "session",
        )

        def agents(found: Any, name: str = name) -> Iterator[tuple[str, str, str, str]]:
            for agent in found or []:
                label = f"{agent.get('type')}: {agent.get('description')}"
                yield str(agent.get("id")), str(agent.get("state")), label, f"subagent of {name}"
                yield from agents(agent.get("subagents"))

        yield from agents(session.get("subagents"))
        for command in session.get("commands") or []:
            yield (
                str(command.get("id")),
                str(command.get("state")),
                str(command.get("command")),
                f"command of {name}",
            )
        yield from _marks(session.get("children") or [], name)


def _links(current: str, base: Mapping[str, str]) -> str:
    """The segmented control that picks which sessions to show; the current choice is the pressed one."""
    out = []
    for value in SHOW:
        if value == current:
            out.append(f"<b>{esc(value)}</b>")
        else:
            query = "&".join(f"{k}={v}" for k, v in {**base, "state": value}.items() if v)
            out.append(f'<a href="?{esc(query)}">{esc(value)}</a>')
    return f'<span class="seg">{"".join(out)}</span>'


def _table(head: Sequence[str], body: Sequence[str]) -> str:
    cells = "".join(f"<th>{esc(name)}</th>" for name in head)
    return f'<div class="scroll"><table class="data"><tr>{cells}</tr>{"".join(body)}</table></div>'


def _quiet(item: SessionItem | SubagentItem | CommandItem) -> str:
    return esc(span(item.quiet_s)) if item.quiet_s is not None else "-"


def _attention(rows: Sequence[Row], history: Sequence[tuple[datetime, dict[str, str]]], now: datetime) -> str:
    wanted = [row for row in rows if row.item.state in states.ATTENTION]
    if not wanted:
        return '<p class="muted">Nothing needs attention.</p>'
    body = []
    for row in sorted(wanted, key=lambda row: states.ORDER.index(row.item.state)):
        since = history[0][0]
        for started, marks in history:  # newest first: walk back while the state holds
            if marks.get(row.id) != row.item.state:
                break
            since = started
        held = f"{span(int((now - since).total_seconds()))}, since {_clock(since, now)}"
        where = f"{row.kind} {row.id[:8]}" + (f" of {row.owner}" if row.owner else "")
        body.append(
            f"<tr><td>{_state(row.item.state)}</td>"
            f'<td class="key">{esc(row.label)}<br><span class="muted">{esc(where)}</span></td>'
            f'<td class="num">{_quiet(row.item)}</td><td class="time">{esc(held)}</td>'
            f"<td>{esc(row.item.reason)}</td></tr>"
        )
    return _table(("State", "Item", "Quiet", "In this state", "Evidence"), body)


def _session_cells(session: SessionItem) -> tuple[str, str, str, str]:
    """Model, doing now, turn and agent for a session row."""
    turn = session.turn
    progress = (
        f"{span(turn.elapsed_s)} · {turn.tool_calls or 0} calls"
        if turn and turn.elapsed_s is not None
        else "-"
    )
    doing = session.activity or session.reason
    agent = " · ".join(part for part in (session.agent, session.entrypoint or "") if part)
    return session.model or "-", doing, progress, agent


def _tree(sessions: Sequence[SessionItem]) -> str:
    body = []
    for row in (row for session in sessions for row in _rows(session)):
        item = row.item
        indent = "&nbsp;" * (4 * row.depth) + ("└ " if row.depth else "")
        if isinstance(item, SessionItem):
            model, doing, progress, agent = _session_cells(item)
            prompt = item.turn.prompt if item.turn and item.turn.prompt else ""
            below = " · ".join(part for part in (prompt, subagent_counts(item)) if part)
            kind = '<span class="muted">session</span> ' if row.depth else ""
            name = f"{indent}{kind}{esc(item.name)}" + (
                f'<br><span class="muted">{esc(below)}</span>' if below else ""
            )
        else:
            model = item.model or "-" if isinstance(item, SubagentItem) else ""
            doing = (item.activity if isinstance(item, SubagentItem) else None) or item.reason
            progress = (
                f"{item.tool_calls} calls" if isinstance(item, SubagentItem) else span(item.runtime_s or 0)
            )
            agent = (
                item.mode if isinstance(item, SubagentItem) else ", ".join(f":{port}" for port in item.ports)
            )
            name = f'{indent}<span class="muted">{esc(row.kind)}</span> {esc(row.label)}'
        body.append(
            f'<tr><td>{_state(item.state)}</td><td class="key">{name}</td><td>{esc(model)}</td>'
            f'<td>{esc(doing)}</td><td class="time">{esc(progress)}</td>'
            f'<td class="num">{_quiet(item)}</td><td>{esc(agent)}</td></tr>'
        )
    head = ("State", "Session and what it started", "Model", "Doing now", "Turn", "Quiet", "Agent")
    return _table(head, body) if body else '<p class="muted">No sessions match.</p>'


def _changes(
    history: Sequence[tuple[datetime, dict[str, str]]], labels: Mapping[str, tuple[str, str]], now: datetime
) -> str:
    """State changes between consecutive kept runs, newest first, up to the cap."""
    body: list[str] = []
    for (started, newer), (_, older) in itertools.pairwise(history):
        for item_id, state in newer.items():
            before = older.get(item_id)
            if before is None or before == state or len(body) >= MAX_CHANGES:
                continue
            label, where = labels.get(item_id, (item_id, ""))
            body.append(
                f'<tr class="change"><td class="time">{esc(_clock(started, now))}</td>'
                f"<td>{_state(before)} to {_state(state)}</td>"
                f'<td class="key">{esc(label)} <span class="muted">{esc(where)}</span></td></tr>'
            )
        if len(body) >= MAX_CHANGES:
            break
    if not body:
        return '<p class="muted">No state changes in the kept runs.</p>'
    capped = (
        f'<p class="muted">Showing the newest {MAX_CHANGES} changes.</p>' if len(body) >= MAX_CHANGES else ""
    )
    return _table(("Time", "Change", "Item"), body) + capped


def _ended(sessions: Sequence[SessionItem], retain_s: int) -> str:
    period = "day" if retain_s == 86400 else span(retain_s)
    body = []
    for session in sessions:
        prompt = session.turn.prompt if session.turn and session.turn.prompt else ""
        calls = session.turn.tool_calls if session.turn and session.turn.tool_calls is not None else 0
        counts = (
            f"{calls} calls, {session.subagents_finished} subagents, {session.commands_finished} commands"
        )
        body.append(
            f'<tr><td>{_state(session.state)}</td><td class="key">{esc(session.name)}</td>'
            f'<td>{esc(session.model or "-")}</td><td class="time">{esc(session.last_write or "-")}</td>'
            f"<td>{esc(prompt)}</td><td>{esc(counts)}</td></tr>"
        )
    table = _table(("State", "Session", "Model", "Last write", "Last prompt", "Last turn"), body)
    inner = table if body else '<p class="muted">None.</p>'
    title = f"Ended in the last {esc(period)} <small>{len(sessions)}</small>"
    return f"<details><summary>{title}</summary>{inner}</details>"


def _history(
    runs: Sequence[RunRecord],
) -> tuple[list[tuple[datetime, dict[str, str]]], dict[str, tuple[str, str]]]:
    """Per good run, newest first, the state of every item; and the newest label of each item."""
    history: list[tuple[datetime, dict[str, str]]] = []
    labels: dict[str, tuple[str, str]] = {}
    for run in runs:
        if not run.ok or not isinstance(run.result, dict):
            continue
        marks: dict[str, str] = {}
        for item_id, state, label, where in _marks(run.result):
            marks[item_id] = state
            labels.setdefault(item_id, (label, where))
        history.append((run.started, marks))
    return history, labels


def _latest(runs: Sequence[RunRecord]) -> tuple[RunRecord, Report] | None:
    for run in runs:
        if run.ok and run.result:
            try:
                return run, Report.model_validate(run.result)
            except ValidationError:
                continue
    return None


def view(job: str, runs: Sequence[RunRecord], now: datetime, query: Mapping[str, str]) -> str:
    show = query.get("state") if query.get("state") in SHOW else "all"
    project = query.get("project") or ""
    parts = [f'<h1>{esc(TITLE)} <small><a href="/jobs/{esc(job)}">{esc(job)}</a></small></h1>']
    found = _latest(runs)
    if runs and not runs[0].ok:
        parts.append(
            f'<p class="error">The latest run failed: {esc(runs[0].error or runs[0].summary)}. '
            "Everything below comes from the last good run.</p>"
        )
    if found is None:
        parts.append(
            '<div class="empty"><p>No successful run yet. Run the job once and this page fills itself.'
            "</p></div>"
        )
        return "\n".join(parts)
    run, report = found
    versions = "".join(
        f"<span>{esc(cli)} <b>{esc(', '.join(seen))}</b></span>" for cli, seen in report.versions.items()
    )
    parts.insert(
        1,
        f'<div class="meta"><span title="{esc(report.taken)}">snapshot '
        f"<b>{esc(ago(run.started, now))}</b></span>"
        f"<span>taken <b>{esc(report.taken[11:])}</b></span>"
        f"<span>stall after <b>{esc(span(report.stall_after_s))}</b></span>{versions}"
        f"<span>show {_links(show or 'all', {'project': project})}</span></div>",
    )
    counted = "".join(
        f"<span>{pill(_PILL.get(state, 'off'), f'{n} {state}')}</span>"
        for state in states.ORDER
        if (n := report.counts.get(state))
    )
    parts.append(f'<div class="meta">{counted}</div>')
    history, labels = _history(runs)
    rows = [row for session in report.sessions for row in _rows(session)]
    attention = sum(1 for row in rows if row.item.state in states.ATTENTION)
    parts.append(f'<h2 class="section">Needs attention <small>{attention} items</small></h2>')
    parts.append(_attention(rows, history, now))

    live = [session.model_copy(deep=True) for session in report.sessions if session.state != "ended"]
    if project:
        live = [session for session in live if session.project.rstrip("/").rsplit("/", 1)[-1] == project]
    if show == "attention":
        live = [session for session in live if keep_states(session, states.ATTENTION)]
    elif show in ("working", "idle"):
        live = [session for session in live if session.state == show]
    parts.append(f'<h2 class="section">Sessions <small>{len(live)} live</small></h2>')
    parts.append(_tree(live))

    oldest = runs[-1].started
    kept = f"{len(runs)} runs kept, back to {_clock(oldest, now)}"
    parts.append(f'<h2 class="section">Recent changes <small>{esc(kept)}</small></h2>')
    parts.append(_changes(history, labels, now))
    parts.append(
        _ended([session for session in report.sessions if session.state == "ended"], report.retain_s)
    )
    return "\n".join(parts)
