"""Minimal Lark Suite (open.larksuite.com) client: tenant token, JSON and file GETs, approval helpers,
and the parsing of links copied from Lark."""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterator
from typing import Any
from urllib.parse import unquote, urlsplit

import httpx

LARK_BASE = "https://open.larksuite.com/open-apis"
TOKEN_MARGIN_S = 300  # refresh this long before Lark's expiry
PAGE_SIZE = 100
# First path segment of a Lark link to the kind of thing it opens.
_LINK_KINDS = {
    "docx": "docx",
    "docs": "doc",
    "wiki": "wiki",
    "file": "file",
    "sheets": "sheet",
    "base": "bitable",
}
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{8,}$")


class LarkError(Exception):
    """A Lark call failed. Never carries the request itself, whose headers hold the bearer token."""


def filename_from(content_disposition: str) -> str:
    """The file name a Content-Disposition header carries: the RFC 5987 form first, else the plain one."""
    match = re.search(r"filename\*=(?:UTF-8|utf-8)''([^;]+)", content_disposition)
    if match:
        return unquote(match.group(1).strip())
    match = re.search(r'filename="?([^";]+)"?', content_disposition)
    return match.group(1).strip() if match else ""


def parse_link(target: str) -> tuple[str, str]:
    """(kind, token) from a link copied out of Lark, or ("token", target) for a bare token.

    Kinds: docx, doc (the legacy editor), wiki, folder, file, sheet, bitable.
    """
    text = target.strip()
    if not re.match(r"https?://", text):
        if not _TOKEN_RE.match(text):
            raise LarkError(f"not a Lark link or token: {text[:60]!r}")
        return "token", text
    parts = urlsplit(text).path.strip("/").split("/")
    if len(parts) >= 2 and parts[0] in _LINK_KINDS and _TOKEN_RE.match(parts[1]):
        return _LINK_KINDS[parts[0]], parts[1]
    if len(parts) >= 3 and parts[:2] == ["drive", "folder"] and _TOKEN_RE.match(parts[2]):
        return "folder", parts[2]
    raise LarkError(f"unrecognised Lark link: {text[:80]}")


class LarkClient:
    def __init__(
        self,
        app_id: str,
        app_secret: str,
        *,
        base: str = LARK_BASE,
        min_gap_s: float = 0.1,
        timeout_s: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._app_id = app_id
        self._app_secret = app_secret
        self._base = base.rstrip("/")
        self._min_gap_s = min_gap_s
        self._sleep = sleep
        self._http = httpx.Client(timeout=timeout_s, transport=transport)
        self._token = ""
        self._token_expires_at = 0.0
        self._last_call_at = 0.0

    def close(self) -> None:
        self._http.close()

    def _paced(self) -> None:
        """Keep at most ~1/min_gap calls per second while scanning a month of instances."""
        wait = self._last_call_at + self._min_gap_s - time.monotonic()
        if wait > 0:
            self._sleep(wait)
        self._last_call_at = time.monotonic()

    @staticmethod
    def _body(response: httpx.Response, what: str) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError as exc:
            raise LarkError(f"{what}: HTTP {response.status_code} with a non-JSON body") from exc
        if not isinstance(body, dict):
            raise LarkError(f"{what}: unexpected body type {type(body).__name__}")
        if body.get("code") != 0:  # Lark reports logical errors in the body, not the HTTP status
            raise LarkError(f"{what} failed: code={body.get('code')} msg={body.get('msg')}")
        return body

    def access_token(self) -> str:
        if self._token and time.monotonic() < self._token_expires_at:
            return self._token
        self._paced()
        response = self._http.post(
            f"{self._base}/auth/v3/tenant_access_token/internal",
            json={"app_id": self._app_id, "app_secret": self._app_secret},
        )
        body = self._body(response, "tenant_access_token")
        self._token = str(body["tenant_access_token"])
        self._token_expires_at = time.monotonic() + max(int(body.get("expire", 7200)) - TOKEN_MARGIN_S, 60)
        return self._token

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET a JSON endpoint with the tenant token; the body is returned once its code is 0."""
        token = self.access_token()
        self._paced()
        response = self._http.get(
            f"{self._base}{path}", params=params, headers={"Authorization": f"Bearer {token}"}
        )
        return self._body(response, f"GET {path}")

    def _binary(self, url: str, headers: dict[str, str], what: str) -> tuple[bytes, str, str]:
        self._paced()
        response = self._http.get(url, headers=headers, follow_redirects=True)
        content_type = response.headers.get("content-type", "")
        if content_type.startswith("application/json"):  # Lark answers a file endpoint's errors as JSON
            self._body(response, what)
            raise LarkError(f"{what}: got JSON instead of a file")
        if response.status_code != 200:
            raise LarkError(f"{what}: HTTP {response.status_code}")
        return response.content, content_type, filename_from(response.headers.get("content-disposition", ""))

    def download(self, path: str) -> tuple[bytes, str, str]:
        """GET a file endpoint with the tenant token: (content, content type, header file name or '')."""
        token = self.access_token()
        return self._binary(f"{self._base}{path}", {"Authorization": f"Bearer {token}"}, f"GET {path}")

    def fetch(self, url: str) -> tuple[bytes, str, str]:
        """GET a plain download link from a form field, without the tenant token."""
        return self._binary(url, {}, "GET attachment link")

    def wiki_node(self, token: str) -> dict[str, Any]:
        """The wiki node behind a /wiki/ link: its obj_type (docx, sheet, file, ...), obj_token and title."""
        node = (self.get("/wiki/v2/spaces/get_node", {"token": token}).get("data") or {}).get("node")
        if not isinstance(node, dict):
            raise LarkError(f"wiki node {token}: no node in response")
        return node

    def get_instance(self, instance_code: str) -> dict[str, Any]:
        """One approval instance. ``form`` in the result is a JSON-encoded string."""
        data = self.get(f"/approval/v4/instances/{instance_code}").get("data")
        if not isinstance(data, dict):
            raise LarkError(f"GET instance {instance_code}: no data in response")
        return data

    def instance_codes(self, approval_code: str, start_ms: int, end_ms: int) -> Iterator[str]:
        """Codes of instances created in [start_ms, end_ms], earliest first, across all pages."""
        page_token = ""
        while True:
            params: dict[str, Any] = {
                "approval_code": approval_code,
                "start_time": start_ms,
                "end_time": end_ms,
                "page_size": PAGE_SIZE,
            }
            if page_token:
                params["page_token"] = page_token
            data = self.get("/approval/v4/instances", params).get("data") or {}
            # Lark documents instance_code_list; older notes say instance_list. Accept either.
            yield from data.get("instance_code_list") or data.get("instance_list") or []
            next_token = str(data.get("page_token") or "")
            if not next_token or next_token == page_token:  # a repeated token would loop forever
                return
            page_token = next_token
