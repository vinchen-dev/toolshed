"""Turn SLS console CSV exports (the ~/Downloads/50a50-*.csv files) into the lines sls-logs prints.

Use it on exports you already have instead of querying SLS again. The output is the text that
bdo-incidents --raw reads, and --raw also accepts a .csv directly.
"""

from __future__ import annotations

import csv
import glob
import re
import sys
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from shed import Tool, ToolContext, ToolError
from shed.lib.manila import format_manila, parse_manila

CONTENT_COLUMN = "content"
EPOCH_COLUMN = "__time__"
ISO_COLUMN = "_time_"
CONTAINER_COLUMNS = ("__tag__:_container_name_", "_container_name_")
_FRACTION = re.compile(r"(\.\d{6})\d+")  # fromisoformat takes at most six fractional digits


class Input(BaseModel):
    path: list[str] = Field(
        description="CSV export(s) to read; globs and ~ allowed, relative to the current directory"
    )
    grep: list[str] = Field(default_factory=list, description="Keep lines containing any of these substrings")
    container: list[str] = Field(default_factory=list, description="Keep only these container names")
    start: str | None = Field(None, description="Drop lines before this Manila time 'YYYY-MM-DD HH:MM[:SS]'")
    end: str | None = Field(None, description="Drop lines after this Manila time 'YYYY-MM-DD HH:MM[:SS]'")
    limit: int = Field(0, ge=0, description="Maximum lines to return, oldest first (0 = all)")

    @field_validator("start", "end")
    @classmethod
    def _check_time(cls, value: str | None) -> str | None:
        if value is not None:
            parse_manila(value)
        return value


class LogLine(BaseModel):
    time: str
    container: str
    content: str


class CsvResult(BaseModel):
    files: list[str]
    rows: int
    """Rows read across all files, before de-duplication and filters."""
    duplicates: int
    count: int
    truncated: bool
    start: str | None
    end: str | None
    lines: list[LogLine]


def resolve_paths(patterns: list[str], cwd: Path) -> list[Path]:
    """Expand ~ and globs against cwd; every pattern must match at least one file."""
    paths: list[Path] = []
    for pattern in patterns:
        expanded = Path(pattern).expanduser()
        base = expanded if expanded.is_absolute() else cwd / expanded
        matches = sorted(Path(m) for m in glob.glob(str(base)))
        if not matches:
            raise ToolError(f"no file matches {pattern}")
        paths += [m for m in matches if m.is_file()]
    return paths


def _epoch(row: dict[str, str], path: Path) -> float:
    raw = row.get(EPOCH_COLUMN)
    if raw:
        return float(raw)
    iso = row.get(ISO_COLUMN)
    if iso:
        return datetime.fromisoformat(_FRACTION.sub(r"\1", iso)).timestamp()
    raise ToolError(f"{path}: a row has neither {EPOCH_COLUMN} nor {ISO_COLUMN}")


def read_export(path: Path) -> list[tuple[float, LogLine]]:
    """Read one export as (epoch, line) pairs in file order."""
    csv.field_size_limit(sys.maxsize)
    try:
        with path.open(newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            columns = reader.fieldnames or []
            if CONTENT_COLUMN not in columns or not any(c in columns for c in (EPOCH_COLUMN, ISO_COLUMN)):
                raise ToolError(
                    f"{path}: not an SLS export (needs '{CONTENT_COLUMN}' and '{EPOCH_COLUMN}' columns)"
                )
            out: list[tuple[float, LogLine]] = []
            for row in reader:
                epoch = _epoch(row, path)
                container = next((row[c] for c in CONTAINER_COLUMNS if row.get(c)), "-")
                out.append(
                    (
                        epoch,
                        LogLine(time=format_manila(epoch), container=container, content=row[CONTENT_COLUMN]),
                    )
                )
            return out
    except OSError as exc:
        raise ToolError(f"cannot read {path}: {exc.strerror or exc}") from exc


def run(params: Input, ctx: ToolContext) -> CsvResult:
    paths = resolve_paths(params.path, ctx.cwd)
    start = parse_manila(params.start).timestamp() if params.start else None
    end = parse_manila(params.end).timestamp() if params.end else None

    rows = 0
    seen: set[tuple[float, str, str]] = set()
    kept: list[tuple[float, LogLine]] = []
    for path in paths:
        entries = read_export(path)
        rows += len(entries)
        ctx.log(f"# {path}: {len(entries)} rows")
        for epoch, line in entries:
            key = (epoch, line.container, line.content)
            if key in seen:
                continue
            seen.add(key)
            kept.append((epoch, line))
    duplicates = rows - len(kept)

    kept.sort(key=lambda pair: pair[0])
    lines = [
        line
        for epoch, line in kept
        if (start is None or epoch >= start)
        and (end is None or epoch <= end)
        and (not params.container or line.container in params.container)
        and (not params.grep or any(g in line.content for g in params.grep))
    ]
    truncated = bool(params.limit) and len(lines) > params.limit
    if truncated:
        lines = lines[: params.limit]
    ctx.log(f"# {len(lines)} lines" + (f" (stopped at --limit {params.limit})" if truncated else ""))
    return CsvResult(
        files=[str(p) for p in paths],
        rows=rows,
        duplicates=duplicates,
        count=len(lines),
        truncated=truncated,
        start=lines[0].time if lines else None,
        end=lines[-1].time if lines else None,
        lines=lines,
    )


def render(result: CsvResult) -> str:
    if not result.lines:
        return "(no lines)"
    return "\n".join(f"{line.time} [{line.container}] {line.content}" for line in result.lines)


TOOL = Tool(
    name="sls-csv",
    description=(
        "Read SLS console CSV exports (the 50a50-*.csv downloads) and print them as 'time [container] "
        "content' lines, oldest first, de-duplicated across overlapping files. Same shape as sls-logs, so "
        "use it on exports you already have instead of querying SLS; bdo-incidents --raw reads the output."
    ),
    input=Input,
    run=run,
    render=render,
    examples=(
        'shed sls-csv --path "~/Downloads/50a50-*.csv" --grep "Re-login" --grep "Invalidated"',
        'shed sls-csv --path export.csv --container pms-session-manager-bdo --start "2026-10-16 03:00"',
        "shed sls-csv --path export.csv > pull.txt && shed bdo-incidents --raw qat=pull.txt",
    ),
)
