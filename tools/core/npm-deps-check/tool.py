"""Outdated and vulnerable dependencies of a Node project, through npm or pnpm.

The package manager is picked from the lockfile: pnpm-lock.yaml means pnpm, anything else means npm (a
yarn.lock project gets npm's read-only checks and a note). Both managers exit 1 when they find something,
so a non-zero exit with JSON on stdout is a finding, not a failure.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from shed import Tool, ToolContext, ToolError, sh

Manager = Literal["npm", "pnpm"]
Severity = Literal["critical", "high", "moderate", "low", "info"]
SEVERITIES: tuple[Severity, ...] = ("critical", "high", "moderate", "low", "info")


class Input(BaseModel):
    path: str = Field(".", description="The Node project folder (where package.json is)")
    outdated: bool = Field(True, description="Check for packages behind their wanted or latest version")
    audit: bool = Field(True, description="Run the vulnerability audit")
    production: bool = Field(False, description="Production dependencies only (npm --omit=dev, pnpm --prod)")
    limit: int = Field(50, ge=1, description="Rows kept per list; the totals are reported either way")


class Outdated(BaseModel):
    name: str
    current: str | None
    wanted: str | None
    latest: str | None
    type: str | None
    """dependencies or devDependencies when the manager says."""
    location: str | None


class Vulnerability(BaseModel):
    name: str
    severity: Severity
    range: str
    fix_available: bool | Literal["major"]
    """True when a fix is a normal update, "major" when it needs a semver-major bump."""
    via: list[str]


class Counts(BaseModel):
    critical: int = 0
    high: int = 0
    moderate: int = 0
    low: int = 0
    info: int = 0


class DepsReport(BaseModel):
    repo: str
    manager: Manager
    lockfile: str | None
    outdated: list[Outdated]
    outdated_total: int
    outdated_truncated: bool
    vulnerabilities: list[Vulnerability]
    vulnerabilities_total: int
    vulnerabilities_truncated: bool
    counts: Counts
    audit_available: bool
    notes: list[str]


def detect_manager(root: Path) -> tuple[Manager, str | None, list[str]]:
    """(manager, lockfile name, notes) from the lockfiles present."""
    if (root / "pnpm-lock.yaml").is_file():
        return "pnpm", "pnpm-lock.yaml", []
    if (root / "package-lock.json").is_file():
        return "npm", "package-lock.json", []
    if (root / "yarn.lock").is_file():
        return (
            "npm",
            "yarn.lock",
            ["yarn.lock project: npm's read-only checks are used; npm audit needs package-lock.json"],
        )
    return "npm", None, ["no lockfile found; npm audit needs package-lock.json"]


def _json_output(cmd: str, args: list[str], root: Path) -> tuple[Any, str | None]:
    """(parsed JSON or None, failure text). Exit 1 with JSON is a finding, not a failure."""
    result = sh(cmd, args, cwd=root, allow_failure=True, timeout=600)
    text = result.stdout.strip()
    if text:
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if isinstance(data, dict) and isinstance(data.get("error"), dict):  # npm reports failures as JSON too
            error = data["error"]
            code = f" ({error['code']})" if error.get("code") else ""
            return None, f"{error.get('summary') or error.get('detail') or 'failed'}{code}"
        if data is not None:
            return data, None
    if result.code == 0:
        return {}, None
    detail = (result.stderr.strip() or text or f"exit {result.code}").splitlines()
    return None, " ".join(detail[:3])[:300]


def parse_outdated(manager: Manager, data: Any) -> list[Outdated]:
    if not isinstance(data, dict):
        return []
    rows: list[Outdated] = []
    for name, info in sorted(data.items()):
        if isinstance(info, list):  # pnpm lists one entry per occurrence
            info = info[0] if info else {}
        if not isinstance(info, dict):
            continue
        kind = info.get("type") or info.get("dependencyType")
        rows.append(
            Outdated(
                name=str(name),
                current=_text(info.get("current")),
                wanted=_text(info.get("wanted")),
                latest=_text(info.get("latest")),
                type=_text(kind),
                location=_text(info.get("location")),
            )
        )
    return rows


def _text(value: Any) -> str | None:
    return None if value is None else str(value)


def _severity(value: Any) -> Severity:
    text = str(value or "info").lower()
    return text if text in SEVERITIES else "info"  # type: ignore[return-value]


def parse_audit(manager: Manager, data: Any) -> tuple[list[Vulnerability], Counts, str | None]:
    """(vulnerabilities, counts, error text) from npm 7+ or pnpm's npm-v6 style audit JSON."""
    if not isinstance(data, dict):
        return [], Counts(), None
    rows: list[Vulnerability] = []
    if isinstance(data.get("vulnerabilities"), dict):  # npm 7+
        for name, info in data["vulnerabilities"].items():
            if not isinstance(info, dict):
                continue
            fix = info.get("fixAvailable")
            fix_available: bool | Literal["major"] = (
                "major" if isinstance(fix, dict) and fix.get("isSemVerMajor") else bool(fix)
            )
            via = [
                str(v.get("title") or v.get("name")) if isinstance(v, dict) else str(v)
                for v in (info.get("via") or [])
            ]
            rows.append(
                Vulnerability(
                    name=str(name),
                    severity=_severity(info.get("severity")),
                    range=str(info.get("range") or ""),
                    fix_available=fix_available,
                    via=via,
                )
            )
    elif isinstance(data.get("advisories"), dict):  # pnpm (npm v6 shape)
        for info in data["advisories"].values():
            if not isinstance(info, dict):
                continue
            patched = str(info.get("patched_versions") or "")
            rows.append(
                Vulnerability(
                    name=str(info.get("module_name") or ""),
                    severity=_severity(info.get("severity")),
                    range=str(info.get("vulnerable_versions") or ""),
                    fix_available=bool(patched) and patched != "<0.0.0",
                    via=[str(info.get("title") or "")],
                )
            )
    counts = Counts()
    meta = (
        (data.get("metadata") or {}).get("vulnerabilities")
        if isinstance(data.get("metadata"), dict)
        else None
    )
    if isinstance(meta, dict):
        counts = Counts(**{s: int(meta.get(s) or 0) for s in SEVERITIES})
    else:
        for row in rows:
            setattr(counts, row.severity, getattr(counts, row.severity) + 1)
    rows.sort(key=lambda r: (SEVERITIES.index(r.severity), r.name))
    return rows, counts, None


