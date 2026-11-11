from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from shed import ShResult, ToolContext, ToolError, UsageError
from shed.registry import load_tool, load_tool_module

module = load_tool_module("graph-query")
TOOL = load_tool("graph-query")


class Recorder:
    def __init__(self, stdout: str = "answer", fail: str | None = None) -> None:
        self.calls: list[tuple[str, list[str], Any]] = []
        self.stdout = stdout
        self.fail = fail

    def __call__(self, cmd: str, args: tuple[str, ...] | list[str] = (), **kwargs: Any) -> ShResult:
        self.calls.append((cmd, list(args), kwargs))
        if self.fail:
            raise ToolError(self.fail)
        return ShResult(stdout=self.stdout, stderr="", code=0)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    out = root / "graphify-out"
    out.mkdir(parents=True)
    (out / "graph.json").write_text("{}")
    (out / "GRAPH_SUMMARY.md").write_text(
        "# Graph Summary — repo (built 2026-07-03)\n\n> Lean head.\n\n## Stats\n- 10 nodes\n- 20 edges\n"
    )
    (root / "server" / "src").mkdir(parents=True)
    return root


def ctx(cwd: Path) -> ToolContext:
    return ToolContext(cwd=cwd, log=lambda _: None)


def test_runs_each_graphify_mode_against_the_nearest_graph(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = Recorder()
    monkeypatch.setattr(module, "sh", rec)
    graph = str(repo / "graphify-out" / "graph.json")
    answer = TOOL.run(TOOL.input(question="how are deposits made", budget=600, repo="server/src"), ctx(repo))
    assert (answer.mode, answer.repo, answer.graph_built, answer.output) == (
        "query",
        str(repo),
        "2026-07-03",
        "answer\n",
    )
    assert rec.calls[-1][1] == ["query", "how are deposits made", "--budget", "600", "--graph", graph]
    assert rec.calls[-1][2]["cwd"] == str(repo)
    TOOL.run(TOOL.input(explain="Node"), ctx(repo))
    assert rec.calls[-1][1][:2] == ["explain", "Node"]
    TOOL.run(TOOL.input(path_from="A", path_to="B"), ctx(repo))
    assert rec.calls[-1][1][:3] == ["path", "A", "B"]
    affected = TOOL.run(TOOL.input(affected="X", depth=3), ctx(repo))
    assert rec.calls[-1][1][:4] == ["affected", "X", "--depth", "3"]
    assert affected.command == f"graphify affected X --depth 3 --graph {graph}"


def test_summary_reads_the_file_without_graphify(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rec = Recorder()
    monkeypatch.setattr(module, "sh", rec)
    answer = TOOL.run(TOOL.input(summary=True, lines=2), ctx(repo))
    assert answer.output == "# Graph Summary — repo (built 2026-07-03)\n" and answer.command is None
    assert rec.calls == []
    (repo / "graphify-out" / "GRAPH_SUMMARY.md").unlink()
    (repo / "graphify-out" / "GRAPH_REPORT.md").write_text("# Report\nmore\n")
    assert TOOL.run(TOOL.input(summary=True), ctx(repo)).output == "# Report\nmore\n"
    assert module.render(answer).startswith("# summary on ")


def test_exactly_one_mode_is_required(repo: Path) -> None:
    with pytest.raises(UsageError, match="exactly one"):
        TOOL.run(TOOL.input(), ctx(repo))
    with pytest.raises(UsageError, match="exactly one"):
        TOOL.run(TOOL.input(question="q", summary=True), ctx(repo))
    with pytest.raises(UsageError, match="go together"):
        TOOL.run(TOOL.input(path_from="A"), ctx(repo))


def test_missing_graph_or_cli_fail_cleanly(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(ToolError, match="no graphify-out/ at or above"):
        TOOL.run(TOOL.input(summary=True), ctx(tmp_path))
    monkeypatch.setattr(module, "sh", Recorder(fail="Command not found: graphify"))
    with pytest.raises(ToolError, match="not on PATH"):
        TOOL.run(TOOL.input(explain="Node"), ctx(repo))
    (repo / "graphify-out" / "graph.json").unlink()
    with pytest.raises(ToolError, match=r"graph\.json is missing"):
        TOOL.run(TOOL.input(explain="Node"), ctx(repo))
