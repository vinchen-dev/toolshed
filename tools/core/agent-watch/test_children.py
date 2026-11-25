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
finished = fakes.finished
notice = fakes.notice
only_command = fakes.only_command
prompt = fakes.prompt
reading = fakes.reading
result = fakes.result
said = fakes.said
spawning = fakes.spawning
words = fakes.words
wrapper = fakes.wrapper


def test_a_session_waiting_on_a_subagent_is_working_while_the_subagent_writes(home: Any) -> None:
    home.session(pid=4412)
    home.transcript(spawning(ago(minutes=12)))
    grep = call("toolu_s1", "Grep", pattern="retry")
    home.subagent("a111", [said(ago(seconds=20), "s1", grep, model="claude-haiku-5-5")], use_id="toolu_agent")
    report = home.run()
    [session] = report.sessions
    [agent] = session.subagents
    assert (session.state, agent.state, session.quiet_s) == ("working", "working", 20)
    assert (agent.id, agent.type, agent.description) == ("a111", "Explore", "find the retry policy")
    assert (agent.mode, agent.depth, agent.activity, agent.tool_calls) == ("foreground", 1, "Grep: retry", 1)
    assert (agent.model, agent.quiet_s) == ("claude-haiku-5-5", 20)
    assert session.waiting_on == "a111"
    assert report.counts == {"working": 2}


def test_a_stalled_subagent_stalls_the_session_waiting_on_it(home: Any) -> None:
    home.session(pid=4412)
    home.transcript(spawning(ago(minutes=12)))
    home.subagent("a111", [result(ago(minutes=11), "toolu_s0")], use_id="toolu_agent")
    report = home.run()
    [session] = report.sessions
    [agent] = session.subagents
    assert (session.state, agent.state) == ("stalled", "stalled")
    assert (session.waiting_on, agent.model) == ("a111", None)
    assert "find the retry policy" in session.reason
    assert "11m" in agent.reason
    assert (report.attention, report.counts) == (2, {"stalled": 2})


def test_a_subagent_stalled_on_any_pending_call_may_be_waiting_for_approval(home: Any) -> None:
    home.session(pid=4412)
    home.transcript(spawning(ago(minutes=12)))
    edit = call("toolu_s1", "Edit", file_path="/tmp/x.py")
    home.subagent("a111", [said(ago(minutes=8), "s1", edit)], use_id="toolu_agent")
    [agent] = home.run().sessions[0].subagents
    assert agent.state == "stalled"
    assert "may be waiting for your approval" in agent.reason


def test_a_finished_subagent_is_done_or_failed_and_drops_off_after_the_window(home: Any) -> None:
    home.session(pid=4412)
    after = said(ago(seconds=10), "m2", call("toolu_2", "Read", file_path="/tmp/x.py"))
    report_back = said(ago(minutes=10), "s1", words("Found it."), stop="end_turn")
    home.subagent("a111", [report_back], use_id="toolu_agent")

    home.transcript([*spawning(ago(minutes=12)), result(ago(minutes=10), "toolu_agent", "Found it."), after])
    assert [a.state for a in home.run().sessions[0].subagents] == ["done"]

    failed = result(ago(minutes=10), "toolu_agent", "agent crashed", error=True)
    home.transcript([*spawning(ago(minutes=12)), failed, after])
    assert [a.state for a in home.run().sessions[0].subagents] == ["failed"]

    assert home.run(window="5m").sessions[0].subagents == []


def test_a_background_subagent_is_done_once_its_metadata_says_finished(home: Any) -> None:
    home.session(pid=4412)
    launched = result(ago(minutes=12), "toolu_agent", "launched", isAsync=True, status="async_launched")
    home.transcript([*spawning(ago(minutes=12)), launched, *reading(ago(seconds=10))])
    quiet = [result(ago(minutes=8), "toolu_s0")]
    home.subagent("a111", quiet, use_id="toolu_agent", requestShape="background")
    assert [a.state for a in home.run().sessions[0].subagents] == ["stalled"]

    home.subagent("a111", quiet, use_id="toolu_agent", requestShape="background", finished=True)
    assert [(a.state, a.mode) for a in home.run().sessions[0].subagents] == [("done", "background")]


