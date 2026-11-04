"""Manila wall-clock helpers shared by the SLS-backed tools."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

MANILA = ZoneInfo("Asia/Manila")
_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M")


def parse_manila(text: str) -> datetime:
    """Parse 'YYYY-MM-DD HH:MM[:SS]' as Manila wall-clock time (timezone-aware)."""
    for fmt in _FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=MANILA)
        except ValueError:
            continue
    raise ValueError(f"bad time '{text}', expected 'YYYY-MM-DD HH:MM' (Manila time)")


def format_manila(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, MANILA).strftime("%Y-%m-%d %H:%M:%S")


def now_manila() -> datetime:
    return datetime.now(MANILA)
