"""Token and tool usage per Claude Code session, read from the transcripts under ~/.claude/projects.

One row per session: when it ran, how many prompts, which tools were called how often, and the tokens
consumed (input, cache reads, cache writes, output). Usage is counted once per assistant message even when
the transcript holds it as several streamed chunks.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field

from shed import Tool, ToolContext, ToolError

from . import transcripts as t
from .transcripts import Session


class Input(BaseModel):
    project: str | None = Field(
        None,
        description="Project folder: an absolute path, or the encoded name under ~/.claude/projects "
        "(default: the current folder)",
    )
    all_projects: bool = Field(False, description="Scan every project folder instead of one")
    dir: str | None = Field(
        None, description="Transcripts root (default $CLAUDE_PROJECTS_DIR or ~/.claude/projects)"
    )
    since: str | None = Field(
        None,
        description="Skip sessions that ended before this: Manila 'YYYY-MM-DD[ HH:MM]' or 7d / 12h / 30m",
    )
    session: str | None = Field(None, description="Only sessions whose id starts with this")
    limit: int = Field(20, ge=1, description="Sessions to list, newest first")
    top_tools: int = Field(5, ge=0, description="Tool names kept per session, most called first")


class ToolCount(BaseModel):
    name: str
    calls: int


class Tokens(BaseModel):
    input: int = 0
    cache_read: int = 0
    cache_create: int = 0
    output: int = 0
    total: int = 0


class SessionStats(BaseModel):
    id: str
    project: str
    cwd: str
    started: str
    ended: str
    duration_min: float
    models: list[str]
    version: str
    user_turns: int
    assistant_messages: int
    tool_calls: int
    tools: list[ToolCount]
    tokens: Tokens
    first_prompt: str


class Totals(BaseModel):
    sessions: int
    user_turns: int
    tool_calls: int
    tokens: Tokens


class Report(BaseModel):
    root: str
    projects: list[str]
    since: str | None
    sessions: list[SessionStats]
    totals: Totals


def _stats(s: Session, top: int) -> SessionStats:
    calls = s.tool_calls
    return SessionStats(
        id=s.id,
        project=s.project,
        cwd=s.cwd,
        started=t.manila(s.started),
        ended=t.manila(s.ended),
        duration_min=s.duration_min,
        models=list(s.models),
        version=s.version,
        user_turns=len(s.prompts),
        assistant_messages=len(s.usage),
        tool_calls=sum(calls.values()),
        tools=[ToolCount(name=n, calls=c) for n, c in calls.most_common(top)],
        tokens=Tokens(**s.tokens),
        first_prompt=s.first_prompt,
    )


def run(params: Input, ctx: ToolContext) -> Report:
    root = t.projects_root(params.dir, ctx.cwd)
    projects = (
        t.list_projects(root) if params.all_projects else [t.resolve_project(params.project, ctx.cwd, root)]
    )
    if not params.all_projects and not (root / projects[0]).is_dir():
        raise ToolError(f"no transcripts for {projects[0]} under {root}")
    since = t.parse_since(params.since)
    rows: list[SessionStats] = []
    for project, path in t.session_files(root, projects):
        if len(rows) >= params.limit:
            break
        if params.session and not path.stem.startswith(params.session):
            continue
        if since and datetime.fromtimestamp(path.stat().st_mtime, UTC) < since:
            continue  # the file was last written before the cut-off, so the session ended before it too
        session = t.read_session(path, project)
        if since and (session.ended is None or session.ended < since):
            continue
        rows.append(_stats(session, params.top_tools))
    ctx.log(f"{len(rows)} session(s) from {len(projects)} project folder(s) under {root}")
    totals = Totals(
        sessions=len(rows),
        user_turns=sum(r.user_turns for r in rows),
        tool_calls=sum(r.tool_calls for r in rows),
        tokens=Tokens(
            input=sum(r.tokens.input for r in rows),
            cache_read=sum(r.tokens.cache_read for r in rows),
            cache_create=sum(r.tokens.cache_create for r in rows),
            output=sum(r.tokens.output for r in rows),
            total=sum(r.tokens.total for r in rows),
        ),
    )
    return Report(
        root=str(root),
        projects=projects,
        since=t.manila(since) if since else None,
        sessions=rows,
        totals=totals,
    )


def _k(n: int) -> str:
    return f"{n / 1_000_000:.1f}M" if n >= 1_000_000 else f"{n / 1000:.0f}k" if n >= 1000 else str(n)


def render(report: Report) -> str:
    lines = [
        f"{'session':8}  {'started':16}  {'min':>6}  {'turns':>5}  {'tools':>5}  {'tokens':>7}  first prompt"
    ]
    for s in report.sessions:
        lines.append(
            f"{s.id[:8]:8}  {s.started[:16]:16}  {s.duration_min:6.1f}  {s.user_turns:5d}  "
            f"{s.tool_calls:5d}  {_k(s.tokens.total):>7}  {s.first_prompt[:60]}"
        )
    t = report.totals
    lines.append(
        f"{t.sessions} session(s), {t.user_turns} turns, {t.tool_calls} tool calls, "
        f"{_k(t.tokens.total)} tokens (in {_k(t.tokens.input)}, cache read {_k(t.tokens.cache_read)}, "
        f"cache write {_k(t.tokens.cache_create)}, out {_k(t.tokens.output)})"
    )
    return "\n".join(lines) + "\n"


TOOL = Tool(
    name="session-stats",
    description=(
        "Token and tool usage per Claude Code session, from the transcripts under ~/.claude/projects: "
        "prompts, tool calls by name and input/cache/output tokens, newest first. Use it to see where "
        "context goes in a project or across all projects; use transcript-digest to read one session."
    ),
    input=Input,
    run=run,
    render=render,
    examples=(
        "shed session-stats --limit 10",
        "shed session-stats --all-projects --since 7d --json",
        "shed session-stats --project ~/Desktop/Projects/PMS2.0_PH --top-tools 8",
    ),
    version="0.1.0",
)
