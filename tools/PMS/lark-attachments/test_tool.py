from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

import pytest

from shed import ToolContext, ToolError
from shed.registry import load_tool, load_tool_module

module = load_tool_module("lark-attachments")
approval = load_tool_module("lark-approval")
LarkError = importlib.import_module(f"{approval.__package__}.lark_client").LarkError
TOOL = load_tool("lark-attachments")

FORM = [
    {"id": "w1", "name": "Amount", "type": "amount", "value": "12.50"},
    {
        "id": "w2",
        "name": "Supporting documents",
        "type": "attachmentV2",
        "value": ["https://files.example/dl?code=abc", {"file_token": "tok_1", "name": "invoice.pdf"}],
    },
    {"id": "w3", "name": "Photo", "type": "image", "value": ["tok_2"]},
    {"id": "w4", "name": "Note", "type": "textarea", "value": "https://not-an-attachment.example"},
]


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_instance(self, instance_code: str) -> dict[str, Any]:
        if instance_code != "OK":
            raise LarkError("GET instance: code=1390010 msg=not found")
        return {"instance_code": "OK", "form": json.dumps(FORM)}

    def fetch(self, url: str) -> tuple[bytes, str, str]:
        self.calls.append(f"url {url}")
        return b"%PDF-link", "application/pdf", "receipt.pdf"

    def download(self, path: str) -> tuple[bytes, str, str]:
        self.calls.append(f"get {path}")
        if path.startswith("/drive/v1/medias/tok_2/"):
            raise LarkError("GET medias failed: code=1061002 msg=no permission")
        if "tok_2" in path:
            return b"\x89PNG", "image/png", ""
        return b"%PDF-token", "application/pdf", "inv copy.pdf"


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


def test_references_pick_attachment_and_image_fields_only() -> None:
    refs = module.references(FORM)
    assert [(f, k, n) for f, k, _, n in refs] == [
        ("Supporting documents", "url", ""),
        ("Supporting documents", "token", "invoice.pdf"),
        ("Photo", "token", ""),
    ]
    assert [r[2] for r in module.references(FORM, only="w3")] == ["tok_2"]
    assert module.references(FORM, only="Note") == [("Note", "url", "https://not-an-attachment.example", "")]


def test_downloads_every_file_with_a_sensible_name(client: FakeClient, tmp_path: Path) -> None:
    result = TOOL.run(TOOL.input(target="OK", out_dir="files"), ctx(tmp_path))
    names = [a.name for a in result.attachments]
    assert names == ["Supporting documents - receipt.pdf", "invoice.pdf", "Photo-3.png"]
    assert (tmp_path / "files" / "invoice.pdf").read_bytes() == b"%PDF-token"
    assert result.attachments[2].source == "token" and result.attachments[2].bytes == 4
    assert client.calls == [
        "url https://files.example/dl?code=abc",
        "get /drive/v1/medias/tok_1/download",
        "get /drive/v1/medias/tok_2/download",
        "get /approval/v4/instances/OK/files/tok_2",
    ]
    assert TOOL.render is not None
    assert "Supporting documents - receipt.pdf (9 bytes, application/pdf)" in TOOL.render(result)


def test_list_only_and_field_filter(client: FakeClient, tmp_path: Path) -> None:
    listed = TOOL.run(TOOL.input(target="OK", list_only=True), ctx(tmp_path))
    assert listed.out_dir is None and len(listed.attachments) == 3 and client.calls == []
    one = TOOL.run(TOOL.input(target="OK", field="Photo", out_dir="p"), ctx(tmp_path))
    assert [a.name for a in one.attachments] == ["Photo-1.png"]


def test_header_names_are_decoded_and_made_safe() -> None:
    lark_client = importlib.import_module(f"{approval.__package__}.lark_client")
    assert lark_client.filename_from("attachment; filename*=UTF-8''inv%20copy.pdf") == "inv copy.pdf"
    assert lark_client.filename_from('inline; filename="a.png"') == "a.png"
    assert module._file_name("../../x/evil.txt", "", "", "f", 1) == "x_evil.txt"
    assert module._file_name("", "a1b2.pdf", "application/pdf", "Invoice", 4) == "Invoice - a1b2.pdf"
    assert module._file_name("", "", "image/jpeg", "Photo", 2) == "Photo-2.jpg"


def test_env_file_flag_loads_credentials_from_another_project(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeClient()
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(module, "make_client", lambda app_id, secret: seen.append((app_id, secret)) or fake)
    monkeypatch.setattr(approval, "ENV_FILE", tmp_path / "absent.env")
    monkeypatch.delenv("LARK_APP_ID", raising=False)
    monkeypatch.delenv("LARK_APP_SECRET", raising=False)
    (tmp_path / "other.env").write_text("LARK_APP_ID=cli_other\nLARK_APP_SECRET=s3\nUNRELATED=1\n")
    with pytest.raises(ToolError, match="Missing LARK_APP_ID"):
        TOOL.run(TOOL.input(target="OK", list_only=True), ctx(tmp_path))
    result = TOOL.run(TOOL.input(target="OK", list_only=True, env_file="other.env"), ctx(tmp_path))
    assert seen == [("cli_other", "s3")] and len(result.attachments) == 3


def test_unknown_instance_is_a_tool_error(client: FakeClient, tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="cannot read instance NOPE"):
        TOOL.run(TOOL.input(target="NOPE"), ctx(tmp_path))


def test_per_file_failures_do_not_stop_the_rest(
    client: FakeClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(url: str) -> tuple[bytes, str, str]:
        raise LarkError("GET attachment link: HTTP 403")

    monkeypatch.setattr(client, "fetch", boom)
    result = TOOL.run(TOOL.input(target="OK", out_dir="files"), ctx(tmp_path))
    assert result.attachments[0].error == "GET attachment link: HTTP 403" and result.attachments[1].path
    assert TOOL.render is not None and "FAILED GET attachment link" in TOOL.render(result)
