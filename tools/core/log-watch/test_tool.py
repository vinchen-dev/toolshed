from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

import pytest

from shed import ToolContext, ToolError
from shed.registry import load_tool, load_tool_module

module = load_tool_module("log-watch")
TOOL = load_tool("log-watch")
NOW = datetime(2026, 10, 19, 18, 0, 0)
LINES = [
    "2026-10-19 17:40:00 | INFO     | SYSTEM       | src.utils.logger:setup_logger:201 - Logger initialized",
    "2026-10-19 17:47:33 | INFO     | BDO          | src.web.authenticators.bdo:refresh:591 - "
    "BDO token CHANGED (page visit)",
    "2026-10-19 17:50:00 | ERROR    | BDO          | src.web.scrapers.bdo:fetch:10 - GET page 1 error",
    "Traceback (most recent call last):",
    '  File "x.py", line 1, in <module>',
    "RuntimeError: boom",
    "2026-10-19 17:51:00 | INFO     | BDO          | src.web.auth.otp:submit:5 - "
    "OTP value=123456 submitted, 'otp': '654321'",
    "2026-10-19 17:52:00 | DEBUG    | BDO          | src.web.auth.login:fill:7 - "
    "credentials: user=abc password=hunter2",
    "[INFO] [BDO] 2026-10-19 17:55:00 | INFO | Fetch attempt 1/1",
]


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "now_manila", lambda: NOW)


@pytest.fixture
def log(tmp_path: Path) -> Path:
    path = tmp_path / "20261019_auto_deposit.log"
    path.write_text("\n".join(LINES) + "\n", encoding="utf-8")
    return path


def ctx(cwd: Path) -> ToolContext:
    return ToolContext(cwd=cwd, log=lambda _: None)


def test_parses_records_and_attaches_continuation_lines(log: Path) -> None:
    result = TOOL.run(TOOL.input(path=[log.name], minutes=60), ctx(log.parent))
    report = result.files[0]
    assert report.exists and report.count == 6 and result.total == 6
    error = report.records[2]
    assert (error.time, error.level, error.channel, error.where) == (
        "2026-10-19 17:50:00",
        "ERROR",
        "BDO",
        "src.web.scrapers.bdo:fetch:10",
    )
    assert error.message == "GET page 1 error"
    assert error.text.endswith("RuntimeError: boom")
    scraper = report.records[-1]
    assert (scraper.time, scraper.level, scraper.channel, scraper.message) == (
        "2026-10-19 17:55:00",
        "INFO",
        None,
        "Fetch attempt 1/1",
    )


def test_redacts_otps_and_credential_lines(log: Path) -> None:
    result = TOOL.run(TOOL.input(path=[log.name], minutes=60), ctx(log.parent))
    texts = [r.text for r in result.files[0].records]
    assert "OTP value=*** submitted, 'otp': '***'" in texts[3]
    assert texts[4].endswith("src.web.auth.login:fill:7 - [redacted: credentials]")
    assert "hunter2" not in "\n".join(texts) and "123456" not in "\n".join(texts)


def test_window_level_grep_and_limit(log: Path) -> None:
    c = ctx(log.parent)
    assert TOOL.run(TOOL.input(path=[log.name], minutes=12), c).total == 4  # 17:48 onwards
    assert TOOL.run(TOOL.input(path=[log.name], since="2026-10-19 17:51"), c).total == 3
    errors = TOOL.run(TOOL.input(path=[log.name], minutes=60, level=["error", "CRITICAL"]), c)
    assert [r.message for r in errors.files[0].records] == ["GET page 1 error"]
    grep = TOOL.run(TOOL.input(path=[log.name], minutes=60, grep=["token changed"], ignore_case=True), c)
    assert grep.total == 1 and "page visit" in grep.files[0].records[0].text
    limited = TOOL.run(TOOL.input(path=[log.name], minutes=60, limit=2), c)
    assert limited.files[0].truncated is True
    assert [r.time for r in limited.files[0].records] == ["2026-10-19 17:52:00", "2026-10-19 17:55:00"]


def test_cursor_reports_only_appended_lines_and_handles_truncation(log: Path, tmp_path: Path) -> None:
    c = ctx(log.parent)
    cursor = tmp_path / "state" / "watch.json"
    first = TOOL.run(TOOL.input(path=[log.name], minutes=5, cursor=str(cursor)), c)
    assert first.total == 1 and first.since == "2026-10-19 17:55:00"  # the window applies on the first run
    assert json.loads(cursor.read_text())[str(log.resolve())]["offset"] == log.stat().st_size

    with log.open("a", encoding="utf-8") as fh:
        fh.write("2026-10-19 17:58:00 | WARNING  | BDO          | src.web.x:y:1 - new line\npartial")
    second = TOOL.run(TOOL.input(path=[log.name], minutes=5, cursor=str(cursor)), c)
    assert [r.message for r in second.files[0].records] == ["new line"] and second.since is None
    assert second.files[0].read_to == log.stat().st_size - len("partial")

    log.write_text(
        "2026-10-19 17:59:00 | INFO     | BDO          | src.web.x:y:1 - after rotation\n", encoding="utf-8"
    )
    third = TOOL.run(TOOL.input(path=[log.name], cursor=str(cursor)), c)
    assert third.files[0].read_from == 0 and [r.message for r in third.files[0].records] == ["after rotation"]


def test_globs_mtime_skip_and_missing_files(log: Path, tmp_path: Path) -> None:
    old = tmp_path / "20260923_auto_deposit.log"
    old.write_text("2026-09-23 01:00:00 | INFO     | SYSTEM       | a:b:1 - old\n", encoding="utf-8")
    stale = time.mktime(datetime(2026, 9, 23, 1, 0).timetuple())
    os.utime(old, (stale, stale))
    result = TOOL.run(TOOL.input(path=["*_auto_deposit.log", "missing.log"], minutes=60), ctx(tmp_path))
    by_name = {Path(f.path).name: f for f in result.files}
    assert by_name["20260923_auto_deposit.log"].skipped is True
    assert by_name["20261019_auto_deposit.log"].count == 6
    assert by_name["missing.log"].exists is False
    assert TOOL.render is not None
    rendered = TOOL.render(result)
    assert "missing.log: missing" in rendered and "not modified in the window" in rendered

    with pytest.raises(ToolError, match="none of the paths exist"):
        TOOL.run(TOOL.input(path=["nope.log"]), ctx(tmp_path))


def test_render_says_no_change(log: Path) -> None:
    result = TOOL.run(TOOL.input(path=[log.name], minutes=60, grep=["never there"]), ctx(log.parent))
    assert TOOL.render is not None
    assert TOOL.render(result).rstrip().endswith("no change")


def test_identical_consecutive_records_fold_into_a_count(tmp_path: Path) -> None:
    prefix = "| INFO     | PNB_SESSION  | src.web.session_manager.pnb_impl:_do_relogin"
    line = f"2026-10-19 17:58:00 {prefix}:412 - PNB: Re-login"
    other = f"2026-10-19 17:58:01 {prefix}:413 - next"
    path = tmp_path / "dup.log"
    path.write_text("\n".join([line, line, line, other, line]) + "\n", encoding="utf-8")
    result = TOOL.run(TOOL.input(path=[path.name], minutes=60), ctx(tmp_path))
    records = result.files[0].records
    assert [(r.message, r.repeats) for r in records] == [
        ("PNB: Re-login", 3),
        ("next", 1),
        ("PNB: Re-login", 1),
    ]
    assert TOOL.render is not None
    assert "PNB: Re-login  (x3)" in TOOL.render(result)
