from __future__ import annotations

import importlib
from typing import Any

import pytest
from pydantic import ValidationError

from shed.registry import load_tool, load_tool_module

module = load_tool_module("agent-watch")
fakes = importlib.import_module(f"{module.__package__}.fakes")
TOOL = load_tool("agent-watch")
MODEL, OTHER, BUSY = fakes.MODEL, fakes.OTHER, fakes.BUSY
ago, result, reading, finished, spawning = (
    fakes.ago,
    fakes.result,
    fakes.reading,
    fakes.finished,
    fakes.spawning,
)
building, only_command = fakes.building, fakes.only_command
codex_turn, codex_call = fakes.codex_turn, fakes.codex_call

CID = "01a1c87c-9743-7bf0-9c9a-db1274762458"
NOTES = "/tmp/work/notes"


def mixed(home: Any) -> None:
    """A working Claude session with a stalled background subagent, an idle one, and a Codex session."""
    home.session(pid=4412)
    launched = result(ago(minutes=12), "toolu_agent", "launched", isAsync=True, status="async_launched")
    home.transcript([*spawning(ago(minutes=12)), launched, *reading(ago(seconds=10))])
    home.subagent(
        "a111", [result(ago(minutes=8), "toolu_s0")], use_id="toolu_agent", requestShape="background"
    )
    home.session(pid=4413, sid=OTHER, status="idle", name="notes-4f", cwd=NOTES)
    home.transcript(finished(ago(hours=3)), sid=OTHER, cwd=NOTES)
    home.rollout(CID, [*codex_turn(ago(minutes=2)), codex_call(ago(seconds=10))], started=ago(minutes=2))


def names(report: Any) -> list[str]:
    return [session.name for session in report.sessions]


def test_filters_narrow_the_result_to_an_agent_a_project_or_a_session(home: Any) -> None:
    mixed(home)
    assert names(home.run()) == ["checkout-api-97", "codex exec", "notes-4f"]
    assert names(home.run(agent=["codex"])) == ["codex exec"]
    assert names(home.run(agent=["claude"])) == ["checkout-api-97", "notes-4f"]
    assert names(home.run(project=NOTES)) == ["notes-4f"]
    assert names(home.run(session=OTHER[:8])) == ["notes-4f"]
    assert names(home.run(session="notes-4f")) == ["notes-4f"]


def test_a_state_filter_keeps_the_parents_of_what_matches(home: Any) -> None:
    mixed(home)
    report = home.run(state=["stalled"])
    [session] = report.sessions
    assert (session.name, session.state) == ("checkout-api-97", "working")
    assert [(agent.id, agent.state) for agent in session.subagents] == [("a111", "stalled")]
    assert report.counts == {"stalled": 1, "working": 2, "idle": 1}
    assert names(home.run(state=["idle"])) == ["notes-4f"]


def test_long_text_is_cut_and_command_output_is_only_included_on_request(home: Any) -> None:
    building(home, BUSY)
    home.output("b123", "line 1\nline 2\nline 3\n", written=ago(seconds=20))
    [session] = home.run(max_chars=10).sessions
    assert (session.turn.prompt, session.commands[0].command) == ("run the p…", "npm run b…")
    assert session.commands[0].tail is None
    assert only_command(home, tail=2).tail == ["line 2", "line 3"]


def test_the_text_output_lists_a_commands_last_output_lines_under_it_when_asked(home: Any) -> None:
    building(home, BUSY)
    home.output("b123", "line 1\nline 2\nline 3\n", written=ago(seconds=20))
    assert TOOL.render is not None
    lines = TOOL.render(home.run(tail=2)).splitlines()
    at = next(i for i, line in enumerate(lines) if "command   npm run build" in line)
    assert [line.strip() for line in lines[at + 1 : at + 3]] == ["| line 2", "| line 3"]
    assert "| line" not in TOOL.render(home.run())


