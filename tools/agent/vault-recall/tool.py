"""Find the one Obsidian note that answers a question, the way the vault's recall protocol says.

The project hub (Projects/<project>/<project>.md) routes every note with a one-line hook under its type;
this tool reads only the hub, scores the hooks against the query, and opens the single note asked for.
It never lists folders, reads _Index_of_* files or the generated Graph section.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from shed import Tool, ToolContext, ToolError

DEFAULT_VAULT = Path.home() / "Obsidian" / "Claude"
NoteType = Literal["specs", "decisions", "knowledge", "plans", "reference", "investigations"]
SKIPPED_FOLDERS = {"graphify-auto"}
_SECTION = re.compile(r"^###\s+(?P<title>.+?)\s*\(`(?P<folder>[^`]+?)/?`\)\s*$")
_ENTRY = re.compile(
    r"^-\s*\[\[(?P<slug>[^\]|#]+)(?:[|#][^\]]*)?\]\]\s*(?:(?:\u2014|\u2013|-)\s*(?P<hook>.*))?$"
)
_WORD = re.compile(r"[a-z0-9]+")


class Input(BaseModel):
    project: str | None = Field(
        None, description="Project name under the vault's Projects/ (default: the current folder's name)"
    )
    query: str | None = Field(None, description="Keywords to match against each note's slug and hook")
    type: NoteType | None = Field(None, description="Keep only notes of this type")
    open: str | None = Field(None, description="Slug of the note to return in full (with or without [[ ]])")
    limit: int = Field(10, ge=1, description="Matches to list")
    vault: str | None = Field(None, description="Vault folder (default $SHED_VAULT_DIR or ~/Obsidian/Claude)")


class Match(BaseModel):
    type: str
    slug: str
    hook: str
    score: int
    path: str
    is_index: bool


class Note(BaseModel):
    slug: str
    path: str
    content: str


class Recall(BaseModel):
    project: str
    hub: str
    has_hub: bool
    total_notes: int
    matches: list[Match]
    note: Note | None


def vault_dir(override: str | None, cwd: Path) -> Path:
    if override:
        return (cwd / Path(override).expanduser()).resolve()
    env = os.environ.get("SHED_VAULT_DIR")
    return Path(env).expanduser() if env else DEFAULT_VAULT


def parse_hub(text: str, project_dir: Path) -> list[Match]:
    """Every routed note in hub order, from the ### sections under ## Notes that name a folder."""
    entries: list[Match] = []
    folder: str | None = None
    in_notes = False
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("## "):
            in_notes = line[3:].strip().lower() == "notes"
            folder = None
            continue
        if not in_notes:
            continue
        if line.startswith("### "):
            section = _SECTION.match(line)
            folder = (
                section.group("folder")
                if section and section.group("folder") not in SKIPPED_FOLDERS
                else None
            )
            continue
        entry = _ENTRY.match(line) if folder else None
        if entry:
            slug = entry.group("slug").strip()
            entries.append(
                Match(
                    type=folder or "",
                    slug=slug,
                    hook=(entry.group("hook") or "").strip(),
                    score=0,
                    path=str(project_dir / (folder or "") / f"{slug}.md"),
                    is_index=slug.endswith("-00-index"),
                )
            )
    return entries


def score(entry: Match, words: list[str]) -> int:
    haystack = f"{entry.slug} {entry.hook}".lower()
    return sum(1 for word in words if word in haystack)


def find_note(project_dir: Path, slug: str, entries: list[Match]) -> Path:
    """The note's file: where the hub routes it, else the first type folder that holds it."""
    slug = slug.strip().strip("[]").split("|")[0].strip()
    for entry in entries:
        if entry.slug == slug and Path(entry.path).is_file():
            return Path(entry.path)
    for folder in sorted(p for p in project_dir.iterdir() if p.is_dir() and p.name not in SKIPPED_FOLDERS):
        candidate = folder / f"{slug}.md"
        if candidate.is_file():
            return candidate
    raise ToolError(f"no note {slug!r} under {project_dir} (hub lists {len(entries)} notes)")


def run(params: Input, ctx: ToolContext) -> Recall:
    project = params.project or ctx.cwd.resolve().name
    project_dir = vault_dir(params.vault, ctx.cwd) / "Projects" / project
    hub = project_dir / f"{project}.md"
    if not hub.is_file():
        ctx.log(f"no hub at {hub}: the project has no notes")
        return Recall(project=project, hub=str(hub), has_hub=False, total_notes=0, matches=[], note=None)
    entries = parse_hub(hub.read_text(encoding="utf-8", errors="replace"), project_dir)
    candidates = [e for e in entries if params.type is None or e.type == params.type]
    words = list(dict.fromkeys(w for w in _WORD.findall((params.query or "").lower()) if len(w) >= 2))
    if words:
        scored = [e.model_copy(update={"score": score(e, words)}) for e in candidates]
        candidates = sorted((e for e in scored if e.score), key=lambda e: -e.score)
    matches = candidates[: params.limit]
    note = None
    if params.open:
        path = find_note(project_dir, params.open, entries)
        note = Note(
            slug=path.stem, path=str(path), content=path.read_text(encoding="utf-8", errors="replace")
        )
    ctx.log(
        f"{hub.name}: {len(entries)} notes, {len(matches)} shown" + (f", opened {note.slug}" if note else "")
    )
    return Recall(
        project=project, hub=str(hub), has_hub=True, total_notes=len(entries), matches=matches, note=note
    )


def render(recall: Recall) -> str:
    if not recall.has_hub:
        return f"no notes for {recall.project} (no hub at {recall.hub})\n"
    lines = [f"# {recall.project}: {len(recall.matches)} of {recall.total_notes} notes"]
    for m in recall.matches:
        tag = f"{m.type}{' (index)' if m.is_index else ''}"
        lines.append(f"{tag:16} {m.slug} — {m.hook}" + (f" ({m.score})" if m.score else ""))
    if recall.note:
        lines += ["", f"## {recall.note.slug} ({recall.note.path})", "", recall.note.content.rstrip()]
    return "\n".join(lines) + "\n"


TOOL = Tool(
    name="vault-recall",
    description=(
        "Recall project knowledge from the Obsidian vault the way its protocol says: read the project hub, "
        "rank its note hooks against your keywords, and open exactly one note. Use it before debugging or "
        "deciding something in a project that has a hub, to check whether it was already investigated or "
        "decided; it never greps the vault."
    ),
    input=Input,
    run=run,
    render=render,
    examples=(
        'shed vault-recall --query "gcash split error"',
        "shed vault-recall --project PMS2.0_PH --type investigations --limit 5",
        "shed vault-recall --project PMS2.0_PH --open gcash-proposal-stuck-split-error --json",
    ),
    version="0.1.0",
)
