from __future__ import annotations

import importlib
import json
from typing import Any

from shed.registry import load_tool_module

module = load_tool_module("agent-watch")
fakes = importlib.import_module(f"{module.__package__}.fakes")
ago, reading, wrapper = fakes.ago, fakes.reading, fakes.wrapper
prompt, said, call = fakes.prompt, fakes.said, fakes.call
codex_turn, codex_call, codex_output, codex_done = (
    fakes.codex_turn,
    fakes.codex_call,
    fakes.codex_output,
    fakes.codex_done,
)

CID = "01a1c87c-9743-7bf0-9c9a-db1274762458"


def states(home: Any) -> list[str]:
    return [session.state for session in home.run().sessions]


def test_a_codex_session_says_when_it_started(home: Any) -> None:
    home.rollout(CID, [*codex_turn(ago(minutes=2)), codex_call(ago(seconds=10))], started=ago(minutes=2))
    [session] = home.run().sessions
    assert session.started == "2026-11-23 16:58:00"


def test_a_codex_session_with_an_open_turn_and_fresh_writes_is_working(home: Any) -> None:
    home.rollout(CID, [*codex_turn(ago(minutes=2)), codex_call(ago(seconds=10))], started=ago(minutes=2))
    report = home.run()
    [session] = report.sessions
    assert (session.agent, session.state, session.model) == ("codex", "working", "gpt-6.1-sol")
    assert (session.project, session.entrypoint, session.quiet_s) == ("/tmp/work/tool-shed", "exec", 10)
    assert session.turn is not None
    assert (session.turn.prompt, session.turn.tool_calls) == ("review the stall rule", 1)
    assert session.activity == "exec: git log --oneline"
    assert report.versions == {"codex": ["0.162.1"]}


def test_a_codex_session_is_named_after_its_thread_when_the_index_has_one(home: Any) -> None:
    home.rollout(CID, [*codex_turn(ago(minutes=2)), codex_call(ago(seconds=10))], started=ago(minutes=2))
    index = {"id": CID, "thread_name": "Tidy the changelog", "updated_at": "2026-11-23T08:58:00Z"}
    (home.codex / "session_index.jsonl").write_text(json.dumps(index) + "\n", encoding="utf-8")
    assert [session.name for session in home.run().sessions] == ["Tidy the changelog"]


def test_a_quiet_codex_turn_is_gone_without_a_process_and_stalled_with_one(home: Any) -> None:
    home.rollout(CID, [*codex_turn(ago(minutes=20)), codex_call(ago(minutes=12))], started=ago(minutes=20))
    assert states(home) == ["gone"]
    home.process(8001, ago(minutes=20, seconds=3), command="codex exec review the stall rule")
    [session] = home.run().sessions
    assert (session.state, session.pid) == ("stalled", 8001)
    assert "12m" in session.reason


def test_a_finished_codex_turn_is_ended_without_a_process_and_idle_with_one(home: Any) -> None:
    lines = [*codex_turn(ago(minutes=20)), codex_call(ago(minutes=19)), codex_output(ago(minutes=19))]
    home.rollout(CID, [*lines, codex_done(ago(minutes=18))], started=ago(minutes=20))
    [ended] = home.run().sessions
    assert (ended.state, ended.last_write, ended.activity) == ("ended", "2026-11-23 16:42:00", None)
    home.process(8001, ago(minutes=20, seconds=3), command="/usr/local/bin/codex")
    assert states(home) == ["idle"]
    assert home.run(retain="10m").sessions[0].state == "idle"


def test_a_codex_run_started_by_a_claude_session_is_listed_under_it(home: Any) -> None:
    home.session(pid=4412)
    review = said(ago(minutes=2, seconds=5), "m1", call("toolu_1", "Bash", command="codex exec review"))
    home.transcript([prompt(ago(minutes=3), "get a second opinion"), review])
    shell = wrapper("codex exec review", background=False)
    home.process(6001, ago(minutes=2, seconds=5), command=shell, ppid=4412, cpu=0.0)
    home.process(8001, ago(minutes=2, seconds=3), command="codex exec review", ppid=6001)
    home.rollout(CID, [*codex_turn(ago(minutes=2)), codex_call(ago(seconds=10))], started=ago(minutes=2))
    report = home.run()
    [session] = report.sessions
    assert [(child.agent, child.state, child.pid) for child in session.children] == [
        ("codex", "working", 8001)
    ]
    assert report.counts == {"working": 2}


def test_a_missing_codex_home_is_a_note_and_claude_sessions_still_stand(home: Any) -> None:
    (home.codex / "sessions").rmdir()
    home.session(pid=4412)
    home.transcript(reading(ago(seconds=10)))
    report = home.run()
    assert [session.state for session in report.sessions] == ["working"]
    assert f"no Codex sessions at {home.codex / 'sessions'}" in report.notes


def test_a_claude_run_started_by_a_claude_session_is_listed_under_it(home: Any) -> None:
    home.session(pid=4412)
    review = said(ago(minutes=2, seconds=5), "m1", call("toolu_1", "Bash", command="claude -p review"))
    home.transcript([prompt(ago(minutes=3), "get a second opinion"), review])
    shell = wrapper("claude -p review", background=False)
    home.process(6001, ago(minutes=2, seconds=5), command=shell, ppid=4412, cpu=0.0)
    inner = "44444444-aaaa-bbbb-cccc-000000000004"
    home.session(pid=8101, sid=inner, name="checkout-api-c1", started=ago(minutes=2), alive=False)
    home.process(8101, ago(minutes=2), command="claude -p review", ppid=6001)
    home.transcript(reading(ago(seconds=10)), sid=inner)
    report = home.run()
    [session] = report.sessions
    nested = [(child.name, child.state, child.pid) for child in session.children]
    assert nested == [("checkout-api-c1", "working", 8101)]
    assert report.counts == {"working": 2}


def test_a_claude_run_started_by_a_codex_session_is_listed_under_it(home: Any) -> None:
    home.rollout(CID, [*codex_turn(ago(minutes=2)), codex_call(ago(seconds=10))], started=ago(minutes=2))
    home.process(8001, ago(minutes=2, seconds=3), command="codex exec review the stall rule")
    home.session(pid=8201, name="checkout-api-c2", started=ago(minutes=1), alive=False)
    home.process(8201, ago(minutes=1), command="claude -p check", ppid=8001)
    home.transcript(reading(ago(seconds=10)))
    [session] = home.run().sessions
    assert (session.agent, [child.name for child in session.children]) == ("codex", ["checkout-api-c2"])
