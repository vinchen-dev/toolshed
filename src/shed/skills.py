"""Skills that ride along with the tools: prose guides for agents, linked into ~/.claude/skills."""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from shed.lib.errors import UsageError
from shed.registry import SHED_ROOT

SKILLS_DIR = Path(os.environ.get("SHED_SKILLS_DIR") or SHED_ROOT / "skills")
CLAUDE_SKILLS_DIR = Path.home() / ".claude" / "skills"
_DESCRIPTION = re.compile(r"^description:\s*(.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class Skill:
    name: str
    path: Path
    description: str
    group: str = ""
    """Folder between skills/ and the skill, "" at the top level."""


@dataclass(frozen=True, slots=True)
class LinkResult:
    name: str
    status: Literal["linked", "already", "conflict"]
    target: Path


def list_skills(skills_dir: Path = SKILLS_DIR, groups: Sequence[str] | None = None) -> list[Skill]:
    """Folders holding a SKILL.md, at any depth under skills/; ``_``-prefixed folders are skipped.

    ``groups`` keeps only the skills directly inside those folders.
    """
    skills: list[Skill] = []

    def walk(folder: Path) -> None:
        for child in sorted(folder.iterdir()):
            if not child.is_dir() or child.name.startswith(("_", ".")):
                continue
            if not (child / "SKILL.md").is_file():
                walk(child)
                continue
            match = _DESCRIPTION.search((child / "SKILL.md").read_text(encoding="utf-8"))
            rel = child.parent.relative_to(skills_dir)
            group = "" if rel == Path(".") else rel.as_posix()
            skills.append(Skill(child.name, child, match.group(1) if match else "", group))

    if skills_dir.is_dir():
        walk(skills_dir)
    skills.sort(key=lambda s: s.name)
    if groups is None:
        return skills
    wanted = [g.strip("/") for g in groups]
    known = sorted({s.group for s in skills if s.group})
    for group in wanted:
        if group not in known:
            raise UsageError(f'Unknown skill group "{group}" (groups: {", ".join(known) or "none"})')
    return [s for s in skills if s.group in wanted]


def link_skills(
    skills_dir: Path = SKILLS_DIR, target_dir: Path = CLAUDE_SKILLS_DIR, groups: Sequence[str] | None = None
) -> list[LinkResult]:
    """Symlink each skill folder into target_dir. Existing links are kept; anything else is left untouched."""
    target_dir.mkdir(parents=True, exist_ok=True)
    results: list[LinkResult] = []
    for skill in list_skills(skills_dir, groups):
        target = target_dir / skill.name
        source = skill.path.resolve()
        if target.is_symlink() and not target.exists():
            target.unlink()  # a link left dangling by a skill that moved folders
        if target.is_symlink() and target.resolve() == source:
            status: Literal["linked", "already", "conflict"] = "already"
        elif target.exists() or target.is_symlink():
            status = "conflict"
        else:
            target.symlink_to(source, target_is_directory=True)
            status = "linked"
        results.append(LinkResult(skill.name, status, target))
    return results
