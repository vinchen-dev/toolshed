from __future__ import annotations

import importlib
import importlib.machinery
import importlib.util
import re
import sys
from hashlib import sha1
from pathlib import Path
from types import ModuleType
from typing import Any

from shed.define import Tool
from shed.lib.errors import ShedError, UsageError

SHED_ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = SHED_ROOT / "tools"
TEMPLATE_DIR = TOOLS_DIR / "_template"

_NAME_RE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
_PACKAGE = "shed_tools"


def assert_valid_name(name: str) -> None:
    if not _NAME_RE.fullmatch(name):
        raise UsageError(
            f'Invalid tool name "{name}": use kebab-case letters, digits and dashes (e.g. git-context)'
        )


def _hidden(folder: Path) -> bool:
    return not folder.is_dir() or folder.name.startswith(("_", "."))


def _relative(folder: Path, root: Path) -> str:
    rel = folder.relative_to(root)
    return "" if rel == Path(".") else rel.as_posix()


def tool_dirs(tools_dir: Path = TOOLS_DIR) -> dict[str, Path]:
    """Tool name to folder for every folder under tools/ that holds a tool.py, at any depth.

    A folder without a tool.py is a group and is searched further; a tool's own subfolders are not.
    ``_``-prefixed folders (like _template) are skipped. Tool names must be unique across the tree.
    """
    found: dict[str, Path] = {}

    def walk(folder: Path) -> None:
        for child in sorted(folder.iterdir()):
            if _hidden(child):
                continue
            if not (child / "tool.py").is_file():
                walk(child)
            elif child.name in found:
                raise ShedError(
                    f'Tool "{child.name}" exists twice: tools/{_relative(found[child.name], tools_dir)} '
                    f"and tools/{_relative(child, tools_dir)}"
                )
            else:
                found[child.name] = child

    if tools_dir.is_dir():
        walk(tools_dir)
    return dict(sorted(found.items()))


def list_tool_names(tools_dir: Path = TOOLS_DIR) -> list[str]:
    return list(tool_dirs(tools_dir))


def list_groups(tools_dir: Path = TOOLS_DIR) -> dict[str, list[str]]:
    """Group folder (relative to tools/, "" for the top level) to the tools directly inside it."""
    groups: dict[str, list[str]] = {}
    for name, folder in tool_dirs(tools_dir).items():
        groups.setdefault(_relative(folder.parent, tools_dir), []).append(name)
    return dict(sorted(groups.items()))


def tool_group(name: str, tools_dir: Path = TOOLS_DIR) -> str:
    """The folder a tool sits in, relative to tools/: "" at the top level, "SN/autorunner" when nested."""
    return _relative(_tool_dir(name, tools_dir).parent, tools_dir)


def _tool_dir(name: str, tools_dir: Path) -> Path:
    assert_valid_name(name)
    folder = tool_dirs(tools_dir).get(name)
    if folder is None:
        raise UsageError(f'Unknown tool "{name}". Run `shed list` to see what is available.')
    return folder


def _ensure_package(name: str, search_path: list[str]) -> ModuleType:
    """Register an in-memory namespace package so tool folders import like real packages."""
    module = sys.modules.get(name)
    if module is None:
        spec = importlib.machinery.ModuleSpec(name, None, is_package=True)
        spec.submodule_search_locations = search_path
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
    return module


def load_tool_module(name: str, tools_dir: Path = TOOLS_DIR) -> ModuleType:
    """Import the tool's tool.py as ``shed_tools.<name>.tool`` so it can use relative imports."""
    tool_dir = _tool_dir(name, tools_dir)
    root = _PACKAGE
    if tools_dir != TOOLS_DIR:
        root = f"{_PACKAGE}_{sha1(str(tools_dir).encode()).hexdigest()[:8]}"
    _ensure_package(root, [])
    package = f"{root}.{name.replace('-', '_')}"
    _ensure_package(package, [str(tool_dir)])
    return importlib.import_module(f"{package}.tool")


def load_tool(name: str, tools_dir: Path = TOOLS_DIR) -> Tool[Any, Any]:
    module = load_tool_module(name, tools_dir)
    where = f"tools/{_relative(_tool_dir(name, tools_dir), tools_dir)}/tool.py"
    tool = getattr(module, "TOOL", None)
    if not isinstance(tool, Tool):
        raise ShedError(f"{where} must assign TOOL = Tool(...)")
    if tool.name != name:
        raise ShedError(f'{where} declares name "{tool.name}" but the folder is "{name}"; they must match')
    return tool


def load_all_tools(tools_dir: Path = TOOLS_DIR) -> list[Tool[Any, Any]]:
    return [load_tool(name, tools_dir) for name in list_tool_names(tools_dir)]
