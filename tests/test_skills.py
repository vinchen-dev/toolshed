from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from shed.lib.errors import UsageError
from shed.registry import SHED_ROOT
from shed.skills import link_skills, list_skills


def make_skill(root: Path, name: str, description: str = "Use when testing.") -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n")
    return folder


def test_lists_skills_with_their_descriptions(tmp_path: Path) -> None:
    make_skill(tmp_path, "beta", "Second one.")
    make_skill(tmp_path, "alpha")
    make_skill(tmp_path, "_draft")
    (tmp_path / "notes").mkdir()
    skills = list_skills(tmp_path)
    assert [(s.name, s.description) for s in skills] == [
        ("alpha", "Use when testing."),
        ("beta", "Second one."),
    ]
    assert list_skills(tmp_path / "missing") == []


def test_links_once_and_leaves_other_folders_alone(tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    make_skill(skills, "alpha")
    make_skill(skills, "beta")
    target = tmp_path / "claude" / "skills"
    (target / "beta").mkdir(parents=True)  # someone's real folder, must not be replaced

    first = link_skills(skills, target)
    assert [(r.name, r.status) for r in first] == [("alpha", "linked"), ("beta", "conflict")]
    assert (target / "alpha").is_symlink() and (target / "alpha").resolve() == (skills / "alpha").resolve()
    assert not (target / "beta").is_symlink()

    second = link_skills(skills, target)
    assert [(r.name, r.status) for r in second] == [("alpha", "already"), ("beta", "conflict")]


def test_skills_in_group_folders_list_and_link_by_group(tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    make_skill(skills, "alpha")
    make_skill(skills / "PMS" / "auto", "beta")
    make_skill(skills / "PMS", "gamma")
    listed = [(s.name, s.group) for s in list_skills(skills)]
    assert listed == [("alpha", ""), ("beta", "PMS/auto"), ("gamma", "PMS")]
    target = tmp_path / "claude"
    assert [r.name for r in link_skills(skills, target, groups=["PMS"])] == ["gamma"]
    with pytest.raises(UsageError, match=r'Unknown skill group "nope" \(groups: PMS, PMS/auto\)'):
        list_skills(skills, groups=["nope"])
    (skills / "PMS" / "gamma").rename(skills / "gamma")  # the old link now dangles and gets replaced
    results = link_skills(skills, target)
    assert [(r.name, r.status) for r in results] == [(n, "linked") for n in ("alpha", "beta", "gamma")]
    assert (target / "gamma").resolve() == (skills / "gamma").resolve()


def shed(*args: str, skills_dir: Path) -> subprocess.CompletedProcess[str]:
    """Run the CLI against a skills folder of the test's own, so the repo's groups can come and go."""
    env = {**os.environ, "SHED_SKILLS_DIR": str(skills_dir)}
    cmd = [sys.executable, "-m", "shed.cli", *args]
    return subprocess.run(cmd, capture_output=True, text=True, cwd=SHED_ROOT, env=env, check=False)


def test_cli_lists_and_links_the_skills_folder(tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    make_skill(skills / "grp", "probe-skill", "Use when probing.")
    target = tmp_path / "target"

    listed = shed("skills", skills_dir=skills)
    assert listed.returncode == 0 and listed.stdout == "probe-skill  grp  Use when probing.\n"

    linked = shed("skills", "link", "--group", "grp", str(target), skills_dir=skills)
    assert linked.returncode == 0, linked.stderr
    assert "linked   probe-skill" in linked.stdout and (target / "probe-skill" / "SKILL.md").is_file()
    assert shed("skills", "link", "--group", "nope", str(target), skills_dir=skills).returncode == 2

    again = shed("skills", "link", str(target), skills_dir=skills)
    assert again.returncode == 0 and "already  probe-skill" in again.stdout


def test_cli_reports_conflicts_with_exit_1(tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    make_skill(skills, "probe-skill")
    target = tmp_path / "target"
    (target / "probe-skill").mkdir(parents=True)
    run = shed("skills", "link", str(target), skills_dir=skills)
    assert run.returncode == 1
    assert "conflict probe-skill" in run.stdout and "move it aside" in run.stderr
