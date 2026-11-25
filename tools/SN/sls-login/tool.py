"""Sign in to Alibaba Cloud SLS through the company single sign-on, with no AccessKey.

A browser window opens on the sign-in page (SLS_LOGIN_URL in ~/.config/aliyun-sls.env, or --url) and the
person signs in there. The SAML response that sign-in posts to Alibaba Cloud is exchanged for temporary
credentials (STS AssumeRoleWithSAML, an anonymous call), which sls-logs and the tools built on it use until
they expire. `renew()` then repeats the exchange with no window, for as long as the single sign-on's own
session lasts.
"""

from __future__ import annotations

import base64
import fcntl
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from pydantic import BaseModel, Field

from shed import Tool, ToolContext, ToolError
from shed.lib.env import load_env_file
from shed.lib.manila import format_manila
from shed.registry import load_tool_module

from .browser import Browser, Capture

STS_ENDPOINT = "sts.ap-southeast-1.aliyuncs.com"
ROLE_ATTRIBUTE = "https://www.aliyun.com/SAML-Role/Attributes/Role"
DURATION_ATTRIBUTE = "https://www.aliyun.com/SAML-Role/Attributes/SessionDuration"
ASSERTION_NS = "{urn:oasis:names:tc:SAML:2.0:assertion}"
RETRY = "run `shed sls-login` again"
STATE_FILE = "state.json"  # the browser's cookies, the single sign-on session among them
RENEWAL_FILE = "renewal.json"  # where and as which role a renewal signs in, and when it may try again
RENEW_TIMEOUT_S = 40
RENEW_RETRY_S = (
    600  # a failed renewal is not repeated sooner, so a dead session costs one try per ten minutes
)

browser: Any = Browser()


def now() -> float:
    """Current epoch seconds. Patched in tests."""
    return time.time()


def state_folder() -> Path:
    """The folder with the sign-in's browser state: beside the other browser profiles, private to the user."""
    root = os.environ.get("SHED_BROWSER_DIR") or Path.home() / ".config" / "shed" / "browser"
    folder = Path(root) / "sls-login"
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    return folder


def read_assertion(saml_response: str) -> tuple[list[tuple[str, str]], int | None]:
    """The (role ARN, provider ARN) pairs a SAML response offers, and the session seconds it allows."""
    try:
        root = ElementTree.fromstring(base64.b64decode(saml_response))
    except (ValueError, ElementTree.ParseError) as exc:
        raise ToolError(f"The sign-in posted a SAML response that cannot be read; {RETRY}") from exc
    values: dict[str, list[str]] = {}
    for attribute in root.iter(f"{ASSERTION_NS}Attribute"):
        texts = [(value.text or "").strip() for value in attribute.iter(f"{ASSERTION_NS}AttributeValue")]
        values.setdefault(attribute.get("Name", ""), []).extend(texts)
    roles: list[tuple[str, str]] = []
    for value in values.get(ROLE_ATTRIBUTE, []):
        arns = [part.strip() for part in value.split(",")]
        role = next((arn for arn in arns if ":role/" in arn), None)
        provider = next((arn for arn in arns if ":saml-provider/" in arn), None)
        if role and provider:
            roles.append((role, provider))
    duration = next((int(text) for text in values.get(DURATION_ATTRIBUTE, []) if text.isdigit()), None)
    return roles, duration


def role_name(role_arn: str) -> str:
    return role_arn.rsplit("/", 1)[-1]


def choose_role(roles: list[tuple[str, str]], wanted: str | None) -> tuple[str, str]:
    """The one role to assume: the only one offered, the one named `wanted`, or the one containing it."""
    if not roles:
        raise ToolError(
            "The sign-in named no Alibaba Cloud role; open the Alibaba Cloud app of your single sign-on, "
            "or pass its link as --url"
        )
    named = [pair for pair in roles if wanted and wanted.lower() == role_name(pair[0]).lower()]
    matches = named or [
        pair for pair in roles if wanted is None or wanted.lower() in role_name(pair[0]).lower()
    ]
    if len(matches) == 1:
        return matches[0]
    offered = ", ".join(role_name(arn) for arn, _ in roles)
    if not matches:
        raise ToolError(f"The sign-in offers no role matching '{wanted}' (offered: {offered})")
    raise ToolError(f"The sign-in offers {len(matches)} roles ({offered}); pass --role with part of one name")


