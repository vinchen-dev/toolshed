"""Download the files attached to one Lark approval instance.

Attachment and image fields of the form carry either a download link or a file token; a token is fetched
through the drive media endpoint first and through the approval files endpoint when that refuses it.
"""

from __future__ import annotations

import importlib
import mimetypes
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from shed import Tool, ToolContext, ToolError
from shed.lib.env import require_env
from shed.registry import load_tool_module

ATTACHMENT_TYPES = ("attachment", "image")
Kind = Literal["url", "token"]
_UNSAFE = re.compile(r"[\\/\x00-\x1f]+")


class Input(BaseModel):
    target: str = Field(description="Instance code of the approval, as lark-approval prints it")
    out_dir: str = Field(
        "~/Downloads/lark-{code}", description="Folder to save into; {code} is replaced by the instance code"
    )
    field: str | None = Field(None, description="Only the attachments of this form field (its name or id)")
    list_only: bool = Field(False, description="List what is attached without downloading anything")
    env_file: str | None = Field(
        None,
        description="Env file holding LARK_APP_ID and LARK_APP_SECRET, for example another project's .env "
        "(default ~/.config/lark.env)",
    )


class Attachment(BaseModel):
    field: str
    name: str
    source: Kind
    path: str | None = None
    bytes: int | None = None
    content_type: str | None = None
    error: str | None = None


class Result(BaseModel):
    instance_code: str
    out_dir: str | None
    """None when listing only."""
    attachments: list[Attachment]


def _approval() -> Any:
    """The lark-approval tool module: its client factory, env file and form parser are reused here."""
    return load_tool_module("lark-approval")


def make_client(app_id: str, app_secret: str) -> Any:
    return _approval().make_client(app_id, app_secret)


def _lark_error() -> type[Exception]:
    approval = _approval()
    return importlib.import_module(f"{approval.__package__}.lark_client").LarkError


def references(fields: list[dict[str, Any]], only: str | None = None) -> list[tuple[str, Kind, str, str]]:
    """(field, kind, reference, name) per attached file; kind is url for a link, token for a file token."""
    out: list[tuple[str, Kind, str, str]] = []
    for field in fields:
        label = str(field.get("name") or field.get("id") or "")
        ftype = str(field.get("type") or "").lower()
        wanted = only in (label, field.get("id")) if only else any(t in ftype for t in ATTACHMENT_TYPES)
        if not wanted:
            continue
        value = field.get("value")
        items = value if isinstance(value, list) else [value] if value else []
        for item in items:
            if isinstance(item, dict):
                ref = item.get("url") or item.get("file_token") or item.get("token") or item.get("code")
                name = str(item.get("name") or item.get("file_name") or "")
            else:
                ref, name = item, ""
            if not isinstance(ref, str) or not ref:
                continue
            kind: Kind = "url" if ref.startswith(("http://", "https://")) else "token"
            out.append((label, kind, ref, name))
    return out


def _fetch(client: Any, code: str, kind: Kind, ref: str) -> tuple[bytes, str, str]:
    if kind == "url":
        return client.fetch(ref)
    try:
        return client.download(f"/drive/v1/medias/{ref}/download")
    except _lark_error():
        return client.download(f"/approval/v4/instances/{code}/files/{ref}")


def _file_name(given: str, header: str, content_type: str, field: str, index: int) -> str:
    """The form's own name wins; a name Lark made up gets the field in front; otherwise field and index."""
    label = _UNSAFE.sub("_", field).strip("._ ") or "attachment"
    if given:
        return _UNSAFE.sub("_", given).strip("._ ") or f"{label}-{index}"
    header = _UNSAFE.sub("_", header).strip("._ ")
    if header:
        return f"{label} - {header}"
    ext = mimetypes.guess_extension(content_type.split(";")[0].strip()) or ".bin"
    return f"{label}-{index}{ext}"


def run(params: Input, ctx: ToolContext) -> Result:
    env_file = ctx.cwd / Path(params.env_file).expanduser() if params.env_file else _approval().ENV_FILE
    creds = require_env("LARK_APP_ID", "LARK_APP_SECRET", env_file=env_file)
    client = make_client(creds["LARK_APP_ID"], creds["LARK_APP_SECRET"])
    try:
        instance = client.get_instance(params.target)
    except _lark_error() as exc:
        raise ToolError(f"cannot read instance {params.target}: {exc}") from exc
    code = str(instance.get("instance_code") or params.target)
    found = references(_approval().parse_form(instance), params.field)
    ctx.log(
        f"# {code}: {len(found)} attached file(s)" + (f" in field {params.field}" if params.field else "")
    )
    if params.list_only:
        rows = [Attachment(field=f, name=n or ref[:12] + "...", source=k) for f, k, ref, n in found]
        return Result(instance_code=code, out_dir=None, attachments=rows)

    out_dir = ctx.cwd / Path(params.out_dir.replace("{code}", code)).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    rows: list[Attachment] = []
    for index, (field, kind, ref, given) in enumerate(found, start=1):
        try:
            content, content_type, header_name = _fetch(client, code, kind, ref)
        except _lark_error() as exc:
            rows.append(Attachment(field=field, name=given or f"#{index}", source=kind, error=str(exc)))
            continue
        name = _file_name(given, header_name, content_type, field, index)
        target = out_dir / name
        target.write_bytes(content)
        rows.append(
            Attachment(
                field=field,
                name=name,
                source=kind,
                path=str(target),
                bytes=len(content),
                content_type=content_type or None,
            )
        )
        ctx.log(f"# saved {name} ({len(content)} bytes)")
    return Result(instance_code=code, out_dir=str(out_dir), attachments=rows)


def render(result: Result) -> str:
    if not result.attachments:
        return f"{result.instance_code}: no attachments on the form"
    lines = [f"{result.instance_code}: {len(result.attachments)} attachment(s)"]
    for a in result.attachments:
        if a.error:
            lines.append(f"  {a.field}: {a.name} FAILED {a.error}")
        elif a.path:
            lines.append(f"  {a.field}: {a.path} ({a.bytes} bytes, {a.content_type or 'unknown type'})")
        else:
            lines.append(f"  {a.field}: {a.name} [{a.source}]")
    return "\n".join(lines)


TOOL = Tool(
    name="lark-attachments",
    description=(
        "Download the files attached to a Lark approval instance (attachment and image form fields) into a "
        "folder, or list them. Use it after lark-approval has shown which instance holds the file."
    ),
    input=Input,
    run=run,
    render=render,
    examples=(
        "shed lark-attachments --target E4DD80E4-5C4F-486E-83C5-E01B1BEDB9FD",
        "shed lark-attachments --target 7A2B... --list-only",
        'shed lark-attachments --target 7A2B... --field "Supporting documents" --out-dir ./rfp-files',
    ),
)
