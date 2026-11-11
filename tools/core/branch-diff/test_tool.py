from __future__ import annotations

from pathlib import Path

import pytest

from shed import ToolContext, ToolError, sh
from shed.registry import load_tool, load_tool_module

module = load_tool_module("branch-diff")
TOOL = load_tool("branch-diff")
CTX = ToolContext(cwd=Path.cwd(), log=lambda _: None)


def _git(cwd: Path, *args: str) -> None:
    sh("git", ["-c", "user.name=Test", "-c", "user.email=test@example.com", *args], cwd=cwd)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """main has two commits; feature branches off the first, adds, edits, renames and deletes."""
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "a.txt").write_text("one\ntwo\n", encoding="utf-8")
    (tmp_path / "old.txt").write_text("keep me\n", encoding="utf-8")
    (tmp_path / "gone.txt").write_text("bye\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "first")
    _git(tmp_path, "checkout", "-q", "-b", "feature")
    (tmp_path / "a.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("new\n", encoding="utf-8")
    _git(tmp_path, "mv", "old.txt", "renamed.txt")
    _git(tmp_path, "rm", "-q", "gone.txt")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "-c", "user.name=Alice", "commit", "-q", "-m", "feature work")
    (tmp_path / "b.txt").write_text("new\nmore\n", encoding="utf-8")
    _git(tmp_path, "commit", "-q", "-am", "more feature work")
    _git(tmp_path, "checkout", "-q", "main")
    (tmp_path / "c.txt").write_text("main moved on\n", encoding="utf-8")
    _git(tmp_path, "add", "c.txt")
    _git(tmp_path, "commit", "-q", "-m", "main advances")
    _git(tmp_path, "checkout", "-q", "feature")
    return tmp_path


def test_lists_commits_files_and_authors(repo: Path) -> None:
    diff = TOOL.run(TOOL.input(path=str(repo)), CTX)
    assert (diff.base, diff.head, diff.ahead, diff.behind) == ("main", "HEAD", 2, 1)
    assert [c.subject for c in diff.commits] == ["more feature work", "feature work"]
    assert diff.commits_total == 2 and diff.commits_truncated is False
    files = {f.path: (f.status, f.added, f.deleted, f.renamed_from) for f in diff.files}
    assert files == {
        "a.txt": ("M", 1, 0, None),
        "b.txt": ("A", 2, 0, None),
        "gone.txt": ("D", 0, 1, None),
        "renamed.txt": ("R", 0, 0, "old.txt"),
    }
    assert (diff.totals.files, diff.totals.added, diff.totals.deleted) == (4, 3, 1)
    assert [(a.name, a.commits) for a in diff.authors] == [("Alice", 1), ("Test", 1)]


def test_limits_commits_and_skips_files_on_request(repo: Path) -> None:
    diff = TOOL.run(TOOL.input(path=str(repo), base="main", commits=1, files=False, stat=False), CTX)
    assert len(diff.commits) == 1 and diff.commits_truncated is True and diff.commits_total == 2
    assert diff.files == [] and diff.totals.files == 0


def test_renders_a_readable_summary(repo: Path) -> None:
    assert TOOL.render is not None
    text = TOOL.render(TOOL.run(TOOL.input(path=str(repo), commits=1), CTX))
    assert text.splitlines()[0].startswith("main...HEAD  (merge base ") and "2 ahead, 1 behind" in text
    assert "  ... 1 more commits" in text
    assert "files: 4 changed, +3 -1" in text
    assert "  R  old.txt -> renamed.txt" in text and "  M  a.txt  +1 -0" in text
    assert "authors: Alice 1, Test 1" in text


def test_prefers_the_remote_head_branch(repo: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    _git(repo, "checkout", "-q", "main")  # the remote's HEAD decides origin/HEAD in the clone
    clone = tmp_path_factory.mktemp("clone") / "repo"
    sh("git", ["clone", "-q", "--branch", "feature", str(repo), str(clone)])
    assert module.pick_base(clone) == "origin/main"
    diff = TOOL.run(TOOL.input(path=str(clone)), CTX)
    assert diff.base == "origin/main" and diff.ahead == 2


def test_fails_cleanly_on_bad_refs(repo: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    with pytest.raises(ToolError, match="base ref 'nope' does not resolve"):
        TOOL.run(TOOL.input(path=str(repo), base="nope"), CTX)
    plain = tmp_path_factory.mktemp("plain")  # outside the fixture repo
    with pytest.raises(ToolError, match="not inside a git repository"):
        TOOL.run(TOOL.input(path=str(plain)), CTX)
    lonely = tmp_path_factory.mktemp("lonely")
    _git(lonely, "init", "-q", "-b", "trunk")
    (lonely / "x").write_text("x", encoding="utf-8")
    _git(lonely, "add", "x")
    _git(lonely, "commit", "-q", "-m", "x")
    with pytest.raises(ToolError, match=r"cannot pick a base branch \(tried origin/HEAD"):
        TOOL.run(TOOL.input(path=str(lonely)), CTX)


def test_parses_numstat_with_renames_and_binaries() -> None:
    raw = "1\t2\tREADME\0" + "3\t1\t\0old/Makefile\0new/Makefile\0" + "-\t-\timg.png\0"
    assert module.parse_numstat(raw) == {"README": (1, 2), "new/Makefile": (3, 1), "img.png": (0, 0)}
