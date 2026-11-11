"""Compare the indexes of two MongoDB databases (QAT against PROD, or a database against a saved snapshot).

Each side connects like mongo-query does: a profile from ~/.config/mongo.toml or a repo's .env. Index
definitions are normalized (name, key, unique, sparse, TTL, partial filter, collation locale, hidden) so
only meaningful differences show. ``--snapshot-out`` saves the left side's indexes as JSON, and
``--snapshot-in`` compares the left side against such a file instead of a live right side.
"""

from __future__ import annotations

import importlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from shed import Tool, ToolContext, ToolError
from shed.registry import load_tool_module

SNAPSHOT_FORMAT = 1
SYSTEM_PREFIX = "system."
Side = Literal["left", "right", "both"]


class Input(BaseModel):
    left: str | None = Field(None, description="Profile name of the left side (from ~/.config/mongo.toml)")
    right: str | None = Field(None, description="Profile name of the right side")
    left_env_file: str | None = Field(None, description="A .env file holding the left side's URL instead")
    right_env_file: str | None = Field(None, description="A .env file holding the right side's URL instead")
    url_var: str | None = Field(
        None, description="Variable in the env files that holds the URL (default MONGODB_URI)"
    )
    left_db: str | None = Field(None, description="Left database name; overrides the profile's and the URL's")
    right_db: str | None = Field(
        None, description="Right database name; overrides the profile's and the URL's"
    )
    collections: list[str] = Field(
        default_factory=list, description="Only these collections (default: every collection on either side)"
    )
    snapshot_out: str | None = Field(None, description="Write the left side's indexes as JSON to this path")
    snapshot_in: str | None = Field(
        None, description="Compare the left side against this snapshot file instead of a live right side"
    )
    timeout: float = Field(5, gt=0, description="Seconds to wait for a server before giving up")


class IndexChange(BaseModel):
    name: str
    left: dict[str, Any]
    right: dict[str, Any]


class CollectionDiff(BaseModel):
    name: str
    only_left: list[str] = []
    only_right: list[str] = []
    different: list[IndexChange] = []
    same: int = 0
    missing_side: Side | None = None
    """Set when the collection itself exists on one side only (or on neither, for a requested name)."""


class Totals(BaseModel):
    collections: int
    only_left: int
    only_right: int
    different: int
    same: int


class IndexDiff(BaseModel):
    left: str
    right: str | None
    """None when only a snapshot was written."""
    snapshot_written: str | None = None
    collections: list[CollectionDiff] = []
    totals: Totals
    clean: bool


class Snapshot(BaseModel):
    format: int = SNAPSHOT_FORMAT
    label: str
    taken: str
    collections: dict[str, dict[str, dict[str, Any]]]
    """collection -> index name -> normalized spec."""


def _mongo_query() -> Any:
    """The mongo-query tool module: its profile file, client factory and source resolution are reused."""
    return load_tool_module("mongo-query")


def _source_module() -> Any:
    return importlib.import_module(f"{_mongo_query().__package__}.db_source")


def make_client(url: str, timeout: float) -> Any:
    return _mongo_query().make_client(url, timeout)


def normalize(name: str, info: dict[str, Any]) -> dict[str, Any]:
    """The parts of an index definition that matter for a diff; v and ns are dropped."""
    collation = info.get("collation") or {}
    partial = info.get("partialFilterExpression")
    return {
        "name": name,
        "key": [[str(field), direction] for field, direction in info.get("key") or []],
        "unique": bool(info.get("unique", False)),
        "sparse": bool(info.get("sparse", False)),
        "expireAfterSeconds": info.get("expireAfterSeconds"),
        "partialFilterExpression": _mongo_query().to_json(partial) if partial is not None else None,
        "collation": collation.get("locale") if isinstance(collation, dict) else None,
        "hidden": bool(info.get("hidden", False)),
    }


def read_side(client: Any, db: str, wanted: list[str]) -> dict[str, dict[str, dict[str, Any]]]:
    """collection -> index name -> spec for one database; system collections are skipped."""
    database = client[db]
    names = [str(n) for n in database.list_collection_names() if not str(n).startswith(SYSTEM_PREFIX)]
    if wanted:
        names = [n for n in names if n in wanted]
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for name in sorted(names):
        info = database[name].index_information()
        out[name] = {str(index): normalize(str(index), spec) for index, spec in info.items()}
    return out


