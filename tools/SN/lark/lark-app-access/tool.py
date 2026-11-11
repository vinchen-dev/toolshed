"""Report what the Lark app behind ~/.config/lark.env can reach.

Fetches a tenant token, reads the app's own name and status, then makes one cheap read per capability
that was given a target (an approval code, a docx, a Drive folder, a wiki page, a Base) and classifies
each answer as ok, scope-missing or error. Nothing is written. Lark has no account-free endpoint that
lists granted scopes, so probing is how a missing scope shows up before a real tool fails on it.
"""

from __future__ import annotations

import importlib
import os
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from shed import Tool, ToolContext, ToolError
from shed.lib.env import require_env
from shed.registry import load_tool_module

Status = Literal["ok", "scope-missing", "error", "not-probed"]
PERMISSION_CODES = {99991672, 99991679}
_CODE_RE = re.compile(r"code=(\d+)")
# Scope names are hints taken from Lark's docs and were not verified against a live app.
SCOPE_HINTS = {
    "approval": "approval:approval:readonly",
    "docx": "docx:document:readonly",
    "drive": "drive:drive:readonly",
    "wiki": "wiki:wiki:readonly",
    "bitable": "bitable:app:readonly",
}
CAPABILITIES = tuple(SCOPE_HINTS)
_APP_STATUS = {0: "disabled", 1: "enabled"}


class Input(BaseModel):
    approval: str | None = Field(
        None, description="Approval code to probe (default: LARK_APPROVAL_CODE from the env file, if set)"
    )
    doc: str | None = Field(None, description="docx link or token to probe document read access")
    folder: str | None = Field(None, description="Drive folder link or token to probe Drive read access")
    wiki: str | None = Field(None, description="Wiki link or token to probe wiki read access")
    bitable: str | None = Field(None, description="Base link or app token to probe Bitable read access")
    env_file: str | None = Field(
        None,
        description="Env file holding LARK_APP_ID and LARK_APP_SECRET, for example another project's .env "
        "(default ~/.config/lark.env)",
    )


class Probe(BaseModel):
    capability: str
    target: str | None
    status: Status
    scope_hint: str
    detail: str


class Report(BaseModel):
    app_id: str
    app_name: str | None
    app_status: str | None
    token_ok: bool
    probes: list[Probe]
    summary: str


def _approval() -> Any:
    """The lark-approval tool module: its client factory and env file are reused here."""
    return load_tool_module("lark-approval")


def _client_module() -> Any:
    return importlib.import_module(f"{_approval().__package__}.lark_client")


def make_client(app_id: str, app_secret: str) -> Any:
    return _approval().make_client(app_id, app_secret)


def classify(error: Exception) -> tuple[Status, str]:
    """scope-missing for Lark's permission codes or a permission/scope message, else error."""
    text = str(error)
    match = _CODE_RE.search(text)
    code = int(match.group(1)) if match else None
    if code in PERMISSION_CODES or re.search(r"permission|scope", text, re.IGNORECASE):
        return "scope-missing", text
    return "error", text


def _token_for(capability: str, target: str, expected: tuple[str, ...]) -> str:
    """The token inside a link, or the bare token; a link of another kind is refused."""
    kind, token = _client_module().parse_link(target)
    if kind not in ("token", *expected):
        raise ToolError(f"--{capability} target is a {kind} link, expected {' or '.join(expected)}")
    return token


def _request(client: Any, capability: str, target: str) -> None:
    if capability == "approval":
        client.get(f"/approval/v4/approvals/{target}")
    elif capability == "docx":
        client.get(f"/docx/v1/documents/{_token_for('doc', target, ('docx',))}")
    elif capability == "drive":
        client.get(
            "/drive/v1/files", {"folder_token": _token_for("folder", target, ("folder",)), "page_size": 1}
        )
    elif capability == "wiki":
        client.get("/wiki/v2/spaces/get_node", {"token": _token_for("wiki", target, ("wiki",))})
    else:
        app = _token_for("bitable", target, ("bitable",))
        client.get(f"/bitable/v1/apps/{app}/tables", {"page_size": 1})


