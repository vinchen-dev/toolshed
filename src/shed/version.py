"""The shed's version: the latest `v*` git tag in a checkout (plus distance, hash and a dirty marker), else
what the installed package says. `shed --version` prints it, run records carry it, the dashboard shows it."""

from __future__ import annotations

import functools
import importlib.metadata
import re
from pathlib import Path

from shed.lib.shell import sh

SHED_ROOT = Path(__file__).resolve().parents[2]
_DESCRIBE = re.compile(
    r"^v(?P<tag>\d+(?:\.\d+)*)(?:-(?P<distance>\d+)-g(?P<hash>[0-9a-f]+))?(?P<dirty>-dirty)?$"
)
_HASH_ONLY = re.compile(r"^(?P<hash>[0-9a-f]{4,40})(?P<dirty>-dirty)?$")


def from_describe(text: str) -> str:
    """`git describe --tags --dirty --always --match v*` output as a PEP 440 version string."""
    text = text.strip()
    if m := _DESCRIBE.match(text):
        out = m.group("tag")
        if m.group("distance") and m.group("distance") != "0":
            out += f".post{m.group('distance')}"
        local = [f"g{m.group('hash')}"] if m.group("distance") and m.group("distance") != "0" else []
        if m.group("dirty"):
            local.append("dirty")
        return out + (f"+{'.'.join(local)}" if local else "")
    if m := _HASH_ONLY.match(text):
        return f"0.0.0+g{m.group('hash')}" + (".dirty" if m.group("dirty") else "")
    return text or "0.0.0"


def in_checkout() -> bool:
    return (SHED_ROOT / ".git").exists()


def installed_version() -> str | None:
    try:
        return importlib.metadata.version("tool-shed")
    except importlib.metadata.PackageNotFoundError:
        return None


@functools.cache
def current() -> str:
    if in_checkout():
        result = sh(
            "git",
            ["describe", "--tags", "--dirty", "--always", "--match", "v*"],
            cwd=SHED_ROOT,
            allow_failure=True,
        )
        if result.code == 0 and result.stdout.strip():
            return from_describe(result.stdout)
    return installed_version() or "0.0.0"
