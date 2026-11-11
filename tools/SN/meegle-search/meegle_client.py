"""Minimal Meegle Open API client (read-only): plugin token, paced JSON calls, the query endpoints the
meegle tools need, and helpers that turn Meegle's values into text.

Every call carries X-PLUGIN-TOKEN, X-USER-KEY and x-auth-mode: 1, so only data the user may read comes back.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from typing import Any

import httpx

from shed.lib.manila import format_manila

DEFAULT_DOMAIN = "project.larksuite.com"
TOKEN_MARGIN_S = 300  # refresh this long before Meegle's expiry
MIN_GAP_S = 0.1  # Meegle allows 15 QPS per token and interface
PAGE_SIZE = 200
ACROSS_PAGE_SIZE = 50
QUERY_BATCH = 50
USER_BATCH = 100
SPACE_BATCH = 100
TOKEN_TYPES = ("0", "1")

HINTS: dict[int, str] = {
    10001: "no permission: the plug-in is not installed in that space, or the user may not read it",
    10021: "no plug-in token was sent",
    10022: "the plug-in token failed its check (MEEGLE_PLUGIN_ID / MEEGLE_PLUGIN_SECRET)",
    10211: "the plug-in token is invalid, or X-USER-KEY is missing (MEEGLE_USER_KEY)",
    10301: (
        "the plug-in lacks this API permission, was not updated after its permissions changed, "
        "is not installed in the space, or the space key is wrong"
    ),
    10302: "the user in MEEGLE_USER_KEY has left the company",
    10404: "the user in MEEGLE_USER_KEY has no access to that space",
    10429: "rate limit: more than 15 calls per second to one interface; retry in a moment",
    20002: "page size over 200",
    20013: "the change-log time range may span at most 7 days",
    30006: "user not found: MEEGLE_USER_KEY is wrong",
    1000051135: "work item not found",
    1000051256: "no permission to read this work item's comments",
}


class MeegleError(Exception):
    """A Meegle call failed. Never carries the request, whose headers hold the plug-in token."""

    def __init__(self, message: str, code: int = -1) -> None:
        super().__init__(message)
        self.code = code


def describe_error(code: int, msg: str) -> str:
    hint = HINTS.get(code)
    return f"code={code} msg={msg}" + (f" ({hint})" if hint else "")


class MeegleClient:
    def __init__(
        self,
        plugin_id: str,
        plugin_secret: str,
        user_key: str,
        *,
        domain: str = DEFAULT_DOMAIN,
        token_type: str = "0",
        min_gap_s: float = MIN_GAP_S,
        timeout_s: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if token_type not in TOKEN_TYPES:
            raise MeegleError(f"MEEGLE_TOKEN_TYPE must be 0 or 1, not {token_type!r}")
        self._plugin_id = plugin_id
        self._plugin_secret = plugin_secret
        self.user_key = user_key
        self.domain = domain.strip().removeprefix("https://").removeprefix("http://").strip("/")
        self._base = f"https://{self.domain}/open_api"
        self._token_type = int(token_type)
        self._min_gap_s = min_gap_s
        self._sleep = sleep
        self._http = httpx.Client(timeout=timeout_s, transport=transport)
        self._token = ""
        self._token_expires_at = 0.0
        self._last_call_at = 0.0

    def close(self) -> None:
        self._http.close()

    def _paced(self) -> None:
        wait = self._last_call_at + self._min_gap_s - time.monotonic()
        if wait > 0:
            self._sleep(wait)
        self._last_call_at = time.monotonic()

    @staticmethod
    def _json(response: httpx.Response, what: str) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError as exc:
            raise MeegleError(f"{what}: HTTP {response.status_code} with a non-JSON body") from exc
        if not isinstance(body, dict):
            raise MeegleError(f"{what}: unexpected body type {type(body).__name__}")
        return body

    def access_token(self) -> str:
        if self._token and time.monotonic() < self._token_expires_at:
            return self._token
        self._paced()
        response = self._http.post(
            f"{self._base}/authen/plugin_token",
            json={
                "plugin_id": self._plugin_id,
                "plugin_secret": self._plugin_secret,
                "type": self._token_type,
            },
            headers={"Content-Type": "application/json"},
        )
        body = self._json(response, "plugin_token")
        error = body.get("error") or {}
        code = int(error.get("code") or 0) if isinstance(error, dict) else 0
        if code != 0:
            msg = str(error.get("msg") or "")
            raise MeegleError(f"plugin_token failed: {describe_error(code, msg)}", code)
        data = body.get("data") or {}
        token = str(data.get("token") or "")
        if not token:
            raise MeegleError("plugin_token: no token in the response")
        self._token = token
        self._token_expires_at = time.monotonic() + max(
            int(data.get("expire_time") or 7200) - TOKEN_MARGIN_S, 60
        )
        return token

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "X-PLUGIN-TOKEN": self.access_token(),
            "X-USER-KEY": self.user_key,
            "x-auth-mode": "1",
        }

    def _body(self, response: httpx.Response, what: str) -> dict[str, Any]:
        body = self._json(response, what)
        code = int(body.get("err_code") or 0)
        if code != 0:
            msg = str(body.get("err_msg") or (body.get("err") or {}).get("msg") or "")
            raise MeegleError(f"{what} failed: {describe_error(code, msg)}", code)
        return body

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = self._headers()
        self._paced()
        return self._body(
            self._http.get(f"{self._base}{path}", params=params, headers=headers), f"GET {path}"
        )

    def post(self, path: str, json: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = self._headers()
        self._paced()
        return self._body(
            self._http.post(f"{self._base}{path}", json=json or {}, headers=headers), f"POST {path}"
        )


# --- endpoint helpers (each takes any object with get/post, so tests can pass a fake) ----------------


def space_keys(client: Any) -> list[str]:
    """project_keys of the spaces the plug-in is installed in and the user can see, last visited first."""
    data = (
        client.post("/projects", {"user_key": client.user_key, "order": ["-last_visited"]}).get("data") or []
    )
    return [str(k) for k in data]


def space_details(client: Any, keys: Iterable[str]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    batch: list[str] = []
    for key in [*keys, ""]:
        if key:
            batch.append(key)
        if batch and (len(batch) == SPACE_BATCH or not key):
            data = client.post("/projects/detail", {"user_key": client.user_key, "project_keys": batch}).get(
                "data"
            )
            if isinstance(data, dict):
                out.update({str(k): v for k, v in data.items() if isinstance(v, dict)})
            batch = []
    return out


def work_item_types(client: Any, space: str) -> list[dict[str, Any]]:
    data = client.get(f"/{space}/work_item/all-types").get("data") or []
    return [t for t in data if isinstance(t, dict)]


def filter_page(
    client: Any, space: str | None, body: dict[str, Any]
) -> tuple[list[dict[str, Any]], int | None]:
    """One page of a work item search: (items, total or None); no space means the across-space endpoint."""
    path = f"/{space}/work_item/filter" if space else "/work_items/filter_across_project"
    result = client.post(path, body)
    items = [i for i in (result.get("data") or []) if isinstance(i, dict)]
    pagination = result.get("pagination") or {}
    total = pagination.get("total") if isinstance(pagination, dict) else None
    return items, (int(total) if isinstance(total, int | float | str) and str(total).isdigit() else None)


def query_items(
    client: Any, space: str, type_key: str, ids: list[int], fields: list[str] | None = None
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for start in range(0, len(ids), QUERY_BATCH):
        body: dict[str, Any] = {"work_item_ids": ids[start : start + QUERY_BATCH]}
        if fields:
            body["fields"] = fields
        data = client.post(f"/{space}/work_item/{type_key}/query", body).get("data") or []
        out.extend(i for i in data if isinstance(i, dict))
    return out


def comments(client: Any, space: str, type_key: str, item_id: int, limit: int) -> list[dict[str, Any]]:
    """All comments of a work item, oldest first as Meegle returns them, up to ``limit``."""
    out: list[dict[str, Any]] = []
    page = 1
    while len(out) < limit:
        size = min(PAGE_SIZE, limit - len(out))
        result = client.get(
            f"/{space}/work_item/{type_key}/{item_id}/comments", {"page_size": size, "page_num": page}
        )
        data = [c for c in (result.get("data") or []) if isinstance(c, dict)]
        out.extend(data)
        if len(data) < size:
            break
        page += 1
    return out[:limit]


def users(client: Any, keys: Iterable[str]) -> dict[str, str]:
    """user_key -> display name (English name first) for every key Meegle knows; unknown keys are absent."""
    wanted = sorted({k for k in keys if k})
    names: dict[str, str] = {}
    for start in range(0, len(wanted), USER_BATCH):
        data = client.post("/user/query", {"user_keys": wanted[start : start + USER_BATCH]}).get("data") or []
        for user in data:
            if not isinstance(user, dict):
                continue
            key = str(user.get("user_key") or "")
            raw_name = user.get("name")
            name: dict[str, Any] = raw_name if isinstance(raw_name, dict) else {}
            label = str(
                name.get("en_us") or user.get("name_en") or name.get("default") or user.get("name_cn") or ""
            )
            if key and label:
                names[key] = label
    return names


def change_log(client: Any, project_key: str, item_id: int, limit: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    start_from = ""
    while len(out) < limit:
        body: dict[str, Any] = {
            "project_key": project_key,
            "work_item_ids": [item_id],
            "page_size": PAGE_SIZE,
        }
        if start_from:
            body["start_from"] = start_from
        data = client.post("/op_record/work_item/list", body).get("data") or {}
        records = [r for r in (data.get("op_records") or []) if isinstance(r, dict)]
        out.extend(records)
        next_from = str(data.get("start_from") or "")
        if not data.get("has_more") or not next_from or next_from == start_from or not records:
            break
        start_from = next_from
    return out[:limit]


# --- value helpers ---------------------------------------------------------------------------------


def ms_text(value: Any) -> str:
    """A Meegle millisecond timestamp as Manila wall-clock text; anything else comes back unchanged."""
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        return "" if value in (None, 0) else str(value)
    return format_manila(value / 1000)


def item_state(item: dict[str, Any]) -> str:
    status = item.get("work_item_status")
    if isinstance(status, dict) and status.get("state_key"):
        return str(status["state_key"])
    return str(item.get("sub_stage") or "")


def item_field(item: dict[str, Any], key: str) -> Any:
    for field in item.get("fields") or []:
        if isinstance(field, dict) and field.get("field_key") == key:
            return field.get("field_value")
    return None


def user_keys_in(value: Any) -> set[str]:
    """Every string that looks like a user_key inside a user-typed value (plain, list, or role owners)."""
    if isinstance(value, str):
        return {value} if value else set()
    if isinstance(value, list):
        out: set[str] = set()
        for v in value:
            out |= user_keys_in(v)
        return out
    if isinstance(value, dict):
        return user_keys_in(value.get("owners")) | user_keys_in(value.get("user_key"))
    return set()


def flatten(value: Any, type_key: str, names: dict[str, str]) -> str:
    """Field values as text: users by name, dates in Manila time, options by label, lists joined."""
    if value is None or value == "" or value == []:
        return ""
    if type_key in ("user", "multi_user", "user_list") or type_key.endswith("_user"):
        keys = user_keys_in(value)
        return ", ".join(names.get(k, k) for k in sorted(keys))
    if type_key == "role_owners" and isinstance(value, list):
        parts = []
        for entry in value:
            if isinstance(entry, dict) and entry.get("owners"):
                owners = ", ".join(names.get(str(k), str(k)) for k in entry["owners"])
                parts.append(f"{entry.get('role', '')}: {owners}")
        return "; ".join(parts)
    if (
        type_key in ("date", "datetime", "time")
        and isinstance(value, int | float)
        and not isinstance(value, bool)
    ):
        return ms_text(value)
    if type_key == "work_item_template" and isinstance(value, dict):
        return f"{value.get('id', '')} v{value.get('version', '')}".strip()
    if isinstance(value, dict):
        for key in ("label", "name", "text", "value", "id"):
            if value.get(key) not in (None, ""):
                return str(value[key])
        return ", ".join(f"{k}={v}" for k, v in value.items())
    if isinstance(value, list):
        return ", ".join(s for s in (flatten(v, "", names) for v in value) if s)
    return str(value)