def diff_sides(
    left: dict[str, dict[str, dict[str, Any]]],
    right: dict[str, dict[str, dict[str, Any]]],
    wanted: list[str],
) -> list[CollectionDiff]:
    names = sorted(set(left) | set(right) | set(wanted))
    out: list[CollectionDiff] = []
    for name in names:
        if name not in left and name not in right:
            out.append(CollectionDiff(name=name, missing_side="both"))
            continue
        if name not in right:
            out.append(CollectionDiff(name=name, only_left=sorted(left[name]), missing_side="right"))
            continue
        if name not in left:
            out.append(CollectionDiff(name=name, only_right=sorted(right[name]), missing_side="left"))
            continue
        l_idx, r_idx = left[name], right[name]
        common = sorted(set(l_idx) & set(r_idx))
        changed = [IndexChange(name=i, left=l_idx[i], right=r_idx[i]) for i in common if l_idx[i] != r_idx[i]]
        out.append(
            CollectionDiff(
                name=name,
                only_left=sorted(set(l_idx) - set(r_idx)),
                only_right=sorted(set(r_idx) - set(l_idx)),
                different=changed,
                same=len(common) - len(changed),
            )
        )
    return out


def totals_of(diffs: list[CollectionDiff]) -> Totals:
    return Totals(
        collections=len(diffs),
        only_left=sum(len(d.only_left) for d in diffs),
        only_right=sum(len(d.only_right) for d in diffs),
        different=sum(len(d.different) for d in diffs),
        same=sum(d.same for d in diffs),
    )


def load_snapshot(path: Path) -> Snapshot:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        snapshot = Snapshot.model_validate(data)
    except (OSError, ValueError) as exc:
        raise ToolError(f"cannot read snapshot {path}: {exc}") from exc
    if snapshot.format != SNAPSHOT_FORMAT:
        raise ToolError(f"snapshot {path} has format {snapshot.format}; this tool writes {SNAPSHOT_FORMAT}")
    return snapshot


def _live_side(
    ctx: ToolContext, params: Input, *, profile: str | None, env_file: str | None, db_override: str | None
) -> tuple[str, dict[str, dict[str, dict[str, Any]]]]:
    """(label, indexes) for a side read from a database, or None for the label when the side is absent."""
    mq = _mongo_query()
    source = _source_module().resolve(
        profile=profile,
        env_file=env_file,
        url_var=params.url_var,
        default_var=mq.DEFAULT_URL_VAR,
        config_file=mq.CONFIG_FILE,
        cwd=ctx.cwd,
    )
    host, url_db = _source_module().describe(source.url)
    db = db_override or (str(source.db) if source.db else "") or url_db
    if not db:
        raise ToolError(f"no database for {source.label}: give --left-db/--right-db or set db in the profile")
    label = f"{source.label} ({host}/{db})"
    ctx.log(f"# reading indexes of {label}")
    client = make_client(source.url, params.timeout)
    try:
        return label, read_side(client, db, params.collections)
    except mq.query_failures() as exc:
        raise ToolError(f"reading {label} failed: {exc}") from exc
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()


