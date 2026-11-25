"""What every agent session on this machine is doing, and which have stalled.

One run is one snapshot: each Claude Code session with its subagents and background commands, every state
checked against evidence (transcript writes, output files, processes) instead of taken from the session's
own "busy" claim, plus the sessions that ended inside the retention period. Read-only.
"""

from __future__ import annotations

import os
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from shed import Tool, ToolContext, ToolError
from shed.cron import parse_every
from shed.lib import transcripts as t

from . import probe as probing
from . import states
from .codex import codex_sessions
from .models import (
    CommandItem,
    Report,
    Scan,
    SessionItem,
    SubagentItem,
    by_state,
    keep_states,
    subagent_counts,
)
from .probe import Proc
from .sessions import claude_sessions

DEFAULT_SESSIONS = Path.home() / ".claude" / "sessions"
Agent = Literal["claude", "codex"]


class Input(BaseModel):
    stall_after: str = Field(
        "5m", description="How long without a sign of progress makes a busy item stalled: 90s, 5m, 1h"
    )
    window: str = Field(
        "30m", description="How long finished and gone subagents and commands stay on the list: 10m, 30m, 2h"
    )
    retain: str = Field("1d", description="How long ended and gone sessions stay on the list: 30m, 12h, 1d")
    max_chars: int = Field(120, ge=1, description="Characters kept per prompt, command and activity line")
    dir: str | None = Field(
        None, description="Transcripts root (default $CLAUDE_PROJECTS_DIR or ~/.claude/projects)"
    )
    sessions_dir: str | None = Field(
        None, description="Claude Code live-session registry folder (default ~/.claude/sessions)"
    )
    tasks_dir: str | None = Field(
        None, description="Root of Claude Code's background-task output (default /private/tmp/claude-<uid>)"
    )
    codex_dir: str | None = Field(None, description="Codex home (default $CODEX_HOME or ~/.codex)")
    project: str | None = Field(
        None,
        description="Only sessions of this project: a folder path, or the encoded name under the "
        "transcripts root (default: every project on the machine)",
    )
    agent: list[Agent] = Field(
        default_factory=lambda: ["claude", "codex"], description="Agent CLIs to cover: claude, codex"
    )
    session: str | None = Field(
        None, description="Only the session whose id starts with this or is named this"
    )
    state: list[states.State] = Field(
        default_factory=list,
        description="Keep only items in these states, with their parents: stalled, needs-input, gone, "
        "working, serving, done, failed, idle, ended",
    )
    tail: int = Field(0, ge=0, description="Lines of each background command's output to include")

    @field_validator("stall_after", "window", "retain")
    @classmethod
    def _check_duration(cls, value: str) -> str:
        parse_every(value)
        return value


def _folder(value: str | None, default: Path, cwd: Path) -> Path:
    return (cwd / Path(value).expanduser()).resolve() if value else default


def _states(item: SessionItem | SubagentItem | CommandItem) -> list[str]:
    """The state of an item and of everything below it."""
    below = [*getattr(item, "subagents", []), *getattr(item, "commands", []), *getattr(item, "children", [])]
    return [item.state, *(state for child in below for state in _states(child))]


def _look(scan: Scan) -> None:
    """Fill in the process table and the listening ports; either may be unavailable."""
    probe = probing.probe
    try:
        scan.procs = {proc.pid: proc for proc in probe.processes()}
    except ToolError as exc:
        scan.procs_known = False
        scan.notes.append(f"process table unavailable: {exc}")
    for proc in scan.procs.values():
        scan.children.setdefault(proc.ppid, []).append(proc)
    try:
        scan.ports = probe.listening_ports()
    except ToolError as exc:
        scan.ports_known = False
        scan.notes.append(f"listening ports unavailable: {exc}")


def _launcher(proc: Proc | None, by_pid: dict[int, SessionItem], scan: Scan) -> SessionItem | None:
    """The session whose process the given process descends from, if any."""
    seen: set[int] = set()
    while proc and proc.pid not in seen:
        seen.add(proc.pid)
        if proc.ppid in by_pid:
            return by_pid[proc.ppid]
        proc = scan.procs.get(proc.ppid)
    return None


def _scoped(sessions: list[SessionItem], params: Input, root: Path, cwd: Path) -> list[SessionItem]:
    """The sessions the project and session filters ask for; a matching child keeps its launcher."""
    project = t.resolve_project(params.project, cwd, root) if params.project else None
    wanted = params.session

    def matches(item: SessionItem) -> bool:
        here = Path(
            item.project
        )  # the transcript folder is named after the path as typed, symlinks unresolved
        in_project = project is None or project in (t.encode_project(here), t.encode_project(here.resolve()))
        named = wanted is None or item.id.startswith(wanted) or item.name == wanted
        return (in_project and named) or any(matches(child) for child in item.children)

    return [session for session in sessions if matches(session)]


