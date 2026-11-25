"""Test doubles for agent-watch: a fake home tree, a fake probe, and builders for transcript records."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from shed import ToolContext, ToolError
from shed.lib import transcripts
from shed.lib.manila import MANILA

from . import probe as probing
from .tool import TOOL

NOW = datetime(2026, 11, 23, 9, 0, 0, tzinfo=UTC)  # 17:00:00 in Manila
CWD = "/tmp/work/checkout-api"
SID = "11111111-aaaa-bbbb-cccc-000000000001"
OTHER = "22222222-aaaa-bbbb-cccc-000000000002"
MODEL = "claude-fable-5-2"


def ago(**delta: float) -> datetime:
    return NOW - timedelta(**delta)


def iso(when: datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _record(kind: str, when: datetime, **extra: Any) -> str:
    return json.dumps({"type": kind, "timestamp": iso(when), "sessionId": SID, "cwd": CWD, **extra})


def prompt(when: datetime, text: str) -> str:
    return _record("user", when, message={"role": "user", "content": text})


def call(use_id: str, name: str, **tool_input: Any) -> dict[str, Any]:
    return {"type": "tool_use", "id": use_id, "name": name, "input": tool_input}


def words(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def said(
    when: datetime, mid: str, *blocks: dict[str, Any], stop: str = "tool_use", model: str = MODEL
) -> str:
    message = {"id": mid, "model": model, "usage": {}, "content": list(blocks), "stop_reason": stop}
    return _record("assistant", when, message=message)


def result(when: datetime, use_id: str, body: str = "ok", error: bool = False, **tool_use_result: Any) -> str:
    block = {"type": "tool_result", "tool_use_id": use_id, "content": body, "is_error": error}
    extra: dict[str, Any] = {"toolUseResult": tool_use_result} if tool_use_result else {}
    return _record("user", when, message={"role": "user", "content": [block]}, **extra)


def turn_end(when: datetime) -> str:
    return _record("system", when, subtype="turn_duration", durationMs=1000, messageCount=2)


class FakeProbe:
    """Stands in for the process table, the listening ports and the clock."""

    def __init__(self) -> None:
        self.procs: list[Any] = []
        self.ports: dict[int, list[int]] = {}
        self.at = NOW
        self.ps_error: str | None = None
        self.lsof_error: str | None = None

    def now(self) -> datetime:
        return self.at

    def processes(self) -> list[Any]:
        if self.ps_error:
            raise ToolError(self.ps_error)
        return list(self.procs)

    def listening_ports(self) -> dict[int, list[int]]:
        if self.lsof_error:
            raise ToolError(self.lsof_error)
        return dict(self.ports)


class Home:
    """A fake home tree: the Claude Code registry, transcripts and task output, and a Codex home."""

    def __init__(self, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.root = root
        self.projects = root / "claude" / "projects"
        self.sessions = root / "claude" / "sessions"
        self.tasks = root / "tasks"
        self.codex = root / "codex"
        for folder in (self.projects, self.sessions, self.tasks, self.codex / "sessions"):
            folder.mkdir(parents=True)
        self.probe = FakeProbe()
        monkeypatch.setattr(probing, "probe", self.probe)

    def process(
        self, pid: int, started: datetime, command: str = "claude", ppid: int = 1, cpu: float = 0.2
    ) -> None:
        local = started.astimezone(MANILA)  # ps reports local time; the registry stores UTC text
        proc = probing.Proc(pid=pid, ppid=ppid, pgid=pid, started=local, cpu=cpu, command=command)
        self.probe.procs.append(proc)

    def session(
        self,
        pid: int,
        sid: str = SID,
        cwd: str = CWD,
        status: str = "busy",
        name: str = "checkout-api-97",
        started: datetime | None = None,
        alive: bool = True,
        **extra: Any,
    ) -> None:
        """Write a registry record, and by default give it the live process it describes."""
        started = started or ago(hours=2)
        record = {
            "pid": pid,
            "sessionId": sid,
            "cwd": cwd,
            "name": name,
            "kind": "interactive",
            "entrypoint": "cli",
            "version": "2.1.322",
            "procStart": started.strftime("%a %b %d %H:%M:%S %Y"),
            "status": status,
            **extra,
        }
        (self.sessions / f"{pid}.json").write_text(json.dumps(record), encoding="utf-8")
        if alive:
            self.process(pid, started)

    def transcript(
        self, lines: list[str], sid: str = SID, cwd: str = CWD, written: datetime | None = None
    ) -> Path:
        folder = self.projects / transcripts.encode_project(Path(cwd))
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{sid}.jsonl"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        stamp = (written or NOW).timestamp()
        os.utime(path, (stamp, stamp))
        return path

    def subagent(
        self,
        agent_id: str,
        lines: list[str],
        use_id: str,
        sid: str = SID,
        cwd: str = CWD,
        **meta: Any,
    ) -> None:
        """Write a subagent's transcript and metadata beside its session's transcript."""
        folder = self.projects / transcripts.encode_project(Path(cwd)) / sid / "subagents"
        folder.mkdir(parents=True, exist_ok=True)
        described = {"agentType": "Explore", "description": "find the retry policy", "toolUseId": use_id}
        shape = {"spawnDepth": 1, "requestShape": "foreground"}
        (folder / f"agent-{agent_id}.meta.json").write_text(
            json.dumps({**described, **shape, **meta}), "utf-8"
        )
        (folder / f"agent-{agent_id}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        stamp = NOW.timestamp()
        os.utime(folder / f"agent-{agent_id}.jsonl", (stamp, stamp))

    def output(self, task_id: str, body: str, written: datetime, sid: str = SID, cwd: str = CWD) -> None:
        """Write a background command's output file, last written to at ``written``."""
        folder = self.tasks / transcripts.encode_project(Path(cwd)) / sid / "tasks"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{task_id}.output").write_text(body, encoding="utf-8")
        os.utime(folder / f"{task_id}.output", (written.timestamp(), written.timestamp()))

    def rollout(
        self,
        session_id: str,
        lines: list[str],
        started: datetime,
        cwd: str = "/tmp/work/tool-shed",
        source: str = "exec",
        written: datetime | None = None,
    ) -> None:
        """Write a Codex rollout file: its session metadata line, then the given records."""
        folder = self.codex / "sessions" / f"{started:%Y}" / f"{started:%m}" / f"{started:%d}"
        folder.mkdir(parents=True, exist_ok=True)
        meta = {"id": session_id, "cwd": cwd, "originator": "codex_exec", "source": source}
        first = codex_record("session_meta", started, **meta, cli_version="0.162.1", timestamp=iso(started))
        path = folder / f"rollout-{started:%Y-%m-%dT%H-%M-%S}-{session_id}.jsonl"
        path.write_text("\n".join([first, *lines]) + "\n", encoding="utf-8")
        stamp = (written or NOW).timestamp()
        os.utime(path, (stamp, stamp))

    def run(self, **params: Any) -> Any:
        folders = {
            "dir": str(self.projects),
            "sessions_dir": str(self.sessions),
            "tasks_dir": str(self.tasks),
            "codex_dir": str(self.codex),
        }
        return TOOL.run(TOOL.input(**folders, **params), ToolContext(cwd=self.root, log=lambda _: None))


