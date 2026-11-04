"""Tools over the cron records the dashboard shows, so an agent gets the same answers through `shed mcp`."""

from __future__ import annotations

import json
import urllib.request
from typing import Any, Literal

from pydantic import BaseModel, Field

from shed.cron import (
    CRON_FILE,
    STATE_DIR,
    RunRecord,
    RunStore,
    append_log,
    cron_lock,
    health,
    job_summary,
    load_jobs,
    run_due,
    run_summary,
)
from shed.define import Tool, ToolContext
from shed.lib.errors import ToolError, UsageError
from shed.ui import DEFAULT_HOST, DEFAULT_PORT


def _store() -> RunStore:
    return RunStore(STATE_DIR / "runs")


def _running() -> list[str]:
    """Jobs the dashboard is running right now, when it is up; this process has no scheduler of its own."""
    try:
        with urllib.request.urlopen(f"http://{DEFAULT_HOST}:{DEFAULT_PORT}/health", timeout=0.5) as resp:
            return [str(name) for name in json.load(resp).get("running", [])]
    except (OSError, ValueError):
        return []


def _check_job(job: str) -> None:
    names = [j.name for j in load_jobs(CRON_FILE)]
    if job not in names:
        raise ToolError(f"unknown job '{job}'; configured jobs: {', '.join(names) or 'none'}")


def _record(job: str, run: str | None, store: RunStore) -> tuple[str, RunRecord]:
    if run is None:
        entries = store.entries(job, 1)
        if not entries:
            raise ToolError(f"job '{job}' has no recorded runs yet")
        return entries[0]
    record = store.get(job, run)
    if record is None:
        raise ToolError(f"no run '{run}' recorded for job '{job}'; ids come from job-runs")
    return run, record


class NoInput(BaseModel):
    pass


class JobInput(BaseModel):
    job: str = Field(description="Job name as listed by jobs-status")


class RunsInput(JobInput):
    limit: int = Field(20, ge=1, le=500, description="How many runs to list, newest first")


class ResultInput(JobInput):
    run: str | None = Field(None, description="Run id from job-runs; default is the latest run")
    part: Literal["result", "text", "log", "all"] = Field(
        "result",
        description="result = the tool's JSON data, text = the rendered CLI output, log = its log lines",
    )


def jobs_status(_params: NoInput, _ctx: ToolContext) -> dict[str, Any]:
    jobs = load_jobs(CRON_FILE)
    store, running = _store(), _running()
    return {"health": health(jobs, store, running), "jobs": [job_summary(j, store, running) for j in jobs]}


def job_runs(params: RunsInput, _ctx: ToolContext) -> list[dict[str, Any]]:
    _check_job(params.job)
    return [{"id": stem, **run_summary(r)} for stem, r in _store().entries(params.job, params.limit)]


def job_result(params: ResultInput, _ctx: ToolContext) -> dict[str, Any]:
    _check_job(params.job)
    stem, record = _record(params.job, params.run, _store())
    out: dict[str, Any] = {
        "id": stem,
        "job": record.job,
        "tool": record.tool,
        "args": record.args,
        **run_summary(record),
        "finished": record.finished,
    }
    if params.part in ("result", "all"):
        out["result"] = record.result
    if params.part in ("text", "all"):
        out["text"] = record.rendered
    if params.part in ("log", "all"):
        out["log"] = record.log
    return out


def job_run(params: JobInput, _ctx: ToolContext) -> dict[str, Any]:
    jobs = load_jobs(CRON_FILE)
    store = _store()
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with cron_lock(STATE_DIR / "cron.lock"):
            records = run_due(jobs, store, force=True, only=[params.job])
    except UsageError as exc:
        raise ToolError(str(exc)) from exc
    append_log(STATE_DIR / "cron.log", records)
    stem, record = store.entries(params.job, 1)[0]
    return {"id": stem, **run_summary(record), "result": record.result}


JOBS_GROUP = "jobs"
"""Group name that `shed mcp --group` uses for these tools, which live here rather than under tools/."""

JOB_TOOLS: tuple[Tool[Any, Any], ...] = (
    Tool(
        name="jobs-status",
        description=(
            "Health of the scheduled jobs the dashboard shows: whether anything is failing or all is "
            "healthy, and per job its tool, interval, last run and next due time. Read this before "
            "re-running a tool by hand."
        ),
        input=NoInput,
        run=jobs_status,
    ),
    Tool(
        name="job-runs",
        description=(
            "Run history of one scheduled job, newest first: run id, start time, duration, ok or failed, "
            "and the one-line summary."
        ),
        input=RunsInput,
        run=job_runs,
    ),
    Tool(
        name="job-result",
        description=(
            "The recorded result of a job's run (latest by default): the tool's JSON data, its rendered text "
            "or its log. The same data as `shed runs <job> --json`."
        ),
        input=ResultInput,
        run=job_result,
    ),
    Tool(
        name="job-run",
        description=(
            "Run a scheduled job now, record it exactly as the scheduler would, and return the new run's "
            "summary and result data."
        ),
        input=JobInput,
        run=job_run,
    ),
)
