"""Codex as agent-watch reads it: one rollout file per session, judged from its own records first."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from shed.lib import transcripts as t

from . import claude, states
from .models import Scan, SessionItem
from .probe import Proc
from .sessions import turn_info

PAIR_SLACK_S = 10
_CALLS = ("custom_tool_call", "function_call")
_OUTPUTS = ("custom_tool_call_output", "function_call_output")
_EPOCH = datetime.min.replace(tzinfo=UTC)


@dataclass(slots=True)
class Rollout:
    """What one rollout file says about its session: its metadata line and the tail of its records."""

    id: str
    cwd: str
    originator: str
    source: str
    version: str
    started: datetime | None
    last_write: datetime | None = None
    turn: claude.Turn | None = None
    """The newest turn in the tail; open while it has no completion record."""
    model: str | None = None
    pending: dict[str, claude.Pending] = field(default_factory=dict)


def _message_text(content: object) -> str:
    parts = content if isinstance(content, list) else []
    return " ".join(str(part.get("text") or "") for part in parts if isinstance(part, dict)).strip()


def _apply(out: Rollout, kind: str, payload: dict[str, Any], when: datetime | None) -> None:
    """Fold one record into what is known about the session."""
    if when is None:
        return
    out.last_write = when if out.last_write is None or when > out.last_write else out.last_write
    what = payload.get("type")
    if kind == "turn_context" and isinstance(payload.get("model"), str):
        out.model = payload["model"]
    elif kind == "event_msg" and what == "task_started":
        out.turn = claude.Turn(started=when)
        out.pending.clear()
    elif kind == "event_msg" and what == "task_complete":
        out.turn = out.turn or claude.Turn(started=when)  # the turn began before the tail
        out.turn.ended = when
        out.pending.clear()
    elif kind == "response_item":
        out.turn = out.turn or claude.Turn(started=when)
        call_id = str(payload.get("call_id") or "")
        if what == "message" and payload.get("role") == "user" and not out.turn.prompt:
            text = _message_text(payload.get("content"))
            out.turn.prompt = "" if text.startswith("<") else text  # injected context blocks are not prompts
        elif what in _CALLS:
            out.turn.tool_calls += 1
            script = str(payload.get("input") or payload.get("arguments") or "")
            out.pending[call_id] = claude.Pending(
                call_id, str(payload.get("name") or "tool"), {"command": script}, when
            )
        elif what in _OUTPUTS:
            out.pending.pop(call_id, None)


def read_rollout(path: Path) -> Rollout | None:
    """The session a rollout file describes, or None when the file cannot be read as one."""
    try:
        meta = json.loads(next(t.tail_lines(path, None), "")).get("payload")
        tail = list(t.tail_lines(path, claude.TAIL_START))
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(meta, dict) or not meta.get("id"):
        return None
    out = Rollout(
        id=str(meta["id"]),
        cwd=str(meta.get("cwd") or ""),
        originator=str(meta.get("originator") or ""),
        source=str(meta.get("source") or ""),
        version=str(meta.get("cli_version") or ""),
        started=claude.when(meta.get("timestamp")),
    )
    for raw in tail:
        try:
            record = json.loads(raw)
        except ValueError:
            continue
        if isinstance(record, dict) and isinstance(record.get("payload"), dict):
            _apply(
                out, str(record.get("type") or ""), record["payload"], claude.when(record.get("timestamp"))
            )
    return out


def _thread_names(index: Path) -> dict[str, str]:
    """Thread name per session id from the Codex session index, when there is one."""
    names: dict[str, str] = {}
    try:
        lines = index.read_text(encoding="utf-8", errors="replace").split("\n")
    except OSError:
        return names
    for raw in lines:
        try:
            row = json.loads(raw)
            names[str(row["id"])] = str(row["thread_name"])
        except (ValueError, KeyError, TypeError):
            continue
    return names


def _is_codex(command: str) -> bool:
    """A Codex CLI process: `codex ...`, or an interpreter running a file named codex."""
    return any(Path(word).name == "codex" for word in command.split()[:2])


def _pair(rollout: Rollout, free: list[Proc]) -> Proc | None:
    """Claim the Codex process that started just before this session's metadata was written."""
    if rollout.started is None:
        return None
    near = [proc for proc in free if -1 <= (rollout.started - proc.started).total_seconds() <= PAIR_SLACK_S]
    if not near:
        return None
    best = min(near, key=lambda proc: abs((rollout.started or _EPOCH) - proc.started))
    free.remove(best)
    return best


