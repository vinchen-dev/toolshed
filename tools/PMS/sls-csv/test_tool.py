from __future__ import annotations

import csv
from pathlib import Path

import pytest

from shed import ToolContext, ToolError
from shed.registry import load_tool

TOOL = load_tool("sls-csv")
COLUMNS = ["__tag__:_container_name_", "__time__", "_source_", "_time_", "content"]
MANAGER = "pms-session-manager-bdo"
SCRAPER = "pms-auto-deposit"
# 2026-10-16 03:30:26 Manila is epoch 1792092626
ROWS = [
    (SCRAPER, "1792092626", "stdout", "2026-10-16T03:30:26.910361572+08:00", "[INFO] [BDO] GET p1 HTTP 200"),
    (
        MANAGER,
        "1792092649",
        "stderr",
        "2026-10-16T03:30:49.343442043+08:00",
        "\x1b[32m2026-10-16 03:30:49\x1b[0m | INFO | Re-login attempt starting",
    ),
    (SCRAPER, "1792092600", "stdout", "2026-10-16T03:30:00.000000000+08:00", "[INFO] [BDO] earlier line"),
    (SCRAPER, "1792092626", "stdout", "2026-10-16T03:30:26.910361572+08:00", "[INFO] [BDO] GET p1 HTTP 200"),
]


def write_csv(path: Path, rows: list[tuple[str, ...]], columns: list[str] = COLUMNS) -> Path:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(columns)
        writer.writerows(rows)
    return path


@pytest.fixture
def export(tmp_path: Path) -> Path:
    return write_csv(tmp_path / "50a50-abc.csv", ROWS)


def ctx(cwd: Path) -> ToolContext:
    return ToolContext(cwd=cwd, log=lambda _: None)


def test_sorts_dedupes_and_renders_like_sls_logs(export: Path) -> None:
    result = TOOL.run(TOOL.input(path=[export.name]), ctx(export.parent))
    assert (result.rows, result.duplicates, result.count) == (4, 1, 3)
    assert [line.time for line in result.lines] == [
        "2026-10-16 03:30:00",
        "2026-10-16 03:30:26",
        "2026-10-16 03:30:49",
    ]
    assert result.lines[1].container == SCRAPER and result.lines[2].container == MANAGER
    assert (result.start, result.end) == ("2026-10-16 03:30:00", "2026-10-16 03:30:49")
    assert TOOL.render is not None
    rendered = TOOL.render(result).splitlines()
    assert rendered[0] == "2026-10-16 03:30:00 [pms-auto-deposit] [INFO] [BDO] earlier line"
    assert rendered[2].startswith("2026-10-16 03:30:49 [pms-session-manager-bdo] \x1b[32m2026-10-16 03:30:49")


def test_filters_by_container_grep_and_window(export: Path) -> None:
    c = ctx(export.parent)
    assert [x.content for x in TOOL.run(TOOL.input(path=[export.name], container=[MANAGER]), c).lines] == [
        ROWS[1][4]
    ]
    assert TOOL.run(TOOL.input(path=[export.name], grep=["Re-login", "nope"]), c).count == 1
    windowed = TOOL.run(
        TOOL.input(path=[export.name], start="2026-10-16 03:30:10", end="2026-10-16 03:30:30"), c
    )
    assert [x.time for x in windowed.lines] == ["2026-10-16 03:30:26"]
    limited = TOOL.run(TOOL.input(path=[export.name], limit=2), c)
    assert limited.count == 2 and limited.truncated is True


def test_merges_overlapping_exports_and_expands_globs(tmp_path: Path) -> None:
    write_csv(tmp_path / "50a50-one.csv", ROWS[:2])
    write_csv(tmp_path / "50a50-two.csv", ROWS[1:])
    result = TOOL.run(TOOL.input(path=["50a50-*.csv"]), ctx(tmp_path))
    assert len(result.files) == 2
    assert (result.rows, result.duplicates, result.count) == (5, 2, 3)


def test_iso_time_fallback_when_epoch_column_is_missing(tmp_path: Path) -> None:
    columns = ["__tag__:_container_name_", "_time_", "content"]
    path = write_csv(tmp_path / "e.csv", [(MANAGER, "2026-10-16T03:30:49.343442043+08:00", "x")], columns)
    result = TOOL.run(TOOL.input(path=[str(path)]), ctx(tmp_path))
    assert result.lines[0].time == "2026-10-16 03:30:49"


@pytest.mark.parametrize(
    ("columns", "rows", "match"),
    [
        (["a", "b"], [("1", "2")], "not an SLS export"),
        (["__time__", "content"], [("", "no time")], "neither __time__ nor _time_"),
    ],
)
def test_rejects_files_that_are_not_exports(
    tmp_path: Path, columns: list[str], rows: list[tuple[str, ...]], match: str
) -> None:
    path = write_csv(tmp_path / "bad.csv", rows, columns)
    with pytest.raises(ToolError, match=match):
        TOOL.run(TOOL.input(path=[str(path)]), ctx(tmp_path))


def test_missing_file_and_bad_time_are_errors(tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="no file matches"):
        TOOL.run(TOOL.input(path=["nope-*.csv"]), ctx(tmp_path))
    with pytest.raises(ValueError, match="bad time"):
        TOOL.input(path=["x.csv"], start="16/10/2026")
