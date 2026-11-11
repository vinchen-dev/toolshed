"""Read-only views over pymongo and redis-py clients.

The database tools only ever see these wrappers, so a write cannot be issued by mistake: an attribute that is
not on the allow-list raises ReadOnlyError, a ToolError, before any command leaves the process.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from shed import ToolError

WRITE_STAGES = frozenset({"$out", "$merge"})
MONGO_COLLECTION_READS = frozenset(
    {
        "aggregate",
        "count_documents",
        "distinct",
        "estimated_document_count",
        "find",
        "find_one",
        "full_name",
        "index_information",
        "list_indexes",
        "name",
    }
)
MONGO_DATABASE_READS = frozenset({"list_collection_names", "list_collections", "name"})
REDIS_READS = frozenset(
    {
        "close",
        "dbsize",
        "exists",
        "get",
        "hget",
        "hgetall",
        "hlen",
        "hscan_iter",
        "lindex",
        "llen",
        "lrange",
        "ping",
        "pttl",
        "scan_iter",
        "scard",
        "sismember",
        "sscan_iter",
        "strlen",
        "ttl",
        "type",
        "xlen",
        "xrange",
        "zcard",
        "zrange",
        "zscore",
    }
)


class ReadOnlyError(ToolError):
    """A tool reached for an operation outside the read-only allow-list."""


def _guard(name: str, allowed: frozenset[str], what: str) -> None:
    if name not in allowed:
        raise ReadOnlyError(f"{what}.{name} is not a read operation; the database tools never write")


def check_pipeline(pipeline: Iterable[Any]) -> list[Any]:
    """The pipeline as a list, once no stage writes to a collection."""
    stages = list(pipeline)
    for stage in stages:
        if isinstance(stage, dict) and WRITE_STAGES & set(stage):
            found = ", ".join(sorted(WRITE_STAGES & set(stage)))
            raise ReadOnlyError(
                f"aggregate stage {found} writes to a collection; the database tools never write"
            )
    return stages


class ReadOnlyCollection:
    def __init__(self, raw: Any) -> None:
        self._raw = raw

    def __getattr__(self, name: str) -> Any:
        _guard(name, MONGO_COLLECTION_READS, "collection")
        return getattr(self._raw, name)

    def aggregate(self, pipeline: Iterable[Any], *args: Any, **kwargs: Any) -> Any:
        return self._raw.aggregate(check_pipeline(pipeline), *args, **kwargs)


class ReadOnlyDatabase:
    def __init__(self, raw: Any) -> None:
        self._raw = raw

    def __getattr__(self, name: str) -> Any:
        _guard(name, MONGO_DATABASE_READS, "database")
        return getattr(self._raw, name)

    def __getitem__(self, name: str) -> ReadOnlyCollection:
        return ReadOnlyCollection(self._raw[name])


class ReadOnlyMongo:
    """A MongoClient that only hands out read-only databases."""

    def __init__(self, raw: Any) -> None:
        self._raw = raw

    def __getitem__(self, name: str) -> ReadOnlyDatabase:
        return ReadOnlyDatabase(self._raw[name])

    def close(self) -> None:
        self._raw.close()


class ReadOnlyRedis:
    """A redis client that exposes read commands only."""

    def __init__(self, raw: Any) -> None:
        self._raw = raw

    def __getattr__(self, name: str) -> Any:
        _guard(name, REDIS_READS, "redis")
        return getattr(self._raw, name)
