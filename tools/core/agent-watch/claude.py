"""Claude Code as agent-watch reads it: the live-session registry and each session's transcript tail."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from shed.lib import transcripts as t

TAIL_START = 512 * 1024
TAIL_CAP = 8 * 1024 * 1024
_PROC_START = "%a %b %d %H:%M:%S %Y"
_PROMPT, _ACTIVITY, _END = 0, 1, 2
MEMO_LIMIT = 512
OUTPUT_TAIL_BYTES = 64 * 1024
STOP_TOOL = "TaskStop"
_OCTAL = re.compile(r"\\([0-7]{3})")
_WRAPPER = re.compile(r"\beval '(.*)'(?: < /dev/null)? && pwd -P", re.DOTALL)
# any name containing a secret-like word; leaning toward withholding, a counter like max_tokens matches too
_SECRET_NAME = r"[A-Za-z0-9_.-]*(?:pass|pwd|secret|token|credential|key)[A-Za-z0-9_.-]*"
_SECRET_LINE = re.compile(
    r"\bauthorization\s*:|\bbearer\s+\S"  # a header that sends one
    rf"|{_SECRET_NAME}[\"']?\s*[:=]\s*\S"  # such a name being set, quoted or not
    r"|://[^\s/:@]+:[^\s/@]+@"  # user:pass@ in a URL
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|\b(?:sk-[A-Za-z0-9]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{12,})",  # well-known key prefixes
    re.IGNORECASE,
)
WITHHELD = "[withheld: may carry credentials]"
SCAN_PAST_CUT = 256
_MEMO: dict[Path, tuple[tuple[int, int, bytes], tuple[t.Session, bool]]] = {}


@dataclass(slots=True)
class Registered:
    """One record of the live-session registry, a JSON file named after the session's pid."""

    pid: int
    session_id: str
    cwd: str
    name: str
    kind: str
    entrypoint: str
    version: str
    status: str
    waiting_for: str
    proc_start: datetime | None
    """When the registry says the process started; it stores UTC text."""
    status_at: datetime | None
    """When the status last changed."""


@dataclass(slots=True)
class Pending:
    """A tool call that has no result yet."""

    id: str
    name: str
    input: dict[str, Any]
    time: datetime


@dataclass(slots=True)
class Turn:
    started: datetime
    prompt: str = ""
    tool_calls: int = 0
    ended: datetime | None = None
    """None while the turn is open."""


@dataclass(slots=True)
class Agent:
    """A subagent: its metadata file and the tail of its own transcript."""

    id: str
    type: str
    description: str
    use_id: str
    """The tool call that started it, in its parent's transcript."""
    depth: int
    mode: str
    """``foreground`` when its parent waits for it, else ``background``."""
    parent: str
    """The subagent that started it, or "" when the session did."""
    finished: bool
    log: t.Session


def _proc_start(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, _PROC_START).replace(tzinfo=UTC)
    except ValueError:
        return None


def _epoch_ms(value: object) -> datetime | None:
    return datetime.fromtimestamp(value / 1000, UTC) if isinstance(value, int | float) and value > 0 else None


