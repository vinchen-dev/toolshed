"""Cloudflare quick tunnels started from the dashboard: expose a local URL at a random trycloudflare.com
address until it is stopped. Rows persist with whether each should be on and the process that serves it.
The cloudflared processes outlive the dashboard: the next one picks them up, or starts them again when
they are gone, as after a reboot. The launcher, process lookup, binary lookup, port picker and health
probe are seams, so tests never run cloudflared."""

from __future__ import annotations

import json
import os
import secrets
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from shutil import which
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel

from shed.lib.manila import MANILA
from shed.lib.shell import sh
from shed.tunnel_process import (
    Adopted,
    Live,
    command_line,
    end,
    free_port,
    ready_connections,
    spawn_cloudflared,
)

LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]", "host.docker.internal")
State = Literal["stopped", "starting", "running", "unreachable", "failed"]
InstallState = Literal["installed", "missing", "installing", "failed", "no-brew"]
CHECK_EVERY_S = 3.0
FOLLOW_EVERY_S = 0.2
"""How often a tunnel's log is read for new lines."""
SILENT_CHECKS = 5
"""Unanswered health checks after the URL banner before the banner alone counts as running."""
SLEEP_GAP_S = 30.0
"""A wall-clock jump this much longer than the check interval means the Mac slept between two checks."""


class TunnelRun(BaseModel):
    """The cloudflared process that serves a tunnel, kept so that a later dashboard can pick it up."""

    pid: int
    metrics_port: int
    started: datetime


class TunnelRow(BaseModel):
    id: str
    target: str
    label: str | None = None
    created: datetime
    on: bool = False
    """Whether it should be running: start sets it, stop clears it, a starting dashboard acts on it."""
    run: TunnelRun | None = None


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
    last_error: str | None = None
    """When unreachable: cloudflared's latest ERR line since the connection was last up."""
    slept_from: datetime | None = None
    slept_until: datetime | None = None
    asleep_s: int | None = None
    """When unreachable: how long the Mac slept between those times, since the connection was last up."""


def cloudflared_binary() -> str | None:
    return which("cloudflared")


def _check_target(target: str) -> str:
    target = target.strip()
    parsed = urlparse(target)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("target must look like http://host:port")
    if parsed.hostname not in LOCAL_HOSTS:
        raise ValueError("only local targets are tunnelled (localhost, 127.0.0.1, ...)")
    return target.rstrip("/")


def now() -> datetime:
    return datetime.now(MANILA).replace(microsecond=0)


def _stamp(wall: float) -> datetime:
    return datetime.fromtimestamp(wall, MANILA).replace(microsecond=0)


