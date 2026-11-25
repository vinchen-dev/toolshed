from __future__ import annotations

import importlib
import json
from typing import Any

import pytest

from shed import ToolError
from shed.registry import load_tool, load_tool_module

module = load_tool_module("agent-watch")
fakes = importlib.import_module(f"{module.__package__}.fakes")
wrapper, spawning, building, BUSY = fakes.wrapper, fakes.spawning, fakes.building, fakes.BUSY
TOOL = load_tool("agent-watch")
CWD = fakes.CWD
Home = fakes.Home
MODEL = fakes.MODEL
OTHER = fakes.OTHER
QUESTION = fakes.QUESTION
SID = fakes.SID
ago = fakes.ago
asking = fakes.asking
call = fakes.call
finished = fakes.finished
prompt = fakes.prompt
reading = fakes.reading
result = fakes.result
said = fakes.said
stamp = fakes.stamp
words = fakes.words


def test_a_busy_session_with_a_fresh_write_is_working(home: Any) -> None:
    home.session(pid=4412)
    home.transcript(
        [
            prompt(ago(minutes=3), "run the tests"),
            said(ago(seconds=20), "m1", call("toolu_1", "Bash", command="uv run pytest")),
        ]
    )
    [session] = home.run().sessions
    assert (session.state, session.agent, session.name) == ("working", "claude", "checkout-api-97")
    assert (session.project, session.pid, session.model) == (CWD, 4412, MODEL)
    assert session.turn is not None
    assert (session.turn.prompt, session.turn.started) == ("run the tests", "2026-11-23 16:57:00")
    assert (session.turn.elapsed_s, session.turn.tool_calls) == (180, 1)
    assert session.activity == "Bash: uv run pytest"
    assert "Bash" in session.reason and "uv run pytest" in session.reason
    assert session.quiet_s == 20


def test_a_busy_session_quiet_past_the_threshold_is_stalled_and_the_run_still_succeeds(home: Any) -> None:
    home.session(pid=4412)
    home.transcript(
        [
            prompt(ago(minutes=23), "fix the flaky payments test"),
            said(ago(minutes=14), "m1", call("toolu_1", "Bash", command="npm test -- payments")),
        ]
    )
    report = home.run()
    [session] = report.sessions
    assert (session.state, session.quiet_s) == ("stalled", 14 * 60)
    for part in ("quiet for 14m", "Bash: npm test -- payments", "limit 2m", "pid 4412 alive", "0.2% CPU"):
        assert part in session.reason
    assert (report.attention, report.counts) == (1, {"stalled": 1})


def test_a_shell_call_is_left_alone_inside_its_time_limit_and_stalled_past_it(home: Any) -> None:
    home.session(pid=4412)
    slow = call("toolu_1", "Bash", command="make slow", timeout=600000)
    home.transcript([prompt(ago(minutes=8), "run the slow suite"), said(ago(minutes=7), "m1", slow)])
    home.process(6001, ago(minutes=7), command=wrapper("make slow", background=False), ppid=4412, cpu=0.0)
    [inside] = home.run(stall_after="5m").sessions
    assert inside.state == "working"
    assert "limit 10m" in inside.reason

    quick = call("toolu_1", "Bash", command="make test")
    home.transcript([prompt(ago(minutes=4), "run the suite"), said(ago(minutes=3), "m1", quick)])
    home.process(6002, ago(minutes=3), command=wrapper("make test", background=False), ppid=4412, cpu=0.0)
    [past] = home.run(stall_after="10m").sessions
    assert past.state == "stalled"
    assert "limit 2m" in past.reason


def test_a_pending_call_with_no_process_past_the_threshold_may_be_waiting_for_approval(home: Any) -> None:
    home.session(pid=4412)
    delete = call("toolu_1", "mcp__github__delete_branch", branch="old")
    home.transcript([prompt(ago(minutes=9), "delete the old branch"), said(ago(minutes=8), "m1", delete)])
    [session] = home.run().sessions
    assert session.state == "stalled"
    assert "mcp__github__delete_branch pending 8m" in session.reason
    assert "may be waiting for your approval" in session.reason


