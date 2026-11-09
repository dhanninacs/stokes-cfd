"""The agent server: localhost only, token handshake, concurrent requests per connection."""

from __future__ import annotations

import asyncio
import inspect
import secrets
from collections.abc import Awaitable, Callable
from typing import Any

from stokes.errors import ProtocolError, StokesError
from stokes.log import fields, get_logger
from stokes_agent import __version__, protocol
from stokes_agent.probe import probe_host

log = get_logger(__name__)

Handler = Callable[..., Any]
AGENT_NAME = f"stokes-agent/{__version__}"


class AgentServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 0, token: str | None = None) -> None:
        if host not in {"127.0.0.1", "::1", "localhost"}:
            raise ProtocolError("the agent binds to loopback only; use an SSH tunnel for remote")
        self.host = host
        self.port = port
        self.token = token or secrets.token_urlsafe(24)
        self._server: asyncio.AbstractServer | None = None
        self._writers: set[asyncio.StreamWriter] = set()
        self._write_locks: dict[asyncio.StreamWriter, asyncio.Lock] = {}
        self._methods: dict[str, Handler] = {}
        self._stop = asyncio.Event()
        self.register("ping", lambda: {"pong": True})
        self.register("probe", probe_host)
        self.register(
            "agent.info", lambda: {"agent": AGENT_NAME, "protocol": protocol.PROTOCOL_VERSION}
        )

    # ------------------------------------------------------------------ registry
    def register(self, method: str, handler: Handler) -> None:
        self._methods[method] = handler

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> tuple[str, int]:
        self._server = await asyncio.start_server(self._handle, self.host, self.port)
        sockets = list(self._server.sockets or ())
        if sockets:
            self.port = int(sockets[0].getsockname()[1])
        log.info("agent listening", extra=fields(host=self.host, port=self.port))
        return self.host, self.port

    async def serve_forever(self) -> None:
        if self._server is None:
            await self.start()
        await self._stop.wait()
        await self.stop()

    async def stop(self) -> None:
        self._stop.set()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        for writer in list(self._writers):
            writer.close()
        self._writers.clear()
        self._write_locks.clear()

    def request_stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------ connections
    async def _send(self, writer: asyncio.StreamWriter, message: dict[str, Any]) -> None:
        lock = self._write_locks.setdefault(writer, asyncio.Lock())
        async with lock:
            writer.write(protocol.encode(message))
            await writer.drain()

    async def broadcast(self, name: str, data: dict[str, Any]) -> None:
        message = protocol.event(name, data)
        for writer in list(self._writers):
            try:
                await self._send(writer, message)
            except (ConnectionError, OSError):
                self._writers.discard(writer)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        try:
            first = await asyncio.wait_for(protocol.read_message(reader), timeout=10)
            if (
                first is None
                or first.get("kind") != "hello"
                or first.get("protocol") != protocol.PROTOCOL_VERSION
                or not secrets.compare_digest(str(first.get("token", "")), self.token)
            ):
                await self._send(writer, protocol.error_response("", "handshake rejected"))
                log.warning("handshake rejected", extra=fields(peer=str(peer)))
                return
            await self._send(writer, protocol.hello_ok(AGENT_NAME))
            self._writers.add(writer)
            tasks: set[asyncio.Task[None]] = set()
            while True:
                message = await protocol.read_message(reader)
                if message is None:
                    break
                if message.get("kind") != "request":
                    continue
                task = asyncio.create_task(self._dispatch(writer, message))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
            for task in tasks:
                task.cancel()
        except (ProtocolError, TimeoutError, ConnectionError, OSError) as exc:
            log.warning("connection ended", extra=fields(peer=str(peer), reason=str(exc)))
        finally:
            self._writers.discard(writer)
            self._write_locks.pop(writer, None)
            writer.close()

    async def _dispatch(self, writer: asyncio.StreamWriter, message: dict[str, Any]) -> None:
        request_id = str(message.get("id", ""))
        method = str(message.get("method", ""))
        params = message.get("params") or {}
        handler = self._methods.get(method)
        if handler is None:
            await self._send(
                writer, protocol.error_response(request_id, f"unknown method {method!r}")
            )
            return
        if not isinstance(params, dict):
            await self._send(writer, protocol.error_response(request_id, "params must be a map"))
            return
        try:
            result = handler(**params)
            if inspect.isawaitable(result):
                result = await result
            await self._send(writer, protocol.response(request_id, result))
        except StokesError as exc:
            await self._send(writer, protocol.error_response(request_id, exc))
        except TypeError as exc:
            await self._send(writer, protocol.error_response(request_id, f"bad parameters: {exc}"))
        except Exception as exc:
            log.exception("handler failed", extra=fields(method=method))
            await self._send(writer, protocol.error_response(request_id, f"internal error: {exc}"))


AwaitableHandler = Callable[..., Awaitable[Any]]
