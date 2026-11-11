from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from shed import ToolContext, ToolError, sh
from shed.registry import load_tool, load_tool_module

module = load_tool_module("deployed-version")
sls = load_tool_module("sls-logs")
TOOL = load_tool("deployed-version")

V1 = "20261110-0812-df43b8fffe"
V2 = "20261109-1700-0a1b2c3d4e"


def line(time: str, container: str, content: str) -> Any:
    return sls.LogLine(time=time, container=container, content=content)


def result(lines: list[Any], truncated: bool = False) -> Any:
    return sls.LogResult(
        project="p",
        logstore="l",
        query=module.QUERY,
        start="2026-11-07 08:00:00",
        end="2026-11-10 08:00:00",
        count=len(lines),
        truncated=truncated,
        lines=lines,
    )


QAT_LINES = [
    line("2026-11-09 17:00:01", "pms-a", f"[Process 1] Initializing src/app.js (version: {V2})"),
    line("2026-11-09 17:00:02", "pms-a", "[Process 1] NODE_VERSION=v20.11.0, NODE_MODULE_VERSION=115"),
    line("2026-11-10 08:12:00", "pms-a", f"[Process 1] Initializing src/app.js (version: {V1})"),
    line("2026-11-10 08:12:01", "pms-a", "[Process 1] NODE_VERSION=v22.14.0, NODE_MODULE_VERSION=127"),
    line(
        "2026-11-10 08:12:30",
        "pms-b",
        f"[Process 2] Initializing src/scheduler/pulsarRunner.js (version: {V1})",
    ),
    line("2026-11-10 08:12:31", "pms-b", "[Process 2] NODE_VERSION=v22.14.0, NODE_MODULE_VERSION=127"),
    line("2026-11-10 09:00:00", "pms-a", "[Process 1] Initializing merchant cache"),  # no version: ignored
]
PROD_LINES = [
    line("2026-11-09 17:00:01", "prod-a", f"[Process 1] Initializing src/app.js (version: {V2})"),
    line("2026-11-10 08:12:00", "prod-b", f"[Process 1] Initializing src/app.js (version: {V1})"),
]


class FakeSls:
    def __init__(self, pulls: dict[str, Any]) -> None:
        self.pulls = pulls
        self.calls: list[dict[str, Any]] = []

    def input(self, **kwargs: Any) -> dict[str, Any]:
        return kwargs

    def run(self, params: dict[str, Any], ctx: ToolContext) -> Any:
        self.calls.append(params)
        pull = self.pulls[params["project"]]
        if isinstance(pull, Exception):
            raise pull
        return pull


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeSls:
    sls_fake = FakeSls(
        {
            "platform-qat-aliyun-logs": result(QAT_LINES),
            "platform-prod-aliyun-logs": result(PROD_LINES),
        }
    )
    monkeypatch.setattr(module, "_sls", lambda: sls_fake)
    return sls_fake


def ctx(cwd: Path) -> ToolContext:
    return ToolContext(cwd=cwd, log=lambda _: None)


def _git(cwd: Path, *args: str) -> None:
    sh("git", ["-c", "user.name=Test", "-c", "user.email=test@example.com", *args], cwd=cwd)


def test_reports_both_environments_newest_container_first(fake: FakeSls, tmp_path: Path) -> None:
    out = TOOL.run(TOOL.input(repo=str(tmp_path)), ctx(tmp_path))
    assert [c["project"] for c in fake.calls] == ["platform-qat-aliyun-logs", "platform-prod-aliyun-logs"]
    assert fake.calls[0]["minutes"] == 72 * 60 and fake.calls[0]["query"] == module.QUERY
    qat, prod = out.envs
    assert (qat.version, qat.since, qat.containers, qat.node_version) == (
        V1,
        "2026-11-10 08:12:30",
        2,
        "v22.14.0",
    )
    assert qat.mixed is False and qat.versions[0].containers == 2
    assert qat.scripts == ["src/app.js", "src/scheduler/pulsarRunner.js"]
    assert prod.mixed is True
    assert [(g.version, g.containers) for g in prod.versions] == [(V1, 1), (V2, 1)]
    assert out.local is None and qat.matches_head is None
    assert any("not a git repository" in n for n in out.notes)


