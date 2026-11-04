"""Cursor file for repeated checks: which post links each platform+query has already reported."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from shed import ToolError

from .models import Post

MAX_SEEN = 5000


def load(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ToolError(f"cannot read cursor file {path}: {exc}") from exc


def save(path: Path, state: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".cursor-")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    os.replace(tmp, path)


def key(platform: str, subject: str) -> str:
    return f"{platform}|{subject}"


def unseen(state: dict[str, dict[str, Any]], k: str, posts: list[Post]) -> list[Post]:
    """Posts whose link the cursor has not recorded; an absent key means a first run and keeps everything."""
    entry = state.get(k)
    if entry is None:
        return posts
    seen: set[str] = set(entry.get("seen") or [])
    return [p for p in posts if p.url not in seen]


def remember(state: dict[str, dict[str, Any]], k: str, posts: list[Post]) -> None:
    entry = state.setdefault(k, {"seen": [], "newest": None})
    seen: list[str] = list(entry.get("seen") or [])
    known = set(seen)
    seen.extend(p.url for p in posts if p.url not in known)
    entry["seen"] = seen[-MAX_SEEN:]
    dated = [p.created_at for p in posts if p.created_at is not None]
    newest = max(dated) if dated else None
    previous = entry.get("newest")
    if newest is not None and (not isinstance(previous, str) or newest > datetime.fromisoformat(previous)):
        entry["newest"] = newest.isoformat()