def test_a_session_the_registry_calls_waiting_needs_input(home: Any) -> None:
    home.session(pid=4412, status="waiting", waitingFor="input needed")
    home.transcript(asking(ago(minutes=8)))
    [session] = home.run().sessions
    assert session.state == "needs-input"
    assert "input needed" in session.reason and "8m" in session.reason
    assert session.activity == f"Question: {QUESTION}"


def test_a_pending_question_needs_input_when_the_registry_is_unreadable(home: Any) -> None:
    (home.sessions / "4412.json").write_text("{not json", encoding="utf-8")
    home.transcript(asking(ago(minutes=8)))
    report = home.run()
    [session] = report.sessions
    assert session.state == "needs-input"
    assert "registry record 4412.json is unreadable" in report.notes


def test_an_unknown_registry_status_is_treated_as_busy_and_noted(home: Any) -> None:
    home.session(pid=4412, status="compacting")
    home.transcript(reading(ago(seconds=5)))
    report = home.run()
    assert [s.state for s in report.sessions] == ["working"]
    assert 'unknown registry status "compacting" for checkout-api-97, treated as busy' in report.notes


def test_a_live_session_between_turns_is_idle_and_keeps_its_last_prompt(home: Any) -> None:
    home.session(pid=4412, status="idle")
    home.transcript(finished(ago(hours=3), "summarise the file"))
    [session] = home.run().sessions
    assert session.state == "idle"
    assert "Idle for 3h 00m" in session.reason
    assert session.turn is not None and session.turn.prompt == "summarise the file"
    assert session.activity is None


def test_a_session_whose_process_is_gone_is_gone_mid_turn_and_ended_after_a_finished_turn(home: Any) -> None:
    home.session(pid=4412, alive=False)
    home.transcript(reading(ago(minutes=25)))
    [mid_turn] = home.run().sessions
    assert (mid_turn.state, mid_turn.pid) == ("gone", None)
    assert "no live process" in mid_turn.reason and "25m" in mid_turn.reason

    home.transcript(finished(ago(minutes=25)))
    assert [s.state for s in home.run().sessions] == ["ended"]

    home.process(4412, started=ago(minutes=5), command="vim")  # the pid was reused by another program
    assert [s.state for s in home.run().sessions] == ["ended"]
    home.transcript(reading(ago(minutes=25)))
    assert [s.state for s in home.run().sessions] == ["gone"]


def test_an_ended_session_stays_for_the_retention_period_as_one_row(home: Any) -> None:
    home.transcript(finished(ago(hours=5)), written=ago(hours=5))
    [session] = home.run().sessions
    assert (session.state, session.pid, session.model) == ("ended", None, MODEL)
    assert session.turn is not None
    assert (session.turn.prompt, session.turn.tool_calls) == ("tidy the changelog", 1)
    assert session.last_write == "2026-11-23 12:00:00"
    assert home.run(retain="1h").sessions == []

    home.transcript(finished(ago(days=2)), written=ago(days=2))
    assert home.run().sessions == []

    home.transcript(finished(ago(days=2)))  # the file was touched just now, its newest record is two days old
    assert home.run().sessions == []


def test_a_session_reports_the_model_of_its_newest_assistant_record(home: Any) -> None:
    home.session(pid=4412)
    read = call("toolu_1", "Read", file_path="/tmp/x.py")
    home.transcript(
        [
            prompt(ago(minutes=2), "go"),
            said(ago(minutes=1), "m1", words("Looking."), stop="", model="claude-sonnet-5-5"),
            said(ago(seconds=10), "m2", read, model="claude-haiku-5-5"),
        ]
    )
    assert [s.model for s in home.run().sessions] == ["claude-haiku-5-5"]

    home.transcript([prompt(ago(seconds=10), "go")])
    assert [s.model for s in home.run().sessions] == [None]


def test_without_a_process_table_registry_records_are_trusted_and_nothing_is_gone_or_ended(
    home: Any,
) -> None:
    home.session(pid=4412, alive=False)
    home.transcript(reading(ago(seconds=30)))
    home.transcript(finished(ago(hours=2)), sid=OTHER, written=ago(hours=2))
    home.probe.ps_error = "ps timed out after 10s"
    report = home.run()
    assert [(s.id, s.state) for s in report.sessions] == [(SID, "working")]
    assert "process table unavailable: ps timed out after 10s" in report.notes


