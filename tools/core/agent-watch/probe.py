"""The live machine behind agent-watch: the process table, the listening TCP ports and the clock.

Tests replace the module-level ``probe`` object; nothing else in the tool looks at the machine's processes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from shutil import which

from shed import ToolError, sh

TIMEOUT_S = 10
LSOF_PATHS = ("/usr/sbin/lsof", "/usr/bin/lsof")
"""Where macOS and Linux keep lsof. The dashboard's launchd agent runs without /usr/sbin on its PATH."""
_PS_COLUMNS = "pid=,ppid=,pgid=,lstart=,pcpu=,command="
_LSTART = "%a %b %d %H:%M:%S %Y"


@dataclass(frozen=True, slots=True)
class Proc:
    pid: int
    ppid: int
    pgid: int
    started: datetime
    """When the process started, timezone-aware."""
    cpu: float
    """Recent CPU use in percent, as `ps` reports it."""
    command: str


class Probe:
    def now(self) -> datetime:
        return datetime.now(UTC)

    def processes(self) -> list[Proc]:
        """Every process on this machine, from one `ps` call."""
        try:
            # C locale so the start time's day and month names parse the same on every machine
            out = sh("ps", ["-axo", _PS_COLUMNS], env={**os.environ, "LC_ALL": "C"}, timeout=TIMEOUT_S)
        except UnicodeDecodeError as exc:
            raise ToolError(f"ps output is not readable text: {exc}") from exc
        procs: list[Proc] = []
        for line in out.stdout.splitlines():
            parts = line.split(None, 9)
            if len(parts) < 10:
                continue
            try:
                started = datetime.strptime(" ".join(parts[3:8]), _LSTART).astimezone()
                procs.append(
                    Proc(int(parts[0]), int(parts[1]), int(parts[2]), started, float(parts[8]), parts[9])
                )
            except ValueError:
                continue
        return procs

    def listening_ports(self) -> dict[int, list[int]]:
        """Listening TCP ports per pid, from one `lsof` call; lsof exits 1 when nothing listens."""
        lsof = which("lsof") or next((path for path in LSOF_PATHS if Path(path).is_file()), "lsof")
        out = sh(lsof, ["-nP", "-iTCP", "-sTCP:LISTEN", "-Fpn"], allow_failure=True, timeout=TIMEOUT_S)
        ports: dict[int, list[int]] = {}
        pid: int | None = None
        for line in out.stdout.splitlines():
            if line.startswith("p"):
                pid = int(line[1:]) if line[1:].isdigit() else None
            elif line.startswith("n") and pid is not None:
                port = line.rsplit(":", 1)[-1]
                if port.isdigit() and int(port) not in ports.setdefault(pid, []):
                    ports[pid].append(int(port))
        return ports


probe = Probe()
