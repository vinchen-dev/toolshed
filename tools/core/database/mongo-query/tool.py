"""Read-only MongoDB access: find, count or aggregate on one collection, or list a database's collections.

The connection comes from a profile in ~/.config/mongo.toml (``--profile qat``) or from a repo's .env
(``--env-file ../PMS_TS/.env``, variable MONGODB_URI unless ``--url-var`` says otherwise); see db_source.py.
Filters, projections, sorts and pipelines are MongoDB Extended JSON, so ``{"_id": {"$oid": "..."}}`` and
``{"$date": "..."}`` work. Documents come back the same way.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from shed import Tool, ToolContext, ToolError

from .db_source import describe, resolve
from .readonly import ReadOnlyMongo

CONFIG_FILE = Path.home() / ".config" / "mongo.toml"
DEFAULT_URL_VAR = "MONGODB_URI"
WRITE_STAGES = ("$out", "$merge")
Mode = Literal["find", "count", "aggregate", "collections"]


class Input(BaseModel):
    profile: str | None = Field(None, description="Profile name from ~/.config/mongo.toml")
    env_file: str | None = Field(
        None, description="A .env file (for example a repo's) holding the connection URL instead of a profile"
    )
    url_var: str | None = Field(
        None, description=f"Variable in --env-file that holds the URL (default {DEFAULT_URL_VAR})"
    )
    db: str | None = Field(None, description="Database name; overrides the profile's and the URL's")
    collection: str | None = Field(
        None, description="Collection to read (required unless --list-collections)"
    )
    filter: str = Field("{}", description="Query filter as a JSON object (Extended JSON allowed)")
    projection: str | None = Field(None, description='Fields to return as a JSON object, e.g. {"name": 1}')
    sort: str | None = Field(None, description='Sort as a JSON object, e.g. {"createdAt": -1}')
    limit: int = Field(20, ge=1, le=1000, description="Maximum documents to return")
    skip: int = Field(0, ge=0, description="Documents to skip before returning (find only)")
    count: bool = Field(
        False, description="Return how many documents match --filter instead of the documents"
    )
    aggregate: str | None = Field(
        None,
        description="Aggregation pipeline as a JSON array; replaces filter/projection/sort/count. "
        "A $out or $merge stage is refused",
    )
    list_collections: bool = Field(False, description="List the database's collections and exit")
    timeout: float = Field(5, gt=0, description="Seconds to wait for a server before giving up")


class QueryResult(BaseModel):
    source: str
    """Where the connection came from (profile name or env file and variable); never the URL."""
    host: str
    db: str
    collection: str | None
    mode: Mode
    count: int
    truncated: bool = False
    """True when more documents matched than --limit allowed."""
    docs: list[Any] = []
    collections: list[str] = []


def make_client(url: str, timeout: float) -> Any:
    """A pymongo client behind the read-only view; imported here so `shed list` stays free of it."""
    from pymongo import MongoClient

    ms = int(timeout * 1000)
    return ReadOnlyMongo(MongoClient(url, serverSelectionTimeoutMS=ms, connectTimeoutMS=ms))


def query_failures() -> tuple[type[Exception], ...]:
    from pymongo.errors import PyMongoError

    return (PyMongoError,)


def _json_util() -> Any:
    from bson import json_util

    return json_util


def parse_object(text: str, flag: str) -> dict[str, Any]:
    try:
        value = _json_util().loads(text)
    except ValueError as exc:
        raise ToolError(f"{flag} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ToolError(f"{flag} must be a JSON object")
    return value


def parse_pipeline(text: str) -> list[dict[str, Any]]:
    try:
        value = _json_util().loads(text)
    except ValueError as exc:
        raise ToolError(f"--aggregate is not valid JSON: {exc}") from exc
    if not isinstance(value, list) or not all(isinstance(stage, dict) for stage in value):
        raise ToolError("--aggregate must be a JSON array of stage objects")
    for stage in value:
        for name in WRITE_STAGES:
            if name in stage:
                raise ToolError(f"{name} writes to a collection; mongo-query is read-only")
    return value


def to_json(doc: Any) -> Any:
    """A document as plain JSON types (ObjectId -> {"$oid": ...}, datetime -> {"$date": ...})."""
    return json.loads(_json_util().dumps(doc))


def _mode(params: Input) -> Mode:
    if params.list_collections:
        return "collections"
    if params.aggregate is not None:
        exclusive = params.filter != "{}" or params.projection or params.sort or params.count or params.skip
        if exclusive:
            raise ToolError("--aggregate replaces --filter, --projection, --sort, --skip and --count")
        return "aggregate"
    return "count" if params.count else "find"


def _database(params: Input, source_db: str | int | None, url_db: str) -> str:
    name = params.db or (str(source_db) if source_db else "") or url_db
    if not name:
        raise ToolError("no database: give --db, set db in the profile, or put it in the URL")
    return name


def run(params: Input, ctx: ToolContext) -> QueryResult:
    mode = _mode(params)
    if mode != "collections" and not params.collection:
        raise ToolError("--collection is required (or --list-collections)")
    source = resolve(
        profile=params.profile,
        env_file=params.env_file,
        url_var=params.url_var,
        default_var=DEFAULT_URL_VAR,
        config_file=CONFIG_FILE,
        cwd=ctx.cwd,
    )
    host, url_db = describe(source.url)
    db = _database(params, source.db, url_db)
    # Parse the inputs before connecting so a typo never costs a round trip.
    query = parse_object(params.filter, "--filter")
    projection = parse_object(params.projection, "--projection") if params.projection else None
    sort = parse_object(params.sort, "--sort") if params.sort else None
    pipeline = parse_pipeline(params.aggregate) if params.aggregate is not None else []

    ctx.log(f"# {mode} on {host}/{db} via {source.label}")
    client = make_client(source.url, params.timeout)
    try:
        database = client[db]
        if mode == "collections":
            names = sorted(str(n) for n in database.list_collection_names())
            return QueryResult(
                source=source.label,
                host=host,
                db=db,
                collection=None,
                mode=mode,
                count=len(names),
                collections=names,
            )
        collection = database[params.collection]
        if mode == "count":
            total = int(collection.count_documents(query))
            return QueryResult(
                source=source.label, host=host, db=db, collection=params.collection, mode=mode, count=total
            )
        if mode == "aggregate":
            raw = list(collection.aggregate([*pipeline, {"$limit": params.limit + 1}]))
        else:
            cursor = collection.find(query, projection).skip(params.skip).limit(params.limit + 1)
            if sort:
                cursor = cursor.sort(list(sort.items()))
            raw = list(cursor)
    except query_failures() as exc:
        raise ToolError(f"{mode} on {host}/{db} failed: {exc}") from exc
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()
    truncated = len(raw) > params.limit
    docs = [to_json(doc) for doc in raw[: params.limit]]
    return QueryResult(
        source=source.label,
        host=host,
        db=db,
        collection=params.collection,
        mode=mode,
        count=len(docs),
        truncated=truncated,
        docs=docs,
    )


def render(result: QueryResult) -> str:
    where = f"{result.db}.{result.collection}" if result.collection else result.db
    head = f"# {result.mode} {where} via {result.source} ({result.host})"
    if result.mode == "collections":
        return "\n".join([f"{head}: {result.count} collections", *result.collections]) + "\n"
    if result.mode == "count":
        return f"{head}: {result.count} documents\n"
    more = " (more matched; raise --limit)" if result.truncated else ""
    lines = [f"{head}: {result.count} documents{more}"]
    lines.extend(json.dumps(doc, separators=(",", ":"), ensure_ascii=False) for doc in result.docs)
    return "\n".join(lines) + "\n"


TOOL = Tool(
    name="mongo-query",
    description=(
        "Read-only MongoDB query: find, count or aggregate on one collection, or list collections, using a "
        "saved profile (~/.config/mongo.toml) or a repo's .env for the connection. Use it to check what a "
        "record looks like in QAT or PROD, count matches, or inspect data behind a bug without a shell "
        "into the database. Never writes: $out and $merge are refused."
    ),
    input=Input,
    run=run,
    render=render,
    examples=(
        'shed mongo-query --profile qat --collection players --filter \'{"username": "abc"}\' --limit 5',
        'shed mongo-query --profile qat --collection proposals --count --filter \'{"status": "PENDING"}\'',
        "shed mongo-query --env-file ../PMS_TS/.env --url-var PLAYER_DB_URL --collection players "
        '--aggregate \'[{"$group": {"_id": "$status", "n": {"$sum": 1}}}]\' --json',
        "shed mongo-query --profile prod --list-collections",
    ),
    version="0.1.0",
)
