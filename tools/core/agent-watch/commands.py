"""A session's shells: its background commands, the process of its open shell call, and its helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from shed.lib import transcripts as t

from . import claude, states
from .models import CommandItem, Scan, by_state
from .probe import Proc

CALL_START_S = 5
_EPOCH = datetime.min.replace(tzinfo=UTC)
_OVER = ("done", "failed", "gone")


@dataclass(slots=True)
class Shells:
    """What a session's child processes are: background commands, helpers, and its open call's process."""

    items: list[CommandItem] = field(default_factory=list)
    running: int = 0
    finished: int = 0
    helpers: int = 0
    call_proc: Proc | None = None
    """The process the session's first open call started, when one was found."""
    call_cpu: float = 0.0
    called: dict[str, Proc] = field(default_factory=dict)
    """The shell found for each open shell call, the session's and its running subagents', by call id."""


def _started_with(kids: list[Proc], started: datetime) -> list[Proc]:
    """The shells that began when something was launched at ``started``."""
    return [kid for kid in kids if abs((kid.started - started).total_seconds()) <= CALL_START_S]


class _Wrappers:
    """The session's wrapper shells that nothing has claimed yet; each can be claimed once."""

    def __init__(self, kids: list[Proc]) -> None:
        found = ((kid, claude.wrapped_command(kid.command)) for kid in kids)
        self.free = {kid.pid: (kid, text) for kid, text in found if text is not None}

    def _claim(self, matches: list[Proc], started: datetime) -> Proc | None:
        """Take, from the given shells, the one that started closest to ``started``."""
        if not matches:
            return None
        best = min(matches, key=lambda kid: abs((kid.started - started).total_seconds()))
        del self.free[best.pid]
        return best

    def exact(self, command: str, started: datetime) -> Proc | None:
        """The shell that runs exactly this command."""
        return self._claim([kid for kid, text in self.free.values() if text == command], started)

    def same_launch(self, command: str, started: datetime) -> Proc | None:
        """The shell that runs exactly this command and started when it was launched."""
        same = [kid for kid, text in self.free.values() if text == command]
        return self._claim(_started_with(same, started), started)

    def near(self, started: datetime) -> Proc | None:
        """The shell that started with the call; a hook may have rewritten the command, so text can differ."""
        return self._claim(_started_with([kid for kid, _ in self.free.values()], started), started)

    def newest_after(self, started: datetime) -> Proc | None:
        """The newest shell started after the call, for a call that waited on an approval before it ran."""
        later = [kid for kid, _ in self.free.values() if kid.started > started]
        return self._claim(later, max((kid.started for kid in later), default=started))


def _command_item(command: claude.Launched, proc: Proc | None, scan: Scan) -> CommandItem:
    tree = scan.subtree(proc.pid) if proc else []
    ports = sorted({port for member in tree for port in scan.ports.get(member.pid, [])})
    verdict = states.judge_command(
        now=scan.now,
        command=command,
        alive=proc is not None,
        cpu=sum(member.cpu for member in tree),
        ports=ports,
        ports_known=scan.ports_known,
        procs_known=scan.procs_known,
        stall_after_s=scan.stall_after_s,
    )
    tail = None
    if scan.tail_lines and command.output:
        try:
            tail = claude.output_tail(command.output, scan.tail_lines, scan.max_chars)
        except OSError:
            scan.notes.append(f"output of {command.id} is unreadable")
    last = command.last_output or command.started
    return CommandItem(
        id=command.id,
        command=t.one_line(command.command, scan.max_chars) or "(command not in the read window)",
        description=t.one_line(command.description, scan.max_chars) or None,
        pid=proc.pid if proc else None,
        started=t.manila(command.started),
        runtime_s=int((scan.now - command.started).total_seconds()),
        state=verdict.state,
        reason=verdict.reason,
        quiet_s=int((scan.now - last).total_seconds()) if command.tracked else None,
        output_bytes=command.output_bytes,
        ports=ports if verdict.state == "serving" else [],
        tail=tail,
    )


def _script(call: claude.Pending) -> str:
    return str(call.input.get("command") or "")


def _claim_calls(
    wrappers: _Wrappers,
    calls: list[claude.Pending],
    found: dict[str, Proc],
    how: Literal["exact", "near", "newest"],
) -> None:
    """Give each open shell call that has no process yet the wrapper the named match finds for it."""
    for call in calls:
        if call.id in found:
            continue
        if how == "exact":
            proc = wrappers.exact(_script(call), call.time)
        else:
            proc = wrappers.near(call.time) if how == "near" else wrappers.newest_after(call.time)
        if proc:
            found[call.id] = proc


def session_shells(
    pid: int,
    logs: list[t.Session],
    running_logs: list[t.Session],
    path: Path | None,
    pending: list[claude.Pending],
    scan: Scan,
) -> Shells:
    """Tie the session's wrapper shells to background commands and to open shell calls, its subagents' too.

    Exact command text is matched everywhere before start time is, so one call never takes another's shell.
    """
    out = Shells()
    kids = scan.children.get(pid, [])
    wrappers = _Wrappers(kids)
    out.helpers = len(kids) - len(wrappers.free)
    notices, stops = claude.endings(logs)
    commands = [
        command
        for log in logs
        for command in (claude.read_commands(log, path, scan.tasks_dir, notices, stops) if path else [])
    ]
    own = [call for call in pending if call.name in states.SHELL_TOOLS]
    theirs = [  # the open shell calls of running subagents: their shells are accounted for, never listed
        call
        for log in running_logs
        for call in claude.pending_calls(log, _EPOCH)
        if call.name in states.SHELL_TOOLS
    ]
    live = [command for command in commands if not command.over]
    done = [command for command in commands if command.over]
    calls = [*own, *theirs]
    procs = {c.id: proc for c in live if (proc := wrappers.exact(c.command, c.started))}
    _claim_calls(wrappers, calls, out.called, "exact")
    # a finished command's shell can outlive it and must not be listed as a second command: one that
    # still runs the same text is set aside here, before any start-time match, so no open call takes it
    aside = {c.id for c in done if wrappers.same_launch(c.command, c.started)}
    procs |= {c.id: proc for c in live if c.id not in procs and (proc := wrappers.near(c.started))}
    _claim_calls(wrappers, calls, out.called, "near")
    _claim_calls(wrappers, calls, out.called, "newest")
    for command in done:  # one a hook rewrote is told by start time alone, so every open call goes first
        if command.id not in aside:
            wrappers.near(command.started)
    if pending and pending[0].id in out.called:
        out.call_proc = out.called[pending[0].id]
        out.call_cpu = sum(member.cpu for member in scan.subtree(out.call_proc.pid))
    known = [(command, procs.get(command.id)) for command in commands]
    stray = [  # launched before the read window: only the process is known
        (claude.Launched(f"pid-{kid.pid}", text, "", kid.started, None, None, None, None, tracked=False), kid)
        for kid, text in wrappers.free.values()
    ]
    for command, proc in [*known, *stray]:
        item = _command_item(command, proc, scan)
        over = item.state in _OVER
        out.finished += over
        out.running += not over
        ended = command.stopped or (command.notice.time if command.notice else None) or command.last_output
        if not (over and (scan.now - (ended or command.started)).total_seconds() > scan.window_s):
            out.items.append(item)
    out.items.sort(key=by_state)
    return out
