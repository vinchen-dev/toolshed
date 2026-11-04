"""List a Lark Drive folder, or download its files (or one file) into a local folder.

Takes the link as copied from Lark (a Drive folder, a file, or a wiki page holding a file) or a bare
token. Only uploaded files download; docs, sheets and bases are listed with a note, since docs are
read with lark-doc and the rest have no file to fetch.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from shed import Tool, ToolContext, ToolError
from shed.lib.env import require_env
from shed.lib.manila import format_manila
from shed.registry import load_tool_module

PAGE_SIZE = 200
NOTES = {"docx": "read it with lark-doc", "doc": "read it with lark-doc"}
_UNSAFE = re.compile(r"[\\/\x00-\x1f]+")


class Input(BaseModel):
    target: str = Field(
        description="Link copied from Lark (Drive folder, file, or wiki page holding a file), or a token"
    )
    download: bool = Field(
        False, description="Save the folder's files; implied when the target is a single file"
    )
    out_dir: str = Field(
        "~/Downloads/lark-files-{token}",
        description="Folder to save into; {token} is replaced by the target token",
    )
    match: str | None = Field(
        None, description="Only entries whose name contains this text (case-insensitive)"
    )
    limit: int = Field(PAGE_SIZE, description="At most this many entries, most recently edited first")
    env_file: str | None = Field(
        None,
        description="Env file holding LARK_APP_ID and LARK_APP_SECRET, for example another project's .env "
        "(default ~/.config/lark.env)",
    )


class Entry(BaseModel):
    name: str
    type: str
    token: str
    url: str | None = None
    modified: str | None = None
    """Manila time of the last edit, when Lark reports it."""
    path: str | None = None
    bytes: int | None = None
    note: str | None = None
    """Why nothing was downloaded: a doc to read with lark-doc, a type with no file, or the error."""


class Result(BaseModel):
    kind: str
    """folder or file."""
    token: str
    out_dir: str | None
    entries: list[Entry]


def _approval() -> Any:
    """The lark-approval tool module: its client factory and env file are reused here."""
    return load_tool_module("lark-approval")


def _client_module() -> Any:
    return importlib.import_module(f"{_approval().__package__}.lark_client")


def make_client(app_id: str, app_secret: str) -> Any:
    return _approval().make_client(app_id, app_secret)


def list_folder(client: Any, token: str, limit: int) -> list[dict[str, Any]]:
    """The folder's entries, newest edit first, across pages until limit is reached."""
    rows: list[dict[str, Any]] = []
    page_token = ""
    while len(rows) < limit:
        params: dict[str, Any] = {
            "folder_token": token,
            "page_size": min(PAGE_SIZE, limit - len(rows)),
            "order_by": "EditedTime",
            "direction": "DESC",
        }
        if page_token:
            params["page_token"] = page_token
        data = client.get("/drive/v1/files", params).get("data") or {}
        rows.extend(item for item in data.get("files") or [] if isinstance(item, dict))
        next_token = str(data.get("next_page_token") or "")
        if not data.get("has_more") or not next_token or next_token == page_token:
            break
        page_token = next_token
    return rows[:limit]


def _entry(item: dict[str, Any]) -> Entry:
    edited = str(item.get("modified_time") or "")
    return Entry(
        name=str(item.get("name") or item.get("token") or ""),
        type=str(item.get("type") or "unknown"),
        token=str(item.get("token") or ""),
        url=str(item["url"]) if item.get("url") else None,
        modified=format_manila(int(edited)) if edited.isdigit() else None,
    )


def _save(client: Any, token: str, name: str, out_dir: Path) -> tuple[Path, int]:
    content, _content_type, header_name = client.download(f"/drive/v1/files/{token}/download")
    safe = _UNSAFE.sub("_", name or header_name).strip("._ ") or token
    target = out_dir / safe
    target.write_bytes(content)
    return target, len(content)


