from __future__ import annotations

from pathlib import Path

import pytest

from shed import ToolError
from shed.lib.env import load_env_file, require_env


def test_load_env_file_does_not_override(tmp_path: Path) -> None:
    env_file = tmp_path / "x.env"
    env_file.write_text("# comment\nA='from-file'\nB=from-file\n", encoding="utf-8")
    environ = {"B": "from-env"}
    load_env_file(env_file, environ)
    assert environ == {"A": "from-file", "B": "from-env"}


def test_load_env_file_ignores_missing_file(tmp_path: Path) -> None:
    environ: dict[str, str] = {}
    load_env_file(tmp_path / "absent.env", environ)
    assert environ == {}


def test_require_env_names_what_is_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PRESENT", "1")
    monkeypatch.delenv("ABSENT", raising=False)
    with pytest.raises(ToolError, match="Missing ABSENT"):
        require_env("PRESENT", "ABSENT", env_file=tmp_path / "absent.env")
    assert require_env("PRESENT", env_file=tmp_path / "absent.env") == {"PRESENT": "1"}
