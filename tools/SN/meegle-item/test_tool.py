from __future__ import annotations

import importlib
import re
from pathlib import Path
from typing import Any

import pytest

from shed import ToolContext, ToolError
from shed.registry import load_tool, load_tool_module

module = load_tool_module("meegle-item")
search = load_tool_module("meegle-search")
mc = importlib.import_module(f"{search.__package__}.meegle_client")
TOOL = load_tool("meegle-item")

OWNER = "7491126018028093443"
OTHER = "7012514555133820947"
PROJECT = "68382b399de4b0ec6c5a0001"

RAW = {
    "id": 15677342,
    "project_key": PROJECT,
    "simple_name": "pms",
    "name": "Merchant ClickHouse Proposal crashes",
    "work_item_type_key": "issue",
    "pattern": "Node",
    "template_id": 3047075,
    "created_at": 1762800000000,
    "updated_at": 1762840000000,
    "created_by": OWNER,
    "updated_by": OTHER,
    "work_item_status": {
        "state_key": "doing",
        "is_init_state": False,
        "updated_at": 1762840000000,
        "updated_by": OTHER,
        "history": [
            {"state_key": "started", "updated_at": 1762800000000, "updated_by": OWNER, "is_init_state": True}
        ],
    },
    "current_nodes": [{"id": "state_2", "name": "Fixing", "owners": [OTHER]}],
    "fields": [
        {
            "field_key": "template",
            "field_alias": "template",
            "field_type_key": "work_item_template",
            "field_value": {"id": 3047075, "version": 8},
        },
        {"field_key": "owner", "field_alias": "owner", "field_type_key": "user", "field_value": OWNER},
        {
            "field_key": "role_owners",
            "field_alias": "",
            "field_type_key": "role_owners",
            "field_value": [{"role": "qa", "owners": [OTHER]}],
        },
        {
            "field_key": "start_time",
            "field_alias": "start_time",
            "field_type_key": "date",
            "field_value": 1762800000000,
        },
        {
            "field_key": "description",
            "field_alias": "description",
            "field_type_key": "multi_text",
            "field_value": "React error #152 on the proposal page",
        },
        {
            "field_key": "priority",
            "field_alias": "",
            "field_type_key": "select",
            "field_value": {"label": "P1", "value": "p1"},
        },
        {"field_key": "empty", "field_alias": "", "field_type_key": "text", "field_value": ""},
    ],
}


def ok(data: Any, **extra: Any) -> dict[str, Any]:
    return {"err": {}, "err_code": 0, "err_msg": "", "data": data, **extra}


def comment(id_: int, at: int, who: str, text: str) -> dict[str, Any]:
    return {
        "id": id_,
        "work_item_id": 15677342,
        "work_item_type_key": "issue",
        "created_at": at,
        "operator": who,
        "content": text,
    }


USERS = ok([{"user_key": OWNER, "name": {"en_us": "Vince"}}, {"user_key": OTHER, "name": {"en_us": "Ana"}}])
CHANGES = ok(
    {
        "has_more": False,
        "start_from": "",
        "op_records": [
            {
                "operation_time": 1762840000000,
                "operator_type": "user",
                "operator": OTHER,
                "operation_type": "modify",
                "op_record_module": "field_mod",
                "record_contents": [
                    {
                        "object": {"object_type": "field", "object_value": "priority"},
                        "old": ["p2"],
                        "new": ["p1"],
                        "add": [],
                        "delete": [],
                    }
                ],
            },
            {
                "operation_time": 1762800000000,
                "operator_type": "auto",
                "operator": "rule_9",
                "operation_type": "create",
                "op_record_module": "work_item_mod",
                "record_contents": [],
            },
        ],
    }
)


