"""Report what is new in local log files: records since a time, or since the last run, filtered and redacted.

Built for the PMS-Auto-Deposit logs ('YYYY-MM-DD HH:MM:SS | LEVEL | CHANNEL | where - message'), but any
file whose records start with that timestamp works. Lines without one, such as tracebacks, attach to the
record before them. OTP digits and credential lines are always redacted.
"""

from __future__ import annotations

import glob
import json
import os
import re
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from shed import Tool, ToolContext, ToolError
from shed.lib import manila

from .redact import redact

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_HEAD = re.compile(r"^(?:\[\w+\] \[\w+\] )?(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)(?:[.,]\d+)?(.*)$")
_LOGURU = re.compile(r"^ \| (\S+)\s*\| (\S+)\s*\| (\S+?) - (.*)$", re.DOTALL)
_SHORT = re.compile(r"^ \| (\S+)\s*\| (.*)$", re.DOTALL)  # scraper lines: timestamp | LEVEL | message


def now_manila() -> datetime:
    """Naive Manila wall-clock time, like the log timestamps. Patched in tests."""
    return manila.now_manila().replace(tzinfo=None, microsecond=0)


class Input(BaseModel):
    path: list[str] = Field(
        description="Log files to read; globs and ~ allowed, relative to the current directory"
    )
    minutes: int = Field(10, ge=1, description="Look back N minutes; --since overrides it")
    since: str | None = Field(
        None, description="Keep records at or after this Manila time 'YYYY-MM-DD HH:MM[:SS]'"
    )
    cursor: str | None = Field(
        None,
        description=(
            "State file for repeated checks: the first run uses --since/--minutes, later runs read only what "
            "was appended since, then the file is updated"
        ),
    )
    grep: list[str] = Field(
        default_factory=list, description="Keep records containing any of these substrings"
    )
    ignore_case: bool = Field(False, description="Make --grep case-insensitive")
    level: list[str] = Field(
        default_factory=list, description="Keep only these levels, e.g. WARNING ERROR CRITICAL"
    )
    limit: int = Field(200, ge=1, description="Keep at most N records per file, the newest ones")

    @field_validator("since")
    @classmethod
    def _check_time(cls, value: str | None) -> str | None:
        if value is not None:
            manila.parse_manila(value)
        return value


class Record(BaseModel):
    time: str | None
    """Timestamp as written in the file; None for lines before the first timestamp."""
    level: str | None = None
    channel: str | None = None
    where: str | None = None
    message: str
    """First line of the record after the prefix."""
    text: str
    """The whole record, continuation lines included, ANSI stripped and redacted."""
    repeats: int = 1
    """How many identical records followed each other; they are folded into one."""


class FileReport(BaseModel):
    path: str
    exists: bool = True
    skipped: bool = False
    """True when the file was not read because it was last modified before the window."""
    read_from: int = 0
    read_to: int = 0
    count: int = 0
    truncated: bool = False
    records: list[Record] = []


class WatchResult(BaseModel):
    now: str
    since: str | None
    cursor: str | None
    files: list[FileReport]
    total: int


def resolve_paths(patterns: list[str], cwd: Path) -> list[Path]:
    """Expand ~ and globs against cwd. A pattern that matches nothing yields its literal path."""
    paths: list[Path] = []
    for pattern in patterns:
        expanded = Path(pattern).expanduser()
        base = expanded if expanded.is_absolute() else cwd / expanded
        matches = sorted(Path(m) for m in glob.glob(str(base))) or [base]
        paths += matches
    return paths


def parse_records(text: str) -> list[Record]:
    """Split log text into records; lines without a leading timestamp join the record before them."""
    records: list[Record] = []
    chunks: list[list[str]] = []
    for line in _ANSI.sub("", text).split("\n"):
        if _HEAD.match(line) or not chunks:
            chunks.append([line])
        else:
            chunks[-1].append(line)
    for lines in chunks:
        body = "\n".join(lines).rstrip("\n")
        if not body.strip():
            continue
        head = _HEAD.match(lines[0])
        if head is None:
            records.append(Record(time=None, message=lines[0], text=body))
            continue
        rest = head.group(2)
        level = channel = where = None
        if fields := _LOGURU.match(rest):
            level, channel, where, message = fields.groups()
        elif short := _SHORT.match(rest):
            level, message = short.groups()
        else:
            message = rest.strip(" |-")
        records.append(
            Record(time=head.group(1), level=level, channel=channel, where=where, message=message, text=body)
        )
    return records


