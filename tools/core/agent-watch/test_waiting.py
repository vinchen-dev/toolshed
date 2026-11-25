from __future__ import annotations

import importlib
from typing import Any

from shed.registry import load_tool_module

module = load_tool_module("agent-watch")
fakes = importlib.import_module(f"{module.__package__}.fakes")
ago = fakes.ago
call = fakes.call
result = fakes.result
said = fakes.said
spawning = fakes.spawning
words = fakes.words
wrapper = fakes.wrapper

TEN_MINUTES_MS = 600_000


def shell_call(when: Any, command: str, **options: Any) -> str:
    """A subagent's record whose Bash call is still open."""
    return said(when, "s1", call("toolu_s1", "Bash", command=command, **options))


def shell(home: Any, command: str, started: Any) -> None:
    """The live shell of a subagent's foreground call."""
    home.process(6701, started, command=wrapper(command, background=False), ppid=4412, cpu=0.0)


def test_a_session_is_working_while_its_subagent_runs_a_quiet_command_inside_its_limit(home: Any) -> None:
    home.session(pid=4412)
    home.transcript(spawning(ago(minutes=12)))
    slow = shell_call(ago(minutes=6), "make slow", timeout=TEN_MINUTES_MS)
    home.subagent("a111", [slow], use_id="toolu_agent")
    shell(home, "make slow", ago(minutes=6))
    report = home.run()
    [session] = report.sessions
    [agent] = session.subagents
    assert (session.state, agent.state, session.waiting_on) == ("working", "working", "a111")
    assert (session.quiet_s, report.attention) == (6 * 60, 0)


def test_a_session_is_working_while_its_subagents_shell_started_late(home: Any) -> None:
    home.session(pid=4412)
    home.transcript(spawning(ago(minutes=12)))
    home.subagent("a111", [shell_call(ago(minutes=8), "make test")], use_id="toolu_agent")
    shell(home, "make test", ago(minutes=1))
    report = home.run()
    [session] = report.sessions
    assert (session.state, session.subagents[0].state, report.attention) == ("working", "working", 0)


def test_a_session_stalls_with_the_subagent_it_waits_on_before_its_own_threshold(home: Any) -> None:
    home.session(pid=4412)
    home.transcript(spawning(ago(minutes=4)))
    home.subagent("a111", [shell_call(ago(minutes=3), "make test")], use_id="toolu_agent")
    shell(home, "make test", ago(minutes=3))
    report = home.run()
    [session] = report.sessions
    assert (session.state, session.subagents[0].state, session.waiting_on) == ("stalled", "stalled", "a111")
    assert "quiet for 3m" in session.reason
    assert "no subagent it waits on is making progress" in session.reason
    assert report.attention == 2


def test_a_subagent_waiting_on_its_own_subagent_is_judged_by_it(home: Any) -> None:
    home.session(pid=4412)
    home.transcript(spawning(ago(minutes=12)))
    inner = call("toolu_inner", "Agent", description="run the suite")
    home.subagent("a111", [said(ago(minutes=7), "o1", inner)], use_id="toolu_agent")
    slow = shell_call(ago(minutes=6), "make slow", timeout=TEN_MINUTES_MS)
    nested = {"spawnDepth": 2, "parentAgentId": "a111", "description": "run the suite"}
    home.subagent("c333", [slow], use_id="toolu_inner", **nested)
    shell(home, "make slow", ago(minutes=6))
    report = home.run()
    [session] = report.sessions
    [outer] = session.subagents
    [inner_agent] = outer.subagents
    assert (session.state, outer.state, inner_agent.state) == ("working", "working", "working")
    assert report.attention == 0


def ended_turn_with_a_background_subagent(home: Any, turn_ended: Any, subagent: list[str]) -> None:
    """A busy session whose own turn has ended while the background subagent it started is unfinished."""
    home.session(pid=4412)
    launched = result(ago(minutes=12), "toolu_agent", "launched", isAsync=True, status="async_launched")
    waiting = said(turn_ended, "m2", words("I will wait for the review."), stop="end_turn")
    home.transcript([*spawning(ago(minutes=12)), launched, waiting])
    home.subagent("a111", subagent, use_id="toolu_agent", requestShape="background")


def test_a_session_whose_turn_ended_is_working_while_a_background_subagent_runs_a_quiet_command(
    home: Any,
) -> None:
    slow = shell_call(ago(minutes=6), "make slow", timeout=TEN_MINUTES_MS)
    ended_turn_with_a_background_subagent(home, ago(minutes=11), [slow])
    shell(home, "make slow", ago(minutes=6))
    report = home.run()
    [session] = report.sessions
    assert (session.state, session.subagents[0].state, session.waiting_on) == ("working", "working", "a111")
    assert report.attention == 0


def test_a_session_whose_turn_just_ended_keeps_its_own_clock_when_a_background_subagent_stalls(
    home: Any,
) -> None:
    ended_turn_with_a_background_subagent(home, ago(seconds=30), [result(ago(minutes=8), "toolu_s0")])
    [session] = home.run().sessions
    assert (session.state, session.subagents[0].state, session.quiet_s) == ("working", "stalled", 30)
