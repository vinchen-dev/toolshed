"""Build the result items for Claude Code: each session with its subagents and background commands."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from shed.lib import transcripts as t

from . import claude, states
from .commands import Shells, session_shells
from .models import Scan, SessionItem, SubagentItem, TurnInfo, by_state

START_TOLERANCE_S = 2
_EPOCH = datetime.min.replace(tzinfo=UTC)


def _is_live(record: claude.Registered, scan: Scan) -> bool:
    """A registry record is trusted only while its pid is the process that wrote it."""
    if not scan.procs_known:
        return True
    proc = scan.procs.get(record.pid)
    if proc is None:
        return False
    if record.proc_start is None:
        return True
    return abs((proc.started - record.proc_start).total_seconds()) <= START_TOLERANCE_S


def turn_info(turn: claude.Turn | None, scan: Scan) -> TurnInfo | None:
    if turn is None:
        return None
    return TurnInfo(
        started=t.manila(turn.started),
        elapsed_s=int(((turn.ended or scan.now) - turn.started).total_seconds()),
        prompt=t.one_line(turn.prompt, scan.max_chars) or None,
        tool_calls=turn.tool_calls,
    )


@dataclass(slots=True)
class Delegated:
    """A session's subagents as a tree, and what they mean for the session that started them."""

    roots: list[SubagentItem] = field(default_factory=list)
    running: int = 0
    finished: int = 0
    last_sign: datetime | None = None
    """The newest sign of progress among the subagents still in progress."""
    by_call: dict[str, str] = field(default_factory=dict)
    """Subagent id per starting tool call, for the ones still in progress."""
    unfinished: list[tuple[datetime, str, str]] = field(default_factory=list)
    """(last sign of progress, id, label) of each subagent still in progress."""
    stalled: set[str] = field(default_factory=set)
    """Ids of the subagents in progress that were judged stalled."""

    def behind(self, pending: list[claude.Pending]) -> list[str]:
        """The subagents in progress that these open calls started."""
        return [self.by_call[call.id] for call in pending if call.id in self.by_call]

    def working(self, agent_ids: Iterable[str]) -> bool | None:
        """Whether any of these subagents in progress still works; None when there are none."""
        found = [agent_id not in self.stalled for agent_id in agent_ids]
        return any(found) if found else None

    def stalest(self) -> tuple[str, str]:
        """(id, label) of the unfinished subagent that has been quiet longest, or two empty strings."""
        return min(self.unfinished)[1:] if self.unfinished else ("", "")

    def sign_of(self, agent_id: str) -> datetime | None:
        """The last sign of progress of an unfinished subagent, its own subagents included."""
        return next(
            (sign for sign, known, _ in self.unfinished if known == agent_id and sign != _EPOCH), None
        )


def _subagents(
    agents: list[claude.Agent], outcomes: dict[str, str | None], shells: Shells, procs_known: bool, scan: Scan
) -> Delegated:
    """The subagents as a tree of items; ``outcomes`` says which are over, ``shells`` what their calls run."""
    known = {agent.id for agent in agents}
    children: dict[str, list[claude.Agent]] = {}
    for agent in agents:
        children.setdefault(agent.parent if agent.parent in known else "", []).append(agent)
    out = Delegated()

    def build(agent: claude.Agent) -> tuple[SubagentItem | None, datetime | None]:
        """The subagent's item (None once it is outside the window) and its sign of progress if unfinished."""
        built = [build(child) for child in children.get(agent.id, [])]
        outcome = outcomes[agent.id]
        signs = [when for when in (agent.log.ended, *(sign for _, sign in built)) if when]
        sign = max(signs) if signs else None
        pending = claude.pending_calls(agent.log, _EPOCH)
        if outcome:
            out.finished += 1
            verdict = states.finished("done" if outcome == "done" else "failed", scan.now, agent.log.ended)
        else:
            out.running += 1
            out.by_call[agent.use_id] = agent.id
            label = t.one_line(f"{agent.type}: {agent.description}", scan.max_chars)
            out.unfinished.append((sign or _EPOCH, agent.id, label))
            out.last_sign = max(out.last_sign, sign) if out.last_sign and sign else out.last_sign or sign
            verdict = states.judge_agent(
                now=scan.now,
                last_sign=sign,
                pending=pending,
                call_proc=shells.called.get(pending[0].id) if pending else None,
                procs_known=procs_known,
                subagent_alive=out.working(out.behind(pending)),  # its own were judged just above
                stall_after_s=scan.stall_after_s,
                limit=scan.max_chars,
            )
            if verdict.state == "stalled":
                out.stalled.add(agent.id)
        kept = [item for item, _ in built if item is not None]
        ended = agent.log.ended
        if outcome and not kept and ended and (scan.now - ended).total_seconds() > scan.window_s:
            return None, None
        shown = pending[0] if pending and not outcome else None
        item = SubagentItem(
            id=agent.id,
            type=agent.type,
            description=t.one_line(agent.description, scan.max_chars),
            model=claude.newest_model(agent.log),
            mode="background" if agent.mode == "background" else "foreground",
            depth=agent.depth,
            state=verdict.state,
            reason=verdict.reason,
            started=t.manila(agent.log.started) or None,
            quiet_s=int((scan.now - sign).total_seconds()) if sign else None,
            tool_calls=len(agent.log.tool_uses),
            activity=states.describe_call(shown.name, shown.input, scan.max_chars) if shown else None,
            subagents=sorted(kept, key=by_state),
        )
        return item, None if outcome else sign

    out.roots = sorted((item for item, _ in map(build, children.get("", [])) if item), key=by_state)
    return out