def _resolve(client: Any, kind: str, token: str, limit: int) -> tuple[str, str, list[dict[str, Any]] | None]:
    """(kind, token, folder rows or None for one file); wiki links and bare tokens are worked out here."""
    LarkError = _client_module().LarkError
    if kind == "wiki":
        node = client.wiki_node(token)
        kind, token = str(node.get("obj_type") or ""), str(node.get("obj_token") or "")
    if kind == "token":
        try:
            return "folder", token, list_folder(client, token, limit)
        except LarkError:
            return "file", token, None
    if kind == "folder":
        return kind, token, list_folder(client, token, limit)
    if kind == "file":
        return kind, token, None
    hint = "read it with lark-doc" if kind in NOTES else "nothing to list or download there"
    raise ToolError(f"{token} is a {kind}, not a folder or file; {hint}")


def run(params: Input, ctx: ToolContext) -> Result:
    env_file = ctx.cwd / Path(params.env_file).expanduser() if params.env_file else _approval().ENV_FILE
    creds = require_env("LARK_APP_ID", "LARK_APP_SECRET", env_file=env_file)
    lark = _client_module()
    try:
        kind, token = lark.parse_link(params.target)
    except lark.LarkError as exc:
        raise ToolError(str(exc)) from exc
    client = make_client(creds["LARK_APP_ID"], creds["LARK_APP_SECRET"])
    try:
        return _run(params, ctx, client, lark.LarkError, kind, token)
    except lark.LarkError as exc:
        raise ToolError(f"cannot read {params.target}: {exc}") from exc
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()


def _run(
    params: Input, ctx: ToolContext, client: Any, LarkError: type[Exception], kind: str, token: str
) -> Result:
    kind, token, rows = _resolve(client, kind, token, params.limit)
    out_dir = ctx.cwd / Path(params.out_dir.replace("{token}", token)).expanduser()
    if rows is None:  # one file: downloading is the whole point
        out_dir.mkdir(parents=True, exist_ok=True)
        path, size = _save(client, token, "", out_dir)
        ctx.log(f"# saved {path.name} ({size} bytes)")
        entry = Entry(name=path.name, type="file", token=token, path=str(path), bytes=size)
        return Result(kind="file", token=token, out_dir=str(out_dir), entries=[entry])
    entries = [_entry(item) for item in rows]
    if params.match:
        needle = params.match.lower()
        entries = [e for e in entries if needle in e.name.lower()]
    ctx.log(f"# folder {token}: {len(entries)} entr{'y' if len(entries) == 1 else 'ies'}")
    if not params.download:
        return Result(kind="folder", token=token, out_dir=None, entries=entries)
    out_dir.mkdir(parents=True, exist_ok=True)
    for entry in entries:
        if entry.type != "file":
            entry.note = NOTES.get(entry.type, f"a {entry.type} has no file to download")
            continue
        try:
            path, size = _save(client, entry.token, entry.name, out_dir)
        except LarkError as exc:
            entry.note = str(exc)
            continue
        entry.path, entry.bytes = str(path), size
        ctx.log(f"# saved {path.name} ({size} bytes)")
    return Result(kind="folder", token=token, out_dir=str(out_dir), entries=entries)


def render(result: Result) -> str:
    if not result.entries:
        return f"{result.kind} {result.token}: empty"
    count = len(result.entries)
    lines = [f"{result.kind} {result.token}: {count} entr{'y' if count == 1 else 'ies'}"]
    for e in result.entries:
        when = f"  {e.modified}" if e.modified else ""
        if e.path:
            lines.append(f"  {e.name}  [{e.type}] -> {e.path} ({e.bytes} bytes)")
        elif e.note:
            lines.append(f"  {e.name}  [{e.type}]{when}  ({e.note})")
        else:
            lines.append(f"  {e.name}  [{e.type}]{when}  {e.url or e.token}")
    return "\n".join(lines)


TOOL = Tool(
    name="lark-files",
    description=(
        "List a Lark Drive folder (name, type, last edit and link per entry) or download its uploaded "
        "files, or download one file, from a link copied out of Lark or a bare token. Use it to pull "
        "attachments and exports shared in Drive; docs are read with lark-doc, approval attachments "
        "with lark-attachments."
    ),
    input=Input,
    run=run,
    render=render,
    examples=(
        "shed lark-files --target https://example.larksuite.com/drive/folder/Fld123Tok",
        "shed lark-files --target https://x.larksuite.com/drive/folder/Fld123Tok --download --match xlsx",
        "shed lark-files --target https://example.larksuite.com/file/Fil123Tok --out-dir ./inbox",
    ),
)
