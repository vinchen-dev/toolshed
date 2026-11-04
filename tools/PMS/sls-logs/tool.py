"""Query Alibaba Cloud SLS logs, by default the BDO session-manager and auto-deposit containers.

Credentials come from the environment or ~/.config/aliyun-sls.env (chmod 600):
ALIBABA_CLOUD_ACCESS_KEY_ID, ALIBABA_CLOUD_ACCESS_KEY_SECRET, optional SLS_ENDPOINT.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from shed import Tool, ToolContext, ToolError
from shed.lib.env import require_env
from shed.lib.manila import format_manila
from shed.lib.manila import parse_manila as _parse_manila

ENV_FILE = Path.home() / ".config" / "aliyun-sls.env"
DEFAULT_ENDPOINT = "ap-southeast-1.log.aliyuncs.com"
DEFAULT_PROJECT = "platform-qat-aliyun-logs"
DEFAULT_LOGSTORE = "platform-pms-qat"
BASE_FILTER = (
    "(__tag__:_container_name_: pms-session-manager-bdo "
    'OR (__tag__:_container_name_: pms-auto-deposit AND "[BDO]"))'
)


def parse_manila(text: str) -> int:
    """Parse 'YYYY-MM-DD HH:MM[:SS]' as Manila time into epoch seconds."""
    return int(_parse_manila(text).timestamp())


class Input(BaseModel):
    minutes: int = Field(30, ge=1, description="Look back N minutes from the end time")
    start: str | None = Field(
        None, description="Start time, Manila 'YYYY-MM-DD HH:MM[:SS]'; overrides --minutes"
    )
    end: str | None = Field(None, description="End time, Manila 'YYYY-MM-DD HH:MM[:SS]' (default: now)")
    grep: list[str] = Field(default_factory=list, description="Keyword filter; repeat to OR several")
    query: str | None = Field(None, description="Full SLS query; replaces the BDO base filter and --grep")
    limit: int = Field(500, ge=1, description="Maximum log lines to return")
    project: str = Field(DEFAULT_PROJECT, description="SLS project")
    logstore: str = Field(DEFAULT_LOGSTORE, description="SLS logstore")

    @field_validator("start", "end")
    @classmethod
    def _check_time(cls, value: str | None) -> str | None:
        if value is not None:
            parse_manila(value)
        return value


class LogLine(BaseModel):
    time: str
    container: str
    content: str


class LogResult(BaseModel):
    project: str
    logstore: str
    query: str
    start: str
    end: str
    count: int
    truncated: bool
    lines: list[LogLine]


def build_query(greps: Sequence[str], raw_query: str | None) -> str:
    """Return the SLS search query: the base filter AND any keyword filters (OR-ed together)."""
    if raw_query:
        return raw_query
    if not greps:
        return BASE_FILTER
    keywords = " OR ".join(f'"{g}"' for g in greps)
    return f"{BASE_FILTER} AND ({keywords})"


def make_client(endpoint: str, key_id: str, key_secret: str) -> Any:
    """Build the SLS client. Imported lazily so `shed list` stays fast; patched in tests."""
    try:
        from aliyun.log import LogClient
    except ImportError as exc:
        raise ToolError("Missing SDK: run `uv add aliyun-log-python-sdk` in tool-shed") from exc
    return LogClient(endpoint, key_id, key_secret)


def query_failures() -> tuple[type[Exception], ...]:
    """Exception types the SDK raises for a failed query. Imported lazily, like the client."""
    try:
        from aliyun.log import LogException
    except ImportError:
        return ()
    return (LogException,)


def _describe(exc: Exception) -> str:
    """The SDK's str() is a JSON blob; reduce it to 'code: message' when it parses."""
    try:
        detail = json.loads(str(exc))
        return f"{detail['errorCode']}: {detail['errorMessage']}"
    except (ValueError, KeyError, TypeError):
        return str(exc)


def run(params: Input, ctx: ToolContext) -> LogResult:
    creds = require_env("ALIBABA_CLOUD_ACCESS_KEY_ID", "ALIBABA_CLOUD_ACCESS_KEY_SECRET", env_file=ENV_FILE)

    end = parse_manila(params.end) if params.end else int(time.time())
    start = parse_manila(params.start) if params.start else end - params.minutes * 60
    query = build_query(params.grep, params.query)
    client = make_client(
        os.environ.get("SLS_ENDPOINT", DEFAULT_ENDPOINT),
        creds["ALIBABA_CLOUD_ACCESS_KEY_ID"],
        creds["ALIBABA_CLOUD_ACCESS_KEY_SECRET"],
    )
    ctx.log(f"# {params.project}/{params.logstore} {query}")

    lines: list[LogLine] = []
    truncated = False
    try:
        for response in client.get_log_all(
            params.project, params.logstore, start, end, query=query, reverse=False
        ):
            for log in response.get_logs():
                contents: dict[str, Any] = log.get_contents()
                container = contents.get("__tag__:_container_name_") or contents.get("_container_name_", "-")
                content = contents.get("content")
                lines.append(
                    LogLine(
                        time=format_manila(log.get_time()),
                        container=container,
                        content=content
                        if isinstance(content, str)
                        else json.dumps(contents, ensure_ascii=False),
                    )
                )
                if len(lines) >= params.limit:
                    truncated = True
                    break
            if truncated:
                break
    except query_failures() as exc:
        raise ToolError(f"SLS query failed for {params.project}/{params.logstore}: {_describe(exc)}") from exc

    ctx.log(f"# {len(lines)} lines" + (f" (stopped at --limit {params.limit})" if truncated else ""))
    return LogResult(
        project=params.project,
        logstore=params.logstore,
        query=query,
        start=format_manila(start),
        end=format_manila(end),
        count=len(lines),
        truncated=truncated,
        lines=lines,
    )


def render(result: LogResult) -> str:
    if not result.lines:
        return f"(no log lines between {result.start} and {result.end})"
    return "\n".join(f"{line.time} [{line.container}] {line.content}" for line in result.lines)


TOOL = Tool(
    name="sls-logs",
    description=(
        "Query Alibaba Cloud SLS logs; by default the PMS QAT logstore filtered to the BDO session-manager "
        "and auto-deposit containers. Use it to find relogins, timeouts or errors in a time window."
    ),
    input=Input,
    run=run,
    render=render,
    examples=(
        "shed sls-logs --minutes 30 --grep relogin",
        'shed sls-logs --start "2026-10-20 14:12" --end "2026-10-20 14:15"',
        "shed sls-logs --minutes 60 --grep timeout --grep Timeout --limit 200 --json",
    ),
)