def read_registry(folder: Path, notes: list[str]) -> list[Registered]:
    """Every readable registry record; the key files beside them are never opened."""
    if not folder.is_dir():
        notes.append(f"no Claude Code session registry at {folder}")
        return []
    found: list[Registered] = []
    for path in sorted(folder.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            found.append(
                Registered(
                    pid=int(data["pid"]),
                    session_id=str(data["sessionId"]),
                    cwd=str(data.get("cwd") or ""),
                    name=str(data.get("name") or ""),
                    kind=str(data.get("kind") or ""),
                    entrypoint=str(data.get("entrypoint") or ""),
                    version=str(data.get("version") or ""),
                    status=str(data.get("status") or ""),
                    waiting_for=str(data.get("waitingFor") or ""),
                    proc_start=_proc_start(data.get("procStart")),
                    status_at=_epoch_ms(data.get("statusUpdatedAt")),
                )
            )
        except (OSError, ValueError, KeyError, TypeError):
            notes.append(f"registry record {path.name} is unreadable")
    return found


def find_transcript(root: Path, session_id: str) -> Path | None:
    """The session's transcript, whichever project folder holds it."""
    return next(iter(sorted(root.glob(f"*/{session_id}.jsonl"))), None) if root.is_dir() else None


def _has_boundary(session: t.Session) -> bool:
    typed = any(not prompt.summary for prompt in session.prompts)
    return typed or bool(session.turn_ends) or any(r.stop_reason == "end_turn" for r in session.replies)


def read_tail(path: Path) -> tuple[t.Session, bool]:
    """The transcript's tail, grown until it holds a turn boundary; True when the cap was hit without one."""
    size = path.stat().st_size
    tail = TAIL_START
    while True:
        session = t.read_session(path, path.parent.name, tail_bytes=tail)
        if tail >= size or _has_boundary(session):
            return session, False
        if tail >= TAIL_CAP:
            return session, True
        tail = min(tail * 2, TAIL_CAP)


def read_tail_cached(path: Path) -> tuple[t.Session, bool]:
    """``read_tail`` for a transcript nobody writes to any more: read again only when the file changed."""
    stat = path.stat()
    with path.open("rb") as handle:
        handle.seek(max(stat.st_size - 256, 0))
        key = (stat.st_size, stat.st_mtime_ns, handle.read())
    hit = _MEMO.get(path)
    if hit and hit[0] == key:
        return hit[1]
    if len(_MEMO) >= MEMO_LIMIT:
        _MEMO.clear()
    _MEMO[path] = (key, read_tail(path))
    return _MEMO[path][1]


def read_agents(transcript: Path, oldest: float, notes: list[str]) -> list[Agent]:
    """The session's subagents whose transcripts were written to since ``oldest`` (an epoch time)."""
    found: list[Agent] = []
    for meta_path in sorted((transcript.parent / transcript.stem / "subagents").glob("agent-*.meta.json")):
        stem = meta_path.name.removesuffix(".meta.json")
        log = meta_path.with_name(f"{stem}.jsonl")
        try:
            if log.stat().st_mtime < oldest:
                continue
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            depth = int(meta.get("spawnDepth") or 1)
            tail = read_tail_cached(log)[0]
        except (OSError, ValueError, TypeError, AttributeError):
            notes.append(f"subagent {stem} of {transcript.stem[:8]} is unreadable")
            continue
        found.append(
            Agent(
                id=stem.removeprefix("agent-"),
                type=str(meta.get("agentType") or ""),
                description=str(meta.get("description") or ""),
                use_id=str(meta.get("toolUseId") or ""),
                depth=depth,
                mode="background" if meta.get("requestShape") == "background" else "foreground",
                parent=str(meta.get("parentAgentId") or ""),
                finished=meta.get("finished") is True,
                log=tail,
            )
        )
    return found


def agent_outcome(agent: Agent, logs: list[t.Session], notices: dict[str, t.TaskNotice]) -> str | None:
    """``done`` or ``failed`` once the subagent is over, None while it is in progress.

    ``logs`` are the transcripts its starting call can live in: the session's and every subagent's.
    """
    result = next((r for log in logs for r in log.tool_results if r.tool_use_id == agent.use_id), None)
    called = any(u.id == agent.use_id for log in logs for u in log.tool_uses)
    notice = notices.get(agent.id)
    foreground = agent.mode == "foreground"
    # a foreground call that is answered, or too old to be in any tail, is over
    if not (agent.finished or notice or (foreground and (result is not None or not called))):
        return None
    failed = (foreground and result is not None and result.is_error) or (
        notice and notice.status != "completed"
    )
    return "failed" if failed else "done"


def turns(session: t.Session) -> list[Turn]:
    """The tail's turns, oldest first; the last one is open when nothing has ended it."""
    typed = [p for p in session.prompts if not p.summary]  # a compaction summary starts no turn
    events: list[tuple[datetime, int, str]] = [(p.time, _PROMPT, p.text) for p in typed]
    events += [(u.time, _ACTIVITY, "call") for u in session.tool_uses]
    events += [(r.time, _ACTIVITY, "") for r in session.tool_results]
    events += [(r.time, _END if r.stop_reason == "end_turn" else _ACTIVITY, "") for r in session.replies]
    events += [(when, _END, "") for when in session.turn_ends]
    found: list[Turn] = []
    for when, kind, text in sorted(events, key=lambda event: event[:2]):
        current = found[-1] if found and found[-1].ended is None else None
        if kind == _END:
            if found:  # also moves a closed turn's end to the turn-end record that follows its last reply
                found[-1].ended = when
            continue
        if current is None:
            current = Turn(started=when, prompt=text if kind == _PROMPT else "")
            found.append(current)
        elif kind == _PROMPT and not current.prompt:  # activity cut off from its own prompt by the tail
            current = found[-1] = Turn(started=when, prompt=text)
        if text == "call":
            current.tool_calls += 1
    return found


def pending_calls(session: t.Session, since: datetime) -> list[Pending]:
    """Tool calls made at or after ``since`` that have no result yet, oldest first."""
    answered = {r.tool_use_id for r in session.tool_results}
    return [
        Pending(u.id, u.name, u.input, u.time)
        for u in session.tool_uses
        if u.id not in answered and u.time >= since
    ]


def newest_model(session: t.Session) -> str | None:
    """The model named by the newest assistant record that names one."""
    return next((r.model for r in reversed(session.replies) if r.model), None)


def wrapped_command(command_line: str) -> str | None:
    """The command a Bash-tool wrapper shell runs, or None when the process is not such a wrapper.

    Undoes the shell's quoting of single quotes and the octal escapes `ps` prints for newlines and the like.
    """
    match = _WRAPPER.search(command_line)
    if match is None:
        return None
    text = match.group(1).replace("'\"'\"'", "'").replace("'\\''", "'")
    return _OCTAL.sub(lambda found: chr(int(found.group(1), 8)), text)


@dataclass(slots=True)
class Launched:
    """A background command as the transcript and its output file describe it."""

    id: str
    command: str
    description: str
    started: datetime
    output_bytes: int | None
    last_output: datetime | None
    notice: t.TaskNotice | None
    stopped: datetime | None
    """When the session stopped it with its own stop call."""
    output: Path | None = None
    tracked: bool = True
    """False when only the process is known, so there is no output file to judge it by."""

    @property
    def over(self) -> bool:
        """Whether the transcript says it ended: a completion notice, or the session's own stop call."""
        return self.stopped is not None or self.notice is not None


def endings(logs: list[t.Session]) -> tuple[dict[str, t.TaskNotice], dict[str, datetime]]:
    """Per task id, across the given transcripts: its completion notice, and when a stop call named it.

    The notice for a command a subagent started is written to the session's transcript, not the subagent's.
    """
    notices = {notice.task_id: notice for log in logs for notice in log.notices}
    stops = {
        str(use.input.get("task_id") or use.input.get("shell_id") or ""): use.time
        for log in logs
        for use in log.tool_uses
        if use.name == STOP_TOOL
    }
    return notices, stops


def read_commands(
    session: t.Session,
    transcript: Path,
    tasks_dir: Path,
    notices: dict[str, t.TaskNotice],
    stops: dict[str, datetime],
) -> list[Launched]:
    """The background commands whose launch is inside this transcript's tail, oldest first."""
    uses = {use.id: use for use in session.tool_uses}
    answered = {result.tool_use_id: result.time for result in session.tool_results}
    folder = tasks_dir / transcript.parent.name / transcript.stem / "tasks"
    found: list[Launched] = []
    for use_id, task_id in session.background.items():
        use = uses.get(use_id)
        size, last = None, None
        try:
            stat = (folder / f"{task_id}.output").stat()
            size, last = stat.st_size, datetime.fromtimestamp(stat.st_mtime, UTC)
        except OSError:
            pass  # no output file: the command is judged on its process alone
        found.append(
            Launched(
                id=task_id,
                command=str(use.input.get("command") or "") if use else "",
                description=str(use.input.get("description") or "") if use else "",
                started=use.time if use else answered[use_id],
                output_bytes=size,
                last_output=last,
                notice=notices.get(task_id),
                stopped=stops.get(task_id),
                output=folder / f"{task_id}.output" if size is not None else None,
            )
        )
    return sorted(found, key=lambda command: command.started)


def output_tail(path: Path, count: int, limit: int) -> list[str]:
    """The last ``count`` lines of a command's output, cut to ``limit``; lines that set or send a credential
    are withheld. Raises OSError when the file cannot be read."""
    lines = [line.rstrip("\n") for line in t.tail_lines(path, OUTPUT_TAIL_BYTES)]
    # scanned a little past the cut: that bounds the cost, and a credential the cut splits is still seen
    scanned = [t.one_line(line, limit + SCAN_PAST_CUT) for line in lines if line][-count:]
    return [WITHHELD if _SECRET_LINE.search(line) else t.one_line(line, limit) for line in scanned]
