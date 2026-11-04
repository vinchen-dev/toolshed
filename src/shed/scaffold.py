from __future__ import annotations

import shutil
from pathlib import Path

from shed.lib.errors import UsageError
from shed.registry import TEMPLATE_DIR, TOOLS_DIR, assert_valid_name, tool_dirs


def scaffold_tool(name: str, tools_dir: Path = TOOLS_DIR, template_dir: Path = TEMPLATE_DIR) -> Path:
    """Copy tools/_template to tools/<name>; "<folder>/<name>" puts the tool inside that group folder."""
    group, _, tool = name.strip("/").rpartition("/")
    assert_valid_name(tool)
    parts = group.split("/") if group else []
    if any(not part or part.startswith(("_", ".")) for part in parts):
        raise UsageError(f'Invalid group "{group}": folder names cannot be empty or start with "_" or "."')
    existing = tool_dirs(tools_dir)
    if tool in existing:
        raise UsageError(f"tools/{existing[tool].relative_to(tools_dir).as_posix()} already exists")
    dest = tools_dir.joinpath(*parts, tool)
    if dest.exists():
        raise UsageError(f"tools/{name} already exists")
    for parent in dest.parents:
        if parent == tools_dir:
            break
        if (parent / "tool.py").is_file():
            raise UsageError(f"tools/{name} would sit inside the tool {parent.name}; tools cannot nest")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(template_dir, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for file in dest.rglob("*"):
        if file.is_file():
            file.write_text(file.read_text(encoding="utf-8").replace("__NAME__", tool), encoding="utf-8")
    return dest
