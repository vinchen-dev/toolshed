from __future__ import annotations

import importlib
from typing import Any

from shed.registry import load_tool_module

module = load_tool_module("agent-watch")
fakes = importlib.import_module(f"{module.__package__}.fakes")
launching = fakes.launching
BUSY = fakes.BUSY
ago = fakes.ago
building = fakes.building
call = fakes.call
notice = fakes.notice
prompt = fakes.prompt
result = fakes.result
said = fakes.said
spawning = fakes.spawning
wrapper = fakes.wrapper


def test_a_call_that_ran_only_after_an_approval_gets_the_shell_started_later(home: Any) -> None:
    home.session(pid=4412)
    tests = call("toolu_1", "Bash", command="uv run pytest")
    home.transcript([prompt(ago(minutes=9), "run the tests"), said(ago(minutes=8), "m1", tests)])
    home.process(5901, ago(minutes=10), command=wrapper("rtk lint", background=False), ppid=4412, cpu=0.0)
    [waiting] = home.run().sessions
    assert (waiting.state, waiting.activity_pid) == ("stalled", None)
    assert "may be waiting for your approval" in waiting.reason
    assert [command.id for command in waiting.commands] == ["pid-5901"]

    home.process(6001, ago(minutes=1), command=wrapper("rtk pytest", background=False), ppid=4412, cpu=0.0)
    [running] = home.run().sessions
    assert (running.state, running.activity_pid) == ("working", 6001)
    assert "may be waiting for your approval" not in running.reason


def test_a_subagents_rewritten_shell_is_matched_by_when_it_started(home: Any) -> None:
    home.session(pid=4412)
    home.transcript(spawning(ago(minutes=2)))
    viewing = said(ago(seconds=3), "s1", call("toolu_s1", "Bash", command="gh pr view 12"))
    home.subagent("a111", [viewing], use_id="toolu_agent")
    home.process(6501, ago(seconds=3), command=wrapper("rtk gh pr view 12"), ppid=4412, cpu=0.0)
    [session] = home.run().sessions
    assert session.commands == []


def test_every_open_shell_call_of_a_session_gets_its_own_shell(home: Any) -> None:
    home.session(pid=4412)
    first = call("toolu_a", "Bash", command="make a", timeout=600000)
    second = call("toolu_b", "Bash", command="make b", timeout=600000)
    home.transcript([prompt(ago(minutes=8), "build both"), said(ago(minutes=7), "m1", first, second)])
    home.process(6001, ago(minutes=7), command=wrapper("make a", background=False), ppid=4412, cpu=0.0)
    home.process(6002, ago(minutes=7), command=wrapper("make b", background=False), ppid=4412, cpu=0.0)
    report = home.run()
    [session] = report.sessions
    assert (session.state, session.commands, report.attention) == ("working", [], 0)


def stopping(when: Any, task_id: str = "b123") -> list[str]:
    """The session's own stop call for a background command, and its result."""
    stop = said(when, f"m-stop-{task_id}", call(f"toolu_stop_{task_id}", "TaskStop", task_id=task_id))
    return [stop, result(when, f"toolu_stop_{task_id}")]


def test_a_finished_command_whose_shell_is_still_alive_is_listed_once(home: Any) -> None:
    building(home, *stopping(ago(minutes=4)), BUSY)
    [session] = home.run().sessions
    assert [(command.id, command.state) for command in session.commands] == [("b123", "done")]


def test_a_finished_command_whose_shell_a_hook_rewrote_is_still_listed_once(home: Any) -> None:
    home.session(pid=4412)
    lines = [
        prompt(ago(minutes=40), "build it"),
        *launching(ago(minutes=31)),
        *stopping(ago(minutes=4)),
        BUSY,
    ]
    home.transcript(lines)
    home.process(7001, ago(minutes=31), command=wrapper("rtk npm run build"), ppid=4412, cpu=0.0)
    [session] = home.run().sessions
    assert [(command.id, command.state) for command in session.commands] == [("b123", "done")]


