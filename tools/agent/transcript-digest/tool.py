"""Condense one Claude Code session into prompts, replies, tools, files, commands and errors.

The digest is the raw material for a note: what was asked, what was answered, which files changed and what
went wrong, in order and with Manila times. Thinking blocks are left out unless asked for.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from types import ModuleType
from typing import Any

from pydantic import BaseModel, Field

from shed import Tool, ToolContext, ToolError
from shed.registry import load_tool_module


def transcripts() -> ModuleType:
    """The session-stats tool's transcript reader, shared rather than copied."""
    return importlib.import_module(f"{load_tool_module('session-stats').__package__}.transcripts")


class Input(BaseModel):
    session: str | None = Field(
        None, description="Session id or a unique prefix of it (default: the project's newest session)"
    )
    project: str | None = Field(
        None,
        description="Project folder: an absolute path, or the encoded name under ~/.claude/projects "
        "(default: the current folder)",
    )
    dir: str | None = Field(
        None, description="Transcripts root (default $CLAUDE_PROJECTS_DIR or ~/.claude/projects)"
    )
    max_chars: int = Field(
        400, ge=0, description="Characters kept per prompt, reply, command or error; 0 keeps all"
    )
    include_tools: bool = Field(True, description="Include the tool counts, files touched and commands run")
    include_thinking: bool = Field(False, description="Also include the assistant's thinking blocks")
    max_items: int = Field(100, ge=1, description="Items kept per section")


class Item(BaseModel):
    time: str
    text: str


class Command(BaseModel):
    time: str
    command: str
    description: str | None = None


class ToolFailure(BaseModel):
    time: str
    tool: str
    text: str


class Digest(BaseModel):
    session: str
    project: str
    cwd: str
    started: str
    ended: str
    duration_min: float
    models: list[str]
    prompts: list[Item]
    replies: list[Item]
    tools: dict[str, int]
    files_touched: list[str]
    commands: list[Command]
    errors: list[ToolFailure]
    thinking: list[Item]


def pick_session(root: Path, project: str, wanted: str | None) -> Path:
    """The newest session file of the project, or the one whose id starts with ``wanted``."""
    folder = root / project
    if not folder.is_dir():
        raise ToolError(f"no transcripts for {project} under {root}")
    files = transcripts().session_files(root, [project])
    if not files:
        raise ToolError(f"no sessions under {folder}")
    if not wanted:
        return files[0][1]
    hits = [path for _, path in files if path.stem.startswith(wanted)]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise ToolError(f"no session starting with {wanted!r} under {folder}")
    raise ToolError(f"{wanted!r} matches {len(hits)} sessions: " + ", ".join(p.stem[:12] for p in hits[:6]))


def _paths(value: Any) -> list[str]:
    """File paths named by a tool input: the usual path keys, plus the edits list of a multi-edit."""
    if not isinstance(value, dict):
        return []
    found = [str(value[k]) for k in transcripts().PATH_KEYS if isinstance(value.get(k), str) and value[k]]
    for edit in value.get("edits") or []:
        found.extend(_paths(edit))
    return found


def build(session: Any, params: Input) -> Digest:
    t = transcripts()
    cut = params.max_chars
    names = t.tool_names_by_id(session)
    files: list[str] = []
    commands: list[Command] = []
    for use in session.tool_uses:
        for path in _paths(use.input):
            if path not in files:
                files.append(path)
        if use.name == "Bash" and isinstance(use.input.get("command"), str):
            description = use.input.get("description")
            commands.append(
                Command(
                    time=t.manila(use.time),
                    command=t.one_line(use.input["command"], cut),
                    description=str(description) if description else None,
                )
            )
    errors = [
        ToolFailure(time=t.manila(r.time), tool=names.get(r.tool_use_id, "?"), text=t.one_line(r.text, cut))
        for r in session.tool_results
        if r.is_error
    ]
    thinking = [
        Item(time=t.manila(r.time), text=t.one_line("\n".join(r.thinking), cut))
        for r in session.replies
        if r.thinking
    ]
    n = params.max_items
    return Digest(
        session=session.id,
        project=session.project,
        cwd=session.cwd,
        started=t.manila(session.started),
        ended=t.manila(session.ended),
        duration_min=session.duration_min,
        models=list(session.models),
        prompts=[Item(time=t.manila(p.time), text=t.one_line(p.text, cut)) for p in session.prompts][:n],
        replies=[
            Item(time=t.manila(r.time), text=t.one_line("\n".join(r.texts), cut))
            for r in session.replies
            if r.texts
        ][:n],
        tools=dict(session.tool_calls.most_common()) if params.include_tools else {},
        files_touched=files[:n] if params.include_tools else [],
        commands=commands[:n] if params.include_tools else [],
        errors=errors[:n],
        thinking=thinking[:n] if params.include_thinking else [],
    )


def run(params: Input, ctx: ToolContext) -> Digest:
    t = transcripts()
    root = t.projects_root(params.dir, ctx.cwd)
    project = t.resolve_project(params.project, ctx.cwd, root)
    path = pick_session(root, project, params.session)
    ctx.log(f"reading {path}")
    return build(t.read_session(path, project), params)


def _section(title: str, rows: list[str]) -> list[str]:
    return [f"## {title}", *rows, ""] if rows else []


def render(d: Digest) -> str:
    lines = [
        f"# session {d.session[:8]} in {d.cwd or d.project}: {d.started} to {d.ended} "
        f"({d.duration_min} min, {', '.join(d.models) or 'no model'})",
        "",
    ]
    lines += _section("Prompts", [f"- {p.time[11:16]} {p.text}" for p in d.prompts])
    lines += _section("Replies", [f"- {r.time[11:16]} {r.text}" for r in d.replies])
    lines += _section("Tools", [f"- {name}: {calls}" for name, calls in d.tools.items()])
    lines += _section("Files touched", [f"- {f}" for f in d.files_touched])
    lines += _section(
        "Commands",
        [
            f"- {c.time[11:16]} {c.command}" + (f"  ({c.description})" if c.description else "")
            for c in d.commands
        ],
    )
    lines += _section("Errors", [f"- {e.time[11:16]} {e.tool}: {e.text}" for e in d.errors])
    lines += _section("Thinking", [f"- {x.time[11:16]} {x.text}" for x in d.thinking])
    return "\n".join(lines).rstrip() + "\n"


TOOL = Tool(
    name="transcript-digest",
    description=(
        "Condense one Claude Code session (newest by default) into its prompts, replies, tool counts, files "
        "touched, commands run and tool errors, with Manila times. Use it to write up a session, for "
        "example before an Obsidian audit; use session-stats to list sessions and their token use."
    ),
    input=Input,
    run=run,
    render=render,
    examples=(
        "shed transcript-digest",
        "shed transcript-digest --session 8a84e5c1 --max-chars 200",
        "shed transcript-digest --project ~/Desktop/Projects/PMS2.0_PH --include-thinking --json",
    ),
    version="0.1.0",
)
