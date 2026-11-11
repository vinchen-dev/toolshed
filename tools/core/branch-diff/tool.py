"""Commits and files between two refs, the raw material for PR descriptions and release notes."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from pydantic import BaseModel, Field

from shed import Tool, ToolContext, ToolError, sh

BASE_CANDIDATES = ("origin/HEAD", "origin/main", "origin/master", "main", "master")
_LOG_FORMAT = "%H%x1f%h%x1f%an%x1f%ad%x1f%s"


class Input(BaseModel):
    path: str = Field(".", description="Any directory inside the repository")
    base: str | None = Field(
        None,
        description="Base ref; default: the branch origin/HEAD points at, else origin/main, origin/master, "
        "main or master, whichever exists first",
    )
    head: str = Field("HEAD", description="Head ref")
    commits: int = Field(50, ge=0, description="Maximum commits listed; the total is reported either way")
    files: bool = Field(True, description="List the changed files")
    stat: bool = Field(True, description="Count added and deleted lines per file")


class Commit(BaseModel):
    hash: str
    short: str
    author: str
    date: str
    subject: str


class FileChange(BaseModel):
    path: str
    status: str
    """A, M, D, R (renamed), C (copied) or T (type changed)."""
    added: int
    deleted: int
    renamed_from: str | None = None


class Totals(BaseModel):
    files: int
    added: int
    deleted: int


class Author(BaseModel):
    name: str
    commits: int


class BranchDiff(BaseModel):
    root: str
    base: str
    head: str
    merge_base: str
    ahead: int
    """Commits on head that base does not have."""
    behind: int
    """Commits on base that head does not have."""
    commits: list[Commit]
    commits_total: int
    commits_truncated: bool
    files: list[FileChange]
    totals: Totals
    authors: list[Author]


def _exists(root: Path, ref: str) -> bool:
    return (
        sh("git", ["rev-parse", "--verify", "-q", f"{ref}^{{commit}}"], cwd=root, allow_failure=True).code
        == 0
    )


def pick_base(root: Path) -> str:
    """The first of origin/HEAD's branch, origin/main, origin/master, main, master that resolves."""
    for candidate in BASE_CANDIDATES:
        if candidate == "origin/HEAD":
            target = sh(
                "git",
                ["symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD"],
                cwd=root,
                allow_failure=True,
            )
            ref = target.stdout.strip()
            if target.code == 0 and ref and _exists(root, ref):
                return ref
            continue
        if _exists(root, candidate):
            return candidate
    raise ToolError(f"cannot pick a base branch (tried {', '.join(BASE_CANDIDATES)}); pass --base")


def parse_log(raw: str) -> list[Commit]:
    commits: list[Commit] = []
    for line in raw.splitlines():
        if not line:
            continue
        hash_, short, author, date, subject = (line.split("\x1f", 4) + [""] * 5)[:5]
        commits.append(Commit(hash=hash_, short=short, author=author, date=date, subject=subject))
    return commits


def parse_name_status(raw: str) -> list[FileChange]:
    """Parse ``git diff --name-status -z``: status, path, and a second path for renames and copies."""
    tokens = iter(t for t in raw.split("\0"))
    changes: list[FileChange] = []
    for status in tokens:
        if not status:
            continue
        path = next(tokens, "")
        if status[0] in "RC":
            new = next(tokens, "")
            changes.append(FileChange(path=new, status=status[0], added=0, deleted=0, renamed_from=path))
        else:
            changes.append(FileChange(path=path, status=status[0], added=0, deleted=0))
    return changes


def parse_numstat(raw: str) -> dict[str, tuple[int, int]]:
    """Parse ``git diff --numstat -z``; a rename leaves the path empty and follows with old and new paths."""
    tokens = raw.split("\0")
    counts: dict[str, tuple[int, int]] = {}
    i = 0
    while i < len(tokens):
        entry = tokens[i]
        i += 1
        if not entry:
            continue
        added, deleted, path = [*entry.split("\t", 2), "", ""][:3]
        if path == "":  # rename: two more NUL-terminated fields follow
            if i + 1 < len(tokens):
                path = tokens[i + 1]
            i += 2
        pair = (int(added) if added.isdigit() else 0, int(deleted) if deleted.isdigit() else 0)
        counts[path] = pair
    return counts