QUESTION = "Which environment should the migration run against?"


def asking(when: datetime) -> list[str]:
    ask = call("toolu_q", "AskUserQuestion", questions=[{"question": QUESTION}])
    return [prompt(ago(minutes=9), "write the migration"), said(when, "m1", ask)]


def reading(when: datetime, text: str = "refactor the parser") -> list[str]:
    """An open turn whose last record is a pending Read call."""
    read = call("toolu_1", "Read", file_path="/tmp/x.py")
    return [prompt(when - timedelta(minutes=1), text), said(when, "m1", read)]


def finished(when: datetime, text: str = "tidy the changelog") -> list[str]:
    """A turn that made one tool call and then ended."""
    read = call("toolu_1", "Read", file_path="/tmp/x.py")
    return [
        prompt(when - timedelta(minutes=10), text),
        said(when - timedelta(minutes=9), "m1", read),
        result(when - timedelta(minutes=8), "toolu_1"),
        said(when, "m2", words("Done."), stop="end_turn"),
    ]


def stamp(when: datetime) -> int:
    """A time the way the registry stores it: epoch milliseconds."""
    return int(when.timestamp() * 1000)


def spawning(
    when: datetime, use_id: str = "toolu_agent", description: str = "find the retry policy"
) -> list[str]:
    """An open turn whose last record is a pending Agent call."""
    spawn = call(use_id, "Agent", description=description, subagent_type="Explore", prompt="look around")
    return [prompt(when - timedelta(minutes=1), "investigate the flaky test"), said(when, "m1", spawn)]