def _judge(rollout: Rollout, proc: Proc | None, scan: Scan) -> states.Verdict | None:
    """The session's state, or None when it is over and outside the retention period or cannot be told."""
    turn, last = rollout.turn, rollout.last_write
    quiet = (scan.now - last).total_seconds() if last else scan.retain_s + 1
    if turn and turn.ended is None:
        if proc is None and scan.procs_known and quiet > scan.stall_after_s:
            return states.gone(scan.now, last, turn) if quiet <= scan.retain_s else None
        return states.judge_session(
            now=scan.now,
            last_sign=last,
            turn=turn,
            pending=sorted(rollout.pending.values(), key=lambda call: call.time),
            proc=proc,
            call_proc=None,
            call_cpu=0.0,
            procs_known=False,
            waiting_on="",
            subagent_alive=None,
            stall_after_s=scan.stall_after_s,
            limit=scan.max_chars,
        )
    if proc:
        return states.idle(scan.now, last)
    if not scan.procs_known or quiet > scan.retain_s:
        return None  # idle and ended cannot be told apart, or it ended too long ago
    return states.ended(scan.now, last)


def _item(rollout: Rollout, proc: Proc | None, name: str, scan: Scan) -> SessionItem | None:
    verdict = _judge(rollout, proc, scan)
    if verdict is None:
        return None
    scan.saw_version("codex", rollout.version)
    in_turn = verdict.state in ("working", "stalled")
    pending = sorted(rollout.pending.values(), key=lambda call: call.time)
    shown = pending[0] if pending and in_turn else None
    over = verdict.state in ("ended", "gone")
    return SessionItem(
        id=rollout.id,
        agent="codex",
        name=name or f"codex {rollout.source}".strip(),
        project=rollout.cwd,
        pid=proc.pid if proc else None,
        kind=rollout.originator or None,
        entrypoint=rollout.source or None,
        model=rollout.model,
        started=t.manila(rollout.started) or None,
        state=verdict.state,
        reason=verdict.reason,
        quiet_s=int((scan.now - rollout.last_write).total_seconds()) if rollout.last_write else None,
        turn=turn_info(rollout.turn, scan),
        activity=states.describe_call(shown.name, shown.input, scan.max_chars) if shown else None,
        last_write=t.manila(rollout.last_write) if over and rollout.last_write else None,
    )


def codex_sessions(home: Path, scan: Scan) -> list[tuple[SessionItem, Proc | None]]:
    """Every Codex session worth listing, each with the live process paired to it, if any."""
    folder = home / "sessions"
    if not folder.is_dir():
        scan.notes.append(f"no Codex sessions at {folder}")
        return []
    names = _thread_names(home / "session_index.jsonl")
    oldest = scan.now.timestamp() - scan.retain_s
    rollouts: list[Rollout] = []
    for path in sorted(folder.glob("*/*/*/rollout-*.jsonl")):
        try:
            if path.stat().st_mtime < oldest:
                continue
        except OSError:
            scan.notes.append(f"rollout {path.name} is unreadable")
            continue
        rollout = read_rollout(path)
        if rollout is None:
            scan.notes.append(f"rollout {path.name} is unreadable")
        else:
            rollouts.append(rollout)
    free = [proc for proc in scan.procs.values() if _is_codex(proc.command)]
    found: list[tuple[SessionItem, Proc | None]] = []
    for rollout in sorted(rollouts, key=lambda rollout: rollout.started or _EPOCH):
        proc = _pair(rollout, free)
        item = _item(rollout, proc, names.get(rollout.id, ""), scan)
        if item:
            found.append((item, proc))
    return found