def _judge(
    record: claude.Registered | None,
    open_turn: claude.Turn | None,
    pending: list[claude.Pending],
    last_sign: datetime | None,
    vouched: bool,
    name: str,
    shells: Shells,
    waiting_on: str,
    subagent_alive: bool | None,
    scan: Scan,
) -> states.Verdict:
    """The session's state: from the registry's claim and the evidence, or from the transcript alone."""
    status = record.status if record else ""
    if record and status == "waiting":
        return states.waiting(scan.now, last_sign, record.waiting_for or "input needed")
    if record and status == "idle":
        return states.idle(scan.now, last_sign)
    if record is None and open_turn is None:
        return states.ended(scan.now, last_sign)
    if record is None and open_turn and vouched:
        return states.gone(scan.now, last_sign, open_turn)
    if record is None and any(call.name == states.QUESTION_TOOL for call in pending):
        return states.waiting(scan.now, last_sign, "a question is pending")
    if record and status != "busy":
        scan.notes.append(f'unknown registry status "{status}" for {name}, treated as busy')
    return states.judge_session(
        now=scan.now,
        last_sign=last_sign,
        turn=open_turn,
        pending=pending,
        proc=scan.procs.get(record.pid) if record else None,
        call_proc=shells.call_proc,
        call_cpu=shells.call_cpu,
        procs_known=scan.procs_known and record is not None,
        waiting_on=waiting_on,
        subagent_alive=subagent_alive,
        stall_after_s=scan.stall_after_s,
        limit=scan.max_chars,
    )


def _last_turn_counts(session: t.Session, turn: claude.Turn | None) -> tuple[int, int]:
    """How many subagents and background commands the given turn started."""
    if turn is None:
        return 0, 0

    def inside(when: datetime) -> bool:
        return turn.started <= when and (turn.ended is None or when <= turn.ended)

    started = {use.id for use in session.tool_uses if inside(use.time)}
    agents = sum(1 for use in session.tool_uses if use.id in started and use.name in states.AGENT_TOOLS)
    return agents, sum(1 for use_id in session.background if use_id in started)


def _read(path: Path | None, live: bool, scan: Scan) -> tuple[t.Session | None, bool]:
    """The transcript's tail, or nothing and a note when the file cannot be read."""
    if path is None:
        return None, False
    try:
        return claude.read_tail(path) if live else claude.read_tail_cached(path)
    except OSError:
        scan.notes.append(f"transcript {path.name} is unreadable")
        return None, False


