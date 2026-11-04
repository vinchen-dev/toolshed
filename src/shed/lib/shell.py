from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from shed.lib.errors import ToolError


@dataclass(frozen=True, slots=True)
class ShResult:
    stdout: str
    stderr: str
    code: int


def sh(
    cmd: str,
    args: Sequence[str] = (),
    *,
    cwd: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    allow_failure: bool = False,
    timeout: float | None = None,
) -> ShResult:
    """Run a command without a shell, so there is no quoting to get wrong.

    Raises ToolError when the command is missing or exits non-zero, unless ``allow_failure`` is set.
    """
    try:
        proc = subprocess.run(
            [cmd, *args],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        missing = exc.filename if exc.filename and exc.filename != cmd else None
        message = f"Directory not found: {missing}" if missing else f"Command not found: {cmd}"
        raise ToolError(message) from exc
    except subprocess.TimeoutExpired as exc:
        raise ToolError(f"{cmd} timed out after {timeout}s") from exc

    result = ShResult(stdout=proc.stdout, stderr=proc.stderr, code=proc.returncode)
    if result.code != 0 and not allow_failure:
        detail = result.stderr.strip() or result.stdout.strip()
        suffix = f": {detail}" if detail else ""
        raise ToolError(f"{cmd} {' '.join(args)} exited with {result.code}{suffix}")
    return result
