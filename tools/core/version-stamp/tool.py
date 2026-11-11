"""Bump a project's version and roll CHANGELOG.md's Unreleased section into a dated release.

Version sources, all updated together when present: package.json (with package-lock.json), pyproject.toml's
static `[project] version`, and otherwise the latest `v*` git tag (this repo's own scheme, where only the
changelog and the optional tag change). Nothing is ever committed or pushed.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from shed import Tool, ToolContext, ToolError, sh
from shed.lib.manila import now_manila

Source = Literal["package.json", "pyproject", "git-tag"]
_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)([-+].*)?$")
_UNRELEASED_RE = re.compile(r"^## \[Unreleased\][^\n]*\n", re.MULTILINE)
_HEADING_RE = re.compile(r"^## ", re.MULTILINE)


class Input(BaseModel):
    path: str = Field(".", description="The repository root, or any folder inside it")
    part: Literal["patch", "minor", "major"] = Field("patch", description="Which part of the version to bump")
    set: str | None = Field(None, description="Use this exact version (MAJOR.MINOR.PATCH) instead of bumping")
    changelog: bool = Field(True, description="Move CHANGELOG.md's Unreleased entries under the new version")
    allow_empty: bool = Field(False, description="Go ahead even when the Unreleased section has no entries")
    tag: bool = Field(False, description="Create the git tag v<version> afterwards (never pushed)")
    dry_run: bool = Field(False, description="Report what would change without writing anything")


class Stamp(BaseModel):
    repo: str
    previous: str
    version: str
    source: Source
    files_changed: list[str]
    changelog_updated: bool
    tag_created: bool
    dry_run: bool
    notes: list[str]


class _Plan(BaseModel):
    """Every file write, computed before anything is touched so a dry run and a real run agree."""

    writes: dict[str, str]
    source: Source
    previous: str
    changelog_updated: bool
    notes: list[str]


def bump(version: str, part: str) -> str:
    match = _SEMVER_RE.match(version)
    if not match:
        raise ToolError(f"current version '{version}' is not MAJOR.MINOR.PATCH; pass --set")
    major, minor, patch = (int(match.group(i)) for i in (1, 2, 3))
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def _indent_of(text: str) -> int:
    match = re.search(r"^( +)\"", text, re.MULTILINE)
    return len(match.group(1)) if match else 2


def _load_json(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ToolError(f"{path.name} is not valid JSON: {exc}") from exc
    return (data if isinstance(data, dict) else {}), text


def _json_version(path: Path) -> str | None:
    data, _ = _load_json(path)
    version = data.get("version")
    return version if isinstance(version, str) else None


def _json_with_version(path: Path, version: str, lock: bool) -> str:
    """package.json or package-lock.json with the version replaced, keeping the file's indent."""
    data, text = _load_json(path)
    data["version"] = version
    if lock:
        root = (data.get("packages") or {}).get("")
        if isinstance(root, dict) and "version" in root:
            root["version"] = version
    return json.dumps(data, indent=_indent_of(text), ensure_ascii=False) + "\n"


def _pyproject_version(path: Path) -> str | None:
    """The static `[project] version`, or None when absent or declared dynamic."""
    try:
        project = tomllib.loads(path.read_text(encoding="utf-8")).get("project") or {}
    except tomllib.TOMLDecodeError as exc:
        raise ToolError(f"pyproject.toml does not parse: {exc}") from exc
    current = project.get("version")
    if not isinstance(current, str) or "version" in (project.get("dynamic") or []):
        return None
    return current


def _pyproject_with_version(path: Path, version: str) -> str:
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(r'(^\[project\][^\[]*?^version\s*=\s*")([^"]*)(")', re.MULTILINE | re.DOTALL)
    new, count = pattern.subn(lambda m: f"{m.group(1)}{version}{m.group(3)}", text, count=1)
    if count != 1:
        raise ToolError("pyproject.toml: cannot find the version line under [project]")
    return new


def roll_changelog(text: str, version: str, date: str, allow_empty: bool) -> str:
    """Open a `## [version] - date` section holding the Unreleased entries, leaving Unreleased empty."""
    match = _UNRELEASED_RE.search(text)
    if not match:
        raise ToolError("CHANGELOG.md has no '## [Unreleased]' heading")
    body_start = match.end()
    next_heading = _HEADING_RE.search(text, body_start)
    body_end = next_heading.start() if next_heading else len(text)
    entries = text[body_start:body_end].strip("\n")
    if not entries and not allow_empty:
        raise ToolError("CHANGELOG.md has no Unreleased entries; add them or pass --allow-empty")
    release = f"## [{version}] - {date}\n{entries}\n\n" if entries else f"## [{version}] - {date}\n\n"
    return f"{text[:body_start]}\n{release}{text[body_end:]}"


def _latest_tag(root: Path) -> str | None:
    tag = sh("git", ["describe", "--tags", "--abbrev=0", "--match", "v*"], cwd=root, allow_failure=True)
    return tag.stdout.strip() if tag.code == 0 and tag.stdout.strip() else None