def test_nested_subagents_hang_under_their_parent_and_the_session_counts_them(home: Any) -> None:
    home.session(pid=4412)
    home.transcript(spawning(ago(minutes=3)))
    inner = call("toolu_inner", "Agent", description="check the docs")
    home.subagent("a111", [said(ago(seconds=30), "s1", inner)], use_id="toolu_agent")
    reading_docs = [said(ago(seconds=5), "n1", call("toolu_n1", "Read", file_path="/tmp/docs.md"))]
    nested = {"spawnDepth": 2, "parentAgentId": "a111", "agentType": "claude-code-guide"}
    home.subagent("c333", reading_docs, use_id="toolu_inner", description="check the docs", **nested)
    lint_done = [said(ago(minutes=2), "f1", words("Lint is clean."), stop="end_turn")]
    home.subagent(
        "d444", lint_done, use_id="toolu_lint", description="lint", requestShape="background", finished=True
    )
    [session] = home.run().sessions
    assert [(a.id, a.state) for a in session.subagents] == [("a111", "working"), ("d444", "done")]
    assert [(a.id, a.depth, a.type) for a in session.subagents[0].subagents] == [
        ("c333", 2, "claude-code-guide")
    ]
    assert (session.subagents_running, session.subagents_finished) == (2, 1)


def test_a_background_command_with_recent_output_or_cpu_is_working(home: Any) -> None:
    building(home, BUSY)
    home.output("b123", "compiling...", written=ago(seconds=20))
    command = only_command(home)
    assert (command.id, command.command, command.description) == ("b123", "npm run build", "Build")
    assert (command.state, command.pid, command.runtime_s) == ("working", 7001, 31 * 60)
    assert (command.quiet_s, command.output_bytes, command.started) == (20, 12, "2026-11-23 16:29:00")


def test_a_silent_background_command_that_uses_cpu_is_working(home: Any) -> None:
    building(home, BUSY, cpu=35.0)
    home.output("b123", "compiling...", written=ago(minutes=22))
    command = only_command(home)
    assert command.state == "working"
    assert "35.0% CPU" in command.reason


def test_a_background_command_with_no_output_no_cpu_and_no_port_is_stalled(home: Any) -> None:
    building(home, BUSY)
    home.output("b123", "compiling...", written=ago(minutes=22))
    command = only_command(home)
    assert (command.state, command.quiet_s) == ("stalled", 22 * 60)
    for part in ("Running 31m", "no output for 22m", "0.0% CPU", "no listening port"):
        assert part in command.reason


def test_a_background_command_that_listens_on_a_port_is_serving(home: Any) -> None:
    building(home, BUSY)
    home.output("b123", "ready", written=ago(minutes=22))
    home.probe.ports = {7002: [3000]}
    command = only_command(home)
    assert (command.state, command.ports) == ("serving", [3000])
    assert "Listening on :3000" in command.reason


def test_a_background_command_with_a_completion_notice_is_done_or_failed(home: Any) -> None:
    building(home, notice(ago(minutes=5), "b123", "completed"), BUSY, alive=False)
    assert only_command(home).state == "done"
    building(home, notice(ago(minutes=5), "b123", "failed"), BUSY, alive=False)
    assert only_command(home).state == "failed"
    assert home.run(window="2m").sessions[0].commands == []


def test_a_background_command_that_vanished_without_a_notice_is_gone(home: Any) -> None:
    building(home, BUSY, alive=False)
    home.output("b123", "compiling...", written=ago(minutes=19))
    command = only_command(home)
    assert (command.state, command.pid) == ("gone", None)
    assert "no completion notice" in command.reason and "19m" in command.reason