def test_notes_an_empty_window_and_a_truncated_pull(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = FakeSls({"platform-prod-aliyun-logs": result([line("t", "c", "noise")], truncated=True)})
    monkeypatch.setattr(module, "_sls", lambda: fake)
    out = TOOL.run(TOOL.input(env="prod", hours=6, limit=1, repo=str(tmp_path)), ctx(tmp_path))
    assert out.envs[0].version is None and out.envs[0].containers == 0
    assert any("no start-up lines in the last 6h on prod" in n for n in out.notes)
    assert any("stopped at --limit 1" in n for n in out.notes)
    assert TOOL.render is not None
    assert "PROD  (no start-up lines in the last 6h)" in TOOL.render(out)


def test_sls_failures_name_the_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = FakeSls({"platform-qat-aliyun-logs": ToolError("Missing ALIBABA_CLOUD_ACCESS_KEY_ID")})
    monkeypatch.setattr(module, "_sls", lambda: fake)
    with pytest.raises(ToolError, match="qat: Missing ALIBABA"):
        TOOL.run(TOOL.input(env="qat"), ctx(tmp_path))


def test_compares_with_the_local_checkout(fake: FakeSls, tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q", "-b", "release-4.0.120")
    (tmp_path / "server").mkdir()
    (tmp_path / "server" / "package.json").write_text(
        '{"name": "PMS", "version": "1.21.1"}\n', encoding="utf-8"
    )
    _git(tmp_path, "add", "server")
    _git(tmp_path, "commit", "-q", "-m", "first")
    _git(tmp_path, "tag", "v4.0.98")
    sha = sh("git", ["rev-parse", "HEAD"], cwd=tmp_path).stdout.strip()
    # Make the QAT version carry this checkout's sha so the match is exercised.
    fake.pulls["platform-qat-aliyun-logs"] = result(
        [
            line(
                "2026-11-10 08:12:00",
                "pms-a",
                f"[Process 1] Initializing src/app.js (version: 20261110-0812-{sha[:10]})",
            )
        ]
    )

    out = TOOL.run(TOOL.input(env="qat", repo="."), ctx(tmp_path))
    assert out.local is not None
    assert (out.local.branch, out.local.latest_tag, out.local.package_version) == (
        "release-4.0.120",
        "v4.0.98",
        "1.21.1",
    )
    assert out.local.head == sha[:7] and out.envs[0].matches_head is True
    assert TOOL.render is not None
    text = TOOL.render(out)
    assert "(= local HEAD)" in text
    assert f"local release-4.0.120 @ {sha[:7]}  tag v4.0.98  package.json 1.21.1" in text


def test_renders_mixed_versions(fake: FakeSls, tmp_path: Path) -> None:
    assert TOOL.render is not None
    text = TOOL.render(TOOL.run(TOOL.input(env="prod", repo=str(tmp_path)), ctx(tmp_path)))
    assert text.startswith(f"PROD  {V1}  since 2026-11-10 08:12:00  2 containers")
    assert f"MIXED: {V1} x1 (last 2026-11-10 08:12:00), {V2} x1 (last 2026-11-09 17:00:01)" in text


def test_one_entry_holding_a_whole_pm2_burst() -> None:
    blob = (
        "20:06:18 5|checkLarkChatHistory | [Process 24716] NODE_VERSION=v22.23.4, NODE_MODULE_VERSION=127\n"
        f"20:06:18 5|checkLarkChatHistory | [Process 24716] Initializing ./lark/check.js (version: {V1})\n"
        f"20:06:19 6|other | [Process 24717] Initializing ./lark/other.js (version: {V1})\n"
    )
    report = module.summarise("qat", [line("2026-11-10 20:06:18", "pay-scheduler-common-2", blob)], None)
    assert (report.version, report.node_version, report.containers) == (V1, "v22.23.4", 1)
    assert report.scripts == ["./lark/check.js", "./lark/other.js"]


def test_hex_match_ignores_the_date_part() -> None:
    report = module.summarise("qat", [line("t", "c", f"Initializing x (version: {V1})")], "20261110aaaa")
    assert report.matches_head is False
    report = module.summarise("qat", [line("t", "c", f"Initializing x (version: {V1})")], "df43b8fffe123")
    assert report.matches_head is True
