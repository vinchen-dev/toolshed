"""The shape of an agent-watch result, and what one run knows about the machine while it builds one."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from .probe import Proc
from .states import ORDER, State


class TurnInfo(BaseModel):
    started: str | None = None
    elapsed_s: int | None = None
    prompt: str | None = None
    tool_calls: int | None = None


class CommandItem(BaseModel):
    """A shell command a session started in the background."""

    id: str
    command: str
    description: str | None = None
    pid: int | None = None
    started: str | None = None
    runtime_s: int | None = None
    state: State
    reason: str
    quiet_s: int | None = None
    """Seconds since its last output; None when its output is not tracked."""
    output_bytes: int | None = None
    ports: list[int] = []
    tail: list[str] | None = None
    """The last lines of its output, only when asked for."""


class SubagentItem(BaseModel):
    id: str
    type: str
    description: str
    model: str | None = None
    mode: Literal["foreground", "background"]
    depth: int
    state: State
    reason: str
    started: str | None = None
    quiet_s: int | None = None
    tool_calls: int = 0
    activity: str | None = None
    subagents: list[SubagentItem] = []


class SessionItem(BaseModel):
    id: str
    agent: Literal["claude", "codex"]
    name: str
    title: str | None = None
    project: str
    pid: int | None = None
    kind: str | None = None
    entrypoint: str | None = None
    model: str | None = None
    started: str | None = None
    """When the session began: its process start, or its first record once no process holds it."""
    state: State
    reason: str
    quiet_s: int | None = None
    turn: TurnInfo | None = None
    activity: str | None = None
    activity_pid: int | None = None
    """The process the open tool call started, when one was found."""
    waiting_on: str | None = None
    """The id of the item this session is waiting on."""
    last_write: str | None = None
    """When the transcript was last written to; set for sessions whose process is gone."""
    helpers: int = 0
    """Child processes that are not tool calls: MCP servers and the like."""
    subagents_running: int = 0
    subagents_finished: int = 0
    commands_running: int = 0
    commands_finished: int = 0
    subagents: list[SubagentItem] = []
    commands: list[CommandItem] = []
    children: list[SessionItem] = []
    """Sessions this one launched, such as `codex exec` started from a tool call."""


class Report(BaseModel):
    taken: str
    stall_after_s: int
    window_s: int
    retain_s: int
    versions: dict[str, list[str]]
    """Agent CLI versions seen, per CLI."""
    counts: dict[str, int]
    """Items per state: sessions, subagents and commands together."""
    attention: int
    sessions: list[SessionItem]
    notes: list[str]


def by_state(item: SubagentItem | SessionItem | CommandItem) -> tuple[int, str]:
    """Attention first, then by name, so every listing reads the same way."""
    return ORDER.index(item.state), item.name if isinstance(item, SessionItem) else item.id


def folder(project: str) -> str:
    """A session's folder, with the home folder written as ~."""
    home = str(Path.home())
    return "~" + project[len(home) :] if project.startswith(home) else project


def subagent_counts(session: SessionItem) -> str:
    """How many of the session's subagents are running and finished, or "" when it started none."""
    if not (session.subagents_running or session.subagents_finished):
        return ""
    return f"subagents: {session.subagents_running} running, {session.subagents_finished} finished"


def keep_states(item: SessionItem | SubagentItem, wanted: Collection[str]) -> bool:
    """Drop what is not in a wanted state from the item's tree, keeping the parents of what is.

    Changes the item in place and returns True when the item itself stays.
    """
    item.subagents = [agent for agent in item.subagents if keep_states(agent, wanted)]
    if isinstance(item, SessionItem):
        item.commands = [command for command in item.commands if command.state in wanted]
        item.children = [child for child in item.children if keep_states(child, wanted)]
        return item.state in wanted or bool(item.subagents or item.commands or item.children)
    return item.state in wanted or bool(item.subagents)


@dataclass(slots=True)
class Scan:
    """What one run knows about the machine, shared by every item it judges."""

    now: datetime
    stall_after_s: int
    window_s: int
    retain_s: int
    max_chars: int
    tasks_dir: Path
    tail_lines: int = 0
    procs: dict[int, Proc] = field(default_factory=dict)
    procs_known: bool = True
    children: dict[int, list[Proc]] = field(default_factory=dict)
    ports: dict[int, list[int]] = field(default_factory=dict)
    ports_known: bool = True
    notes: list[str] = field(default_factory=list)
    versions: dict[str, set[str]] = field(default_factory=dict)

    def saw_version(self, agent: str, version: str) -> None:
        if version:
            self.versions.setdefault(agent, set()).add(version)

    def subtree(self, pid: int) -> list[Proc]:
        """The process and everything below it."""
        found = [self.procs[pid]] if pid in self.procs else []
        for child in self.children.get(pid, []):
            found.extend(self.subtree(child.pid))
        return found
