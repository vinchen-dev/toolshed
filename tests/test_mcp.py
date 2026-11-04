from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import mcp.types as types
import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from shed.job_tools import JOB_TOOLS
from shed.lib.errors import UsageError
from shed.mcp_server import select_tools
from shed.registry import SHED_ROOT, list_groups
from shed.scaffold import scaffold_tool

SERVER = StdioServerParameters(command=sys.executable, args=["-m", "shed.cli", "mcp"], cwd=SHED_ROOT)


@pytest.fixture
async def session() -> AsyncIterator[ClientSession]:
    async with (
        stdio_client(SERVER) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as s,
    ):
        await s.initialize()
        yield s


@pytest.mark.anyio
async def test_lists_tools_with_schemas_from_pydantic(session: ClientSession) -> None:
    tools = {t.name: t for t in (await session.list_tools()).tools}
    assert {"git-context", "log-watch"} <= set(tools)
    assert "commits" in tools["git-context"].input_schema["properties"]
    assert tools["git-context"].meta == {"version": "0.1.0"}  # agents can tell when a tool's contract moved


@pytest.mark.anyio
async def test_calls_a_tool_and_returns_json_text(session: ClientSession) -> None:
    result = await session.call_tool("git-context", {"commits": 1})
    assert isinstance(result, types.CallToolResult)
    assert not result.is_error
    content = result.content[0]
    assert isinstance(content, types.TextContent)
    assert "branch" in json.loads(content.text)


@pytest.mark.anyio
async def test_invalid_arguments_are_an_error_result(session: ClientSession) -> None:
    result = await session.call_tool("git-context", {"commits": "many"})
    assert isinstance(result, types.CallToolResult)
    assert result.is_error


def _text(result: types.CallToolResult | Any) -> str:
    assert isinstance(result, types.CallToolResult) and not result.is_error, result
    content = result.content[0]
    assert isinstance(content, types.TextContent)
    return content.text


@pytest.mark.anyio
async def test_job_tools_read_and_run_the_cron_records(tmp_path: Path) -> None:
    cron = tmp_path / "cron.toml"
    cron.write_text(
        f'[jobs.ctx]\ntool = "git-context"\nevery = "1h"\nargs = {{ commits = 1 }}\ncwd = "{SHED_ROOT}"\n'
    )
    env = {**os.environ, "SHED_CRON_FILE": str(cron), "SHED_STATE_DIR": str(tmp_path / "state")}
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "shed.cli", "mcp"], cwd=SHED_ROOT, env=env
    )
    async with (
        stdio_client(params) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as s,
    ):
        await s.initialize()
        names = {t.name for t in (await s.list_tools()).tools}
        assert {"jobs-status", "job-runs", "job-result", "job-run"} <= names
        before = json.loads(_text(await s.call_tool("jobs-status", {})))
        assert before["health"]["state"] == "off" and before["jobs"][0]["name"] == "ctx"
        ran = json.loads(_text(await s.call_tool("job-run", {"job": "ctx"})))
        assert ran["ok"] and "branch" in ran["result"]
        runs = json.loads(_text(await s.call_tool("job-runs", {"job": "ctx"})))
        assert runs[0]["id"] == ran["id"] and runs[0]["ok"]
        full = json.loads(_text(await s.call_tool("job-result", {"job": "ctx", "part": "all"})))
        assert full["id"] == ran["id"] and "branch" in full["result"] and isinstance(full["text"], str)
        latest = json.loads(_text(await s.call_tool("job-result", {"job": "ctx"})))
        assert "result" in latest and "text" not in latest
        after = json.loads(_text(await s.call_tool("jobs-status", {})))
        assert after["health"]["headline"] == "All healthy" and after["jobs"][0]["runs"] == 1
        missing = await s.call_tool("job-result", {"job": "nope"})
        assert isinstance(missing, types.CallToolResult) and missing.is_error


def test_groups_are_the_tools_folders(tmp_path: Path) -> None:
    tools = tmp_path / "tools"
    scaffold_tool("grp/echo-a", tools_dir=tools)
    scaffold_tool("grp/sub/echo-b", tools_dir=tools)
    scaffold_tool("echo-c", tools_dir=tools)
    assert list_groups(tools) == {"": ["echo-c"], "grp": ["echo-a"], "grp/sub": ["echo-b"]}
    job_names = [t.name for t in JOB_TOOLS]
    assert [t.name for t in select_tools(["grp/sub", "jobs"], tools)] == ["echo-b", *job_names]
    assert [t.name for t in select_tools(["grp/", "grp"], tools)] == ["echo-a"]
    assert len(select_tools((), tools)) == 3 + len(JOB_TOOLS)
    with pytest.raises(UsageError, match=r'Unknown group "nope" \(groups: grp, grp/sub, jobs\)'):
        select_tools(["nope"], tools)


def _shed(*args: str) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, "-m", "shed.cli", *args]
    return subprocess.run(cmd, capture_output=True, text=True, cwd=SHED_ROOT, check=False)


def test_mcp_groups_command_lists_the_repo_folders() -> None:
    listed = _shed("mcp", "groups")
    assert listed.returncode == 0, listed.stderr
    lines = listed.stdout.splitlines()  # only base and jobs exist on every device
    assert "base: api-call, git-context, log-watch" in lines
    assert "jobs: jobs-status, job-runs, job-result, job-run" in lines
    assert all(": " in line for line in lines)
    bad = _shed("mcp", "--group", "nope")
    assert bad.returncode == 2 and 'Unknown group "nope"' in bad.stderr
    assert _shed("mcp", "--group").returncode == 2 and _shed("mcp", "extra").returncode == 2


@pytest.mark.anyio
async def test_group_flags_limit_the_served_tools() -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "shed.cli", "mcp", "--group", "base", "--group", "jobs"],
        cwd=SHED_ROOT,
    )
    async with (
        stdio_client(params) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as s,
    ):
        await s.initialize()
        names = [t.name for t in (await s.list_tools()).tools]
        assert names == [
            "api-call",
            "git-context",
            "log-watch",
            "jobs-status",
            "job-runs",
            "job-result",
            "job-run",
        ]
        missing = await s.call_tool("nope", {})
        assert isinstance(missing, types.CallToolResult) and missing.is_error