def assume_role(role_arn: str, provider_arn: str, saml_response: str, duration: int | None) -> dict[str, Any]:
    """Exchange the SAML response for temporary credentials. Imported lazily; patched in tests."""
    import httpx

    form = {
        "Action": "AssumeRoleWithSAML",
        "Version": "2015-04-01",
        "Format": "JSON",
        "Timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "RoleArn": role_arn,
        "SAMLProviderArn": provider_arn,
        "SAMLAssertion": saml_response,
    }
    if duration:
        form["DurationSeconds"] = str(duration)
    try:
        response = httpx.post(f"https://{STS_ENDPOINT}/", data=form, timeout=30)
        body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ToolError(f"Could not reach Alibaba Cloud STS at {STS_ENDPOINT}: {exc}; {RETRY}") from exc
    granted = body.get("Credentials") if isinstance(body, dict) else None
    if response.status_code != 200 or not isinstance(granted, dict):
        detail = (
            f"{body.get('Code')}: {body.get('Message')}" if isinstance(body, dict) else response.status_code
        )
        raise ToolError(f"Alibaba Cloud refused the sign-in ({detail})")
    return granted


class Input(BaseModel):
    url: str | None = Field(
        None,
        description=(
            "Sign-in page to open: the single sign-on portal or its Alibaba Cloud sign-on link "
            "(default: the page the last sign-in was posted from, else SLS_LOGIN_URL)"
        ),
    )
    role: str | None = Field(
        None, description="When the sign-in offers several roles: part of the name of the one to use"
    )
    minutes: int | None = Field(
        None,
        ge=15,
        le=720,
        description=(
            "How long the credentials should last (default: what the sign-in allows, else 60); "
            "the role's own limit still applies"
        ),
    )
    timeout: int = Field(
        300, ge=30, le=1800, description="Seconds to wait for the sign-in to finish in the browser window"
    )


class LoginResult(BaseModel):
    role: str
    expires: str
    """When the credentials end, Manila time."""
    minutes: int
    """How long they last from now."""
    renews: bool
    """Whether the SLS tools can renew them with no window, while the single sign-on session lasts."""


def _write_renewal(folder: Path, record: dict[str, Any]) -> None:
    path = folder / RENEWAL_FILE
    path.write_text(json.dumps(record), encoding="utf-8")
    os.chmod(path, 0o600)


def _read_renewal(folder: Path) -> dict[str, Any]:
    """What the last sign-in left for a renewal. Raises ToolError when there is nothing to renew from."""
    try:
        record = json.loads((folder / RENEWAL_FILE).read_text(encoding="utf-8"))
        if not isinstance(record, dict) or not str(record.get("address", "")).startswith("https://"):
            raise ValueError("no sign-on address")
    except (OSError, ValueError) as exc:
        raise ToolError("there is no earlier sign-in to renew from") from exc
    return record


def _remembered_address(folder: Path) -> str:
    """The sign-on page the last sign-in was posted from, or '' when none is remembered."""
    try:
        return str(_read_renewal(folder)["address"])
    except ToolError:
        return ""


