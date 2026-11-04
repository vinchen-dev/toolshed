from __future__ import annotations

import importlib
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from shed import ToolContext
from shed.cron import RunRecord
from shed.lib.manila import MANILA
from shed.registry import load_tool, load_tool_module

module = load_tool_module("bdo-incidents")
view = importlib.import_module(f"{module.__package__}.view")
TOOL = load_tool("bdo-incidents")
FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 25, 12, 0, 0)
PAGE_NOW = datetime(2026, 10, 25, 12, 30, 0, tzinfo=MANILA)
CTX = ToolContext(cwd=FIXTURES, log=lambda _: None)


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "now_manila", lambda: NOW)


def record(events_since: str, started: datetime, *, full: bool = True, ok: bool = True) -> RunRecord:
    params = TOOL.input(
        raw=["qat=qat.txt", "prod=prod.txt"], env="both", hours=4, events_since=events_since, full=full
    )
    report = TOOL.run(params, CTX)
    return RunRecord(
        job="bdo-session",
        tool="bdo-incidents",
        args={},
        started=started,
        finished=started + timedelta(seconds=2),
        duration_ms=1900,
        ok=ok,
        error=None if ok else "SLS query failed",
        summary="x",
        result=None if not ok else report.model_dump(mode="json"),
    )


def test_timeline_merges_runs_without_duplicates() -> None:
    newer = record("2026-10-25 10:00", PAGE_NOW)
    older = record("2026-10-25 09:30", PAGE_NOW - timedelta(minutes=10))
    found = view.reports([newer, older])
    entries = view.timeline(found, datetime(2026, 10, 24, 12, 0))
    keys = {(e.env, e.time, e.kind, e.text) for e in entries}
    assert len(keys) == len(entries)
    assert entries[0].time >= entries[-1].time
    solo = view.timeline(view.reports([older]), datetime(2026, 10, 24, 12, 0))
    assert len(entries) == len(solo)  # the newer run adds nothing the older window did not cover
    assert view.timeline(found, datetime(2026, 10, 25, 11, 30)) == [
        e for e in entries if e.time >= datetime(2026, 10, 25, 11, 30)
    ]


def test_page_shows_cards_incidents_and_timeline() -> None:
    runs = [
        record("2026-10-25 10:00", PAGE_NOW),
        record("2026-10-25 09:30", PAGE_NOW - timedelta(minutes=10)),
    ]
    html = view.view("bdo-session", runs, PAGE_NOW, {})
    assert "<h1>BDO sessions" in html and 'href="/jobs/bdo-session"' in html
    assert html.count('<article class="card">') == 2 and ">QAT<" in html and ">PROD<" in html
    assert "<th>flag</th>" in html and "relogin SAVED" in html
    assert '<span class="pill fail">invalidated</span>' in html
    assert '<a href="?hours=6&amp;env=both">6h</a>' in html and "<b>24h</b>" in html
    assert "2 runs kept" in html


def test_page_filters_by_env_and_window() -> None:
    runs = [record("2026-10-25 09:30", PAGE_NOW)]
    prod = view.view("bdo-session", runs, PAGE_NOW, {"env": "prod"})
    assert prod.count('<article class="card">') == 1 and ">PROD<" in prod and ">QAT<" not in prod
    assert "<b>prod</b>" in prod and '<a href="?hours=24&amp;env=both">both</a>' in prod
    hour = view.view("bdo-session", runs, PAGE_NOW, {"hours": "1", "env": "nope"})
    expected = view.timeline(view.reports(runs), datetime(2026, 10, 25, 11, 30))
    assert 0 < len(expected) < 5 and f"{len(expected)} events in the last 1 h" in hour
    assert "scraper stop" in hour and "<b>both</b>" in hour
    assert "<b>24h</b>" in view.view("bdo-session", runs, PAGE_NOW, {"hours": "7"})


def test_failed_latest_run_falls_back_to_the_last_good_one() -> None:
    runs = [
        record("2026-10-25 10:00", PAGE_NOW, ok=False),
        record("2026-10-25 09:30", PAGE_NOW - timedelta(minutes=10)),
    ]
    html = view.view("bdo-session", runs, PAGE_NOW, {})
    assert "The latest run failed: SLS query failed" in html and "<th>flag</th>" in html
    assert "last pull <b>just now</b> (1.9 s, failed)" in html


def test_events_only_runs_explain_the_missing_table() -> None:
    html = view.view("bdo-session", [record("2026-10-25 09:30", PAGE_NOW, full=False)], PAGE_NOW, {})
    assert "<code>full = true</code>" in html and "<th>flag</th>" not in html
    assert view.view("bdo-session", [], PAGE_NOW, {}).count("No successful run yet") == 1
