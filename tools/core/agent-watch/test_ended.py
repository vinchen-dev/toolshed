from __future__ import annotations

import importlib
from typing import Any

from shed.registry import load_tool_module

module = load_tool_module("agent-watch")
fakes = importlib.import_module(f"{module.__package__}.fakes")
ago, call, said, result, words, prompt = (
    fakes.ago,
    fakes.call,
    fakes.said,
    fakes.result,
    fakes.words,
    fakes.prompt,
)
launching, spawning = fakes.launching, fakes.spawning


def closing(when: Any) -> str:
    return said(when, f"m-end-{int(when.timestamp())}", words("Done."), stop="end_turn")


def test_an_ended_session_counts_only_what_its_last_turn_started(home: Any) -> None:
    earlier = [*spawning(ago(hours=4, minutes=30)), result(ago(hours=4, minutes=29), "toolu_agent")]
    earlier += [*launching(ago(hours=4, minutes=20)), closing(ago(hours=4))]
    last = [
        prompt(ago(hours=3), "tidy the changelog"),
        said(ago(hours=2, minutes=59), "m1", call("toolu_r", "Read", file_path="/tmp/x.py")),
        result(ago(hours=2, minutes=58), "toolu_r"),
        closing(ago(hours=2, minutes=57)),
    ]
    home.transcript([*earlier, *last], written=ago(hours=2, minutes=57))
    [session] = home.run().sessions
    assert session.state == "ended"
    assert session.turn is not None and session.turn.tool_calls == 1
    assert (session.subagents_finished, session.commands_finished) == (0, 0)
    assert (session.subagents, session.commands, session.children) == ([], [], [])


def test_an_ended_session_counts_the_subagents_and_commands_its_last_turn_started(home: Any) -> None:
    last = [*spawning(ago(hours=3)), result(ago(hours=2, minutes=59), "toolu_agent")]
    last += [*launching(ago(hours=2, minutes=50)), closing(ago(hours=2, minutes=40))]
    home.transcript(last, written=ago(hours=2, minutes=40))
    [session] = home.run().sessions
    assert session.state == "ended"
    assert (session.subagents_finished, session.commands_finished) == (1, 1)
    assert (session.subagents, session.commands, session.children) == ([], [], [])


def test_the_activity_line_is_cut_to_max_chars(home: Any) -> None:
    home.session(pid=4412)
    long = call("toolu_1", "Bash", command="uv run pytest tools/core/agent-watch --maxfail=1 -q")
    home.transcript([prompt(ago(minutes=2), "go"), said(ago(seconds=10), "m1", long)])
    [session] = home.run(max_chars=12).sessions
    assert session.activity is not None and len(session.activity) <= 12
