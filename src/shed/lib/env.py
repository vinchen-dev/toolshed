from __future__ import annotations

import os
from collections.abc import MutableMapping
from pathlib import Path


def load_env_file(path: Path, environ: MutableMapping[str, str] = os.environ) -> None:
    """Load KEY=VALUE lines from a dotenv-style file without overriding variables already set."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            environ.setdefault(key.strip(), value.strip().strip("'\""))


def require_env(*names: str, env_file: Path) -> dict[str, str]:
    """Return the named variables, loading ``env_file`` first. Raises ToolError naming what is missing."""
    from shed.lib.errors import ToolError

    load_env_file(env_file)
    values = {name: os.environ.get(name, "") for name in names}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ToolError(f"Missing {'/'.join(missing)} (set them or add to {env_file})")
    return values
