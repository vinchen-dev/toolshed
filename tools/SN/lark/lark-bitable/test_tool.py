from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from shed import ToolContext, ToolError
from shed.registry import load_tool, load_tool_module

module = load_tool_module("lark-bitable")
approval = load_tool_module("lark-approval")
lark = importlib.import_module(f"{approval.__package__}.lark_client")
TOOL = load_tool("lark-bitable")

APP = "BasTok12345"
RECORDS = f"/bitable/v1/apps/{APP}/tables/tblAbc/records"
PAGE_ONE = {
    "data": {
        "items": [
            {"record_id": "rec1", "fields": {"Name": "Alpha", "Owner": [{"name": "Vince", "id": "ou_1"}]}},
            {"record_id": "rec2", "fields": {"Name": "Beta", "Link": {"link": "https://x", "text": "x"}}},
        ],
        "has_more": True,
        "page_token": "p2",
    }
}
PAGE_TWO = {"data": {"items": [{"record_id": "rec3", "fields": {"Name": "Gamma"}}], "has_more": False}}


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.closed = False

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append(("GET", path, params))
        if path == RECORDS:
            return PAGE_TWO if (params or {}).get("page_token") == "p2" else PAGE_ONE
        if path == f"/bitable/v1/apps/{APP}/tables":
            return {"data": {"items": [{"table_id": "tblAbc", "name": "Tasks"}], "has_more": False}}
        raise lark.LarkError(f"GET {path} failed: code=1254005 msg=not found")

    def post(self, path: str, json: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append(("POST", path, json))
        return {"data": {"record": {"record_id": "recNew", "fields": (json or {}).get("fields")}}}

    def put(self, path: str, json: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append(("PUT", path, json))
        return {
            "data": {"record": {"record_id": path.rsplit("/", 1)[-1], "fields": (json or {}).get("fields")}}
        }

    def wiki_node(self, token: str) -> dict[str, Any]:
        self.calls.append(("WIKI", token, None))
        if token == "WikiBase123":
            return {"obj_type": "bitable", "obj_token": APP, "title": "Tasks base"}
        return {"obj_type": "docx", "obj_token": "DocTok12345", "title": "A doc"}

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeClient:
    fake = FakeClient()
    monkeypatch.setattr(module, "make_client", lambda *_: fake)
    monkeypatch.setattr(approval, "ENV_FILE", tmp_path / "absent.env")
    monkeypatch.setenv("LARK_APP_ID", "id")
    monkeypatch.setenv("LARK_APP_SECRET", "secret")
    return fake


def ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(cwd=tmp_path, log=lambda _: None)


def test_parse_target_reads_table_and_view_from_the_link() -> None:
    link = f"https://x.larksuite.com/base/{APP}?table=tblAbc&view=vewXyz"
    assert module.parse_target(link) == ("bitable", APP, "tblAbc", "vewXyz")
    assert module.parse_target(f"https://x.larksuite.com/base/{APP}") == ("bitable", APP, None, None)
    assert module.parse_target(APP) == ("token", APP, None, None)
    assert module.parse_target("https://x.larksuite.com/wiki/WikiBase123") == (
        "wiki",
        "WikiBase123",
        None,
        None,
    )


def test_reads_records_across_pages_and_flattens_cells(client: FakeClient, tmp_path: Path) -> None:
    link = f"https://x.larksuite.com/base/{APP}?table=tblAbc&view=vewXyz"
    result = TOOL.run(TOOL.input(target=link), ctx(tmp_path))
    assert (result.mode, result.app_token, result.table_id, result.count, result.has_more) == (
        "records",
        APP,
        "tblAbc",
        3,
        False,
    )
    assert [r.record_id for r in result.records] == ["rec1", "rec2", "rec3"]
    first = client.calls[0]
    assert first[0:2] == ("GET", RECORDS) and first[2] == {"page_size": 100, "view_id": "vewXyz"}
    assert client.calls[1][2]["page_token"] == "p2"
    assert client.closed
    text = module.render(result)
    assert text.startswith("# records BasTok12345/tblAbc: 3 records\n")
    assert "rec1: Name=Alpha, Owner=Vince" in text and "rec2: Name=Beta, Link=x" in text


def test_limit_stops_paging_and_reports_more(client: FakeClient, tmp_path: Path) -> None:
    params = TOOL.input(
        target=APP, table="tblAbc", limit=2, filter='CurrentValue.[Status]="Done"', fields=["Name"]
    )
    result = TOOL.run(params, ctx(tmp_path))
    assert (result.count, result.has_more) == (2, True)
    assert len([c for c in client.calls if c[1] == RECORDS]) == 1
    query = client.calls[0][2]
    assert query == {"page_size": 2, "filter": 'CurrentValue.[Status]="Done"', "field_names": '["Name"]'}


def test_lists_tables_and_resolves_a_wiki_link(client: FakeClient, tmp_path: Path) -> None:
    result = TOOL.run(
        TOOL.input(target="https://x.larksuite.com/wiki/WikiBase123", list_tables=True), ctx(tmp_path)
    )
    assert result.mode == "tables" and [t.name for t in result.tables] == ["Tasks"]
    assert client.calls[0] == ("WIKI", "WikiBase123", None)
    assert module.render(result) == f"# {APP}: 1 tables\ntblAbc  Tasks\n"


def test_create_and_update_are_explicit_writes(client: FakeClient, tmp_path: Path) -> None:
    created = TOOL.run(TOOL.input(target=APP, table="tblAbc", create='{"Name": "New"}'), ctx(tmp_path))
    assert created.mode == "created" and created.records[0].record_id == "recNew"
    assert client.calls[-1] == ("POST", RECORDS, {"fields": {"Name": "New"}})
    updated = TOOL.run(
        TOOL.input(target=APP, table="tblAbc", update="rec1", set='{"Name": "Renamed"}'), ctx(tmp_path)
    )
    assert updated.mode == "updated" and updated.records[0].fields == {"Name": "Renamed"}
    assert client.calls[-1] == ("PUT", f"{RECORDS}/rec1", {"fields": {"Name": "Renamed"}})
    assert module.render(updated) == f"# updated {APP}/tblAbc: 1 record\nrec1: Name=Renamed\n"


def test_input_rejects_bad_write_combinations() -> None:
    with pytest.raises(ValidationError, match="not valid JSON"):
        TOOL.input(target=APP, table="t", create="{oops")
    with pytest.raises(ValidationError, match="JSON object"):
        TOOL.input(target=APP, table="t", create="[1]")
    with pytest.raises(ValidationError, match="needs --set"):
        TOOL.input(target=APP, table="t", update="rec1")
    with pytest.raises(ValidationError, match="only applies"):
        TOOL.input(target=APP, table="t", set="{}")
    with pytest.raises(ValidationError, match="pass one of them"):
        TOOL.input(target=APP, table="t", create="{}", update="rec1", set="{}")
    with pytest.raises(ValidationError, match="cannot be combined"):
        TOOL.input(target=APP, list_tables=True, create="{}")


def test_refuses_other_kinds_and_a_missing_table(client: FakeClient, tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="is a docx, not a Base; lark-doc"):
        TOOL.run(TOOL.input(target="https://x.larksuite.com/docx/DocTok12345", table="t"), ctx(tmp_path))
    with pytest.raises(ToolError, match="holds a docx, not a Base"):
        TOOL.run(TOOL.input(target="https://x.larksuite.com/wiki/WikiDoc1234", table="t"), ctx(tmp_path))
    with pytest.raises(ToolError, match="no table"):
        TOOL.run(TOOL.input(target=APP), ctx(tmp_path))
    with pytest.raises(ToolError, match=r"cannot use Base .*code=1254005"):
        TOOL.run(TOOL.input(target=APP, table="tblNope"), ctx(tmp_path))
    assert client.closed


def test_flatten_handles_lark_cell_shapes() -> None:
    assert module.flatten([{"name": "A"}, {"name": "B"}]) == "A / B"
    assert module.flatten({"text": "t", "link": "l"}) == "t"
    assert module.flatten({"k": 1}) == '{"k": 1}'
    assert module.flatten(None) == "" and module.flatten(3) == "3"


def test_real_client_posts_and_puts_with_the_tenant_token() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/tenant_access_token/internal"):
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "tok", "expire": 7200})
        if request.method == "PUT":
            return httpx.Response(200, json={"code": 99991672, "msg": "permission denied"})
        return httpx.Response(200, json={"code": 0, "data": {"record": {"record_id": "r1"}}})

    client = lark.LarkClient("id", "secret", transport=httpx.MockTransport(handler), min_gap_s=0)
    body = client.post("/bitable/v1/apps/a/tables/t/records", {"fields": {"Name": "x"}})
    assert body["data"]["record"]["record_id"] == "r1"
    assert seen[1].method == "POST" and seen[1].headers["authorization"] == "Bearer tok"
    assert json.loads(seen[1].content) == {"fields": {"Name": "x"}}
    with pytest.raises(lark.LarkError, match=r"PUT .* code=99991672"):
        client.put("/bitable/v1/apps/a/tables/t/records/r1", {"fields": {}})
    assert len(seen) == 3  # the token was fetched once
    client.close()
