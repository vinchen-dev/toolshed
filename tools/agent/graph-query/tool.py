"""Ask a repository's graphify knowledge graph a question, through the graphify CLI.

Finds graphify-out/ by walking up from the start folder, then runs one of graphify's read-only commands
(query, explain, path, affected) against its graph.json, or returns the head of the graph summary.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, Field

from shed import Tool, ToolContext, ToolError, UsageError, sh

GRAPH_DIR = "graphify-out"
_BUILT = re.compile(r"built (\d{4}-\d{2}-\d{2})")
INSTALL_HINT = "graphify is not on PATH: install it (pip install graphify, or see the graphify skill)"


class Input(BaseModel):
    question: str | None = Field(
        None, description="Question for `graphify query` (graph traversal + summary)"
    )
    explain: str | None = Field(None, description="Node to explain with its neighbours (`graphify explain`)")
    path_from: str | None = Field(None, description="Start node for `graphify path`; needs --path-to")
    path_to: str | None = Field(None, description="End node for `graphify path`; needs --path-from")
    affected: str | None = Field(None, description="Node whose dependants to list (`graphify affected`)")
    depth: int = Field(2, ge=1, description="Reverse traversal depth for --affected")
    budget: int = Field(2000, ge=100, description="Token cap for the --question answer")
    summary: bool = Field(
        False, description="Return the head of GRAPH_SUMMARY.md (or GRAPH_REPORT.md) instead"
    )
    lines: int = Field(60, ge=1, description="Lines of the summary to return")
    repo: str = Field(".", description="Folder to start from; graphify-out/ is searched upwards from here")


class GraphAnswer(BaseModel):
    mode: str
    repo: str
    graph_dir: str
    graph_built: str | None
    command: str | None
    output: str


def find_graph_dir(start: Path) -> Path:
    """The nearest graphify-out/ at or above ``start``; names the folders tried when there is none."""
    tried: list[Path] = []
    for folder in (start, *start.parents):
        candidate = folder / GRAPH_DIR
        if candidate.is_dir():
            return candidate
        tried.append(folder)
    shown = ", ".join(str(t) for t in tried[:4]) + (" ..." if len(tried) > 4 else "")
    raise ToolError(f"no {GRAPH_DIR}/ at or above {start} (tried {shown}); run /graphify in the repo first")


def built_date(graph_dir: Path) -> str | None:
    summary = graph_dir / "GRAPH_SUMMARY.md"
    if not summary.is_file():
        return None
    with summary.open(encoding="utf-8", errors="replace") as handle:
        match = _BUILT.search(handle.readline())
    return match.group(1) if match else None


def read_summary(graph_dir: Path, lines: int) -> str:
    for name in ("GRAPH_SUMMARY.md", "GRAPH_REPORT.md"):
        path = graph_dir / name
        if path.is_file():
            with path.open(encoding="utf-8", errors="replace") as handle:
                return "".join(handle.readline() for _ in range(lines)).rstrip() + "\n"
    raise ToolError(f"{graph_dir} has neither GRAPH_SUMMARY.md nor GRAPH_REPORT.md")


def choose_mode(params: Input) -> tuple[str, list[str]]:
    """(mode, graphify argv without --graph) for the one mode the inputs select."""
    if (params.path_from is None) != (params.path_to is None):
        raise UsageError("--path-from and --path-to go together")
    modes: dict[str, list[str] | None] = {
        "query": ["query", params.question, "--budget", str(params.budget)] if params.question else None,
        "explain": ["explain", params.explain] if params.explain else None,
        "path": ["path", params.path_from, params.path_to] if params.path_from and params.path_to else None,
        "affected": ["affected", params.affected, "--depth", str(params.depth)] if params.affected else None,
        "summary": [] if params.summary else None,
    }
    chosen = [(mode, argv) for mode, argv in modes.items() if argv is not None]
    if len(chosen) != 1:
        raise UsageError(
            "give exactly one of --question, --explain, --path-from/--path-to, --affected or --summary"
        )
    mode, argv = chosen[0]
    return mode, [str(a) for a in argv]


def run(params: Input, ctx: ToolContext) -> GraphAnswer:
    mode, argv = choose_mode(params)
    start = (ctx.cwd / Path(params.repo).expanduser()).resolve()
    if not start.is_dir():
        raise ToolError(f"{start} is not a directory")
    graph_dir = find_graph_dir(start)
    built = built_date(graph_dir)
    repo = str(graph_dir.parent)
    if mode == "summary":
        return GraphAnswer(
            mode=mode,
            repo=repo,
            graph_dir=str(graph_dir),
            graph_built=built,
            command=None,
            output=read_summary(graph_dir, params.lines),
        )
    graph = graph_dir / "graph.json"
    if not graph.is_file():
        raise ToolError(f"{graph} is missing; rebuild the graph with /graphify")
    args = [*argv, "--graph", str(graph)]
    ctx.log(f"graphify {' '.join(args)}")
    try:
        result = sh("graphify", args, cwd=repo, timeout=300)
    except ToolError as exc:
        raise ToolError(INSTALL_HINT) if "Command not found" in str(exc) else exc from exc
    output = result.stdout.strip() or result.stderr.strip()
    return GraphAnswer(
        mode=mode,
        repo=repo,
        graph_dir=str(graph_dir),
        graph_built=built,
        command="graphify " + " ".join(args),
        output=output + "\n" if output else "(graphify printed nothing)\n",
    )


def render(answer: GraphAnswer) -> str:
    head = f"# {answer.mode} on {answer.graph_dir}" + (
        f" (built {answer.graph_built})" if answer.graph_built else ""
    )
    return f"{head}\n{answer.output}".rstrip() + "\n"


TOOL = Tool(
    name="graph-query",
    description=(
        "Ask the repo's graphify knowledge graph: a question (BFS traversal), a node to explain, the path "
        "between two nodes, what a change to a node affects, or the graph summary. Use it before grepping "
        "when a repo has graphify-out/, to find where something happens or what a change touches."
    ),
    input=Input,
    run=run,
    render=render,
    examples=(
        'shed graph-query --question "how is a deposit proposal created"',
        "shed graph-query --summary --lines 40 --repo ~/Desktop/Projects/PMS2.0_PH",
        "shed graph-query --affected createProposal --depth 3 --json",
    ),
    version="0.1.0",
)
