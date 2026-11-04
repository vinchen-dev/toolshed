from __future__ import annotations

import json
import os
import plistlib
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import shed.cron as cron
from shed import ToolError, UsageError
from shed.cron import (
    Job,
    RunRecord,
    RunStore,
    agent_installed,
    agent_plist,
    cron_lock,
    install_agent,
    is_due,
    load_jobs,
    parse_every,
    run_due,
    run_job,
    summarize,
    uninstall_agent,
)
from shed.registry import SHED_ROOT
from shed.scaffold import scaffold_tool
from shed.version import current as shed_version

BOOM = """
from pydantic import BaseModel
from shed import Tool, ToolContext


class Input(BaseModel):
    pass


def run(params: Input, ctx: ToolContext) -> None:
    raise RuntimeError("kaboom")


TOOL = Tool(name="boom", description="always crashes", input=Input, run=run)
"""


@pytest.fixture
def tools_dir(tmp_path: Path) -> Path:
    folder = tmp_path / "tools"
    folder.mkdir()
    scaffold_tool("echo-x", tools_dir=folder)
    (folder / "boom").mkdir()
    (folder / "boom" / "tool.py").write_text(BOOM, encoding="utf-8")
    return folder


def record(job: str, started: datetime, ok: bool = True, rendered: str = "fine") -> RunRecord:
    return RunRecord(
        job=job,
        tool="t",
        args={},
        started=started,
        finished=started,
        duration_ms=5,
        ok=ok,
        rendered=rendered,
        error=None if ok else "broke\nmore",
        summary="",
    )


@pytest.mark.parametrize(
    ("text", "seconds"), [("30s", 30), ("10m", 600), ("2h", 7200), ("1d", 86400), ("45", 45)]
)
def test_parse_every(text: str, seconds: int) -> None:
    assert parse_every(text) == seconds


@pytest.mark.parametrize("bad", ["0m", "soon", "5w", ""])
def test_parse_every_rejects(bad: str) -> None:
    with pytest.raises(ValueError, match="bad interval"):
        parse_every(bad)


def test_load_jobs(tmp_path: Path) -> None:
    path = tmp_path / "cron.toml"
    path.write_text(
        '[jobs.bdo]\ntool = "bdo-incidents"\nevery = "10m"\n'
        'args = { env = "both", events_hours = 0.25 }\nkeep = 5\n'
        '[jobs.off]\ntool = "git-context"\nenabled = false\ncwd = "~/x"\n',
        encoding="utf-8",
    )
    jobs = load_jobs(path)
    assert [(j.name, j.tool, j.every_seconds, j.keep, j.enabled) for j in jobs] == [
        ("bdo", "bdo-incidents", 600, 5, True),
        ("off", "git-context", 600, 100, False),
    ]
    assert jobs[0].args == {"env": "both", "events_hours": 0.25}
    assert jobs[1].workdir() == Path.home() / "x"
    assert load_jobs(tmp_path / "missing.toml") == []

    path.write_text('[jobs.bad]\ntool = "x"\nevery = "soon"\n', encoding="utf-8")
    with pytest.raises(ToolError, match=r"\[jobs.bad\] is invalid"):
        load_jobs(path)
    path.write_text("jobs = 1\n", encoding="utf-8")
    with pytest.raises(ToolError, match="must hold one"):
        load_jobs(path)


def test_run_job_records_success(tools_dir: Path) -> None:
    job = Job(name="j", tool="echo-x", args={"text": "hi", "upper": True})
    r = run_job(job, tools_dir=tools_dir)
    assert r.ok and r.error is None
    assert r.version == shed_version() and r.tool_version == "0.1.0"  # stamped for later comparison
    assert (r.rendered, r.result, r.summary) == ("HI", {"result": "HI"}, "HI")
    assert r.finished >= r.started and r.duration_ms >= 0


