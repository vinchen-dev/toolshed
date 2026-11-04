from __future__ import annotations

import importlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from shed import ToolContext, ToolError
from shed.registry import load_tool, load_tool_module

module = load_tool_module("bdo-incidents")
sls_module = load_tool_module("sls-logs")
TOOL = load_tool("bdo-incidents")
FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 25, 12, 0, 0)
CTX = ToolContext(cwd=FIXTURES, log=lambda _: None)
QAT_TEXT = (FIXTURES / "qat.txt").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(module, "now_manila", lambda: NOW)
    monkeypatch.setattr(module, "sleep", lambda _: None)
    monkeypatch.setattr(sls_module, "ENV_FILE", tmp_path / "absent.env")
    monkeypatch.setenv("ALIBABA_CLOUD_ACCESS_KEY_ID", "id")
    monkeypatch.setenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "secret")


def raw(*envs: str) -> list[str]:
    return [f"{env}={env}.txt" for env in envs]


def test_parse_splits_joined_records_strips_ansi_and_dedupes() -> None:
    events = module.parse(QAT_TEXT)
    kinds = [(e.time.strftime("%H:%M:%S"), e.kind) for e in events]
    assert ("09:58:00", "LOC") in kinds and ("09:58:01", "FLAG") in kinds  # one SLS line, two records
    assert kinds.count(("10:05:01", "FLAG")) == 1
    visit = next(e for e in events if e.kind == "VISIT")
    assert visit.time == datetime(2026, 10, 25, 8, 15, 11)  # the ANSI-wrapped line
    assert visit.expires_at == datetime(2026, 10, 25, 10, 15, 11)
    saved = next(e for e in events if e.kind == "SAVED")
    assert (saved.created_at, saved.new_created_at) == (
        datetime(2026, 10, 25, 6, 30),
        datetime(2026, 10, 25, 8, 0, 10),
    )


def test_incident_table_for_qat() -> None:
    report = TOOL.run(TOOL.input(raw=raw("qat"), hours=4), CTX)
    assert report.mode == "incidents"
    env = report.envs[0]
    assert env.error is None
    rows = env.incidents
    assert [r.flag.strftime("%H:%M:%S") for r in rows] == ["09:58:01", "10:05:01", "11:30:01"]

    retry, relogin, still_open = rows
    assert (retry.life_before_flag, retry.hang_location, retry.retry_recovered) == (
        "1h57m51s",
        "GET page 1, 30s",
        "Yes",
    )
    assert (retry.attempts, retry.saved, retry.outage) == ("0 / 0", "n/a (retry fixed it)", "0m39s")

    assert (relogin.life_before_flag, relogin.hang_location) == ("2h04m51s", "balance fetch")
    assert relogin.attempts == "3 / 2 (1x account selector 25s, 1x 30s wait)"
    assert (relogin.saved, relogin.outage_seconds, relogin.failed_count) == ("10:08:01", 180, 2)

    assert still_open.open is True
    assert (still_open.life_before_flag, still_open.hang_location) == (
        "1h22m00s",
        "token refresh (session expired)",
    )
    assert (still_open.attempts, still_open.outage) == ("open", "21m59s")  # runs to the end of the saved data
    assert env.until == datetime(2026, 10, 25, 11, 52)

    assert (env.total_outage_seconds, env.downtime_percent, env.failed_attempts) == (1538, 11.0, 3)
    assert env.failed_step_mix == {"account selector 25s": 1, "30s wait": 1, "login form 30s": 1}
    assert [s.strftime("%H:%M") for s in env.scraper_stops] == ["11:52"]


def test_window_filters_sort_and_top() -> None:
    report = TOOL.run(TOOL.input(raw=raw("qat"), since="2026-10-25 10:00", sort="outage", top=1), CTX)
    rows = report.envs[0].incidents
    assert [r.flag.strftime("%H:%M:%S") for r in rows] == ["11:30:01"]
    assert report.envs[0].total_outage_seconds == 180 + 1319  # the 09:58 incident is outside the window


