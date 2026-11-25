from __future__ import annotations

import importlib
import time
from datetime import datetime, timedelta
from typing import Any

from shed.cron import RunRecord
from shed.registry import load_tool_module

module = load_tool_module("agent-watch")
fakes = importlib.import_module(f"{module.__package__}.fakes")
view = importlib.import_module(f"{module.__package__}.view")
models = importlib.import_module(f"{module.__package__}.models")
NOW, MODEL, OTHER = fakes.NOW, fakes.MODEL, fakes.OTHER
ago, result, reading, finished, spawning = (
    fakes.ago,
    fakes.result,
    fakes.reading,
    fakes.finished,
    fakes.spawning,
)

NOTES = "/tmp/work/notes"


def record(report: Any, started: datetime, ok: bool = True) -> RunRecord:
    return RunRecord(
        job="agents",
        tool="agent-watch",
        args={},
        started=started,
        finished=started + timedelta(seconds=1),
        duration_ms=400,
        ok=ok,
        error=None if ok else "ps timed out after 10s",
        summary="x",
        result=report.model_dump(mode="json") if ok else None,
    )


def snapshot(home: Any, at: datetime | None = None) -> RunRecord:
    """Run the tool on the fake home as of ``at`` and wrap the result as the scheduler would record it."""
    home.probe.at = at or NOW
    return record(home.run(), home.probe.at)


def mixed(home: Any) -> None:
    """A working session with a stalled background subagent, an idle session, and one that ended."""
    home.session(pid=4412)
    launched = result(ago(minutes=12), "toolu_agent", "launched", isAsync=True, status="async_launched")
    home.transcript([*spawning(ago(minutes=12)), launched, *reading(ago(seconds=10))])
    home.subagent(
        "a111", [result(ago(minutes=8), "toolu_s0")], use_id="toolu_agent", requestShape="background"
    )
    home.session(pid=4413, sid=OTHER, status="idle", name="notes-4f", cwd=NOTES)
    home.transcript(finished(ago(hours=3)), sid=OTHER, cwd=NOTES)
    home.transcript(finished(ago(hours=5)), sid="33333333-aaaa-bbbb-cccc-000000000003", written=ago(hours=5))


def page(runs: list[RunRecord], query: dict[str, str] | None = None, now: datetime | None = None) -> str:
    return view.view("agents", runs, now or NOW + timedelta(seconds=40), query or {})


def sessions_part(html: str) -> str:
    return html[html.index("Sessions <small>") : html.index("Recent changes")]


def block_of(html: str, session_id: str) -> str:
    """The block of one session: its own row and everything listed under it."""
    start = html.index(f'id="s-{session_id[:8]}"')
    return html[
        start : html.index('<div class="group"', start) if '<div class="group"' in html[start:] else None
    ]


def test_the_page_lists_each_session_as_a_block_with_what_it_started_under_it(home: Any) -> None:
    mixed(home)
    html = page([snapshot(home)])
    assert '<h1>Agents <small><a href="/jobs/agents">agents</a></small></h1>' in html
    order = [html.index(text) for text in ("Sessions <small>", "Recent changes", "Ended in the last day")]
    assert order == sorted(order) and "Needs attention" not in html
    assert "snapshot <b>just now</b>" in html
    for count in ("1 stalled", "1 working", "1 idle", "1 ended"):
        assert count in html
    assert "<span>Model</span>" in html and MODEL in html
    assert "<span>Started</span>" in html and "<span>Agent</span>" not in html
    assert '<details id="ended">' in html, "the ended sessions stay folded"

    busy = block_of(html, fakes.SID)
    assert busy.startswith(f'id="s-{fakes.SID[:8]}" open>'), "a session that started something opens unfolded"
    head, under = busy.split("</summary>")
    assert "checkout-api-97" in head and '<span class="pill fail">1 stalled</span>' in head
    assert "<small>/tmp/work/checkout-api · claude · cli</small>" in head, "its folder and what runs it"
    assert '<span class="time">15:00</span>' in head, "when the session started"
    assert '<span class="muted">subagent · background</span>' in under
    assert '<span class="time">16:52</span>' in under, "when the subagent started"
    assert "Explore: find the retry policy" in under and "No progress for 8m" in under
    assert "notes-4f" not in busy

    alone = html[html.index("notes-4f") - 200 : html.index("notes-4f")]
    assert '<div class="group">' in alone, "a session that started nothing is a plain block with no fold"
    assert html.index("checkout-api-97") < html.index("notes-4f")
    assert "· 1 call<" in html and "1 calls" not in html, "one call is not written as a plural"


