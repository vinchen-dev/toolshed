"""The cloudflared process behind a tunnel, and what a dashboard reads from it. It runs in a session of its
own with its output in a log file, so it outlives the dashboard that started it; a later dashboard reaches
it by pid. Its health comes from its own ready endpoint on a local metrics port."""

from __future__ import annotations

import contextlib
import json
import os
import re
import signal
import socket
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import datetime
from http.client import HTTPConnection, HTTPException
from pathlib import Path
from typing import Any

from shed.lib.errors import ToolError
from shed.lib.shell import sh

STOP_WAIT_S = 5.0
PS_TIMEOUT_S = 5.0
EXIT_UNKNOWN = 0
"""What `Adopted.poll` answers once the process is gone: only a parent can read an exit code."""


PUBLIC_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
ERROR_LINE = re.compile(r"^\S+\s+ERR\s+(?P<message>.+)$")


class Live:
    """A tunnel this dashboard follows: the process, its log, the URL once announced, its last lines."""

    def __init__(self, process: Any, metrics_port: int, log: Path) -> None:
        self.process = process
        self.metrics_port = metrics_port
        self.log = log
        self.adopted = False
        """True when an earlier dashboard started the process."""
        self.public_url: str | None = None
        self.since: datetime | None = None
        self.error: str | None = None
        self.tail: deque[str] = deque(maxlen=20)
        self.done = False
        """Set once cloudflared has exited and its log has been read to the end."""
        self.connections: int | None = None
        self.connected_once = False
        self.changed: datetime | None = None
        """When the connection count last went between zero and non-zero."""
        self.silent_checks = 0
        self.stopping = threading.Event()
        self.last_error: str | None = None
        self.up_at: float | None = None
        """Wall clock of the last check that found a Cloudflare connection."""
        self.sleep: tuple[float, float, float] | None = None
        """(first sleep start, last wake, seconds asleep) for the sleeps since `up_at`."""

    def note_sleep(self, start: float, end: float) -> None:
        if self.sleep is None or (self.up_at is not None and self.sleep[1] < self.up_at):
            self.sleep = (start, end, end - start)
        else:
            self.sleep = (self.sleep[0], end, self.sleep[2] + end - start)

    def note(self, text: str, at: datetime) -> None:
        """Take in one line cloudflared printed around ``at``: the public URL, its latest error, the tail."""
        self.tail.append(text)
        if self.public_url is None and (m := PUBLIC_URL.search(text)):
            self.public_url, self.since = m.group(0), self.since or at
        if m := ERROR_LINE.match(text):
            self.last_error = m.group("message")[:300]

    def last(self) -> str | None:
        """The last line it printed that was not empty."""
        return next((line for line in reversed(self.tail) if line), None)


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


def spawn_cloudflared(args: list[str], log: Path) -> Any:
    """Start the tunnel in its own session, writing to ``log``. launchd ends a job's process group when
    the dashboard stops; a process outside that group keeps running."""
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w", encoding="utf-8") as out:
        return subprocess.Popen(
            args, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, start_new_session=True
        )


def command_line(pid: int) -> str | None:
    """The command line of a live process, or None when there is no such process or `ps` cannot say."""
    try:
        found = sh("ps", ["-o", "command=", "-p", str(pid)], allow_failure=True, timeout=PS_TIMEOUT_S)
    except ToolError:  # ps is missing or hung: the process cannot be identified, so it is left alone
        return None
    return (found.stdout.strip() or None) if found.code == 0 else None


class Adopted:
    """A cloudflared that an earlier dashboard started: not a child of this one, so it is reached by pid.
    A pid is reused once it is free, so every use first checks that it still runs that tunnel."""

    def __init__(
        self,
        pid: int,
        metrics_port: int,
        command_of: Callable[[int], str | None] = command_line,
        send: Callable[[int, int], None] = os.kill,
    ) -> None:
        self.pid = pid
        self._flag = f"--metrics 127.0.0.1:{metrics_port}"
        self._command_of = command_of
        self._send = send

    def poll(self) -> int | None:
        """None while the tunnel's process runs, as `Popen.poll` answers."""
        return None if self._flag in (self._command_of(self.pid) or "") else EXIT_UNKNOWN

    def terminate(self) -> None:
        self._signal(signal.SIGTERM)

    def kill(self) -> None:
        self._signal(signal.SIGKILL)

    def wait(self, timeout: float) -> int:
        deadline = time.monotonic() + timeout
        while self.poll() is None:
            if time.monotonic() > deadline:
                raise subprocess.TimeoutExpired(self._flag, timeout)
            time.sleep(0.1)
        return EXIT_UNKNOWN

    def _signal(self, sig: int) -> None:
        if self.poll() is not None:
            return
        with contextlib.suppress(ProcessLookupError):  # it can exit between the check and the signal
            self._send(self.pid, sig)


def end(process: Any) -> None:
    """Stop a tunnel's process: ask it to exit, and kill it when it has not done so in time."""
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=STOP_WAIT_S)
    except subprocess.TimeoutExpired:
        process.kill()
