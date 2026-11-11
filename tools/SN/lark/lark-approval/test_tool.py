from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from shed import ToolContext, ToolError
from shed.registry import load_tool, load_tool_module

module = load_tool_module("lark-approval")
TOOL = load_tool("lark-approval")
CTX = ToolContext(cwd=Path.cwd(), log=lambda _: None)


def _detail(code: str, rfp_no: str | None, *, form: object = None) -> dict[str, Any]:
    fields = [
        {"id": "w1", "name": "RFP No.", "type": "serialNumber", "value": rfp_no},
        {"id": "w2", "type": "input"},
    ]
    return {
        "instance_code": code,
        "serial_number": 42,
        "status": "APPROVED",
        "start_time": str(int(datetime(2025, 9, 9, tzinfo=UTC).timestamp() * 1000)),
        "form": json.dumps(fields) if form is None else form,
    }


class FakeClient:
    def __init__(self, details: dict[str, dict[str, Any]], codes: list[str]) -> None:
        self.details = details
        self.codes = codes
        self.listed: list[tuple[str, int, int]] = []
        self.fetched: list[str] = []
        self.closed = False

    def get_instance(self, instance_code: str) -> dict[str, Any]:
        self.fetched.append(instance_code)
        return self.details[instance_code]

    def instance_codes(self, approval_code: str, start_ms: int, end_ms: int) -> Iterator[str]:
        self.listed.append((approval_code, start_ms, end_ms))
        yield from self.codes

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeClient:
    details = {
        "A": _detail("A", "202609-RFP-1"),
        "B": _detail("B", "202609-rfp-1919"),
        "C": _detail("C", "202609-RFP-3"),
    }
    client = FakeClient(details, ["A", "B", "C"])
    monkeypatch.setattr(module, "make_client", lambda *_: client)
    monkeypatch.setattr(module, "ENV_FILE", tmp_path / "absent.env")
    monkeypatch.setenv("LARK_APP_ID", "id")
    monkeypatch.setenv("LARK_APP_SECRET", "secret")
    monkeypatch.setenv("LARK_APPROVAL_CODE", "APPROVAL-1")
    return client


# --- pure helpers -----------------------------------------------------------


def test_parse_date_bare_day_is_a_whole_local_day() -> None:
    start = module.parse_date("2026-09-10", end_of_day=False)
    end = module.parse_date("2026-09-10", end_of_day=True)
    assert end - start == 86_400_000 - 1
    assert datetime.fromtimestamp(start / 1000).hour == 0


def test_parse_date_accepts_iso_and_rejects_junk() -> None:
    expected = int(datetime(2026, 9, 10, tzinfo=UTC).timestamp() * 1000)
    assert module.parse_date("2026-09-10T00:00:00Z", end_of_day=False) == expected
    with pytest.raises(ValueError, match="expected YYYY-MM-DD"):
        module.parse_date("yesterday", end_of_day=False)


def test_month_window_spans_the_month_plus_a_day_each_side() -> None:
    start, end = module.month_window("202612-RFP-7")
    assert datetime.fromtimestamp(start / 1000, UTC) == datetime(2026, 11, 30, tzinfo=UTC)
    assert datetime.fromtimestamp(end / 1000, UTC) == datetime(2027, 1, 2, tzinfo=UTC)


@pytest.mark.parametrize(
    "form",
    [json.dumps([{"name": "X", "value": 1}]), [{"name": "X", "value": 1}], "{not json", None, 5],
)
def test_parse_form_never_raises(form: object) -> None:
    fields = module.parse_form({"form": form})
    assert isinstance(fields, list)


def test_form_data_keys_fall_back_like_parse_form_attachments() -> None:
    fields = [
        {"name": "A", "value": "x"},
        {"id": "w9", "value": None},
        {"id": "w3", "name": "", "type": "input"},
    ]
    assert module.form_data(fields) == {"A": "x", "w9": None, "w3": None}


# --- validation ---------------------------------------------------------------


def test_bad_dates_are_validation_errors() -> None:
    with pytest.raises(ValueError, match="expected YYYY-MM-DD"):
        TOOL.input(target="X", start="10/09/2026")
    with pytest.raises(ValueError, match="start is after end"):
        TOOL.input(target="X", start="2026-09-11", end="2026-09-10")


# --- lookups -------------------------------------------------------------------


def test_lookup_by_instance_code(fake: FakeClient) -> None:
    result = TOOL.run(TOOL.input(target="B"), CTX)
    assert result.scanned is None
    assert result.instance.instance_code == "B"
    assert result.instance.serial_number == "42"
    assert result.instance.start_time == "2025-09-09T00:00:00+00:00"
    assert result.instance.rfp_no == "202609-rfp-1919"
    assert result.instance.form_data == {"RFP No.": "202609-rfp-1919", "w2": None}
    assert result.instance.raw is None
    assert fake.listed == []
    assert fake.closed is True


