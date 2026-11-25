from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from shed import ToolContext, ToolError
from shed.registry import load_tool, load_tool_module

module = load_tool_module("sls-logs")
login_module = load_tool_module("sls-login")
TOOL = load_tool("sls-logs")
CTX = ToolContext(cwd=Path.cwd(), log=lambda _: None)
FAR_FUTURE = 4_000_000_000  # epoch seconds, year 2096


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
        self.creds: Any = None

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
    """Keep tests away from the real ~/.config files and from an SLS_AUTH set in the shell."""
    monkeypatch.setattr(module, "ENV_FILE", tmp_path / "absent.env")
    monkeypatch.setattr(module, "SESSION_FILE", tmp_path / "session.json")
    monkeypatch.setenv("SHED_BROWSER_DIR", str(tmp_path / "browser"))
    monkeypatch.delenv("SLS_AUTH", raising=False)


@pytest.fixture
def session(no_env_file: None) -> None:
    """A saved sign-in that never expires during a test run."""
    module.save_session(module.Credentials("id", "secret", "token"), FAR_FUTURE)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, session: None) -> FakeClient:
    logs = [
        FakeLog(
            1_760_940_000 + i, {"__tag__:_container_name_": "pms-session-manager-bdo", "content": f"line {i}"}
        )
        for i in range(3)
    ]
    fake = FakeClient(logs)

    def build(endpoint: str, creds: Any) -> FakeClient:
        fake.creds = creds
        return fake

    monkeypatch.setattr(module, "make_client", build)
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


def test_without_a_sign_in_it_says_how_to_sign_in(no_env_file: None) -> None:
    with pytest.raises(ToolError, match="No SLS sign-in yet: run `shed sls-login`"):
        TOOL.run(TOOL.input(), CTX)


def test_an_expired_sign_in_with_nothing_to_renew_from_says_when_it_ended(no_env_file: None) -> None:
    module.save_session(module.Credentials("id", "secret", "token"), 1_760_940_000)
    with pytest.raises(
        ToolError,
        match=(
            r"expired at 2025-10-20 14:00:00 Manila time and could not renew itself "
            r"\(there is no earlier sign-in to renew from\): run `shed sls-login` and sign in again"
        ),
    ):
        TOOL.run(TOOL.input(), CTX)


def test_an_expired_sign_in_is_renewed_before_the_query(
    monkeypatch: pytest.MonkeyPatch, client: FakeClient
) -> None:
    module.save_session(module.Credentials("id", "secret", "old-token"), 1_760_940_000)
    renewed = module.Credentials("new-id", "new-secret", "new-token")
    monkeypatch.setattr(login_module, "renew", lambda: module.save_session(renewed, FAR_FUTURE))
    assert TOOL.run(TOOL.input(), CTX).count == 3
    assert client.creds == renewed


def test_a_renewal_that_fails_is_reported_with_its_reason(
    monkeypatch: pytest.MonkeyPatch, no_env_file: None
) -> None:
    def renew() -> None:
        raise ToolError("the single sign-on asked for a person to sign in")

    module.save_session(module.Credentials("id", "secret", "token"), 1_760_940_000)
    monkeypatch.setattr(login_module, "renew", renew)
    with pytest.raises(
        ToolError,
        match=r"could not renew itself \(the single sign-on asked for a person to sign in\): run `shed",
    ):
        TOOL.run(TOOL.input(), CTX)


def test_a_damaged_session_file_is_a_tool_error(no_env_file: None) -> None:
    module.SESSION_FILE.write_text('{"access_key_id": "id"}', encoding="utf-8")
    with pytest.raises(ToolError, match="Cannot read the SLS sign-in"):
        TOOL.run(TOOL.input(), CTX)


def test_the_session_file_is_readable_by_its_owner_only(session: None) -> None:
    assert module.SESSION_FILE.stat().st_mode & 0o777 == 0o600


def test_the_sign_in_token_reaches_the_client(client: FakeClient) -> None:
    TOOL.run(TOOL.input(), CTX)
    assert client.creds == module.Credentials("id", "secret", "token")


def test_key_mode_uses_the_access_key_pair(monkeypatch: pytest.MonkeyPatch, client: FakeClient) -> None:
    monkeypatch.setenv("SLS_AUTH", "key")
    monkeypatch.setenv("ALIBABA_CLOUD_ACCESS_KEY_ID", "key-id")
    monkeypatch.setenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "key-secret")
    TOOL.run(TOOL.input(), CTX)
    assert client.creds == module.Credentials("key-id", "key-secret", None)


def test_key_mode_names_the_missing_key(monkeypatch: pytest.MonkeyPatch, no_env_file: None) -> None:
    monkeypatch.setenv("SLS_AUTH", "key")
    monkeypatch.delenv("ALIBABA_CLOUD_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET", raising=False)
    with pytest.raises(ToolError, match="Missing ALIBABA_CLOUD_ACCESS_KEY_ID"):
        TOOL.run(TOOL.input(), CTX)


def test_an_unknown_auth_setting_is_rejected(monkeypatch: pytest.MonkeyPatch, no_env_file: None) -> None:
    monkeypatch.setenv("SLS_AUTH", "sso")
    with pytest.raises(ToolError, match="SLS_AUTH is 'sso': use 'login'"):
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


def test_sdk_failure_is_a_tool_error(monkeypatch: pytest.MonkeyPatch, session: None) -> None:
    from aliyun.log import LogException

    class Unauthorized:
        def get_log_all(self, *args: Any, **kwargs: Any) -> Iterator[FakeResponse]:
            raise LogException("Unauthorized", "denied by sts or ram, action: log:GetLogStoreLogs", "req-1")
            yield FakeResponse([])

    monkeypatch.setattr(module, "make_client", lambda *_: Unauthorized())
    with pytest.raises(
        ToolError,
        match="SLS query failed for platform-qat-aliyun-logs/platform-pms-qat: Unauthorized: denied",
    ):
        TOOL.run(TOOL.input(), CTX)
