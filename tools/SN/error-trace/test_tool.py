from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from shed import ToolContext, ToolError
from shed.registry import load_tool, load_tool_module

module = load_tool_module("error-trace")
sls = load_tool_module("sls-logs")
TOOL = load_tool("error-trace")

STACK = (
    "TypeError: Cannot read properties of undefined (reading 'amount')\n"
    "    at buildProposal (/app/server/src/handler/proposalHandler.js:212:17)\n"
    "    at processTicksAndRejections (node:internal/process/task_queues:95:5)\n"
    "    at /app/node_modules/express/lib/router/layer.js:95:5"
)


def line(time: str, container: str, content: str) -> Any:
    return sls.LogLine(time=time, container=container, content=content)


def result(lines: list[Any], truncated: bool = False) -> Any:
    return sls.LogResult(
        project="p",
        logstore="l",
        query="q",
        start="2026-11-10 09:00:00",
        end="2026-11-11 09:00:00",
        count=len(lines),
        truncated=truncated,
        lines=lines,
    )


LINES = [
    line("2026-11-10 10:05:00", "pms-a", f"[Process 3] proposal 6a04112786 failed\n{STACK}"),
    line("2026-11-10 10:40:10", "pms-b", f"[Process 1] proposal 7b15223897 failed\n{STACK}"),
    line(
        "2026-11-11 08:01:30",
        "pms-a",
        "08:01:30 3|api | request 41 ok\x002026-11-11T08:01:30.5+08:00 stdout F 08:01:30 3|api | "
        "Cannot read properties of undefined (reading 'x')\x002026-11-11T08:01:30.6+08:00 stderr F "
        "    at other (/app/server/src/unrelated.js:9:1)\x002026-11-11T08:01:31+08:00 stdout F done",
    ),
    line(
        "2026-11-11 08:02:00",
        "finance-ai",
        json.dumps({"msg": "Cannot read properties of undefined", "trace": "2955310f", "level": 50}),
    ),
]


class FakeSls:
    def __init__(self, pulls: list[Any]) -> None:
        self.pulls = pulls
        self.calls: list[dict[str, Any]] = []

    def input(self, **kwargs: Any) -> dict[str, Any]:
        return kwargs

    def run(self, params: dict[str, Any], ctx: ToolContext) -> Any:
        self.calls.append(params)
        pull = self.pulls[len(self.calls) - 1]
        if isinstance(pull, Exception):
            raise pull
        return pull


def ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(cwd=tmp_path, log=lambda _: None)


def fake(monkeypatch: pytest.MonkeyPatch, *pulls: Any) -> FakeSls:
    stub = FakeSls(list(pulls))
    monkeypatch.setattr(module, "_sls", lambda: stub)
    return stub


def test_groups_an_error_into_a_timeline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    stub = fake(monkeypatch, result(LINES))
    trace = TOOL.run(TOOL.input(match=["Cannot read properties"], env="prod", hours=24), ctx(tmp_path))
    call = stub.calls[0]
    assert (call["project"], call["logstore"]) == ("platform-prod-aliyun-logs", "platform-pms-prod")
    assert call["query"] == '"Cannot read properties"' and call["minutes"] == 1440 and call["limit"] == 2000
    assert (trace.count, trace.first_seen, trace.last_seen) == (
        4,
        "2026-11-10 10:05:00",
        "2026-11-11 08:02:00",
    )
    assert [(b.hour, b.count) for b in trace.per_hour] == [
        ("2026-11-10 10:00", 2),
        ("2026-11-11 08:00", 2),
    ]
    assert [(c.name, c.count) for c in trace.containers] == [("pms-a", 2), ("pms-b", 1), ("finance-ai", 1)]
    top = trace.signatures[0]
    assert top.count == 2 and (top.first_seen, top.last_seen) == (
        "2026-11-10 10:05:00",
        "2026-11-10 10:40:10",
    )
    assert top.signature == "TypeError: Cannot read properties of undefined (reading 'amount')"
    assert {s.signature for s in trace.signatures} >= {"Cannot read properties of undefined"}
    assert [(f.name, f.count) for f in trace.frames] == [
        ("/app/server/src/handler/proposalHandler.js:212", 2),
        ("/app/server/src/unrelated.js:9", 1),
    ]
    assert "N:N:N N|api | Cannot read properties of undefined (reading 'x')" in [
        s.signature for s in trace.signatures
    ]
    assert not trace.truncated and not trace.notes and not trace.context
    text = TOOL.render(trace) if TOOL.render else ""
    assert text.startswith('# PROD "Cannot read properties"\nwindow 2026-11-10 09:00:00 to')
    assert "4 matches, first 2026-11-10 10:05:00, last 2026-11-11 08:02:00" in text
    assert "containers: pms-a x2, pms-b x1, finance-ai x1" in text
    assert "  2026-11-10 10:00  2" in text and "  x2  /app/server/src/handler/proposalHandler.js:212" in text


