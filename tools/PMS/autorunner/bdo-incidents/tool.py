"""Summarise BDO session hangs and relogin outages from SLS logs (QAT, PROD or both).

Pulls through the sls-logs tool, so credentials come from the same ~/.config/aliyun-sls.env.
The result holds parsed fields only, never raw log text, so credentials and OTPs cannot leak.
"""

from __future__ import annotations

import re
import time
from collections import Counter
from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from shed import Tool, ToolContext, ToolError
from shed.lib import manila
from shed.lib.env import require_env
from shed.registry import load_tool, load_tool_module

from .incidents import (
    LEGEND,
    SIDE_FOOTNOTE,
    EventLine,
    Format,
    Incident,
    Sort,
    State,
    build_incidents,
    dur,
    event_lines,
    pair_incidents,
    parse,
    render_boxes,
    render_md,
    render_plain,
    render_side,
    session_created,
    session_state,
    sort_incidents,
)

Env = Literal["qat", "prod"]
ENVS: dict[Env, tuple[str, str]] = {
    "qat": ("platform-qat-aliyun-logs", "platform-pms-qat"),
    "prod": ("platform-prod-aliyun-logs", "platform-pms-prod"),
}
GREPS = (
    "Re-login",
    "REPLACED",
    "LOGIN_FAILED",
    "Invalid Login",
    "OTP timeout",
    "ERROR",
    "CRITICAL",
    "Invalidated",
    "BDO retry",
    "on-demand page revisit",
    "cookie refresh successful",
    "token CHANGED (page visit)",
    "Stale-grid signal",
    "Stopping scraper",
    "Token refresh failed",
)
PULL_LIMIT = 20_000
PULL_ATTEMPTS = 3
RETRY_WAIT_S = 3.0
EVENT_CONTEXT = timedelta(hours=2, minutes=10)  # extra history so retries and saves pair up
EMPTY_PULL = (
    "no matching events after 3 tries (SLS empty or failing; check ~/.config/aliyun-sls.env, "
    "then rerun in a minute). Do NOT report this as 'no change'."
)
_RAW_RE = re.compile(r"^(qat|prod)=(.+)$")


def now_manila() -> datetime:
    """Current Manila wall-clock time, naive like the log timestamps. Patched in tests."""
    return manila.now_manila().replace(tzinfo=None, microsecond=0)


def parse_naive(text: str) -> datetime:
    return manila.parse_manila(text).replace(tzinfo=None)


def sleep(seconds: float) -> None:
    time.sleep(seconds)


class Input(BaseModel):
    env: Literal["qat", "prod", "both"] = Field(
        "qat", description="Environment to pull; PROD only when asked"
    )
    hours: float = Field(24, gt=0, description="Window: the last N hours on the Manila clock")
    since: str | None = Field(None, description="Window start, Manila 'YYYY-MM-DD HH:MM'; overrides --hours")
    sort: Sort = Field("time", description="Incident order: chronological, or longest outage first")
    top: int = Field(
        0,
        ge=0,
        description="Keep only the first N incidents after sorting (0 = all); with side, the N longest pairs",
    )
    format: Format = Field(
        "box",
        description=(
            "Table layout: box = one boxed table per env with shared widths; side = QAT and PROD paired by "
            "start time; plain = compact table with short step labels; md = markdown table"
        ),
    )
    events_since: str | None = Field(
        None,
        description=(
            "Cron mode: list what happened after this Manila time (visits, hangs, retries, relogins, saves) "
            "instead of the incident table"
        ),
    )
    events_hours: float | None = Field(
        None, gt=0, description="Cron mode for the last N hours; same as --events-since"
    )
    full: bool = Field(
        False,
        description=(
            "With --events-hours/--events-since: also build the incident table and totals over the --hours "
            "window from the same pull, as the dashboard job does"
        ),
    )
    raw: list[str] = Field(
        default_factory=list,
        description=(
            "Parse a saved pull (sls-logs text or an SLS console .csv export) instead of querying SLS, "
            "as qat=FILE or prod=FILE; repeat per env"
        ),
    )

    @field_validator("since", "events_since")
    @classmethod
    def _check_time(cls, value: str | None) -> str | None:
        if value is not None:
            parse_naive(value)
        return value

    @field_validator("raw")
    @classmethod
    def _check_raw(cls, value: list[str]) -> list[str]:
        for item in value:
            if not _RAW_RE.match(item):
                raise ValueError(f"bad --raw '{item}', expected qat=FILE or prod=FILE")
        return value


