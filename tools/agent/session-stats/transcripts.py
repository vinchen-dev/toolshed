"""Read Claude Code transcripts (~/.claude/projects/<encoded cwd>/<session>.jsonl) into session records.

Shared by session-stats and transcript-digest, which reach it through the session-stats tool package.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from shed import ToolError
from shed.lib.manila import MANILA, parse_manila

DEFAULT_ROOT = Path.home() / ".claude" / "projects"
PATH_KEYS = ("file_path", "path", "notebook_path")
_COMMAND_NAME = re.compile(r"<command-name>([^<]*)</command-name>")
_COMMAND_MESSAGE = re.compile(r"<command-message>([^<]*)</command-message>")
_DURATION = re.compile(r"^(\d+)([dhm])$")
INJECTED_PREFIXES = ("<task-notification>", "<local-command-stdout>", "<local-command-stderr>")
SYNTHETIC_MODEL = "<synthetic>"


@dataclass(slots=True)
class Prompt:
    time: datetime
    text: str


@dataclass(slots=True)
class Reply:
    time: datetime
    message_id: str
    texts: list[str] = field(default_factory=list)
    thinking: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ToolUse:
    time: datetime
    id: str
    name: str
    input: dict[str, Any]


@dataclass(slots=True)
class ToolResult:
    time: datetime
    tool_use_id: str
    is_error: bool
    text: str


@dataclass(slots=True)
class Session:
    id: str
    project: str
    path: Path
    cwd: str = ""
    version: str = ""
    git_branch: str = ""
    started: datetime | None = None
    ended: datetime | None = None
    models: list[str] = field(default_factory=list)
    prompts: list[Prompt] = field(default_factory=list)
    replies: list[Reply] = field(default_factory=list)
    tool_uses: list[ToolUse] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    usage: dict[str, dict[str, int]] = field(default_factory=dict)
    """Token usage per assistant message id; the last streamed chunk of a message wins."""

    @property
    def duration_min(self) -> float:
        if self.started is None or self.ended is None:
            return 0.0
        return round((self.ended - self.started).total_seconds() / 60, 1)

    @property
    def tool_calls(self) -> Counter[str]:
        return Counter(use.name for use in self.tool_uses)

    @property
    def tokens(self) -> dict[str, int]:
        totals = {"input": 0, "cache_read": 0, "cache_create": 0, "output": 0}
        for usage in self.usage.values():
            totals["input"] += int(usage.get("input_tokens") or 0)
            totals["cache_read"] += int(usage.get("cache_read_input_tokens") or 0)
            totals["cache_create"] += int(usage.get("cache_creation_input_tokens") or 0)
            totals["output"] += int(usage.get("output_tokens") or 0)
        totals["total"] = sum(totals.values())
        return totals

    @property
    def first_prompt(self) -> str:
        return one_line(self.prompts[0].text, 120) if self.prompts else ""


def projects_root(override: str | None, cwd: Path) -> Path:
    """The transcripts folder: --dir, else $CLAUDE_PROJECTS_DIR, else ~/.claude/projects."""
    if override:
        return (cwd / Path(override).expanduser()).resolve()
    env = os.environ.get("CLAUDE_PROJECTS_DIR")
    return Path(env).expanduser() if env else DEFAULT_ROOT


def encode_project(path: Path) -> str:
    """Claude Code names a project folder after its absolute path with every non-alphanumeric char as '-'."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def resolve_project(project: str | None, cwd: Path, root: Path) -> str:
    """The encoded folder name for a path or an already encoded name (default: the current folder)."""
    if project and project.startswith("-") and (root / project).is_dir():
        return project
    target = (cwd / Path(project).expanduser()).resolve() if project else cwd.resolve()
    return encode_project(target)


def parse_since(text: str | None, now: datetime | None = None) -> datetime | None:
    """A Manila date/time, or a duration like 7d / 12h / 30m back from now, as an aware datetime."""
    if not text:
        return None
    now = now or datetime.now(UTC)
    match = _DURATION.match(text.strip())
    if match:
        amount, unit = int(match.group(1)), match.group(2)
        delta = {"d": timedelta(days=amount), "h": timedelta(hours=amount), "m": timedelta(minutes=amount)}
        return now - delta[unit]
    value = text.strip()
    try:
        return parse_manila(value if " " in value else f"{value} 00:00")
    except ValueError as exc:
        raise ToolError(f"bad --since '{text}': use YYYY-MM-DD[ HH:MM] (Manila) or 7d / 12h / 30m") from exc


