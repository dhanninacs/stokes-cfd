"""Run the server in a background thread on a loopback port chosen by the OS."""

from __future__ import annotations

import socket
import threading
import time

from fastapi import FastAPI

from stokes.errors import StokesError
from stokes.log import fields, get_logger

log = get_logger(__name__)


class ServerThread:
    def __init__(self, app: FastAPI, host: str = "127.0.0.1", port: int = 0) -> None:
        import uvicorn

        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((host, port))
        self._sock.listen(128)
        self.host, self.port = self._sock.getsockname()[:2]
        # Per-message deflate is off: the socket is on the loopback (or an SSH tunnel, which has its
        # own compression), where it saves nothing, and the library compresses a frame on the event
        # loop: a 2 MB tree of 4,500 parts held the loop for up to 120 ms that way, measured.
        config = uvicorn.Config(
            app,
            host=host,
            port=self.port,
            log_level="warning",
            access_log=False,
            ws_per_message_deflate=False,
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._run, name="stokes-server", daemon=True)

    def _run(self) -> None:
        self._server.run(sockets=[self._sock])

    def start(self, timeout: float = 15.0) -> str:
        self._thread.start()
        deadline = time.monotonic() + timeout
        while not self._server.started:
            if time.monotonic() > deadline:
                raise StokesError("the server did not start in time")
            time.sleep(0.02)
        log.info("server listening", extra=fields(host=self.host, port=self.port))
        return self.url

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)
