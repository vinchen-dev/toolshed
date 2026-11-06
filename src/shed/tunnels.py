"""Cloudflare quick tunnels started from the dashboard: expose a local URL at a random trycloudflare.com
address for as long as the dashboard runs. Rows (target, label) persist; processes die with the dashboard.

Health comes from cloudflared's own ready endpoint on a local metrics port, which counts its live
Cloudflare connections. The launcher, binary lookup, port picker and health probe are seams, so tests
never run cloudflared."""

from __future__ import annotations

import json
import re
import secrets
import socket
import subprocess
import threading
from collections import deque
from collections.abc import Callable
from datetime import datetime
from http.client import HTTPConnection, HTTPException
from pathlib import Path
from shutil import which
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel

from shed.lib.manila import MANILA
from shed.lib.shell import sh

PUBLIC_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]", "host.docker.internal")
State = Literal["stopped", "starting", "running", "unreachable", "failed"]
InstallState = Literal["installed", "missing", "installing", "failed", "no-brew"]
CHECK_EVERY_S = 3.0
SILENT_CHECKS = 5
"""Unanswered health checks after the URL banner before the banner alone counts as running."""


class TunnelRow(BaseModel):
    id: str
    target: str
    label: str | None = None
    created: datetime


class TunnelStatus(BaseModel):
    id: str
    target: str
    label: str | None
    state: State
    public_url: str | None = None
    since: datetime | None = None
    """When the current state began: the connection for running, its loss for unreachable."""
    connections: int | None = None
    """Cloudflare connections by cloudflared's own count; None when its ready endpoint has not answered."""
    error: str | None = None


class _Live:
    """A started tunnel: the process, the URL once announced, the last lines it printed."""

    def __init__(self, process: Any, metrics_port: int) -> None:
        self.process = process
        self.metrics_port = metrics_port
        self.public_url: str | None = None
        self.since: datetime | None = None
        self.error: str | None = None
        self.tail: deque[str] = deque(maxlen=20)
        self.done = False
        """Set once the process output closed, which is when cloudflared has exited."""
        self.connections: int | None = None
        self.connected_once = False
        self.changed: datetime | None = None
        """When the connection count last went between zero and non-zero."""
        self.silent_checks = 0
        self.stopping = threading.Event()


def spawn_cloudflared(args: list[str]) -> Any:
    return subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)


def cloudflared_binary() -> str | None:
    return which("cloudflared")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def ready_connections(port: int) -> int | None:
    """Ask cloudflared's /ready endpoint how many Cloudflare connections it holds; it answers 503 at zero."""
    conn = HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        conn.request("GET", "/ready")
        return int(json.loads(conn.getresponse().read())["readyConnections"])
    except (OSError, HTTPException, ValueError, KeyError, TypeError):
        return None
    finally:
        conn.close()


def _check_target(target: str) -> str:
    target = target.strip()
    parsed = urlparse(target)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("target must look like http://host:port")
    if parsed.hostname not in LOCAL_HOSTS:
        raise ValueError("only local targets are tunnelled (localhost, 127.0.0.1, ...)")
    return target.rstrip("/")


def _last(live: _Live) -> str | None:
    return next((t for t in reversed(live.tail) if t), None)


def now() -> datetime:
    return datetime.now(MANILA).replace(microsecond=0)


