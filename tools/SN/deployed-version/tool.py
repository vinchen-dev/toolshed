"""Which PMS release is running on QAT and PROD, read from the server's start-up log lines.

PMS2.0_PH logs `[Process N] Initializing <script> (version: <releaseVersion>)` and
`[Process N] NODE_VERSION=v22.x, NODE_MODULE_VERSION=127` every time a process starts. The lines are pulled
through the sls-logs tool (credentials in ~/.config/aliyun-sls.env), the newest line per container wins,
and the result is set beside the local checkout (branch, HEAD, latest tag, package.json version).
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from shed import Tool, ToolContext, ToolError, sh
from shed.registry import load_tool

Env = Literal["qat", "prod"]
# Same SLS project/logstore pairs as bdo-incidents; kept here because that tool sits in another group.
ENVS: dict[Env, tuple[str, str]] = {
    "qat": ("platform-qat-aliyun-logs", "platform-pms-qat"),
    "prod": ("platform-prod-aliyun-logs", "platform-pms-prod"),
}
# The NODE_VERSION line has no "Initializing", so it is OR-ed in to learn the runtime as well.
QUERY = '("Initializing" and "version:") or "NODE_VERSION"'
_INIT_RE = re.compile(r"Initializing (\S+) \(version: ([^)]+)\)")
_NODE_RE = re.compile(r"NODE_VERSION=(v?\d[\w.]*)")
_HEX_RE = re.compile(r"[0-9a-f]{8,}")


class Input(BaseModel):
    env: Literal["qat", "prod", "both"] = Field("both", description="Environment to check")
    hours: int = Field(72, ge=1, description="Look back this many hours for process start-up lines")
    repo: str = Field(
        ".", description="Local PMS checkout to compare with; the comparison is skipped outside a git repo"
    )
    limit: int = Field(500, ge=1, description="Maximum log lines pulled per environment")


class VersionGroup(BaseModel):
    version: str
    containers: int
    last_seen: str


class EnvReport(BaseModel):
    env: Env
    version: str | None
    """The version of the container that started most recently; None when no start-up line was found."""
    since: str | None
    containers: int
    node_version: str | None
    versions: list[VersionGroup]
    """Every version still live, newest start first."""
    mixed: bool
    scripts: list[str]
    matches_head: bool | None
    """True when the deployed version carries the local HEAD's sha; None without a local checkout."""


class Local(BaseModel):
    repo: str
    branch: str
    head: str
    sha: str
    latest_tag: str | None
    package_version: str | None


class DeployedVersions(BaseModel):
    window_hours: int
    envs: list[EnvReport]
    local: Local | None
    notes: list[str]


class _Start(BaseModel):
    time: str
    script: str
    version: str


def _sls() -> Any:
    """The sls-logs tool; a seam so tests can hand back recorded pulls."""
    return load_tool("sls-logs")


def fetch_lines(env: Env, hours: int, limit: int, ctx: ToolContext) -> tuple[list[Any], bool]:
    """One environment's start-up lines (sls-logs LogLine objects) and whether the pull hit the limit."""
    project, logstore = ENVS[env]
    sls = _sls()
    params = sls.input(project=project, logstore=logstore, minutes=hours * 60, limit=limit, query=QUERY)
    try:
        result = sls.run(params, ctx)
    except ToolError as exc:
        raise ToolError(f"{env}: {exc}") from exc
    return list(result.lines), bool(result.truncated)


def summarise(env: Env, lines: list[Any], sha: str | None) -> EnvReport:
    """Keep the newest start-up line per container and group the containers by version."""
    starts: dict[str, _Start] = {}
    nodes: dict[str, tuple[str, str]] = {}
    scripts: set[str] = set()
    # One SLS entry can hold a whole pm2 burst, so every match in it counts, not just the first.
    for line in lines:
        for init in _INIT_RE.finditer(line.content):
            scripts.add(init.group(1))
            current = starts.get(line.container)
            if current is None or line.time >= current.time:
                starts[line.container] = _Start(time=line.time, script=init.group(1), version=init.group(2))
        for node in _NODE_RE.finditer(line.content):
            if line.container not in nodes or line.time >= nodes[line.container][0]:
                nodes[line.container] = (line.time, node.group(1))

    by_version: dict[str, list[_Start]] = defaultdict(list)
    for start in starts.values():
        by_version[start.version].append(start)
    groups = sorted(
        (
            VersionGroup(version=version, containers=len(items), last_seen=max(s.time for s in items))
            for version, items in by_version.items()
        ),
        key=lambda g: g.last_seen,
        reverse=True,
    )
    newest = max(starts.items(), key=lambda item: item[1].time) if starts else None
    node_version = None
    if newest and newest[0] in nodes:
        node_version = nodes[newest[0]][1]
    elif nodes:
        node_version = max(nodes.values())[1]
    version = newest[1].version if newest else None
    matches = None
    if version is not None and sha:
        # All-digit runs are the date stamp (20220404), not a sha prefix.
        matches = any(sha.startswith(run) for run in _HEX_RE.findall(version) if not run.isdigit())
    return EnvReport(
        env=env,
        version=version,
        since=newest[1].time if newest else None,
        containers=len(starts),
        node_version=node_version,
        versions=groups,
        mixed=len(groups) > 1,
        scripts=sorted(scripts),
        matches_head=matches,
    )