def run(params: Input, ctx: ToolContext) -> IndexDiff:
    if not params.left and not params.left_env_file:
        raise ToolError("the left side needs --left <profile> or --left-env-file <path>")
    has_right = bool(params.right or params.right_env_file)
    if params.snapshot_in and has_right:
        raise ToolError("--snapshot-in replaces the right side; drop --right/--right-env-file")
    if not has_right and not params.snapshot_in and not params.snapshot_out:
        raise ToolError("nothing to compare: give --right, --right-env-file, --snapshot-in or --snapshot-out")

    left_label, left = _live_side(
        ctx, params, profile=params.left, env_file=params.left_env_file, db_override=params.left_db
    )
    written: str | None = None
    if params.snapshot_out:
        path = (ctx.cwd / Path(params.snapshot_out).expanduser()).resolve()
        snapshot = Snapshot(
            label=left_label, taken=datetime.now(UTC).isoformat(timespec="seconds"), collections=left
        )
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(snapshot.model_dump_json(indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            raise ToolError(f"cannot write snapshot {path}: {exc}") from exc
        written = str(path)
        ctx.log(f"# wrote {sum(len(v) for v in left.values())} indexes of {len(left)} collections to {path}")

    if params.snapshot_in:
        path = (ctx.cwd / Path(params.snapshot_in).expanduser()).resolve()
        snapshot = load_snapshot(path)
        right_label: str | None = f"snapshot {path.name} ({snapshot.label}, {snapshot.taken})"
        right = snapshot.collections
        if params.collections:
            right = {name: idx for name, idx in right.items() if name in params.collections}
    elif has_right:
        right_label, right = _live_side(
            ctx, params, profile=params.right, env_file=params.right_env_file, db_override=params.right_db
        )
    else:
        empty = Totals(collections=0, only_left=0, only_right=0, different=0, same=0)
        return IndexDiff(left=left_label, right=None, snapshot_written=written, totals=empty, clean=True)

    diffs = diff_sides(left, right, params.collections)
    totals = totals_of(diffs)
    clean = not any(d.only_left or d.only_right or d.different or d.missing_side for d in diffs)
    return IndexDiff(
        left=left_label,
        right=right_label,
        snapshot_written=written,
        collections=diffs,
        totals=totals,
        clean=clean,
    )


def _spec_line(spec: dict[str, Any]) -> str:
    key = ", ".join(f"{field}:{direction}" for field, direction in spec.get("key", []))
    flags = [name for name in ("unique", "sparse", "hidden") if spec.get(name)]
    if spec.get("expireAfterSeconds") is not None:
        flags.append(f"ttl={spec['expireAfterSeconds']}s")
    if spec.get("partialFilterExpression") is not None:
        flags.append(f"partial={json.dumps(spec['partialFilterExpression'], separators=(',', ':'))}")
    if spec.get("collation"):
        flags.append(f"collation={spec['collation']}")
    return f"{{{key}}}" + (f" [{' '.join(flags)}]" if flags else "")


def render(result: IndexDiff) -> str:
    lines: list[str] = []
    if result.snapshot_written:
        lines.append(f"wrote snapshot of {result.left} to {result.snapshot_written}")
    if result.right is None:
        return "\n".join(lines) + "\n"
    lines.append(f"# {result.left}  vs  {result.right}")
    for d in result.collections:
        if not (d.only_left or d.only_right or d.different or d.missing_side):
            continue
        where = ""
        if d.missing_side:
            side = "neither side" if d.missing_side == "both" else f"{d.missing_side} only"
            where = f" (collection exists on {side})"
        lines.append(f"\n{d.name}{where}")
        lines.extend(f"  only left:  {name}" for name in d.only_left)
        lines.extend(f"  only right: {name}" for name in d.only_right)
        for change in d.different:
            lines.append(f"  differs:    {change.name}")
            lines.append(f"    left:  {_spec_line(change.left)}")
            lines.append(f"    right: {_spec_line(change.right)}")
    t = result.totals
    if result.clean:
        lines.append(f"no differences ({t.collections} collections, {t.same} indexes)")
    else:
        lines.append(
            f"\n{t.collections} collections: {t.only_left} only left, {t.only_right} only right, "
            f"{t.different} differ, {t.same} same"
        )
    return "\n".join(lines) + "\n"


TOOL = Tool(
    name="mongo-index-diff",
    description=(
        "Compare MongoDB indexes between two databases (for example QAT and PROD) or against a saved "
        "snapshot, per collection: indexes on one side only and definitions that differ (keys, unique, "
        "sparse, TTL, partial filter, collation, hidden). Use it before a release or when a query is slow "
        "on one environment only. Read-only; connections come from ~/.config/mongo.toml profiles or a .env."
    ),
    input=Input,
    run=run,
    render=render,
    examples=(
        "shed mongo-index-diff --left qat --right prod",
        "shed mongo-index-diff --left qat --right prod --collections players --collections proposals --json",
        "shed mongo-index-diff --left prod --snapshot-out prod-indexes.json",
        "shed mongo-index-diff --left qat --snapshot-in prod-indexes.json",
    ),
    version="0.1.0",
)
