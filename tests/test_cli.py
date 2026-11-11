from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from shed import cli
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
    assert "commits" in git_context["input"]["properties"] and git_context["group"] == "core"


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
    assert line.split()[:3] == ["git-context", "core", "0.1.0"]
    tools = json.loads(shed("list", "--json").stdout)
    assert next(t for t in tools if t["name"] == "git-context")["version"] == "0.1.0"
    assert shed("help", "git-context").stdout.startswith("shed git-context  (v0.1.0)")


class FakeDashboard:
    """The launchd agent and its dashboard as `shed start` sees them: /health answers and install_agent."""

    def __init__(self, up: bool, busy_polls: int = 0, permanent: bool = True) -> None:
        self.pid: int | None = 100 if up else None
        self.busy_polls, self.permanent, self.installs = busy_polls, permanent, 0
        self.job_running = False

    def health(self) -> dict[str, Any] | None:
        if self.pid is None:
            return None
        running = ["bdo-session"] if self.busy_polls > 0 else []
        self.busy_polls -= 1
        self.job_running = bool(running)
        return {"ok": True, "pid": self.pid, "running": running, "version": "0.2.0"}

    def install(self, port: int) -> tuple[Path, bool]:
        assert not self.job_running, "a running job was cut off"
        self.installs += 1
        self.pid = 200 + self.installs
        return Path("/x/com.tool-shed.cron.plist"), self.permanent


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)


def use(monkeypatch: pytest.MonkeyPatch, fake: FakeDashboard, tunnels: list[str] | None = None) -> None:
    monkeypatch.setattr(cli, "_health", fake.health)
    monkeypatch.setattr(cli, "install_agent", fake.install)
    monkeypatch.setattr(cli, "_open_tunnels", lambda: tunnels or [])


def test_start_starts_the_agent_and_waits_for_the_dashboard(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], no_sleep: None
) -> None:
    fake = FakeDashboard(up=False)
    use(monkeypatch, fake)
    assert cli.main(["start"]) == 0
    out = capsys.readouterr()
    assert out.out == "Started the dashboard and scheduler (shed 0.2.0): http://127.0.0.1:8765\n"
    assert fake.installs == 1 and out.err == ""


def test_start_restarts_after_running_jobs_finish_and_names_the_stopped_tunnels(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], no_sleep: None
) -> None:
    fake = FakeDashboard(up=True, busy_polls=3, permanent=False)
    use(monkeypatch, fake, tunnels=["finance"])
    assert cli.main(["start"]) == 0
    out = capsys.readouterr()
    assert out.out.splitlines() == [
        "Restarted the dashboard and scheduler (shed 0.2.0): http://127.0.0.1:8765",
        "Tunnels stopped by the restart: finance. Start them again at http://127.0.0.1:8765/tunnels; "
        "each gets a new public URL.",
    ]
    assert "waiting for bdo-session to finish" in out.err and "&& shed start" in out.err


def test_start_fails_when_the_dashboard_never_answers_and_rejects_arguments(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], no_sleep: None
) -> None:
    fake = FakeDashboard(up=False)
    use(monkeypatch, fake)
    # Registered, but the dashboard never answers.
    monkeypatch.setattr(cli, "install_agent", lambda port: (Path("/x.plist"), True))
    monkeypatch.setattr(cli, "DASHBOARD_START_S", 0.01)
    assert cli.main(["start"]) == 1
    assert "did not answer" in capsys.readouterr().err
    use(monkeypatch, fake)
    assert cli.main(["start", "--now"]) == 2
