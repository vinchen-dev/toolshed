"""Fetch one Lark approval instance, with its form parsed exactly as finance-ai's processApproval sees it.

Look it up by instance code, or by the form's "RFP No." (searched across the approval code for that
RFP's month, stopping at the first match). Credentials come from the environment or ~/.config/lark.env:
LARK_APP_ID, LARK_APP_SECRET, LARK_APPROVAL_CODE.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field, field_validator, model_validator

from shed import Tool, ToolContext, ToolError
from shed.lib.env import require_env

from .lark_client import LarkClient, LarkError

ENV_FILE = Path.home() / ".config" / "lark.env"
FIELD_RFP_NO = "RFP No."
# The form's RFP No. ("202609-RFP-1919") is a serialNumber widget, unrelated to Lark's serial_number.
RFP_NO_RE = re.compile(r"^(\d{4})(\d{2})-RFP-\d+$", re.IGNORECASE)
BARE_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
PROGRESS_EVERY = 25


class ApprovalClient(Protocol):
    def get_instance(self, instance_code: str) -> dict[str, Any]: ...
    def instance_codes(self, approval_code: str, start_ms: int, end_ms: int) -> Iterator[str]: ...


def parse_date(value: str, *, end_of_day: bool) -> int:
    """Epoch ms. A bare YYYY-MM-DD is a whole local day (an end date includes all of it);
    anything else must be an ISO timestamp."""
    bare = BARE_DATE_RE.match(value)
    if bare:
        year, month, day = (int(g) for g in bare.groups())
        local = datetime(year, month, day).astimezone()
        if end_of_day:
            return int((local + timedelta(days=1)).timestamp() * 1000) - 1
        return int(local.timestamp() * 1000)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"expected YYYY-MM-DD or an ISO timestamp, got '{value}'") from exc
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return int(parsed.timestamp() * 1000)


def month_window(rfp_no: str) -> tuple[int, int]:
    """The RFP's month in UTC, widened a day each side so the tenant's timezone can't push an instance out."""
    match = RFP_NO_RE.match(rfp_no)
    if match is None:
        raise ValueError(f"{rfp_no} is not an RFP No.")
    year, month = int(match.group(1)), int(match.group(2))
    first = datetime(year, month, 1, tzinfo=UTC)
    following = datetime(year + month // 12, month % 12 + 1, 1, tzinfo=UTC)
    return int((first - timedelta(days=1)).timestamp() * 1000), int(
        (following + timedelta(days=1)).timestamp() * 1000
    )


def parse_form(detail: dict[str, Any]) -> list[dict[str, Any]]:
    """Mirrors processApproval: an unparseable form yields no fields rather than an error."""
    form = detail.get("form")
    if isinstance(form, str):
        try:
            form = json.loads(form)
        except ValueError:
            return []
    return [f for f in form if isinstance(f, dict)] if isinstance(form, list) else []


def form_data(fields: list[dict[str, Any]]) -> dict[str, Any]:
    """Same keys and values as finance-ai's parseFormAttachments().formData."""
    return {f.get("name") or f.get("id") or f"field_{f.get('id')}": f.get("value") for f in fields}


class Input(BaseModel):
    target: str = Field(description="Instance code, or an RFP No. such as 202609-RFP-1919")
    start: str | None = Field(
        None,
        description="RFP search only: earliest creation date, YYYY-MM-DD or ISO (default: the RFP's month)",
    )
    end: str | None = Field(
        None, description="RFP search only: latest creation date, inclusive (default: the RFP's month)"
    )
    approval_code: str | None = Field(
        None, description="Approval code to search (default: LARK_APPROVAL_CODE)"
    )
    raw: bool = Field(False, description="Include the untouched instance payload from Lark")
    env_file: str | None = Field(
        None,
        description="Env file holding LARK_APP_ID and LARK_APP_SECRET, for example another project's .env "
        "(default ~/.config/lark.env)",
    )

    @field_validator("start")
    @classmethod
    def _check_start(cls, value: str | None) -> str | None:
        if value is not None:
            parse_date(value, end_of_day=False)
        return value

    @field_validator("end")
    @classmethod
    def _check_end(cls, value: str | None) -> str | None:
        if value is not None:
            parse_date(value, end_of_day=True)
        return value

    @model_validator(mode="after")
    def _check_window(self) -> Input:
        if (
            self.start
            and self.end
            and parse_date(self.start, end_of_day=False) > parse_date(self.end, end_of_day=True)
        ):
            raise ValueError("start is after end")
        return self


class Instance(BaseModel):
    instance_code: str
    serial_number: str | None
    status: str | None
    start_time: str | None
    rfp_no: str | None
    form_data: dict[str, Any]
    raw: dict[str, Any] | None = None


class Lookup(BaseModel):
    instance: Instance
    scanned: int | None = None
    """How many instances were fetched before the RFP No. matched; None for a direct code lookup."""


def describe_instance(detail: dict[str, Any], code: str, *, raw: bool) -> Instance:
    data = form_data(parse_form(detail))
    start_time = detail.get("start_time")
    started = datetime.fromtimestamp(int(start_time) / 1000, UTC).isoformat() if start_time else None
    return Instance(
        instance_code=str(detail.get("instance_code") or code),
        serial_number=_optional_str(detail.get("serial_number")),
        status=_optional_str(detail.get("status")),
        start_time=started,
        rfp_no=_optional_str(data.get(FIELD_RFP_NO)),
        form_data=data,
        raw=detail if raw else None,
    )


def _optional_str(value: object) -> str | None:
    return None if value is None else str(value)


def same_rfp_no(a: object, b: str) -> bool:
    return str(a or "").strip().upper() == b.strip().upper()


def find_by_rfp_no(
    rfp_no: str, params: Input, client: ApprovalClient, approval_codes: list[str], ctx: ToolContext
) -> Lookup:
    if not approval_codes:
        raise ToolError(
            "No approval code to search for an RFP No.; pass --approval-code or set LARK_APPROVAL_CODE."
        )
    month_start, month_end = month_window(rfp_no)
    start_ms = parse_date(params.start, end_of_day=False) if params.start else month_start
    end_ms = parse_date(params.end, end_of_day=True) if params.end else month_end
    scanned = 0
    for approval_code in approval_codes:
        for code in client.instance_codes(approval_code, start_ms, end_ms):
            detail = client.get_instance(code)
            scanned += 1
            if scanned % PROGRESS_EVERY == 0:
                ctx.log(f"... {scanned} instance(s) fetched")
            instance = describe_instance(detail, code, raw=params.raw)
            if same_rfp_no(instance.rfp_no, rfp_no):
                return Lookup(instance=instance, scanned=scanned)
    window = f"{_iso(start_ms)} .. {_iso(end_ms)}"
    raise ToolError(
        f"No instance with RFP No. {rfp_no} under approval code(s) {', '.join(approval_codes)} "
        f"created {window}; {scanned} instance(s) scanned."
    )


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat()


def make_client(app_id: str, app_secret: str) -> LarkClient:
    """Seam for tests, which replace it with a fake."""
    return LarkClient(app_id, app_secret)


def _lookup(params: Input, client: ApprovalClient, approval_code: str, ctx: ToolContext) -> Lookup:
    if RFP_NO_RE.match(params.target):
        return find_by_rfp_no(params.target, params, client, [approval_code] if approval_code else [], ctx)
    detail = client.get_instance(params.target)
    return Lookup(instance=describe_instance(detail, params.target, raw=params.raw))


def run(params: Input, ctx: ToolContext) -> Lookup:
    env_file = ctx.cwd / Path(params.env_file).expanduser() if params.env_file else ENV_FILE
    creds = require_env("LARK_APP_ID", "LARK_APP_SECRET", env_file=env_file)
    approval_code = params.approval_code or os.environ.get("LARK_APPROVAL_CODE", "")
    client = make_client(creds["LARK_APP_ID"], creds["LARK_APP_SECRET"])
    try:
        return _lookup(params, client, approval_code, ctx)
    except LarkError as exc:
        raise ToolError(str(exc)) from exc
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()


def render(result: Lookup) -> str:
    inst = result.instance
    lines = [
        f"{inst.instance_code}  status={inst.status}  serial={inst.serial_number}  started={inst.start_time}",
        f"RFP No.: {inst.rfp_no}"
        + (f"  (matched after scanning {result.scanned})" if result.scanned else ""),
        "",
        json.dumps(inst.form_data, indent=2, ensure_ascii=False),
    ]
    if inst.raw is not None:
        lines += ["", "raw:", json.dumps(inst.raw, indent=2, ensure_ascii=False)]
    return "\n".join(lines)


TOOL = Tool(
    name="lark-approval",
    description=(
        "Fetch one Lark approval instance by instance code or by the form's RFP No., returning its form "
        "data parsed the way finance-ai's processApproval sees it. Read-only; use it to inspect what a "
        "submission contains."
    ),
    input=Input,
    run=run,
    render=render,
    examples=(
        "shed lark-approval --target 7A2B...   # an instance code",
        "shed lark-approval --target 202609-RFP-1919 --json",
        "shed lark-approval --target 202609-RFP-1919 --start 2026-09-10 --end 2026-09-12 --raw",
    ),
)
