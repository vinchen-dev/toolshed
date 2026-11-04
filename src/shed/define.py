from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Runtime information handed to every tool invocation."""

    cwd: Path
    """Directory the caller invoked the tool from. Resolve relative paths against it."""

    log: Callable[[str], None]
    """Progress or diagnostic output. Goes to stderr so stdout stays machine-readable."""


@dataclass(frozen=True, slots=True)
class Tool[I: BaseModel, R]:
    """One tool. Assign an instance to ``TOOL`` in ``tools/<name>/tool.py``."""

    name: str
    """Kebab-case name. Must equal the folder name under tools/."""

    description: str
    """One or two sentences an agent can use to decide when to call this tool."""

    input: type[I]
    """Pydantic model. Drives CLI flags, the MCP input schema and validation."""

    run: Callable[[I, ToolContext], R]
    """Does the work. Returns JSON-serialisable data (pydantic models are fine); never prints."""

    render: Callable[[R], str] | None = None
    """Optional human-friendly rendering for terminal output. Defaults to pretty JSON."""

    examples: tuple[str, ...] = ()
    """Example invocations shown by ``shed help <name>``."""

    version: str = "0.1.0"
    """The tool's own version. Bump it when the inputs or result shape change, and note it in CHANGELOG.md."""
