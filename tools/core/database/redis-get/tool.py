"""Read one Redis key type-aware, or scan keys by pattern. Never writes.

The connection comes from a profile in ~/.config/redis.toml (``--profile qat``) or from a repo's .env
(``--env-file ../PMS2.0_PH/server/.env``, variable REDIS_URL unless ``--url-var`` says otherwise). Values
are decoded as UTF-8 with replacement, so binary content shows up as text with � where it is not text.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, Field

from shed import Tool, ToolContext, ToolError
from shed.registry import load_tool_module

CONFIG_FILE = Path.home() / ".config" / "redis.toml"
DEFAULT_URL_VAR = "REDIS_URL"
SCAN_COUNT = 500
PATH_DB_SCHEMES = ("redis", "rediss")
Mode = Literal["key", "scan"]


class Input(BaseModel):
    profile: str | None = Field(None, description="Profile name from ~/.config/redis.toml")
    env_file: str | None = Field(
        None, description="A .env file (for example a repo's) holding the connection URL instead of a profile"
    )
    url_var: str | None = Field(
        None, description=f"Variable in --env-file that holds the URL (default {DEFAULT_URL_VAR})"
    )
    db: int | None = Field(None, ge=0, description="Database index; overrides the profile's and the URL's")
    key: str | None = Field(None, description="Exact key to read (string, hash, list, set, zset or stream)")
    scan: str | None = Field(None, description='Key pattern to scan instead, e.g. "session:*" (no values)')
    limit: int = Field(100, ge=1, le=5000, description="Elements of a key, or keys of a scan, to return")
    timeout: float = Field(5, gt=0, description="Seconds to wait for the server before giving up")


class KeyInfo(BaseModel):
    key: str
    type: str
    ttl: int
    """Seconds left; -1 means no expiry."""


class RedisResult(BaseModel):
    source: str
    """Where the connection came from (profile name or env file and variable); never the URL."""
    host: str
    db: int
    mode: Mode
    key: str | None = None
    type: str | None = None
    ttl: int | None = None
    length: int | None = None
    truncated: bool = False
    """True when the key holds, or the scan matched, more than --limit."""
    value: Any = None
    json_like: bool = False
    """True when a string value parses as JSON."""
    pattern: str | None = None
    keys: list[KeyInfo] = []


def _source_module() -> Any:
    """db_source.py lives in mongo-query; the profile and env-file rules are the same for Redis."""
    return importlib.import_module(f"{load_tool_module('mongo-query').__package__}.db_source")


def with_db(url: str, db: int | None) -> str:
    """The URL with its database replaced; redis:// and rediss:// carry it in the path."""
    if db is None:
        return url
    parts = urlsplit(url)
    if parts.scheme not in PATH_DB_SCHEMES:
        return url
    return urlunsplit((parts.scheme, parts.netloc, f"/{db}", parts.query, parts.fragment))


def url_db(url: str) -> int:
    parts = urlsplit(url)
    if parts.scheme in PATH_DB_SCHEMES:
        text = parts.path.strip("/")
        return int(text) if text.isdigit() else 0
    for pair in parts.query.split("&"):
        name, _, value = pair.partition("=")
        if name == "db" and value.isdigit():
            return int(value)
    return 0


def _profile_db(value: str | int | None) -> int | None:
    """A profile's db as an index; a Mongo-style name is ignored for Redis."""
    if isinstance(value, int):
        return value
    return int(value) if isinstance(value, str) and value.isdigit() else None


def make_client(url: str, db: int | None, timeout: float) -> Any:
    """A redis client (raw bytes) behind the read-only view; imported here so `shed list` stays free of it."""
    import redis

    kwargs: dict[str, Any] = {"socket_timeout": timeout, "socket_connect_timeout": timeout}
    if db is not None and urlsplit(url).scheme not in PATH_DB_SCHEMES:
        kwargs["db"] = db
    readonly = importlib.import_module(f"{load_tool_module('mongo-query').__package__}.readonly")
    return readonly.ReadOnlyRedis(redis.Redis.from_url(with_db(url, db), **kwargs))


def read_failures() -> tuple[type[Exception], ...]:
    from redis.exceptions import RedisError

    return (RedisError,)


def text(value: Any) -> Any:
    """Bytes as UTF-8 text (with replacement); numbers and text pass through."""
    if isinstance(value, bytes | bytearray):
        return bytes(value).decode("utf-8", errors="replace")
    return value


def _take(items: Any, limit: int) -> tuple[list[Any], bool]:
    """Up to ``limit`` items from an iterable, and whether it held more."""
    out: list[Any] = []
    for item in items:
        if len(out) == limit:
            return out, True
        out.append(item)
    return out, False


