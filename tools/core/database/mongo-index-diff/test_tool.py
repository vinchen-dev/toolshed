from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

import pytest

from shed import ToolContext, ToolError
from shed.registry import load_tool, load_tool_module

module = load_tool_module("mongo-index-diff")
mongo_query = load_tool_module("mongo-query")
readonly = importlib.import_module(f"{mongo_query.__package__}.readonly")
TOOL = load_tool("mongo-index-diff")

ID_INDEX = {"v": 2, "key": [("_id", 1)], "ns": "pms.x"}


def idx(key: list[tuple[str, Any]], **options: Any) -> dict[str, Any]:
    return {"v": 2, "key": key, "ns": "pms.x", **options}


class FakeCollection:
    def __init__(self, indexes: dict[str, dict[str, Any]]) -> None:
        self.indexes = indexes

    def index_information(self) -> dict[str, dict[str, Any]]:
        return dict(self.indexes)


class FakeDB:
    def __init__(self, collections: dict[str, dict[str, dict[str, Any]]]) -> None:
        self.collections = {name: FakeCollection(indexes) for name, indexes in collections.items()}

    def list_collection_names(self) -> list[str]:
        return list(self.collections)

    def __getitem__(self, name: str) -> FakeCollection:
        return self.collections[name]


class FakeClient:
    def __init__(self, dbs: dict[str, FakeDB]) -> None:
        self.dbs = dbs
        self.closed = False

    def __getitem__(self, name: str) -> FakeDB:
        return self.dbs[name]

    def close(self) -> None:
        self.closed = True


QAT = {
    "players": {
        "_id_": ID_INDEX,
        "username_1": idx([("username", 1)], unique=True),
        "createdAt_-1": idx([("createdAt", -1)]),
        "qat_only_1": idx([("qatOnly", 1)]),
    },
    "proposals": {"_id_": ID_INDEX, "status_1_createdAt_-1": idx([("status", 1), ("createdAt", -1)])},
    "system.views": {"_id_": ID_INDEX},
    "qat_scratch": {"_id_": ID_INDEX},
}
PROD = {
    "players": {
        "_id_": ID_INDEX,
        "username_1": idx([("username", 1)]),
        "createdAt_-1": idx([("createdAt", -1)], expireAfterSeconds=3600),
        "prod_only_1": idx([("prodOnly", 1)]),
    },
    "proposals": {"_id_": ID_INDEX, "status_1_createdAt_-1": idx([("status", 1), ("createdAt", -1)])},
}


def ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(cwd=tmp_path, log=lambda _: None)


@pytest.fixture
def clients(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, FakeClient]:
    made = {
        "mongodb://u:p@qat:27017/pms": FakeClient({"pms": FakeDB(QAT)}),
        "mongodb://u:p@prod:27017/pms": FakeClient({"pms": FakeDB(PROD)}),
    }
    monkeypatch.setattr(module, "make_client", lambda url, timeout: readonly.ReadOnlyMongo(made[url]))
    config = tmp_path / "mongo.toml"
    config.write_text(
        '[qat]\nurl = "mongodb://u:p@qat:27017/pms"\n[prod]\nurl = "mongodb://u:p@prod:27017/pms"\n'
    )
    monkeypatch.setattr(mongo_query, "CONFIG_FILE", config)
    return made


def test_reports_what_differs_per_collection(clients: dict[str, FakeClient], tmp_path: Path) -> None:
    result = TOOL.run(
        TOOL.input(left="qat", right="prod", collections=["players", "proposals", "ghost"]), ctx(tmp_path)
    )
    assert result.left == "profile qat (qat:27017/pms)" and result.right == "profile prod (prod:27017/pms)"
    by_name = {d.name: d for d in result.collections}
    players = by_name["players"]
    assert (players.only_left, players.only_right, players.same) == (["qat_only_1"], ["prod_only_1"], 1)
    assert [c.name for c in players.different] == ["createdAt_-1", "username_1"]
    unique = players.different[1]
    assert unique.left["unique"] is True and unique.right["unique"] is False
    assert unique.left["key"] == [["username", 1]] and "v" not in unique.left and "ns" not in unique.left
    ttl = players.different[0]
    assert (ttl.left["expireAfterSeconds"], ttl.right["expireAfterSeconds"]) == (None, 3600)
    assert by_name["proposals"].same == 2 and not by_name["proposals"].different
    assert by_name["ghost"].missing_side == "both"
    assert "qat_scratch" not in by_name and "system.views" not in by_name
    assert result.totals.model_dump() == {
        "collections": 3,
        "only_left": 1,
        "only_right": 1,
        "different": 2,
        "same": 3,
    }
    assert not result.clean and all(c.closed for c in clients.values())
    text = TOOL.render(result) if TOOL.render else ""
    assert "players\n  only left:  qat_only_1\n  only right: prod_only_1\n  differs:    createdAt_-1" in text
    assert "left:  {username:1} [unique]" in text and "right: {createdAt:-1} [ttl=3600s]" in text
    assert "ghost (collection exists on neither side)" in text
    assert text.rstrip().endswith("3 collections: 1 only left, 1 only right, 2 differ, 3 same")


