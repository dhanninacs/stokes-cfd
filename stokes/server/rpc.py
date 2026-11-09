"""JSON-RPC over WebSocket onto one :class:`~stokes.api.Session` (ADR-0010).

Messages from the client::

    {"id": 7, "method": "parameter.set", "params": {"node": "...", "name": "value", "value": "3 [m/s]"}}

Replies::

    {"id": 7, "result": {...}}      or      {"id": 7, "error": {"code": "...", "message": "...", "details": {}}}

Events pushed to every client::

    {"event": "node.changed", "data": {...}}

All commands run on the server's event-loop thread, which serialises access to
the session. Events raised while a command runs follow its reply. Events raised
on other threads (a geometry import, for one) go out at once: they are handed to
each client's event loop with ``call_soon_threadsafe``. Every client has one
outgoing queue drained by one task, so its messages keep their order and never
interleave. Events raised while no client is connected are not kept; a client that connects to an open
project is sent one ``project.opened`` (marked ``replay``) first, so it sees the project at once.

One command does not hold the loop for its whole run: ``node.tree`` of thousands of nodes is
tens of milliseconds of work, so it is built and written in fragments and the loop is handed
back between them (:meth:`RpcHub._tree_reply`). It stays on the loop thread, where every other
command runs, rather than on a worker thread: the session is not safe to read from two threads at
once (``Session.execute`` keeps a call depth), and a snapshot cut in slices is repaired by the
node events that follow a change, which carry the summaries (:mod:`stokes.api.summaries`).
"""

from __future__ import annotations

import asyncio
import json
import math
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from stokes.api import Session
from stokes.api.summaries import tree_stream
from stokes.errors import StokesError
from stokes.log import fields, get_logger

log = get_logger(__name__)
MAX_MESSAGE_BYTES = 4 * 1024 * 1024
TREE_SLICE_SECONDS = 0.003
"""Longest a ``node.tree`` reply keeps the event loop before it hands it back."""


