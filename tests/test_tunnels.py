from __future__ import annotations

import json
import signal
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from shed import ShResult, tunnels
from shed.lib.manila import MANILA
from shed.tunnels import TunnelManager

URL_LINE = "2026-11-04T10:00:00Z INF |  https://brave-lamp-quick-tunnel.trycloudflare.com   |"
NEW_URL_LINE = "2026-11-25T09:00:00Z INF |  https://calm-river-quick-tunnel.trycloudflare.com   |"


class FakeProcess:
    """Stands in for the cloudflared process: `emit` writes its output lines to the tunnel's log file."""

    def __init__(self, args: list[str], log: Path, pid: int = 4100) -> None:
        self.args, self.log, self.pid = args, log, pid
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("", encoding="utf-8")
        self.ended = threading.Event()
        self.returncode: int | None = None
        self.terminated = False

    def emit(self, line: str) -> None:
        with self.log.open("a", encoding="utf-8") as out:
            out.write(line + "\n")

    def end(self, code: int) -> None:
        self.returncode = code
        self.ended.set()

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


class FakeMachine:
    """The machine's processes as a dashboard that did not start them sees them: a command line per pid."""

    def __init__(self) -> None:
        self.commands: dict[int, str] = {}
        self.signals: list[tuple[int, int]] = []

    def command_of(self, pid: int) -> str | None:
        return self.commands.get(pid)

    def signal(self, pid: int, sig: int) -> None:
        if pid not in self.commands:
            raise ProcessLookupError(pid)
        self.signals.append((pid, sig))
        del self.commands[pid]


class FakeHealth:
    """Stands in for cloudflared's ready endpoint: `count` is its answer, None when it does not answer."""

    def __init__(self) -> None:
        self.count: int | None = None
        self.ports: list[int] = []

    def __call__(self, port: int) -> int | None:
        self.ports.append(port)
        return self.count


class FakeClock:
    """The wall clock the health check reads; moving it forward between checks looks like a sleep."""

    def __init__(self) -> None:
        self.t = time.time()

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def spawned() -> list[FakeProcess]:
    return []


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def health() -> FakeHealth:
    return FakeHealth()


@pytest.fixture
def machine() -> FakeMachine:
    return FakeMachine()


@pytest.fixture
def make_manager(
    tmp_path: Path,
    spawned: list[FakeProcess],
    health: FakeHealth,
    clock: FakeClock,
    machine: FakeMachine,
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[], TunnelManager]:
    """Each call is one dashboard's manager over the same state folder and the same machine."""

    def spawn(args: list[str], log: Path) -> FakeProcess:
        proc = FakeProcess(args, log, pid=4100 + len(spawned))
        spawned.append(proc)
        machine.commands[proc.pid] = " ".join(args)
        return proc

    monkeypatch.setattr(tunnels, "which", lambda name: f"/opt/homebrew/bin/{name}")
    return lambda: TunnelManager(
        tmp_path / "state" / "tunnels.json",
        spawn=spawn,
        metrics_port=lambda: 20400,
        probe=health,
        check_every=0.01,
        follow_every=0.005,
        clock=clock,
        command_of=machine.command_of,
        signal=machine.signal,
    )


@pytest.fixture
def manager(make_manager: Callable[[], TunnelManager]) -> TunnelManager:
    return make_manager()


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


def test_an_unreachable_tunnel_names_the_sleep_and_cloudflareds_last_error_and_restarts(
    manager: TunnelManager, spawned: list[FakeProcess], health: FakeHealth, clock: FakeClock
) -> None:
    row = manager.add("http://localhost:3333", label="finance")
    manager.start(row.id)
    health.count = 1
    spawned[0].emit(URL_LINE)
    wait_for(lambda: manager.status(row.id).state == "running")

    clock.t += 14 * 60  # the Mac sleeps 14 minutes between two checks
    health.count = 0
    spawned[0].emit('2026-11-06T05:01:33Z ERR Register tunnel error from server side error="Unauthorized"')
    wait_for(lambda: (s := manager.status(row.id)).asleep_s is not None and s.last_error is not None)
    down = manager.status(row.id)
    assert down.state == "unreachable" and down.asleep_s == 14 * 60
    assert (
        down.slept_from and down.slept_until and (down.slept_until - down.slept_from).total_seconds() == 840
    )
    assert down.last_error == 'Register tunnel error from server side error="Unauthorized"'

    health.count = 1  # it reconnects: the sleep and the error belong to the old outage
    wait_for(lambda: manager.status(row.id).state == "running")
    health.count = 0
    wait_for(lambda: manager.status(row.id).state == "unreachable")
    again = manager.status(row.id)
    assert (again.last_error, again.slept_from, again.asleep_s) == (None, None, None)

    manager.restart(row.id)
    assert len(spawned) == 2 and spawned[0].terminated
    assert manager.status(row.id).state == "starting"


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


def test_a_tunnel_that_dies_before_announcing_a_url_is_failed_with_its_last_line(
    manager: TunnelManager, spawned: list[FakeProcess]
) -> None:
    row = manager.add("http://localhost:9999")
    manager.start(row.id)
    spawned[0].emit("ERR failed to connect to origin: connection refused")
    spawned[0].end(1)
    wait_for(lambda: manager.status(row.id).state == "failed")
    assert "connection refused" in (manager.status(row.id).error or "")


