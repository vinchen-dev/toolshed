from __future__ import annotations

import fnmatch
import importlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from shed import ToolContext, ToolError
from shed.registry import load_tool, load_tool_module

module = load_tool_module("redis-get")
readonly = importlib.import_module(f"{load_tool_module('mongo-query').__package__}.readonly")
TOOL = load_tool("redis-get")


class FakeRedis:
    """Keys as (type, value, ttl); values hold bytes the way a raw redis client returns them."""

    def __init__(self, data: dict[str, tuple[str, Any, int]]) -> None:
        self.data = data
        self.closed = False
        self.url = ""
        self.db: int | None = None
        self.timeout = 0.0

    def _get(self, key: Any, kind: str) -> Any:
        name = key.decode() if isinstance(key, bytes) else key
        entry = self.data.get(name)
        assert entry is not None and entry[0] == kind, f"{name} is not a {kind}"
        return entry[1]

    def type(self, key: Any) -> bytes:
        name = key.decode() if isinstance(key, bytes) else key
        return self.data[name][0].encode() if name in self.data else b"none"

    def ttl(self, key: Any) -> int:
        name = key.decode() if isinstance(key, bytes) else key
        return self.data[name][2] if name in self.data else -2

    def get(self, key: str) -> bytes:
        return self._get(key, "string")

    def strlen(self, key: str) -> int:
        return len(self._get(key, "string"))

    def hlen(self, key: str) -> int:
        return len(self._get(key, "hash"))

    def hscan_iter(self, key: str, count: int) -> Iterator[tuple[bytes, bytes]]:
        return iter(self._get(key, "hash").items())

    def llen(self, key: str) -> int:
        return len(self._get(key, "list"))

    def lrange(self, key: str, start: int, end: int) -> list[bytes]:
        return self._get(key, "list")[start : end + 1]

    def scard(self, key: str) -> int:
        return len(self._get(key, "set"))

    def sscan_iter(self, key: str, count: int) -> Iterator[bytes]:
        return iter(self._get(key, "set"))

    def zcard(self, key: str) -> int:
        return len(self._get(key, "zset"))

    def zrange(self, key: str, start: int, end: int, withscores: bool) -> list[tuple[bytes, float]]:
        assert withscores
        return self._get(key, "zset")[start : end + 1]

    def xlen(self, key: str) -> int:
        return len(self._get(key, "stream"))

    def xrange(self, key: str, min: str, max: str, count: int) -> list[tuple[bytes, dict[bytes, bytes]]]:
        return self._get(key, "stream")[:count]

    def scan_iter(self, match: str, count: int) -> Iterator[bytes]:
        return iter(k.encode() for k in self.data if fnmatch.fnmatchcase(k, match))

    def close(self) -> None:
        self.closed = True


DATA: dict[str, tuple[str, Any, int]] = {
    "session:abc": ("string", b'{"user": "abc", "role": "ops"}', 3500),
    "greeting": ("string", "caf\xe9 \xff".encode("latin-1"), -1),
    "merchant:keys": ("hash", {b"k1": b"v1", b"k2": b"v2", b"k3": b"v3"}, -1),
    "queue:deposits": ("list", [b"a", b"b", b"c"], -1),
    "tags": ("set", [b"x", b"y"], -1),
    "leaderboard": ("zset", [(b"p1", 10.0), (b"p2", 7.5), (b"p3", 1.0)], 60),
    "events": ("stream", [(b"1-0", {b"type": b"login"}), (b"2-0", {b"type": b"logout"})], -1),
    "session:def": ("string", b"plain", 10),
}


def ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(cwd=tmp_path, log=lambda _: None)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeRedis:
    client = FakeRedis(dict(DATA))

    def make_client(url: str, db: int | None, timeout: float) -> Any:
        client.url, client.db, client.timeout = url, db, timeout
        return readonly.ReadOnlyRedis(client)  # the tool runs through the same view as production

    monkeypatch.setattr(module, "make_client", make_client)
    config = tmp_path / "redis.toml"
    config.write_text(
        '[qat]\nurl = "redis://:secret@cache-qat:6379/2"\n\n[prod]\nurl = "redis://cache-prod:6379"\ndb = 1\n'
    )
    monkeypatch.setattr(module, "CONFIG_FILE", config)
    return client


def test_reads_a_json_string_with_its_ttl(fake: FakeRedis, tmp_path: Path) -> None:
    result = TOOL.run(TOOL.input(profile="qat", key="session:abc"), ctx(tmp_path))
    assert (result.source, result.host, result.db, result.mode) == ("profile qat", "cache-qat:6379", 2, "key")
    assert (result.type, result.ttl, result.length, result.json_like) == ("string", 3500, 30, True)
    assert result.value == '{"user": "abc", "role": "ops"}' and not result.truncated
    assert fake.db is None and fake.timeout == 5 and fake.closed and "secret" not in str(result)
    text = TOOL.render(result) if TOOL.render else ""
    assert text == (
        "# session:abc (string, ttl 3500s, length 30) via profile qat (cache-qat:6379/2)\n"
        '{"user": "abc", "role": "ops"}\n'
    )


def test_binary_strings_decode_with_replacement(fake: FakeRedis, tmp_path: Path) -> None:
    result = TOOL.run(TOOL.input(profile="qat", key="greeting"), ctx(tmp_path))
    assert result.value == "caf� �" and not result.json_like and result.ttl == -1


