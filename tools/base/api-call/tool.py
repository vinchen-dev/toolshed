"""Call an HTTP API with a saved profile (base URL, auth headers): test endpoints without tokens in chat.

Profiles live in ~/.config/api-call.toml (chmod 600), one table per profile:

    [pms-qat]
    base_url = "https://qat.example.com/api"
    headers = { Authorization = "Bearer ...", "x-api-key" = "..." }
    timeout = 30      # optional, seconds
    verify = true     # optional, false skips TLS verification
"""

from __future__ import annotations

import json
import shlex
import time
import tomllib
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlencode

from pydantic import BaseModel, Field

from shed import Tool, ToolContext, ToolError
from shed.lib.errors import UsageError

CONFIG_FILE = Path.home() / ".config" / "api-call.toml"
Method = Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
SENSITIVE_RESPONSE_HEADERS = ("set-cookie", "authorization")


class Input(BaseModel):
    url: str | None = Field(None, description="Absolute URL, or a path joined to the profile's base_url")
    profile: str | None = Field(None, description="Profile name from ~/.config/api-call.toml")
    method: Method = Field("GET", description="HTTP method")
    header: list[str] = Field(
        default_factory=list, description="Extra header 'Name: value'; overrides the profile's"
    )
    query: list[str] = Field(
        default_factory=list, description="Query parameter 'key=value'; repeat for several"
    )
    body: str | None = Field(
        None,
        description=(
            "Request body; '@file' reads a file. Sent as application/json when it parses as JSON and no "
            "Content-Type header is given"
        ),
    )
    timeout: float | None = Field(
        None, gt=0, description="Seconds before giving up (default: profile's, else 30)"
    )
    insecure: bool = Field(False, description="Skip TLS certificate verification")
    max_body: int = Field(20_000, ge=0, description="Characters of a non-JSON response body to keep")
    curl: bool = Field(
        False, description="Print the equivalent curl command (with real header values) instead of sending"
    )
    list_profiles: bool = Field(False, description="List the configured profiles and exit")


class Profile(BaseModel):
    name: str
    base_url: str = ""
    headers: dict[str, str] = {}
    timeout: float | None = None
    verify: bool = True


class ProfileSummary(BaseModel):
    name: str
    base_url: str
    headers: list[str]
    """Header names only; values are never shown."""
    timeout: float | None
    verify: bool


class ProfileList(BaseModel):
    file: str
    profiles: list[ProfileSummary]


class Request(BaseModel):
    method: Method
    url: str
    headers: dict[str, str]
    body: str | None
    timeout: float
    verify: bool


class CurlCommand(BaseModel):
    command: str


class Response(BaseModel):
    method: Method
    url: str
    status: int
    reason: str
    elapsed_ms: int
    headers: dict[str, str]
    content_type: str | None
    body: Any
    """Parsed JSON when the body is JSON, otherwise text cut at --max-body."""
    body_truncated: bool = False


def load_profiles(path: Path = CONFIG_FILE) -> dict[str, Profile]:
    if not path.is_file():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ToolError(f"cannot read {path}: {exc}") from exc
    profiles: dict[str, Profile] = {}
    for name, table in data.items():
        if not isinstance(table, dict):
            raise ToolError(f"{path}: [{name}] must be a table")
        try:
            profiles[name] = Profile(name=name, **table)
        except ValueError as exc:
            raise ToolError(f"{path}: [{name}] is invalid: {exc}") from exc
    return profiles


