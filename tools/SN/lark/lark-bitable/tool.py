"""Read records from a Lark Base (Bitable) table, list its tables, or add or update one record.

Takes the Base link as copied from Lark (https://x.larksuite.com/base/<app_token>?table=tblXXX&view=vewXXX),
a wiki link that holds a Base, or the bare app token. Reading is the default; --create and --update are the
only writes and have to be asked for explicitly. Credentials come from ~/.config/lark.env or --env-file.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qs, urlsplit

from pydantic import BaseModel, Field, field_validator, model_validator

from shed import Tool, ToolContext, ToolError
from shed.lib.env import require_env
from shed.registry import load_tool_module

PAGE_SIZE = 100
MAX_LIMIT = 500
Mode = Literal["records", "tables", "created", "updated"]
_OTHER_TOOL = {
    "docx": "lark-doc reads documents",
    "doc": "lark-doc reads documents",
    "folder": "lark-files lists folders",
    "file": "lark-files downloads files",
    "sheet": "sheets are not supported",
}


def _json_object(value: str | None, flag: str) -> dict[str, Any] | None:
    if value is None:
        return None
    try:
        parsed = json.loads(value)
    except ValueError as exc:
        raise ValueError(f"{flag} is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{flag} must be a JSON object of field values")
    return parsed


class Input(BaseModel):
    target: str = Field(
        description="Base link copied from Lark (keeps its ?table= and ?view=), a wiki link holding a Base, "
        "or the bare app token"
    )
    table: str | None = Field(
        None, description="Table id (tblXXX); overrides the link's; required unless --list-tables"
    )
    view: str | None = Field(None, description="View id (vewXXX); overrides the link's")
    filter: str | None = Field(None, description='Lark filter formula, e.g. CurrentValue.[Status]="Done"')
    fields: list[str] = Field(default_factory=list, description="Field names to return (default: all)")
    limit: int = Field(
        100, ge=1, le=MAX_LIMIT, description="Maximum records to return (fetched in pages of 100)"
    )
    list_tables: bool = Field(False, description="List the Base's tables and exit")
    create: str | None = Field(
        None, description="JSON object of field values to add as one record; this is a write"
    )
    update: str | None = Field(None, description="Record id to update with --set; this is a write")
    set: str | None = Field(None, description="JSON object of field values for --update")
    env_file: str | None = Field(
        None,
        description="Env file holding LARK_APP_ID and LARK_APP_SECRET, for example another project's .env "
        "(default ~/.config/lark.env)",
    )

    @field_validator("create", "set")
    @classmethod
    def _check_json(cls, value: str | None, info: Any) -> str | None:
        _json_object(value, f"--{info.field_name}")
        return value

    @model_validator(mode="after")
    def _check_mode(self) -> Input:
        writes = [flag for flag, value in (("--create", self.create), ("--update", self.update)) if value]
        if len(writes) > 1:
            raise ValueError("--create and --update are separate writes; pass one of them")
        if writes and self.list_tables:
            raise ValueError(f"--list-tables cannot be combined with {writes[0]}")
        if self.update and self.set is None:
            raise ValueError("--update needs --set with the field values")
        if self.set is not None and not self.update:
            raise ValueError("--set only applies with --update")
        return self


class Record(BaseModel):
    record_id: str
    fields: dict[str, Any]


class TableInfo(BaseModel):
    table_id: str
    name: str


class Result(BaseModel):
    app_token: str
    table_id: str | None
    mode: Mode
    count: int
    has_more: bool
    records: list[Record] = Field(default_factory=list)
    tables: list[TableInfo] = Field(default_factory=list)


def _approval() -> Any:
    """The lark-approval tool module: its client factory and env file are reused here."""
    return load_tool_module("lark-approval")


def _client_module() -> Any:
    return importlib.import_module(f"{_approval().__package__}.lark_client")


def make_client(app_id: str, app_secret: str) -> Any:
    return _approval().make_client(app_id, app_secret)


def parse_target(target: str) -> tuple[str, str, str | None, str | None]:
    """(kind, token, table, view): the link's kind and token plus its ?table= and ?view= when present."""
    kind, token = _client_module().parse_link(target)
    query = parse_qs(urlsplit(target.strip()).query) if kind != "token" else {}
    table = (query.get("table") or [None])[0]
    view = (query.get("view") or [None])[0]
    return kind, token, table or None, view or None


def resolve_app(client: Any, kind: str, token: str) -> str:
    """The Base's app token behind a link kind from parse_link; anything but a Base is refused."""
    if kind == "wiki":
        node = client.wiki_node(token)
        obj_type = str(node.get("obj_type") or "")
        if obj_type != "bitable":
            hint = _OTHER_TOOL.get(obj_type, "not supported")
            raise ToolError(f"wiki page {token} holds a {obj_type or 'unknown object'}, not a Base; {hint}")
        return str(node.get("obj_token") or "")
    if kind in ("bitable", "token"):
        return token
    raise ToolError(f"{token} is a {kind}, not a Base; {_OTHER_TOOL.get(kind, 'not supported')}")


def _record(item: dict[str, Any]) -> Record:
    return Record(record_id=str(item.get("record_id") or ""), fields=dict(item.get("fields") or {}))


def list_tables(client: Any, app: str) -> list[TableInfo]:
    tables: list[TableInfo] = []
    page_token = ""
    while True:
        params: dict[str, Any] = {"page_size": PAGE_SIZE}
        if page_token:
            params["page_token"] = page_token
        data = client.get(f"/bitable/v1/apps/{app}/tables", params).get("data") or {}
        for item in data.get("items") or []:
            tables.append(
                TableInfo(table_id=str(item.get("table_id") or ""), name=str(item.get("name") or ""))
            )
        next_token = str(data.get("page_token") or "")
        if not data.get("has_more") or not next_token or next_token == page_token:
            return tables
        page_token = next_token