def load_cursor(path: Path) -> dict[str, dict[str, int]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise ToolError(f"cannot read cursor file {path}: {exc}") from exc
    return data if isinstance(data, dict) else {}


def save_cursor(path: Path, state: dict[str, dict[str, int]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=1)
    os.replace(tmp, path)


def read_new_bytes(path: Path, offset: int) -> tuple[str, int, int]:
    """Return (text, start, end): complete lines from `offset`, restarting at 0 if the file shrank."""
    size = path.stat().st_size
    start = 0 if size < offset else offset
    with path.open("rb") as fh:
        fh.seek(start)
        chunk = fh.read()
    cut = chunk.rfind(b"\n")
    if cut < 0:
        return "", start, start
    return chunk[: cut + 1].decode("utf-8", errors="replace"), start, start + cut + 1


def fold_repeats(records: list[Record]) -> list[Record]:
    """Fold identical consecutive records into one with a count (duplicate handlers write the same line)."""
    folded: list[Record] = []
    for r in records:
        if folded and folded[-1].text == r.text:
            folded[-1].repeats += 1
        else:
            folded.append(r)
    return folded


def _keep(record: Record, params: Input, since: datetime | None) -> bool:
    if since is not None and (
        record.time is None or manila.parse_manila(record.time).replace(tzinfo=None) < since
    ):
        return False
    if params.level and (record.level or "").upper() not in {lvl.upper() for lvl in params.level}:
        return False
    if params.grep:
        haystack = record.text.lower() if params.ignore_case else record.text
        needles = [g.lower() for g in params.grep] if params.ignore_case else params.grep
        if not any(n in haystack for n in needles):
            return False
    return True


def run(params: Input, ctx: ToolContext) -> WatchResult:
    now = now_manila()
    window = (
        manila.parse_manila(params.since).replace(tzinfo=None)
        if params.since
        else now - timedelta(minutes=params.minutes)
    )
    cursor_path = (ctx.cwd / Path(params.cursor).expanduser()) if params.cursor else None
    state = load_cursor(cursor_path) if cursor_path else {}

    reports: list[FileReport] = []
    for path in resolve_paths(params.path, ctx.cwd):
        key = str(path.resolve())
        report = FileReport(path=str(path))
        if not path.is_file():
            report.exists = False
            reports.append(report)
            continue
        stored = state.get(key)
        continuing = stored is not None
        since = None if continuing else window
        if (
            since is not None
            and datetime.fromtimestamp(path.stat().st_mtime, manila.MANILA).replace(tzinfo=None) < since
        ):
            report.skipped = True
            reports.append(report)
            continue
        text, report.read_from, report.read_to = read_new_bytes(path, stored["offset"] if stored else 0)
        records = [r for r in parse_records(text) for _ in [0] if _keep(r, params, since)]
        for r in records:
            r.text = redact(r.text)
            r.message = redact(r.message)
        records = fold_repeats(records)
        report.truncated = len(records) > params.limit
        report.records = records[-params.limit :]
        report.count = len(report.records)
        state[key] = {"offset": report.read_to, "size": path.stat().st_size}
        ctx.log(f"# {path}: {report.count} records from bytes {report.read_from}-{report.read_to}")
        reports.append(report)

    if not any(r.exists for r in reports):
        raise ToolError("none of the paths exist: " + ", ".join(r.path for r in reports))
    if cursor_path:
        save_cursor(cursor_path, state)
    return WatchResult(
        now=now.strftime("%Y-%m-%d %H:%M:%S"),
        since=None
        if params.cursor and all(r.read_from for r in reports if r.exists)
        else window.strftime("%Y-%m-%d %H:%M:%S"),
        cursor=str(cursor_path) if cursor_path else None,
        files=reports,
        total=sum(r.count for r in reports),
    )


def render(result: WatchResult) -> str:
    out = [
        f"now {result.now} Manila" + (f", since {result.since}" if result.since else ", since the last check")
    ]
    skipped = [Path(f.path).name for f in result.files if f.skipped]
    for f in result.files:
        if not f.exists:
            out.append(f"\n== {f.path}: missing")
        elif not f.skipped:
            note = " (older records cut by --limit)" if f.truncated else ""
            out.append(f"\n== {f.path}: {f.count} records, bytes {f.read_from}-{f.read_to}{note}")
            out += [r.text + (f"  (x{r.repeats})" if r.repeats > 1 else "") for r in f.records]
    if skipped:
        names = ", ".join(skipped) if len(skipped) <= 3 else f"{len(skipped)} files"
        out.append(f"\n== not modified in the window: {names}")
    if result.total == 0:
        out.append("\nno change")
    return "\n".join(out)


TOOL = Tool(
    name="log-watch",
    description=(
        "Report what is new in local log files since a time or since the last run (with --cursor), filtered "
        "by level or substring, with OTP digits and credential lines redacted. Use it to observe a running "
        "test or service through its log file instead of screenshots or a terminal."
    ),
    input=Input,
    run=run,
    render=render,
    examples=(
        'shed log-watch --path "logs/*_auto_deposit.log" --minutes 15 --grep "token CHANGED"',
        'shed log-watch --path logs/20261019_errors.log --since "2026-10-19 17:30" --level ERROR',
        "shed log-watch --path logs/20261019_auto_deposit.log --cursor /tmp/bdo-test.cursor",
    ),
)