def test_events_mode_lists_what_is_new() -> None:
    report = TOOL.run(TOOL.input(raw=raw("qat"), events_since="2026-10-25 09:30"), CTX)
    assert report.mode == "events"
    lines = [f"{e.time:%H:%M:%S} {e.text}" for e in report.envs[0].events]
    assert lines[:2] == [
        "09:40:00 stale-grid signal +500.00",
        "09:40:21 on-demand visit, new expires_at 10-25 11:40:21 (+2h00m00s)",
    ]
    assert "09:58:01 session invalidated (session age 1h57m51s <- near the 2h mark)" in lines
    assert "10:05:01 session invalidated (session age 2h04m51s <- near the 2h mark)" in lines
    assert "10:08:01 relogin SAVED (created_at 08:00:10 -> 10:08:01)" in lines
    assert lines[-1] == "11:52:00 scraper stopped (20 min session wait)"
    assert len(lines) == 14

    earlier = TOOL.run(TOOL.input(raw=raw("qat"), events_since="2026-10-25 08:10"), CTX)
    assert earlier.envs[0].events[0].text == "timer visit, new expires_at 10-25 10:15:11 (+2h00m00s)"


def test_full_events_mode_adds_the_incident_table_from_the_same_pull() -> None:
    params = TOOL.input(raw=raw("qat"), hours=4, events_since="2026-10-25 09:30", full=True)
    report = TOOL.run(params, CTX)
    env = report.envs[0]
    assert report.mode == "events" and report.full
    assert env.events_since == datetime(2026, 10, 25, 9, 30) and env.since == NOW - timedelta(hours=4)
    assert len(env.events) == 14 and len(env.incidents) == len(
        TOOL.run(TOOL.input(raw=raw("qat"), hours=4), CTX).envs[0].incidents
    )
    assert TOOL.render is not None
    text = TOOL.render(report)
    assert "events since 10-25 09:30:00" in text and " down (" in text


def test_events_mode_reports_no_change() -> None:
    report = TOOL.run(TOOL.input(raw=raw("qat"), events_hours=0.05), CTX)
    assert report.envs[0].events == []
    assert TOOL.render is not None
    assert TOOL.render(report).rstrip().endswith("no change")


def test_box_render_shares_widths_and_stars_long_outages() -> None:
    report = TOOL.run(TOOL.input(raw=raw("qat", "prod"), env="both", hours=4), CTX)
    assert TOOL.render is not None
    text = TOOL.render(report)
    assert text.startswith("now 2026-10-25 12:00:00 Manila")
    assert (
        "**QAT:** 3 incidents, 25m38s down (11.0% of 3h52m00s), 3 failed relogin attempts, "
        "scraper stops: 10-25 11:52."
    ) in text
    assert "**PROD:** 1 incidents, 7m00s down (5.3% of 2h12m30s), 1 failed relogin attempts." in text
    assert "21m59s*" in text and "3m00s " in text
    tables = [line for line in text.splitlines() if line.startswith("┌")]
    assert len(tables) == 2 and len(set(tables)) == 1  # identical top borders means identical widths


def test_side_render_pairs_envs_by_start_time() -> None:
    report = TOOL.run(TOOL.input(raw=raw("qat", "prod"), env="both", hours=4, format="side"), CTX)
    assert TOOL.render is not None
    text = TOOL.render(report)
    assert "QAT" in text and "PROD" in text and "+29s" in text
    rows = [line for line in text.splitlines() if line.startswith("10-25")]
    assert len(rows) == 3  # two unpaired QAT incidents, one paired

    top = TOOL.run(
        TOOL.input(raw=raw("qat", "prod"), env="both", hours=4, format="side", sort="outage", top=1), CTX
    )
    assert TOOL.render(top).count("\n10-25") == 1


