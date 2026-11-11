from __future__ import annotations

from pathlib import Path

import pytest

from shed import ToolContext, ToolError
from shed.registry import load_tool, load_tool_module

module = load_tool_module("vault-recall")
TOOL = load_tool("vault-recall")

HUB = """---
type: hub
---

# PMS

Intro paragraph, not a note.

## Notes

### Specs (`specs/`)
- [[deposit-split-00-index]] — deposit split feature, 3 parts
- [[deposit-split-01-server]] \u2013 server rules for splitting a deposit

### Decisions (`decisions/`)
- [[use-clickhouse-for-reports]] - reports read ClickHouse, not Mongo

### Knowledge (`knowledge/`)
- [[gcash-callback-fields]] — what GCash sends back on a cash-in callback

### Not covered yet
- [[ignored-because-no-folder]] — should not appear

### Investigations (`investigations/`)
- [[gcash-proposal-stuck-split-error]] — GCash proposal stuck in VERIFIED with "not allow to split" error
- [[no-hook-note]]

### Graph (`graphify-auto/`)
- [[graph-node]] — never read

## Key Paths
- [[not-a-note-section]] — outside Notes
"""


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "vault"
    project = root / "Projects" / "PMS"
    for folder in ("specs", "decisions", "knowledge", "investigations", "graphify-auto"):
        (project / folder).mkdir(parents=True)
    (project / "PMS.md").write_text(HUB, encoding="utf-8")
    (project / "investigations" / "gcash-proposal-stuck-split-error.md").write_text(
        "# Stuck\n\nroot cause here\n"
    )
    (project / "knowledge" / "orphan-note.md").write_text("# Orphan\n")
    (project / "_Index_of_PMS.md").write_text("noise")
    monkeypatch.setenv("SHED_VAULT_DIR", str(root))
    return root


def ctx(cwd: Path) -> ToolContext:
    return ToolContext(cwd=cwd, log=lambda _: None)


def test_parses_the_hub_in_order_skipping_graph_and_unrouted_sections(vault: Path, tmp_path: Path) -> None:
    recall = TOOL.run(TOOL.input(project="PMS", limit=20), ctx(tmp_path))
    assert recall.has_hub and recall.total_notes == 6
    assert [(m.type, m.slug) for m in recall.matches] == [
        ("specs", "deposit-split-00-index"),
        ("specs", "deposit-split-01-server"),
        ("decisions", "use-clickhouse-for-reports"),
        ("knowledge", "gcash-callback-fields"),
        ("investigations", "gcash-proposal-stuck-split-error"),
        ("investigations", "no-hook-note"),
    ]
    assert recall.matches[0].is_index and not recall.matches[1].is_index
    assert recall.matches[2].hook == "reports read ClickHouse, not Mongo" and recall.matches[5].hook == ""
    assert recall.matches[4].path.endswith("Projects/PMS/investigations/gcash-proposal-stuck-split-error.md")


def test_scores_notes_by_query_words_and_filters_by_type(vault: Path, tmp_path: Path) -> None:
    recall = TOOL.run(TOOL.input(project="PMS", query="GCash split error"), ctx(tmp_path))
    assert [(m.slug, m.score) for m in recall.matches] == [
        ("gcash-proposal-stuck-split-error", 3),
        ("deposit-split-00-index", 1),
        ("deposit-split-01-server", 1),
        ("gcash-callback-fields", 1),
    ]
    only = TOOL.run(TOOL.input(project="PMS", query="gcash", type="knowledge"), ctx(tmp_path))
    assert [m.slug for m in only.matches] == ["gcash-callback-fields"]
    assert TOOL.run(TOOL.input(project="PMS", query="zzz"), ctx(tmp_path)).matches == []


def test_opens_one_note_by_slug(vault: Path, tmp_path: Path) -> None:
    recall = TOOL.run(
        TOOL.input(project="PMS", open="[[gcash-proposal-stuck-split-error]]", limit=1), ctx(tmp_path)
    )
    assert recall.note is not None and recall.note.content == "# Stuck\n\nroot cause here\n"
    assert recall.note.slug == "gcash-proposal-stuck-split-error"
    orphan = TOOL.run(TOOL.input(project="PMS", open="orphan-note"), ctx(tmp_path))  # not in the hub, on disk
    assert orphan.note is not None and orphan.note.path.endswith("knowledge/orphan-note.md")
    with pytest.raises(ToolError, match="no note 'missing'"):
        TOOL.run(TOOL.input(project="PMS", open="missing"), ctx(tmp_path))
    text = module.render(recall)
    assert text.startswith("# PMS: 1 of 6 notes\nspecs (index)    deposit-split-00-index — ")
    assert "## gcash-proposal-stuck-split-error (" in text and text.rstrip().endswith("root cause here")


def test_project_without_a_hub_stops_without_searching(vault: Path, tmp_path: Path) -> None:
    cwd = tmp_path / "Other"
    cwd.mkdir()
    recall = TOOL.run(TOOL.input(query="anything"), ctx(cwd))
    assert (recall.project, recall.has_hub, recall.matches, recall.note) == ("Other", False, [], None)
    assert module.render(recall).startswith("no notes for Other")
    explicit = TOOL.run(TOOL.input(project="PMS", vault=str(vault)), ctx(cwd))
    assert explicit.has_hub
