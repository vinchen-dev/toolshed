"""Search Meegle work items, or list the spaces and work item types the plug-in can see. Read-only.

Credentials come from ~/.config/meegle.env (chmod 600) or --env-file: MEEGLE_PLUGIN_ID, MEEGLE_PLUGIN_SECRET
and MEEGLE_USER_KEY (the caller's user_key, from double-clicking your avatar), optional MEEGLE_DOMAIN (default
project.larksuite.com) and MEEGLE_TOKEN_TYPE (0 = plugin token, the default; 1 = the virtual token an
unpublished plug-in uses inside its data-scope space). Every call runs as that user with x-auth-mode 1.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator

from shed import Tool, ToolContext, ToolError
from shed.lib.env import require_env
from shed.lib.manila import parse_manila

from . import meegle_client as mc

ENV_FILE = Path.home() / ".config" / "meegle.env"
MAX_LIMIT = 1000


class Input(BaseModel):
    space: str | None = Field(
        None,
        description="Space to search: its project_key or the simple_name from the space URL. "
        "Without it --type (one value) is required and every space the plug-in is installed in is searched",
    )
    name: str | None = Field(None, description="Work item name to match (fuzzy)")
    ids: list[int] = Field(default_factory=list, description="Work item IDs to fetch (repeatable)")
    type: list[str] = Field(
        default_factory=list, description="Work item type keys, e.g. story issue (see --list-types)"
    )
    status: list[str] = Field(default_factory=list, description="Workflow state keys the items must be in")
    user: list[str] = Field(
        default_factory=list,
        description="user_keys: items where this person is creator, follower or in a role",
    )
    created_since: str | None = Field(None, description="Created at or after, Manila 'YYYY-MM-DD[ HH:MM]'")
    created_until: str | None = Field(None, description="Created at or before, Manila 'YYYY-MM-DD[ HH:MM]'")
    updated_since: str | None = Field(None, description="Updated at or after, Manila 'YYYY-MM-DD[ HH:MM]'")
    updated_until: str | None = Field(None, description="Updated at or before, Manila 'YYYY-MM-DD[ HH:MM]'")
    priority: list[str] = Field(default_factory=list, description="Priority option values")
    tag: list[str] = Field(default_factory=list, description="Tag option values")
    limit: int = Field(
        50, ge=1, le=MAX_LIMIT, description="Most items to return (pages of 200, or 50 across spaces)"
    )
    list_spaces: bool = Field(False, description="List the spaces the plug-in and user can see, and exit")
    list_types: bool = Field(False, description="List the work item types of --space, and exit")
    resolve_users: bool = Field(
        True, description="Turn owner user_keys into names (one extra call per 100 users)"
    )
    env_file: str | None = Field(
        None, description="Env file holding the MEEGLE_* variables instead of ~/.config/meegle.env"
    )

    @model_validator(mode="after")
    def _check(self) -> Input:
        if self.list_spaces and self.list_types:
            raise ValueError("--list-spaces and --list-types are exclusive")
        if self.list_types and not self.space:
            raise ValueError("--list-types needs --space")
        if not self.list_spaces and not self.list_types and not self.space and len(self.type) != 1:
            raise ValueError("without --space give exactly one --type (the across-space search needs it)")
        return self


class Person(BaseModel):
    key: str
    name: str | None = None


class Item(BaseModel):
    id: int
    type: str
    name: str
    state: str
    owner: Person | None
    created_at: str
    updated_at: str
    current_nodes: list[str]
    space: str
    """project_key the item lives in."""


class Space(BaseModel):
    project_key: str
    name: str
    simple_name: str


class ItemType(BaseModel):
    type_key: str
    name: str
    disabled: bool


class SearchResult(BaseModel):
    mode: str
    """items, spaces or types."""
    domain: str
    space: str | None
    total: int | None
    """Items matching on the server when it says, else None."""
    count: int
    truncated: bool
    items: list[Item] = []
    spaces: list[Space] = []
    types: list[ItemType] = []


def make_client(plugin_id: str, plugin_secret: str, user_key: str, domain: str, token_type: str) -> Any:
    return mc.MeegleClient(plugin_id, plugin_secret, user_key, domain=domain, token_type=token_type)


def open_client(env_file: str | None, cwd: Path) -> Any:
    """A client from the env file; raises ToolError naming what is missing or wrong."""
    path = cwd / Path(env_file).expanduser() if env_file else ENV_FILE
    creds = require_env("MEEGLE_PLUGIN_ID", "MEEGLE_PLUGIN_SECRET", "MEEGLE_USER_KEY", env_file=path)
    domain = os.environ.get("MEEGLE_DOMAIN") or mc.DEFAULT_DOMAIN
    token_type = os.environ.get("MEEGLE_TOKEN_TYPE") or "0"
    if token_type not in mc.TOKEN_TYPES:
        raise ToolError(f"MEEGLE_TOKEN_TYPE must be 0 or 1, not {token_type!r} (in {path})")
    try:
        return make_client(
            creds["MEEGLE_PLUGIN_ID"],
            creds["MEEGLE_PLUGIN_SECRET"],
            creds["MEEGLE_USER_KEY"],
            domain,
            token_type,
        )
    except mc.MeegleError as exc:
        raise ToolError(str(exc)) from exc


def close_client(client: Any) -> None:
    close = getattr(client, "close", None)
    if callable(close):
        close()


def to_ms(text: str | None, flag: str, *, end: bool = False) -> int | None:
    """Manila 'YYYY-MM-DD[ HH:MM[:SS]]' as epoch ms; a bare date is its start, or its end when ``end``."""
    if text is None:
        return None
    value = text.strip()
    if len(value) == 10:
        value += " 23:59:59" if end else " 00:00:00"
    try:
        return int(parse_manila(value).timestamp() * 1000)
    except ValueError as exc:
        raise ToolError(f"{flag}: {exc}") from exc


def time_range(since: int | None, until: int | None) -> dict[str, int] | None:
    if since is None and until is None:
        return None
    window: dict[str, int] = {"start": since if since is not None else 0}
    if until is not None:
        window["end"] = until
    return window


def build_body(params: Input) -> dict[str, Any]:
    body: dict[str, Any] = {}
    if params.name:
        body["work_item_name"] = params.name
    if params.ids:
        body["work_item_ids"] = params.ids
    if params.space:
        if params.type:
            body["work_item_type_keys"] = params.type
        if params.user:
            body["user_keys"] = params.user
    else:
        body["work_item_type_key"] = params.type[0]
        if params.user:
            body["search_user"] = {"user_keys": params.user}
    created = time_range(
        to_ms(params.created_since, "--created-since"),
        to_ms(params.created_until, "--created-until", end=True),
    )
    updated = time_range(
        to_ms(params.updated_since, "--updated-since"),
        to_ms(params.updated_until, "--updated-until", end=True),
    )
    if created:
        body["created_at"] = created
    if updated:
        body["updated_at"] = updated
    if params.status:
        body["work_item_status"] = [{"state_key": s} for s in params.status]
    if params.priority:
        body["priorities"] = params.priority
    if params.tag:
        body["tags"] = params.tag
    return body


def search(client: Any, params: Input, log: Any) -> tuple[list[dict[str, Any]], int | None, bool]:
    """Every page up to the limit: (raw items, server total or None, truncated)."""
    page_cap = mc.PAGE_SIZE if params.space else mc.ACROSS_PAGE_SIZE
    body = build_body(params)
    items: list[dict[str, Any]] = []
    total: int | None = None
    page = 1
    while len(items) < params.limit:
        size = min(page_cap, params.limit - len(items))
        page_items, total = mc.filter_page(
            client, params.space, {**body, "page_num": page, "page_size": size}
        )
        items.extend(page_items)
        log(f"# page {page}: {len(page_items)} items" + (f" of {total}" if total is not None else ""))
        if len(page_items) < size or (total is not None and len(items) >= total):
            break
        page += 1
    truncated = (total is not None and total > len(items)) or (total is None and len(items) >= params.limit)
    return items[: params.limit], total, truncated


def resolve(client: Any, keys: set[str], wanted: bool, log: Any) -> dict[str, str]:
    """Names for user_keys, best effort: a failed lookup only means the keys stay keys."""
    if not wanted or not keys:
        return {}
    try:
        return mc.users(client, keys)
    except mc.MeegleError as exc:
        log(f"# user names not resolved: {exc}")
        return {}


def to_item(raw: dict[str, Any], names: dict[str, str]) -> Item:
    owner_key = raw_owner = mc.item_field(raw, "owner")
    owner = None
    if isinstance(raw_owner, str) and raw_owner:
        owner = Person(key=owner_key, name=names.get(owner_key))
    nodes = [
        str(n.get("name") or n.get("id") or "") for n in raw.get("current_nodes") or [] if isinstance(n, dict)
    ]
    return Item(
        id=int(raw.get("id") or 0),
        type=str(raw.get("work_item_type_key") or ""),
        name=str(raw.get("name") or ""),
        state=mc.item_state(raw),
        owner=owner,
        created_at=mc.ms_text(raw.get("created_at")),
        updated_at=mc.ms_text(raw.get("updated_at")),
        current_nodes=[n for n in nodes if n],
        space=str(raw.get("project_key") or ""),
    )


def run(params: Input, ctx: ToolContext) -> SearchResult:
    client = open_client(params.env_file, ctx.cwd)
    try:
        if params.list_spaces:
            keys = mc.space_keys(client)
            details = mc.space_details(client, keys)
            spaces = [
                Space(
                    project_key=k,
                    name=str(details.get(k, {}).get("name") or ""),
                    simple_name=str(details.get(k, {}).get("simple_name") or ""),
                )
                for k in keys
            ]
            ctx.log(f"# {len(spaces)} spaces")
            return SearchResult(
                mode="spaces",
                domain=client.domain,
                space=None,
                total=len(spaces),
                count=len(spaces),
                truncated=False,
                spaces=spaces,
            )
        if params.list_types:
            assert params.space
            types = [
                ItemType(
                    type_key=str(t.get("type_key") or ""),
                    name=str(t.get("name") or ""),
                    disabled=t.get("is_disable") == 1,
                )
                for t in mc.work_item_types(client, params.space)
            ]
            return SearchResult(
                mode="types",
                domain=client.domain,
                space=params.space,
                total=len(types),
                count=len(types),
                truncated=False,
                types=types,
            )
        raw, total, truncated = search(client, params, ctx.log)
        owners = {v for v in (mc.item_field(r, "owner") for r in raw) if isinstance(v, str) and v}
        names = resolve(client, owners, params.resolve_users, ctx.log)
        items = [to_item(r, names) for r in raw]
        return SearchResult(
            mode="items",
            domain=client.domain,
            space=params.space,
            total=total,
            count=len(items),
            truncated=truncated,
            items=items,
        )
    except mc.MeegleError as exc:
        raise ToolError(str(exc)) from exc
    finally:
        close_client(client)


def _cell(text: str, width: int) -> str:
    return text[: width - 1] + "…" if len(text) > width else text.ljust(width)


def render(result: SearchResult) -> str:
    if result.mode == "spaces":
        lines = [f"# {result.count} spaces on {result.domain}"]
        lines += [f"{s.project_key}  {s.simple_name:<20} {s.name}" for s in result.spaces]
        return "\n".join(lines) + "\n"
    if result.mode == "types":
        lines = [f"# {result.count} work item types in {result.space}"]
        lines += [f"{t.type_key:<24} {t.name}" + ("  (disabled)" if t.disabled else "") for t in result.types]
        return "\n".join(lines) + "\n"
    where = result.space or "all spaces"
    head = f"# {result.count} items in {where}"
    if result.total is not None:
        head += f" of {result.total}"
    if result.truncated:
        head += " (truncated: raise --limit or narrow the filters)"
    lines = [head, f"{'id':<12} {'type':<10} {'state':<14} {'owner':<18} {'updated':<19} name"]
    for i in result.items:
        owner = (i.owner.name or i.owner.key) if i.owner else ""
        lines.append(
            f"{i.id:<12} {_cell(i.type, 10)} {_cell(i.state, 14)} {_cell(owner, 18)} "
            f"{i.updated_at:<19} {i.name}"
        )
    return "\n".join(lines) + "\n"


TOOL = Tool(
    name="meegle-search",
    description=(
        "Search Meegle work items (tickets, stories, issues) in a space by name, id, type, state, person, "
        "dates, priority or tag, or across every space for one type; --list-spaces and --list-types show "
        "what the plug-in can see. Read-only. Use it to find a ticket before meegle-item, or to list what is "
        "open for someone; needs ~/.config/meegle.env (MEEGLE_PLUGIN_ID, MEEGLE_PLUGIN_SECRET, "
        "MEEGLE_USER_KEY)."
    ),
    input=Input,
    run=run,
    render=render,
    examples=(
        "shed meegle-search --list-spaces",
        "shed meegle-search --space pms --list-types",
        "shed meegle-search --space pms --type issue --status doing --updated-since 2026-11-01",
        "shed meegle-search --space pms --name 'GCash' --limit 20 --json",
        "shed meegle-search --type story --user 7491126018028093443",
    ),
    version="0.1.0",
)
