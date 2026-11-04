from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import anyio.to_thread
import mcp.types as types
from mcp.server import Server, ServerRequestContext
from mcp.server.stdio import stdio_server
from pydantic import ValidationError

from shed.define import Tool, ToolContext
from shed.job_tools import JOB_TOOLS, JOBS_GROUP
from shed.lib.errors import ShedError, UsageError
from shed.lib.output import to_json
from shed.registry import TOOLS_DIR, list_groups, load_all_tools, load_tool

SERVER_NAME = "tool-shed"
SERVER_VERSION = "0.1.0"


def select_tools(groups: Sequence[str] = (), tools_dir: Path = TOOLS_DIR) -> list[Tool[Any, Any]]:
    """Every tool, or only the tools directly inside the named tools/ folders; "jobs" adds the job tools."""
    if not groups:
        return load_all_tools(tools_dir) + list(JOB_TOOLS)
    folders = list_groups(tools_dir)
    chosen: dict[str, Tool[Any, Any]] = {}
    for group in groups:
        key = group.strip("/")
        if key == JOBS_GROUP:
            chosen.update((tool.name, tool) for tool in JOB_TOOLS)
        elif key and key in folders:
            chosen.update((name, load_tool(name, tools_dir)) for name in folders[key])
        else:
            known = ", ".join([*(name for name in folders if name), JOBS_GROUP])
            raise UsageError(f'Unknown group "{group}" (groups: {known})')
    return list(chosen.values())


def _error(message: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=message)], is_error=True)


async def serve_mcp(groups: Sequence[str] = ()) -> None:
    """Serve the tools in tools/ and the job tools, or only the named groups, over MCP on stdio.

    Only protocol traffic touches stdout; tool logs go to stderr.
    """
    tools = select_tools(groups)
    by_name = {tool.name: tool for tool in tools}
    ctx = ToolContext(cwd=Path.cwd(), log=lambda message: print(message, file=sys.stderr))

    async def on_list_tools(
        _request: ServerRequestContext[Any], _params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool.model_validate(
                    {
                        "name": t.name,
                        "description": t.description,
                        "inputSchema": t.input.model_json_schema(),
                        "_meta": {"version": t.version},  # the tool's own version, for agents that compare
                    }
                )
                for t in tools
            ]
        )

    async def on_call_tool(
        _request: ServerRequestContext[Any], params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        tool = by_name.get(params.name)
        if tool is None:
            return _error(f"Unknown tool: {params.name}")
        try:
            model = tool.input.model_validate(params.arguments or {})
        except ValidationError as exc:
            return _error(f"Invalid arguments for {params.name}: {exc}")
        try:
            # Tools are synchronous; a thread keeps a slow one from stalling the protocol loop.
            result = await anyio.to_thread.run_sync(tool.run, model, ctx)
        except ShedError as exc:
            return _error(f"{tool.name} failed: {exc}")
        except Exception as exc:  # boundary: a crashing tool must not take the server down
            return _error(f"{tool.name} crashed: {type(exc).__name__}: {exc}")
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=to_json(result, compact=True))]
        )

    server: Server[Any] = Server(
        SERVER_NAME, version=SERVER_VERSION, on_list_tools=on_list_tools, on_call_tool=on_call_tool
    )
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())
