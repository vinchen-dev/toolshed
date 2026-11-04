from __future__ import annotations

from pathlib import Path

from shed import ToolContext
from shed.registry import load_tool

TOOL = load_tool("__NAME__")
CTX = ToolContext(cwd=Path.cwd(), log=lambda _: None)


def test_echoes_text() -> None:
    assert TOOL.run(TOOL.input(text="hi"), CTX).result == "hi"


def test_upper_cases_on_request() -> None:
    assert TOOL.run(TOOL.input(text="hi", upper=True), CTX).result == "HI"
