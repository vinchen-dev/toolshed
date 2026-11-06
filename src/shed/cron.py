"""Scheduled tool runs: jobs from ~/.config/shed/cron.toml, run records on disk, a launchd agent to
drive them.

The agent keeps `shed ui --cron` alive; `shed cron run` performs the same due-check by hand."""

from __future__ import annotations

import fcntl
import json
import os
import plistlib
import re
import time
import tomllib
import traceback
from collections.abc import Collection, Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from shed.define import ToolContext
from shed.lib.errors import ShedError, ToolError, UsageError
from shed.lib.output import render_result, to_json
from shed.lib.shell import sh
from shed.registry import TOOLS_DIR, load_tool
from shed.version import current as shed_version

CRON_FILE = Path(os.environ.get("SHED_CRON_FILE") or Path.home() / ".config" / "shed" / "cron.toml")
STATE_DIR = Path(os.environ.get("SHED_STATE_DIR") or Path.home() / ".local" / "state" / "shed")
AGENT_LABEL = "com.tool-shed.cron"
AGENT_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{AGENT_LABEL}.plist"
AGENT_INTERVAL_S = 60
_EVERY = re.compile(r"^(\d+)\s*([smhd]?)$")
_UNITS = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}
_NOISE = re.compile(r"^(now .* Manila|```.*|#+\s*|\s*)$")
_STEM = re.compile(r"[0-9]{8}-[0-9]{6}(-[0-9]+)?")


def local_now() -> datetime:
    return datetime.now().astimezone().replace(microsecond=0)


def parse_every(text: str) -> int:
    """'30s', '10m', '2h', '1d' or plain seconds, as seconds."""
    match = _EVERY.match(text.strip())
    if not match or int(match.group(1)) <= 0:
        raise ValueError(f"bad interval '{text}', expected something like 30s, 10m, 2h or 1d")
    return int(match.group(1)) * _UNITS[match.group(2)]


class Job(BaseModel):
    name: str
    tool: str
    every: str = "10m"
    args: dict[str, Any] = {}
    keep: int = Field(default=100, ge=1)
    cwd: str | None = None
    enabled: bool = True

    @field_validator("every")
    @classmethod
    def _check_every(cls, value: str) -> str:
        parse_every(value)
        return value

    @property
    def every_seconds(self) -> int:
        return parse_every(self.every)

    def workdir(self) -> Path:
        return Path(self.cwd).expanduser() if self.cwd else Path.home()


def load_jobs(path: Path = CRON_FILE) -> list[Job]:
    """Jobs from the [jobs.<name>] tables of the cron file; no file means no jobs."""
    if not path.is_file():
        return []
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ToolError(f"cannot read {path}: {exc}") from exc
    tables = data.get("jobs", {})
    if not isinstance(tables, dict):
        raise ToolError(f"{path}: [jobs] must hold one [jobs.<name>] table per job")
    jobs: list[Job] = []
    for name, table in tables.items():
        if not isinstance(table, dict):
            raise ToolError(f"{path}: [jobs.{name}] must be a table")
        try:
            jobs.append(Job.model_validate({**table, "name": name}))
        except ValidationError as exc:
            raise ToolError(f"{path}: [jobs.{name}] is invalid: {exc}") from exc
    return jobs


class RunRecord(BaseModel):
    job: str
    tool: str
    args: dict[str, Any]
    started: datetime
    finished: datetime
    duration_ms: int
    ok: bool
    error: str | None = None
    summary: str = ""
    """One line: the first informative line of the rendered output, or the error."""
    rendered: str = ""
    result: Any = None
    """The tool's result as JSON data, what `shed <tool> --json` would print."""
    log: list[str] = []
    version: str | None = None
    """The shed version that produced this record; None for records older than versioning."""
    tool_version: str | None = None


def summarize(record: RunRecord) -> str:
    if record.error:
        return record.error.splitlines()[0][:160]
    for line in record.rendered.splitlines():
        if not _NOISE.match(line):
            return line.replace("**", "").strip("# ").strip()[:160]
    return "(no output)"


def run_job(job: Job, tools_dir: Path = TOOLS_DIR) -> RunRecord:
    """Run one job in-process and return its record. Failures are recorded, never raised."""
    started = local_now()
    clock = time.perf_counter()
    log: list[str] = []
    record = RunRecord(
        job=job.name,
        tool=job.tool,
        args=job.args,
        started=started,
        finished=started,
        duration_ms=0,
        ok=False,
        version=shed_version(),
    )
    try:
        tool = load_tool(job.tool, tools_dir)
        record.tool_version = tool.version
        params = tool.input.model_validate({key.replace("-", "_"): value for key, value in job.args.items()})
        result = tool.run(params, ToolContext(cwd=job.workdir(), log=log.append))
        record.rendered = render_result(tool, result, json_mode=False)
        record.result = json.loads(to_json(result, compact=True))
        record.ok = True
    except ValidationError as exc:
        record.error = f"invalid args: {exc}"
    except ShedError as exc:
        record.error = str(exc)
    except Exception as exc:  # a crashing tool must not stop the other jobs
        record.error = f"{type(exc).__name__}: {exc}"
        log.append("".join(traceback.format_exception(exc)).rstrip())
    record.finished = local_now()
    record.duration_ms = int((time.perf_counter() - clock) * 1000)
    record.log = log
    record.summary = summarize(record)
    return record


