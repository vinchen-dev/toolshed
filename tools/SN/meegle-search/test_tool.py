from __future__ import annotations

import importlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from shed import ToolContext, ToolError
from shed.registry import load_tool, load_tool_module

module = load_tool_module("meegle-search")
mc = importlib.import_module(f"{module.__package__}.meegle_client")
TOOL = load_tool("meegle-search")

OWNER = "7491126018028093443"
OTHER = "7012514555133820947"


def item(
    id_: int, name: str, state: str = "doing", owner: str = OWNER, type_key: str = "issue"
) -> dict[str, Any]:
    return {
        "id": id_,
        "name": name,
        "work_item_type_key": type_key,
        "project_key": "68382b399de4b0ec6c5a0001",
        "simple_name": "pms",
        "pattern": "Node",
        "created_at": 1762800000000,
        "updated_at": 1762840000000,
        "created_by": owner,
        "updated_by": OTHER,
        "work_item_status": {"state_key": state, "is_init_state": False, "history": []},
        "current_nodes": [{"id": "state_1", "name": "Dev", "owners": [owner]}],
        "fields": [
            {"field_key": "owner", "field_alias": "owner", "field_type_key": "user", "field_value": owner},
            {
                "field_key": "priority",
                "field_alias": "",
                "field_type_key": "select",
                "field_value": {"label": "P1", "value": "p1"},
            },
        ],
    }