def test_rows_persist_with_whether_they_are_on_and_the_process_that_serves_them(
    tmp_path: Path, manager: TunnelManager, spawned: list[FakeProcess]
) -> None:
    row = manager.add("http://localhost:3000", label="web")
    manager.start(row.id)
    saved = json.loads((tmp_path / "state" / "tunnels.json").read_text())
    assert [r["target"] for r in saved] == ["http://localhost:3000"]
    assert saved[0]["on"] is True
    assert (saved[0]["run"]["pid"], saved[0]["run"]["metrics_port"]) == (spawned[0].pid, 20400)
    again = TunnelManager(tmp_path / "state" / "tunnels.json")
    assert [(r.target, r.label, r.on) for r in again.rows()] == [("http://localhost:3000", "web", True)]

    manager.stop(row.id)
    saved = json.loads((tmp_path / "state" / "tunnels.json").read_text())
    assert (saved[0]["on"], saved[0]["run"]) == (False, None)


def running_tunnel(manager: TunnelManager, spawned: list[FakeProcess], health: FakeHealth) -> str:
    """Add and start a tunnel and wait until Cloudflare holds its connection; returns its id."""
    row = manager.add("http://localhost:3333", label="finance")
    manager.start(row.id)
    health.count = 1
    spawned[-1].emit(URL_LINE)
    wait_for(lambda: manager.status(row.id).state == "running")
    return row.id


def test_a_tunnel_outlives_the_dashboard_and_the_next_one_picks_it_up_with_the_same_url(
    make_manager: Callable[[], TunnelManager],
    spawned: list[FakeProcess],
    health: FakeHealth,
    machine: FakeMachine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = datetime(2026, 11, 25, 9, 0, tzinfo=MANILA)
    monkeypatch.setattr(tunnels, "now", lambda: started)
    first = make_manager()
    tunnel_id = running_tunnel(first, spawned, health)
    first.close()
    assert spawned[0].poll() is None, "closing the dashboard does not stop the tunnel"

    monkeypatch.setattr(tunnels, "now", lambda: started + timedelta(hours=2))
    second = make_manager()
    second.resume()
    wait_for(lambda: second.status(tunnel_id).state == "running")
    status = second.status(tunnel_id)
    assert status.public_url == "https://brave-lamp-quick-tunnel.trycloudflare.com"
    assert status.since == started, "it has been up since before this dashboard started"
    assert len(spawned) == 1, "no second cloudflared was started"

    second.stop(tunnel_id)
    assert machine.signals == [(spawned[0].pid, signal.SIGTERM)]
    assert second.status(tunnel_id).state == "stopped"


def test_a_tunnel_that_was_on_starts_again_after_a_reboot_with_a_new_url(
    make_manager: Callable[[], TunnelManager],
    spawned: list[FakeProcess],
    health: FakeHealth,
    machine: FakeMachine,
) -> None:
    first = make_manager()
    tunnel_id = running_tunnel(first, spawned, health)
    first.close()
    machine.commands.clear()  # the Mac restarted: no process survived

    second = make_manager()
    second.resume()
    assert len(spawned) == 2
    spawned[1].emit(NEW_URL_LINE)
    wait_for(lambda: second.status(tunnel_id).state == "running")
    assert second.status(tunnel_id).public_url == "https://calm-river-quick-tunnel.trycloudflare.com"


def test_a_tunnel_that_was_stopped_stays_off_when_the_dashboard_starts(
    make_manager: Callable[[], TunnelManager], spawned: list[FakeProcess], health: FakeHealth
) -> None:
    first = make_manager()
    tunnel_id = running_tunnel(first, spawned, health)
    first.stop(tunnel_id)
    second = make_manager()
    second.resume()
    assert len(spawned) == 1 and second.status(tunnel_id).state == "stopped"


def test_a_reused_pid_is_not_taken_for_the_tunnel_and_is_never_signalled(
    make_manager: Callable[[], TunnelManager],
    spawned: list[FakeProcess],
    health: FakeHealth,
    machine: FakeMachine,
) -> None:
    first = make_manager()
    running_tunnel(first, spawned, health)
    first.close()
    machine.commands[spawned[0].pid] = "/usr/bin/vim notes.txt"  # cloudflared died and its pid was reused

    second = make_manager()
    second.resume()
    assert len(spawned) == 2, "the tunnel was started again, not adopted"
    second.stop_all()
    assert machine.signals == [] and spawned[1].terminated


def test_a_manager_that_never_ran_the_tunnels_can_still_stop_them(
    make_manager: Callable[[], TunnelManager],
    spawned: list[FakeProcess],
    health: FakeHealth,
    machine: FakeMachine,
) -> None:
    first = make_manager()
    running_tunnel(first, spawned, health)
    first.close()
    outside = make_manager()  # `shed cron uninstall` runs outside the dashboard
    assert [row.label for row in outside.stop_all()] == ["finance"]
    assert machine.signals == [(spawned[0].pid, signal.SIGTERM)]
    make_manager().resume()
    assert len(spawned) == 1, "a stopped tunnel is not started again"


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
    manager = TunnelManager(tmp_path / "tunnels.json", spawn=FakeProcess)
    assert manager.installed() is False and manager.install_state() == "missing"
    manager.install()
    wait_for(lambda: manager.install_state() == "installed")
    assert calls == [("brew", "install", "cloudflared")] and manager.installed()
    bare = TunnelManager(tmp_path / "t2.json", spawn=FakeProcess, binary=lambda: None)
    with pytest.raises(RuntimeError, match="not installed"):
        bare.start(bare.add("http://localhost:5").id)
