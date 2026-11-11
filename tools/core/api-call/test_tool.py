from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from shed import ToolContext, ToolError, UsageError
from shed.registry import load_tool, load_tool_module

module = load_tool_module("api-call")
TOOL = load_tool("api-call")
CTX = ToolContext(cwd=Path.cwd(), log=lambda _: None)
CONFIG = """
[pms-qat]
base_url = "https://qat.example.com/api/"
headers = { Authorization = "Bearer secret-token", "x-api-key" = "k1" }
timeout = 5

[bare]
base_url = "https://bare.example.com"
verify = false
"""


@pytest.fixture(autouse=True)
def config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / "api-call.toml"
    path.write_text(CONFIG, encoding="utf-8")
    monkeypatch.setattr(module, "CONFIG_FILE", path)
    return path


@pytest.fixture
def requests(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/text"):
            return httpx.Response(200, text="x" * 50, headers={"content-type": "text/plain"})
        if request.url.path.endswith("/boom"):
            raise httpx.ConnectError("boom")
        return httpx.Response(
            201,
            json={"ok": True, "echo": json.loads(request.content) if request.content else None},
            headers={"set-cookie": "sid=1", "x-trace": "t1"},
        )

    monkeypatch.setattr(
        module, "make_client", lambda timeout, verify: httpx.Client(transport=httpx.MockTransport(handler))
    )
    return seen


def test_profile_supplies_base_url_and_auth(requests: list[httpx.Request]) -> None:
    result = TOOL.run(TOOL.input(profile="pms-qat", url="/v1/status", query=["platform=50", "x=a b"]), CTX)
    assert isinstance(result, module.Response)
    assert (result.status, result.reason, result.method) == (201, "Created", "GET")
    assert result.url == "https://qat.example.com/api/v1/status?platform=50&x=a+b"
    assert result.body == {"ok": True, "echo": None}
    assert result.headers["set-cookie"] == "[redacted]" and result.headers["x-trace"] == "t1"
    sent = requests[0]
    assert sent.headers["authorization"] == "Bearer secret-token" and sent.headers["x-api-key"] == "k1"


def test_header_override_and_json_body(requests: list[httpx.Request]) -> None:
    params = TOOL.input(
        profile="pms-qat", method="POST", url="v1/things", body='{"a": 1}', header=["X-API-Key: k2"]
    )
    result = TOOL.run(params, CTX)
    sent = requests[0]
    assert sent.headers["x-api-key"] == "k2" and "X-API-Key" in sent.headers
    assert sent.headers["content-type"] == "application/json"
    assert result.body["echo"] == {"a": 1}


def test_body_from_file_and_plain_text_response(requests: list[httpx.Request], tmp_path: Path) -> None:
    (tmp_path / "payload.txt").write_text("not json", encoding="utf-8")
    ctx = ToolContext(cwd=tmp_path, log=lambda _: None)
    result = TOOL.run(TOOL.input(url="https://x.example.com/text", body="@payload.txt", max_body=10), ctx)
    assert requests[0].content == b"not json" and "content-type" not in requests[0].headers
    assert (result.body, result.body_truncated, result.content_type) == ("x" * 10, True, "text/plain")
    assert TOOL.render is not None
    assert TOOL.render(result).startswith("HTTP 200 OK")
    assert "[cut at --max-body]" in TOOL.render(result)


def test_curl_mode_prints_the_command_without_sending(requests: list[httpx.Request]) -> None:
    result = TOOL.run(TOOL.input(profile="bare", url="/ping", body="{}", curl=True), CTX)
    assert isinstance(result, module.CurlCommand)
    assert result.command == (
        "curl -sS -X GET https://bare.example.com/ping -H 'Content-Type: application/json' --data-raw '{}' -k"
    )
    assert requests == []


def test_lists_profiles_without_values(config: Path) -> None:
    result = TOOL.run(TOOL.input(list_profiles=True), CTX)
    assert isinstance(result, module.ProfileList)
    assert [(p.name, p.headers, p.timeout, p.verify) for p in result.profiles] == [
        ("pms-qat", ["Authorization", "x-api-key"], 5.0, True),
        ("bare", [], None, False),
    ]
    assert TOOL.render is not None
    assert "secret-token" not in TOOL.render(result)


def test_connection_failure_is_a_tool_error(requests: list[httpx.Request]) -> None:
    with pytest.raises(ToolError, match=r"GET https://x\.example\.com/boom failed: ConnectError: boom"):
        TOOL.run(TOOL.input(url="https://x.example.com/boom"), CTX)


@pytest.mark.parametrize(
    ("kwargs", "error", "match"),
    [
        ({"profile": "nope", "url": "/x"}, ToolError, "unknown profile 'nope'"),
        ({"url": "/relative"}, UsageError, "not absolute"),
        ({"list_profiles": False}, UsageError, "--url is required"),
        ({"url": "https://x.example.com", "header": ["bad"]}, UsageError, "bad --header"),
        ({"url": "https://x.example.com", "query": ["novalue"]}, UsageError, "bad --query"),
    ],
)
def test_bad_inputs(kwargs: dict[str, object], error: type[Exception], match: str) -> None:
    with pytest.raises(error, match=match):
        TOOL.run(TOOL.input(**kwargs), CTX)


def test_broken_config_file_is_reported(config: Path) -> None:
    config.write_text("[x]\nbase_url = 1\n", encoding="utf-8")
    with pytest.raises(ToolError, match=r"\[x\] is invalid"):
        TOOL.run(TOOL.input(list_profiles=True), CTX)
