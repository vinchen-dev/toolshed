from __future__ import annotations

import json
from pathlib import Path

import pytest

from shed import ToolContext, ToolError, sh
from shed.registry import load_tool, load_tool_module

module = load_tool_module("version-stamp")
TOOL = load_tool("version-stamp")

CHANGELOG = """# Changelog

## [Unreleased]

### Added
- A thing.

## [1.2.3] - 2026-01-01

### Fixed
- Old fix.
"""


def _git(cwd: Path, *args: str) -> None:
    sh("git", ["-c", "user.name=Test", "-c", "user.email=test@example.com", *args], cwd=cwd)


def ctx(cwd: Path) -> ToolContext:
    return ToolContext(cwd=cwd, log=lambda _: None)


def node_repo(root: Path, with_lock: bool = True) -> Path:
    root.mkdir(exist_ok=True)
    (root / "package.json").write_text('{\n  "name": "pms",\n  "version": "1.2.3",\n  "private": true\n}\n')
    if with_lock:
        lock = {
            "name": "pms",
            "version": "1.2.3",
            "lockfileVersion": 3,
            "packages": {"": {"version": "1.2.3"}},
        }
        (root / "package-lock.json").write_text(json.dumps(lock, indent=2) + "\n")
    (root / "CHANGELOG.md").write_text(CHANGELOG)
    return root


def test_bumps_each_part() -> None:
    assert module.bump("1.2.3", "patch") == "1.2.4"
    assert module.bump("1.2.3", "minor") == "1.3.0"
    assert module.bump("1.2.3-rc.1", "major") == "2.0.0"
    with pytest.raises(ToolError, match=r"not MAJOR\.MINOR\.PATCH"):
        module.bump("2026.11", "patch")


def test_stamps_package_json_lock_and_changelog(tmp_path: Path) -> None:
    repo = node_repo(tmp_path / "app")
    stamp = TOOL.run(TOOL.input(path="app"), ctx(tmp_path))
    assert (stamp.previous, stamp.version, stamp.source) == ("1.2.3", "1.2.4", "package.json")
    assert stamp.files_changed == ["package.json", "package-lock.json", "CHANGELOG.md"]
    assert stamp.changelog_updated is True and stamp.tag_created is False and stamp.dry_run is False
    package = json.loads((repo / "package.json").read_text())
    assert package["version"] == "1.2.4" and list(package) == ["name", "version", "private"]
    lock = json.loads((repo / "package-lock.json").read_text())
    assert lock["version"] == "1.2.4" and lock["packages"][""]["version"] == "1.2.4"
    text = (repo / "CHANGELOG.md").read_text()
    assert "## [Unreleased]\n\n## [1.2.4] - 20" in text
    assert "## [1.2.4] - " in text and text.index("### Added\n- A thing.") > text.index("## [1.2.4]")
    assert text.index("## [1.2.3] - 2026-01-01") > text.index("- A thing.")
    assert TOOL.render is not None
    assert TOOL.render(stamp) == "1.2.3 -> 1.2.4 (package.json, package-lock.json, CHANGELOG.md)"


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    repo = node_repo(tmp_path, with_lock=False)
    before = {p.name: p.read_text() for p in repo.iterdir()}
    stamp = TOOL.run(TOOL.input(part="minor", dry_run=True), ctx(repo))
    assert (stamp.version, stamp.files_changed, stamp.dry_run) == (
        "1.3.0",
        ["package.json", "CHANGELOG.md"],
        True,
    )
    assert {p.name: p.read_text() for p in repo.iterdir()} == before
    assert TOOL.render is not None
    assert TOOL.render(stamp).endswith("[dry run: nothing written]")


def test_set_overrides_the_part_and_guards_the_empty_changelog(tmp_path: Path) -> None:
    repo = node_repo(tmp_path, with_lock=False)
    (repo / "CHANGELOG.md").write_text("# Changelog\n\n## [Unreleased]\n\n## [1.2.3] - 2026-01-01\n- x\n")
    with pytest.raises(ToolError, match="no Unreleased entries"):
        TOOL.run(TOOL.input(set="2.0.0"), ctx(repo))
    with pytest.raises(ToolError, match=r"is not MAJOR\.MINOR\.PATCH"):
        TOOL.run(TOOL.input(set="v2"), ctx(repo))
    with pytest.raises(ToolError, match="already the current one"):
        TOOL.run(TOOL.input(set="1.2.3", changelog=False), ctx(repo))
    stamp = TOOL.run(TOOL.input(set="2.0.0", allow_empty=True), ctx(repo))
    assert stamp.version == "2.0.0"
    assert "## [Unreleased]\n\n## [2.0.0] - " in (repo / "CHANGELOG.md").read_text()
    assert "## [1.2.3] - 2026-01-01\n- x\n" in (repo / "CHANGELOG.md").read_text()


def test_pyproject_static_version(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["hatchling"]\n\n[project]\nname = "x"\nversion = "0.9.0"\n\n'
        "[tool.ruff]\nline-length = 110\n"
    )
    stamp = TOOL.run(TOOL.input(changelog=False), ctx(tmp_path))
    assert (stamp.previous, stamp.version, stamp.source, stamp.files_changed) == (
        "0.9.0",
        "0.9.1",
        "pyproject",
        ["pyproject.toml"],
    )
    assert 'version = "0.9.1"' in (tmp_path / "pyproject.toml").read_text()


def test_dynamic_pyproject_falls_back_to_the_git_tag_and_tags(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "shed"\ndynamic = ["version"]\n')
    (tmp_path / "CHANGELOG.md").write_text(CHANGELOG)
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "first")
    with pytest.raises(ToolError, match="no version source found"):
        TOOL.run(TOOL.input(), ctx(tmp_path))
    _git(tmp_path, "tag", "v0.4.0")
    stamp = TOOL.run(TOOL.input(part="minor", tag=True), ctx(tmp_path))
    assert (stamp.previous, stamp.version, stamp.source, stamp.tag_created) == (
        "0.4.0",
        "0.5.0",
        "git-tag",
        True,
    )
    assert stamp.files_changed == ["CHANGELOG.md"]
    assert any("has no static version" in n for n in stamp.notes)
    assert "v0.5.0" in sh("git", ["tag"], cwd=tmp_path).stdout.split()
    _git(tmp_path, "tag", "-d", "v0.4.0")  # two tags on one commit would make "latest" ambiguous
    _git(tmp_path, "tag", "v0.6.0")
    with pytest.raises(ToolError, match=r"tag v0\.6\.0 already exists"):
        TOOL.run(TOOL.input(part="minor", tag=True, allow_empty=True), ctx(tmp_path))


def test_tag_needs_a_repository_and_a_missing_changelog_is_noted(tmp_path: Path) -> None:
    repo = node_repo(tmp_path, with_lock=False)
    (repo / "CHANGELOG.md").unlink()
    with pytest.raises(ToolError, match="not inside a git repository, so --tag"):
        TOOL.run(TOOL.input(tag=True), ctx(repo))
    stamp = TOOL.run(TOOL.input(), ctx(repo))
    assert stamp.changelog_updated is False and stamp.files_changed == ["package.json"]
    assert any("no CHANGELOG.md" in n for n in stamp.notes)