def test_a_background_command_the_session_stopped_itself_is_done(home: Any) -> None:
    stop = said(ago(minutes=4), "m-stop", call("toolu_stop", "TaskStop", task_id="b123"))
    building(home, stop, result(ago(minutes=4), "toolu_stop"), BUSY, alive=False)
    command = only_command(home)
    assert command.state == "done"
    assert "Stopped by the session 4m ago" in command.reason


def test_an_idle_session_with_a_stalled_command_stays_idle_and_the_command_is_flagged(home: Any) -> None:
    done = said(ago(minutes=30), "m-end", words("I will wait for the build."), stop="end_turn")
    building(home, done, status="idle")
    home.output("b123", "compiling...", written=ago(minutes=22))
    report = home.run()
    [session] = report.sessions
    assert (session.state, [c.state for c in session.commands]) == ("idle", ["stalled"])
    assert (report.attention, report.counts) == (1, {"idle": 1, "stalled": 1})
    assert (session.commands_running, session.commands_finished) == (1, 0)


def test_helper_processes_are_counted_and_a_tool_call_gets_the_process_it_started(home: Any) -> None:
    home.session(pid=4412)
    home.process(5001, ago(hours=2), command="shed mcp --group core", ppid=4412)
    home.process(5002, ago(minutes=3), command="caffeinate -i -t 300", ppid=4412)
    slow = call("toolu_1", "Bash", command="make slow", timeout=600000)
    home.transcript([prompt(ago(minutes=8), "run the slow suite"), said(ago(minutes=7), "m1", slow)])
    home.process(6001, ago(minutes=7), command=wrapper("make slow", background=False), ppid=4412, cpu=0.0)
    home.process(6002, ago(minutes=7), command="make slow", ppid=6001, cpu=41.5)
    [session] = home.run().sessions
    assert (session.helpers, session.activity_pid, session.commands) == (2, 6001, [])
    assert "pid 6001" in session.reason and "41.5% CPU" in session.reason


def test_without_port_information_nothing_is_serving_and_the_reason_says_so(home: Any) -> None:
    building(home, BUSY)
    home.output("b123", "ready", written=ago(minutes=22))
    home.probe.ports = {7002: [3000]}
    home.probe.lsof_error = "lsof timed out after 10s"
    report = home.run()
    [command] = report.sessions[0].commands
    assert command.state == "stalled"
    assert "listening ports unknown" in command.reason
    assert "listening ports unavailable: lsof timed out after 10s" in report.notes


def test_a_multi_line_command_with_quotes_is_matched_to_the_tool_call_that_runs_it(home: Any) -> None:
    home.session(pid=4412)
    script = "python3 - <<'EOF'\nprint('hi')\nEOF"
    running = said(ago(seconds=30), "m1", call("toolu_1", "Bash", command=script))
    home.transcript([prompt(ago(minutes=1), "run the script"), running])
    home.process(6001, ago(seconds=30), command=wrapper(script, background=False), ppid=4412, cpu=0.0)
    [session] = home.run().sessions
    assert (session.activity_pid, session.commands) == (6001, [])


def test_a_tool_call_gets_its_process_even_when_a_hook_rewrote_the_command(home: Any) -> None:
    home.session(pid=4412)
    asked = said(ago(seconds=30), "m1", call("toolu_1", "Bash", command="uv run pytest"))
    home.transcript([prompt(ago(minutes=1), "run the tests"), asked])
    home.process(6001, ago(seconds=29), command=wrapper("rtk pytest", background=False), ppid=4412, cpu=0.0)
    [session] = home.run().sessions
    assert (session.activity_pid, session.commands) == (6001, [])


def test_a_subagents_own_shell_is_not_reported_as_a_background_command(home: Any) -> None:
    home.session(pid=4412)
    home.transcript(spawning(ago(minutes=2)))
    viewing = said(ago(seconds=3), "s1", call("toolu_s1", "Bash", command="gh pr view 12"))
    home.subagent("a111", [viewing], use_id="toolu_agent")
    home.process(6501, ago(seconds=3), command=wrapper("gh pr view 12"), ppid=4412, cpu=0.0)
    report = home.run()
    [session] = report.sessions
    assert (session.commands, session.helpers) == ([], 0)
    assert report.counts == {"working": 2}