def test_a_session_with_trouble_under_it_comes_before_the_ones_without(home: Any) -> None:
    mixed(home)
    fine = "44444444-aaaa-bbbb-cccc-000000000004"
    home.session(pid=4414, sid=fine, name="aaa-app", cwd="/tmp/work/aaa")
    home.transcript(reading(ago(seconds=5)), sid=fine, cwd="/tmp/work/aaa")
    html = page([snapshot(home)])
    part = sessions_part(html)
    assert part.index("checkout-api-97") < part.index("aaa-app") < part.index("notes-4f")
    assert "3 live, 1 needs attention and comes first" in part


def test_how_long_an_item_has_been_in_its_state_spans_the_kept_runs(home: Any) -> None:
    home.session(pid=4412)
    home.transcript(reading(ago(seconds=30)))
    runs = [snapshot(home, NOW + timedelta(minutes=minutes)) for minutes in (7, 6, 0)]  # newest first
    html = page(runs, now=NOW + timedelta(minutes=7, seconds=40))
    assert "since 17:06" in sessions_part(html)
    assert "Claims busy but quiet for" in sessions_part(html), "a stalled row shows its evidence"
    changes = html[html.index("Recent changes") :]
    assert changes.count('class="change"') == 1
    assert "17:06" in changes and "working" in changes and "stalled" in changes


def test_the_change_list_is_capped(home: Any) -> None:
    home.session(pid=4412)
    home.transcript(reading(ago(seconds=30)))
    fresh, late = snapshot(home, NOW), snapshot(home, NOW + timedelta(minutes=6))
    flips = [
        (late if step % 2 else fresh).model_copy(update={"started": NOW + timedelta(minutes=step)})
        for step in range(260, 0, -1)
    ]
    html = page(flips, now=NOW + timedelta(minutes=261))
    assert html.count('class="change"') == 200
    assert "Showing the newest 200 changes." in html
    assert "Showing the newest" not in page(flips[:3], now=NOW + timedelta(minutes=261))


def test_the_state_and_project_filters_narrow_the_session_tree(home: Any) -> None:
    mixed(home)
    runs = [snapshot(home)]

    def tree(query: dict[str, str]) -> str:
        return sessions_part(page(runs, query))

    assert "notes-4f" in tree({}) and "checkout-api-97" in tree({})
    assert "subagents: 1 running, 0 finished" in tree({})
    attention = tree({"state": "attention"})
    assert "checkout-api-97" in attention and "notes-4f" not in attention
    assert "Explore: find the retry policy" in attention
    assert "notes-4f" in tree({"state": "idle"}) and "checkout-api-97" not in tree({"state": "idle"})
    assert "notes-4f" in tree({"state": "nonsense"}) and "checkout-api-97" in tree({"state": "nonsense"})
    assert "notes-4f" in tree({"project": "notes"}) and "checkout-api-97" not in tree({"project": "notes"})
    assert "No sessions match." in tree({"project": "elsewhere"})


def test_a_failed_latest_run_falls_back_to_the_last_good_snapshot(home: Any) -> None:
    mixed(home)
    good = snapshot(home)
    failed = record(None, NOW + timedelta(minutes=1), ok=False)
    html = page([failed, good], now=NOW + timedelta(minutes=1, seconds=30))
    assert (
        "The latest run failed: ps timed out after 10s. Everything below comes from the last good run."
        in html
    )
    assert "checkout-api-97" in html
    assert "No successful run yet" in page([failed]) and "No successful run yet" in page([])


def test_a_quiet_machine_says_nothing_needs_attention(home: Any) -> None:
    home.session(pid=4413, status="idle", name="notes-4f", cwd=NOTES)
    home.transcript(finished(ago(hours=3)), cwd=NOTES)
    assert "1 live, nothing needs attention" in page([snapshot(home)])


def test_a_day_of_snapshots_of_fifteen_sessions_renders_quickly() -> None:
    def session(index: int, state: str) -> Any:
        reason = "Idle for 3h 00m." if state == "idle" else "Busy for 3m; last sign of progress 20s ago."
        return models.SessionItem(
            id=f"{index:08d}-aaaa",
            agent="claude",
            name=f"project-{index}",
            project=f"/tmp/work/p{index}",
            pid=4000 + index,
            model=MODEL,
            state=state,
            reason=reason,
            quiet_s=20,
        )

    def report(flip: bool) -> Any:
        sessions = [session(i, "working" if (i < 3) != flip else "idle") for i in range(15)]
        return models.Report(
            taken="2026-11-23 17:00:00",
            stall_after_s=300,
            window_s=1800,
            retain_s=86400,
            versions={},
            counts={"working": 3, "idle": 12},
            attention=0,
            sessions=sessions,
            notes=[],
        )

    runs = [record(report(step % 2 == 0), NOW - timedelta(minutes=step)) for step in range(1440)]
    began = time.perf_counter()
    html = page(runs)
    assert time.perf_counter() - began < 5
    assert html.count('class="change"') == 200
