"""`shed update`: one command that brings this machine up to date after a pull or a `uv add`.

Syncs the venv, reinstalls the global `shed` (so it sees new dependencies), installs Playwright's Chromium
when it is a dependency, links every skill, and registers every tool group found under tools/ (plus the job
tools) as its own MCP server in Claude Code at user scope. Every step is idempotent."""

from __future__ import annotations

import importlib.util
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from shutil import which

from shed.job_tools import JOBS_GROUP
from shed.lib.errors import EXIT_FAILURE, EXIT_OK
from shed.lib.shell import sh
from shed.registry import SHED_ROOT, TOOLS_DIR, list_groups
from shed.skills import CLAUDE_SKILLS_DIR, SKILLS_DIR, link_skills
from shed.version import current as shed_version

SERVER_PREFIX = "tool-shed"


@dataclass(frozen=True, slots=True)
class Step:
    label: str
    cmd: str
    args: tuple[str, ...]
    allow_failure: bool = False

    def text(self) -> str:
        return " ".join([self.cmd, *self.args])


def server_names(groups: Sequence[str]) -> dict[str, str]:
    """MCP server name per group: `tool-shed` for base, else the last folder, or the full path on a clash."""
    last = {g: g.rsplit("/", 1)[-1].lower() for g in groups}
    counts: dict[str, int] = {}
    for leaf in last.values():
        counts[leaf] = counts.get(leaf, 0) + 1
    names: dict[str, str] = {}
    for group in groups:
        if group == "base":
            names[group] = SERVER_PREFIX
            continue
        suffix = last[group] if counts[last[group]] == 1 else group.lower().replace("/", "-")
        names[group] = f"{SERVER_PREFIX}-{suffix}"
    return names


def groups_to_register(tools_dir: Path = TOOLS_DIR) -> list[str]:
    return [g for g in list_groups(tools_dir) if g] + [JOBS_GROUP]


@dataclass(frozen=True, slots=True)
class Registration:
    """One MCP server to (re)create in one agent CLI."""

    agent: str
    name: str
    group: str
    remove: Step
    add: Step


AGENTS: dict[str, Callable[[str, str], tuple[Step, Step]]] = {
    "claude": lambda name, group: (
        Step(f"unregister {name}", "claude", ("mcp", "remove", "--scope", "user", name), True),
        Step(
            f"register {name} ({group})",
            "claude",
            ("mcp", "add", "--scope", "user", name, "--", "shed", "mcp", "--group", group),
        ),
    ),
    "codex": lambda name, group: (
        Step(f"unregister {name}", "codex", ("mcp", "remove", name), True),
        Step(
            f"register {name} ({group})", "codex", ("mcp", "add", name, "--", "shed", "mcp", "--group", group)
        ),
    ),
}
"""Agent CLIs that can hold an MCP server registration, and how each adds and removes one."""


def plan(
    tools_dir: Path = TOOLS_DIR, *, browser: bool | None = None, cloudflared: bool | None = None
) -> list[Step]:
    """The plain commands `shed update` runs, in order. `browser` defaults to Playwright being installed,
    `cloudflared` to it being missing while Homebrew is present (the dashboard's Tunnels page needs it)."""
    if browser is None:
        browser = importlib.util.find_spec("playwright") is not None
    if cloudflared is None:
        cloudflared = which("cloudflared") is None and which("brew") is not None
    steps = [
        Step("sync the project venv", "uv", ("sync",)),
        Step(
            "reinstall the global shed",
            "uv",
            ("tool", "install", "--editable", str(SHED_ROOT), "--reinstall"),
        ),
    ]
    if browser:
        steps.append(
            Step("install Playwright's Chromium", "uv", ("run", "playwright", "install", "chromium"))
        )
    if cloudflared:
        steps.append(
            Step("install cloudflared for the Tunnels page", "brew", ("install", "cloudflared"), True)
        )
    return steps


def registrations(tools_dir: Path = TOOLS_DIR, agents: Sequence[str] = tuple(AGENTS)) -> list[Registration]:
    """Every group on this machine (plus jobs) as one MCP server per agent CLI, in registration order."""
    names = server_names(groups_to_register(tools_dir))
    out: list[Registration] = []
    for agent in agents:
        for group, name in names.items():
            remove, add = AGENTS[agent](name, group)
            out.append(Registration(agent, name, group, remove, add))
    return out


def run_update(
    *,
    tools_dir: Path = TOOLS_DIR,
    skills_dir: Path = SKILLS_DIR,
    skills_target: Path = CLAUDE_SKILLS_DIR,
    browser: bool | None = None,
    cloudflared: bool | None = None,
    dry_run: bool = False,
    out: Callable[[str], None] = print,
) -> int:
    out(f"shed {shed_version()} at {SHED_ROOT}")
    present = [agent for agent in AGENTS if which(agent) is not None]
    for agent in AGENTS:
        if agent not in present:
            out(f"{agent} CLI not found on PATH: its MCP registration is skipped (install it, then rerun)")
    steps = plan(tools_dir, browser=browser, cloudflared=cloudflared)
    wanted = registrations(tools_dir, present)
    if dry_run:
        for step in steps:
            out(f"would run: {step.text()}")
        for reg in wanted:
            out(f"would run: {reg.remove.text()}")
            out(f"would run: {reg.add.text()}")
        out(f"would link skills from {skills_dir} into {skills_target}")
        return EXIT_OK
    failed = False
    for step in steps:
        result = sh(step.cmd, step.args, cwd=SHED_ROOT, allow_failure=step.allow_failure)
        out(f"{step.label}: {'ok' if result.code == 0 else 'failed'}")
    for reg in wanted:
        sh(reg.remove.cmd, reg.remove.args, cwd=SHED_ROOT, allow_failure=True)
        added = sh(reg.add.cmd, reg.add.args, cwd=SHED_ROOT, allow_failure=True)
        if added.code == 0:
            out(f"{reg.agent}: registered {reg.name}: shed mcp --group {reg.group}")
        else:
            failed = True
            out(f"{reg.agent}: failed to register {reg.name}: {added.stderr.strip() or added.stdout.strip()}")
    linked = link_skills(skills_dir, skills_target)
    for item in linked:
        out(f"skill {item.name}: {item.status}")
    out(f"{len(linked)} skills linked into {skills_target}")
    return EXIT_FAILURE if failed else EXIT_OK
