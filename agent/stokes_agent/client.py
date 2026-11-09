"""Asyncio client for the agent protocol; used by the desktop runners and tests."""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from typing import Any

from stokes.errors import ProtocolError, StokesError
from stokes_agent import protocol


class AgentError(StokesError):
    code = "agent.remote"


class AgentClient:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._reader = reader
        self._writer = writer
        self._pending: dict[str, asyncio.Future[Any]] = {}
        self.events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._reader_task: asyncio.Task[None] | None = None
        self.agent: str = ""

    @classmethod
    async def connect(
        cls, host: str, port: int, token: str, client_name: str = "stokes-desktop"
    ) -> AgentClient:
        reader, writer = await asyncio.open_connection(host, port)
        client = cls(reader, writer)
        writer.write(protocol.encode(protocol.hello(token, client_name)))
        await writer.drain()
        reply = await asyncio.wait_for(protocol.read_message(reader), timeout=10)
        if reply is None or reply.get("kind") != "hello" or not reply.get("ok"):
            writer.close()
            raise ProtocolError("agent rejected the handshake")
        client.agent = str(reply.get("agent", ""))
        client._reader_task = asyncio.create_task(client._read_loop())
        return client

    @classmethod
    async def connect_mutual(
        cls, host: str, port: int, secret: str, client_name: str = "stokes-desktop"
    ) -> AgentClient:
        """**The v3 connect: prove ourselves to the agent, and it proves itself to us** (T-159a).

        **The agent's proof is checked before ours is sent.** A host that does not hold the secret
        therefore never receives anything derived from it, which matters when the thing on the other
        end is not who the caller thinks: the caller learns it is talking to the wrong machine
        without having told that machine anything.
        """
        from stokes_agent import handshake

        reader, writer = await asyncio.open_connection(host, port)
        client = cls(reader, writer)
        nonce_c = handshake.new_nonce()
        writer.write(protocol.encode(protocol.hello_nonce(nonce_c, client_name)))
        await writer.drain()
        reply = await asyncio.wait_for(protocol.read_message(reader), timeout=10)
        if reply is None or reply.get("kind") != "hello" or not reply.get("nonce"):
            writer.close()
            raise ProtocolError("the agent did not answer the handshake")
        nonce_a = str(reply["nonce"])
        expected = handshake.agent_proof(secret, nonce_c, nonce_a)
        if not handshake.check_proof(expected, reply.get("proof")):
            writer.close()
            # **The same message as any other handshake failure.** Which half failed is not the
            # client's business either: a peer that is not the agent should learn nothing.
            raise ProtocolError("handshake rejected")
        writer.write(
            protocol.encode(
                protocol.hello_proof(handshake.client_proof(secret, nonce_c, nonce_a), client_name)
            )
        )
        await writer.drain()
        final = await asyncio.wait_for(protocol.read_message(reader), timeout=10)
        if final is None or final.get("kind") != "hello" or not final.get("proof"):
            writer.close()
            raise ProtocolError("handshake rejected")
        client.agent = str(final.get("agent", ""))
        client._reader_task = asyncio.create_task(client._read_loop())
        return client

    async def _read_loop(self) -> None:
        try:
            while True:
                message = await protocol.read_message(self._reader)
                if message is None:
                    break
                kind = message.get("kind")
                if kind == "response":
                    future = self._pending.pop(str(message.get("id")), None)
                    if future is not None and not future.done():
                        if message.get("ok"):
                            future.set_result(message.get("result"))
                        else:
                            err = message.get("error") or {}
                            # **The server's code becomes the exception's code.** It was put in
                            # `details["remote_code"]` while the exception kept the generic
                            # `agent.remote`, so `except ... as e: e.code == "agent.busy"` never
                            # matched and every client had to dig into details to tell a refusal
                            # from a
                            # transport failure. A typed error is only worth having if its type
                            # survives
                            # the wire.
                            future.set_exception(
                                AgentError(
                                    str(err.get("message", "agent error")),
                                    code=str(err.get("code") or AgentError.code),
                                    details={
                                        "remote_code": err.get("code"),
                                        **(err.get("details") or {}),
                                    },
                                )
                            )
                elif kind == "event":
                    await self.events.put(message)
        except (ProtocolError, ConnectionError, OSError) as exc:
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(ProtocolError(f"connection lost: {exc}"))
            self._pending.clear()

    async def call(self, method: str, *, timeout: float | None = 60.0, **params: Any) -> Any:
        request_id = uuid.uuid4().hex
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        self._writer.write(protocol.encode(protocol.request(request_id, method, params)))
        await self._writer.drain()
        return await asyncio.wait_for(future, timeout=timeout)

    async def close(self) -> None:
        if self._reader_task is not None:
            self._reader_task.cancel()
        self._writer.close()
        with contextlib.suppress(ConnectionError, OSError):
            await self._writer.wait_closed()