def run(params: Input, ctx: ToolContext) -> DepsReport:
    root = (ctx.cwd / Path(params.path).expanduser()).resolve()
    if not (root / "package.json").is_file():
        raise ToolError(f"no package.json in {root}")
    manager, lockfile, notes = detect_manager(root)
    ctx.log(f"# {root}: {manager} ({lockfile or 'no lockfile'})")

    outdated: list[Outdated] = []
    if params.outdated:
        args = ["outdated", "--json"] if manager == "npm" else ["outdated", "--format", "json"]
        if params.production:
            args.append("--omit=dev" if manager == "npm" else "--prod")
        data, failure = _json_output(manager, args, root)
        if failure:
            raise ToolError(f"{manager} outdated failed: {failure}")
        outdated = parse_outdated(manager, data)
        ctx.log(f"# {len(outdated)} outdated")

    vulnerabilities: list[Vulnerability] = []
    counts = Counts()
    audit_available = False
    if params.audit:
        args = ["audit", "--json"]
        if params.production:
            args.append("--omit=dev" if manager == "npm" else "--prod")
        data, failure = _json_output(manager, args, root)
        if failure:
            notes.append(f"audit unavailable: {failure}")
        else:
            vulnerabilities, counts, error = parse_audit(manager, data)
            if error:
                notes.append(f"audit unavailable: {error}")
            else:
                audit_available = True
                ctx.log(f"# {len(vulnerabilities)} vulnerable packages")

    return DepsReport(
        repo=str(root),
        manager=manager,
        lockfile=lockfile,
        outdated=outdated[: params.limit],
        outdated_total=len(outdated),
        outdated_truncated=len(outdated) > params.limit,
        vulnerabilities=vulnerabilities[: params.limit],
        vulnerabilities_total=len(vulnerabilities),
        vulnerabilities_truncated=len(vulnerabilities) > params.limit,
        counts=counts,
        audit_available=audit_available,
        notes=notes,
    )


def render(report: DepsReport) -> str:
    out = [f"{report.repo}  ({report.manager}, {report.lockfile or 'no lockfile'})"]
    if report.audit_available:
        c = report.counts
        out.append(
            f"vulnerabilities: {c.critical} critical, {c.high} high, {c.moderate} moderate, "
            f"{c.low} low, {c.info} info"
        )
        for v in report.vulnerabilities:
            fix = "major" if v.fix_available == "major" else ("yes" if v.fix_available else "no")
            shown = v.via[:3] + ([f"+{len(v.via) - 3} more"] if len(v.via) > 3 else [])
            via = f"  via: {'; '.join(shown)}" if shown else ""
            out.append(f"  {v.severity:<8} {v.name}  {v.range}  fix: {fix}{via}")
        if report.vulnerabilities_truncated:
            out.append(f"  ... {report.vulnerabilities_total - len(report.vulnerabilities)} more")
    out.append(f"outdated: {report.outdated_total}")
    for o in report.outdated:
        kind = f"  {o.type}" if o.type else ""
        out.append(f"  {o.name}  {o.current or 'missing'} -> {o.wanted} (latest {o.latest}){kind}")
    if report.outdated_truncated:
        out.append(f"  ... {report.outdated_total - len(report.outdated)} more")
    out.extend(f"note: {n}" for n in report.notes)
    return "\n".join(out)


TOOL = Tool(
    name="npm-deps-check",
    description=(
        "List a Node project's outdated packages (current, wanted, latest) and its audit findings by "
        "severity, through npm or pnpm as the lockfile dictates. Read-only. Use it before a release or "
        "when deciding what to upgrade."
    ),
    input=Input,
    run=run,
    render=render,
    examples=(
        "shed npm-deps-check",
        "shed npm-deps-check --path ~/Desktop/Projects/PMS2.0_PH/server --production",
        "shed npm-deps-check --no-outdated --json",
    ),
    version="0.1.0",
)