def parse_headers(items: list[str]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for item in items:
        name, sep, value = item.partition(":")
        if not sep or not name.strip():
            raise UsageError(f"bad --header '{item}', expected 'Name: value'")
        headers[name.strip()] = value.strip()
    return headers


def parse_query(items: list[str]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for item in items:
        key, sep, value = item.partition("=")
        if not sep or not key:
            raise UsageError(f"bad --query '{item}', expected 'key=value'")
        pairs.append((key, value))
    return pairs


def _merge_headers(base: dict[str, str], extra: dict[str, str]) -> dict[str, str]:
    merged = dict(base)
    for name, value in extra.items():
        for existing in [k for k in merged if k.lower() == name.lower()]:
            del merged[existing]
        merged[name] = value
    return merged


def build_request(params: Input, profile: Profile | None, cwd: Path) -> Request:
    if not params.url:
        raise UsageError("--url is required (a path when --profile sets base_url)")
    url = params.url
    if not url.lower().startswith(("http://", "https://")):
        if not profile or not profile.base_url:
            raise UsageError(f"'{url}' is not absolute and no profile with base_url was given")
        url = profile.base_url.rstrip("/") + "/" + url.lstrip("/")
    if params.query:
        url += ("&" if "?" in url else "?") + urlencode(parse_query(params.query))

    headers = _merge_headers(profile.headers if profile else {}, parse_headers(params.header))
    body = params.body
    if body and body.startswith("@"):
        path = cwd / Path(body[1:]).expanduser()
        try:
            body = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ToolError(f"cannot read body file {path}: {exc.strerror or exc}") from exc
    if body and not any(k.lower() == "content-type" for k in headers):
        try:
            json.loads(body)
            headers["Content-Type"] = "application/json"
        except ValueError:
            pass
    timeout = params.timeout or (profile.timeout if profile else None) or 30.0
    verify = (profile.verify if profile else True) and not params.insecure
    return Request(method=params.method, url=url, headers=headers, body=body, timeout=timeout, verify=verify)


def curl_command(req: Request) -> str:
    parts = ["curl", "-sS", "-X", req.method, shlex.quote(req.url)]
    for name, value in req.headers.items():
        parts += ["-H", shlex.quote(f"{name}: {value}")]
    if req.body is not None:
        parts += ["--data-raw", shlex.quote(req.body)]
    if not req.verify:
        parts.append("-k")
    return " ".join(parts)


def make_client(timeout: float, verify: bool) -> Any:
    """Build the HTTP client. Imported lazily so `shed list` stays fast; patched in tests."""
    import httpx

    return httpx.Client(timeout=timeout, verify=verify, follow_redirects=False)


def send(req: Request, max_body: int = 20_000) -> Response:
    import httpx

    started = time.perf_counter()
    try:
        with make_client(req.timeout, req.verify) as client:
            raw = client.request(
                req.method,
                req.url,
                headers=req.headers,
                content=req.body.encode() if req.body is not None else None,
            )
            text = raw.text
    except httpx.HTTPError as exc:
        raise ToolError(f"{req.method} {req.url} failed: {type(exc).__name__}: {exc}") from exc
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    content_type = raw.headers.get("content-type")
    body: Any
    truncated = False
    try:
        body = json.loads(text) if text else ""
    except ValueError:
        body = text
    if isinstance(body, str) and len(body) > max_body:  # non-JSON text is cut; JSON is kept whole
        body, truncated = body[:max_body], True
    headers = {
        k: ("[redacted]" if k.lower() in SENSITIVE_RESPONSE_HEADERS else v) for k, v in raw.headers.items()
    }
    return Response(
        method=req.method,
        url=str(raw.url),
        status=raw.status_code,
        reason=raw.reason_phrase,
        elapsed_ms=elapsed_ms,
        headers=headers,
        content_type=content_type,
        body=body,
        body_truncated=truncated,
    )


def run(params: Input, ctx: ToolContext) -> Response | CurlCommand | ProfileList:
    profiles = load_profiles(CONFIG_FILE)
    if params.list_profiles:
        return ProfileList(
            file=str(CONFIG_FILE),
            profiles=[
                ProfileSummary(
                    name=p.name,
                    base_url=p.base_url,
                    headers=sorted(p.headers),
                    timeout=p.timeout,
                    verify=p.verify,
                )
                for p in profiles.values()
            ],
        )
    profile = None
    if params.profile:
        profile = profiles.get(params.profile)
        if profile is None:
            known = ", ".join(sorted(profiles)) or "none configured"
            raise ToolError(f"unknown profile '{params.profile}' in {CONFIG_FILE} (known: {known})")
    req = build_request(params, profile, ctx.cwd)
    if params.curl:
        return CurlCommand(command=curl_command(req))
    ctx.log(f"# {req.method} {req.url}")
    return send(req, params.max_body)


def render(result: Response | CurlCommand | ProfileList) -> str:
    if isinstance(result, CurlCommand):
        return result.command
    if isinstance(result, ProfileList):
        if not result.profiles:
            return (
                f"No profiles. Create {result.file} with one [name] table per API (see `shed help api-call`)."
            )
        width = max(len(p.name) for p in result.profiles)
        rows = [
            f"{p.name.ljust(width)}  {p.base_url or '-'}  headers: {', '.join(p.headers) or '-'}"
            for p in result.profiles
        ]
        return "\n".join([f"{result.file}", *rows])
    out = [f"HTTP {result.status} {result.reason}  {result.elapsed_ms} ms  {result.method} {result.url}"]
    if result.content_type:
        out.append(f"content-type: {result.content_type}")
    out.append("")
    if isinstance(result.body, str):
        out.append(result.body + ("\n... [cut at --max-body]" if result.body_truncated else ""))
    else:
        out.append(json.dumps(result.body, indent=2, ensure_ascii=False))
    return "\n".join(out)


TOOL = Tool(
    name="api-call",
    description=(
        "Send an HTTP request using a saved profile (base URL and auth headers from ~/.config/api-call.toml) "
        "or a full URL, and return status, headers and the parsed body. --curl prints the equivalent curl "
        "command instead. Use it to test an endpoint on QAT or PROD without pasting tokens into the chat."
    ),
    input=Input,
    run=run,
    render=render,
    examples=(
        "shed api-call --list-profiles",
        "shed api-call --profile pms-qat --url /v1/getBankMaintenanceStatusList --query platform=50",
        'shed api-call --profile finance-qat --method POST --url /approvals/check --body \'{"rfp": "1"}\'',
        "shed api-call --profile pms-qat --url /v1/health --curl",
    ),
)
