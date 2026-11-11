from __future__ import annotations

import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from shed import ToolContext, ToolError, sh
from shed.registry import load_tool

TOOL = load_tool("git-context")
CTX = ToolContext(cwd=Path.cwd(), log=lambda _: None)


def _git(cwd: Path, *args: str) -> None:
    sh("git", ["-c", "user.name=Test", "-c", "user.email=test@example.com", *args], cwd=cwd)


@pytest.fixture(scope="module")
def repo() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="shed-git-") as tmp:
        path = Path(tmp)
        _git(path, "init", "-q", "-b", "main")
        (path / "a.txt").write_text("one\n", encoding="utf-8")
        _git(path, "add", "a.txt")
        _git(path, "commit", "-q", "-m", "first commit")
        (path / "a.txt").write_text("two\n", encoding="utf-8")
        (path / "b.txt").write_text("new\n", encoding="utf-8")
        yield path


def test_reports_branch_changes_and_commits(repo: Path) -> None:
    result = TOOL.run(TOOL.input(path=str(repo), commits=10), CTX)
    assert result.branch == "main"
    assert result.upstream is None
    statuses = {(c.path, c.status, c.staged) for c in result.changes}
    assert {("a.txt", " M", False), ("b.txt", "??", False)} <= statuses
    assert [c.subject for c in result.commits] == ["first commit"]


def test_renders_a_readable_summary(repo: Path) -> None:
    assert TOOL.render is not None
    text = TOOL.render(TOOL.run(TOOL.input(path=str(repo), commits=1), CTX))
    assert "branch: main (no upstream)" in text
    assert "?? b.txt" in text


def test_fails_cleanly_outside_a_repository(tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="not inside a git repository"):
        TOOL.run(TOOL.input(path=str(tmp_path), commits=1), CTX)
