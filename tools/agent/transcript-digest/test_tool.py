from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from shed import ToolContext, ToolError
from shed.registry import load_tool, load_tool_module

module = load_tool_module("transcript-digest")
TOOL = load_tool("transcript-digest")

PROJECT = "-tmp-work-proj"
USAGE = {
    "input_tokens": 1,
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 0,
    "output_tokens": 1,
}


def _line(kind: str, ts: str, **extra: Any) -> str:
    return json.dumps({"type": kind, "timestamp": ts, "sessionId": "s", "cwd": "/tmp/work/proj", **extra})


def _assistant(ts: str, mid: str, content: list[dict[str, Any]]) -> str:
    return _line("assistant", ts, message={"id": mid, "model": "m1", "usage": USAGE, "content": content})


def lines() -> list[str]:
    long_text = "x" * 500
    return [
        _line("user", "2026-11-10T01:00:00.000Z", message={"role": "user", "content": "fix the login bug"}),
        _assistant(
            "2026-11-10T01:00:05.000Z",
            "msg_1",
            [
                {"type": "thinking", "thinking": "let me look"},
                {"type": "text", "text": "Looking."},
                {"type": "tool_use", "id": "tu_1", "name": "Read", "input": {"file_path": "src/app.py"}},
            ],
        ),
        _assistant(
            "2026-11-10T01:00:06.000Z",
            "msg_1",
            [
                {
                    "type": "tool_use",
                    "id": "tu_2",
                    "name": "Bash",
                    "input": {"command": "pytest -q", "description": "Run tests"},
                }
            ],
        ),
        _line(
            "user",
            "2026-11-10T01:00:07.000Z",
            message={
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tu_2", "is_error": True, "content": "1 failed"}
                ],
            },
        ),
        _assistant(
            "2026-11-10T01:00:08.000Z",
            "msg_2",
            [
                {
                    "type": "tool_use",
                    "id": "tu_3",
                    "name": "MultiEdit",
                    "input": {"file_path": "src/app.py", "edits": [{"old_string": "a", "new_string": "b"}]},
                },
                {
                    "type": "tool_use",
                    "id": "tu_4",
                    "name": "Write",
                    "input": {"file_path": "tests/test_app.py"},
                },
                {"type": "text", "text": long_text},
            ],
        ),
        _line("user", "2026-11-10T01:10:00.000Z", message={"role": "user", "content": "thanks"}),
    ]


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    base = tmp_path / "projects"
    folder = base / PROJECT
    folder.mkdir(parents=True)
    now = time.time()
    for stem, age in (("aaaa1111", 600), ("aaaa2222", 300), ("bbbb3333", 10)):
        path = folder / f"{stem}.jsonl"
        path.write_text("\n".join(lines()) + "\n", encoding="utf-8")
        os.utime(path, (now - age, now - age))
    monkeypatch.setenv("CLAUDE_PROJECTS_DIR", str(base))
    return base


def ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(cwd=tmp_path, log=lambda _: None)


def test_digests_the_newest_session_by_default(root: Path, tmp_path: Path) -> None:
    digest = TOOL.run(TOOL.input(project=PROJECT), ctx(tmp_path))
    assert digest.session == "bbbb3333" and digest.models == ["m1"] and digest.duration_min == 10.0
    assert [p.text for p in digest.prompts] == ["fix the login bug", "thanks"]
    assert [r.text for r in digest.replies] == ["Looking.", "x" * 399 + "…"]
    assert digest.tools == {"Read": 1, "Bash": 1, "MultiEdit": 1, "Write": 1}
    assert digest.files_touched == ["src/app.py", "tests/test_app.py"]
    assert [(c.command, c.description) for c in digest.commands] == [("pytest -q", "Run tests")]
    assert [(e.tool, e.text) for e in digest.errors] == [("Bash", "1 failed")]
    assert digest.thinking == [] and digest.prompts[0].time == "2026-11-10 09:00:00"


def test_picks_a_session_by_prefix_and_rejects_ambiguity(root: Path, tmp_path: Path) -> None:
    assert TOOL.run(TOOL.input(project=PROJECT, session="aaaa2"), ctx(tmp_path)).session == "aaaa2222"
    with pytest.raises(ToolError, match="matches 2 sessions"):
        TOOL.run(TOOL.input(project=PROJECT, session="aaaa"), ctx(tmp_path))
    with pytest.raises(ToolError, match="no session starting with"):
        TOOL.run(TOOL.input(project=PROJECT, session="zzzz"), ctx(tmp_path))
    with pytest.raises(ToolError, match="no transcripts for"):
        TOOL.run(TOOL.input(project="/nowhere"), ctx(tmp_path))


def test_options_drop_tools_and_add_thinking(root: Path, tmp_path: Path) -> None:
    digest = TOOL.run(
        TOOL.input(project=PROJECT, include_tools=False, include_thinking=True, max_chars=0, max_items=1),
        ctx(tmp_path),
    )
    assert digest.tools == {} and digest.files_touched == [] and digest.commands == []
    assert [x.text for x in digest.thinking] == ["let me look"]
    assert len(digest.prompts) == 1 and digest.replies[0].text == "Looking."


def test_renders_markdown_sections(root: Path, tmp_path: Path) -> None:
    text = module.render(TOOL.run(TOOL.input(project=PROJECT, max_chars=50), ctx(tmp_path)))
    assert text.startswith(
        "# session bbbb3333 in /tmp/work/proj: 2026-11-10 09:00:00 to 2026-11-10 09:10:00 (10.0 min, m1)"
    )
    assert "## Prompts\n- 09:00 fix the login bug" in text
    assert "## Tools\n- Read: 1" in text and "## Files touched\n- src/app.py" in text
    assert "- 09:00 pytest -q  (Run tests)" in text and "## Errors\n- 09:00 Bash: 1 failed" in text
    assert "## Thinking" not in text