def test_a_collection_on_one_side_only_is_flagged(clients: dict[str, FakeClient], tmp_path: Path) -> None:
    result = TOOL.run(TOOL.input(left="qat", right="prod"), ctx(tmp_path))
    scratch = next(d for d in result.collections if d.name == "qat_scratch")
    assert scratch.missing_side == "right" and scratch.only_left == ["_id_"]
    assert "qat_scratch (collection exists on right only)" in (TOOL.render(result) if TOOL.render else "")


def test_identical_sides_are_clean(clients: dict[str, FakeClient], tmp_path: Path) -> None:
    result = TOOL.run(TOOL.input(left="qat", right="qat", collections=["proposals"]), ctx(tmp_path))
    assert result.clean and result.totals.same == 2
    assert (TOOL.render(result) if TOOL.render else "").endswith(
        "no differences (1 collections, 2 indexes)\n"
    )


def test_snapshot_round_trip(clients: dict[str, FakeClient], tmp_path: Path) -> None:
    written = TOOL.run(TOOL.input(left="prod", snapshot_out="snap/prod.json"), ctx(tmp_path))
    path = tmp_path / "snap" / "prod.json"
    assert written.snapshot_written == str(path) and written.right is None and written.clean
    assert "wrote snapshot of profile prod (prod:27017/pms) to" in (
        TOOL.render(written) if TOOL.render else ""
    )
    data = json.loads(path.read_text())
    assert data["format"] == 1 and data["collections"]["players"]["username_1"]["key"] == [["username", 1]]

    same = TOOL.run(TOOL.input(left="prod", snapshot_in="snap/prod.json"), ctx(tmp_path))
    assert same.clean and same.right is not None and same.right.startswith("snapshot prod.json (profile prod")

    changed = TOOL.run(
        TOOL.input(left="qat", snapshot_in="snap/prod.json", collections=["players"]), ctx(tmp_path)
    )
    assert not changed.clean and changed.collections[0].only_right == ["prod_only_1"]


def test_input_errors(clients: dict[str, FakeClient], tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="left side needs"):
        TOOL.run(TOOL.input(right="prod"), ctx(tmp_path))
    with pytest.raises(ToolError, match="nothing to compare"):
        TOOL.run(TOOL.input(left="qat"), ctx(tmp_path))
    with pytest.raises(ToolError, match="replaces the right side"):
        TOOL.run(TOOL.input(left="qat", right="prod", snapshot_in="x.json"), ctx(tmp_path))
    with pytest.raises(ToolError, match="cannot read snapshot"):
        TOOL.run(TOOL.input(left="qat", snapshot_in="missing.json"), ctx(tmp_path))
    (tmp_path / "old.json").write_text('{"format": 0, "label": "x", "taken": "t", "collections": {}}')
    with pytest.raises(ToolError, match="has format 0"):
        TOOL.run(TOOL.input(left="qat", snapshot_in="old.json"), ctx(tmp_path))
    with pytest.raises(ToolError, match='no profile "staging"'):
        TOOL.run(TOOL.input(left="qat", right="staging"), ctx(tmp_path))


def test_normalize_keeps_only_what_matters() -> None:
    spec = module.normalize(
        "k",
        {
            "v": 2,
            "ns": "db.c",
            "key": [("a", 1), ("b", "text")],
            "sparse": True,
            "partialFilterExpression": {"deleted": False},
            "collation": {"locale": "en", "strength": 2},
        },
    )
    assert spec == {
        "name": "k",
        "key": [["a", 1], ["b", "text"]],
        "unique": False,
        "sparse": True,
        "expireAfterSeconds": None,
        "partialFilterExpression": {"deleted": False},
        "collation": "en",
        "hidden": False,
    }