@pytest.mark.parametrize(
    ("fmt", "needle"),
    [
        ("plain", "Failed steps: sel25 = account selector 25s"),
        ("md", "| 10-25 11:30:01 | 1h22m00s | token refresh"),
    ],
)
def test_plain_and_md_renders(fmt: str, needle: str) -> None:
    report = TOOL.run(TOOL.input(raw=raw("qat"), hours=4, format=fmt), CTX)
    assert TOOL.render is not None
    text = TOOL.render(report)
    assert "### QAT - 3 incidents, 25m38s down" in text
    assert needle in text


def test_pull_maps_env_to_project_and_window(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, datetime]] = []

    def fake_fetch(env: str, start: datetime, ctx: ToolContext) -> str:
        calls.append((env, start))
        return QAT_TEXT

    monkeypatch.setattr(module, "fetch_logs", fake_fetch)
    report = TOOL.run(TOOL.input(env="prod", hours=4), CTX)
    assert (report.envs[0].project, report.envs[0].logstore) == (
        "platform-prod-aliyun-logs",
        "platform-pms-prod",
    )
    assert calls == [("prod", NOW - timedelta(hours=4))]
    assert report.envs[0].until == NOW  # a live pull runs to now

    TOOL.run(TOOL.input(env="qat", events_since="2026-10-25 11:00"), CTX)
    assert calls[-1] == ("qat", datetime(2026, 10, 25, 8, 50))  # 2h10m of context before the cut-off


def test_empty_pull_retries_then_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(module, "fetch_logs", lambda env, start, ctx: calls.append(env) or "")
    with pytest.raises(ToolError, match=r"^no matching events after 3 tries"):
        TOOL.run(TOOL.input(), CTX)
    assert calls == ["qat", "qat", "qat"]


def test_transient_refusal_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fetch(env: str, start: datetime, ctx: ToolContext) -> str:
        calls.append(env)
        if len(calls) < 3:
            raise ToolError(
                "SLS query failed for platform-qat-aliyun-logs/platform-pms-qat: Unauthorized: denied"
            )
        return QAT_TEXT

    monkeypatch.setattr(module, "fetch_logs", fetch)
    report = TOOL.run(TOOL.input(hours=4), CTX)
    assert report.envs[0].error is None and len(report.envs[0].incidents) == 3
    assert calls == ["qat", "qat", "qat"]


def test_missing_credentials_fail_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALIBABA_CLOUD_ACCESS_KEY_ID")
    monkeypatch.setattr(module, "fetch_logs", lambda env, start, ctx: pytest.fail("must not pull"))
    with pytest.raises(ToolError, match="Missing ALIBABA_CLOUD_ACCESS_KEY_ID"):
        TOOL.run(TOOL.input(env="both"), CTX)
    TOOL.run(TOOL.input(raw=raw("qat")), CTX)  # saved pulls need no credentials


def test_failed_pull_in_one_env_is_reported_not_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    def fetch(env: str, start: datetime, ctx: ToolContext) -> str:
        if env == "prod":
            raise ToolError(
                "SLS query failed for platform-prod-aliyun-logs/platform-pms-prod: Unauthorized: denied"
            )
        return QAT_TEXT

    monkeypatch.setattr(module, "fetch_logs", fetch)
    report = TOOL.run(TOOL.input(env="both", hours=4), CTX)
    assert report.envs[0].error is None and len(report.envs[0].incidents) == 3
    assert report.envs[1].error == (
        "pull failed: SLS query failed for platform-prod-aliyun-logs/platform-pms-prod: Unauthorized: denied"
    )
    assert TOOL.render is not None
    assert "### PROD - pull failed: SLS query failed" in TOOL.render(report)


def test_every_env_failing_raises_each_message_once(monkeypatch: pytest.MonkeyPatch) -> None:
    def fetch(env: str, start: datetime, ctx: ToolContext) -> str:
        raise ToolError("SLS query failed: ServerBusy")

    monkeypatch.setattr(module, "fetch_logs", fetch)
    with pytest.raises(ToolError, match=r"^pull failed: SLS query failed: ServerBusy$"):
        TOOL.run(TOOL.input(env="both"), CTX)


