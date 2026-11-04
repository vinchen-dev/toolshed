from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass

from shed.registry import SHED_ROOT


@dataclass(frozen=True, slots=True)
class Run:
    code: int
    stdout: str
    stderr: str


def shed(*args: str) -> Run:
    proc = subprocess.run(
        [sys.executable, "-m", "shed.cli", *args], capture_output=True, text=True, cwd=SHED_ROOT, check=False
    )
    return Run(proc.returncode, proc.stdout, proc.stderr)


def test_lists_tools_as_json_with_schemas() -> None:
    run = shed("list", "--json")
    assert run.code == 0
    tools = json.loads(run.stdout)
    names = [t["name"] for t in tools]
    assert {"api-call", "git-context", "log-watch"} <= set(names)  # base is on every device
    assert not any(name.startswith("_") for name in names)
    git_context = next(t for t in tools if t["name"] == "git-context")
    assert "commits" in git_context["input"]["properties"] and git_context["group"] == "base"


def test_shows_help_for_a_tool() -> None:
    run = shed("help", "git-context")
    assert run.code == 0
    assert "--commits <int>" in run.stdout
    assert "default: 10" in run.stdout


def test_runs_a_tool_and_emits_compact_json() -> None:
    run = shed("git-context", "--commits", "1", "--json")
    assert run.code == 0, run.stderr
    assert len(run.stdout.strip().splitlines()) == 1
    assert "branch" in json.loads(run.stdout)


def test_exits_2_on_unknown_tool_and_bad_flags() -> None:
    assert shed("nope").code == 2
    bad = shed("git-context", "--commits", "many")
    assert bad.code == 2
    assert "invalid int value" in bad.stderr


def test_tool_failure_exits_1(tmp_path: str) -> None:
    run = shed("git-context", "--path", str(tmp_path))
    assert run.code == 1
    assert "not inside a git repository" in run.stderr


def test_version_flag_prints_the_shed_version() -> None:
    run = shed("--version")
    assert run.code == 0 and run.stdout.startswith("shed ") and len(run.stdout.split()) == 2


def test_list_and_help_show_each_tools_version() -> None:
    listing = shed("list")
    line = next(line for line in listing.stdout.splitlines() if line.startswith("git-context"))
    assert line.split()[:3] == ["git-context", "base", "0.1.0"]
    tools = json.loads(shed("list", "--json").stdout)
    assert next(t for t in tools if t["name"] == "git-context")["version"] == "0.1.0"
    assert shed("help", "git-context").stdout.startswith("shed git-context  (v0.1.0)")
