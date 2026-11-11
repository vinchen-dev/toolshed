from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import pytest

from shed import ToolContext, ToolError
from shed.registry import load_tool, load_tool_module

module = load_tool_module("lark-doc")
approval = load_tool_module("lark-approval")
lark = importlib.import_module(f"{approval.__package__}.lark_client")
TOOL = load_tool("lark-doc")

RESPONSES: dict[str, dict[str, Any]] = {
    "/docx/v1/documents/DocTok12345/raw_content": {"data": {"content": "Hello\nWorld"}},
    "/docx/v1/documents/DocTok12345": {"data": {"document": {"title": "Spec", "revision_id": 3}}},
    "/doc/v2/OldTok12345/raw_content": {"data": {"content": "legacy text"}},
    "/doc/v2/meta/OldTok12345": {"data": {"title": "Old"}},
}


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.closed = False

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append(path)
        if path in RESPONSES:
            return RESPONSES[path]
        raise lark.LarkError(f"GET {path} failed: code=1770002 msg=not found")

    def wiki_node(self, token: str) -> dict[str, Any]:
        self.calls.append(f"wiki {token}")
        if token == "WikiDoc1234":
            return {"obj_type": "docx", "obj_token": "DocTok12345", "title": "Wiki title"}
        if token == "WikiSheet12":
            return {"obj_type": "sheet", "obj_token": "ShtTok12345", "title": "Numbers"}
        raise lark.LarkError("GET /wiki/v2/spaces/get_node failed: code=131006 msg=not found")

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


def test_parse_link_knows_the_shapes_lark_copies() -> None:
    assert lark.parse_link("https://x.larksuite.com/docx/DocTok12345?from=share") == ("docx", "DocTok12345")
    assert lark.parse_link("https://x.larksuite.com/docs/OldTok12345") == ("doc", "OldTok12345")
    assert lark.parse_link("https://x.larksuite.com/wiki/WikiDoc1234#h1") == ("wiki", "WikiDoc1234")
    assert lark.parse_link("https://x.larksuite.com/drive/folder/FldTok12345") == ("folder", "FldTok12345")
    assert lark.parse_link("https://x.larksuite.com/file/FilTok12345") == ("file", "FilTok12345")
    assert lark.parse_link("https://x.larksuite.com/sheets/ShtTok12345") == ("sheet", "ShtTok12345")
    assert lark.parse_link("https://x.larksuite.com/base/BasTok12345") == ("bitable", "BasTok12345")
    assert lark.parse_link(" DocTok12345 ") == ("token", "DocTok12345")
    with pytest.raises(lark.LarkError, match="unrecognised"):
        lark.parse_link("https://x.larksuite.com/drive/home")
    with pytest.raises(lark.LarkError, match="not a Lark link"):
        lark.parse_link("hello world")


def test_reads_a_docx_with_its_title(client: FakeClient, tmp_path: Path) -> None:
    doc = TOOL.run(TOOL.input(target="https://x.larksuite.com/docx/DocTok12345"), ctx(tmp_path))
    assert (doc.title, doc.kind, doc.token, doc.chars, doc.truncated) == (
        "Spec",
        "docx",
        "DocTok12345",
        11,
        False,
    )
    assert doc.content == "Hello\nWorld" and client.closed
    assert TOOL.render is not None
    assert TOOL.render(doc) == "# Spec  [docx DocTok12345, 11 chars]\n\nHello\nWorld\n"
    short = TOOL.run(TOOL.input(target="DocTok12345", max_chars=5), ctx(tmp_path))
    assert short.content == "Hello" and short.truncated and short.chars == 11


def test_wiki_links_resolve_to_the_doc_behind_them(client: FakeClient, tmp_path: Path) -> None:
    doc = TOOL.run(TOOL.input(target="https://x.larksuite.com/wiki/WikiDoc1234"), ctx(tmp_path))
    assert doc.kind == "docx" and doc.token == "DocTok12345" and doc.title == "Spec"
    with pytest.raises(ToolError, match="ShtTok12345 is a sheet, not a document"):
        TOOL.run(TOOL.input(target="https://x.larksuite.com/wiki/WikiSheet12"), ctx(tmp_path))


def test_bare_token_is_tried_as_docx_then_legacy_doc_then_wiki(client: FakeClient, tmp_path: Path) -> None:
    doc = TOOL.run(TOOL.input(target="OldTok12345"), ctx(tmp_path))
    assert doc.kind == "doc" and doc.title == "Old" and doc.content == "legacy text"
    assert client.calls[-1] == "/doc/v2/OldTok12345/raw_content" and "wiki OldTok12345" not in client.calls
    with pytest.raises(ToolError, match="cannot read NoSuchTok12"):
        TOOL.run(TOOL.input(target="NoSuchTok12"), ctx(tmp_path))
    assert client.calls[-1] == "wiki NoSuchTok12"


def test_bad_links_and_missing_credentials_are_tool_errors(
    client: FakeClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(ToolError, match="unrecognised Lark link"):
        TOOL.run(TOOL.input(target="https://x.larksuite.com/drive/home"), ctx(tmp_path))
    monkeypatch.delenv("LARK_APP_ID")
    with pytest.raises(ToolError, match="Missing LARK_APP_ID"):
        TOOL.run(TOOL.input(target="DocTok12345"), ctx(tmp_path))