def test_bad_inputs_are_refused_as_bad_usage_naming_the_field() -> None:
    for field, value in (("tail", -1), ("state", ["sleeping"]), ("agent", ["gemini"]), ("window", "later")):
        with pytest.raises(ValidationError, match=field):
            TOOL.input(**{field: value})


def test_the_text_output_lists_attention_first_then_live_sessions_then_ended_ones(home: Any) -> None:
    mixed(home)
    home.transcript(finished(ago(hours=5)), sid="33333333-aaaa-bbbb-cccc-000000000003", written=ago(hours=5))
    assert TOOL.render is not None
    lines = TOOL.render(home.run()).splitlines()
    assert lines[0].startswith("1 stalled, 2 working, 1 idle, 1 ended")
    assert lines[0].endswith("taken 2026-11-23 17:00:00, stall after 5m")
    text = "\n".join(lines)
    assert text.index("needs attention") < text.index("\nsessions") < text.index("\nended in the last day")

    def row(start: str) -> int:
        return next(i for i, line in enumerate(lines) if line.strip().startswith(start))

    assert row("stalled      a111") < row("working      checkout-api-97") < row("idle         notes-4f")
    assert row("idle         notes-4f") < row("ended")
    assert MODEL in lines[row("working      checkout-api-97")]
    assert "gpt-6.1-sol" in lines[row("working      codex exec")]
    assert lines[row("working      checkout-api-97") + 1].startswith("    stalled      subagent")


def test_output_lines_that_set_or_send_a_credential_are_withheld_and_mentions_are_kept(home: Any) -> None:
    building(home, BUSY)
    secret = [
        "Authorization: Bearer abc123",
        "export API_KEY=xyz",
        "DB_PASSWORD: hunter2",
        "PGPASSWORD=hunter2",
        "export DB_PASS=hunter2",
        "MYSQL_PWD=hunter2",
        "clientSecret=abc123",
        'accessToken: "eyJhbGciOi"',
        "SECRET_KEY_BASE=abc",
        "passphrase: correct horse",
        "connecting to postgres://admin:hunter2@db.internal/app",
        "using key ghp_abcdefghijklmnopqrstuvwxyz0123456789",
        '{"password": "hunter2"}',
        "'api_key': 'abc123'",
        "secret-key: abc",
        "mysql --password=hunter2 app",
        "-----BEGIN RSA PRIVATE KEY-----",
        "max_tokens: 4096",
    ]
    plain = [
        "SyntaxError: Unexpected token '}'",
        "3 tests passed, 1200 tokens",
        "password reset email sent",
        "access-token expired",
        "x-api-key header is missing",
        "usage: gh auth login --with-token < file",
        "compiled 12 modules in 3.1s",
    ]
    home.output("b123", "\n".join([*secret, *plain]) + "\n", written=ago(seconds=20))
    withheld = "[withheld: may carry credentials]"
    assert only_command(home, tail=len(secret) + len(plain)).tail == [withheld] * len(secret) + plain


def test_a_credential_that_the_cut_would_split_is_withheld_not_shown_in_part(home: Any) -> None:
    building(home, BUSY)
    lines = [
        "pushing with ghp_abcdefghijklmnopqrstuvwxyz0123456789",
        "fetching https://admin:hunter2hunter2@db.internal/app",
        "a long line of plain build output that is only cut",
    ]
    home.output("b123", "\n".join(lines) + "\n", written=ago(seconds=20))
    withheld = "[withheld: may carry credentials]"
    assert only_command(home, tail=3, max_chars=24).tail == [withheld, withheld, "a long line of plain bu…"]


def test_the_text_output_says_how_many_subagents_are_running_and_finished(home: Any) -> None:
    mixed(home)
    assert TOOL.render is not None
    lines = TOOL.render(home.run()).splitlines()
    [session] = [line for line in lines if line.strip().startswith("working      checkout-api-97")]
    assert "subagents: 1 running, 0 finished" in session
