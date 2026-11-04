from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from shed import ShedError, ToolContext, UsageError
from shed.registry import list_tool_names, load_tool, tool_group
from shed.scaffold import scaffold_tool


def test_scaffold_creates_a_runnable_tool(tmp_path: Path) -> None:
    dest = scaffold_tool("probe-tool", tools_dir=tmp_path)
    assert dest == tmp_path / "probe-tool"
    assert "__NAME__" not in (dest / "tool.py").read_text(encoding="utf-8")
    assert list_tool_names(tmp_path) == ["probe-tool"]

    tool = load_tool("probe-tool", tools_dir=tmp_path)
    ctx = ToolContext(cwd=tmp_path, log=lambda _: None)
    assert tool.run(tool.input(text="hi", upper=True), ctx).result == "HI"


def test_scaffold_rejects_bad_names_and_duplicates(tmp_path: Path) -> None:
    with pytest.raises(UsageError, match="kebab-case"):
        scaffold_tool("Bad_Name", tools_dir=tmp_path)
    scaffold_tool("dup", tools_dir=tmp_path)
    with pytest.raises(UsageError, match="already exists"):
        scaffold_tool("dup", tools_dir=tmp_path)


def test_scaffold_into_a_group_folder(tmp_path: Path) -> None:
    dest = scaffold_tool("grp/sub/probe", tools_dir=tmp_path)
    assert dest == tmp_path / "grp" / "sub" / "probe"
    assert list_tool_names(tmp_path) == ["probe"] and tool_group("probe", tmp_path) == "grp/sub"
    assert load_tool("probe", tools_dir=tmp_path).name == "probe"
    with pytest.raises(UsageError, match="tools/grp/sub/probe already exists"):
        scaffold_tool("other/probe", tools_dir=tmp_path)
    with pytest.raises(UsageError, match="tools cannot nest"):
        scaffold_tool("grp/sub/probe/inner", tools_dir=tmp_path)
    with pytest.raises(UsageError, match="Invalid group"):
        scaffold_tool("_hidden/x", tools_dir=tmp_path)
    shutil.copytree(dest, tmp_path / "probe")
    with pytest.raises(ShedError, match="exists twice: tools/grp/sub/probe and tools/probe"):
        list_tool_names(tmp_path)