def _finish(captured: Capture, role: str | None, minutes: int | None, folder: Path) -> LoginResult:
    """Exchange a captured SAML response for credentials, save them, and note how to renew them."""
    sls = load_tool_module("sls-logs")
    roles, allowed = read_assertion(captured.saml_response)
    role_arn, provider_arn = choose_role(roles, role)
    duration = minutes * 60 if minutes else allowed
    granted = assume_role(role_arn, provider_arn, captured.saml_response, duration)
    try:
        creds = sls.Credentials(granted["AccessKeyId"], granted["AccessKeySecret"], granted["SecurityToken"])
        expires = int(datetime.fromisoformat(granted["Expiration"]).timestamp())
    except (KeyError, ValueError, TypeError) as exc:
        raise ToolError(f"Alibaba Cloud answered the sign-in without usable credentials; {RETRY}") from exc
    sls.save_session(creds, expires)
    renews = captured.posted_from.startswith("https://")
    if renews:
        _write_renewal(
            folder,
            {
                "address": captured.posted_from,
                "role": role_name(role_arn),
                "user_agent": captured.user_agent,
                "retry_after": 0,
            },
        )
    else:
        (folder / RENEWAL_FILE).unlink(missing_ok=True)
    return LoginResult(
        role=role_name(role_arn),
        expires=format_manila(expires),
        minutes=int((expires - now()) // 60),
        renews=renews,
    )


def renew() -> None:
    """Sign in again with nobody at the screen, from the single sign-on session the last sign-in left.

    Raises ToolError saying why when it cannot. A failed try is not repeated for RENEW_RETRY_S seconds."""
    sls = load_tool_module("sls-logs")
    folder = state_folder()
    with open(folder / "renewal.lock", "w", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # one renewal at a time; a process that waited finds it done
        try:
            sls.load_session()
            return
        except ToolError:
            pass  # still expired: this process is the one to renew it
        record = _read_renewal(folder)
        if record.get("retry_after", 0) > now():
            again = format_manila(record["retry_after"])[11:16]
            raise ToolError(f"the last try failed, and the next one is not before {again}")
        try:
            captured = browser.capture(
                record["address"],
                folder / STATE_FILE,
                RENEW_TIMEOUT_S,
                headed=False,
                user_agent=record.get("user_agent"),
            )
            if captured is None:
                raise ToolError("the single sign-on asked for a person to sign in")
            _finish(captured, record.get("role"), None, folder)
        except ToolError:
            _write_renewal(folder, {**record, "retry_after": now() + RENEW_RETRY_S})
            raise


def run(params: Input, ctx: ToolContext) -> LoginResult:
    sls = load_tool_module("sls-logs")
    load_env_file(sls.ENV_FILE)
    folder = state_folder()
    remembered = "" if params.url else _remembered_address(folder)
    url = params.url or remembered or os.environ.get("SLS_LOGIN_URL", "")
    if not url:
        raise ToolError(
            "No sign-in page: pass --url with the address of your single sign-on portal, "
            f"or add SLS_LOGIN_URL=<that address> to {sls.ENV_FILE}"
        )
    if not url.startswith("https://"):
        raise ToolError(f"The sign-in page must be an https:// address, not '{url}'")

    ctx.log(
        "# a browser window is opening on the page that signed you in last time: sign in there"
        if remembered
        else "# a browser window is opening: sign in there, then open Alibaba Cloud from the portal "
        "(Permission, then Log On)"
    )
    captured = browser.capture(url, folder / STATE_FILE, params.timeout, headed=True)
    if captured is None:
        raise ToolError(
            f"No sign-in arrived within {params.timeout}s (the window was closed, or Alibaba Cloud was not "
            f"opened from the portal: Permission, then Log On); {RETRY}"
        )
    return _finish(captured, params.role, params.minutes, folder)


def render(result: LoginResult) -> str:
    after = (
        "They renew by themselves while the single sign-on keeps you signed in; "
        "run `shed sls-login` again when a tool says the sign-in expired."
        if result.renews
        else "Run `shed sls-login` again after that."
    )
    return (
        f"Signed in as {result.role}. The SLS tools work until {result.expires} Manila time "
        f"({result.minutes} min). {after}"
    )


TOOL = Tool(
    name="sls-login",
    description=(
        "Sign in to Alibaba Cloud SLS through the company single sign-on: opens a browser window where the "
        "person signs in, then saves temporary credentials that sls-logs, error-trace, deployed-version and "
        "bdo-incidents use and renew by themselves while the single sign-on session lasts. Run it when one "
        "of those says the SLS sign-in is missing or expired. It needs a person at the screen, so never "
        "schedule it."
    ),
    input=Input,
    run=run,
    render=render,
    examples=(
        "shed sls-login",
        "shed sls-login --url https://example.aliyunidaas.com/ --minutes 240",
        "shed sls-login --role readonly",
    ),
    version="0.1.0",
)