class EnvReport(BaseModel):
    env: Env
    project: str
    logstore: str
    since: datetime
    """Start of the window the incidents or events are counted in, Manila time."""
    until: datetime
    """End of the window: now for a live pull, the last event for a saved pull."""
    error: str | None = None
    """Set when the pull returned nothing; the other fields are then empty."""
    events_since: datetime | None = None
    """Cron mode: the cut-off the events list starts at; the incidents still use `since`."""
    incidents: list[Incident] = []
    total_outage_seconds: int = 0
    downtime_percent: float = 0
    failed_attempts: int = 0
    failed_step_mix: dict[str, int] = {}
    scraper_stops: list[datetime] = []
    state: State = "unknown"
    """Session state after the last event that settles it: up after a saved login, a retry recovery or a
    page visit; down after an invalidation, a scraper stop, or failed relogins with no live session."""
    state_since: datetime | None = None
    """When the current state began, Manila time."""
    session_created: datetime | None = None
    """created_at of the last saved login, Manila time: the age of the session in use while up."""
    events: list[EventLine] = []
    """Filled in cron mode only."""


class Report(BaseModel):
    now: datetime
    mode: Literal["incidents", "events"]
    format: Format
    sort: Sort
    top: int
    full: bool = False
    """Cron mode that also carries the incident table (--full)."""
    envs: list[EnvReport]


def fetch_logs(env: Env, start: datetime, ctx: ToolContext) -> str:
    """Pull one environment's BDO lines through the sls-logs tool, as the text its render prints."""
    project, logstore = ENVS[env]
    sls = load_tool("sls-logs")
    params = sls.input(
        project=project,
        logstore=logstore,
        start=start.strftime("%Y-%m-%d %H:%M"),
        limit=PULL_LIMIT,
        grep=list(GREPS),
    )
    result = sls.run(params, ctx)
    return "\n".join(f"{line.time} [{line.container}] {line.content}" for line in result.lines)


def check_credentials() -> None:
    """Fail fast with sls-logs' own message, so a missing key is not retried per environment."""
    sls = load_tool_module("sls-logs")
    require_env("ALIBABA_CLOUD_ACCESS_KEY_ID", "ALIBABA_CLOUD_ACCESS_KEY_SECRET", env_file=sls.ENV_FILE)


def _pull(env: Env, start: datetime, ctx: ToolContext) -> str:
    """Fetch with retries: SLS occasionally returns nothing, or a transient refusal, for a good query."""
    error: ToolError | None = None
    for attempt in range(1, PULL_ATTEMPTS + 1):
        try:
            text, error = fetch_logs(env, start, ctx), None
        except ToolError as exc:
            text, error = "", exc
        if text.strip():
            return text
        if attempt < PULL_ATTEMPTS:
            ctx.log(f"# {env}: {error or 'empty result'}; retrying ({attempt}/{PULL_ATTEMPTS})")
            sleep(RETRY_WAIT_S)
    if error is not None:
        raise error
    return ""


def _read_raw(path: Path, ctx: ToolContext) -> str:
    """A saved sls-logs pull as text, or an SLS console CSV export converted through sls-csv."""
    if path.suffix.lower() == ".csv":
        sls_csv = load_tool_module("sls-csv")
        return sls_csv.render(sls_csv.run(sls_csv.Input(path=[str(path)]), ctx))
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ToolError(f"cannot read --raw file {path}: {exc.strerror or exc}") from exc


def _window(params: Input, now: datetime) -> tuple[datetime, datetime | None]:
    """(incident window start, events-mode cut-off or None)."""
    since = parse_naive(params.since) if params.since else now - timedelta(hours=params.hours)
    if params.events_since:
        return since, parse_naive(params.events_since)
    if params.events_hours is not None:
        return since, now - timedelta(hours=params.events_hours)
    return since, None


def run(params: Input, ctx: ToolContext) -> Report:
    now = now_manila()
    since, events_since = _window(params, now)
    table = events_since is None or params.full
    pull_from = events_since - EVENT_CONTEXT if events_since else since
    if table:
        pull_from = min(pull_from, since)
    raw_files = {
        m.group(1): ctx.cwd / Path(m.group(2)).expanduser()
        for item in params.raw
        if (m := _RAW_RE.match(item))
    }
    envs: list[Env] = ["qat", "prod"] if params.env == "both" else [params.env]
    if any(env not in raw_files for env in envs):
        check_credentials()

    reports: list[EnvReport] = []
    for env in envs:
        project, logstore = ENVS[env]
        report = EnvReport(
            env=env,
            project=project,
            logstore=logstore,
            since=since if table else events_since or since,
            until=now,
            events_since=events_since,
        )
        try:
            text = _read_raw(raw_files[env], ctx) if env in raw_files else _pull(env, pull_from, ctx)
        except ToolError as exc:
            report.error = f"pull failed: {exc}"
            ctx.log(f"# {env}: {exc}")
            reports.append(report)
            continue
        events = parse(text)
        if events and env in raw_files:
            report.until = max(e.time for e in events)
        if not events:
            report.error = EMPTY_PULL
        else:
            report.state, report.state_since = session_state(events)
            report.session_created = session_created(events)
        if events and events_since:
            report.events = event_lines(events, events_since)
        if events and table:
            rows = [r for r in build_incidents(events, report.until) if r.flag >= since]
            window = max((report.until - since).total_seconds(), 1)
            mix: Counter[str] = Counter()
            for r in rows:
                mix.update(r.failed_steps)
            report.incidents = (
                rows if params.format == "side" else sort_incidents(rows, params.sort, params.top)
            )
            report.total_outage_seconds = sum(r.outage_seconds for r in rows)
            report.downtime_percent = round(report.total_outage_seconds / window * 100, 1)
            report.failed_attempts = sum(mix.values())
            report.failed_step_mix = dict(mix.most_common())
            report.scraper_stops = [e.time for e in events if e.kind == "SCRAPER_STOP" and e.time >= since]
        ctx.log(f"# {env}: {len(events)} events" + (f", {len(report.incidents)} incidents" if table else ""))
        reports.append(report)

    if all(r.error for r in reports):
        unique = list(dict.fromkeys(r.error for r in reports))
        raise ToolError(unique[0] if len(unique) == 1 else "; ".join(f"{r.env}: {r.error}" for r in reports))
    return Report(
        now=now,
        mode="events" if events_since else "incidents",
        format=params.format,
        sort=params.sort,
        top=params.top,
        full=bool(events_since and params.full),
        envs=reports,
    )