def _package_version(root: Path) -> str | None:
    for candidate in (root / "package.json", root / "server" / "package.json"):
        if candidate.is_file():
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
            except ValueError:
                continue
            if isinstance(data, dict) and isinstance(data.get("version"), str):
                return data["version"]
    return None


def local_checkout(path: Path) -> Local | None:
    """Branch, HEAD, latest tag and package.json version of the checkout at ``path``; None outside git."""
    top = sh("git", ["rev-parse", "--show-toplevel"], cwd=path, allow_failure=True)
    if top.code != 0:
        return None
    root = Path(top.stdout.strip())
    sha = sh("git", ["rev-parse", "HEAD"], cwd=root, allow_failure=True).stdout.strip()
    if not sha:
        return None  # an unborn repository has nothing to compare
    branch = sh("git", ["branch", "--show-current"], cwd=root).stdout.strip() or "HEAD"
    tag = sh("git", ["describe", "--tags", "--abbrev=0"], cwd=root, allow_failure=True)
    return Local(
        repo=str(root),
        branch=branch,
        head=sha[:7],
        sha=sha,
        latest_tag=tag.stdout.strip() if tag.code == 0 and tag.stdout.strip() else None,
        package_version=_package_version(root),
    )


def run(params: Input, ctx: ToolContext) -> DeployedVersions:
    repo = (ctx.cwd / Path(params.repo).expanduser()).resolve()
    local = local_checkout(repo) if repo.is_dir() else None
    notes: list[str] = []
    if local is None:
        notes.append(f"{repo} is not a git repository: no local comparison")
    envs: list[Env] = ["qat", "prod"] if params.env == "both" else [params.env]
    reports: list[EnvReport] = []
    for env in envs:
        ctx.log(f"# {env}: pulling start-up lines from the last {params.hours}h")
        lines, truncated = fetch_lines(env, params.hours, params.limit, ctx)
        report = summarise(env, lines, local.sha if local else None)
        reports.append(report)
        if report.version is None:
            notes.append(f"no start-up lines in the last {params.hours}h on {env}: widen --hours")
        if truncated:
            notes.append(
                f"{env}: the pull stopped at --limit {params.limit}; the newest starts may be missing"
            )
    return DeployedVersions(window_hours=params.hours, envs=reports, local=local, notes=notes)


def render(result: DeployedVersions) -> str:
    out: list[str] = []
    for env in result.envs:
        label = env.env.upper().ljust(5)
        if env.version is None:
            out.append(f"{label} (no start-up lines in the last {result.window_hours}h)")
            continue
        node = f"  node {env.node_version}" if env.node_version else ""
        match = "  (= local HEAD)" if env.matches_head else ""
        plural = "s" if env.containers != 1 else ""
        out.append(
            f"{label} {env.version}  since {env.since}  {env.containers} container{plural}{node}{match}"
        )
        if env.mixed:
            parts = ", ".join(f"{g.version} x{g.containers} (last {g.last_seen})" for g in env.versions)
            out.append(f"      MIXED: {parts}")
    if result.local:
        tag = f"  tag {result.local.latest_tag}" if result.local.latest_tag else "  no tag"
        pkg = f"  package.json {result.local.package_version}" if result.local.package_version else ""
        out.append(f"local {result.local.branch} @ {result.local.head}{tag}{pkg}")
    out.extend(f"note: {note}" for note in result.notes)
    return "\n".join(out)


TOOL = Tool(
    name="deployed-version",
    description=(
        "Report which PMS release is running on QAT and PROD from the servers' start-up log lines in SLS "
        "(version, since when, how many containers, Node version, mixed versions mid-rollout) and compare "
        "it with the local checkout. Use it before a check-in or a release to know what is live instead "
        "of guessing from branch names."
    ),
    input=Input,
    run=run,
    render=render,
    examples=(
        "shed deployed-version",
        "shed deployed-version --env prod --hours 168 --repo ~/Desktop/Projects/PMS2.0_PH",
        "shed deployed-version --env qat --json",
    ),
    version="0.1.0",
)