def test_matches_are_anded_inside_a_container(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    stub = fake(monkeypatch, result([]))
    trace = TOOL.run(
        TOOL.input(match=['say "hi"', "2955310f"], container="finance-ai", start="2026-11-05 10:00"),
        ctx(tmp_path),
    )
    call = stub.calls[0]
    assert call["query"] == '__tag__:_container_name_: finance-ai AND "say \\"hi\\"" AND "2955310f"'
    assert call["project"] == "platform-qat-aliyun-logs" and call["start"] == "2026-11-05 10:00"
    assert trace.count == 0 and trace.first_seen is None and "no matches" in trace.notes[0]
    assert "0 matches" in (TOOL.render(trace) if TOOL.render else "")
    with pytest.raises(ToolError, match="needs some text"):
        TOOL.run(TOOL.input(match=["  "]), ctx(tmp_path))


def test_ids_and_numbers_collapse_into_one_signature(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    lines = [
        line(
            "2026-11-11 08:00:00", "pms-auto-deposit", "[BDO] Timeout 30000ms on page 3 for 6a041127868ed0af"
        ),
        line(
            "2026-11-11 08:05:00", "pms-auto-deposit", "[BDO] Timeout 30000ms on page 12 for 68e51b4bd7e0f8a0"
        ),
        line("2026-11-11 08:06:00", "pms-auto-deposit", "[BDO] Timeout waiting for selector"),
    ]
    fake(monkeypatch, result(lines, truncated=True))
    trace = TOOL.run(TOOL.input(match=["[BDO]", "Timeout"], samples=1), ctx(tmp_path))
    assert len(trace.signatures) == 1 and trace.other_signatures == 1
    assert trace.signatures[0].signature == "[BDO] Timeout Nms on page N for <id>"
    assert trace.signatures[0].sample.endswith("68e51b4bd7e0f8a0") and trace.signatures[0].count == 2
    assert trace.truncated and "stopped at --limit 2000" in trace.notes[0] and not trace.frames
    text = TOOL.render(trace) if TOOL.render else ""
    assert "3+ matches" in text and "(+1 more signatures; raise --samples)" in text
    assert "code frames: none in these lines" in text


def test_around_pulls_the_container_near_the_latest_hit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    nearby = [
        line("2026-11-11 08:01:45", "finance-ai", json.dumps({"msg": "query_payments metric=list"})),
        line("2026-11-11 08:01:50", "finance-ai", "login password=hunter2"),
        line(
            "2026-11-11 08:01:55", "finance-ai", "generateToken err for client 7 with form password=hunter2"
        ),
        line("2026-11-11 08:02:00", "finance-ai", json.dumps({"msg": "Cannot read properties of undefined"})),
    ]
    stub = fake(monkeypatch, result(LINES), result(nearby))
    trace = TOOL.run(TOOL.input(match=["Cannot read"], env="prod", around=30), ctx(tmp_path))
    second = stub.calls[1]
    assert second["query"] == "__tag__:_container_name_: finance-ai" and second["limit"] == 500
    assert (second["start"], second["end"]) == ("2026-11-11 08:01:30", "2026-11-11 08:02:31")
    assert trace.context_container == "finance-ai"
    assert [c.content for c in trace.context] == [
        "query_payments metric=list",
        module.WITHHELD,
        "generateToken err for client 7 with form" + module.CUT,
        "Cannot read properties of undefined",
    ]
    assert "hunter2" not in trace.model_dump_json()
    assert "around the latest occurrence (finance-ai):" in (TOOL.render(trace) if TOOL.render else "")


def test_a_failed_pull_names_the_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake(monkeypatch, ToolError("SLS query failed for p/l: Unauthorized"))
    with pytest.raises(ToolError, match="prod: SLS query failed"):
        TOOL.run(TOOL.input(match=["x"], env="prod"), ctx(tmp_path))


def test_line_helpers() -> None:
    assert module.message_of('{"level": 50, "msg": "boom", "stack": "Error: boom\\n  at a.js:1:2"}') == (
        "boom\nError: boom\n  at a.js:1:2"
    )
    assert module.message_of("{not json") == "{not json" and module.message_of('{"a": 1}') == '{"a": 1}'
    parts = module.split_record("first\x002026-11-11T08:00:00.1+08:00 stdout F second TIMEOUT a\nthird a")
    assert parts == ["first", "second TIMEOUT a", "third a"]
    assert module.occurrences(parts, ["timeout", "a"]) == [1] and module.occurrences(parts, ["a", "zz"]) == [
        1,
        2,
    ]
    assert module.occurrences(parts, ["absent"]) == [0] and module.occurrences([], ["x"]) == []
    assert module.stack_after(
        ["boom", "  at f (a.js:1:2)", "3|api | at g (b.js:3:4)", "next", "at h"], 0
    ) == ("boom\n  at f (a.js:1:2)\n3|api | at g (b.js:3:4)")
    assert (
        module.safe("request failed, the access token: abc", 100) == "request failed, the access" + module.CUT
    )
    assert module.safe("generateToken failed with 401", 100) == "generateToken failed with 401"
    assert module.signature_of("user 123 trace 3f2a9c1b-0000-4a1b-8c2d-123456789abc") == "user N trace <id>"
    assert module.signature_of("deadbeef is a word, defaced too") == "deadbeef is a word, defaced too"
    assert module.signature_of("otp is 123456") == module.WITHHELD
    assert len(module.safe("x" * 500, 100)) == 100
    assert module.frames_of(
        'Traceback\n  File "/srv/app/sync/lark_sync.py", line 88, in run\n'
        '  File "/usr/lib/python3.12/site-packages/httpx/_client.py", line 10\n'
        "  at run (src/jobs/sync.ts:14:3)"
    ) == ["/srv/app/sync/lark_sync.py:88", "src/jobs/sync.ts:14"]