def wrapper(command: str, background: bool = True) -> str:
    """The command line of the shell Claude Code wraps a Bash tool call in, as `ps` prints it."""
    quoted = command.replace("'", "'\"'\"'").replace("\n", "\\012")
    setup = "/bin/zsh -c source /x/snapshot.sh 2>/dev/null || true"
    stdin = " < /dev/null" if background else ""  # only a background command is cut off from stdin
    return f"{setup} && eval '{quoted}'{stdin} && pwd -P >| /tmp/c-cwd"


def launching(when: datetime, task_id: str = "b123", command: str = "npm run build") -> list[str]:
    """A Bash call started in the background, and the result that names its task."""
    start = call(f"toolu_{task_id}", "Bash", command=command, description="Build", run_in_background=True)
    started = result(
        when,
        f"toolu_{task_id}",
        f"Command running in background with ID: {task_id}",
        backgroundTaskId=task_id,
    )
    return [said(when, f"m-{task_id}", start), started]


def notice(when: datetime, task_id: str, status: str) -> str:
    tags = f"<task-id>{task_id}</task-id>\n<status>{status}</status>\n<summary>over</summary>"
    body = f"<task-notification>\n{tags}"
    record = {"type": "queue-operation", "operation": "enqueue", "timestamp": iso(when), "content": body}
    return json.dumps(record)


def building(home: Home, *more: str, status: str = "busy", cpu: float = 0.0, alive: bool = True) -> None:
    """A session that started `npm run build` in the background 31 minutes ago and has kept working since."""
    home.session(pid=4412, status=status)
    home.transcript([prompt(ago(minutes=40), "run the production build"), *launching(ago(minutes=31)), *more])
    if alive:
        home.process(7001, ago(minutes=31), command=wrapper("npm run build"), ppid=4412, cpu=0.0)
        home.process(7002, ago(minutes=31), command="node build.js", ppid=7001, cpu=cpu)


BUSY = reading(ago(seconds=10))[1]


def only_command(home: Home, **params: Any) -> Any:
    [session] = home.run(**params).sessions
    [command] = session.commands
    return command


def codex_record(kind: str, when: datetime, **payload: Any) -> str:
    return json.dumps({"timestamp": iso(when), "type": kind, "payload": payload})


def codex_turn(when: datetime, text: str = "review the stall rule", model: str = "gpt-6.1-sol") -> list[str]:
    """The records that open a Codex turn: its start, its context with the model, and the user's message."""
    asked = [{"type": "input_text", "text": text}]
    return [
        codex_record("event_msg", when, type="task_started", turn_id="t1", started_at=int(when.timestamp())),
        codex_record("turn_context", when, turn_id="t1", model=model),
        codex_record("response_item", when, type="message", role="user", content=asked),
    ]


def codex_call(when: datetime, call_id: str = "call_1", script: str = "git log --oneline") -> str:
    return codex_record(
        "response_item", when, type="custom_tool_call", call_id=call_id, name="exec", input=script
    )


def codex_output(when: datetime, call_id: str = "call_1") -> str:
    return codex_record("response_item", when, type="custom_tool_call_output", call_id=call_id, output=[])


def codex_done(when: datetime) -> str:
    return codex_record(
        "event_msg", when, type="task_complete", turn_id="t1", completed_at=int(when.timestamp())
    )