class RunStore:
    """Run records as JSON files under <root>/<job>/, newest kept up to each job's `keep`."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def job_dir(self, job: str) -> Path:
        return self.root / job

    def _files(self, job: str) -> list[Path]:
        folder = self.job_dir(job)
        if not folder.is_dir():
            return []
        return sorted((p for p in folder.glob("*.json") if p.name != "latest.json"), reverse=True)

    def save(self, record: RunRecord, keep: int) -> Path:
        folder = self.job_dir(record.job)
        folder.mkdir(parents=True, exist_ok=True)
        stamp = record.started.strftime("%Y%m%d-%H%M%S")
        path = folder / f"{stamp}.json"
        n = 1
        while path.exists():
            n += 1
            path = folder / f"{stamp}-{n}.json"
        text = record.model_dump_json(indent=1)
        path.write_text(text, encoding="utf-8")
        (folder / "latest.json").write_text(text, encoding="utf-8")
        self.prune(record.job, keep)
        return path

    def latest(self, job: str) -> RunRecord | None:
        files = self._files(job)
        return self._read(files[0]) if files else None

    def entries(self, job: str, limit: int = 20) -> list[tuple[str, RunRecord]]:
        """(file stem, record) pairs, newest first; the stem is the run's id in the UI and API."""
        return [(p.stem, self._read(p)) for p in self._files(job)[:limit]]

    def history(self, job: str, limit: int = 20) -> list[RunRecord]:
        return [record for _, record in self.entries(job, limit)]

    def get(self, job: str, stem: str) -> RunRecord | None:
        if not _STEM.fullmatch(stem):
            return None
        path = self.job_dir(job) / f"{stem}.json"
        return self._read(path) if path.is_file() else None

    def count(self, job: str) -> int:
        return len(self._files(job))

    def prune(self, job: str, keep: int) -> int:
        stale = self._files(job)[keep:]
        for path in stale:
            path.unlink()
        return len(stale)

    def jobs(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(p.name for p in self.root.iterdir() if p.is_dir())

    @staticmethod
    def _read(path: Path) -> RunRecord:
        try:
            return RunRecord.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ToolError(f"cannot read run record {path}: {exc}") from exc


def is_due(job: Job, latest: RunRecord | None, now: datetime) -> bool:
    """Due when never run, or when the interval has passed minus a little slack for the agent's own tick."""
    if latest is None:
        return True
    slack = min(AGENT_INTERVAL_S / 2, job.every_seconds / 2)
    return (now - latest.started).total_seconds() >= job.every_seconds - slack


def next_due(job: Job, latest: RunRecord | None) -> datetime | None:
    if latest is None:
        return None
    return latest.started + timedelta(seconds=job.every_seconds)


def run_summary(record: RunRecord) -> dict[str, Any]:
    """The few fields a list or a status line needs; the full record stays in the store."""
    return {
        "started": record.started,
        "duration_ms": record.duration_ms,
        "ok": record.ok,
        "error": record.error,
        "summary": record.summary,
    }


def job_summary(job: Job, store: RunStore, running: Collection[str] = ()) -> dict[str, Any]:
    latest = store.latest(job.name)
    return {
        "name": job.name,
        "tool": job.tool,
        "every": job.every,
        "enabled": job.enabled,
        "running": job.name in running,
        "next_due": next_due(job, latest),
        "runs": store.count(job.name),
        "latest": run_summary(latest) if latest else None,
    }


def health(jobs: Sequence[Job], store: RunStore, running: Collection[str] = ()) -> dict[str, Any]:
    """The one-glance answer the strip and the MCP status share; failing beats running beats never run."""
    latest = {job.name: store.latest(job.name) for job in jobs}
    failing = [job.name for job in jobs if (r := latest[job.name]) and not r.ok]
    active = [job.name for job in jobs if job.name in running]
    never = [job.name for job in jobs if latest[job.name] is None]
    if not jobs:
        state, headline = "off", "No jobs yet"
    elif failing:
        state, headline = "fail", f"{len(failing)} failing: {', '.join(failing)}"
    elif active:
        state, headline = "running", f"running {', '.join(active)}"
    elif never:
        state, headline = "off", f"{len(never)} of {len(jobs)} not run yet"
    else:
        state, headline = "ok", "All healthy"
    runs = [r for r in latest.values() if r]
    last = max(runs, key=lambda r: r.started) if runs else None
    dues = [due for job in jobs if job.enabled and (due := next_due(job, latest[job.name]))]
    return {
        "state": state,
        "headline": headline,
        "jobs": len(jobs),
        "failing": failing,
        "running": active,
        "never_run": never,
        "last_run": {"job": last.job, **run_summary(last)} if last else None,
        "next_due": min(dues) if dues else None,
    }


def run_due(
    jobs: Sequence[Job],
    store: RunStore,
    *,
    now: datetime | None = None,
    force: bool = False,
    only: Sequence[str] = (),
    tools_dir: Path = TOOLS_DIR,
) -> list[RunRecord]:
    """Run every enabled job that is due (or every named job when forced) and save the records."""
    now = now or local_now()
    unknown = sorted(set(only) - {job.name for job in jobs})
    if unknown:
        raise UsageError(f"unknown job(s): {', '.join(unknown)}. Run `shed cron` to list them.")
    records: list[RunRecord] = []
    for job in jobs:
        if only and job.name not in only:
            continue
        if not job.enabled and not force:
            continue
        if not force and not is_due(job, store.latest(job.name), now):
            continue
        record = run_job(job, tools_dir=tools_dir)
        store.save(record, job.keep)
        records.append(record)
    return records


@contextmanager
def cron_lock(path: Path) -> Iterator[None]:
    """One `shed cron run` at a time, so a slow job and the agent's next tick never overlap."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ToolError("another `shed cron run` is still running") from exc
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def append_log(path: Path, records: Sequence[RunRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for r in records:
            status = "ok" if r.ok else "FAILED"
            fh.write(f"{r.started:%Y-%m-%d %H:%M:%S} {r.job} {status} {r.duration_ms}ms {r.summary}\n")


def shed_executable(path_env: str | None = None) -> Path:
    """The `shed` the agent should run: the global tool shim, never the repo's own .venv entry point.

    A shell inside the repo puts `.venv/bin` first on PATH, but launchd cannot read a venv under
    Desktop or Documents (macOS keeps those folders from background agents), so that copy would crash."""
    candidates = [
        Path(p) / "shed"
        for p in (path_env if path_env is not None else os.environ.get("PATH", "")).split(os.pathsep)
        if p
    ]
    venv = (Path(__file__).resolve().parents[2] / ".venv").resolve()
    for candidate in candidates:
        if (
            candidate.is_file()
            and os.access(candidate, os.X_OK)
            and not candidate.resolve().is_relative_to(venv)
        ):
            return candidate
    raise ToolError("the global `shed` is not on PATH; run `uv tool install --editable .` in tool-shed first")


def agent_plist(shed_path: Path, state_dir: Path, port: int) -> bytes:
    """launchd property list that keeps `shed ui --cron` running: the dashboard plus the scheduler."""
    home = Path.home()
    return plistlib.dumps(
        {
            "Label": AGENT_LABEL,
            "ProgramArguments": [str(shed_path), "ui", "--cron", "--port", str(port)],
            "KeepAlive": True,
            "RunAtLoad": True,
            "StandardOutPath": str(state_dir / "launchd.log"),
            "StandardErrorPath": str(state_dir / "launchd.log"),
            "EnvironmentVariables": {
                "HOME": str(home),
                "PATH": f"{home / '.local' / 'bin'}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
            },
        }
    )


def _domain() -> str:
    return f"gui/{os.getuid()}"


def agent_paths(plist_path: Path = AGENT_PLIST, state_dir: Path = STATE_DIR) -> tuple[Path, Path]:
    """(permanent location that launchd loads at login, fallback inside the state dir)."""
    return plist_path, state_dir / plist_path.name


def install_agent(
    port: int, plist_path: Path = AGENT_PLIST, state_dir: Path = STATE_DIR
) -> tuple[Path, bool]:
    """Write the plist and bootstrap it. Returns (path, permanent); permanent is False after a fallback."""
    state_dir.mkdir(parents=True, exist_ok=True)
    content = agent_plist(shed_executable(), state_dir, port)
    permanent, fallback = agent_paths(plist_path, state_dir)
    for candidate in (permanent, fallback):
        sh("launchctl", ["bootout", f"{_domain()}/{AGENT_LABEL}"], allow_failure=True)
        if candidate.is_file():
            candidate.unlink()
    try:
        permanent.parent.mkdir(parents=True, exist_ok=True)
        permanent.write_bytes(content)
        target, is_permanent = permanent, True
    except PermissionError:
        fallback.write_bytes(content)
        target, is_permanent = fallback, False
    result = sh("launchctl", ["bootstrap", _domain(), str(target)], allow_failure=True)
    if result.code != 0:
        raise ToolError(f"launchctl bootstrap failed: {result.stderr.strip() or result.stdout.strip()}")
    return target, is_permanent


def uninstall_agent(plist_path: Path = AGENT_PLIST, state_dir: Path = STATE_DIR) -> bool:
    """Stop the agent and delete its plist wherever it was written. False when there was nothing to remove."""
    found = [p for p in agent_paths(plist_path, state_dir) if p.is_file()]
    if not found:
        return False
    sh("launchctl", ["bootout", f"{_domain()}/{AGENT_LABEL}"], allow_failure=True)
    for path in found:
        path.unlink()
    return True


def agent_installed(plist_path: Path = AGENT_PLIST, state_dir: Path = STATE_DIR) -> Path | None:
    """The plist that is installed, or None."""
    return next((p for p in agent_paths(plist_path, state_dir) if p.is_file()), None)