class FakeClient:
    """Canned responses keyed by path; a list of responses is consumed one call at a time (pagination)."""

    def __init__(self, responses: dict[str, Any], *, user_key: str = OWNER) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self.user_key = user_key
        self.domain = "project.larksuite.com"
        self.closed = False

    def _answer(self, method: str, path: str, body: dict[str, Any] | None) -> dict[str, Any]:
        self.calls.append((method, path, body))
        if path not in self.responses:
            raise mc.MeegleError(
                f"{method} {path} failed: {mc.describe_error(10301, 'Check Token Perm Failed')}", 10301
            )
        answer = self.responses[path]
        if isinstance(answer, list):
            answer = answer.pop(0) if len(answer) > 1 else answer[0]
        if callable(answer):
            answer = answer(body)
        if isinstance(answer, Exception):
            raise answer
        return cast(dict[str, Any], answer)

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._answer("GET", path, params)

    def post(self, path: str, json: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._answer("POST", path, json)

    def close(self) -> None:
        self.closed = True


def ok(data: Any, **extra: Any) -> dict[str, Any]:
    return {"err": {}, "err_code": 0, "err_msg": "", "data": data, **extra}


USERS = ok(
    [
        {"user_key": OWNER, "name": {"en_us": "Vince", "default": "文斯"}},
        {"user_key": OTHER, "name_en": "Ana", "name": {}},
    ]
)


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Callable[[dict[str, Any]], FakeClient]:
    monkeypatch.setattr(module, "ENV_FILE", tmp_path / "absent.env")
    monkeypatch.setenv("MEEGLE_PLUGIN_ID", "MII_1")
    monkeypatch.setenv("MEEGLE_PLUGIN_SECRET", "s3cret")
    monkeypatch.setenv("MEEGLE_USER_KEY", OWNER)
    monkeypatch.delenv("MEEGLE_DOMAIN", raising=False)
    monkeypatch.delenv("MEEGLE_TOKEN_TYPE", raising=False)
    made: list[FakeClient] = []

    def install(responses: dict[str, Any]) -> FakeClient:
        fake = FakeClient(responses)
        made.append(fake)
        monkeypatch.setattr(module, "make_client", lambda *args: fake)
        return fake

    return install


def ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(cwd=tmp_path, log=lambda _: None)


def test_searches_one_space_and_resolves_owner_names(env: Any, tmp_path: Path) -> None:
    fake = env(
        {
            "/pms/work_item/filter": ok(
                [item(1, "GCash stuck"), item(2, "Split error", "done", OTHER)],
                pagination={"total": 2, "page_num": 1, "page_size": 200},
            ),
            "/user/query": USERS,
        }
    )
    result = TOOL.run(
        TOOL.input(
            space="pms",
            name="GCash",
            type=["issue"],
            status=["doing"],
            created_since="2026-11-01",
            updated_until="2026-11-11",
        ),
        ctx(tmp_path),
    )
    assert (result.mode, result.total, result.count, result.truncated) == ("items", 2, 2, False)
    assert [(i.id, i.state, i.owner and i.owner.name) for i in result.items] == [
        (1, "doing", "Vince"),
        (2, "done", "Ana"),
    ]
    assert result.items[0].current_nodes == ["Dev"] and result.items[0].updated_at == "2025-11-11 13:46:40"
    method, path, body = fake.calls[0]
    assert (method, path) == ("POST", "/pms/work_item/filter")
    assert body == {
        "work_item_name": "GCash",
        "work_item_type_keys": ["issue"],
        "work_item_status": [{"state_key": "doing"}],
        "created_at": {"start": 1793462400000},
        "updated_at": {"start": 0, "end": 1794412799000},
        "page_num": 1,
        "page_size": 50,
    }
    assert fake.closed
    text = module.render(result)
    assert "# 2 items in pms of 2" in text and "Vince" in text and "GCash stuck" in text


def test_pages_until_the_limit_and_marks_truncation(env: Any, tmp_path: Path) -> None:
    pages = [
        ok([item(n, f"i{n}") for n in range(1, 201)], pagination={"total": 450}),
        ok([item(n, f"i{n}") for n in range(201, 301)], pagination={"total": 450}),
    ]
    fake = env({"/pms/work_item/filter": pages, "/user/query": USERS})
    result = TOOL.run(TOOL.input(space="pms", limit=300, resolve_users=False), ctx(tmp_path))
    assert (result.count, result.total, result.truncated) == (300, 450, True)
    sizes = [(b or {}).get("page_num") for _, p, b in fake.calls if p.endswith("/filter")]
    assert sizes == [1, 2] and fake.calls[1][2]["page_size"] == 100
    assert not any(p == "/user/query" for _, p, _ in fake.calls)
    assert result.items[0].owner == module.Person(key=OWNER, name=None)


def test_across_spaces_needs_exactly_one_type(env: Any, tmp_path: Path) -> None:
    fake = env(
        {
            "/work_items/filter_across_project": ok(
                [item(9, "x", type_key="story")], pagination={"total": 1}
            ),
            "/user/query": USERS,
        }
    )
    result = TOOL.run(TOOL.input(type=["story"], user=[OWNER]), ctx(tmp_path))
    assert result.count == 1 and result.space is None
    assert fake.calls[0][2] == {
        "work_item_type_key": "story",
        "search_user": {"user_keys": [OWNER]},
        "page_num": 1,
        "page_size": 50,
    }
    with pytest.raises(ValueError, match="exactly one --type"):
        TOOL.input(name="x")
    with pytest.raises(ValueError, match="needs --space"):
        TOOL.input(list_types=True)


def test_lists_spaces_with_names_and_types(env: Any, tmp_path: Path) -> None:
    env(
        {
            "/projects": ok(["k1", "k2"]),
            "/projects/detail": ok(
                {
                    "k1": {"name": "PMS", "simple_name": "pms", "project_key": "k1"},
                    "k2": {"name": "Finance", "simple_name": "fin"},
                }
            ),
            "/pms/work_item/all-types": ok(
                [
                    {"type_key": "issue", "name": "Issue", "is_disable": 2},
                    {"type_key": "old", "name": "Old", "is_disable": 1},
                ]
            ),
        }
    )
    spaces = TOOL.run(TOOL.input(list_spaces=True), ctx(tmp_path))
    assert [(s.project_key, s.simple_name, s.name) for s in spaces.spaces] == [
        ("k1", "pms", "PMS"),
        ("k2", "fin", "Finance"),
    ]
    assert "k2  fin" in module.render(spaces)
    types = TOOL.run(TOOL.input(space="pms", list_types=True), ctx(tmp_path))
    assert [(t.type_key, t.disabled) for t in types.types] == [("issue", False), ("old", True)]
    assert "(disabled)" in module.render(types)


def test_user_lookup_failure_keeps_keys(env: Any, tmp_path: Path) -> None:
    env(
        {
            "/pms/work_item/filter": ok([item(1, "a")], pagination={"total": 1}),
            "/user/query": mc.MeegleError("POST /user/query failed: code=10301", 10301),
        }
    )
    result = TOOL.run(TOOL.input(space="pms"), ctx(tmp_path))
    assert result.items[0].owner == module.Person(key=OWNER, name=None)


def test_meegle_errors_become_tool_errors_with_hints(env: Any, tmp_path: Path) -> None:
    env({})
    with pytest.raises(ToolError, match=r"10301.*not installed in the space"):
        TOOL.run(TOOL.input(space="pms"), ctx(tmp_path))
    with pytest.raises(ToolError, match="--created-since: bad time"):
        TOOL.run(TOOL.input(space="pms", created_since="yesterday"), ctx(tmp_path))


def test_missing_credentials_and_bad_token_type(
    env: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env({})
    monkeypatch.setenv("MEEGLE_TOKEN_TYPE", "2")
    with pytest.raises(ToolError, match="MEEGLE_TOKEN_TYPE must be 0 or 1"):
        TOOL.run(TOOL.input(space="pms"), ctx(tmp_path))
    monkeypatch.delenv("MEEGLE_PLUGIN_SECRET")
    with pytest.raises(ToolError, match="Missing MEEGLE_PLUGIN_SECRET"):
        TOOL.run(TOOL.input(space="pms"), ctx(tmp_path))


def test_env_file_sets_domain_and_token_type(
    env: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    def make_client(
        plugin_id: str, plugin_secret: str, user_key: str, domain: str, token_type: str
    ) -> FakeClient:
        seen.update(domain=domain, token_type=token_type, user_key=user_key)
        return FakeClient({"/projects": ok([]), "/projects/detail": ok({})})

    monkeypatch.setattr(module, "make_client", make_client)
    (tmp_path / "meegle.env").write_text("MEEGLE_DOMAIN=project.feishu.cn\nMEEGLE_TOKEN_TYPE=1\n")
    TOOL.run(TOOL.input(list_spaces=True, env_file="meegle.env"), ctx(tmp_path))
    assert seen == {"domain": "project.feishu.cn", "token_type": "1", "user_key": OWNER}


def test_real_client_sends_token_and_headers() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/authen/plugin_token"):
            return httpx.Response(
                200,
                json={"data": {"token": "p-1", "expire_time": 7200}, "error": {"code": 0, "msg": "success"}},
            )
        if request.url.path.endswith("/all-types"):
            return httpx.Response(200, json=ok([{"type_key": "issue", "name": "Issue"}]))
        return httpx.Response(200, json={"err_code": 10404, "err_msg": "No Project Permission", "err": {}})

    client = mc.MeegleClient(
        "MII_1",
        "s3cret",
        OWNER,
        domain="https://project.feishu.cn/",
        token_type="1",
        transport=httpx.MockTransport(handler),
        sleep=lambda _: None,
    )
    try:
        assert mc.work_item_types(client, "pms") == [{"type_key": "issue", "name": "Issue"}]
        assert mc.work_item_types(client, "pms")  # the token is cached: one token call for two requests
        with pytest.raises(mc.MeegleError, match=r"10404.*no access to that space"):
            client.post("/nope/work_item/filter", {})
    finally:
        client.close()
    token_calls = [r for r in requests if r.url.path.endswith("/authen/plugin_token")]
    assert len(token_calls) == 1 and json.loads(token_calls[0].content) == {
        "plugin_id": "MII_1",
        "plugin_secret": "s3cret",
        "type": 1,
    }
    assert str(token_calls[0].url) == "https://project.feishu.cn/open_api/authen/plugin_token"
    call = requests[1]
    assert str(call.url) == "https://project.feishu.cn/open_api/pms/work_item/all-types"
    assert (call.headers["X-PLUGIN-TOKEN"], call.headers["X-USER-KEY"], call.headers["x-auth-mode"]) == (
        "p-1",
        OWNER,
        "1",
    )
    with pytest.raises(mc.MeegleError, match="must be 0 or 1"):
        mc.MeegleClient("a", "b", "c", token_type="7")


def test_token_errors_use_the_error_object() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {}, "error": {"code": 10022, "msg": "Check Token Failed"}})

    client = mc.MeegleClient(
        "MII_1", "wrong", OWNER, transport=httpx.MockTransport(handler), sleep=lambda _: None
    )
    with pytest.raises(mc.MeegleError, match=r"plugin_token failed: code=10022.*MEEGLE_PLUGIN_SECRET"):
        client.get("/pms/work_item/all-types")
    client.close()


def test_value_helpers() -> None:
    names = {OWNER: "Vince"}
    assert mc.flatten(OWNER, "user", names) == "Vince"
    assert (
        mc.flatten(
            [{"role": "dev", "owners": [OWNER, OTHER]}, {"role": "qa", "owners": None}], "role_owners", names
        )
        == f"dev: Vince, {OTHER}"
    )
    assert mc.flatten(1762840000000, "date", names) == "2025-11-11 13:46:40"
    assert mc.flatten({"id": 3047075, "version": 8}, "work_item_template", names) == "3047075 v8"
    assert mc.flatten([{"label": "P1", "value": "p1"}, {"label": "P2"}], "multi_select", names) == "P1, P2"
    assert mc.flatten("", "text", names) == "" and mc.flatten(None, "x", names) == ""
    assert mc.ms_text(0) == "" and mc.ms_text("n/a") == "n/a"
    assert mc.item_state({"sub_stage": "legacy"}) == "legacy"
