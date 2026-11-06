from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from shed import ShResult, tunnels
from shed.tunnels import TunnelManager

URL_LINE = "2026-11-04T10:00:00Z INF |  https://brave-lamp-quick-tunnel.trycloudflare.com   |"


class FakeProcess:
    """Stands in for the cloudflared process: its output lines arrive through `emit`."""

    def __init__(self, args: list[str]) -> None:
        self.args = args
        self.lines: list[str] = []
        self.ready = threading.Event()
        self.ended = threading.Event()
        self.returncode: int | None = None
        self.terminated = False

    def emit(self, line: str) -> None:
        self.lines.append(line)
        self.ready.set()

    def end(self, code: int) -> None:
        self.returncode = code
        self.ended.set()
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
        self.terminated = True
        self.end(-15)

    def kill(self) -> None:
        self.end(-9)

    def wait(self, timeout: float | None = None) -> int:
        self.ended.wait(timeout)
        return self.returncode or 0


class FakeHealth:
    """Stands in for cloudflared's ready endpoint: `count` is its answer, None when it does not answer."""

    def __init__(self) -> None:
        self.count: int | None = None
        self.ports: list[int] = []

    def __call__(self, port: int) -> int | None:
        self.ports.append(port)
        return self.count


@pytest.fixture
def spawned() -> list[FakeProcess]:
    return []


@pytest.fixture
def health() -> FakeHealth:
    return FakeHealth()


@pytest.fixture
def manager(
    tmp_path: Path, spawned: list[FakeProcess], health: FakeHealth, monkeypatch: pytest.MonkeyPatch
) -> TunnelManager:
    def spawn(args: list[str]) -> FakeProcess:
        proc = FakeProcess(args)
        spawned.append(proc)
        return proc

    monkeypatch.setattr(tunnels, "which", lambda name: f"/opt/homebrew/bin/{name}")
    return TunnelManager(
        tmp_path / "state" / "tunnels.json",
        spawn=spawn,
        metrics_port=lambda: 20400,
        probe=health,
        check_every=0.01,
    )


def wait_for(check: Any, timeout: float = 3) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.02)


def test_add_start_and_copy_the_public_url(
    manager: TunnelManager, spawned: list[FakeProcess], health: FakeHealth
) -> None:
    row = manager.add("http://localhost:8000", label="dev server")
    assert (row.target, row.label) == ("http://localhost:8000", "dev server")
    assert manager.status(row.id).state == "stopped"

    manager.start(row.id)
    assert spawned[0].args == [
        *("cloudflared", "tunnel", "--no-autoupdate"),
        *("--metrics", "127.0.0.1:20400", "--url", "http://localhost:8000"),
    ]
    assert manager.status(row.id).state == "starting"
    health.count = 0
    spawned[0].emit("2026-11-04T10:00:00Z INF Thank you for trying Cloudflare Tunnel.")
    spawned[0].emit(URL_LINE)
    wait_for(lambda: manager.status(row.id).connections == 0)
    status = manager.status(row.id)
    assert (
        status.state == "starting"
        and status.public_url == "https://brave-lamp-quick-tunnel.trycloudflare.com"
    )

    health.count = 1
    wait_for(lambda: manager.status(row.id).state == "running")
    status = manager.status(row.id)
    assert status.public_url == "https://brave-lamp-quick-tunnel.trycloudflare.com"
    assert status.connections == 1 and status.since is not None
    assert set(health.ports) == {20400}

    manager.stop(row.id)
    assert spawned[0].terminated and manager.status(row.id).state == "stopped"
    assert manager.status(row.id).public_url is None
    time.sleep(0.05)
    checks = len(health.ports)
    time.sleep(0.05)
    assert len(health.ports) == checks, "the health check ends with the tunnel"


def test_a_tunnel_that_loses_its_cloudflare_connection_is_unreachable_until_it_reconnects(
    manager: TunnelManager, spawned: list[FakeProcess], health: FakeHealth
) -> None:
    row = manager.add("http://localhost:8000")
    manager.start(row.id)
    health.count = 1
    spawned[0].emit(URL_LINE)
    wait_for(lambda: manager.status(row.id).state == "running")
    up_since = manager.status(row.id).since

    health.count = 0
    wait_for(lambda: manager.status(row.id).state == "unreachable")
    down = manager.status(row.id)
    assert down.public_url == "https://brave-lamp-quick-tunnel.trycloudflare.com" and down.connections == 0
    assert down.since is not None and up_since is not None and down.since >= up_since

    health.count = 1
    wait_for(lambda: manager.status(row.id).state == "running")
    assert manager.status(row.id).connections == 1


