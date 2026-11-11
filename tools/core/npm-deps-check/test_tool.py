from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from shed import ShResult, ToolContext, ToolError
from shed.registry import load_tool, load_tool_module

module = load_tool_module("npm-deps-check")
TOOL = load_tool("npm-deps-check")

NPM_OUTDATED = {
    "lodash": {
        "current": "4.17.20",
        "wanted": "4.17.21",
        "latest": "4.17.21",
        "type": "dependencies",
        "location": "node_modules/lodash",
    },
    "jest": {"current": "29.0.0", "wanted": "29.7.0", "latest": "30.0.0", "type": "devDependencies"},
}
NPM_AUDIT = {
    "auditReportVersion": 2,
    "vulnerabilities": {
        "minimatch": {
            "name": "minimatch",
            "severity": "high",
            "via": [{"title": "ReDoS in minimatch", "name": "minimatch"}],
            "range": "<3.0.5",
            "fixAvailable": {"name": "jest", "version": "30.0.0", "isSemVerMajor": True},
        },
        "lodash": {
            "name": "lodash",
            "severity": "critical",
            "via": ["minimatch"],
            "range": "<4.17.21",
            "fixAvailable": True,
        },
    },
    "metadata": {
        "vulnerabilities": {"info": 0, "low": 1, "moderate": 0, "high": 1, "critical": 1, "total": 3}
    },
}
PNPM_OUTDATED = {
    "react": {
        "current": "18.2.0",
        "latest": "19.0.0",
        "wanted": "18.3.1",
        "isDeprecated": False,
        "dependencyType": "dependencies",
    },
}
PNPM_AUDIT = {
    "advisories": {
        "1": {
            "module_name": "semver",
            "severity": "moderate",
            "vulnerable_versions": "<7.5.2",
            "patched_versions": ">=7.5.2",
            "title": "semver vulnerable to ReDoS",
        }
    },
    "metadata": {"vulnerabilities": {"info": 0, "low": 0, "moderate": 1, "high": 0, "critical": 0}},
}


class FakeSh:
    def __init__(self, responses: dict[tuple[str, str], ShResult]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, list[str]]] = []

    def __call__(self, cmd: str, args: list[str], **kwargs: Any) -> ShResult:
        self.calls.append((cmd, list(args)))
        return self.responses[(cmd, args[0])]


def project(root: Path, *lockfiles: str) -> Path:
    root.mkdir(exist_ok=True)
    (root / "package.json").write_text('{"name": "app"}\n')
    for name in lockfiles:
        (root / name).write_text("")
    return root


def fake(monkeypatch: pytest.MonkeyPatch, responses: dict[tuple[str, str], ShResult]) -> FakeSh:
    sh_fake = FakeSh(responses)
    monkeypatch.setattr(module, "sh", sh_fake)
    return sh_fake


def ctx(cwd: Path) -> ToolContext:
    return ToolContext(cwd=cwd, log=lambda _: None)