def probe(client: Any, capability: str, target: str | None) -> Probe:
    hint = SCOPE_HINTS[capability]
    if not target:
        return Probe(
            capability=capability, target=None, status="not-probed", scope_hint=hint, detail="no target given"
        )
    try:
        _request(client, capability, target)
    except _client_module().LarkError as exc:
        status, detail = classify(exc)
        return Probe(capability=capability, target=target, status=status, scope_hint=hint, detail=detail)
    return Probe(capability=capability, target=target, status="ok", scope_hint=hint, detail="")


def app_info(client: Any, app_id: str) -> tuple[str | None, str | None]:
    """(name, status) of the app itself; a refusal here is reported, never fatal."""
    try:
        app = (client.get(f"/application/v6/applications/{app_id}", {"lang": "en_us"}).get("data") or {}).get(
            "app"
        ) or {}
    except _client_module().LarkError as exc:
        status, detail = classify(exc)
        return None, f"unknown ({'scope missing' if status == 'scope-missing' else detail})"
    raw_status = app.get("status")
    status = _APP_STATUS.get(raw_status, str(raw_status)) if raw_status is not None else None
    return (str(app["app_name"]) if app.get("app_name") else None), status


def _summary(token_ok: bool, probes: list[Probe]) -> str:
    if not token_ok:
        return "tenant token refused; check LARK_APP_ID and LARK_APP_SECRET"
    counts = {
        status: sum(1 for p in probes if p.status == status) for status in ("ok", "scope-missing", "error")
    }
    skipped = sum(1 for p in probes if p.status == "not-probed")
    return (
        f"{counts['ok']} ok, {counts['scope-missing']} scope missing, {counts['error']} errors, "
        f"{skipped} not probed"
    )


def run(params: Input, ctx: ToolContext) -> Report:
    env_file = ctx.cwd / Path(params.env_file).expanduser() if params.env_file else _approval().ENV_FILE
    creds = require_env("LARK_APP_ID", "LARK_APP_SECRET", env_file=env_file)
    app_id = creds["LARK_APP_ID"]
    targets = {
        "approval": params.approval or os.environ.get("LARK_APPROVAL_CODE") or None,
        "docx": params.doc,
        "drive": params.folder,
        "wiki": params.wiki,
        "bitable": params.bitable,
    }
    client = make_client(app_id, creds["LARK_APP_SECRET"])
    try:
        try:
            client.access_token()
        except _client_module().LarkError as exc:
            ctx.log(f"# tenant token failed: {exc}")
            probes = [
                Probe(
                    capability=c,
                    target=targets[c],
                    status="not-probed",
                    scope_hint=SCOPE_HINTS[c],
                    detail=str(exc),
                )
                for c in CAPABILITIES
            ]
            return Report(
                app_id=app_id,
                app_name=None,
                app_status=None,
                token_ok=False,
                probes=probes,
                summary=_summary(False, probes),
            )
        name, status = app_info(client, app_id)
        probes = [probe(client, capability, targets[capability]) for capability in CAPABILITIES]
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()
    summary = _summary(True, probes)
    ctx.log(f"# {app_id}: {summary}")
    return Report(
        app_id=app_id, app_name=name, app_status=status, token_ok=True, probes=probes, summary=summary
    )


def render(report: Report) -> str:
    name = f' "{report.app_name}"' if report.app_name else ""
    status = f" ({report.app_status})" if report.app_status else ""
    lines = [f"app {report.app_id}{name}{status}: token {'ok' if report.token_ok else 'FAILED'}"]
    for p in report.probes:
        line = f"{p.capability:<9} {p.status:<14} {p.target or '-'}"
        if p.status == "scope-missing":
            line += f"  needs {p.scope_hint}? {p.detail}"
        elif p.detail:
            line += f"  {p.detail}"
        lines.append(line)
    lines.append(report.summary)
    return "\n".join(lines) + "\n"


TOOL = Tool(
    name="lark-app-access",
    description=(
        "Check what the Lark app can reach: tenant token, app name and status, and one read probe per "
        "capability given a target (approval code, docx, Drive folder, wiki page, Base), each classified as "
        "ok, scope-missing or error. Use it before a Lark tool fails with a permission error, or after "
        "adding scopes to the app. Read-only."
    ),
    input=Input,
    run=run,
    render=render,
    examples=(
        "shed lark-app-access",
        "shed lark-app-access --doc https://example.larksuite.com/docx/Abc123DefGhi --bitable BasTok12345",
        "shed lark-app-access --folder FldTok12345 --env-file ~/work/finance-ai/.env --json",
    ),
    version="0.1.0",
)