def run(params: Input, ctx: ToolContext) -> BranchDiff:
    cwd = (ctx.cwd / Path(params.path).expanduser()).resolve()
    if not cwd.is_dir():
        raise ToolError(f"{cwd} is not a directory")
    top = sh("git", ["rev-parse", "--show-toplevel"], cwd=cwd, allow_failure=True)
    if top.code != 0:
        raise ToolError(f"{cwd} is not inside a git repository")
    root = Path(top.stdout.strip())

    base = params.base or pick_base(root)
    for name, ref in (("base", base), ("head", params.head)):
        if not _exists(root, ref):
            raise ToolError(f"{name} ref '{ref}' does not resolve to a commit")
    merge_base = sh("git", ["merge-base", base, params.head], cwd=root, allow_failure=True)
    if merge_base.code != 0:
        raise ToolError(f"{base} and {params.head} share no history")
    merge_short = sh("git", ["rev-parse", "--short", merge_base.stdout.strip()], cwd=root).stdout.strip()
    counts = sh(
        "git", ["rev-list", "--left-right", "--count", f"{base}...{params.head}"], cwd=root
    ).stdout.split()
    behind, ahead = (int(counts[0]), int(counts[1])) if len(counts) == 2 else (0, 0)
    ctx.log(f"# {base}...{params.head}: {ahead} ahead, {behind} behind")

    log = sh(
        "git",
        ["log", "--no-merges", f"--format={_LOG_FORMAT}", "--date=short", f"{base}..{params.head}"],
        cwd=root,
    )
    commits = parse_log(log.stdout)
    authors = Counter(c.author for c in commits)

    changes: list[FileChange] = []
    if params.files or params.stat:
        changes = parse_name_status(
            sh("git", ["diff", "--name-status", "-z", "-M", f"{base}...{params.head}"], cwd=root).stdout
        )
    totals = Totals(files=len(changes), added=0, deleted=0)
    if params.stat:
        numbers = parse_numstat(
            sh("git", ["diff", "--numstat", "-z", "-M", f"{base}...{params.head}"], cwd=root).stdout
        )
        for change in changes:
            change.added, change.deleted = numbers.get(change.path, (0, 0))
        totals.added = sum(c.added for c in changes)
        totals.deleted = sum(c.deleted for c in changes)

    return BranchDiff(
        root=str(root),
        base=base,
        head=params.head,
        merge_base=merge_short,
        ahead=ahead,
        behind=behind,
        commits=commits[: params.commits],
        commits_total=len(commits),
        commits_truncated=len(commits) > params.commits,
        files=changes if params.files else [],
        totals=totals,
        authors=[Author(name=n, commits=c) for n, c in sorted(authors.items(), key=lambda i: (-i[1], i[0]))],
    )


def render(diff: BranchDiff) -> str:
    out = [
        f"{diff.base}...{diff.head}  (merge base {diff.merge_base})  {diff.ahead} ahead, {diff.behind} behind"
    ]
    if diff.commits_total == 0:
        out.append("no commits on head that base lacks")
    out.extend(f"  {c.short} {c.date} {c.author}  {c.subject}" for c in diff.commits)
    if diff.commits_truncated:
        out.append(f"  ... {diff.commits_total - len(diff.commits)} more commits")
    if diff.files or diff.totals.files:
        out.append(f"files: {diff.totals.files} changed, +{diff.totals.added} -{diff.totals.deleted}")
    for f in diff.files:
        stat = f"  +{f.added} -{f.deleted}" if (f.added or f.deleted) else ""
        name = f"{f.renamed_from} -> {f.path}" if f.renamed_from else f.path
        out.append(f"  {f.status}  {name}{stat}")
    if diff.authors:
        out.append("authors: " + ", ".join(f"{a.name} {a.commits}" for a in diff.authors))
    return "\n".join(out)


TOOL = Tool(
    name="branch-diff",
    description=(
        "Summarise what a branch adds over its base: ahead/behind counts, the non-merge commits, the changed "
        "files with line counts, and who authored them. Use it to write a PR description or release notes, "
        "or to see what a branch contains before merging or deploying it."
    ),
    input=Input,
    run=run,
    render=render,
    examples=(
        "shed branch-diff",
        "shed branch-diff --base origin/master --head release-4.0.120",
        "shed branch-diff --path ~/Desktop/Projects/PMS2.0_PH --base v4.0.96 --head v4.0.98 --json",
    ),
    version="0.1.0",
)