class FakeClient:
    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self.user_key = OWNER
        self.domain = "project.larksuite.com"
        self.closed = False

    def _answer(self, method: str, path: str, body: dict[str, Any] | None) -> dict[str, Any]:
        self.calls.append((method, path, body))
        if path not in self.responses:
            raise mc.MeegleError(
                f"{method} {path} failed: code=1000051135 msg=WorkItem Not Found", 1000051135
            )
        answer = self.responses[path]
        if isinstance(answer, list):
            answer = answer.pop(0) if len(answer) > 1 else answer[0]
        if isinstance(answer, mc.MeegleError):
            raise answer
        return answer

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._answer("GET", path, params)

    def post(self, path: str, json: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._answer("POST", path, json)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    monkeypatch.setattr(search, "ENV_FILE", tmp_path / "absent.env")
    monkeypatch.setenv("MEEGLE_PLUGIN_ID", "MII_1")
    monkeypatch.setenv("MEEGLE_PLUGIN_SECRET", "s3cret")
    monkeypatch.setenv("MEEGLE_USER_KEY", OWNER)
    monkeypatch.delenv("MEEGLE_TOKEN_TYPE", raising=False)

    def install(responses: dict[str, Any]) -> FakeClient:
        fake = FakeClient(responses)
        monkeypatch.setattr(search, "make_client", lambda *args: fake)
        return fake

    return install


def ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(cwd=tmp_path, log=lambda _: None)


def test_reads_an_item_with_fields_comments_and_names(env: Any, tmp_path: Path) -> None:
    fake = env(
        {
            "/pms/work_item/filter": ok(
                [{"id": 15677342, "work_item_type_key": "issue"}], pagination={"total": 1}
            ),
            "/pms/work_item/issue/query": ok([RAW]),
            "/pms/work_item/issue/15677342/comments": ok(
                [
                    comment(1, 1762810000000, OWNER, "repro attached"),
                    comment(2, 1762820000000, OTHER, "fixed in m-1"),
                ],
                pagination={"total": 2},
            ),
            "/user/query": USERS,
        }
    )
    item = TOOL.run(TOOL.input(id=15677342, space="pms"), ctx(tmp_path))
    assert (item.type, item.state, item.pattern, item.project_key) == ("issue", "doing", "Node", PROJECT)
    assert item.owner == module.Person(key=OWNER, name="Vince") and item.updated_by == module.Person(
        key=OTHER, name="Ana"
    )
    assert item.current_nodes == ["Fixing"] and [h.state for h in item.state_history] == ["started"]
    assert item.description == "React error #152 on the proposal page"
    values = {f.key: f.value for f in item.fields}
    assert (
        values["template"] == "3047075 v8"
        and values["role_owners"] == "qa: Ana"
        and values["priority"] == "P1"
    )
    assert values["start_time"] == "2025-11-11 02:40:00" and values["empty"] == ""
    assert next(f.raw for f in item.fields if f.key == "priority") == {"label": "P1", "value": "p1"}
    assert [(c.operator and c.operator.name, c.content) for c in item.comments] == [
        ("Vince", "repro attached"),
        ("Ana", "fixed in m-1"),
    ]
    assert item.changes == [] and fake.closed
    paths = [p for _, p, _ in fake.calls]
    assert paths == [
        "/pms/work_item/filter",
        "/pms/work_item/issue/query",
        "/pms/work_item/issue/15677342/comments",
        "/user/query",
    ]
    assert fake.calls[1][2] == {"work_item_ids": [15677342]}
    assert set(fake.calls[3][2]["user_keys"]) == {OWNER, OTHER}
    text = module.render(item)
    assert text.startswith("# issue 15677342: Merchant ClickHouse Proposal crashes  [doing]")
    assert (
        re.search(r"^owner\s+Vince$", text, re.M)
        and "## Comments (2)" in text
        and "Ana: fixed in m-1" in text
        and "## Description" in text
    )


def test_explicit_type_skips_discovery_and_pages_comments(env: Any, tmp_path: Path) -> None:
    pages = [
        ok([comment(n, 1762800000000 + n, OWNER, f"c{n}") for n in range(1, 201)]),
        ok([comment(201, 1762900000000, OWNER, "last")]),
    ]
    fake = env(
        {
            "/pms/work_item/issue/query": ok([RAW]),
            "/pms/work_item/issue/15677342/comments": pages,
            "/user/query": USERS,
        }
    )
    item = TOOL.run(
        TOOL.input(id=15677342, space="pms", type="issue", fields=["name", "-description"]), ctx(tmp_path)
    )
    assert len(item.comments) == 201 and item.comments[-1].content == "last"
    comment_calls = [b for _, p, b in fake.calls if p.endswith("/comments")]
    assert comment_calls == [{"page_size": 200, "page_num": 1}, {"page_size": 200, "page_num": 2}]
    assert fake.calls[0][2] == {"work_item_ids": [15677342], "fields": ["name", "-description"]}
    assert not any(p.endswith("/filter") for _, p, _ in fake.calls)


def test_changelog_and_no_comments(env: Any, tmp_path: Path) -> None:
    fake = env(
        {"/pms/work_item/issue/query": ok([RAW]), "/op_record/work_item/list": CHANGES, "/user/query": USERS}
    )
    item = TOOL.run(
        TOOL.input(id=15677342, space="pms", type="issue", comments=False, changelog=True), ctx(tmp_path)
    )
    assert item.comments == []
    assert [
        (c.operation, c.operator and (c.operator.name or c.operator.key), c.details) for c in item.changes
    ] == [
        ("modify", "Ana", "field priority: old=p2 new=p1"),
        ("create", "rule_9", ""),
    ]
    log_call = next(b for _, p, b in fake.calls if p == "/op_record/work_item/list")
    assert log_call == {"project_key": PROJECT, "work_item_ids": [15677342], "page_size": 200}
    assert "## Changes (2)" in module.render(item) and "rule_9  create work_item_mod" in module.render(item)


def test_not_found_and_user_lookup_fallback(env: Any, tmp_path: Path) -> None:
    env({"/pms/work_item/filter": ok([], pagination={"total": 0})})
    with pytest.raises(ToolError, match="work item 404 not found in space pms"):
        TOOL.run(TOOL.input(id=404, space="pms"), ctx(tmp_path))
    env({"/pms/work_item/issue/query": ok([]), "/pms/work_item/issue/404/comments": ok([])})
    with pytest.raises(ToolError, match=r"work item 404 \(issue\) not found"):
        TOOL.run(TOOL.input(id=404, space="pms", type="issue"), ctx(tmp_path))
    env(
        {
            "/pms/work_item/issue/query": ok([RAW]),
            "/pms/work_item/issue/15677342/comments": ok([]),
            "/user/query": mc.MeegleError("POST /user/query failed: code=10301", 10301),
        }
    )
    item = TOOL.run(TOOL.input(id=15677342, space="pms", type="issue"), ctx(tmp_path))
    assert item.owner == module.Person(key=OWNER, name=None) and item.created_by == module.Person(
        key=OWNER, name=None
    )


def test_api_errors_and_token_type(env: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env(
        {
            "/pms/work_item/issue/query": mc.MeegleError(
                "POST /pms/work_item/issue/query failed: code=10404 msg=No Project Permission "
                "(the user in MEEGLE_USER_KEY has no access to that space)",
                10404,
            )
        }
    )
    with pytest.raises(ToolError, match=r"10404.*no access"):
        TOOL.run(TOOL.input(id=1, space="pms", type="issue"), ctx(tmp_path))
    monkeypatch.setenv("MEEGLE_TOKEN_TYPE", "x")
    with pytest.raises(ToolError, match="MEEGLE_TOKEN_TYPE must be 0 or 1"):
        TOOL.run(TOOL.input(id=1, space="pms", type="issue"), ctx(tmp_path))