def test_raw_accepts_an_sls_csv_export(tmp_path: Path) -> None:
    import csv

    rows = []
    for line in QAT_TEXT.splitlines():
        if line.startswith("#"):
            continue
        stamp, rest = line[:19], line[20:]
        container, content = rest[1:].split("] ", 1)
        epoch = int(
            datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=module.manila.MANILA).timestamp()
        )
        rows.append((container, str(epoch), content))
    export = tmp_path / "50a50-qat.csv"
    with export.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["__tag__:_container_name_", "__time__", "content"])
        writer.writerows(reversed(rows))  # exports are not always oldest-first

    report = TOOL.run(TOOL.input(raw=[f"qat={export}"], hours=4), CTX)
    assert [r.flag.strftime("%H:%M:%S") for r in report.envs[0].incidents] == [
        "09:58:01",
        "10:05:01",
        "11:30:01",
    ]


def test_missing_raw_file_is_a_tool_error() -> None:
    with pytest.raises(ToolError, match="cannot read --raw file"):
        TOOL.run(TOOL.input(raw=["qat=nope.txt"]), CTX)


def test_one_empty_env_is_reported_not_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "fetch_logs", lambda env, start, ctx: QAT_TEXT if env == "qat" else "")
    report = TOOL.run(TOOL.input(env="both", hours=4), CTX)
    assert report.envs[0].error is None and report.envs[1].error is not None
    assert TOOL.render is not None
    assert "### PROD - no matching events after 3 tries" in TOOL.render(report)


def test_fetch_logs_goes_through_sls_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    EPOCH = int(datetime(2026, 10, 25, 8, 0, tzinfo=module.manila.MANILA).timestamp())

    class FakeLog:
        def get_time(self) -> int:
            return EPOCH

        def get_contents(self) -> dict[str, Any]:
            return {
                "__tag__:_container_name_": "pms-auto-deposit",
                "content": "2026-10-25 08:00:00 | INFO | hi",
            }

    class FakeResponse:
        def get_logs(self) -> list[FakeLog]:
            return [FakeLog()]

    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def get_log_all(
            self, project: str, logstore: str, start: int, end: int, **kw: Any
        ) -> list[FakeResponse]:
            self.calls.append({"project": project, "logstore": logstore, "start": start, **kw})
            return [FakeResponse()]

    fake = FakeClient()
    monkeypatch.setattr(sls_module, "make_client", lambda *_: fake)

    text = module.fetch_logs("prod", datetime(2026, 10, 25, 8, 0), CTX)
    assert text == "2026-10-25 08:00:00 [pms-auto-deposit] 2026-10-25 08:00:00 | INFO | hi"
    call = fake.calls[0]
    assert call["project"] == "platform-prod-aliyun-logs"
    assert '"Invalidated"' in call["query"] and '"Stale-grid signal"' in call["query"]
    assert call["start"] == EPOCH


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"since": "25/10/2026"}, "bad time"),
        ({"events_since": "yesterday"}, "bad time"),
        ({"raw": ["dev=file.txt"]}, "bad --raw"),
        ({"hours": 0}, "greater than 0"),
    ],
)
def test_bad_inputs_are_validation_errors(kwargs: dict[str, Any], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        TOOL.input(**kwargs)


@pytest.mark.parametrize(
    ("reason", "label"),
    [
        ("Page.wait_for_selector: Timeout 25000ms exceeded.", "account selector 25s"),
        ("Page.wait_for_selector: Timeout 90000ms exceeded.", "90s wait"),
        ("login page did not load", "login page 40s"),
        ("something new happened here today", "something new happened he"),
    ],
)
def test_where_labels_failure_reasons(reason: str, label: str) -> None:
    incidents = importlib.import_module(f"{module.__package__}.incidents")
    assert incidents.where(reason) == label
