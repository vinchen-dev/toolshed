"""Shared by the agent-watch tests: a fake home tree with a fake probe behind the tool."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import pytest

from shed.registry import load_tool_module


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    fakes = importlib.import_module(f"{load_tool_module('agent-watch').__package__}.fakes")
    return fakes.Home(tmp_path, monkeypatch)
