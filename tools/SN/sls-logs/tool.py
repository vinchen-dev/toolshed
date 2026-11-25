"""Query Alibaba Cloud SLS logs, by default the BDO session-manager and auto-deposit containers.

Credentials are the temporary ones `shed sls-login` saves after a browser sign-in; when they run out they are
renewed through sls-login with no window, while the single sign-on session lasts. SLS_AUTH=key, in the
environment or ~/.config/aliyun-sls.env (chmod 600), switches to an AccessKey pair instead:
ALIBABA_CLOUD_ACCESS_KEY_ID, ALIBABA_CLOUD_ACCESS_KEY_SECRET. Optional either way: SLS_ENDPOINT.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from shed import Tool, ToolContext, ToolError
from shed.lib.env import load_env_file, require_env
from shed.lib.manila import format_manila
from shed.lib.manila import parse_manila as _parse_manila
from shed.registry import load_tool_module

ENV_FILE = Path.home() / ".config" / "aliyun-sls.env"
SESSION_FILE = Path.home() / ".config" / "aliyun-sls-session.json"
SIGN_IN = "run `shed sls-login` and sign in"
EXPIRY_MARGIN_S = 60  # a session this close to its end would expire in the middle of a pull
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


@dataclass(frozen=True, slots=True)
class Credentials:
    key_id: str
    key_secret: str
    token: str | None = None
    """The security token of a sign-in session; None for an AccessKey pair."""


def save_session(creds: Credentials, expires: int) -> None:
    """Keep a sign-in session (and the epoch second it ends) for later runs, readable by this user only."""
    payload = {
        "access_key_id": creds.key_id,
        "access_key_secret": creds.key_secret,
        "security_token": creds.token,
        "expires": expires,
    }
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(SESSION_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        os.fchmod(fd, 0o600)  # the mode above only applies when the file is new
        json.dump(payload, handle)


class SessionExpired(ToolError):
    """The saved sign-in has run out; `sls-login` may still be able to renew it with no window."""


def load_session() -> Credentials:
    """The session `sls-login` saved. Raises ToolError when there is none, SessionExpired when it ended."""
    try:
        data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
        creds = Credentials(data["access_key_id"], data["access_key_secret"], data["security_token"])
        expires = int(data["expires"])
        if not all(isinstance(v, str) and v for v in (creds.key_id, creds.key_secret, creds.token)):
            raise ValueError("empty credential")
    except FileNotFoundError as exc:
        raise ToolError(f"No SLS sign-in yet: {SIGN_IN}") from exc
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ToolError(f"Cannot read the SLS sign-in in {SESSION_FILE}: {SIGN_IN} again") from exc
    if expires - EXPIRY_MARGIN_S <= time.time():
        raise SessionExpired(f"The SLS sign-in expired at {format_manila(expires)} Manila time")
    return creds


def _session_credentials() -> Credentials:
    """The saved sign-in, renewed through sls-login first when it has run out."""
    try:
        return load_session()
    except SessionExpired as expired:
        try:
            load_tool_module("sls-login").renew()
        except ToolError as why:
            raise ToolError(f"{expired} and could not renew itself ({why}): {SIGN_IN} again") from why
        return load_session()


def credentials() -> Credentials:
    """This run's credentials: the sls-login session, or the AccessKey pair when SLS_AUTH=key."""
    load_env_file(ENV_FILE)
    auth = os.environ.get("SLS_AUTH", "login")
    if auth == "login":
        return _session_credentials()
    if auth == "key":
        pair = require_env(
            "ALIBABA_CLOUD_ACCESS_KEY_ID", "ALIBABA_CLOUD_ACCESS_KEY_SECRET", env_file=ENV_FILE
        )
        return Credentials(pair["ALIBABA_CLOUD_ACCESS_KEY_ID"], pair["ALIBABA_CLOUD_ACCESS_KEY_SECRET"])
    raise ToolError(
        f"SLS_AUTH is '{auth}': use 'login' (the `shed sls-login` session) or 'key' (an AccessKey pair)"
    )


def make_client(endpoint: str, creds: Credentials) -> Any:
    """Build the SLS client. Imported lazily so `shed list` stays fast; patched in tests."""
    try:
        from aliyun.log import LogClient
    except ImportError as exc:
        raise ToolError("Missing SDK: run `uv add aliyun-log-python-sdk` in tool-shed") from exc
    return LogClient(endpoint, creds.key_id, creds.key_secret, securityToken=creds.token)


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
    creds = credentials()

    end = parse_manila(params.end) if params.end else int(time.time())
    start = parse_manila(params.start) if params.start else end - params.minutes * 60
    query = build_query(params.grep, params.query)
    client = make_client(os.environ.get("SLS_ENDPOINT", DEFAULT_ENDPOINT), creds)
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