def _claude_session(
    session_id: str, path: Path | None, record: claude.Registered | None, vouched: bool, scan: Scan
) -> SessionItem | None:
    """One Claude Code session; ``record`` is its live registry record, or None when no process holds it."""
    session, capped = _read(path, record is not None, scan)
    last_write = session.ended if session else None
    signs = [when for when in (last_write, record.status_at if record else None) if when]
    if record is None and (not signs or (scan.now - max(signs)).total_seconds() > scan.retain_s):
        return None  # nothing holds it and its newest record is older than the retention period
    found = claude.turns(session) if session else []
    last = found[-1] if found else None
    open_turn = last if last and last.ended is None else None
    watched = record is not None or (open_turn is not None and not vouched)  # judged as a live session
    oldest = scan.now.timestamp() - scan.retain_s
    agents = claude.read_agents(path, oldest, scan.notes) if path and session and watched else []
    logs = [session, *(agent.log for agent in agents)] if session else []
    notices = {notice.task_id: notice for notice in session.notices} if session else {}
    outcomes = {agent.id: claude.agent_outcome(agent, logs, notices) for agent in agents}
    running_logs = [agent.log for agent in agents if outcomes[agent.id] is None]
    pending = claude.pending_calls(session, open_turn.started) if session and open_turn else []
    shells = session_shells(record.pid, logs, running_logs, path, pending, scan) if record else Shells()
    delegated = _subagents(agents, outcomes, shells, scan.procs_known and record is not None, scan)
    # a subagent's progress counts for the session only while the session waits on it: through an open
    # call to that subagent, or, once its own turn has ended, for every subagent still in progress
    behind = delegated.behind(pending)
    held = sorted((delegated.sign_of(agent_id) or _EPOCH, agent_id) for agent_id in behind)
    signs += [sign for sign, _ in held if sign != _EPOCH]
    if open_turn is None and delegated.last_sign:
        signs.append(delegated.last_sign)
    held_by = held[0][1] if held else None
    last_sign = max(signs) if signs else None
    name = (record.name if record else "") or (session.title if session else "") or session_id[:8]
    began = (record.proc_start if record else None) or (claude.first_write(path) if path else None)
    scan.saw_version("claude", (record.version if record else "") or (session.version if session else ""))
    if capped:
        scan.notes.append(f"{name}: turn started before the last 8 MB of its transcript")
    # once its own turn has ended, a session with unfinished subagents is waiting on the quietest of them
    idle_wait, idle_label = delegated.stalest() if open_turn is None else ("", "")
    # through an open call the session is as alive as the subagents behind it; once its own turn has
    # ended, a working subagent keeps it alive and stalled ones leave it on its own clock
    in_progress = [agent_id for _, agent_id, _ in delegated.unfinished]
    alive = delegated.working(behind) if open_turn else (delegated.working(in_progress) or None)
    verdict = _judge(record, open_turn, pending, last_sign, vouched, name, shells, idle_label, alive, scan)
    in_turn = verdict.state in ("working", "stalled", "needs-input", "gone")
    question = next((call for call in pending if call.name == states.QUESTION_TOOL), None)
    shown = question or (pending[0] if pending else None)
    turn_agents, turn_commands = _last_turn_counts(session, last) if session else (0, 0)
    return SessionItem(
        id=session_id,
        agent="claude",
        name=name,
        title=(session.title or None) if session else None,
        project=(record.cwd if record else "") or (session.cwd if session else ""),
        pid=record.pid if record else None,
        kind=(record.kind or None) if record else None,
        entrypoint=(record.entrypoint or None) if record else None,
        model=claude.newest_model(session) if session else None,
        started=t.manila(began) or None,
        state=verdict.state,
        reason=verdict.reason,
        quiet_s=int((scan.now - last_sign).total_seconds()) if last_sign else None,
        turn=None if capped else turn_info(open_turn if in_turn else last, scan),
        activity=states.describe_call(shown.name, shown.input, scan.max_chars) if shown and in_turn else None,
        activity_pid=shells.call_proc.pid if shells.call_proc and in_turn else None,
        waiting_on=held_by or (idle_wait if in_turn and verdict.state != "needs-input" else None) or None,
        last_write=t.manila(last_write) if record is None and last_write else None,
        helpers=shells.helpers,
        subagents_running=delegated.running,
        subagents_finished=delegated.finished if watched else turn_agents,
        commands_running=shells.running,
        commands_finished=shells.finished if record else turn_commands,
        subagents=delegated.roots,
        commands=shells.items,
    )


def claude_sessions(root: Path, registry_dir: Path, scan: Scan) -> list[SessionItem]:
    """Every live Claude Code session, then the ones that ended or vanished inside the retention period."""
    known = len(scan.notes)
    records = [record for record in claude.read_registry(registry_dir, scan.notes) if _is_live(record, scan)]
    vouched = len(scan.notes) == known  # a missing folder or an unreadable record means it cannot vouch
    held = {record.session_id: claude.find_transcript(root, record.session_id) for record in records}
    found = [
        _claude_session(record.session_id, held[record.session_id], record, vouched, scan)
        for record in records
    ]
    if scan.procs_known and root.is_dir():
        oldest = scan.now.timestamp() - scan.retain_s  # a cheap first cut; the records decide
        for path in sorted(root.glob("*/*.jsonl")):
            if path.stem in held:
                continue
            try:
                recent = path.stat().st_mtime >= oldest
            except OSError:
                scan.notes.append(f"transcript {path.name} is unreadable")
                continue
            if recent:
                found.append(_claude_session(path.stem, path, None, vouched, scan))
    return [item for item in found if item is not None]
