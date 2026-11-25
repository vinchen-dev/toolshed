from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from shed.lib.transcripts import Session, read_session

PROJECT = "-tmp-work-proj"


def _line(kind: str, ts: str, **extra: Any) -> str:
    return json.dumps({"type": kind, "timestamp": ts, "sessionId": "s", "cwd": "/tmp/work/proj", **extra})


def _prompt(ts: str, text: str) -> str:
    return _line("user", ts, message={"role": "user", "content": text})


def _reply(ts: str, mid: str, text: str) -> str:
    content = [{"type": "text", "text": text}]
    return _line("assistant", ts, message={"id": mid, "model": "m1", "usage": {}, "content": content})


def _write(path: Path, lines: list[str]) -> Path:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_tail_read_returns_only_the_records_inside_the_tail(tmp_path: Path) -> None:
    old = [
        _prompt("2026-11-23T01:00:00Z", "old prompt"),
        _reply("2026-11-23T01:00:05Z", "m-old", "old reply"),
    ]
    filler = [_reply(f"2026-11-23T01:01:{i:02d}Z", f"m-fill-{i}", "filler " * 20) for i in range(30)]
    new = [
        _prompt("2026-11-23T02:00:00Z", "new prompt"),
        _reply("2026-11-23T02:00:05Z", "m-new", "new reply"),
    ]
    path = _write(tmp_path / "aaaa1111.jsonl", [*old, *filler, *new])
    new_bytes = len(("\n".join(new) + "\n").encode())

    whole = read_session(path, PROJECT)
    assert [p.text for p in whole.prompts] == ["old prompt", "new prompt"]

    cut_mid_line = read_session(path, PROJECT, tail_bytes=new_bytes + 10)
    assert [p.text for p in cut_mid_line.prompts] == ["new prompt"]
    assert ["\n".join(r.texts) for r in cut_mid_line.replies] == ["new reply"]

    cut_on_a_line_start = read_session(path, PROJECT, tail_bytes=new_bytes)
    assert [p.text for p in cut_on_a_line_start.prompts] == ["new prompt"]

    larger_than_the_file = read_session(path, PROJECT, tail_bytes=path.stat().st_size + 1)
    assert [p.text for p in larger_than_the_file.prompts] == ["old prompt", "new prompt"]


NOTICE = (
    "<task-notification>\n<task-id>{id}</task-id>\n<tool-use-id>{use}</tool-use-id>\n"
    "<output-file>/tmp/x/{id}.output</output-file>\n<status>{status}</status>\n"
    "<summary>{summary}</summary>\n</task-notification>"
)
BUILD_DONE = 'Background command "build" completed (exit code 0)'


def _session_with_background_work(tmp_path: Path) -> Session:
    build = NOTICE.format(id="b123", use="toolu_bg", status="completed", summary=BUILD_DONE)
    agent = NOTICE.format(id="a456", use="toolu_agent", status="failed", summary='Agent "review" failed')
    start = {"type": "tool_use", "id": "toolu_bg", "name": "Bash", "input": {"command": "npm run build"}}
    started = {"type": "tool_result", "tool_use_id": "toolu_bg", "content": "Command running in background"}
    queued = {"type": "queue-operation", "sessionId": "s", "content": build}
    lines = [
        _prompt("2026-11-23T01:00:00Z", "run the build"),
        _line("assistant", "2026-11-23T01:00:02Z", message={"id": "m1", "model": "m1", "content": [start]}),
        _line(
            "user",
            "2026-11-23T01:00:03Z",
            message={"role": "user", "content": [started]},
            toolUseResult={"backgroundTaskId": "b123", "stdout": "", "stderr": ""},
        ),
        json.dumps({**queued, "operation": "enqueue", "timestamp": "2026-11-23T01:02:00Z"}),
        json.dumps({**queued, "operation": "remove", "timestamp": "2026-11-23T01:02:01Z"}),
        _prompt("2026-11-23T01:03:00Z", agent),
        _line("system", "2026-11-23T01:04:00Z", subtype="turn_duration", durationMs=240000, messageCount=6),
        json.dumps({"type": "ai-title", "aiTitle": "Build the thing", "sessionId": "s"}),
        json.dumps({"type": "ai-title", "aiTitle": "Build and review", "sessionId": "s"}),
    ]
    return read_session(_write(tmp_path / "bbbb2222.jsonl", lines), PROJECT)


def test_turn_ends_are_the_times_of_the_turn_duration_records(tmp_path: Path) -> None:
    session = _session_with_background_work(tmp_path)
    assert session.turn_ends == [datetime(2026, 11, 23, 1, 4, tzinfo=UTC)]


def test_background_task_ids_are_keyed_by_the_tool_call_that_started_them(tmp_path: Path) -> None:
    session = _session_with_background_work(tmp_path)
    assert session.background == {"toolu_bg": "b123"}


def test_task_notices_are_read_from_both_record_shapes_and_are_not_prompts(tmp_path: Path) -> None:
    session = _session_with_background_work(tmp_path)
    assert [(n.task_id, n.status, n.summary) for n in session.notices] == [
        ("b123", "completed", BUILD_DONE),
        ("a456", "failed", 'Agent "review" failed'),
    ]
    assert session.notices[0].time == datetime(2026, 11, 23, 1, 2, tzinfo=UTC)
    assert [p.text for p in session.prompts] == ["run the build"]


def test_a_reply_carries_the_stop_reason_of_its_last_chunk(tmp_path: Path) -> None:
    def chunk(ts: str, mid: str, block: dict[str, Any], stop: str | None) -> str:
        message = {"id": mid, "model": "m1", "usage": {}, "content": [block], "stop_reason": stop}
        return _line("assistant", ts, message=message)

    call = {"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {}}
    lines = [
        _prompt("2026-11-23T01:00:00Z", "look at the file"),
        chunk("2026-11-23T01:00:01Z", "m1", call, "tool_use"),
        chunk("2026-11-23T01:00:05Z", "m2", {"type": "thinking", "thinking": "hm"}, None),
        chunk("2026-11-23T01:00:06Z", "m2", {"type": "text", "text": "It is fine."}, "end_turn"),
    ]
    session = read_session(_write(tmp_path / "cccc3333.jsonl", lines), PROJECT)
    assert [(r.message_id, r.stop_reason) for r in session.replies] == [
        ("m1", "tool_use"),
        ("m2", "end_turn"),
    ]


def test_a_reply_carries_the_model_that_produced_it(tmp_path: Path) -> None:
    lines = [_prompt("2026-11-23T01:00:00Z", "hi"), _reply("2026-11-23T01:00:05Z", "m-a", "hello")]
    session = read_session(_write(tmp_path / "dddd4444.jsonl", lines), PROJECT)
    assert [r.model for r in session.replies] == ["m1"]


def test_title_is_the_newest_generated_title(tmp_path: Path) -> None:
    session = _session_with_background_work(tmp_path)
    assert session.title == "Build and review"


def test_a_compaction_summary_is_kept_as_a_prompt_but_flagged(tmp_path: Path) -> None:
    summary = json.loads(_prompt("2026-11-23T01:05:00Z", "The summarized conversation so far."))
    summary["isCompactSummary"] = True
    lines = [_prompt("2026-11-23T01:00:00Z", "run the build"), json.dumps(summary)]
    session = read_session(_write(tmp_path / "eeee5555.jsonl", lines), PROJECT)
    assert [(p.text, p.summary) for p in session.prompts] == [
        ("run the build", False),
        ("The summarized conversation so far.", True),
    ]
