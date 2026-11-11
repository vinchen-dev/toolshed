from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from shed import ShResult, update
from shed.cli import main
from shed.registry import SHED_ROOT


def tools_tree(root: Path, *groups: str) -> Path:
    """A tools/ folder with one tool per group, enough for group discovery."""
    tools = root / "tools"
    for n, group in enumerate(groups):
        folder = tools / group / f"tool{n}"
        folder.mkdir(parents=True)
        (folder / "tool.py").write_text("TOOL = None\n")
    return tools


class Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...], dict[str, Any]]] = []

    def __call__(self, cmd: str, args: Any = (), **kwargs: Any) -> ShResult:
        self.calls.append((cmd, tuple(args), kwargs))
        return ShResult(stdout="", stderr="", code=0)

    def lines(self) -> list[str]:
        return [" ".join([cmd, *args]) for cmd, args, _ in self.calls]


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    rec = Recorder()
    monkeypatch.setattr(update, "sh", rec)
    monkeypatch.setattr(update, "which", lambda name: f"/usr/local/bin/{name}")
    return rec


def test_update_reinstalls_links_skills_and_registers_every_group(recorder: Recorder, tmp_path: Path) -> None:
    tools = tools_tree(tmp_path, "core", "SN", "SN/autorunner", "research")
    skills = tmp_path / "skills" / "research" / "product-research"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text("---\nname: product-research\ndescription: x\n---\n")
    target = tmp_path / "claude-skills"
    said: list[str] = []

    code = update.run_update(
        tools_dir=tools, skills_dir=tmp_path / "skills", skills_target=target, browser=True, out=said.append
    )

    assert code == 0
    assert recorder.lines()[:3] == [
        "uv sync",
        f"uv tool install --editable {SHED_ROOT} --reinstall",
        "uv run playwright install chromium",
    ]
    assert all(call[2].get("cwd") == SHED_ROOT for call in recorder.calls[:3])
    assert (target / "product-research").is_symlink()
    expected = {
        "core": "tool-shed",
        "SN": "tool-shed-sn",
        "SN/autorunner": "tool-shed-autorunner",
        "research": "tool-shed-research",
        "jobs": "tool-shed-jobs",
    }
    for group, name in expected.items():
        assert f"claude mcp remove --scope user {name}" in recorder.lines()
        assert f"claude mcp add --scope user {name} -- shed mcp --group {group}" in recorder.lines()
        assert f"codex mcp remove {name}" in recorder.lines()
        assert f"codex mcp add {name} -- shed mcp --group {group}" in recorder.lines()
    removes = [c for c in recorder.calls if c[1][:2] == ("mcp", "remove")]
    assert removes and all(c[2].get("allow_failure") for c in removes)
    assert any("claude: registered tool-shed-research" in line for line in said)
    assert any("codex: registered tool-shed-research" in line for line in said)
    assert said[-1] == "Next: run `shed start` so the dashboard and scheduler load the new code."


def test_update_skips_claude_registration_when_the_cli_is_missing(
    recorder: Recorder, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(update, "which", lambda name: None if name == "claude" else f"/usr/local/bin/{name}")
    tools = tools_tree(tmp_path, "core")
    said: list[str] = []
    update.run_update(
        tools_dir=tools,
        skills_dir=tmp_path / "skills",
        skills_target=tmp_path / "s",
        browser=False,
        out=said.append,
    )
    assert not any(cmd == "claude" for cmd, _, _ in recorder.calls)
    assert "codex mcp add tool-shed -- shed mcp --group core" in recorder.lines()
    assert "uv run playwright install chromium" not in recorder.lines()
    assert any("claude" in line and "not found" in line for line in said)


def test_update_skips_codex_when_its_cli_is_missing(
    recorder: Recorder, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(update, "which", lambda name: None if name == "codex" else f"/usr/local/bin/{name}")
    said: list[str] = []
    update.run_update(
        tools_dir=tools_tree(tmp_path, "core"),
        skills_dir=tmp_path / "skills",
        skills_target=tmp_path / "s",
        browser=False,
        out=said.append,
    )
    assert not any(cmd == "codex" for cmd, _, _ in recorder.calls)
    assert "claude mcp add --scope user tool-shed -- shed mcp --group core" in recorder.lines()
    assert any("codex" in line and "not found" in line for line in said)


def test_a_failed_registration_is_reported_and_fails_the_run(
    recorder: Recorder, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing(cmd: str, args: Any = (), **kwargs: Any) -> ShResult:
        recorder.calls.append((cmd, tuple(args), kwargs))
        if cmd == "codex" and tuple(args[:2]) == ("mcp", "add"):
            return ShResult(stdout="", stderr="config.toml is read-only", code=1)
        return ShResult(stdout="", stderr="", code=0)

    monkeypatch.setattr(update, "sh", failing)
    said: list[str] = []
    code = update.run_update(
        tools_dir=tools_tree(tmp_path, "core"),
        skills_dir=tmp_path / "skills",
        skills_target=tmp_path / "s",
        browser=False,
        out=said.append,
    )
    assert code == 1
    assert any("codex: failed to register tool-shed" in line and "read-only" in line for line in said)


def test_server_names_use_the_last_folder_unless_two_groups_share_it(tmp_path: Path) -> None:
    assert update.server_names(["core", "SN", "SN/autorunner", "research", "jobs"]) == {
        "core": "tool-shed",
        "SN": "tool-shed-sn",
        "SN/autorunner": "tool-shed-autorunner",
        "research": "tool-shed-research",
        "jobs": "tool-shed-jobs",
    }
    assert update.server_names(["A/shared", "B/shared"]) == {
        "A/shared": "tool-shed-a-shared",
        "B/shared": "tool-shed-b-shared",
    }


def test_dry_run_prints_the_plan_without_running_anything(
    recorder: Recorder, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["update", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "uv tool install --editable" in out
    assert "claude mcp add --scope user tool-shed -- shed mcp --group core" in out
    assert "codex mcp add tool-shed -- shed mcp --group core" in out
    assert recorder.calls == []


def test_update_installs_cloudflared_only_when_missing_and_brew_is_present(
    recorder: Recorder, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        update, "which", lambda name: None if name == "cloudflared" else f"/opt/homebrew/bin/{name}"
    )
    update.run_update(
        tools_dir=tools_tree(tmp_path, "core"),
        skills_dir=tmp_path / "skills",
        skills_target=tmp_path / "s",
        browser=False,
        out=lambda _: None,
    )
    assert "brew install cloudflared" in recorder.lines()
    recorder.calls.clear()
    monkeypatch.setattr(update, "which", lambda name: f"/opt/homebrew/bin/{name}")
    update.run_update(
        tools_dir=tools_tree(tmp_path / "again", "core"),
        skills_dir=tmp_path / "skills",
        skills_target=tmp_path / "s",
        browser=False,
        out=lambda _: None,
    )
    assert "brew install cloudflared" not in recorder.lines()
