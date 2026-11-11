from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from shed import ToolContext, ToolError
from shed.registry import load_tool, load_tool_module

module = load_tool_module("sls-logs")
TOOL = load_tool("sls-logs")
CTX = ToolContext(cwd=Path.cwd(), log=lambda _: None)


class FakeLog:
    def __init__(self, epoch: int, contents: dict[str, Any]) -> None:
        self._epoch = epoch
        self._contents = contents

    def get_time(self) -> int:
        return self._epoch

    def get_contents(self) -> dict[str, Any]:
        return self._contents


class FakeResponse:
    def __init__(self, logs: list[FakeLog]) -> None:
        self._logs = logs

    def get_logs(self) -> list[FakeLog]:
        return self._logs


class FakeClient:
    def __init__(self, logs: list[FakeLog]) -> None:
        self.logs = logs
        self.calls: list[dict[str, Any]] = []

    def get_log_all(
        self, project: str, logstore: str, start: int, end: int, *, query: str, reverse: bool
    ) -> Iterator[FakeResponse]:
        self.calls.append(
            {"project": project, "logstore": logstore, "start": start, "end": end, "query": query}
        )
        yield FakeResponse(self.logs[:2])
        yield FakeResponse(self.logs[2:])


@pytest.fixture
def no_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep tests away from the real ~/.config/aliyun-sls.env."""
    monkeypatch.setattr(module, "ENV_FILE", tmp_path / "absent.env")


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, no_env_file: None) -> FakeClient:
    logs = [
        FakeLog(
            1_760_940_000 + i, {"__tag__:_container_name_": "pms-session-manager-bdo", "content": f"line {i}"}
        )
        for i in range(3)
    ]
    fake = FakeClient(logs)
    monkeypatch.setattr(module, "make_client", lambda *_: fake)
    monkeypatch.setenv("ALIBABA_CLOUD_ACCESS_KEY_ID", "id")
    monkeypatch.setenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "secret")
    return fake


@pytest.mark.parametrize(
    ("greps", "raw", "expected"),
    [
        ([], None, module.BASE_FILTER),
        (["relogin"], None, f'{module.BASE_FILTER} AND ("relogin")'),
        (["a", "b"], None, f'{module.BASE_FILTER} AND ("a" OR "b")'),
        (["ignored"], "custom query", "custom query"),
    ],
)
def test_build_query(greps: list[str], raw: str | None, expected: str) -> None:
    assert module.build_query(greps, raw) == expected


def test_parse_manila_accepts_minutes_and_seconds() -> None:
    assert module.parse_manila("2026-10-20 14:12:30") - module.parse_manila("2026-10-20 14:12") == 30


def test_bad_time_is_a_validation_error() -> None:
    with pytest.raises(ValueError, match="bad time"):
        TOOL.input(start="20/10/2026")


def test_missing_credentials(monkeypatch: pytest.MonkeyPatch, no_env_file: None) -> None:
    monkeypatch.delenv("ALIBABA_CLOUD_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET", raising=False)
    with pytest.raises(ToolError, match="Missing ALIBABA_CLOUD_ACCESS_KEY_ID"):
        TOOL.run(TOOL.input(), CTX)


def test_run_returns_lines_and_passes_the_query(client: FakeClient) -> None:
    result = TOOL.run(TOOL.input(start="2026-10-20 14:00", end="2026-10-20 14:10", grep=["relogin"]), CTX)
    assert result.count == 3
    assert result.truncated is False
    assert [line.content for line in result.lines] == ["line 0", "line 1", "line 2"]
    assert result.lines[0].container == "pms-session-manager-bdo"
    call = client.calls[0]
    assert call["query"] == f'{module.BASE_FILTER} AND ("relogin")'
    assert call["end"] - call["start"] == 600
    assert TOOL.render is not None
    assert "[pms-session-manager-bdo] line 0" in TOOL.render(result)


def test_limit_truncates(client: FakeClient) -> None:
    result = TOOL.run(TOOL.input(limit=2), CTX)
    assert result.count == 2
    assert result.truncated is True


def test_sdk_failure_is_a_tool_error(monkeypatch: pytest.MonkeyPatch, no_env_file: None) -> None:
    from aliyun.log import LogException

    class Unauthorized:
        def get_log_all(self, *args: Any, **kwargs: Any) -> Iterator[FakeResponse]:
            raise LogException("Unauthorized", "denied by sts or ram, action: log:GetLogStoreLogs", "req-1")
            yield FakeResponse([])

    monkeypatch.setattr(module, "make_client", lambda *_: Unauthorized())
    monkeypatch.setenv("ALIBABA_CLOUD_ACCESS_KEY_ID", "id")
    monkeypatch.setenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "secret")
    with pytest.raises(
        ToolError,
        match="SLS query failed for platform-qat-aliyun-logs/platform-pms-qat: Unauthorized: denied",
    ):
        TOOL.run(TOOL.input(), CTX)
