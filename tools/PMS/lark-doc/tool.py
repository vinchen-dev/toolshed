"""Read a Lark Doc as plain text.

Takes the link as copied from Lark (a docx page, a legacy doc, or a wiki page that holds one) or a bare
token. Wiki links are resolved to the document behind them. The text comes from Lark's raw_content
endpoint, so headings and tables flatten to lines; it is meant for reading, not for round-tripping.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from shed import Tool, ToolContext, ToolError
from shed.lib.env import require_env
from shed.registry import load_tool_module

DOC_KINDS = ("docx", "doc")


class Input(BaseModel):
    target: str = Field(
        description="Link copied from Lark (docx, legacy doc or wiki page), or the document token"
    )
    max_chars: int = Field(0, description="Keep only the first N characters of the text; 0 keeps everything")
    env_file: str | None = Field(
        None,
        description="Env file holding LARK_APP_ID and LARK_APP_SECRET, for example another project's .env "
        "(default ~/.config/lark.env)",
    )


class Doc(BaseModel):
    title: str
    kind: str
    """docx, or doc for the legacy editor."""
    token: str
    chars: int
    truncated: bool
    content: str


def _approval() -> Any:
    """The lark-approval tool module: its client factory and env file are reused here."""
    return load_tool_module("lark-approval")


def _client_module() -> Any:
    return importlib.import_module(f"{_approval().__package__}.lark_client")


def make_client(app_id: str, app_secret: str) -> Any:
    return _approval().make_client(app_id, app_secret)


def _content(client: Any, kind: str, token: str) -> str:
    path = f"/docx/v1/documents/{token}/raw_content" if kind == "docx" else f"/doc/v2/{token}/raw_content"
    return str((client.get(path).get("data") or {}).get("content") or "")


def _title(client: Any, kind: str, token: str, fallback: str) -> str:
    """The document's title; a failure here must not lose the text already fetched."""
    try:
        if kind == "docx":
            data = client.get(f"/docx/v1/documents/{token}").get("data") or {}
            return str((data.get("document") or {}).get("title") or fallback)
        return str((client.get(f"/doc/v2/meta/{token}").get("data") or {}).get("title") or fallback)
    except _client_module().LarkError:
        return fallback


def read_doc(client: Any, kind: str, token: str) -> tuple[str, str, str, str]:
    """(kind, token, title, content) for a link kind from parse_link; a bare token is tried as each kind."""
    LarkError = _client_module().LarkError
    title = ""
    if kind == "token":
        for attempt in DOC_KINDS:
            try:
                return attempt, token, _title(client, attempt, token, ""), _content(client, attempt, token)
            except LarkError:
                continue
        kind = "wiki"
    if kind == "wiki":
        try:
            node = client.wiki_node(token)
        except LarkError as exc:
            raise LarkError(f"{token} is not a docx, legacy doc or wiki page I can open ({exc})") from exc
        kind, token = str(node.get("obj_type") or ""), str(node.get("obj_token") or "")
        title = str(node.get("title") or "")
    if kind not in DOC_KINDS:
        hint = (
            "lark-files lists folders and downloads files" if kind in ("folder", "file") else "not supported"
        )
        raise ToolError(f"{token} is a {kind}, not a document; {hint}")
    content = _content(client, kind, token)
    return kind, token, _title(client, kind, token, title), content


def run(params: Input, ctx: ToolContext) -> Doc:
    env_file = ctx.cwd / Path(params.env_file).expanduser() if params.env_file else _approval().ENV_FILE
    creds = require_env("LARK_APP_ID", "LARK_APP_SECRET", env_file=env_file)
    lark = _client_module()
    try:
        kind, token = lark.parse_link(params.target)
    except lark.LarkError as exc:
        raise ToolError(str(exc)) from exc
    client = make_client(creds["LARK_APP_ID"], creds["LARK_APP_SECRET"])
    try:
        kind, token, title, content = read_doc(client, kind, token)
    except lark.LarkError as exc:
        raise ToolError(f"cannot read {params.target}: {exc}") from exc
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()
    chars = len(content)
    truncated = 0 < params.max_chars < chars
    ctx.log(f"# {kind} {token}: {chars} chars" + (f", keeping {params.max_chars}" if truncated else ""))
    return Doc(
        title=title,
        kind=kind,
        token=token,
        chars=chars,
        truncated=truncated,
        content=content[: params.max_chars] if truncated else content,
    )


def render(doc: Doc) -> str:
    head = f"# {doc.title or '(untitled)'}  [{doc.kind} {doc.token}, {doc.chars} chars"
    head += ", truncated]" if doc.truncated else "]"
    return f"{head}\n\n{doc.content}".rstrip() + "\n"


TOOL = Tool(
    name="lark-doc",
    description=(
        "Read a Lark Doc as plain text from a link copied out of Lark (docx, legacy doc, or a wiki page "
        "holding one) or a bare token. Use it to read a spec, note or handover kept in Lark; use "
        "lark-files for Drive folders and files."
    ),
    input=Input,
    run=run,
    render=render,
    examples=(
        "shed lark-doc --target https://example.larksuite.com/docx/Abc123DefGhi",
        "shed lark-doc --target https://example.larksuite.com/wiki/Wik123Tok --max-chars 4000",
        "shed lark-doc --target Abc123DefGhi --env-file ~/work/finance-ai/.env --json",
    ),
)
