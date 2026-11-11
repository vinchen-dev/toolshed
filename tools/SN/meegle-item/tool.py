"""Read one Meegle work item: its fields, workflow state, comments and, on request, its change log.

Credentials are the meegle-search tool's: ~/.config/meegle.env or --env-file with MEEGLE_PLUGIN_ID,
MEEGLE_PLUGIN_SECRET, MEEGLE_USER_KEY, optional MEEGLE_DOMAIN and MEEGLE_TOKEN_TYPE (0 = plugin token, the
default; 1 = the virtual token an unpublished plug-in uses inside its data-scope space). Read-only.
"""

from __future__ import annotations

import importlib
from typing import Any

from pydantic import BaseModel, Field

from shed import Tool, ToolContext, ToolError
from shed.registry import load_tool_module

MAX_COMMENTS = 1000
MAX_CHANGES = 1000


class Input(BaseModel):
    id: int = Field(description="Work item ID (the number under ··· > ID on the item, or from meegle-search)")
    space: str = Field(description="Space the item lives in: its project_key or the simple_name from the URL")
    type: str | None = Field(
        None, description="Work item type key, e.g. story or issue; found via a search when absent"
    )
    fields: list[str] = Field(
        default_factory=list, description="Field keys to return (default all); '-key' excludes one instead"
    )
    comments: bool = Field(True, description="Include the item's comments, oldest first")
    changelog: bool = Field(False, description="Include the change log (operation records since 2024-07-02)")
    resolve_users: bool = Field(True, description="Turn user_keys into names (one extra call per 100 users)")
    env_file: str | None = Field(
        None, description="Env file holding the MEEGLE_* variables instead of ~/.config/meegle.env"
    )


class Person(BaseModel):
    key: str
    name: str | None = None


class StateChange(BaseModel):
    state: str
    at: str
    by: Person | None


class FieldView(BaseModel):
    key: str
    alias: str
    type: str
    value: str
    """The value flattened for reading; `raw` keeps what Meegle sent."""
    raw: Any = None


class Comment(BaseModel):
    id: int
    time: str
    operator: Person | None
    content: str


class Change(BaseModel):
    time: str
    operator: Person | None
    operator_type: str
    operation: str
    module: str
    details: str


class ItemResult(BaseModel):
    id: int
    space: str
    project_key: str
    type: str
    name: str
    pattern: str
    state: str
    state_history: list[StateChange]
    current_nodes: list[str]
    owner: Person | None
    created_by: Person | None
    updated_by: Person | None
    created_at: str
    updated_at: str
    description: str
    fields: list[FieldView]
    comments: list[Comment]
    changes: list[Change]


def _search() -> Any:
    """The meegle-search tool module: its client factory and env handling are reused here."""
    return load_tool_module("meegle-search")


def _client_module() -> Any:
    return importlib.import_module(f"{_search().__package__}.meegle_client")


def make_client(plugin_id: str, plugin_secret: str, user_key: str, domain: str, token_type: str) -> Any:
    return _search().make_client(plugin_id, plugin_secret, user_key, domain, token_type)


def discover_type(mc: Any, client: Any, space: str, item_id: int) -> str:
    items, _ = mc.filter_page(client, space, {"work_item_ids": [item_id], "page_num": 1, "page_size": 1})
    for item in items:
        if int(item.get("id") or 0) == item_id and item.get("work_item_type_key"):
            return str(item["work_item_type_key"])
    raise ToolError(
        f"work item {item_id} not found in space {space} (give --type if the search cannot see it)"
    )


def person(key: Any, names: dict[str, str]) -> Person | None:
    if not isinstance(key, str) or not key:
        return None
    return Person(key=key, name=names.get(key))


