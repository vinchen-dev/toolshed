from __future__ import annotations

from typing import Any

import pytest

from shed import ShResult, version


@pytest.mark.parametrize(
    ("describe", "expected"),
    [
        ("v1.2.0", "1.2.0"),
        ("v1.2.0-3-gabc1234", "1.2.0.post3+gabc1234"),
        ("v1.2.0-dirty", "1.2.0+dirty"),
        ("v1.2.0-3-gabc1234-dirty", "1.2.0.post3+gabc1234.dirty"),
        ("abc1234", "0.0.0+gabc1234"),  # no tag yet: just the commit
        ("abc1234-dirty", "0.0.0+gabc1234.dirty"),
    ],
)
def test_git_describe_becomes_a_pep440_version(describe: str, expected: str) -> None:
    assert version.from_describe(describe) == expected


def test_current_reads_git_in_a_checkout_and_falls_back_to_package_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def git(cmd: str, args: Any = (), **kwargs: Any) -> ShResult:
        calls.append((cmd, *args))
        return ShResult(stdout="v0.3.0-1-gdeadbee\n", stderr="", code=0)

    monkeypatch.setattr(version, "sh", git)
    monkeypatch.setattr(version, "in_checkout", lambda: True)
    version.current.cache_clear()
    assert version.current() == "0.3.0.post1+gdeadbee"
    assert calls == [("git", "describe", "--tags", "--dirty", "--always", "--match", "v*")]

    monkeypatch.setattr(version, "in_checkout", lambda: False)
    monkeypatch.setattr(version, "installed_version", lambda: "0.2.0")
    version.current.cache_clear()
    assert version.current() == "0.2.0"

    monkeypatch.setattr(version, "installed_version", lambda: None)
    version.current.cache_clear()
    assert version.current() == "0.0.0"
    version.current.cache_clear()


def test_a_git_failure_in_a_checkout_falls_back_too(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(version, "in_checkout", lambda: True)
    monkeypatch.setattr(
        version, "sh", lambda *a, **k: ShResult(stdout="", stderr="fatal: no commits yet", code=128)
    )
    monkeypatch.setattr(version, "installed_version", lambda: "0.1.0")
    version.current.cache_clear()
    assert version.current() == "0.1.0"
    version.current.cache_clear()