def test_lookup_by_rfp_no_scans_the_month_and_stops_at_the_match(fake: FakeClient) -> None:
    result = TOOL.run(TOOL.input(target="202609-RFP-1919", raw=True), CTX)
    assert result.scanned == 2
    assert result.instance.instance_code == "B"
    assert result.instance.raw == fake.details["B"]
    assert fake.fetched == ["A", "B"]
    approval_code, start_ms, end_ms = fake.listed[0]
    assert approval_code == "APPROVAL-1"
    assert (start_ms, end_ms) == module.month_window("202609-RFP-1919")


def test_explicit_window_overrides_the_month(fake: FakeClient) -> None:
    TOOL.run(TOOL.input(target="202609-RFP-1919", start="2026-09-10", end="2026-09-10"), CTX)
    _, start_ms, end_ms = fake.listed[0]
    assert start_ms == module.parse_date("2026-09-10", end_of_day=False)
    assert end_ms == module.parse_date("2026-09-10", end_of_day=True)


def test_rfp_not_found_reports_how_much_was_scanned(fake: FakeClient) -> None:
    with pytest.raises(ToolError, match=r"No instance with RFP No\. 202609-RFP-9.*3 instance\(s\) scanned"):
        TOOL.run(TOOL.input(target="202609-RFP-9"), CTX)


def test_rfp_search_needs_an_approval_code(fake: FakeClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LARK_APPROVAL_CODE")
    with pytest.raises(ToolError, match="No approval code"):
        TOOL.run(TOOL.input(target="202609-RFP-1919"), CTX)


def test_lark_failures_become_tool_errors(fake: FakeClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_code: str) -> dict[str, Any]:
        raise module.LarkError("GET instance failed: code=99991663 msg=invalid token")

    monkeypatch.setattr(fake, "get_instance", boom)
    with pytest.raises(ToolError, match="code=99991663"):
        TOOL.run(TOOL.input(target="B"), CTX)
    assert fake.closed is True


def test_missing_credentials(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(module, "ENV_FILE", tmp_path / "absent.env")
    monkeypatch.delenv("LARK_APP_ID", raising=False)
    monkeypatch.delenv("LARK_APP_SECRET", raising=False)
    with pytest.raises(ToolError, match="Missing LARK_APP_ID/LARK_APP_SECRET"):
        TOOL.run(TOOL.input(target="X"), CTX)


def test_render_shows_the_form(fake: FakeClient) -> None:
    assert TOOL.render is not None
    text = TOOL.render(TOOL.run(TOOL.input(target="202609-RFP-1919"), CTX))
    assert "RFP No.: 202609-rfp-1919  (matched after scanning 2)" in text
    assert '"RFP No.": "202609-rfp-1919"' in text


# --- the HTTP client against a mock transport -----------------------------------


def test_lark_client_token_pagination_and_errors() -> None:
    LarkClient, LarkError = module.LarkClient, module.LarkError

    calls: list[tuple[str, str, dict[str, str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, dict(request.url.params)))
        if request.url.path.endswith("/tenant_access_token/internal"):
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "tok", "expire": 7200})
        assert request.headers["Authorization"] == "Bearer tok"
        if request.url.path.endswith("/approval/v4/instances"):
            page = request.url.params.get("page_token", "")
            if page == "":
                return httpx.Response(
                    200, json={"code": 0, "data": {"instance_code_list": ["A", "B"], "page_token": "p2"}}
                )
            return httpx.Response(
                200, json={"code": 0, "data": {"instance_code_list": ["C"], "page_token": "p2"}}
            )
        if request.url.path.endswith("/instances/BAD"):
            return httpx.Response(200, json={"code": 1390001, "msg": "not found"})
        return httpx.Response(200, json={"code": 0, "data": {"instance_code": "OK", "form": "[]"}})

    client = LarkClient(
        "id", "secret", transport=httpx.MockTransport(handler), min_gap_s=0, sleep=lambda _: None
    )
    assert list(client.instance_codes("APPROVAL", 1, 2)) == ["A", "B", "C"]
    assert client.get_instance("OK")["instance_code"] == "OK"
    with pytest.raises(LarkError, match="code=1390001 msg=not found"):
        client.get_instance("BAD")
    assert sum(1 for method, path, _ in calls if path.endswith("/tenant_access_token/internal")) == 1
    client.close()
