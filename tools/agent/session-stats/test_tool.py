from __future__ import annotations

import importlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from shed import ToolContext, ToolError
from shed.registry import load_tool, load_tool_module

module = load_tool_module("session-stats")
transcripts = importlib.import_module(f"{module.__package__}.transcripts")
TOOL = load_tool("session-stats")

PROJECT = "-tmp-work-proj"


def _line(kind: str, ts: str, **extra: Any) -> str:
    return json.dumps({"type": kind, "timestamp": ts, "sessionId": "s", "cwd": "/tmp/work/proj", **extra})


def _assistant(
    ts: str, mid: str, content: list[dict[str, Any]], usage: dict[str, int], model: str = "m1"
) -> str:
    return _line("assistant", ts, message={"id": mid, "model": model, "usage": usage, "content": content})


def write_session(folder: Path, stem: str, lines: list[str], mtime: float | None = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{stem}.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def sample_lines() -> list[str]:
    usage_a = {
        "input_tokens": 10,
        "cache_creation_input_tokens": 100,
        "cache_read_input_tokens": 1000,
        "output_tokens": 5,
    }
    usage_b = {
        "input_tokens": 2,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 1100,
        "output_tokens": 50,
    }
    return [
        _line(
            "user",
            "2026-11-10T01:00:00.000Z",
            message={"role": "user", "content": "fix the login bug please"},
        ),
        "not json at all",
        _line(
            "user", "2026-11-10T01:00:01.000Z", isMeta=True, message={"role": "user", "content": "injected"}
        ),
        _assistant("2026-11-10T01:00:05.000Z", "msg_1", [{"type": "thinking", "thinking": "hmm"}], usage_a),
        _assistant(
            "2026-11-10T01:00:06.000Z",
            "msg_1",
            [{"type": "tool_use", "id": "tu_1", "name": "Read", "input": {"file_path": "src/app.py"}}],
            {**usage_a, "output_tokens": 40},
        ),
        _line(
            "user",
            "2026-11-10T01:00:07.000Z",
            message={
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "tu_1", "content": "ok"}],
            },
        ),
        _assistant(
            "2026-11-10T01:00:08.000Z",
            "msg_2",
            [
                {"type": "text", "text": "Found it."},
                {
                    "type": "tool_use",
                    "id": "tu_2",
                    "name": "Bash",
                    "input": {"command": "pytest -q", "description": "Run tests"},
                },
            ],
            usage_b,
            model="m2",
        ),
        _line(
            "user",
            "2026-11-10T01:00:09.000Z",
            message={
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tu_2", "is_error": True, "content": "1 failed"}
                ],
            },
        ),
        _line(
            "user",
            "2026-11-10T01:30:00.000Z",
            message={
                "role": "user",
                "content": "<command-message>x</command-message>\n<command-name>/simplify</command-name>",
            },
        ),
        _assistant("2026-11-10T01:30:00.000Z", "msg_3", [{"type": "text", "text": "Done."}], usage_b),
        _line(
            "user", "2026-11-10T01:30:00.000Z", message={"role": "user", "content": "<task-notification>x"}
        ),
        _assistant(
            "2026-11-10T01:30:00.000Z",
            "msg_4",
            [{"type": "text", "text": "Login expired"}],
            {},
            "<synthetic>",
        ),
    ]


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    base = tmp_path / "projects"
    now = time.time()
    write_session(base / PROJECT, "aaaa1111-older", sample_lines()[:4], mtime=now - 3 * 86400)
    write_session(base / PROJECT, "bbbb2222-newer", sample_lines(), mtime=now - 60)
    write_session(base / "-tmp-other", "cccc3333-other", sample_lines()[:1], mtime=now - 30)
    (base / PROJECT / "bbbb2222-newer").mkdir()  # a session's own subfolder is not a session
    monkeypatch.setenv("CLAUDE_PROJECTS_DIR", str(base))
    return base


def ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(cwd=tmp_path, log=lambda _: None)