def collect_user_keys(
    mc: Any, raw: dict[str, Any], comments: list[dict[str, Any]], changes: list[dict[str, Any]]
) -> set[str]:
    keys: set[str] = set()
    for k in ("created_by", "updated_by"):
        if isinstance(raw.get(k), str):
            keys.add(raw[k])
    status = raw.get("work_item_status") or {}
    if isinstance(status, dict):
        if isinstance(status.get("updated_by"), str):
            keys.add(status["updated_by"])
        for h in status.get("history") or []:
            if isinstance(h, dict) and isinstance(h.get("updated_by"), str):
                keys.add(h["updated_by"])
    for node in raw.get("current_nodes") or []:
        if isinstance(node, dict):
            keys |= mc.user_keys_in(node.get("owners"))
    for field in raw.get("fields") or []:
        if isinstance(field, dict):
            type_key = str(field.get("field_type_key") or "")
            if type_key in ("user", "multi_user", "user_list", "role_owners") or type_key.endswith("_user"):
                keys |= mc.user_keys_in(field.get("field_value"))
    keys |= {c["operator"] for c in comments if isinstance(c.get("operator"), str)}
    keys |= {
        c["operator"]
        for c in changes
        if c.get("operator_type") == "user" and isinstance(c.get("operator"), str)
    }
    return {k for k in keys if k}


def change_details(record: dict[str, Any]) -> str:
    parts = []
    for content in record.get("record_contents") or []:
        if not isinstance(content, dict):
            continue
        obj = content.get("object") or {}
        target = (
            f"{obj.get('object_type', '')} {obj.get('object_value', '')}".strip()
            if isinstance(obj, dict)
            else ""
        )
        moves = []
        for label in ("old", "new", "add", "delete"):
            values = content.get(label)
            if values:
                moves.append(
                    f"{label}={', '.join(str(v) for v in values)}"
                    if isinstance(values, list)
                    else f"{label}={values}"
                )
        parts.append((target + ": " if target else "") + " ".join(moves))
    return "; ".join(p for p in parts if p)


def run(params: Input, ctx: ToolContext) -> ItemResult:
    search = _search()
    mc = _client_module()
    client = search.open_client(params.env_file, ctx.cwd)
    try:
        type_key = params.type or discover_type(mc, client, params.space, params.id)
        found = mc.query_items(client, params.space, type_key, [params.id], params.fields or None)
        raw = next((i for i in found if int(i.get("id") or 0) == params.id), None)
        if raw is None:
            raise ToolError(f"work item {params.id} ({type_key}) not found in space {params.space}")
        project_key = str(raw.get("project_key") or params.space)
        comments = (
            mc.comments(client, params.space, type_key, params.id, MAX_COMMENTS) if params.comments else []
        )
        changes = mc.change_log(client, project_key, params.id, MAX_CHANGES) if params.changelog else []
        names = search.resolve(
            client, collect_user_keys(mc, raw, comments, changes), params.resolve_users, ctx.log
        )
    except mc.MeegleError as exc:
        raise ToolError(str(exc)) from exc
    finally:
        search.close_client(client)
    ctx.log(
        f"# {type_key} {params.id}: {len(raw.get('fields') or [])} fields, "
        f"{len(comments)} comments, {len(changes)} changes"
    )

    status = raw.get("work_item_status") if isinstance(raw.get("work_item_status"), dict) else {}
    history = [
        StateChange(
            state=str(h.get("state_key") or ""),
            at=mc.ms_text(h.get("updated_at")),
            by=person(h.get("updated_by"), names),
        )
        for h in status.get("history") or []
        if isinstance(h, dict)
    ]
    views: list[FieldView] = []
    description = ""
    for field in raw.get("fields") or []:
        if not isinstance(field, dict):
            continue
        key = str(field.get("field_key") or "")
        ftype = str(field.get("field_type_key") or "")
        value = mc.flatten(field.get("field_value"), ftype, names)
        views.append(
            FieldView(
                key=key,
                alias=str(field.get("field_alias") or ""),
                type=ftype,
                value=value,
                raw=field.get("field_value"),
            )
        )
        if key == "description" and not description:
            description = value
    nodes = [
        str(n.get("name") or n.get("id") or "") for n in raw.get("current_nodes") or [] if isinstance(n, dict)
    ]
    return ItemResult(
        id=params.id,
        space=params.space,
        project_key=project_key,
        type=type_key,
        name=str(raw.get("name") or ""),
        pattern=str(raw.get("pattern") or ""),
        state=mc.item_state(raw),
        state_history=history,
        current_nodes=[n for n in nodes if n],
        owner=person(mc.item_field(raw, "owner"), names),
        created_by=person(raw.get("created_by"), names),
        updated_by=person(raw.get("updated_by"), names),
        created_at=mc.ms_text(raw.get("created_at")),
        updated_at=mc.ms_text(raw.get("updated_at")),
        description=description,
        fields=views,
        comments=[
            Comment(
                id=int(c.get("id") or 0),
                time=mc.ms_text(c.get("created_at")),
                operator=person(c.get("operator"), names),
                content=str(c.get("content") or ""),
            )
            for c in comments
        ],
        changes=[
            Change(
                time=mc.ms_text(r.get("operation_time")),
                operator=person(r.get("operator"), names)
                if r.get("operator_type") == "user"
                else Person(key=str(r.get("operator") or "")),
                operator_type=str(r.get("operator_type") or ""),
                operation=str(r.get("operation_type") or ""),
                module=str(r.get("op_record_module") or ""),
                details=change_details(r),
            )
            for r in changes
        ],
    )


