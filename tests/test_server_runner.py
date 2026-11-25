"""The server thread keeps listening when clients reset their connections (Windows, 2026-11-25).

CPython's proactor accept loop closes the listening socket on `WinError 64`, which a client that
resets before the accept completes causes; the server then refused every request while it looked
alive. `ServerThread` runs on a selector loop on Windows, and this test resets hundreds of
connections from four threads and asks the server to answer afterwards. Measured on the proactor
loop, the same burst killed the listener in its first round.
"""

from __future__ import annotations

import socket
import struct
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI

from stokes.server.runner import ServerThread


def _reset_burst(port: int, count: int) -> None:
    """Open and at once reset `count` connections (SO_LINGER 0 sends an RST on close)."""
    for _ in range(count):
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.settimeout(2)
        client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        try:
            client.connect(("127.0.0.1", port))
        except OSError:
            return
        finally:
            client.close()


@pytest.mark.skipif(sys.platform != "win32", reason="the proactor accept loop is Windows-only")
def test_the_server_keeps_listening_after_clients_reset_their_connections() -> None:
    app = FastAPI()

    @app.get("/ping")
    def ping() -> dict[str, bool]:
        return {"ok": True}

    server = ServerThread(app)
    url = server.start()
    try:
        for _ in range(3):
            with ThreadPoolExecutor(4) as pool:
                list(pool.map(lambda _: _reset_burst(server.port, 150), range(4)))
            with urllib.request.urlopen(f"{url}/ping", timeout=5) as reply:
                assert reply.status == 200
    finally:
        server.stop()