def _finite(value: Any) -> Any:
    """``value`` with every NaN and infinity replaced by None, at any depth."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: _finite(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_finite(item) for item in value]
    return value


def dumps(message: Any) -> str:
    """One message as JSON a browser can read: a NaN or an infinity goes out as null.

    **Python writes `NaN` and `Infinity` by default, and they are not JSON.** A browser's
    `JSON.parse` throws on them, so the client dropped the whole reply and the request it answered
    never settled. Measured on the mixing elbow (2026-11-08): a diverged run's monitors are NaN, so
    every `run.list` after it went unanswered and the Run button stayed on "Starting…" for good.
    The common message has no such value and is written once; only one that has is cleaned.
    """
    try:
        return json.dumps(message, default=str, allow_nan=False)
    except ValueError:
        return json.dumps(_finite(message), default=str, allow_nan=False)


@dataclass(eq=False)
class _Client:
    ws: WebSocket
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue[str] = field(default_factory=asyncio.Queue)


class RpcHub:
    def __init__(self, session: Session) -> None:
        self.session = session
        self._clients: list[_Client] = []
        self._lock = threading.Lock()
        self._pending_events: list[str] = []
        #: How many commands are executing on the loop right now (0 or 1 in practice): only an
        #: event raised INSIDE a command is held back until the reply has gone (`_collect`).
        self._executing = 0
        session.events.subscribe("*", self._collect)

    # ------------------------------------------------------------------ events
    def _collect(self, event: str, data: dict[str, Any]) -> None:
        """Send an event to every client: at once, or after the reply of the command raising it.

        **Held back only while a command is executing** (`_executing`), not whenever the caller is
        on the loop. Services finish their work on the loop between commands (the mesh service's
        `_finish_from_job`, the geometry store's dispatch), and an event raised there used to wait
        in `_pending_events` for the NEXT request to flush it: measured, the Mesh card showed
        "Generating… checkMesh" for two minutes after `mesh.status` said done, until something
        else asked the server a question.
        """
        text = dumps({"event": event, "data": data})
        with self._lock:
            if self._executing > 0 and self._on_client_loop():
                self._pending_events.append(text)  # sent after the reply of the running command
                return
            clients = list(self._clients)
        for client in clients:
            self._post(client, text)

    def _on_client_loop(self) -> bool:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        return any(c.loop is loop for c in self._clients)

    def _post(self, client: _Client, text: str) -> None:
        """Queue ``text`` for ``client`` from any thread."""
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is client.loop:
            client.queue.put_nowait(text)
            return
        try:
            client.loop.call_soon_threadsafe(client.queue.put_nowait, text)
        except RuntimeError:  # the client's loop has closed
            self._drop(client)

    def _flush_events(self) -> None:
        with self._lock:
            batch, self._pending_events = self._pending_events, []
            clients = list(self._clients)
        for text in batch:
            for client in clients:
                self._post(client, text)

    async def broadcast(self, message: dict[str, Any]) -> None:
        text = dumps(message)
        with self._lock:
            clients = list(self._clients)
        for client in clients:
            self._post(client, text)

    def _drop(self, client: _Client) -> None:
        with self._lock:
            if client in self._clients:
                self._clients.remove(client)

    async def _sender(self, client: _Client) -> None:
        while True:
            text = await client.queue.get()
            try:
                await client.ws.send_text(text)
            except Exception:
                log.debug("client send failed", exc_info=True)
                self._drop(client)
                return

    # ------------------------------------------------------------------ commands
    def dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}
        if not isinstance(method, str) or not isinstance(params, dict):
            return {
                "id": request_id,
                "error": {
                    "code": "rpc.invalid",
                    "message": "method and params required",
                    "details": {},
                },
            }
        if method == "rpc.ping":
            return {"id": request_id, "result": {"pong": True}}
        self._executing += 1
        try:
            result = self.session.execute(method, **params)
        except StokesError as exc:
            return {"id": request_id, "error": exc.to_dict()}
        except Exception as exc:
            log.exception("command failed", extra=fields(method=method))
            return {
                "id": request_id,
                "error": {"code": "internal", "message": str(exc), "details": {}},
            }
        finally:
            self._executing -= 1
        return {"id": request_id, "result": result}

    async def _tree_reply(self, request: dict[str, Any]) -> str:
        """The reply to a ``node.tree`` request, as JSON text, built without holding the loop.

        The tree is made in fragments (:func:`~stokes.api.summaries.tree_stream`) and the loop gets
        control back whenever one slice of work has run for :data:`TREE_SLICE_SECONDS`, so the
        events and replies of other clients, and the next frame of a client's own work, are never
        more than a few milliseconds late behind a refresh. The answer is the same as the one
        ``Session.execute("node.tree", ...)`` gives, and errors have the same shape.
        """
        request_id = request.get("id")
        try:
            definition = self.session.registry.get("node.tree")
            params = definition.validate(request.get("params") or {})
            project = self.session.require_project()
            root = params.get("root")
            start = project.get(root) if root else project.root
            fragments: list[str] = []
            deadline = time.perf_counter() + TREE_SLICE_SECONDS
            for fragment in tree_stream(project, start, params.get("depth")):
                fragments.append(fragment)
                if time.perf_counter() >= deadline:
                    await asyncio.sleep(0)
                    deadline = time.perf_counter() + TREE_SLICE_SECONDS
        except StokesError as exc:
            return json.dumps({"id": request_id, "error": exc.to_dict()}, default=str)
        except Exception as exc:
            log.exception("command failed", extra=fields(method="node.tree"))
            error = {"code": "internal", "message": str(exc), "details": {}}
            return json.dumps({"id": request_id, "error": error})
        return f'{{"id":{json.dumps(request_id)},"result":{"".join(fragments)}}}'

    async def serve(
        self, ws: WebSocket, *, registered: Callable[[], Awaitable[None]] | None = None
    ) -> None:
        """Serve one client until it disconnects.

        ``registered`` runs once the client is registered and before its first request is read.
        Work that starts there reports to this client from its first event: started earlier, its
        events would reach nobody, because they are not kept for a client that is not connected.
        """
        await ws.accept()
        client = _Client(ws, asyncio.get_running_loop())
        with self._lock:
            self._clients.append(client)
            count = len(self._clients)
        sender = asyncio.create_task(self._sender(client))
        log.info("client connected", extra=fields(clients=count))
        try:
            # **A client that connects to an open project is told so first**, as a fresh open tells
            # it: it missed the `project.opened` emit, and events are not kept for a client that was
            # not connected. `replay` marks it, so a client that reconnects (and already holds the
            # project) can ignore it. Queued before `registered`, so what activation reports about
            # the project's parts follows it.
            opened = self.session.project_opened_data()
            if opened is not None:
                client.queue.put_nowait(
                    dumps({"event": "project.opened", "data": {**opened, "replay": True}})
                )
            if registered is not None:
                await registered()
            while True:
                text = await ws.receive_text()
                if len(text) > MAX_MESSAGE_BYTES:
                    client.queue.put_nowait(
                        json.dumps(
                            {
                                "id": None,
                                "error": {
                                    "code": "rpc.too_large",
                                    "message": "message too large",
                                    "details": {},
                                },
                            }
                        )
                    )
                    continue
                try:
                    request = json.loads(text)
                except ValueError:
                    client.queue.put_nowait(
                        json.dumps(
                            {
                                "id": None,
                                "error": {
                                    "code": "rpc.invalid",
                                    "message": "not JSON",
                                    "details": {},
                                },
                            }
                        )
                    )
                    continue
                if not isinstance(request, dict):
                    continue
                if request.get("method") == "node.tree" and isinstance(
                    request.get("params") or {}, dict
                ):
                    client.queue.put_nowait(await self._tree_reply(request))
                else:
                    client.queue.put_nowait(dumps(self.dispatch(request)))
                self._flush_events()
        except WebSocketDisconnect:
            pass
        finally:
            self._drop(client)
            sender.cancel()
            log.info("client disconnected", extra=fields(clients=len(self._clients)))