def test_encodes_a_project_path_like_claude_code() -> None:
    assert transcripts.encode_project(Path("/Users/vince/Desktop/Projects/PMS2.0_PH")) == (
        "-Users-vince-Desktop-Projects-PMS2-0-PH"
    )
    assert transcripts.encode_project(Path("/Users/vince/new finance/finance-ai")) == (
        "-Users-vince-new-finance-finance-ai"
    )


def test_since_accepts_durations_and_manila_times() -> None:
    now = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
    assert transcripts.parse_since("7d", now) == datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
    assert transcripts.parse_since("12h", now) == datetime(2026, 11, 11, 0, 0, tzinfo=UTC)
    assert transcripts.parse_since("30m", now) == datetime(2026, 11, 11, 11, 30, tzinfo=UTC)
    assert transcripts.manila(transcripts.parse_since("2026-11-10")) == "2026-11-10 00:00:00"
    assert transcripts.parse_since(None) is None


def test_reads_a_session_counting_usage_once_per_message(root: Path) -> None:
    session = transcripts.read_session(root / PROJECT / "bbbb2222-newer.jsonl", PROJECT)
    assert [p.text for p in session.prompts] == ["fix the login bug please", "/simplify"]
    assert session.models == ["m1", "m2"]
    assert len(session.usage) == 3 and session.usage["msg_1"]["output_tokens"] == 40  # the last chunk wins
    assert session.tokens == {
        "input": 14,
        "cache_read": 3200,
        "cache_create": 100,
        "output": 140,
        "total": 3454,
    }
    assert session.tool_calls == {"Read": 1, "Bash": 1}
    assert [r.is_error for r in session.tool_results] == [False, True]
    assert session.duration_min == 30.0 and session.cwd == "/tmp/work/proj"


def test_lists_a_projects_sessions_newest_first(root: Path, tmp_path: Path) -> None:
    report = TOOL.run(TOOL.input(project=PROJECT), ctx(tmp_path))
    assert [s.id for s in report.sessions] == ["bbbb2222-newer", "aaaa1111-older"]
    newest = report.sessions[0]
    assert (newest.user_turns, newest.assistant_messages, newest.tool_calls) == (2, 3, 2)
    assert [(t.name, t.calls) for t in newest.tools] == [("Read", 1), ("Bash", 1)]
    assert newest.first_prompt == "fix the login bug please" and newest.started.startswith("2026-11-10 09:00")
    assert report.totals.sessions == 2 and report.totals.tokens.total == 3454 + 1115
    assert report.sessions[1].tokens.output == 5


def test_since_limit_and_session_filters(root: Path, tmp_path: Path) -> None:
    recent = TOOL.run(TOOL.input(project=PROJECT, since="2026-11-10 09:10"), ctx(tmp_path))
    assert [s.id for s in recent.sessions] == ["bbbb2222-newer"] and recent.since == "2026-11-10 09:10:00"
    one = TOOL.run(TOOL.input(project=PROJECT, limit=1), ctx(tmp_path))
    assert len(one.sessions) == 1
    by_prefix = TOOL.run(TOOL.input(project=PROJECT, session="aaaa"), ctx(tmp_path))
    assert [s.id for s in by_prefix.sessions] == ["aaaa1111-older"]
    with pytest.raises(ToolError, match="bad --since"):
        TOOL.run(TOOL.input(project=PROJECT, since="yesterday"), ctx(tmp_path))


def test_all_projects_and_missing_project(root: Path, tmp_path: Path) -> None:
    everything = TOOL.run(TOOL.input(all_projects=True), ctx(tmp_path))
    assert everything.projects == ["-tmp-other", PROJECT] and len(everything.sessions) == 3
    assert everything.sessions[0].project == "-tmp-other"
    with pytest.raises(ToolError, match="no transcripts for"):
        TOOL.run(TOOL.input(project="/nowhere/at/all"), ctx(tmp_path))


def test_renders_a_table_with_totals(root: Path, tmp_path: Path) -> None:
    text = module.render(TOOL.run(TOOL.input(project=PROJECT), ctx(tmp_path)))
    assert text.splitlines()[0].startswith("session   started")
    assert "bbbb2222  2026-11-10 09:00" in text and "fix the login bug please" in text
    assert text.rstrip().endswith("out 145)")