def test_npm_findings_come_back_with_exit_1(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    project(tmp_path / "app", "package-lock.json")
    sh_fake = fake(
        monkeypatch,
        {
            ("npm", "outdated"): ShResult(json.dumps(NPM_OUTDATED), "", 1),
            ("npm", "audit"): ShResult(json.dumps(NPM_AUDIT), "", 1),
        },
    )
    report = TOOL.run(TOOL.input(path="app", production=True), ctx(tmp_path))
    assert sh_fake.calls == [
        ("npm", ["outdated", "--json", "--omit=dev"]),
        ("npm", ["audit", "--json", "--omit=dev"]),
    ]
    assert (report.manager, report.lockfile, report.audit_available) == ("npm", "package-lock.json", True)
    assert [(o.name, o.current, o.wanted, o.latest, o.type) for o in report.outdated] == [
        ("jest", "29.0.0", "29.7.0", "30.0.0", "devDependencies"),
        ("lodash", "4.17.20", "4.17.21", "4.17.21", "dependencies"),
    ]
    assert [(v.name, v.severity, v.fix_available, v.via) for v in report.vulnerabilities] == [
        ("lodash", "critical", True, ["minimatch"]),
        ("minimatch", "high", "major", ["ReDoS in minimatch"]),
    ]
    assert report.counts.model_dump() == {"critical": 1, "high": 1, "moderate": 0, "low": 1, "info": 0}
    assert TOOL.render is not None
    text = TOOL.render(report)
    assert "vulnerabilities: 1 critical, 1 high, 0 moderate, 1 low, 0 info" in text
    assert "  high     minimatch  <3.0.5  fix: major  via: ReDoS in minimatch" in text
    assert "  jest  29.0.0 -> 29.7.0 (latest 30.0.0)  devDependencies" in text


def test_pnpm_shapes_and_truncation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = project(tmp_path, "pnpm-lock.yaml")
    sh_fake = fake(
        monkeypatch,
        {
            ("pnpm", "outdated"): ShResult(
                json.dumps(
                    PNPM_OUTDATED | {"vue": [{"current": "3.0.0", "latest": "3.5.0", "wanted": "3.0.1"}]}
                ),
                "",
                1,
            ),
            ("pnpm", "audit"): ShResult(json.dumps(PNPM_AUDIT), "", 1),
        },
    )
    report = TOOL.run(TOOL.input(limit=1), ctx(root))
    assert sh_fake.calls[0] == ("pnpm", ["outdated", "--format", "json"])
    assert (report.outdated_total, report.outdated_truncated, [o.name for o in report.outdated]) == (
        2,
        True,
        ["react"],
    )
    assert report.outdated[0].type == "dependencies"
    assert [(v.name, v.severity, v.range, v.fix_available, v.via) for v in report.vulnerabilities] == [
        ("semver", "moderate", "<7.5.2", True, ["semver vulnerable to ReDoS"])
    ]
    assert report.counts.moderate == 1


def test_clean_project_and_skipped_checks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = project(tmp_path, "package-lock.json")
    sh_fake = fake(monkeypatch, {("npm", "outdated"): ShResult("", "", 0)})
    report = TOOL.run(TOOL.input(audit=False), ctx(root))
    assert sh_fake.calls == [("npm", ["outdated", "--json"])]
    assert report.outdated == [] and report.vulnerabilities == [] and report.audit_available is False


def test_yarn_project_without_lock_records_the_audit_as_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = project(tmp_path, "yarn.lock")
    enolock = {
        "error": {"code": "ENOLOCK", "summary": "This command requires an existing lockfile.", "detail": ""}
    }
    fake(
        monkeypatch,
        {
            ("npm", "outdated"): ShResult("{}", "", 0),
            ("npm", "audit"): ShResult(json.dumps(enolock), "npm ERR! code ENOLOCK", 1),
        },
    )
    report = TOOL.run(TOOL.input(), ctx(root))
    assert report.lockfile == "yarn.lock" and report.audit_available is False
    assert any("yarn.lock project" in n for n in report.notes)
    assert any(
        "audit unavailable: This command requires an existing lockfile. (ENOLOCK)" in n for n in report.notes
    )


def test_a_registry_error_fails_the_outdated_check(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = project(tmp_path, "package-lock.json")
    e401 = {
        "error": {
            "code": "E401",
            "summary": 'Unable to authenticate, need: BASIC realm="Nexus"',
            "detail": "",
        }
    }
    fake(monkeypatch, {("npm", "outdated"): ShResult(json.dumps(e401), "", 0)})
    with pytest.raises(ToolError, match=r"npm outdated failed: Unable to authenticate.*\(E401\)"):
        TOOL.run(TOOL.input(audit=False), ctx(root))


def test_real_failures_and_missing_package_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    with pytest.raises(ToolError, match=r"no package\.json"):
        TOOL.run(TOOL.input(), ctx(tmp_path))
    root = project(tmp_path)
    fake(monkeypatch, {("npm", "outdated"): ShResult("", "npm ERR! missing node_modules", 1)})
    with pytest.raises(ToolError, match="npm outdated failed: npm ERR! missing node_modules"):
        TOOL.run(TOOL.input(audit=False), ctx(root))
