from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from shed.cron import RunRecord
from shed.scaffold import scaffold_tool
from shed.ui import UIServer, ago, fmt_scalar, page, plain, render_data, run_page, took, tunnels_page, until
from shed.version import current as shed_version


@pytest.fixture
def server(tmp_path: Path) -> Iterator[UIServer]:
    tools = tmp_path / "tools"
    tools.mkdir()
    scaffold_tool("echo-x", tools_dir=tools)
    cron = tmp_path / "cron.toml"
    cron.write_text('[jobs.echo]\ntool = "echo-x"\nevery = "1h"\nargs = { text = "hi <b>", upper = true }\n')
    srv = UIServer(("127.0.0.1", 0), cron_file=cron, state_dir=tmp_path / "state", tools_dir=tools)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.stop()


def get(url: str, method: str = "GET") -> tuple[int, str]:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method=method), timeout=5) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def wait_for(check: Callable[[], bool], timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        if time.monotonic() > deadline:
            raise AssertionError("timed out waiting")
        time.sleep(0.05)


def test_dashboard_run_now_and_api(server: UIServer) -> None:
    base = server.url()
    status, body = get(f"{base}/")
    assert status == 200 and "never run" in body and 'href="/jobs/echo"' in body
    badge = f'<span class="version" id="version" title="shed version">v{shed_version()}</span>'
    assert f"tool-shed</a>{badge}<a " in body  # beside the brand, where people look for a version
    assert "getElementById('version')" in body  # the refresh keeps it current after `shed start`
    status, payload = get(f"{base}/health")
    assert json.loads(payload) == {
        "ok": True,
        "schedule": False,
        "running": [],
        "pid": os.getpid(),
        "version": shed_version(),
    }

    status, body = get(f"{base}/jobs/echo/run", "POST")  # 303 to the job page
    assert status == 200 and "echo" in body
    wait_for(lambda: server.store.latest("echo") is not None)

    status, body = get(f"{base}/jobs/echo")
    assert (
        "<dd>HI &lt;B&gt;</dd>" in body and "<pre>HI &lt;B&gt;</pre>" in body
    )  # table view and escaped text
    assert 'class="countdown" data-due="' in body and "next <b>" in body
    assert "History" in body and "Settings" in body and "Run now" in body
    assert 'class="pill ok"' in body
    status, payload = get(f"{base}/api/jobs/echo/latest")
    latest = json.loads(payload)
    assert latest["ok"] is True and latest["result"] == {"result": "HI <B>"}
    jobs = json.loads(get(f"{base}/api/jobs")[1])
    assert jobs[0]["name"] == "echo" and jobs[0]["runs"] == 1 and jobs[0]["latest"]["summary"] == "HI <B>"

    stem = server.store.entries("echo")[0][0]
    status, body = get(f"{base}/jobs/echo/runs/{stem}")
    assert status == 200 and "Result as JSON" in body
    runs = json.loads(get(f"{base}/api/jobs/echo/runs?limit=5")[1])
    assert runs[0]["id"] == stem and runs[0]["ok"] is True

    assert get(f"{base}/jobs/nope")[0] == 404
    assert get(f"{base}/jobs/echo/runs/20000101-000000")[0] == 404
    assert get(f"{base}/api/jobs/ghost/latest")[0] == 404
    status, body = get(f"{base}/tools")
    assert status == 200 and "<code>--text</code>" in body and "required" in body
    assert 'href="#tool-echo-x"' in body and '<details class="tool" id="tool-echo-x">' in body
    status, body = get(f"{base}/favicon.svg")
    assert status == 200 and body.startswith("<svg")
    with urllib.request.urlopen(f"{base}/static/inter.woff2", timeout=5) as resp:
        assert resp.status == 200 and resp.headers["Content-Type"] == "font/woff2" and resp.read(4) == b"wOF2"


def test_tool_view_page(server: UIServer, tmp_path: Path) -> None:
    base = server.url()
    assert get(f"{base}/view/echo")[0] == 404 and get(f"{base}/view/nope")[0] == 404
    (tmp_path / "tools" / "echo-x" / "view.py").write_text(
        "TITLE = 'Echo view'\n"
        "def view(job, runs, now, query):\n"
        '    return f\'<h1>Echo view</h1><p>{job} {len(runs)} runs {query.get("hours", "-")}</p>\'\n'
    )
    server._views.clear()
    get(f"{base}/jobs/echo/run", method="POST")
    wait_for(lambda: server.store.latest("echo") is not None and not server.running)
    status, body = get(f"{base}/view/echo?hours=3")
    assert status == 200 and "<p>echo 1 runs 3</p>" in body
    assert '<a href="/view/echo">Echo view</a>' in body  # nav link on every page
    assert "Echo view page" in get(f"{base}/jobs/echo")[1] and 'href="/view/echo"' in get(f"{base}/")[1]


def test_scheduler_runs_due_jobs(server: UIServer) -> None:
    server.start_scheduler()
    wait_for(lambda: server.store.latest("echo") is not None)
    latest = server.store.latest("echo")
    assert latest is not None and latest.ok


def test_plain_strips_markdown_decoration() -> None:
    assert plain("```\n**QAT:** 3 incidents\n```\n") == "QAT: 3 incidents"


def test_empty_dashboard_explains_how_to_add_a_job(tmp_path: Path) -> None:
    srv = UIServer(
        ("127.0.0.1", 0), cron_file=tmp_path / "none.toml", state_dir=tmp_path / "state", tools_dir=tmp_path
    )
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        status, body = get(f"{srv.url()}/")
        assert status == 200 and "No jobs yet" in body and "[jobs.repo-status]" in body
    finally:
        srv.stop()


def test_render_data_makes_facts_tables_and_sections() -> None:
    data = {
        "now": "2026-10-27T11:24:57+08:00",
        "mode": "events",
        "envs": [
            {
                "env": "qat",
                "error": None,
                "downtime_percent": 1.5,
                "incidents": [],
                "events": [{"time": "2026-10-27T11:14:21", "kind": "VISIT", "text": "timer visit <x>"}],
            }
        ],
    }
    out = render_data(data)
    assert "<dt>now</dt><dd>2026-10-27 11:24:57</dd>" in out
    assert "<h3>envs <small>1</small></h3>" in out and "<h4>QAT</h4>" in out
    assert '<dt>incidents</dt><dd><span class="muted">none</span></dd>' in out
    assert "<th>time</th><th>kind</th><th>text</th>" in out and "<td>timer visit &lt;x&gt;</td>" in out
    assert "<td>2026-10-27 11:14:21</td>" in out
    assert render_data([{"a": 1, "b": [1, 2]}, {"a": 2}]) == (
        '<div class="scroll"><table class="data"><tr><th>a</th><th>b</th></tr>'
        '<tr><td class="num">1</td><td>1, 2</td></tr><tr><td class="num">2</td><td>-</td></tr></table></div>'
    )
    assert render_data([]) == '<p class="muted">none</p>' and render_data("x") == "<p>x</p>"
    rows = [
        {"flag": "a", "failed_steps": {}},
        {"flag": "b", "failed_steps": {"30s wait": 2, "page crash": 1}},
    ]
    table = render_data(rows)
    assert (
        table.startswith('<div class="scroll"><table class="data">')
        and "<td>30s wait 2, page crash 1</td>" in table
    )


def test_time_and_scalar_formatting() -> None:
    now = datetime(2026, 10, 27, 12, 0, 0)
    assert ago(now - timedelta(seconds=10), now) == "just now"
    assert ago(now - timedelta(minutes=3), now) == "3 min ago"
    assert ago(now - timedelta(hours=5), now) == "5 h ago"
    assert ago(now - timedelta(days=3), now) == "3 d ago"
    assert until(now - timedelta(seconds=1), now) == "due now"
    assert until(now + timedelta(seconds=42), now) == "in 42 s"
    assert until(now + timedelta(minutes=1, seconds=42), now) == "in 1 min 42 s"
    assert until(now + timedelta(minutes=6), now) == "in 6 min 0 s"
    assert until(now + timedelta(hours=2), now) == "in 2 h"
    assert (took(850), took(1100), took(125_000)) == ("850 ms", "1.1 s", "2 min 5 s")
    assert (fmt_scalar(None), fmt_scalar(True), fmt_scalar(2.50), fmt_scalar("plain")) == (
        "-",
        "yes",
        "2.5",
        "plain",
    )


def test_pages_show_the_shed_version_and_a_run_shows_the_versions_that_produced_it() -> None:
    now = datetime(2026, 11, 4, 12, 0)
    html = page("x", "<p>body</p>", now, scheduler=False)
    assert f"v{shed_version()}" in html
    record = RunRecord(
        job="j",
        tool="echo-x",
        args={},
        started=now,
        finished=now,
        duration_ms=5,
        ok=True,
        version="1.4.0",
        tool_version="0.2.0",
    )
    html = run_page("j", "20261104-120000", record, now)
    assert "echo-x</b> v0.2.0" in html and "shed <b>v1.4.0</b>" in html
    old = record.model_copy(update={"version": None, "tool_version": None})  # a record from before versioning
    assert "shed <b>" not in run_page("j", "x", old, now)


class FakeTunnelProcess:
    def __init__(self, args: list[str]) -> None:
        self.args, self.lines, self.returncode = args, [], None
        self.ready, self.ended = threading.Event(), threading.Event()

    def emit(self, line: str) -> None:
        self.lines.append(line)
        self.ready.set()

    def readline(self) -> str:
        while True:
            self.ready.wait()
            if self.lines:
                return self.lines.pop(0) + "\n"
            if self.ended.is_set():
                return ""
            self.ready.clear()

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.returncode = -15
        self.ended.set()
        self.ready.set()

    kill = terminate

    def wait(self, timeout: float | None = None) -> int:
        self.ended.wait(timeout)
        return self.returncode or 0


def post_form(url: str, **fields: str) -> tuple[int, str]:
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(req, timeout=5) as resp:
            return resp.status, resp.headers.get("Location", "")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Location", "")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def test_tunnels_page_adds_starts_shows_the_public_url_and_stops(tmp_path: Path) -> None:
    from shed.tunnels import TunnelManager

    spawned: list[FakeTunnelProcess] = []

    def spawn(args: list[str]) -> FakeTunnelProcess:
        proc = FakeTunnelProcess(args)
        spawned.append(proc)
        return proc

    connections = [1]
    manager = TunnelManager(
        tmp_path / "tunnels.json",
        spawn=spawn,
        binary=lambda: "/opt/homebrew/bin/cloudflared",
        probe=lambda port: connections[0],
        check_every=0.01,
    )
    tools = tmp_path / "tools"
    tools.mkdir()
    cron = tmp_path / "cron.toml"
    cron.write_text("")
    srv = UIServer(
        ("127.0.0.1", 0), cron_file=cron, state_dir=tmp_path / "state", tools_dir=tools, tunnels=manager
    )
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = srv.url()
    try:
        status, html = get(f"{base}/tunnels")
        assert status == 200 and 'href="/tunnels"' in html and "No tunnels yet" in html

        assert post_form(f"{base}/tunnels", target="http://localhost:8000", label="dev", start="1") == (
            303,
            "/tunnels",
        )
        assert spawned[0].args[-1] == "http://localhost:8000"
        html = get(f"{base}/tunnels")[1]
        assert "<b>dev</b>" in html and "waiting for cloudflared" in html

        spawned[0].emit("INF |  https://neat-tunnel-example.trycloudflare.com  |")
        wait_for(lambda: '<span class="pill ok">running</span>' in get(f"{base}/tunnels")[1])
        html = get(f"{base}/tunnels")[1]
        assert 'data-copy="https://neat-tunnel-example.trycloudflare.com"' in html
        assert 'href="https://neat-tunnel-example.trycloudflare.com"' in html
        api = json.loads(get(f"{base}/api/tunnels")[1])
        assert api["cloudflared"] == "installed" and api["tunnels"][0]["public_url"].endswith(
            "trycloudflare.com"
        )
        assert api["tunnels"][0]["connections"] == 1
        tunnel_id = api["tunnels"][0]["id"]

        connections[0] = 0
        wait_for(lambda: '<span class="pill fail">unreachable</span>' in get(f"{base}/tunnels")[1])
        html = get(f"{base}/tunnels")[1]
        assert "no Cloudflare connection since" in html and "cloudflared is reconnecting" in html
        assert 'data-copy="https://neat-tunnel-example.trycloudflare.com"' in html
        assert ">stop</button>" in html and ">restart</button>" in html
        assert post_form(f"{base}/tunnels/{api['tunnels'][0]['id']}/restart") == (303, "/tunnels")
        assert len(spawned) == 2 and spawned[0].returncode == -15

        assert post_form(f"{base}/tunnels/{tunnel_id}/stop")[0] == 303
        assert "stopped" in get(f"{base}/tunnels")[1] and spawned[0].returncode == -15

        assert post_form(f"{base}/tunnels", target="example.com")[0] == 303
        assert "http://host:port" in get(f"{base}/tunnels")[1]  # the notice shows once
        assert "http://host:port" not in get(f"{base}/tunnels")[1]

        assert post_form(f"{base}/tunnels/{tunnel_id}/remove")[0] == 303
        assert "No tunnels yet" in get(f"{base}/tunnels")[1]
    finally:
        srv.stop()


def test_tunnel_to_the_dashboard_itself_is_flagged_and_install_button_shows_when_missing(
    tmp_path: Path,
) -> None:
    from shed.tunnels import TunnelManager

    manager = TunnelManager(
        tmp_path / "tunnels.json", spawn=lambda a: FakeTunnelProcess(a), binary=lambda: None
    )
    tools = tmp_path / "tools"
    tools.mkdir()
    cron = tmp_path / "cron.toml"
    cron.write_text("")
    srv = UIServer(
        ("127.0.0.1", 0), cron_file=cron, state_dir=tmp_path / "state", tools_dir=tools, tunnels=manager
    )
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = srv.url()
    try:
        port = srv.server_address[1]
        post_form(f"{base}/tunnels", target=f"http://127.0.0.1:{port}")
        html = get(f"{base}/tunnels")[1]
        assert "this dashboard, no login" in html
        assert ("Install with Homebrew" in html) or ("Homebrew is missing" in html)
    finally:
        srv.stop()


def test_an_unreachable_tunnel_row_says_the_mac_slept_and_shows_cloudflareds_error() -> None:
    from shed.tunnels import TunnelStatus

    at = datetime(2026, 11, 6, 13, 1, 33)
    row = TunnelStatus(
        id="5a4f1bd6",
        target="http://localhost:3333",
        label="finance",
        state="unreachable",
        public_url="https://asia-hammer-packard-awareness.trycloudflare.com",
        since=at,
        connections=0,
        last_error='Register tunnel error from server side error="Unauthorized"',
        slept_from=datetime(2026, 11, 6, 12, 47, 37),
        slept_until=datetime(2026, 11, 6, 13, 1, 27),
        asleep_s=830,
    )

    class Rows:
        def install_state(self) -> str:
            return "installed"

        def install_error(self) -> None:
            return None

        def statuses(self) -> list[TunnelStatus]:
            return [row]

    html = tunnels_page(Rows(), 8765, at)  # type: ignore[arg-type]
    assert "the Mac was asleep for 14 min, between 12:47 and 13:01" in html
    assert "cloudflared: Register tunnel error from server side error=&quot;Unauthorized&quot;" in html
    assert 'action="/tunnels/5a4f1bd6/restart"' in html