def manila(moment: datetime | None) -> str:
    return moment.astimezone(MANILA).strftime("%Y-%m-%d %H:%M:%S") if moment else ""


def one_line(text: str, limit: int) -> str:
    """Whitespace collapsed to single spaces and cut to ``limit`` characters with an ellipsis."""
    flat = " ".join(text.split())
    return flat if limit <= 0 or len(flat) <= limit else flat[: max(limit - 1, 1)] + "…"


def _timestamp(record: dict[str, Any]) -> datetime | None:
    raw = record.get("timestamp")
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def _block_text(content: Any) -> str:
    """The text in a content value: a string, or the text blocks of a list, joined."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [str(b.get("text") or "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        return "\n".join(p for p in parts if p)
    return ""


def prompt_text(text: str) -> str:
    """A slash command keeps its name; anything else is returned as typed."""
    if text.lstrip().startswith("<command-"):
        match = _COMMAND_NAME.search(text) or _COMMAND_MESSAGE.search(text)
        return match.group(1).strip() if match else one_line(text, 80)
    return text


def _user_line(session: Session, record: dict[str, Any], when: datetime) -> None:
    content = (record.get("message") or {}).get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                session.tool_results.append(
                    ToolResult(
                        when,
                        str(block.get("tool_use_id") or ""),
                        bool(block.get("is_error")),
                        _block_text(block.get("content")),
                    )
                )
    if record.get("isMeta") or record.get("isSidechain"):
        return
    text = _block_text(content).strip()
    if text and not text.startswith(INJECTED_PREFIXES):  # harness notices are not prompts
        session.prompts.append(Prompt(when, prompt_text(text)))


def _assistant_line(session: Session, record: dict[str, Any], when: datetime) -> None:
    message = record.get("message") or {}
    model = message.get("model")
    if model == SYNTHETIC_MODEL:  # harness notices such as "Login expired", not the assistant
        return
    message_id = str(message.get("id") or record.get("uuid") or "")
    if isinstance(model, str) and model and model not in session.models:
        session.models.append(model)
    usage = message.get("usage")
    if isinstance(usage, dict):
        session.usage[message_id] = usage
    reply = next((r for r in reversed(session.replies) if r.message_id == message_id), None)
    if reply is None:
        reply = Reply(when, message_id)
        session.replies.append(reply)
    for block in message.get("content") or []:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text" and block.get("text"):
            reply.texts.append(str(block["text"]))
        elif kind == "thinking" and block.get("thinking"):
            reply.thinking.append(str(block["thinking"]))
        elif kind == "tool_use":
            raw_input = block.get("input")
            session.tool_uses.append(
                ToolUse(
                    when,
                    str(block.get("id") or ""),
                    str(block.get("name") or "?"),
                    raw_input if isinstance(raw_input, dict) else {},
                )
            )


def read_session(path: Path, project: str) -> Session:
    """Parse one transcript file; lines that are not JSON objects are skipped."""
    session = Session(id=path.stem, project=project, path=path)
    with path.open(encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            try:
                record = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(record, dict):
                continue
            when = _timestamp(record)
            if when is not None:
                session.started = (
                    when if session.started is None or when < session.started else session.started
                )
                session.ended = when if session.ended is None or when > session.ended else session.ended
            for key, attr in (("cwd", "cwd"), ("version", "version"), ("gitBranch", "git_branch")):
                value = record.get(key)
                if isinstance(value, str) and value and not getattr(session, attr):
                    setattr(session, attr, value)
            kind = record.get("type")
            if kind == "user":
                _user_line(session, record, when or session.ended or datetime.now(UTC))
            elif kind == "assistant":
                _assistant_line(session, record, when or session.ended or datetime.now(UTC))
    session.replies = [r for r in session.replies if r.texts or r.thinking or r.message_id in session.usage]
    return session


def session_files(root: Path, projects: list[str]) -> list[tuple[str, Path]]:
    """(project, file) for every session file of the given project folders, newest modified first."""
    found: list[tuple[str, Path]] = []
    for project in projects:
        folder = root / project
        if not folder.is_dir():
            continue
        found.extend((project, f) for f in folder.iterdir() if f.is_file() and f.suffix == ".jsonl")
    return sorted(found, key=lambda item: item[1].stat().st_mtime, reverse=True)


def list_projects(root: Path) -> list[str]:
    if not root.is_dir():
        raise ToolError(f"no Claude Code transcripts folder at {root}")
    return sorted(p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))


def tool_names_by_id(session: Session) -> dict[str, str]:
    return {use.id: use.name for use in session.tool_uses if use.id}