def test_each_type_reads_its_own_way_and_truncates_at_limit(fake: FakeRedis, tmp_path: Path) -> None:
    hashed = TOOL.run(TOOL.input(profile="qat", key="merchant:keys", limit=2), ctx(tmp_path))
    assert hashed.value == {"k1": "v1", "k2": "v2"} and hashed.truncated and hashed.length == 3
    assert "k1=v1\nk2=v2" in (TOOL.render(hashed) if TOOL.render else "")
    listed = TOOL.run(TOOL.input(profile="qat", key="queue:deposits", limit=2), ctx(tmp_path))
    assert listed.value == ["a", "b"] and listed.truncated and listed.length == 3
    members = TOOL.run(TOOL.input(profile="qat", key="tags"), ctx(tmp_path))
    assert sorted(members.value) == ["x", "y"] and not members.truncated
    ranked = TOOL.run(TOOL.input(profile="qat", key="leaderboard", limit=2), ctx(tmp_path))
    assert ranked.value == [["p1", 10.0], ["p2", 7.5]] and ranked.truncated and ranked.ttl == 60
    assert "p1  10.0\np2  7.5" in (TOOL.render(ranked) if TOOL.render else "")
    stream = TOOL.run(TOOL.input(profile="qat", key="events", limit=1), ctx(tmp_path))
    assert stream.value == [["1-0", {"type": "login"}]] and stream.truncated and stream.length == 2
    assert "1-0  type=login" in (TOOL.render(stream) if TOOL.render else "")


def test_missing_key_is_an_error(fake: FakeRedis, tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="key not found: nope"):
        TOOL.run(TOOL.input(profile="qat", key="nope"), ctx(tmp_path))


def test_scan_lists_keys_with_type_and_ttl(fake: FakeRedis, tmp_path: Path) -> None:
    result = TOOL.run(TOOL.input(profile="qat", scan="session:*"), ctx(tmp_path))
    assert result.mode == "scan" and result.pattern == "session:*" and result.length == 2
    assert [(k.key, k.type, k.ttl) for k in result.keys] == [
        ("session:abc", "string", 3500),
        ("session:def", "string", 10),
    ]
    assert not result.truncated and result.value is None
    text = TOOL.render(result) if TOOL.render else ""
    assert text.startswith(
        "# scan session:* via profile qat (cache-qat:6379/2): 2 keys\nsession:abc  string  ttl 3500s\n"
    )
    capped = TOOL.run(TOOL.input(profile="qat", scan="*", limit=3), ctx(tmp_path))
    assert len(capped.keys) == 3 and capped.truncated


def test_exactly_one_of_key_or_scan(fake: FakeRedis, tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="exactly one"):
        TOOL.run(TOOL.input(profile="qat"), ctx(tmp_path))
    with pytest.raises(ToolError, match="exactly one"):
        TOOL.run(TOOL.input(profile="qat", key="a", scan="a*"), ctx(tmp_path))


def test_db_comes_from_the_flag_then_the_profile_then_the_url(fake: FakeRedis, tmp_path: Path) -> None:
    from_profile = TOOL.run(TOOL.input(profile="prod", key="tags"), ctx(tmp_path))
    assert from_profile.db == 1 and fake.db == 1 and from_profile.host == "cache-prod:6379"
    from_flag = TOOL.run(TOOL.input(profile="qat", key="tags", db=7), ctx(tmp_path))
    assert from_flag.db == 7 and fake.db == 7
    env = tmp_path / ".env"
    env.write_text("REDIS_URL=redis://cache-env:6379/4\nCACHE_URL=redis://other:6379\n")
    from_url = TOOL.run(TOOL.input(env_file=".env", key="tags"), ctx(tmp_path))
    assert (from_url.source, from_url.host, from_url.db) == (
        ".env:REDIS_URL",
        "cache-env:6379",
        4,
    ) and fake.db is None
    other = TOOL.run(TOOL.input(env_file=".env", url_var="CACHE_URL", key="tags"), ctx(tmp_path))
    assert (other.source, other.db) == (".env:CACHE_URL", 0)


def test_url_helpers() -> None:
    assert module.with_db("redis://:pw@h:6379/0", 3) == "redis://:pw@h:6379/3"
    assert module.with_db("rediss://h:6380", 2) == "rediss://h:6380/2"
    assert module.with_db("unix:///tmp/redis.sock?db=1", 2) == "unix:///tmp/redis.sock?db=1"
    assert module.with_db("redis://h:6379/5", None) == "redis://h:6379/5"
    assert module.url_db("redis://h:6379/5") == 5 and module.url_db("redis://h:6379") == 0
    assert module.url_db("unix:///tmp/redis.sock?db=3") == 3


def test_read_only_view_refuses_every_write() -> None:
    class Raw:
        def __getattr__(self, name: str) -> Any:
            return lambda *a, **k: "ran"

    client = readonly.ReadOnlyRedis(Raw())
    assert client.get("k") == "ran" and client.ttl("k") == "ran"
    for name in (
        "set",
        "delete",
        "hset",
        "lpush",
        "sadd",
        "zadd",
        "xadd",
        "expire",
        "flushdb",
        "flushall",
        "execute_command",
    ):
        with pytest.raises(readonly.ReadOnlyError, match="never write"):
            getattr(client, name)("k")