class TunnelManager:
    def __init__(
        self,
        state_file: Path,
        *,
        spawn: Callable[[list[str]], Any] = spawn_cloudflared,
        binary: Callable[[], str | None] = cloudflared_binary,
        metrics_port: Callable[[], int] = free_port,
        probe: Callable[[int], int | None] = ready_connections,
        check_every: float = CHECK_EVERY_S,
    ) -> None:
        self.state_file = state_file
        self._spawn = spawn
        self._binary = binary
        self._metrics_port = metrics_port
        self._probe = probe
        self._check_every = check_every
        self._rows: list[TunnelRow] = self._load()
        self._live: dict[str, _Live] = {}
        self._lock = threading.Lock()
        self._install: InstallState | None = None
        self._install_error: str | None = None

    # --- rows -------------------------------------------------------------------------------------

    def _load(self) -> list[TunnelRow]:
        if not self.state_file.exists():
            return []
        try:
            return [
                TunnelRow.model_validate(r) for r in json.loads(self.state_file.read_text(encoding="utf-8"))
            ]
        except (OSError, ValueError):
            return []

    def _save(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(
            json.dumps([r.model_dump(mode="json") for r in self._rows], indent=1), encoding="utf-8"
        )

    def rows(self) -> list[TunnelRow]:
        return list(self._rows)

    def _row(self, tunnel_id: str) -> TunnelRow:
        for row in self._rows:
            if row.id == tunnel_id:
                return row
        raise KeyError(tunnel_id)

    def add(self, target: str, label: str | None = None) -> TunnelRow:
        target = _check_target(target)
        if any(r.target == target for r in self._rows):
            raise ValueError(f"{target} is already in the table")
        row = TunnelRow(
            id=secrets.token_hex(4), target=target, label=(label or "").strip() or None, created=now()
        )
        self._rows.append(row)
        self._save()
        return row

    def remove(self, tunnel_id: str) -> None:
        self.stop(tunnel_id)
        self._rows = [r for r in self._rows if r.id != tunnel_id]
        self._save()

    # --- processes --------------------------------------------------------------------------------

    def installed(self) -> bool:
        return self._binary() is not None

    def start(self, tunnel_id: str) -> None:
        row = self._row(tunnel_id)
        with self._lock:
            live = self._live.get(tunnel_id)
            if live is not None and live.process.poll() is None:
                return
            if not self.installed():
                raise RuntimeError(
                    "cloudflared is not installed (Tunnels page: Install, or `brew install cloudflared`)"
                )
            port = self._metrics_port()
            metrics = f"127.0.0.1:{port}"
            process = self._spawn(
                ["cloudflared", "tunnel", "--no-autoupdate", "--metrics", metrics, "--url", row.target]
            )
            live = _Live(process, port)
            self._live[tunnel_id] = live
        for job, name in ((self._pump, "tunnel"), (self._watch, "tunnel-health")):
            threading.Thread(target=job, args=(live,), daemon=True, name=f"{name}-{tunnel_id}").start()

    def _pump(self, live: _Live) -> None:
        """Follow the process output: the public URL arrives in a boxed banner; the tail explains failures."""
        stream = getattr(live.process, "stdout", None) or live.process
        while True:
            line = stream.readline()
            if not line:
                break
            text = line.rstrip()
            live.tail.append(text)
            if live.public_url is None and (m := PUBLIC_URL.search(text)):
                live.public_url, live.since = m.group(0), now()
        if live.public_url is None and live.error is None:
            live.error = _last(live) or "cloudflared exited before announcing a URL"
        live.done = True

    def _watch(self, live: _Live) -> None:
        """Poll the ready endpoint once the URL is out, until cloudflared exits or the tunnel is stopped."""
        while not live.stopping.wait(self._check_every) and not live.done:
            if live.public_url is None:
                continue
            count = self._probe(live.metrics_port)
            if count is None:
                live.silent_checks += 1
                continue
            if live.connections is None or (live.connections > 0) != (count > 0):
                live.changed = now()
            live.connections = count
            live.connected_once = live.connected_once or count > 0

    def stop(self, tunnel_id: str) -> None:
        with self._lock:
            live = self._live.pop(tunnel_id, None)
        if live is None:
            return
        live.stopping.set()
        if live.process.poll() is None:
            live.process.terminate()
            try:
                live.process.wait(timeout=5)
            except Exception:
                live.process.kill()

    def stop_all(self) -> None:
        for tunnel_id in list(self._live):
            self.stop(tunnel_id)

    def status(self, tunnel_id: str) -> TunnelStatus:
        row = self._row(tunnel_id)
        live = self._live.get(tunnel_id)
        base = {"id": row.id, "target": row.target, "label": row.label}
        if live is None:
            return TunnelStatus(**base, state="stopped")
        if live.done:
            exited = f"exited with {live.process.poll()}"
            return TunnelStatus(**base, state="failed", error=live.error or _last(live) or exited)
        if live.public_url is None:
            return TunnelStatus(**base, state="starting")
        url, count = live.public_url, live.connections
        if count is None:
            # Cloudflare accepts the connection a few seconds after the URL banner.
            if live.silent_checks < SILENT_CHECKS:
                return TunnelStatus(**base, state="starting", public_url=url)
            return TunnelStatus(**base, state="running", public_url=url, since=live.since)
        if count == 0 and not live.connected_once:
            return TunnelStatus(**base, state="starting", public_url=url, connections=0)
        state: State = "running" if count > 0 else "unreachable"
        return TunnelStatus(**base, state=state, public_url=url, since=live.changed, connections=count)

    def statuses(self) -> list[TunnelStatus]:
        return [self.status(r.id) for r in self._rows]

    # --- install ----------------------------------------------------------------------------------

    def install_state(self) -> InstallState:
        if self._install in ("installing", "failed"):
            return self._install
        if self.installed():
            return "installed"
        return "missing" if which("brew") else "no-brew"

    def install_error(self) -> str | None:
        return self._install_error

    def install(self) -> None:
        """`brew install cloudflared` in the background; the page polls install_state()."""
        if self.install_state() != "missing":
            return
        self._install = "installing"

        def run() -> None:
            result = sh("brew", ["install", "cloudflared"], allow_failure=True)
            if result.code == 0 and self.installed():
                self._install = None
            else:
                self._install, self._install_error = (
                    "failed",
                    (result.stderr.strip() or result.stdout.strip())[-400:],
                )

        threading.Thread(target=run, daemon=True, name="install-cloudflared").start()