def test_a_silent_health_endpoint_falls_back_to_the_url_banner(
    manager: TunnelManager, spawned: list[FakeProcess], health: FakeHealth
) -> None:
    row = manager.add("http://localhost:8000")
    manager.start(row.id)
    spawned[0].emit(URL_LINE)
    wait_for(lambda: manager.status(row.id).state == "running")
    status = manager.status(row.id)
    assert status.connections is None and status.since is not None
    assert len(health.ports) >= tunnels.SILENT_CHECKS


def test_ready_connections_reads_cloudflareds_count_from_200_and_503_answers() -> None:
    answers = [(503, 0), (200, 2)]

    class Ready(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            code, count = answers.pop(0)
            body = json.dumps({"status": code, "readyConnections": count, "connectorId": "x"}).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    srv = HTTPServer(("127.0.0.1", 0), Ready)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    try:
        assert tunnels.ready_connections(port) == 0
        assert tunnels.ready_connections(port) == 2
    finally:
        srv.shutdown()
        srv.server_close()
    assert tunnels.ready_connections(port) is None


def test_a_tunnel_that_dies_before_announcing_a_url_is_failed_with_its_last_line(
    manager: TunnelManager, spawned: list[FakeProcess]
) -> None:
    row = manager.add("http://localhost:9999")
    manager.start(row.id)
    spawned[0].emit("ERR failed to connect to origin: connection refused")
    spawned[0].end(1)
    wait_for(lambda: manager.status(row.id).state == "failed")
    assert "connection refused" in (manager.status(row.id).error or "")


def test_rows_persist_but_processes_do_not(tmp_path: Path, manager: TunnelManager) -> None:
    row = manager.add("http://localhost:3000", label="web")
    manager.start(row.id)
    saved = json.loads((tmp_path / "state" / "tunnels.json").read_text())
    assert [r["target"] for r in saved] == ["http://localhost:3000"] and "process" not in saved[0]
    again = TunnelManager(tmp_path / "state" / "tunnels.json", spawn=lambda args: FakeProcess(args))
    assert [(r.target, r.label) for r in again.rows()] == [("http://localhost:3000", "web")]
    assert again.status(row.id).state == "stopped"


def test_remove_stops_a_running_tunnel_and_duplicates_and_bad_targets_are_refused(
    manager: TunnelManager, spawned: list[FakeProcess]
) -> None:
    row = manager.add("http://localhost:8000")
    manager.start(row.id)
    manager.remove(row.id)
    assert spawned[0].terminated and manager.rows() == []
    with pytest.raises(ValueError, match="http://host:port"):
        manager.add("localhost:8000")
    with pytest.raises(ValueError, match="local"):
        manager.add("https://example.com")
    manager.add("http://127.0.0.1:8000")
    with pytest.raises(ValueError, match="already"):
        manager.add("http://127.0.0.1:8000")


def test_stop_all_ends_every_running_tunnel(manager: TunnelManager, spawned: list[FakeProcess]) -> None:
    a, b = manager.add("http://localhost:1"), manager.add("http://localhost:2")
    manager.start(a.id)
    manager.start(b.id)
    manager.stop_all()
    assert all(p.terminated for p in spawned)
    assert {manager.status(a.id).state, manager.status(b.id).state} == {"stopped"}


def test_install_runs_brew_in_the_background_and_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    present = {"brew"}
    monkeypatch.setattr(
        tunnels, "which", lambda name: f"/opt/homebrew/bin/{name}" if name in present else None
    )
    calls: list[tuple[str, ...]] = []

    def brew(cmd: str, args: Any = (), **kwargs: Any) -> ShResult:
        calls.append((cmd, *args))
        present.add("cloudflared")
        return ShResult(stdout="", stderr="", code=0)

    monkeypatch.setattr(tunnels, "sh", brew)
    manager = TunnelManager(tmp_path / "tunnels.json", spawn=lambda args: FakeProcess(args))
    assert manager.installed() is False and manager.install_state() == "missing"
    manager.install()
    wait_for(lambda: manager.install_state() == "installed")
    assert calls == [("brew", "install", "cloudflared")] and manager.installed()
    bare = TunnelManager(tmp_path / "t2.json", spawn=lambda args: FakeProcess(args), binary=lambda: None)
    with pytest.raises(RuntimeError, match="not installed"):
        bare.start(bare.add("http://localhost:5").id)