def test_run_job_records_failures(tools_dir: Path) -> None:
    bad = run_job(Job(name="j", tool="echo-x", args={"nope": 1}), tools_dir=tools_dir)
    assert not bad.ok and bad.error is not None and bad.error.startswith("invalid args")
    unknown = run_job(Job(name="j", tool="missing"), tools_dir=tools_dir)
    assert not unknown.ok and "Unknown tool" in (unknown.error or "")
    crash = run_job(Job(name="j", tool="boom"), tools_dir=tools_dir)
    assert crash.error == "RuntimeError: kaboom" and crash.summary == "RuntimeError: kaboom"
    assert any("kaboom" in line for line in crash.log)


def test_summarize_skips_noise_lines() -> None:
    r = record(
        "j",
        datetime.now(UTC),
        rendered="now 2026-10-27 11:00:00 Manila\n\n**QAT:** 3 incidents, 25m down.\n```\n",
    )
    assert summarize(r) == "QAT: 3 incidents, 25m down."
    assert summarize(record("j", datetime.now(UTC), ok=False)) == "broke"
    assert summarize(record("j", datetime.now(UTC), rendered="")) == "(no output)"


def test_store_keeps_newest_and_looks_up_by_id(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    base = datetime(2026, 10, 27, 11, 0, tzinfo=UTC)
    for i in range(4):
        store.save(record("j", base + timedelta(minutes=i)), keep=3)
    entries = store.entries("j")
    assert [r.started.minute for _, r in entries] == [3, 2, 1]  # newest first, oldest pruned
    assert store.count("j") == 3 and store.jobs() == ["j"]
    latest = store.latest("j")
    assert latest is not None and latest.started.minute == 3
    stem = entries[0][0]
    assert stem.startswith("20261027-") and store.get("j", stem) is not None
    assert store.get("j", "../latest") is None and store.get("j", "20990101-000000") is None
    assert json.loads((store.job_dir("j") / "latest.json").read_text())["started"].endswith("11:03:00Z")

    twice = store.save(record("j", base + timedelta(minutes=3)), keep=10)
    assert twice.name.endswith("-2.json")


def test_due_logic_and_run_due(tools_dir: Path, tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    job = Job(name="echo", tool="echo-x", every="10m", args={"text": "x"})
    off = Job(name="off", tool="echo-x", every="10m", args={"text": "y"}, enabled=False)
    first = run_due([job, off], store, tools_dir=tools_dir)
    assert [r.job for r in first] == ["echo"]
    latest = store.latest("echo")
    assert latest is not None
    assert not is_due(job, latest, latest.started + timedelta(minutes=9))
    assert is_due(job, latest, latest.started + timedelta(minutes=9, seconds=31))
    assert run_due([job], store, now=latest.started + timedelta(minutes=1), tools_dir=tools_dir) == []
    assert [r.job for r in run_due([job, off], store, force=True, tools_dir=tools_dir)] == ["echo", "off"]
    assert [r.job for r in run_due([job, off], store, force=True, only=["off"], tools_dir=tools_dir)] == [
        "off"
    ]
    with pytest.raises(UsageError, match="unknown job"):
        run_due([job], store, only=["nope"], tools_dir=tools_dir)


def test_cron_lock_is_exclusive(tmp_path: Path) -> None:
    lock = tmp_path / "state" / "cron.lock"
    with cron_lock(lock), pytest.raises(ToolError, match="still running"), cron_lock(lock):
        pass
    with cron_lock(lock):
        pass


def test_agent_plist_keeps_the_dashboard_alive(tmp_path: Path) -> None:
    data = plistlib.loads(agent_plist(Path("/x/bin/shed"), tmp_path, 9999))
    assert data["Label"] == "com.tool-shed.cron"
    assert data["ProgramArguments"] == ["/x/bin/shed", "ui", "--cron", "--port", "9999"]
    assert data["KeepAlive"] is True and data["RunAtLoad"] is True
    assert data["StandardOutPath"] == str(tmp_path / "launchd.log")
    assert ".local/bin" in data["EnvironmentVariables"]["PATH"]


def shed(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "shed.cli", *args],
        capture_output=True,
        text=True,
        cwd=SHED_ROOT,
        check=False,
        env={**os.environ, **env},
    )


def test_cli_cron_run_runs_and_records(tmp_path: Path) -> None:
    cron = tmp_path / "cron.toml"
    cron.write_text(
        f'[jobs.ctx]\ntool = "git-context"\nevery = "1h"\nargs = {{ commits = 1 }}\ncwd = "{SHED_ROOT}"\n'
    )
    env = {"SHED_CRON_FILE": str(cron), "SHED_STATE_DIR": str(tmp_path / "state")}

    run = shed(env, "cron", "run")
    assert run.returncode == 0, run.stderr
    assert run.stdout.startswith("ctx: ok in ")
    assert (tmp_path / "state" / "runs" / "ctx" / "latest.json").is_file()
    assert "ctx ok" in (tmp_path / "state" / "cron.log").read_text()

    assert shed(env, "cron", "run").stdout.strip() == "nothing due"
    assert shed(env, "cron", "run", "--force").stdout.startswith("ctx: ok")
    assert shed(env, "cron", "run", "nope").returncode == 2

    status = shed(env, "cron")
    assert (
        status.returncode == 0
        and "ctx" in status.stdout
        and "ok" in status.stdout
        and "agent:" in status.stdout
    )
    as_json = json.loads(shed(env, "cron", "--json").stdout)
    assert as_json["jobs"][0]["name"] == "ctx" and as_json["jobs"][0]["status"] == "ok"

    last = shed(env, "runs", "ctx", "--last")
    assert last.returncode == 0 and "branch:" in last.stdout
    latest = json.loads(shed(env, "runs", "ctx", "--json").stdout)
    assert latest["ok"] is True and "branch" in latest["result"]
    listing = shed(env, "runs")
    assert "ctx" in listing.stdout and "2 runs" in listing.stdout
    history = shed(env, "runs", "ctx", "--limit", "1")
    assert history.returncode == 0 and len(history.stdout.strip().splitlines()) == 1
    assert shed(env, "runs", "ghost", "--last").returncode == 1


def test_cli_cron_with_no_jobs(tmp_path: Path) -> None:
    env = {"SHED_CRON_FILE": str(tmp_path / "none.toml"), "SHED_STATE_DIR": str(tmp_path / "state")}
    assert "No jobs" in shed(env, "cron").stdout
    assert "No jobs" in shed(env, "cron", "run").stdout
    assert "No runs" in shed(env, "runs").stdout


def test_install_agent_falls_back_when_launch_agents_is_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    class Done:
        code = 0
        stdout = stderr = ""

    monkeypatch.setattr(cron, "sh", lambda cmd, args=(), **kw: calls.append([cmd, *args]) or Done())
    monkeypatch.setattr(cron, "shed_executable", lambda: Path("/x/bin/shed"))
    launch_agents = tmp_path / "LaunchAgents"
    launch_agents.mkdir()
    launch_agents.chmod(0o500)
    state = tmp_path / "state"
    plist = launch_agents / "com.tool-shed.cron.plist"
    try:
        path, permanent = install_agent(8765, plist_path=plist, state_dir=state)
    finally:
        launch_agents.chmod(0o700)
    assert (path, permanent) == (state / "com.tool-shed.cron.plist", False)
    assert plistlib.loads(path.read_bytes())["ProgramArguments"][-1] == "8765"
    assert calls[-1] == ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)]
    assert agent_installed(plist_path=plist, state_dir=state) == path
    assert uninstall_agent(plist_path=plist, state_dir=state) is True
    assert agent_installed(plist_path=plist, state_dir=state) is None
    assert uninstall_agent(plist_path=plist, state_dir=state) is False