def list_records(client: Any, app: str, table: str, params: Input) -> tuple[list[Record], bool]:
    """Up to ``params.limit`` records, page by page; has_more says whether Lark had more to give."""
    records: list[Record] = []
    page_token = ""
    has_more = False
    while len(records) < params.limit:
        query: dict[str, Any] = {"page_size": min(PAGE_SIZE, params.limit - len(records))}
        if params.view:
            query["view_id"] = params.view
        if params.filter:
            query["filter"] = params.filter
        if params.fields:
            query["field_names"] = json.dumps(params.fields, ensure_ascii=False)
        if page_token:
            query["page_token"] = page_token
        data = client.get(f"/bitable/v1/apps/{app}/tables/{table}/records", query).get("data") or {}
        records.extend(_record(item) for item in data.get("items") or [])
        has_more = bool(data.get("has_more"))
        next_token = str(data.get("page_token") or "")
        if not has_more or not next_token or next_token == page_token:  # a repeated token would loop forever
            break
        page_token = next_token
    return records[: params.limit], has_more or len(records) > params.limit


def _written(body: dict[str, Any], what: str) -> Record:
    record = (body.get("data") or {}).get("record")
    if not isinstance(record, dict):
        raise ToolError(f"{what}: Lark returned no record")
    return _record(record)


def run(params: Input, ctx: ToolContext) -> Result:
    env_file = ctx.cwd / Path(params.env_file).expanduser() if params.env_file else _approval().ENV_FILE
    creds = require_env("LARK_APP_ID", "LARK_APP_SECRET", env_file=env_file)
    lark = _client_module()
    try:
        kind, token, link_table, link_view = parse_target(params.target)
    except lark.LarkError as exc:
        raise ToolError(str(exc)) from exc
    params = params.model_copy(update={"view": params.view or link_view})
    table = params.table or link_table
    if not table and not params.list_tables:
        raise ToolError("no table: pass --table tblXXX or a link that carries ?table=, or use --list-tables")
    client = make_client(creds["LARK_APP_ID"], creds["LARK_APP_SECRET"])
    try:
        app = resolve_app(client, kind, token)
        if params.list_tables:
            tables = list_tables(client, app)
            ctx.log(f"# {app}: {len(tables)} tables")
            return Result(
                app_token=app, table_id=None, mode="tables", count=len(tables), has_more=False, tables=tables
            )
        assert table is not None
        path = f"/bitable/v1/apps/{app}/tables/{table}/records"
        if params.create is not None:
            record = _written(
                client.post(path, {"fields": _json_object(params.create, "--create")}), "create"
            )
            ctx.log(f"# created {record.record_id} in {app}/{table}")
            return Result(
                app_token=app, table_id=table, mode="created", count=1, has_more=False, records=[record]
            )
        if params.update is not None:
            body = {"fields": _json_object(params.set, "--set")}
            record = _written(client.put(f"{path}/{params.update}", body), f"update {params.update}")
            ctx.log(f"# updated {record.record_id} in {app}/{table}")
            return Result(
                app_token=app, table_id=table, mode="updated", count=1, has_more=False, records=[record]
            )
        records, has_more = list_records(client, app, table, params)
        ctx.log(f"# {app}/{table}: {len(records)} records" + (", more available" if has_more else ""))
        return Result(
            app_token=app,
            table_id=table,
            mode="records",
            count=len(records),
            has_more=has_more,
            records=records,
        )
    except lark.LarkError as exc:
        raise ToolError(f"cannot use Base {params.target}: {exc}") from exc
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()


def flatten(value: Any) -> str:
    """One line of text for a cell: Lark wraps people, links and rich text in dicts and lists."""
    if value is None:
        return ""
    if isinstance(value, dict):
        for key in ("text", "name", "link", "value"):
            if value.get(key) not in (None, ""):
                return flatten(value[key])
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return " / ".join(flatten(item) for item in value)
    return str(value)


def render(result: Result) -> str:
    where = result.app_token + (f"/{result.table_id}" if result.table_id else "")
    if result.mode == "tables":
        lines = [f"# {where}: {result.count} tables"]
        lines.extend(f"{table.table_id}  {table.name}" for table in result.tables)
        return "\n".join(lines) + "\n"
    noun = "record" if result.count == 1 else "records"
    head = f"# {result.mode} {where}: {result.count} {noun}" + (", more available" if result.has_more else "")
    lines = [head]
    for record in result.records:
        cells = ", ".join(f"{name}={flatten(value)}" for name, value in record.fields.items())
        lines.append(f"{record.record_id}: {cells}")
    return "\n".join(lines) + "\n"


TOOL = Tool(
    name="lark-bitable",
    description=(
        "Read records from a Lark Base (Bitable) table by link or app token, with a view, a filter formula "
        "and a field list, or list the Base's tables. Use it to look up rows kept in a Base; --create and "
        "--update are the only writes and must be asked for explicitly."
    ),
    input=Input,
    run=run,
    render=render,
    examples=(
        "shed lark-bitable --target 'https://example.larksuite.com/base/BasTok12345?table=tblAbc&view=vewXyz'",
        "shed lark-bitable --target BasTok12345 --list-tables",
        "shed lark-bitable --target BasTok12345 --table tblAbc --filter 'CurrentValue.[Done]=1' --json",
        'shed lark-bitable --target BasTok12345 --table tblAbc --create \'{"Name": "x"}\'',
    ),
    version="0.1.0",
)