def run(params: Input, ctx: ToolContext) -> Report:
    scan = Scan(
        now=probing.probe.now(),
        stall_after_s=parse_every(params.stall_after),
        window_s=parse_every(params.window),
        retain_s=parse_every(params.retain),
        max_chars=params.max_chars,
        tasks_dir=_folder(params.tasks_dir, Path("/private/tmp") / f"claude-{os.getuid()}", ctx.cwd),
        tail_lines=params.tail,
    )
    root = t.projects_root(params.dir, ctx.cwd)
    registry_dir = _folder(params.sessions_dir, DEFAULT_SESSIONS, ctx.cwd)
    codex_dir = _folder(
        params.codex_dir, Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex"), ctx.cwd
    )
    if not (registry_dir.is_dir() or root.is_dir() or (codex_dir / "sessions").is_dir()):
        raise ToolError(
            f"nothing to watch: no Claude Code sessions under {registry_dir} or {root}, "
            f"and no Codex sessions under {codex_dir}"
        )
    _look(scan)
    found = claude_sessions(root, registry_dir, scan) if "claude" in params.agent else []
    every = [(item, scan.procs.get(item.pid) if item.pid else None) for item in found]
    every += codex_sessions(codex_dir, scan) if "codex" in params.agent else []
    by_pid = {item.pid: item for item, _ in every if item.pid}
    sessions: list[SessionItem] = []
    for item, proc in every:  # a session started by another session is listed under it
        parent = _launcher(proc, by_pid, scan)
        (parent.children if parent else sessions).append(item)
    sessions = sorted(_scoped(sessions, params, root, ctx.cwd), key=by_state)
    counts = Counter(state for session in sessions for state in _states(session))
    if params.state:
        sessions = [session for session in sessions if keep_states(session, set(params.state))]
    ctx.log(f"{len(sessions)} session(s) at {t.manila(scan.now)}")
    return Report(
        taken=t.manila(scan.now),
        stall_after_s=scan.stall_after_s,
        window_s=scan.window_s,
        retain_s=scan.retain_s,
        versions={agent: sorted(seen) for agent, seen in sorted(scan.versions.items())},
        counts=dict(counts),
        attention=sum(n for state, n in counts.items() if state in states.ATTENTION),
        sessions=sessions,
        notes=scan.notes,
    )


def _where(project: str) -> str:
    home = str(Path.home())
    return "~" + project[len(home) :] if project.startswith(home) else project


def _progress(session: SessionItem) -> str:
    """How far along the session is, in a few words."""
    if session.state in ("idle", "ended", "gone"):
        word = "idle" if session.state == "idle" else "last write"
        return f"{word} {states.brief(session.quiet_s or 0)}" + ("" if session.state == "idle" else " ago")
    if session.turn and session.turn.elapsed_s is not None:
        return f"turn {states.brief(session.turn.elapsed_s)}, {session.turn.tool_calls or 0} calls"
    return ""


def _session_lines(session: SessionItem, indent: str = "  ") -> list[str]:
    """One line for the session, then one per subagent, command and launched session below it."""
    prompt = session.turn.prompt if session.turn and session.turn.prompt else ""
    head = f"{indent}{session.state:12} {session.name:22} {session.agent:6} {session.model or '-':18} "
    doing = "  ".join(part for part in (session.activity or prompt, subagent_counts(session)) if part)
    lines = [f"{head}{_where(session.project):28} {_progress(session):20} {doing}".rstrip()]
    deeper = indent + "  "

    def agents(found: list[SubagentItem], pad: str) -> None:
        for agent in found:
            what = f"{agent.type}: {agent.description}"
            lines.append(
                f"{pad}{agent.state:12} subagent  {what:44} {agent.model or '-':18} {agent.reason}".rstrip()
            )
            agents(agent.subagents, pad + "  ")

    agents(session.subagents, deeper)
    for command in session.commands:
        lines.append(f"{deeper}{command.state:12} command   {command.command:44} {command.reason}".rstrip())
        lines.extend(f"{deeper}    | {line}" for line in command.tail or [])  # only there when asked for
    for child in session.children:
        lines.extend(_session_lines(child, deeper))
    return lines


def _attention(report: Report) -> list[str]:
    """One line per item that needs attention, with the session it belongs to."""
    lines: list[str] = []

    def visit(item: SessionItem | SubagentItem | CommandItem, owner: str) -> None:
        label = item.name if isinstance(item, SessionItem) else item.id
        if item.state in states.ATTENTION:
            lines.append(f"  {item.state:12} {label:22} {f'({owner}) ' if owner else ''}{item.reason}")
        inside = label if isinstance(item, SessionItem) else owner
        for child in [
            *getattr(item, "subagents", []),
            *getattr(item, "commands", []),
            *getattr(item, "children", []),
        ]:
            visit(child, inside)

    for session in report.sessions:
        visit(session, "")
    return lines


def render(report: Report) -> str:
    counted = ", ".join(f"{n} {state}" for state in states.ORDER if (n := report.counts.get(state)))
    stall = states.brief(report.stall_after_s)
    lines = [f"{counted or 'no sessions'}    taken {report.taken}, stall after {stall}"]
    attention = _attention(report)
    if attention:
        lines += ["", "needs attention", *attention]
    live = [session for session in report.sessions if session.state != "ended"]
    over = [session for session in report.sessions if session.state == "ended"]
    if live:
        lines += ["", "sessions", *(line for session in live for line in _session_lines(session))]
    if over:
        span = "day" if report.retain_s == 86400 else states.brief(report.retain_s)
        lines += [
            "",
            f"ended in the last {span}",
            *(line for session in over for line in _session_lines(session)),
        ]
    lines += [f"note: {note}" for note in report.notes]
    return "\n".join(lines) + "\n"


TOOL = Tool(
    name="agent-watch",
    description=(
        "Snapshot of every agent session on this machine with what each is doing, the model it runs on, and "
        "a state checked against evidence, so a session that says it is busy but has stalled is flagged. "
        "Use it to see what your agents are doing or whether anything is stuck. Read-only."
    ),
    input=Input,
    run=run,
    render=render,
    examples=(
        "shed agent-watch",
        "shed agent-watch --state stalled --state needs-input --state gone --json",
        "shed agent-watch --project ~/Desktop/Projects/tool-shed --stall-after 2m",
    ),
    version="0.1.0",
)
