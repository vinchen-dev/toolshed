from __future__ import annotations

import json
from typing import Any

from pydantic_core import to_jsonable_python

from shed.define import Tool


def to_json(result: object, *, compact: bool) -> str:
    """Serialise anything pydantic can (models, dataclasses, paths, datetimes) to JSON text."""
    data = to_jsonable_python(result, fallback=str)
    if compact:
        return json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    return json.dumps(data, indent=2, ensure_ascii=False)


def render_result(tool: Tool[Any, Any], result: object, *, json_mode: bool) -> str:
    """Turn a tool result into the text the CLI prints. ``--json`` is compact to save tokens."""
    if json_mode:
        return to_json(result, compact=True)
    if tool.render is not None:
        return tool.render(result)
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    return to_json(result, compact=False)