class TunnelManager:
    def __init__(
        self,
        state_file: Path,
        *,
        spawn: Callable[[list[str], Path], Any] = spawn_cloudflared,
        binary: Callable[[], str | None] = cloudflared_binary,
        metrics_port: Callable[[], int] = free_port,
        probe: Callable[[int], int | None] = ready_connections,
        check_every: float = CHECK_EVERY_S,
        follow_every: float = FOLLOW_EVERY_S,
        clock: Callable[[], float] = time.time,
        command_of: Callable[[int], str | None] = command_line,
        signal: Callable[[int, int], None] = os.kill,
    ) -> None:
        self.state_file = state_file
        self._spawn = spawn
        self._binary = binary
        self._metrics_port = metrics_port
        self._probe = probe
        self._check_every = check_every
        self._follow_every = follow_every
        self._liveness_every = max(1, round(check_every / follow_every))
        """Idle reads of a tunnel's log between two checks that its process is still alive."""
        self._clock = clock
        self._command_of = command_of
        self._signal = signal
        self._rows: list[TunnelRow] = self._load()
        self._live: dict[str, Live] = {}
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
        with self._lock:
            self._rows = [r for r in self._rows if r.id != tunnel_id]
            self._save()
        self._log(tunnel_id).unlink(missing_ok=True)

    def _log(self, tunnel_id: str) -> Path:
        return self.state_file.parent / "tunnels" / f"{tunnel_id}.log"

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
            log = self._log(tunnel_id)
            process = self._spawn(
                ["cloudflared", "tunnel", "--no-autoupdate", "--metrics", metrics, "--url", row.target], log
            )
            live = Live(process, port, log)
            self._live[tunnel_id] = live
            row.on, row.run = True, TunnelRun(pid=process.pid, metrics_port=port, started=now())
            self._save()
        self._follow_and_watch(live, tunnel_id)

    def _follow_and_watch(self, live: Live, tunnel_id: str) -> None:
        for job, name in ((self._follow, "tunnel"), (self._watch, "tunnel-health")):
            threading.Thread(target=job, args=(live,), daemon=True, name=f"{name}-{tunnel_id}").start()

    def _follow(self, live: Live) -> None:
        """Read the tunnel's log as it grows: the public URL arrives in a boxed banner, and the last lines
        explain a failure. Ends when cloudflared has exited, or when this dashboard lets go of the tunnel."""
        idle, pending = 0, ""
        try:
            with live.log.open(encoding="utf-8", errors="replace") as stream:
                while True:
                    chunk = stream.readline()
                    if chunk.endswith("\n"):
                        live.note((pending + chunk).rstrip(), now())
                        pending = ""
                        continue
                    pending += chunk  # the start of a line cloudflared is still writing, or nothing
                    idle += 1
                    if idle % self._liveness_every == 0 and live.process.poll() is not None:
                        break
                    if live.stopping.wait(self._follow_every):
                        return
                for line in (pending + stream.read()).splitlines():  # what it wrote on its way out
                    live.note(line.rstrip(), now())
        except OSError as exc:
            live.error = f"cannot read {live.log}: {exc.strerror or exc}"
        if live.public_url is None and live.error is None:
            live.error = live.last() or "cloudflared exited before announcing a URL"
        live.done = True

    def _watch(self, live: Live) -> None:
        """Poll the ready endpoint once the URL is out, until cloudflared exits or the tunnel is stopped.
        A sleeping process cannot see the sleep, but the wall clock jumps between two checks."""
        last = self._clock()
        while not live.stopping.wait(self._check_every) and not live.done:
            wall = self._clock()
            if wall - last > self._check_every + SLEEP_GAP_S:
                live.note_sleep(last, wall)
            last = wall
            if live.public_url is None:
                continue
            count = self._probe(live.metrics_port)
            if count is None:
                live.silent_checks += 1
                continue
            if live.connections is None or (live.connections > 0) != (count > 0):
                # a tunnel picked up from an earlier dashboard was connected before this one looked
                picked_up = live.adopted and live.connections is None and count > 0
                live.changed = live.since if picked_up else now()
                if count > 0:
                    live.last_error = None
            live.connections = count
            live.connected_once = live.connected_once or count > 0
            if count > 0:
                live.up_at = wall

    def stop(self, tunnel_id: str) -> None:
        """Stop the tunnel and record that it is off. Its process may be one an earlier dashboard left."""
        with self._lock:
            live = self._live.pop(tunnel_id, None)
            row = next((r for r in self._rows if r.id == tunnel_id), None)
            process = live.process if live is not None else self._recorded(row)
            if row is not None and (row.on or row.run is not None):
                row.on, row.run = False, None
                self._save()
        if live is not None:
            live.stopping.set()
        if process is not None:
            end(process)

    def restart(self, tunnel_id: str) -> None:
        """Stop and start again, which makes a new quick tunnel with a new public URL."""
        self.stop(tunnel_id)
        self.start(tunnel_id)

    def stop_all(self) -> list[TunnelRow]:
        """Stop every tunnel that is on, whichever dashboard started it; returns the rows it stopped."""
        on = [row for row in self.rows() if row.on or row.id in self._live]
        for row in on:
            self.stop(row.id)
        return on

    def close(self) -> None:
        """Let go of every tunnel without stopping it: the processes keep running for the next dashboard."""
        with self._lock:
            lives, self._live = list(self._live.values()), {}
        for live in lives:
            live.stopping.set()

    def resume(self) -> None:
        """Bring back the tunnels that should be on: pick up a cloudflared an earlier dashboard left
        running, or start a new one, with a new public URL, when it is gone (as after a reboot)."""
        for row in self.rows():
            if not row.on or row.id in self._live:
                continue
            process = self._recorded(row)
            if process is not None and row.run is not None and self._log(row.id).is_file():
                self._adopt(row.id, row.run, process)
                continue
            if process is not None:
                end(process)  # still running, but its log is gone, so its URL cannot be read
            try:
                self.start(row.id)
            except (RuntimeError, OSError) as exc:  # it stays off, and the page still offers Start
                print(f"tunnels: could not start {row.target}: {exc}", file=sys.stderr)

    def _adopt(self, tunnel_id: str, run: TunnelRun, process: Adopted) -> None:
        live = Live(process, run.metrics_port, self._log(tunnel_id))
        live.adopted, live.since, live.connected_once = True, run.started, True
        with self._lock:
            self._live[tunnel_id] = live
        self._follow_and_watch(live, tunnel_id)

    def _recorded(self, row: TunnelRow | None) -> Adopted | None:
        """The process the row says serves it, when that process still runs the tunnel."""
        if row is None or row.run is None:
            return None
        process = Adopted(row.run.pid, row.run.metrics_port, self._command_of, self._signal)
        return process if process.poll() is None else None

    def status(self, tunnel_id: str) -> TunnelStatus:
        row = self._row(tunnel_id)
        live = self._live.get(tunnel_id)
        base = {"id": row.id, "target": row.target, "label": row.label}
        if live is None:
            return TunnelStatus(**base, state="stopped")
        if live.done:
            return TunnelStatus(
                **base, state="failed", error=live.error or live.last() or "cloudflared exited"
            )
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
        if count > 0:
            return TunnelStatus(
                **base, state="running", public_url=url, since=live.changed, connections=count
            )
        why: dict[str, Any] = {"last_error": live.last_error}
        if live.sleep is not None and (live.up_at is None or live.sleep[1] > live.up_at):
            start, end, asleep = live.sleep
            why |= {"slept_from": _stamp(start), "slept_until": _stamp(end), "asleep_s": round(asleep)}
        return TunnelStatus(
            **base, state="unreachable", public_url=url, since=live.changed, connections=0, **why
        )

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