def test_the_report_carries_manila_times_thresholds_versions_and_counts(home: Any) -> None:
    home.session(pid=4412)
    home.transcript(reading(ago(seconds=30)))
    home.session(pid=4413, sid=OTHER, status="idle", name="notes-4f", cwd="/tmp/work/notes")
    home.transcript(finished(ago(hours=3)), sid=OTHER, cwd="/tmp/work/notes")
    report = home.run()
    assert report.taken == "2026-11-23 17:00:00"
    assert (report.stall_after_s, report.retain_s) == (300, 86400)
    assert report.versions == {"claude": ["2.1.322"]}
    assert report.counts == {"working": 1, "idle": 1}
    assert [(s.name, s.quiet_s) for s in report.sessions] == [("checkout-api-97", 30), ("notes-4f", 3 * 3600)]
    assert all(s.reason for s in report.sessions)


def test_a_missing_registry_folder_is_a_note_and_transcripts_are_still_judged(home: Any) -> None:
    home.sessions.rmdir()
    home.transcript(reading(ago(seconds=30)))
    report = home.run()
    assert [s.state for s in report.sessions] == ["working"]
    assert f"no Claude Code session registry at {home.sessions}" in report.notes


def test_with_nothing_to_read_the_run_fails_and_says_where_it_looked(home: Any) -> None:
    home.sessions.rmdir()
    home.projects.rmdir()
    (home.codex / "sessions").rmdir()
    with pytest.raises(ToolError, match="nothing to watch: no Claude Code sessions under"):
        home.run()


def test_a_large_transcript_is_read_from_the_end_only(home: Any) -> None:
    home.session(pid=4412)
    old = [prompt(ago(hours=6), "old prompt, never finished")]
    filler = [result(ago(hours=5), f"toolu_old_{i}", body="x" * 1000) for i in range(600)]
    home.transcript([*old, *filler, *reading(ago(seconds=30), "new prompt")])
    [session] = home.run().sessions
    assert session.turn is not None
    assert (session.turn.prompt, session.turn.started) == ("new prompt", "2026-11-23 16:58:30")


def test_a_turn_that_started_before_the_read_window_is_still_classified(home: Any) -> None:
    home.session(pid=4412)
    old = [prompt(ago(hours=6), "a very long task")]
    filler = [result(ago(minutes=40), f"toolu_{i}", body="x" * 1000) for i in range(7600)]
    home.transcript([*old, *filler, result(ago(seconds=30), "toolu_last")])
    report = home.run()
    [session] = report.sessions
    assert (session.state, session.turn, session.quiet_s) == ("working", None, 30)
    assert "checkout-api-97: turn started before the last 8 MB of its transcript" in report.notes


def test_a_bad_duration_is_refused_as_bad_usage_naming_the_field(home: Any) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="stall_after"):
        TOOL.input(stall_after="5x")
    with pytest.raises(ValidationError, match="retain"):
        TOOL.input(retain="soon")


