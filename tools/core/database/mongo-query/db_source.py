"""Where a database tool gets its connection: a TOML profile file or a repo's .env, never a URL flag.

A profile file (``~/.config/mongo.toml`` or ``~/.config/redis.toml``, chmod 600) holds one table per profile::

    [qat]
    url = "mongodb://user:pass@host:27017/pms"   # redis: "redis://:pass@host:6379/0"
    db = "pms"                                   # optional; redis: an index such as 0

An env file is read the way ``shed.lib.env`` reads one, into a private dict, so the value never reaches the
process environment or the result. Results show the host and database only.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from shed import ToolError
from shed.lib.env import load_env_file


@dataclass(frozen=True)
class Source:
    label: str
    """Where the URL came from: ``profile qat`` or ``.env:MONGODB_URI``; safe to show."""
    url: str
    db: str | int | None = None
    """The profile's database (a Mongo name or a Redis index), when the profile sets one."""


@dataclass(frozen=True)
class Profile:
    name: str
    url: str
    db: str | int | None = None


def load_profiles(path: Path) -> dict[str, Profile]:
    """Every profile in a TOML file; an absent file is simply no profiles."""
    if not path.is_file():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ToolError(f"cannot read {path}: {exc}") from exc
    profiles: dict[str, Profile] = {}
    for name, table in data.items():
        if not isinstance(table, dict):
            raise ToolError(f"{path}: [{name}] must be a table")
        url = table.get("url")
        if not isinstance(url, str) or not url:
            raise ToolError(f'{path}: [{name}] needs url = "..."')
        db = table.get("db")
        if db is not None and not isinstance(db, str | int):
            raise ToolError(f"{path}: [{name}] db must be a string or an integer")
        profiles[name] = Profile(name=name, url=url, db=db)
    return profiles


def resolve(
    *,
    profile: str | None,
    env_file: str | None,
    url_var: str | None,
    default_var: str,
    config_file: Path,
    cwd: Path,
) -> Source:
    """The connection a run should use, from ``--profile`` or ``--env-file``; errors name what is missing."""
    if profile and env_file:
        raise ToolError("give --profile or --env-file, not both")
    if env_file:
        path = (cwd / Path(env_file).expanduser()).resolve()
        if not path.is_file():
            raise ToolError(f"env file not found: {path}")
        values: dict[str, str] = {}
        load_env_file(path, values)
        var = url_var or default_var
        url = values.get(var, "")
        if not url:
            raise ToolError(f"{var} is not set in {path} (pick another variable with --url-var)")
        return Source(label=f"{path.name}:{var}", url=url)
    if not profile:
        raise ToolError(f"give --profile <name> (a table in {config_file}) or --env-file <path>")
    profiles = load_profiles(config_file)
    if profile not in profiles:
        found = ", ".join(sorted(profiles)) or "none"
        raise ToolError(f'no profile "{profile}" in {config_file} (profiles: {found})')
    chosen = profiles[profile]
    return Source(label=f"profile {profile}", url=chosen.url, db=chosen.db)


def describe(url: str) -> tuple[str, str]:
    """(hosts, database path) of a connection URL with the credentials stripped, for display only."""
    parts = urlsplit(url)
    hosts = parts.netloc.rpartition("@")[2]
    database = parts.path.strip("/").split("/", 1)[0] if parts.path else ""
    return hosts, database
