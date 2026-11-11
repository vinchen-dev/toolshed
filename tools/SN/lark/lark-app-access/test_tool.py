from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import pytest

from shed import ToolContext, ToolError
from shed.registry import load_tool, load_tool_module

module = load_tool_module("lark-app-access")
approval = load_tool_module("lark-approval")
lark = importlib.import_module(f"{approval.__package__}.lark_client")
TOOL = load_tool("lark-app-access")

APP_INFO = "/application/v6/applications/id"


class FakeClient:
    """Answers by path: a dict body, or a LarkError to raise; the token call can fail too."""

    def __init__(self, responses: dict[str, Any], token_error: str | None = None) -> None:
        self.responses = responses
        self.token_error = token_error
        self.calls: list[tuple[str, dict[str, Any] | None]] = []
        self.closed = False

    def access_token(self) -> str:
        if self.token_error:
            raise lark.LarkError(self.token_error)
        return "tok"

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((path, params))
        answer = self.responses.get(path, lark.LarkError(f"GET {path} failed: code=1770002 msg=not found"))
        if isinstance(answer, Exception):
            raise answer
        return answer

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(approval, "ENV_FILE", tmp_path / "absent.env")
    monkeypatch.setenv("LARK_APP_ID", "id")
    monkeypatch.setenv("LARK_APP_SECRET", "secret")
    monkeypatch.delenv("LARK_APPROVAL_CODE", raising=False)


def install(monkeypatch: pytest.MonkeyPatch, fake: FakeClient) -> FakeClient:
    monkeypatch.setattr(module, "make_client", lambda *_: fake)
    return fake


def ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(cwd=tmp_path, log=lambda _: None)


def test_classify_uses_lark_permission_codes_and_messages() -> None:
    assert (
        module.classify(lark.LarkError("GET /x failed: code=99991672 msg=Access denied"))[0]
        == "scope-missing"
    )
    assert module.classify(lark.LarkError("GET /x failed: code=99991679 msg=x"))[0] == "scope-missing"
    assert (
        module.classify(lark.LarkError("GET /x failed: code=1770032 msg=No Permission"))[0] == "scope-missing"
    )
    assert module.classify(lark.LarkError("GET /x failed: code=1770002 msg=not found")) == (
        "error",
        "GET /x failed: code=1770002 msg=not found",
    )


def test_probes_every_capability_with_a_target(
    env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = install(
        monkeypatch,
        FakeClient(
            {
                APP_INFO: {"data": {"app": {"app_name": "Finance bot", "status": 1}}},
                "/approval/v4/approvals/APPR1": {"data": {}},
                "/docx/v1/documents/DocTok12345": lark.LarkError("GET x failed: code=99991672 msg=denied"),
                "/drive/v1/files": {"data": {"files": []}},
                "/wiki/v2/spaces/get_node": lark.LarkError("GET x failed: code=131006 msg=node not found"),
            }
        ),
    )
    params = TOOL.input(
        approval="APPR1",
        doc="https://x.larksuite.com/docx/DocTok12345",
        folder="https://x.larksuite.com/drive/folder/FldTok12345",
        wiki="WikiTok1234",
    )
    report = TOOL.run(params, ctx(tmp_path))
    assert (report.app_id, report.app_name, report.app_status, report.token_ok) == (
        "id",
        "Finance bot",
        "enabled",
        True,
    )
    by = {p.capability: p for p in report.probes}
    assert by["approval"].status == "ok" and by["drive"].status == "ok"
    assert by["docx"].status == "scope-missing" and by["docx"].scope_hint == "docx:document:readonly"
    assert by["wiki"].status == "error" and "131006" in by["wiki"].detail
    assert by["bitable"].status == "not-probed" and by["bitable"].target is None
    assert ("/drive/v1/files", {"folder_token": "FldTok12345", "page_size": 1}) in fake.calls
    assert ("/wiki/v2/spaces/get_node", {"token": "WikiTok1234"}) in fake.calls
    assert report.summary == "2 ok, 1 scope missing, 1 errors, 1 not probed"
    assert fake.closed
    text = module.render(report)
    assert text.startswith('app id "Finance bot" (enabled): token ok\n')
    assert (
        "docx      scope-missing  https://x.larksuite.com/docx/DocTok12345  needs docx:document:readonly?"
        in text
    )
    assert "bitable   not-probed     -  no target given" in text
    assert text.rstrip().endswith(report.summary)


def test_token_failure_stops_before_any_probe(
    env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = install(
        monkeypatch, FakeClient({}, token_error="tenant_access_token failed: code=10003 msg=bad secret")
    )
    report = TOOL.run(TOOL.input(doc="DocTok12345"), ctx(tmp_path))
    assert report.token_ok is False and report.app_name is None and fake.calls == []
    assert {p.status for p in report.probes} == {"not-probed"} and len(report.probes) == 5
    assert "10003" in report.probes[0].detail
    assert "token FAILED" in module.render(report) and "refused" in report.summary
    assert fake.closed


def test_app_info_refusal_and_approval_default_from_env(
    env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LARK_APPROVAL_CODE", "ENVCODE")
    fake = install(
        monkeypatch,
        FakeClient(
            {
                APP_INFO: lark.LarkError("GET x failed: code=99991672 msg=denied"),
                "/approval/v4/approvals/ENVCODE": {"data": {}},
                "/bitable/v1/apps/BasTok12345/tables": {"data": {"items": []}},
            }
        ),
    )
    report = TOOL.run(
        TOOL.input(bitable="https://x.larksuite.com/base/BasTok12345?table=tbl1"), ctx(tmp_path)
    )
    assert (report.app_name, report.app_status) == (None, "unknown (scope missing)")
    by = {p.capability: p for p in report.probes}
    assert by["approval"].status == "ok" and by["approval"].target == "ENVCODE"
    assert by["bitable"].status == "ok"
    assert ("/bitable/v1/apps/BasTok12345/tables", {"page_size": 1}) in fake.calls


def test_a_link_of_the_wrong_kind_is_refused(
    env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    install(monkeypatch, FakeClient({APP_INFO: {"data": {"app": {}}}}))
    with pytest.raises(ToolError, match="--doc target is a wiki link, expected docx"):
        TOOL.run(TOOL.input(doc="https://x.larksuite.com/wiki/WikiTok1234"), ctx(tmp_path))


def test_missing_credentials_fail_cleanly(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(approval, "ENV_FILE", tmp_path / "absent.env")
    monkeypatch.delenv("LARK_APP_ID", raising=False)
    monkeypatch.delenv("LARK_APP_SECRET", raising=False)
    with pytest.raises(ToolError, match="Missing LARK_APP_ID/LARK_APP_SECRET"):
        TOOL.run(TOOL.input(), ctx(tmp_path))
