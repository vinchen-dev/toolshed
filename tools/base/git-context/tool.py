"""Summarise a git repository so an agent can orient itself before making changes."""

from __future__ import annotations

from pydantic import BaseModel, Field

from shed import Tool, ToolContext, ToolError, sh


class Input(BaseModel):
    path: str = Field(".", description="Any directory inside the repository")
    commits: int = Field(10, ge=0, le=100, description="How many recent commits to include")


class Change(BaseModel):
    path: str
    status: str
    staged: bool
    renamed_from: str | None = None


class Commit(BaseModel):
    hash: str
    short: str
    author: str
    date: str
    subject: str


class Context(BaseModel):
    root: str
    branch: str
    detached: bool
    upstream: str | None
    ahead: int | None
    behind: int | None
    changes: list[Change]
    commits: list[Commit]


def _parse_status(raw: str) -> list[Change]:
    """Parse ``git status --porcelain=v1 -z``. Renames carry the original path in a second NUL field."""
    fields = iter(f for f in raw.split("\0") if f)
    changes: list[Change] = []
    for entry in fields:
        if len(entry) < 3:
            continue
        x, y, path = entry[0], entry[1], entry[3:]
        change = Change(path=path, status=f"{x}{y}", staged=x not in " ?")
        if "R" in (x, y) or "C" in (x, y):
            change.renamed_from = next(fields, None)
        changes.append(change)
    return changes


def _parse_log(raw: str) -> list[Commit]:
    commits: list[Commit] = []
    for line in raw.splitlines():
        if not line:
            continue
        hash_, short, author, date, subject = (line.split("\x1f", 4) + [""] * 5)[:5]
        commits.append(Commit(hash=hash_, short=short, author=author, date=date, subject=subject))
    return commits


def run(params: Input, ctx: ToolContext) -> Context:
    cwd = (ctx.cwd / params.path).resolve()
    if not cwd.is_dir():
        raise ToolError(f"{cwd} is not a directory")
    top = sh("git", ["rev-parse", "--show-toplevel"], cwd=cwd, allow_failure=True)
    if top.code != 0:
        raise ToolError(f"{cwd} is not inside a git repository")

    # `branch --show-current` works on an unborn branch; it is empty when HEAD is detached.
    branch = sh("git", ["branch", "--show-current"], cwd=cwd).stdout.strip()
    detached = False
    if not branch:
        head = sh("git", ["rev-parse", "--short", "HEAD"], cwd=cwd, allow_failure=True)
        detached = head.code == 0
        branch = head.stdout.strip() if detached else "HEAD"

    upstream_result = sh("git", ["rev-parse", "--abbrev-ref", "@{upstream}"], cwd=cwd, allow_failure=True)
    upstream = upstream_result.stdout.strip() if upstream_result.code == 0 else None
    ahead = behind = None
    if upstream:
        counts = sh(
            "git", ["rev-list", "--left-right", "--count", "@{upstream}...HEAD"], cwd=cwd
        ).stdout.split()
        behind, ahead = int(counts[0]), int(counts[1])

    changes = _parse_status(sh("git", ["status", "--porcelain=v1", "-z"], cwd=cwd).stdout)
    log_format = "--format=%H%x1f%h%x1f%an%x1f%aI%x1f%s"
    log = sh("git", ["log", f"-n{params.commits}", log_format], cwd=cwd, allow_failure=True)
    commits = _parse_log(log.stdout) if log.code == 0 else []  # fails on a repo with no commits yet

    return Context(
        root=top.stdout.strip(),
        branch=branch,
        detached=detached,
        upstream=upstream,
        ahead=ahead,
        behind=behind,
        changes=changes,
        commits=commits,
    )


def render(result: Context) -> str:
    head = f"branch: {result.branch}{' (detached)' if result.detached else ''}"
    if result.upstream:
        head += f" -> {result.upstream} (ahead {result.ahead}, behind {result.behind})"
    else:
        head += " (no upstream)"
    lines = [result.root, head, ""]
    lines.append(f"changes ({len(result.changes)}):" if result.changes else "working tree clean")
    for change in result.changes:
        origin = f"{change.renamed_from} -> " if change.renamed_from else ""
        lines.append(f"  {change.status} {origin}{change.path}")
    if result.commits:
        lines += ["", f"recent commits ({len(result.commits)}):"]
        lines += [f"  {c.short} {c.date[:10]} {c.author}: {c.subject}" for c in result.commits]
    return "\n".join(lines)


TOOL = Tool(
    name="git-context",
    description=(
        "Summarise a git repo (branch, upstream, ahead/behind, changed files, recent commits) "
        "so an agent can orient itself before making changes."
    ),
    input=Input,
    run=run,
    render=render,
    examples=("shed git-context", "shed git-context --path ../other-repo --commits 5 --json"),
)