def test_a_command_started_before_the_read_window_is_listed_from_its_process(home: Any) -> None:
    home.session(pid=4412)
    home.transcript(reading(ago(seconds=10)))
    home.process(7101, ago(hours=2), command=wrapper("npm run dev -- --host '0.0.0.0'"), ppid=4412, cpu=0.0)
    home.process(7102, ago(hours=2), command="node vite", ppid=7101, cpu=0.0)
    home.probe.ports = {7102: [5173]}
    command = only_command(home)
    assert (command.id, command.command) == ("pid-7101", "npm run dev -- --host '0.0.0.0'")
    assert (command.state, command.ports, command.runtime_s) == ("serving", [5173], 2 * 3600)
    assert (command.quiet_s, command.output_bytes) == (None, None)


def test_a_command_known_only_by_its_process_is_never_stalled_for_want_of_output(home: Any) -> None:
    home.session(pid=4412)
    home.transcript(reading(ago(seconds=10)))
    home.process(7201, ago(hours=1), command=wrapper("npm run watch"), ppid=4412, cpu=0.0)
    command = only_command(home)
    assert (command.id, command.state, command.quiet_s) == ("pid-7201", "working", None)
    for part in ("Running 1h 00m", "output not tracked", "0.0% CPU", "no listening port"):
        assert part in command.reason


def test_a_background_command_started_by_a_subagent_is_listed_under_the_session(home: Any) -> None:
    home.session(pid=4412)
    home.transcript(spawning(ago(minutes=5)))
    home.subagent(
        "a111", launching(ago(minutes=4), task_id="b777", command="npm run e2e"), use_id="toolu_agent"
    )
    home.process(7301, ago(minutes=4), command=wrapper("npm run e2e"), ppid=4412, cpu=0.0)
    home.output("b777", "12 passed", written=ago(seconds=15))
    command = only_command(home)
    assert (command.id, command.command, command.state, command.pid) == (
        "b777",
        "npm run e2e",
        "working",
        7301,
    )


def test_a_session_waiting_for_its_own_model_reply_does_not_name_a_background_subagent(home: Any) -> None:
    home.session(pid=4412)
    launched = result(ago(minutes=2), "toolu_agent", "launched", isAsync=True, status="async_launched")
    home.transcript([*spawning(ago(minutes=2)), launched, result(ago(seconds=5), "toolu_other")])
    home.subagent(
        "a111", [result(ago(seconds=20), "toolu_s0")], use_id="toolu_agent", requestShape="background"
    )
    [session] = home.run().sessions
    assert (session.state, session.waiting_on) == ("working", None)
    assert "waiting for the model" in session.reason


def test_a_background_command_keeps_its_process_when_a_hook_rewrote_the_command(home: Any) -> None:
    home.session(pid=4412)
    home.transcript([prompt(ago(minutes=40), "start the dashboard"), *launching(ago(minutes=31)), BUSY])
    home.process(7001, ago(minutes=31), command=wrapper("rtk npm run build"), ppid=4412, cpu=0.0)
    home.process(7002, ago(minutes=31), command="node build.js", ppid=7001, cpu=0.0)
    home.probe.ports = {7002: [3000]}
    command = only_command(home)
    assert (command.state, command.pid, command.ports) == ("serving", 7001, [3000])


def test_a_busy_session_whose_turn_ended_names_the_background_subagent_it_waits_on(home: Any) -> None:
    home.session(pid=4412)
    launched = result(ago(minutes=12), "toolu_agent", "launched", isAsync=True, status="async_launched")
    waiting = said(ago(minutes=11), "m2", words("I will wait for the review."), stop="end_turn")
    home.transcript([*spawning(ago(minutes=12)), launched, waiting])
    home.subagent(
        "a111", [result(ago(minutes=8), "toolu_s0")], use_id="toolu_agent", requestShape="background"
    )
    [session] = home.run().sessions
    assert (session.state, session.waiting_on) == ("stalled", "a111")
    assert "waiting on subagent Explore: find the retry policy" in session.reason

    home.subagent(
        "a111", [result(ago(seconds=30), "toolu_s0")], use_id="toolu_agent", requestShape="background"
    )
    [session] = home.run().sessions
    assert (session.state, session.waiting_on, session.quiet_s) == ("working", "a111", 30)