def plan(root: Path, params: Input, today: str) -> tuple[_Plan, str]:
    """Work out the new version and every file it touches, without writing."""
    notes: list[str] = []
    package, lock, pyproject = root / "package.json", root / "package-lock.json", root / "pyproject.toml"
    found: list[tuple[str, str | None]] = []
    if package.is_file():
        found.append(("package.json", _json_version(package)))
    if pyproject.is_file():
        found.append(("pyproject.toml", _pyproject_version(pyproject)))
    firsts = [(label, v) for label, v in found if v is not None]
    if firsts:
        label, current = firsts[0]
        source: Source = "pyproject" if label == "pyproject.toml" else "package.json"
    else:
        tag = _latest_tag(root)
        if tag is None:
            raise ToolError(
                "no version source found (package.json, a static pyproject.toml version, or a v* git tag)"
            )
        current, source = tag.removeprefix("v"), "git-tag"
        notes.append(f"version comes from git tag {tag}; only the changelog and the tag change")

    if params.set:
        if not _SEMVER_RE.match(params.set):
            raise ToolError(f"--set '{params.set}' is not MAJOR.MINOR.PATCH")
        version = params.set
    else:
        version = bump(current, params.part)
    if version == current:
        raise ToolError(f"version {version} is already the current one")

    writes: dict[str, str] = {}
    for label, value in found:
        if value is None:
            notes.append(f"{label} has no static version; left alone")
            continue
        if value != current:
            notes.append(f"{label} said {value}, not {current}; it is set to {version} too")
        if label == "package.json":
            writes[str(package)] = _json_with_version(package, version, lock=False)
            if lock.is_file() and _json_version(lock) is not None:
                writes[str(lock)] = _json_with_version(lock, version, lock=True)
        else:
            writes[str(pyproject)] = _pyproject_with_version(pyproject, version)

    changelog_updated = False
    changelog = root / "CHANGELOG.md"
    if params.changelog:
        if changelog.is_file():
            writes[str(changelog)] = roll_changelog(
                changelog.read_text(encoding="utf-8"), version, today, params.allow_empty
            )
            changelog_updated = True
        else:
            notes.append("no CHANGELOG.md at the repository root; nothing to roll")
    plan_ = _Plan(
        writes=writes, source=source, previous=current, changelog_updated=changelog_updated, notes=notes
    )
    return plan_, version


def run(params: Input, ctx: ToolContext) -> Stamp:
    path = (ctx.cwd / Path(params.path).expanduser()).resolve()
    if not path.is_dir():
        raise ToolError(f"{path} is not a directory")
    top = sh("git", ["rev-parse", "--show-toplevel"], cwd=path, allow_failure=True)
    root = Path(top.stdout.strip()) if top.code == 0 else path
    if params.tag and top.code != 0:
        raise ToolError(f"{path} is not inside a git repository, so --tag cannot work")

    today = now_manila().strftime("%Y-%m-%d")
    plan_, version = plan(root, params, today)
    tag_name = f"v{version}"
    if params.tag:
        exists = sh(
            "git", ["rev-parse", "-q", "--verify", f"refs/tags/{tag_name}"], cwd=root, allow_failure=True
        )
        if exists.code == 0:
            raise ToolError(f"tag {tag_name} already exists")

    files_changed = [str(Path(p).relative_to(root)) for p in plan_.writes]
    if not params.dry_run:
        for file, text in plan_.writes.items():
            Path(file).write_text(text, encoding="utf-8")
        if params.tag:
            sh("git", ["tag", tag_name], cwd=root)
    ctx.log(f"# {plan_.previous} -> {version} ({plan_.source}){' [dry run]' if params.dry_run else ''}")
    return Stamp(
        repo=str(root),
        previous=plan_.previous,
        version=version,
        source=plan_.source,
        files_changed=files_changed,
        changelog_updated=plan_.changelog_updated,
        tag_created=params.tag and not params.dry_run,
        dry_run=params.dry_run,
        notes=plan_.notes,
    )


def render(stamp: Stamp) -> str:
    files = ", ".join(stamp.files_changed) if stamp.files_changed else "no files"
    line = f"{stamp.previous} -> {stamp.version} ({files})"
    if stamp.tag_created:
        line += f"  tag v{stamp.version}"
    if stamp.dry_run:
        line += "  [dry run: nothing written]"
    return "\n".join([line, *(f"note: {n}" for n in stamp.notes)])


TOOL = Tool(
    name="version-stamp",
    description=(
        "Bump a project's version (package.json with package-lock.json, a static pyproject.toml version, or "
        "the latest v* git tag) and move CHANGELOG.md's Unreleased entries under a dated heading for it, "
        "optionally creating the git tag. Nothing is committed or pushed. Use it when cutting a release."
    ),
    input=Input,
    run=run,
    render=render,
    examples=(
        "shed version-stamp --dry-run",
        "shed version-stamp --part minor",
        "shed version-stamp --set 1.22.0 --tag --path ~/Desktop/Projects/PMS2.0_PH",
    ),
    version="0.1.0",
)