def _who(p: Person | None) -> str:
    return (p.name or p.key) if p else ""


def render(item: ItemResult) -> str:
    lines = [f"# {item.type} {item.id}: {item.name}  [{item.state}]"]
    pairs = [
        ("space", f"{item.space}" + (f" ({item.project_key})" if item.project_key != item.space else "")),
        ("owner", _who(item.owner)),
        ("nodes", ", ".join(item.current_nodes)),
        ("created", f"{item.created_at} by {_who(item.created_by)}".strip()),
        ("updated", f"{item.updated_at} by {_who(item.updated_by)}".strip()),
    ]
    skip = {"description", "owner"}
    pairs += [(f.alias or f.key, f.value) for f in item.fields if f.key not in skip and f.value]
    width = max((len(k) for k, _ in pairs), default=8)
    lines += [f"{k:<{width}}  {v}" for k, v in pairs if v]
    if item.state_history:
        lines.append("")
        lines.append("## States")
        lines += [f"{h.at}  {h.state}  {_who(h.by)}".rstrip() for h in item.state_history]
    if item.description:
        lines += ["", "## Description", item.description.rstrip()]
    if item.comments:
        lines += ["", f"## Comments ({len(item.comments)})"]
        lines += [f"{c.time}  {_who(c.operator)}: {c.content.rstrip()}" for c in item.comments]
    if item.changes:
        lines += ["", f"## Changes ({len(item.changes)})"]
        lines += [
            f"{c.time}  {_who(c.operator) or c.operator_type}  {c.operation} {c.module}  {c.details}".rstrip()
            for c in item.changes
        ]
    return "\n".join(lines) + "\n"


TOOL = Tool(
    name="meegle-item",
    description=(
        "Read one Meegle work item by ID: name, state and state history, current nodes, owner, every field "
        "(flattened for reading, raw with --json), the description, its comments and optionally its change "
        "log. Read-only. Use it to read a ticket's details after meegle-search found it; the type key is "
        "discovered when --type is absent."
    ),
    input=Input,
    run=run,
    render=render,
    examples=(
        "shed meegle-item --space pms --id 15677342",
        "shed meegle-item --space pms --id 15677342 --type issue --changelog",
        "shed meegle-item --space pms --id 15677342 --no-comments --fields name --fields owner --json",
    ),
    version="0.1.0",
)