def test_a_run_changes_nothing_on_disk_and_the_tool_has_no_action_inputs(home: Any) -> None:
    home.session(pid=4412)
    home.transcript(reading(ago(minutes=20)))
    home.transcript(finished(ago(hours=2)), sid=OTHER, written=ago(hours=2))

    def snapshot() -> list[tuple[str, int, int]]:
        files = sorted(p for p in home.root.rglob("*") if p.is_file())
        return [(str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in files]

    before = snapshot()
    home.run()
    assert snapshot() == before
    assert not {"kill", "stop", "signal", "resume", "restart", "send"} & set(TOOL.input.model_fields)


def test_a_live_session_without_a_transcript_is_judged_from_the_registry_alone(home: Any) -> None:
    home.session(pid=4412, status="idle", statusUpdatedAt=stamp(ago(days=10)))
    [session] = home.run().sessions
    assert (session.state, session.quiet_s, session.model, session.turn) == ("idle", 10 * 86400, None, None)
    assert "Idle for 10d" in session.reason


def test_a_status_that_just_turned_busy_counts_as_a_sign_of_progress(home: Any) -> None:
    home.session(pid=4412, statusUpdatedAt=stamp(ago(seconds=20)))
    home.transcript(finished(ago(hours=3)))
    [session] = home.run().sessions
    assert (session.state, session.quiet_s) == ("working", 20)


def test_unreadable_files_become_notes_and_the_rest_of_the_report_stands(home: Any) -> None:
    home.session(pid=4412)
    home.transcript(reading(ago(seconds=30)))
    stray = home.projects / "-tmp-work-other"
    stray.mkdir()
    (stray / f"{OTHER}.jsonl").symlink_to(home.root / "missing.jsonl")
    day = home.codex / "sessions" / "2026" / "11" / "23"
    day.mkdir(parents=True)
    (day / "rollout-2026-11-23T08-00-00-dead.jsonl").symlink_to(home.root / "missing-too.jsonl")
    report = home.run()
    assert [(s.name, s.state) for s in report.sessions] == [("checkout-api-97", "working")]
    assert f"transcript {OTHER}.jsonl is unreadable" in report.notes
    assert "rollout rollout-2026-11-23T08-00-00-dead.jsonl is unreadable" in report.notes


def test_a_live_session_with_an_unreadable_transcript_is_judged_from_the_registry(home: Any) -> None:
    home.session(pid=4412, status="idle")
    folder = home.projects / "-tmp-work-checkout-api"
    folder.mkdir()
    (folder / f"{SID}.jsonl").symlink_to(home.root / "missing.jsonl")
    report = home.run()
    assert [(s.name, s.state, s.turn) for s in report.sessions] == [("checkout-api-97", "idle", None)]
    assert f"transcript {SID}.jsonl is unreadable" in report.notes


def test_a_compaction_summary_does_not_open_a_turn(home: Any) -> None:
    summary = json.loads(prompt(ago(hours=2), "The summarized conversation so far: the build was started."))
    summary |= {"isCompactSummary": True, "isVisibleInTranscriptOnly": True}
    home.transcript([*finished(ago(hours=3)), json.dumps(summary)], written=ago(hours=2))
    [session] = home.run().sessions
    assert session.state == "ended"
    assert session.turn is not None and session.turn.prompt == "tidy the changelog"


def test_a_session_says_when_it_started(home: Any) -> None:
    home.session(pid=4412)  # its process started two hours before the snapshot
    home.transcript(reading(ago(seconds=10)))
    home.transcript(finished(ago(hours=5)), sid=OTHER, written=ago(hours=5))  # ended: no process holds it
    live, ended = sorted(home.run().sessions, key=lambda session: session.state == "ended")
    assert (live.state, live.started) == ("working", "2026-11-23 15:00:00")
    assert (ended.state, ended.started) == ("ended", "2026-11-23 11:50:00"), "its first record"


def test_a_subagent_started_this_instant_is_left_out_without_a_note(home: Any) -> None:
    building(home, BUSY)
    folder = home.projects / "-tmp-work-checkout-api" / SID / "subagents"
    home.subagent("new1", [], use_id="toolu_x")
    (folder / "agent-new1.jsonl").unlink()  # registered, but its transcript is not written yet
    home.subagent("new2", [], use_id="toolu_y")
    (folder / "agent-new2.meta.json").write_text("", encoding="utf-8")  # its metadata is being written
    report = home.run()
    [session] = report.sessions
    assert session.subagents == []
    assert not any("unreadable" in note for note in report.notes)


def test_an_unreadable_subagent_file_and_output_file_become_notes(home: Any) -> None:
    building(home, BUSY)
    home.subagent("bad", [], use_id="toolu_x")
    meta = home.projects / "-tmp-work-checkout-api" / SID / "subagents" / "agent-bad.meta.json"
    meta.write_text("{not json", encoding="utf-8")
    (home.tasks / "-tmp-work-checkout-api" / SID / "tasks" / "b123.output").mkdir(parents=True)
    report = home.run(tail=5)
    [session] = report.sessions
    assert session.state == "working" and session.commands[0].tail is None
    assert f"subagent agent-bad of {SID[:8]} is unreadable" in report.notes
    assert "output of b123 is unreadable" in report.notes