def test_an_open_call_with_no_process_does_not_take_a_finished_commands_shell(home: Any) -> None:
    home.session(pid=4412)
    side = result(ago(minutes=9), "toolu_agent", "launched", isAsync=True, status="async_launched")
    built = [*launching(ago(minutes=1)), *stopping(ago(seconds=30))]
    home.transcript([*spawning(ago(minutes=9)), side, *built, BUSY])
    unapproved = said(ago(minutes=3), "s1", call("toolu_s1", "Bash", command="make test"))
    home.subagent("a111", [unapproved], use_id="toolu_agent", requestShape="background")
    home.process(7001, ago(minutes=1), command=wrapper("npm run build"), ppid=4412, cpu=0.0)
    [session] = home.run().sessions
    [agent] = session.subagents
    assert [(command.id, command.state) for command in session.commands] == [("b123", "done")]
    assert agent.state == "stalled"
    assert "may be waiting for your approval" in agent.reason


def test_a_late_started_shell_stays_with_its_call_not_with_a_finished_command(home: Any) -> None:
    home.session(pid=4412)
    side = result(ago(minutes=9), "toolu_agent", "launched", isAsync=True, status="async_launched")
    built = [*launching(ago(seconds=60)), notice(ago(seconds=30), "b123", "completed")]
    home.transcript([*spawning(ago(minutes=9)), side, *built, BUSY])
    approved_late = said(ago(minutes=3), "s1", call("toolu_s1", "Bash", command="make test"))
    home.subagent("a111", [approved_late], use_id="toolu_agent", requestShape="background")
    rewritten = wrapper("rtk make test", background=False)  # a hook rewrote the text, so only time can tell
    home.process(6701, ago(seconds=58), command=rewritten, ppid=4412, cpu=0.0)
    report = home.run()
    [session] = report.sessions
    assert (session.subagents[0].state, report.attention) == ("working", 0)
    assert [(command.id, command.state) for command in session.commands] == [("b123", "done")]


def test_a_command_run_again_keeps_its_own_shell(home: Any) -> None:
    home.session(pid=4412)
    again = launching(ago(minutes=9), task_id="b456", command="npm run build")
    lines = [
        prompt(ago(minutes=40), "build it"),
        *launching(ago(minutes=31)),
        *stopping(ago(minutes=10)),
        *again,
        BUSY,
    ]
    home.transcript(lines)
    home.process(7501, ago(minutes=9), command=wrapper("npm run build"), ppid=4412, cpu=0.0)
    home.process(7502, ago(minutes=9), command="node build.js", ppid=7501, cpu=0.0)
    home.probe.ports = {7502: [5173]}
    report = home.run()
    [session] = report.sessions
    assert [(c.id, c.state, c.pid) for c in session.commands] == [
        ("b456", "serving", 7501),
        ("b123", "done", None),
    ]
    assert report.attention == 0


def test_a_foreground_call_keeps_its_shell_when_an_earlier_background_command_had_the_same_text(
    home: Any,
) -> None:
    home.session(pid=4412)
    tests = call("toolu_now", "Bash", command="npm run build")
    earlier = [*launching(ago(minutes=31)), notice(ago(minutes=20), "b123", "failed")]
    home.transcript([prompt(ago(minutes=40), "build it"), *earlier, said(ago(seconds=30), "m-now", tests)])
    home.process(
        7601, ago(seconds=30), command=wrapper("npm run build", background=False), ppid=4412, cpu=0.0
    )
    [session] = home.run().sessions
    assert (session.state, session.activity_pid) == ("working", 7601)
    assert [(c.id, c.state) for c in session.commands] == [("b123", "failed")]


def test_a_subagents_shell_call_follows_the_same_rule_as_a_sessions(home: Any) -> None:
    home.session(pid=4412)
    home.transcript(spawning(ago(minutes=12)))
    slow = said(ago(minutes=8), "s1", call("toolu_s1", "Bash", command="make slow", timeout=600000))
    home.subagent("a111", [slow], use_id="toolu_agent")
    [waiting] = home.run().sessions[0].subagents
    assert waiting.state == "stalled"
    assert "may be waiting for your approval" in waiting.reason

    quick = said(ago(minutes=8), "s1", call("toolu_s1", "Bash", command="make test"))
    home.subagent("a111", [quick], use_id="toolu_agent")
    home.process(6701, ago(minutes=1), command=wrapper("make test"), ppid=4412, cpu=0.0)
    [running] = home.run().sessions[0].subagents
    assert running.state == "working"
    assert "may be waiting for your approval" not in running.reason
