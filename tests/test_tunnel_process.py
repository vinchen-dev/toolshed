from __future__ import annotations

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from shed import ShResult, tunnel_process
from shed.lib.errors import ToolError


def wait_for(check: Any, timeout: float = 3) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.02)


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
        assert tunnel_process.ready_connections(port) == 0
        assert tunnel_process.ready_connections(port) == 2
    finally:
        srv.shutdown()
        srv.server_close()
    assert tunnel_process.ready_connections(port) is None


SLEEPER = "import time; print('up', flush=True); time.sleep(30)"


def test_cloudflared_is_started_in_its_own_session_with_its_output_in_the_tunnels_log(tmp_path: Path) -> None:
    log = tmp_path / "tunnels" / "t.log"
    process = tunnel_process.spawn_cloudflared([sys.executable, "-c", SLEEPER], log)
    try:
        wait_for(lambda: log.read_text() == "up\n")
        assert os.getsid(process.pid) == process.pid != os.getsid(0)
    finally:
        process.kill()
        process.wait(timeout=5)


def test_an_adopted_process_is_reached_by_pid_only_while_it_still_runs_the_tunnel(tmp_path: Path) -> None:
    args = [sys.executable, "-c", SLEEPER, "--metrics", "127.0.0.1:20999"]
    process = tunnel_process.spawn_cloudflared(args, tmp_path / "t.log")
    try:
        assert "--metrics 127.0.0.1:20999" in (tunnel_process.command_line(process.pid) or "")
        assert tunnel_process.Adopted(process.pid, 20400).poll() is not None, "another tunnel's process"
        adopted = tunnel_process.Adopted(process.pid, 20999)
        assert adopted.poll() is None
        tunnel_process.end(adopted)
        assert adopted.poll() is not None
    finally:
        process.kill()
        process.wait(timeout=5)
    assert tunnel_process.command_line(process.pid) is None


def test_a_process_is_not_identified_when_ps_cannot_be_run(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_ps(cmd: str, args: Any = (), **kwargs: Any) -> ShResult:
        raise ToolError("Command not found: ps")

    monkeypatch.setattr(tunnel_process, "sh", no_ps)
    assert tunnel_process.command_line(os.getpid()) is None
    assert tunnel_process.Adopted(os.getpid(), 20400).poll() is not None
