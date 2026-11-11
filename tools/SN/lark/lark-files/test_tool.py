from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import pytest

from shed import ToolContext, ToolError
from shed.registry import load_tool, load_tool_module

module = load_tool_module("lark-files")
approval = load_tool_module("lark-approval")
lark = importlib.import_module(f"{approval.__package__}.lark_client")
TOOL = load_tool("lark-files")

FOLDER = "https://x.larksuite.com/drive/folder/FldTok12345"
PAGE1: dict[str, Any] = {
    "data": {
        "files": [
            {
                "token": "FilA1234567",
                "name": "report.xlsx",
                "type": "file",
                "url": "https://x/file/FilA1234567",
                "modified_time": "1761620400",
            },
            {"token": "DocB1234567", "name": "Notes", "type": "docx", "url": "https://x/docx/DocB1234567"},
        ],
        "has_more": True,
        "next_page_token": "p2",
    }
}
PAGE2: dict[str, Any] = {
    "data": {
        "files": [
            {"token": "FilC1234567", "name": "photo.png", "type": "file", "modified_time": "1761447600"},
            {"token": "ShtD1234567", "name": "Budget", "type": "sheet"},
        ],
        "has_more": False,
    }
}


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = dict(params or {})
        self.calls.append((path, params))
        if path == "/drive/v1/files" and params.get("folder_token") == "FldTok12345":
            return PAGE2 if params.get("page_token") == "p2" else PAGE1
        raise lark.LarkError(f"GET {path} failed: code=1061002 msg=no permission")

    def wiki_node(self, token: str) -> dict[str, Any]:
        self.calls.append((f"wiki {token}", {}))
        return {"obj_type": "file", "obj_token": "FilA1234567", "title": "report"}

    def download(self, path: str) -> tuple[bytes, str, str]:
        self.calls.append((path, {}))
        if "FilC1234567" in path:
            raise lark.LarkError("GET file failed: code=1061045 msg=forbidden")
        return b"PK\x03\x04", "application/vnd.openxmlformats", "report (1).xlsx"

    def close(self) -> None:
        pass


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


def test_lists_a_folder_across_pages_newest_first(client: FakeClient, tmp_path: Path) -> None:
    result = TOOL.run(TOOL.input(target=FOLDER), ctx(tmp_path))
    assert result.kind == "folder" and result.out_dir is None
    assert [(e.name, e.type) for e in result.entries] == [
        ("report.xlsx", "file"),
        ("Notes", "docx"),
        ("photo.png", "file"),
        ("Budget", "sheet"),
    ]
    assert result.entries[0].modified is not None and result.entries[0].modified.startswith("2025-10-28")
    assert result.entries[3].modified is None
    assert client.calls[0][1]["order_by"] == "EditedTime" and client.calls[1][1]["page_token"] == "p2"
    assert TOOL.render is not None
    text = TOOL.render(result)
    assert text.startswith("folder FldTok12345: 4 entries\n  report.xlsx  [file]  2025-10-28")
    assert "https://x/file/FilA1234567" in text and "Budget  [sheet]  ShtD1234567" in text

    few = TOOL.run(TOOL.input(target=FOLDER, limit=3, match="XLSX"), ctx(tmp_path))
    assert [e.name for e in few.entries] == ["report.xlsx"]


def test_download_saves_files_and_explains_the_rest(client: FakeClient, tmp_path: Path) -> None:
    result = TOOL.run(TOOL.input(target=FOLDER, download=True, out_dir="dl"), ctx(tmp_path))
    assert result.out_dir == str(tmp_path / "dl")
    by_name = {e.name: e for e in result.entries}
    assert (
        by_name["report.xlsx"].path == str(tmp_path / "dl" / "report.xlsx")
        and by_name["report.xlsx"].bytes == 4
    )
    assert (tmp_path / "dl" / "report.xlsx").read_bytes() == b"PK\x03\x04"
    assert by_name["Notes"].note == "read it with lark-doc" and by_name["Notes"].path is None
    assert by_name["photo.png"].note is not None and "forbidden" in by_name["photo.png"].note
    assert by_name["Budget"].note == "a sheet has no file to download"
    assert TOOL.render is not None and "report.xlsx  [file] -> " in TOOL.render(result)


def test_single_file_links_download_straight_away(client: FakeClient, tmp_path: Path) -> None:
    result = TOOL.run(
        TOOL.input(target="https://x.larksuite.com/file/FilA1234567", out_dir="one"), ctx(tmp_path)
    )
    assert result.kind == "file" and [e.name for e in result.entries] == ["report (1).xlsx"]
    assert (tmp_path / "one" / "report (1).xlsx").is_file()
    via_wiki = TOOL.run(
        TOOL.input(target="https://x.larksuite.com/wiki/WikiFile123", out_dir="w"), ctx(tmp_path)
    )
    assert via_wiki.kind == "file" and via_wiki.token == "FilA1234567"


def test_bare_token_is_a_folder_if_it_lists_else_a_file(client: FakeClient, tmp_path: Path) -> None:
    assert TOOL.run(TOOL.input(target="FldTok12345"), ctx(tmp_path)).kind == "folder"
    one = TOOL.run(TOOL.input(target="FilA1234567", out_dir="f"), ctx(tmp_path))
    assert one.kind == "file" and one.entries[0].path is not None


def test_docs_and_bad_links_are_tool_errors(client: FakeClient, tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="DocB1234567 is a docx, not a folder or file; read it with lark-doc"):
        TOOL.run(TOOL.input(target="https://x.larksuite.com/docx/DocB1234567"), ctx(tmp_path))
    with pytest.raises(ToolError, match="not a Lark link"):
        TOOL.run(TOOL.input(target="??"), ctx(tmp_path))