def clock(value: datetime, ref: datetime) -> str:
    """A time of day, with its date when it is not the same day as `ref`."""
    return value.strftime("%H:%M:%S" if value.date() == ref.date() else "%m-%d %H:%M:%S")


def status(r: EnvReport) -> str:
    """One sentence on the session now: logged in and how old the session is, or down and for how long."""
    if r.state_since is None:
        return "No session state in the pull"
    since = clock(r.state_since, r.until)
    if r.state == "down":
        return f"Down since {since} ({dur((r.until - r.state_since).total_seconds())} so far)"
    if r.session_created is None:
        return f"Logged in since {since}, session age unknown (no login saved in the pull)"
    return f"Logged in since {since}, session life {dur((r.until - r.session_created).total_seconds())}"


def headline(r: EnvReport) -> str:
    window = dur((r.until - r.since).total_seconds())
    stops = ", ".join(s.strftime("%m-%d %H:%M") for s in r.scraper_stops)
    return (
        f"{status(r)}. {len(r.incidents)} incidents, {dur(r.total_outage_seconds)} down "
        f"({r.downtime_percent}% of {window}), {r.failed_attempts} failed relogin attempts"
        + (f", scraper stops: {stops}" if stops else "")
    )


def _mix_line(r: EnvReport) -> list[str]:
    if not r.failed_step_mix:
        return []
    return ["Failed-step mix: " + ", ".join(f"{n}x {w}" for w, n in r.failed_step_mix.items())]


def render(report: Report) -> str:
    out = [f"now {report.now:%Y-%m-%d %H:%M:%S} Manila"]
    boxes: list[tuple[str, Sequence[Incident]]] = []
    sides: dict[str, list[Incident]] = {}
    for r in report.envs:
        name = r.env.upper()
        if r.error:
            out.append(f"\n### {name} - {r.error}")
        elif report.mode == "events":
            out.append(f"\n### {name} - events since {(r.events_since or r.since):%m-%d %H:%M:%S}")
            out += [f"{e.time:%m-%d %H:%M:%S}  {e.text}" for e in r.events] or ["no change"]
            out.append(f"{headline(r)}." if report.full else f"{status(r)}.")
        elif report.format == "box":
            boxes.append((f"\n**{name}:** {headline(r)}.", r.incidents))
        elif report.format == "side":
            out.append(f"\n{name}: {headline(r)}")
            sides[r.env] = r.incidents
        else:
            out += [f"\n### {name} - {headline(r)}", *_mix_line(r), ""]
            out.append(
                render_plain(r.incidents) + "\n" + LEGEND
                if report.format == "plain"
                else render_md(r.incidents)
            )
    if boxes:
        out.append(render_boxes(boxes))
    if sides:
        if len(sides) == 2:
            pairs = pair_incidents(sides["qat"], sides["prod"], report.sort, report.top)
            out += ["", render_side(pairs), SIDE_FOOTNOTE]
        else:
            rows = next(iter(sides.values()))
            out += ["", render_plain(sort_incidents(rows, report.sort, report.top)), LEGEND]
    return "\n".join(out)


TOOL = Tool(
    name="bdo-incidents",
    description=(
        "Summarise BDO session hangs, relogin outages and session-refresh activity from the PMS SLS logs "
        "(QAT, PROD or both) as a per-incident table, or list what is new since a time for cron-style "
        "checks. "
        "Read-only; pulls through sls-logs and never includes raw log text."
    ),
    input=Input,
    run=run,
    render=render,
    examples=(
        "shed bdo-incidents --env both --hours 24 --sort outage --top 6",
        'shed bdo-incidents --env qat --since "2026-10-26 09:00" --format plain',
        'shed bdo-incidents --env both --events-since "2026-10-26 16:30"',
        "shed bdo-incidents --env both --events-hours 0.25 --full  # what the dashboard job runs",
        "shed bdo-incidents --raw qat=saved-qat.txt --json",
    ),
    version="0.2.0",
)
