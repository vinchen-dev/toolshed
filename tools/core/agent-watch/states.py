"""Evidence over claim: turn what was observed about an item into a state and a one-line reason."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from shed.lib.manila import MANILA
from shed.lib.transcripts import one_line

from .claude import Launched, Pending, Turn
from .probe import Proc

State = Literal["working", "needs-input", "stalled", "idle", "serving", "done", "failed", "gone", "ended"]
ATTENTION: frozenset[str] = frozenset({"stalled", "needs-input", "gone"})
ORDER = ("stalled", "needs-input", "gone", "working", "serving", "done", "failed", "idle", "ended")
SHELL_TOOLS: frozenset[str] = frozenset({"Bash"})
QUESTION_TOOL = "AskUserQuestion"
AGENT_TOOLS: frozenset[str] = frozenset({"Agent", "Task"})
SHELL_LIMIT_S = 120
GRACE_S = 30
CPU_FLOOR = 1.0
_DETAIL_KEYS = ("command", "description", "file_path", "notebook_path", "pattern", "query", "url", "skill")


@dataclass(frozen=True, slots=True)
class Verdict:
    state: State
    reason: str


def brief(seconds: float) -> str:
    """A stretch of time in a few characters: 45s, 14m, 2h 05m, 3d."""
    whole = max(int(seconds), 0)
    if whole < 60:
        return f"{whole}s"
    if whole < 3600:
        return f"{whole // 60}m"
    if whole < 86400:
        return f"{whole // 3600}h {whole % 3600 // 60:02d}m"
    return f"{whole // 86400}d"


def describe_call(name: str, tool_input: dict[str, Any], limit: int) -> str:
    """A tool call as one short line, for example ``Bash: uv run pytest``."""
    if name == QUESTION_TOOL:
        asked = tool_input.get("questions")
        first = (
            asked[0].get("question")
            if isinstance(asked, list) and asked and isinstance(asked[0], dict)
            else ""
        )
        return one_line(f"Question: {first}" if first else "Question", limit)
    detail = next((v for k in _DETAIL_KEYS if isinstance(v := tool_input.get(k), str) and v), "")
    return one_line(f"{name}: {detail}" if detail else name, limit)


def _quiet(now: datetime, last_sign: datetime | None) -> float:
    return (now - last_sign).total_seconds() if last_sign else 0


def waiting(now: datetime, last_sign: datetime | None, waiting_for: str) -> Verdict:
    """A session blocked on the user: an unanswered question or approval."""
    return Verdict("needs-input", f"Waiting on you for {brief(_quiet(now, last_sign))}: {waiting_for}.")


def idle(now: datetime, last_sign: datetime | None) -> Verdict:
    return Verdict("idle", f"Idle for {brief(_quiet(now, last_sign))}.")


def gone(now: datetime, last_sign: datetime | None, turn: Turn) -> Verdict:
    """A session whose process disappeared in the middle of a turn."""
    since = turn.started.astimezone(MANILA).strftime("%H:%M")
    quiet = brief(_quiet(now, last_sign))
    return Verdict(
        "gone", f"Turn open since {since} but no live process holds it; last transcript write {quiet} ago."
    )


def ended(now: datetime, last_sign: datetime | None) -> Verdict:
    return Verdict("ended", f"Process gone; its last turn finished {brief(_quiet(now, last_sign))} ago.")


def shell_limit(call: Pending) -> int:
    """Seconds a shell call may run: its declared timeout, else the harness default."""
    declared = call.input.get("timeout")
    return int(declared) // 1000 if isinstance(declared, int | float) and declared > 0 else SHELL_LIMIT_S


def _progress(
    now: datetime,
    last_sign: datetime | None,
    pending: list[Pending],
    stall_after_s: int,
    limit: int,
    idle_doing: str = "waiting for the model",
    shell_started: datetime | None = None,
    shell_missing: bool = False,
    subagent_alive: bool | None = None,
) -> tuple[bool, str, float]:
    """Whether an item in progress has stalled, what it is doing, and how long it has been quiet.

    ``shell_started`` is when its open shell call's process began; ``shell_missing`` means none was found.
    ``subagent_alive`` says whether a subagent it waits on still works; None when it waits on none.
    """
    quiet = _quiet(now, last_sign)
    call = pending[0] if pending else None
    doing = idle_doing
    # an item that waits on subagents is as alive as they are; any other is judged on its own silence
    stalled = quiet > stall_after_s if subagent_alive is None else not subagent_alive
    if call:
        age = brief((now - call.time).total_seconds())
        doing = f"{describe_call(call.name, call.input, limit)} pending {age}"
    if call and call.name in SHELL_TOOLS:
        allowed = shell_limit(call)
        doing += f" (limit {brief(allowed)})"
        # the limit runs from when the shell started; a call that waited on an approval started late
        past_limit = (now - (shell_started or call.time)).total_seconds() > allowed + GRACE_S
        # a running shell call is judged on its own limit; one with no process on whichever rule trips first
        stalled = past_limit or (shell_missing and stalled)
    if subagent_alive is False:
        doing += "; no subagent it waits on is making progress"
    return stalled, doing, quiet


def _unstarted(call: Pending | None, call_proc: Proc | None, procs_known: bool) -> bool:
    """A call that never got a process may be sitting behind a permission prompt."""
    return call is not None and call.name not in AGENT_TOOLS and call_proc is None and procs_known


def judge_session(
    *,
    now: datetime,
    last_sign: datetime | None,
    turn: Turn | None,
    pending: list[Pending],
    proc: Proc | None,
    call_proc: Proc | None,
    call_cpu: float,
    procs_known: bool,
    waiting_on: str,
    subagent_alive: bool | None,
    stall_after_s: int,
    limit: int,
) -> Verdict:
    """The state of a live session that claims to be busy: working while there is a sign of progress.

    ``proc`` is the session's own process, ``call_proc`` the one its open shell call started, if found.
    ``waiting_on`` names the subagent it waits for when it has no open call of its own.
    ``subagent_alive`` says whether a subagent it waits on still works; None when it waits on none.
    """
    idle_doing = f"waiting on subagent {waiting_on}" if waiting_on else "waiting for the model"
    unstarted = _unstarted(pending[0] if pending else None, call_proc, procs_known)
    stalled, doing, quiet = _progress(
        now,
        last_sign,
        pending,
        stall_after_s,
        limit,
        idle_doing,
        shell_started=call_proc.started if call_proc else None,
        shell_missing=unstarted,
        subagent_alive=subagent_alive,
    )
    started = f"pid {call_proc.pid} at {call_cpu:.1f}% CPU" if call_proc else ""
    if not stalled:
        busy_for = f"Busy for {brief((now - turn.started).total_seconds())}; " if turn else ""
        tail = f", {started}" if started else ""
        return Verdict("working", f"{busy_for}last sign of progress {brief(quiet)} ago; {doing}{tail}.")
    alive = f"pid {proc.pid} alive at {proc.cpu:.1f}% CPU" if proc else "process not checked"
    hint = " It may be waiting for your approval." if unstarted else ""
    return Verdict("stalled", f"Claims busy but quiet for {brief(quiet)}: {doing}; {started or alive}.{hint}")


def judge_agent(
    *,
    now: datetime,
    last_sign: datetime | None,
    pending: list[Pending],
    call_proc: Proc | None,
    procs_known: bool,
    subagent_alive: bool | None,
    stall_after_s: int,
    limit: int,
) -> Verdict:
    """The state of an unfinished subagent; its open shell call follows the same rule as a session's.

    ``subagent_alive`` says whether a subagent of its own that it waits on still works; None for none.
    """
    unstarted = _unstarted(pending[0] if pending else None, call_proc, procs_known)
    stalled, doing, quiet = _progress(
        now,
        last_sign,
        pending,
        stall_after_s,
        limit,
        shell_started=call_proc.started if call_proc else None,
        shell_missing=unstarted,
        subagent_alive=subagent_alive,
    )
    if stalled:
        hint = " It may be waiting for your approval." if unstarted else ""
        return Verdict("stalled", f"No progress for {brief(quiet)}: {doing}.{hint}")
    return Verdict("working", f"Last transcript write {brief(quiet)} ago; {doing}.")


def finished(state: State, now: datetime, last_sign: datetime | None) -> Verdict:
    """A subagent or command that is over: ``done`` or ``failed``."""
    word = "Finished" if state == "done" else "Failed"
    return Verdict(state, f"{word} {brief(_quiet(now, last_sign))} ago.")


def judge_command(
    *,
    now: datetime,
    command: Launched,
    alive: bool,
    cpu: float,
    ports: list[int],
    ports_known: bool,
    procs_known: bool,
    stall_after_s: int,
) -> Verdict:
    """The state of a background command: over, serving, working, stalled, or gone without a word."""
    if command.stopped:
        return Verdict("done", f"Stopped by the session {brief(_quiet(now, command.stopped))} ago.")
    if command.notice:
        return finished(
            "done" if command.notice.status == "completed" else "failed", now, command.notice.time
        )
    quiet = _quiet(now, command.last_output or command.started)
    silence = f"no output for {brief(quiet)}" if command.tracked else "output not tracked"
    if procs_known and not alive:
        return Verdict("gone", f"Process gone with no completion notice; {silence}.")
    running = f"Running {brief(_quiet(now, command.started))}"
    if ports:
        listening = ", ".join(f":{port}" for port in ports)
        return Verdict("serving", f"Listening on {listening}; running {brief(_quiet(now, command.started))}.")
    if cpu > CPU_FLOOR:
        return Verdict("working", f"{running}; using {cpu:.1f}% CPU.")
    used = f"{cpu:.1f}% CPU" if procs_known else "process not checked"
    listening = "no listening port" if ports_known else "listening ports unknown"
    if not command.tracked:  # with no output file to go by, quiet is not evidence of a stall
        return Verdict("working", f"{running}; {silence}, {used}, {listening}.")
    if quiet <= stall_after_s:
        return Verdict("working", f"{running}; last output {brief(quiet)} ago.")
    return Verdict("stalled", f"{running}; {silence}, {used}, {listening}.")
