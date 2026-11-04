"""tool-shed: define a tool once, expose it as a CLI command and as an MCP tool."""

from __future__ import annotations

from shed import version
from shed.define import Tool, ToolContext
from shed.lib.errors import ShedError, ToolError, UsageError
from shed.lib.shell import ShResult, sh

__all__ = ["ShResult", "ShedError", "Tool", "ToolContext", "ToolError", "UsageError", "sh", "version"]
