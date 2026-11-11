from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import pytest
from bson import ObjectId

from shed import ToolContext, ToolError
from shed.registry import load_tool, load_tool_module

module = load_tool_module("mongo-query")
db_source = importlib.import_module(f"{module.__package__}.db_source")
readonly = importlib.import_module(f"{module.__package__}.readonly")
TOOL = load_tool("mongo-query")

OID = ObjectId("5f1d7f3e9c9d4b1a2c3d4e5f")


class FakeCursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self.docs = docs
        self.sorted_by: list[tuple[str, int]] | None = None

    def skip(self, n: int) -> FakeCursor:
        self.docs = self.docs[n:]
        return self

    def limit(self, n: int) -> FakeCursor:
        self.docs = self.docs[:n]
        return self

    def sort(self, keys: list[tuple[str, int]]) -> FakeCursor:
        self.sorted_by = keys
        field, direction = keys[0]
        self.docs = sorted(self.docs, key=lambda d: d.get(field, 0), reverse=direction < 0)
        return self

    def __iter__(self):
        return iter(self.docs)


class FakeCollection:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self.docs = docs
        self.calls: list[tuple[str, Any]] = []

    @staticmethod
    def _matches(doc: dict[str, Any], query: dict[str, Any]) -> bool:
        return all(doc.get(k) == v for k, v in query.items())

    def find(self, query: dict[str, Any], projection: dict[str, Any] | None = None) -> FakeCursor:
        self.calls.append(("find", (query, projection)))
        return FakeCursor([d for d in self.docs if self._matches(d, query)])

    def count_documents(self, query: dict[str, Any]) -> int:
        self.calls.append(("count", query))
        return sum(1 for d in self.docs if self._matches(d, query))

    def aggregate(self, pipeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self.calls.append(("aggregate", pipeline))
        limit = pipeline[-1]["$limit"]
        return [{"_id": "x", "n": len(self.docs)}][:limit]


class FakeDB:
    def __init__(self, collections: dict[str, FakeCollection]) -> None:
        self.collections = collections

    def list_collection_names(self) -> list[str]:
        return list(self.collections)

    def __getitem__(self, name: str) -> FakeCollection:
        return self.collections.setdefault(name, FakeCollection([]))


class FakeClient:
    def __init__(self, dbs: dict[str, FakeDB]) -> None:
        self.dbs = dbs
        self.closed = False
        self.url = ""
        self.timeout = 0.0

    def __getitem__(self, name: str) -> FakeDB:
        return self.dbs.setdefault(name, FakeDB({}))

    def close(self) -> None:
        self.closed = True


def ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(cwd=tmp_path, log=lambda _: None)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeClient:
    players = FakeCollection(
        [
            {"_id": OID, "username": "abc", "level": 2},
            {"_id": ObjectId(), "username": "def", "level": 5},
            {"_id": ObjectId(), "username": "ghi", "level": 1},
        ]
    )
    client = FakeClient({"pms": FakeDB({"players": players, "proposals": FakeCollection([])})})

    def make_client(url: str, timeout: float) -> Any:
        client.url, client.timeout = url, timeout
        return readonly.ReadOnlyMongo(client)  # the tool runs through the same view as production

    monkeypatch.setattr(module, "make_client", make_client)
    config = tmp_path / "mongo.toml"
    config.write_text(
        '[qat]\nurl = "mongodb://user:pw@qat-host:27017/pms?replicaSet=rs0"\n\n'
        '[prod]\nurl = "mongodb://user:pw@prod-a:27017,prod-b:27017"\ndb = "pms"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "CONFIG_FILE", config)
    return client


def test_missing_profile_names_the_file_and_the_profiles(fake: FakeClient, tmp_path: Path) -> None:
    with pytest.raises(ToolError, match=r'no profile "staging" in .*mongo\.toml \(profiles: prod, qat\)'):
        TOOL.run(TOOL.input(profile="staging", collection="players"), ctx(tmp_path))
    with pytest.raises(ToolError, match="give --profile"):
        TOOL.run(TOOL.input(collection="players"), ctx(tmp_path))


def test_find_round_trips_extended_json_and_never_shows_credentials(fake: FakeClient, tmp_path: Path) -> None:
    result = TOOL.run(
        TOOL.input(profile="qat", collection="players", filter=f'{{"_id": {{"$oid": "{OID}"}}}}'),
        ctx(tmp_path),
    )
    assert (result.source, result.host, result.db, result.mode, result.count) == (
        "profile qat",
        "qat-host:27017",
        "pms",
        "find",
        1,
    )
    assert result.docs == [{"_id": {"$oid": str(OID)}, "username": "abc", "level": 2}]
    assert fake.url.startswith("mongodb://user:pw@") and fake.timeout == 5 and fake.closed
    assert "user:pw" not in TOOL.render(result) if TOOL.render else True


def test_find_marks_truncation_and_passes_sort_skip_and_projection(fake: FakeClient, tmp_path: Path) -> None:
    result = TOOL.run(
        TOOL.input(
            profile="prod", collection="players", limit=2, sort='{"level": -1}', projection='{"level": 1}'
        ),
        ctx(tmp_path),
    )
    assert result.host == "prod-a:27017,prod-b:27017" and result.db == "pms"
    assert [d["level"] for d in result.docs] == [5, 2] and result.truncated and result.count == 2
    collection = fake.dbs["pms"].collections["players"]
    assert collection.calls[-1] == ("find", ({}, {"level": 1}))
    text = TOOL.render(result) if TOOL.render else ""
    assert text.startswith(
        "# find pms.players via profile prod (prod-a:27017,prod-b:27017): 2 documents (more"
    )
    assert '{"_id":{"$oid":' in text


def test_count_and_list_collections(fake: FakeClient, tmp_path: Path) -> None:
    counted = TOOL.run(
        TOOL.input(profile="qat", collection="players", count=True, filter='{"level": 2}'), ctx(tmp_path)
    )
    assert (counted.mode, counted.count, counted.docs) == ("count", 1, [])
    assert (
        TOOL.render(counted) == "# count pms.players via profile qat (qat-host:27017): 1 documents\n"
        if TOOL.render
        else True
    )
    listed = TOOL.run(TOOL.input(profile="qat", list_collections=True), ctx(tmp_path))
    assert (listed.mode, listed.collections, listed.count) == ("collections", ["players", "proposals"], 2)


def test_aggregate_runs_read_only_pipelines_with_a_cap(fake: FakeClient, tmp_path: Path) -> None:
    result = TOOL.run(
        TOOL.input(profile="qat", collection="players", aggregate='[{"$group": {"_id": "$level"}}]', limit=5),
        ctx(tmp_path),
    )
    assert result.mode == "aggregate" and result.docs == [{"_id": "x", "n": 3}]
    pipeline = fake.dbs["pms"].collections["players"].calls[-1][1]
    assert pipeline == [{"$group": {"_id": "$level"}}, {"$limit": 6}]
    for stage in ('[{"$out": "copy"}]', '[{"$match": {}}, {"$merge": {"into": "x"}}]'):
        with pytest.raises(ToolError, match="read-only"):
            TOOL.run(TOOL.input(profile="qat", collection="players", aggregate=stage), ctx(tmp_path))
    with pytest.raises(ToolError, match="replaces --filter"):
        TOOL.run(TOOL.input(profile="qat", collection="players", aggregate="[]", count=True), ctx(tmp_path))
    with pytest.raises(ToolError, match="JSON array"):
        TOOL.run(TOOL.input(profile="qat", collection="players", aggregate='{"$match": {}}'), ctx(tmp_path))


def test_env_file_source_reads_the_named_variable_only(fake: FakeClient, tmp_path: Path) -> None:
    env = tmp_path / "repo" / ".env"
    env.parent.mkdir()
    env.write_text(
        "MONGODB_URI=mongodb://u:p@env-host:27017/fromenv\nPLAYER_DB_URL='mongodb://u:p@players:27017/pl'\n"
    )
    result = TOOL.run(TOOL.input(env_file="repo/.env", collection="players"), ctx(tmp_path))
    assert (result.source, result.host, result.db) == (".env:MONGODB_URI", "env-host:27017", "fromenv")
    assert fake.url == "mongodb://u:p@env-host:27017/fromenv"
    other = TOOL.run(
        TOOL.input(env_file="repo/.env", url_var="PLAYER_DB_URL", collection="players"), ctx(tmp_path)
    )
    assert (other.source, other.db) == (".env:PLAYER_DB_URL", "pl")
    with pytest.raises(ToolError, match="REDIS_URL is not set"):
        TOOL.run(TOOL.input(env_file="repo/.env", url_var="REDIS_URL", collection="players"), ctx(tmp_path))
    with pytest.raises(ToolError, match="env file not found"):
        TOOL.run(TOOL.input(env_file="nope.env", collection="players"), ctx(tmp_path))
    with pytest.raises(ToolError, match="not both"):
        TOOL.run(TOOL.input(profile="qat", env_file="repo/.env", collection="players"), ctx(tmp_path))


def test_usage_errors_before_any_connection(fake: FakeClient, tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="--collection is required"):
        TOOL.run(TOOL.input(profile="qat"), ctx(tmp_path))
    with pytest.raises(ToolError, match="not valid JSON"):
        TOOL.run(TOOL.input(profile="qat", collection="players", filter="{nope"), ctx(tmp_path))
    with pytest.raises(ToolError, match="must be a JSON object"):
        TOOL.run(TOOL.input(profile="qat", collection="players", filter="[1]"), ctx(tmp_path))
    (tmp_path / "mongo.toml").write_text('[nodb]\nurl = "mongodb://h:27017"\n')
    with pytest.raises(ToolError, match="no database"):
        TOOL.run(TOOL.input(profile="nodb", collection="players"), ctx(tmp_path))
    assert fake.url == ""


def test_profiles_file_is_validated(tmp_path: Path) -> None:
    path = tmp_path / "mongo.toml"
    assert db_source.load_profiles(path) == {}
    path.write_text("[qat]\ndb = 'x'\n")
    with pytest.raises(ToolError, match=r"\[qat\] needs url"):
        db_source.load_profiles(path)
    path.write_text("qat = 'not a table'\n")
    with pytest.raises(ToolError, match="must be a table"):
        db_source.load_profiles(path)
    path.write_text("[qat\n")
    with pytest.raises(ToolError, match="cannot read"):
        db_source.load_profiles(path)


def test_describe_strips_credentials() -> None:
    assert db_source.describe("mongodb://user:p%40ss@a:27017,b:27018/pms?x=1") == ("a:27017,b:27018", "pms")
    assert db_source.describe("mongodb+srv://u:p@cluster.example.net/") == ("cluster.example.net", "")
    assert db_source.describe("redis://:secret@cache:6379/2") == ("cache:6379", "2")


def test_read_only_view_refuses_every_write() -> None:
    class Raw:
        def __getitem__(self, name: str) -> Raw:
            return self

        def __getattr__(self, name: str) -> Any:
            return lambda *a, **k: "ran"

    client = readonly.ReadOnlyMongo(Raw())
    assert client["pms"]["players"].count_documents({}) == "ran"
    for call in (
        lambda: client["pms"]["players"].insert_one({}),
        lambda: client["pms"]["players"].update_many({}, {}),
        lambda: client["pms"]["players"].delete_many({}),
        lambda: client["pms"]["players"].create_index("x"),
        lambda: client["pms"]["players"].drop(),
        lambda: client["pms"].drop_collection("players"),
        lambda: client["pms"].command("dropDatabase"),
        lambda: client["pms"]["players"].aggregate([{"$match": {}}, {"$out": "copy"}]),
    ):
        with pytest.raises(readonly.ReadOnlyError, match="never write"):
            call()
    with pytest.raises(AttributeError):
        client.drop_database("pms")  # the client view has no such attribute at all