def test_a_shell_call_that_never_started_a_process_may_be_waiting_for_approval(home: Any) -> None:
    home.session(pid=4412)
    deploy = call("toolu_1", "Bash", command="docker compose up", timeout=600000)
    home.transcript([prompt(ago(minutes=9), "bring the stack up"), said(ago(minutes=8), "m1", deploy)])
    [session] = home.run().sessions
    assert session.state == "stalled"
    assert "may be waiting for your approval" in session.reason and "limit 10m" in session.reason

    shell = wrapper("docker compose up", background=False)
    home.process(6001, ago(minutes=8), command=shell, ppid=4412, cpu=0.0)
    [session] = home.run().sessions
    assert session.state == "working"
    assert "may be waiting for your approval" not in session.reason


def test_a_subagents_background_command_is_done_once_the_session_was_told_it_finished(home: Any) -> None:
    home.session(pid=4412)
    home.transcript([*spawning(ago(minutes=5)), notice(ago(minutes=1), "b777", "completed")])
    home.subagent(
        "a111", launching(ago(minutes=4), task_id="b777", command="npm run e2e"), use_id="toolu_agent"
    )
    command = only_command(home)
    assert (command.id, command.state) == ("b777", "done")


def test_a_hung_model_call_does_not_hide_behind_a_busy_background_subagent(home: Any) -> None:
    home.session(pid=4412)
    launched = result(ago(minutes=25), "toolu_agent", "launched", isAsync=True, status="async_launched")
    home.transcript([*spawning(ago(minutes=25)), launched, result(ago(minutes=20), "toolu_other")])
    home.subagent(
        "a111", [result(ago(seconds=5), "toolu_s0")], use_id="toolu_agent", requestShape="background"
    )
    [session] = home.run().sessions
    assert (session.state, session.quiet_s, session.waiting_on) == ("stalled", 20 * 60, None)
    assert "waiting for the model" in session.reason
    assert [agent.state for agent in session.subagents] == ["working"]


def test_a_shell_call_with_no_process_is_stalled_once_its_limit_passes_even_inside_the_threshold(
    home: Any,
) -> None:
    home.session(pid=4412)
    tests = call("toolu_1", "Bash", command="uv run pytest")
    home.transcript([prompt(ago(minutes=4), "run the tests"), said(ago(minutes=3), "m1", tests)])
    [session] = home.run(stall_after="10m").sessions
    assert session.state == "stalled"
    assert "limit 2m" in session.reason and "may be waiting for your approval" in session.reason


def test_a_session_held_by_a_quiet_subagent_is_stalled_whatever_other_subagents_do(home: Any) -> None:
    home.session(pid=4412)
    held = call("toolu_held", "Agent", description="trace the bug", subagent_type="Explore", prompt="look")
    side = result(ago(minutes=25), "toolu_agent", "launched", isAsync=True, status="async_launched")
    home.transcript([*spawning(ago(minutes=25)), side, said(ago(minutes=20), "m-held", held)])
    home.subagent(
        "a111", [result(ago(seconds=5), "toolu_s0")], use_id="toolu_agent", requestShape="background"
    )
    home.subagent(
        "h222", [result(ago(minutes=20), "toolu_h0")], use_id="toolu_held", description="trace the bug"
    )
    [session] = home.run().sessions
    assert (session.state, session.waiting_on, session.quiet_s) == ("stalled", "h222", 20 * 60)
    assert sorted((agent.id, agent.state) for agent in session.subagents) == [
        ("a111", "working"),
        ("h222", "stalled"),
    ]