def read_key(client: Any, key: str, limit: int) -> RedisResult:
    kind = text(client.type(key))
    if kind == "none":
        raise ToolError(f"key not found: {key}")
    ttl = int(client.ttl(key))
    value: Any
    length: int
    truncated = False
    json_like = False
    if kind == "string":
        value = text(client.get(key))
        length = int(client.strlen(key))
        try:
            json.loads(value)
            json_like = True
        except (TypeError, ValueError):
            json_like = False
    elif kind == "hash":
        length = int(client.hlen(key))
        pairs, truncated = _take(client.hscan_iter(key, count=SCAN_COUNT), limit)
        value = {text(field): text(item) for field, item in pairs}
    elif kind == "list":
        length = int(client.llen(key))
        value = [text(item) for item in client.lrange(key, 0, limit - 1)]
        truncated = length > limit
    elif kind == "set":
        length = int(client.scard(key))
        members, truncated = _take(client.sscan_iter(key, count=SCAN_COUNT), limit)
        value = [text(member) for member in members]
    elif kind == "zset":
        length = int(client.zcard(key))
        value = [
            [text(member), float(score)]
            for member, score in client.zrange(key, 0, limit - 1, withscores=True)
        ]
        truncated = length > limit
    elif kind == "stream":
        length = int(client.xlen(key))
        entries = client.xrange(key, min="-", max="+", count=limit)
        value = [
            [text(entry_id), {text(f): text(v) for f, v in fields.items()}] for entry_id, fields in entries
        ]
        truncated = length > limit
    else:
        raise ToolError(f"{key} is a {kind}; this tool reads string, hash, list, set, zset and stream keys")
    return RedisResult(
        source="",
        host="",
        db=0,
        mode="key",
        key=key,
        type=kind,
        ttl=ttl,
        length=length,
        truncated=truncated,
        value=value,
        json_like=json_like,
    )


def scan_keys(client: Any, pattern: str, limit: int) -> tuple[list[KeyInfo], bool]:
    found, truncated = _take(client.scan_iter(match=pattern, count=SCAN_COUNT), limit)
    keys = [KeyInfo(key=text(k), type=text(client.type(k)), ttl=int(client.ttl(k))) for k in found]
    return keys, truncated


def run(params: Input, ctx: ToolContext) -> RedisResult:
    if bool(params.key) == bool(params.scan):
        raise ToolError("give exactly one of --key or --scan")
    source = _source_module().resolve(
        profile=params.profile,
        env_file=params.env_file,
        url_var=params.url_var,
        default_var=DEFAULT_URL_VAR,
        config_file=CONFIG_FILE,
        cwd=ctx.cwd,
    )
    host, _ = _source_module().describe(source.url)
    db = params.db if params.db is not None else _profile_db(source.db)
    shown_db = db if db is not None else url_db(source.url)
    what = f"key {params.key}" if params.key else f"scan {params.scan}"
    ctx.log(f"# {what} on {host}/{shown_db} via {source.label}")
    client = make_client(source.url, db, params.timeout)
    try:
        if params.key:
            result = read_key(client, params.key, params.limit)
            return result.model_copy(update={"source": source.label, "host": host, "db": shown_db})
        keys, truncated = scan_keys(client, str(params.scan), params.limit)
        return RedisResult(
            source=source.label,
            host=host,
            db=shown_db,
            mode="scan",
            pattern=params.scan,
            keys=keys,
            length=len(keys),
            truncated=truncated,
        )
    except read_failures() as exc:
        raise ToolError(f"redis {host}/{shown_db} failed: {exc}") from exc
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()


def _ttl_text(ttl: int | None) -> str:
    return "no expiry" if ttl is None or ttl < 0 else f"ttl {ttl}s"


def render(result: RedisResult) -> str:
    head = f"via {result.source} ({result.host}/{result.db})"
    if result.mode == "scan":
        more = " (more matched; raise --limit)" if result.truncated else ""
        lines = [f"# scan {result.pattern} {head}: {result.length} keys{more}"]
        lines.extend(f"{k.key}  {k.type}  {_ttl_text(k.ttl)}" for k in result.keys)
        return "\n".join(lines) + "\n"
    cut = " (truncated)" if result.truncated else ""
    lines = [f"# {result.key} ({result.type}, {_ttl_text(result.ttl)}, length {result.length}{cut}) {head}"]
    value = result.value
    if isinstance(value, dict):
        lines.extend(f"{field}={item}" for field, item in value.items())
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, list) and len(item) == 2 and not isinstance(item[1], dict):
                lines.append(f"{item[0]}  {item[1]}")
            elif isinstance(item, list) and len(item) == 2:
                fields = " ".join(f"{f}={v}" for f, v in item[1].items())
                lines.append(f"{item[0]}  {fields}")
            else:
                lines.append(str(item))
    else:
        lines.append(str(value))
    return "\n".join(lines) + "\n"


TOOL = Tool(
    name="redis-get",
    description=(
        "Read one Redis key the way its type wants (string, hash, list, set, zset, stream) with its TTL, or "
        "scan keys by pattern with their types and TTLs, using a saved profile (~/.config/redis.toml) or a "
        "repo's .env for the connection. Use it to check a cached session, a rate-limit counter or a queue "
        "while debugging. Never writes."
    ),
    input=Input,
    run=run,
    render=render,
    examples=(
        "shed redis-get --profile qat --key session:abc123",
        'shed redis-get --profile qat --scan "ratelimit:*" --limit 50',
        "shed redis-get --env-file ../PMS2.0_PH/server/.env --key merchant:keys --db 1 --json",
    ),
    version="0.1.0",
)
