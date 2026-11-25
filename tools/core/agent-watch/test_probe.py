from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import pytest

from shed import ShResult
from shed.registry import load_tool_module

probing = importlib.import_module(f"{load_tool_module('agent-watch').__package__}.probe")


def test_lsof_is_found_where_the_system_keeps_it_when_the_path_does_not_have_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    system_copy = tmp_path / "lsof"
    system_copy.write_text("", encoding="utf-8")
    ran: list[str] = []

    def fake_sh(cmd: str, args: Any = (), **kwargs: Any) -> ShResult:
        ran.append(cmd)
        return ShResult(stdout="p4412\nn*:5173\n", stderr="", code=0)

    monkeypatch.setattr(probing, "sh", fake_sh)
    monkeypatch.setattr(probing, "LSOF_PATHS", (str(tmp_path / "absent"), str(system_copy)))
    monkeypatch.setattr(probing, "which", lambda name: None)  # the dashboard's launchd agent has no /usr/sbin
    assert probing.Probe().listening_ports() == {4412: [5173]}
    monkeypatch.setattr(probing, "which", lambda name: f"/opt/bin/{name}")
    probing.Probe().listening_ports()
    assert ran == [str(system_copy), "/opt/bin/lsof"]
