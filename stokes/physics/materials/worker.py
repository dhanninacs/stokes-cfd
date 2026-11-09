"""The materials worker process, parent side (ADR-0018 decision 6; the contract's "Evaluation and
caching"; the child is :mod:`stokes.physics.materials.worker_main`).

Measured on CoolProp 8.0.0 (2026-10-04, this repository's laptop): the nanobind wrapper holds
the GIL inside every call, so a HEOS mixture flash (50-110 ms) stalls every other Python thread
for that long, and importing CoolProp holds it for about 3 s while the fluid library loads (a
1 ms sleep loop on the main thread saw a 3.1 s gap while another thread imported it). A thread
cannot keep the API responsive, so every CoolProp, Cantera and REFPROP call runs in one
spawned worker process, on the pattern of :mod:`stokes.geometry.worker`: JSON frames over a
pipe, nothing pickled after the spawn, every reply length-limited and checked.

One worker serves the whole process (sessions share it, as they share CoolProp's global
state); :data:`_lock` serialises requests. It starts on first use, or ahead of time with
:func:`prewarm`, and stays warm. A request that is cancelled waits a moment for the reply
(evaluations come in chunks of about 0.2 s) and kills the worker only if the reply does not
come; a worker that dies or times out is restarted by the next request. REFPROP loads once per
process, so a new settings path restarts the worker.

Import rule: the standard library and this package; never CoolProp or Cantera.
"""

from __future__ import annotations

import json
import multiprocessing
import threading
import time
from dataclasses import dataclass
from multiprocessing.process import BaseProcess
from typing import Any

from stokes.errors import StokesError, ValidationError
from stokes.log import fields, get_logger
from stokes.physics.materials.errors import (
    Cancelled,
    EvaluationFailed,
    FluidUnknown,
    MaterialsError,
    OutOfRange,
    PropertyUnavailable,
    ProviderMissing,
    RefpropMissing,
)
from stokes.physics.materials.worker_main import MAX_REPLY_BYTES, child_main

log = get_logger(__name__)

START_TIMEOUT = 180.0
"""Seconds a new worker may take to start and import a provider (CoolProp: about 4 s here)."""
REQUEST_TIMEOUT = 300.0
CANCEL_GRACE = 2.0
_POLL = 0.05
_ERRORS: dict[str, type[StokesError]] = {
    cls.code: cls
    for cls in (
        Cancelled,
        EvaluationFailed,
        FluidUnknown,
        MaterialsError,
        OutOfRange,
        PropertyUnavailable,
        ProviderMissing,
        RefpropMissing,
        ValidationError,
    )
}


class WorkerDied(MaterialsError):
    """The worker process stopped or stopped answering."""

    code = "materials.worker"


@dataclass
class _Process:
    process: BaseProcess
    conn: Any  # Any: a Connection on POSIX, a PipeConnection on Windows (typeshed splits them)
    refprop_path: str | None
    started: float


class _Slot:
    """The process-wide worker: one slot, its lock serialising requests."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.current: _Process | None = None


_slot = _Slot()


def _spawn(refprop_path: str | None) -> _Process:
    ctx = multiprocessing.get_context("spawn")
    parent, child = ctx.Pipe(duplex=True)
    process = ctx.Process(target=child_main, args=(child,), name="stokes-materials", daemon=True)
    process.start()
    child.close()
    worker = _Process(process, parent, refprop_path, time.monotonic())
    ready = _receive(worker, time.monotonic() + START_TIMEOUT, None)
    if ready.get("type") != "ready":
        _kill(worker)
        raise WorkerDied("the materials worker did not start")
    log.debug("materials worker started", extra=fields(pid=process.pid))
    return worker


def _kill(worker: _Process) -> None:
    process = worker.process
    if process.is_alive():
        process.kill()
    process.join(5)
    worker.conn.close()


def _receive(worker: _Process, deadline: float, cancel: threading.Event | None) -> dict[str, Any]:
    """The next reply; on cancel, a short grace before the worker is given up."""
    conn = worker.conn
    cancelled_at: float | None = None
    while True:
        now = time.monotonic()
        if cancel is not None and cancel.is_set() and cancelled_at is None:
            cancelled_at = now
        if cancelled_at is not None and now - cancelled_at > CANCEL_GRACE:
            _kill(worker)
            raise Cancelled("the provider request was cancelled")
        if now > deadline:
            _kill(worker)
            raise WorkerDied("the materials worker did not answer in time")
        try:
            if conn.poll(_POLL):
                frame = conn.recv_bytes(MAX_REPLY_BYTES)
                break
        except (EOFError, OSError) as exc:
            _kill(worker)
            raise WorkerDied(
                f"the materials worker stopped (exit code {worker.process.exitcode})"
            ) from exc
        if not worker.process.is_alive():
            _kill(worker)
            raise WorkerDied(f"the materials worker stopped (exit code {worker.process.exitcode})")
    try:
        message = json.loads(frame.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise WorkerDied("the materials worker sent a malformed reply") from exc
    if not isinstance(message, dict):
        raise WorkerDied("the materials worker sent a malformed reply")
    if cancelled_at is not None:
        raise Cancelled("the provider request was cancelled")
    return message


def _worker(refprop_path: str | None) -> _Process:
    """The live worker, started or restarted as needed (call with the lock held)."""
    found = _slot.current
    if found is not None and found.process.is_alive() and found.refprop_path == refprop_path:
        return found
    if found is not None:
        _kill(found)
    _slot.current = None
    _slot.current = _spawn(refprop_path)
    return _slot.current


def request(
    message: dict[str, Any],  # Any: JSON request
    *,
    refprop_path: str | None = None,
    timeout: float = REQUEST_TIMEOUT,
    cancel: threading.Event | None = None,
) -> dict[str, Any]:  # Any: JSON reply
    """Send one request and return the reply's payload; a typed error for an error reply."""
    payload = {**message, "refprop_path": refprop_path}
    with _slot.lock:
        worker = _worker(refprop_path)
        try:
            worker.conn.send_bytes(json.dumps(payload, allow_nan=False).encode("utf-8"))
        except (OSError, ValueError) as exc:
            _kill(worker)
            _slot.current = None
            raise WorkerDied(f"the materials worker cannot be reached: {exc}") from exc
        try:
            reply = _receive(worker, time.monotonic() + timeout, cancel)
        except (WorkerDied, Cancelled):
            if not worker.process.is_alive():
                _slot.current = None
            raise
    if reply.get("type") == "ok":
        return reply
    code = str(reply.get("code", "materials.evaluation"))
    text = str(reply.get("message", "the provider failed"))[:4000]
    details = reply.get("details")
    raise _ERRORS.get(code, EvaluationFailed)(
        text, details=details if isinstance(details, dict) else None
    )


def prewarm(
    providers: tuple[str, ...] = ("coolprop", "cantera"), refprop_path: str | None = None
) -> None:
    """Start the worker and import the providers in it now, so the first query is fast."""
    request({"type": "warm", "providers": list(providers)}, refprop_path=refprop_path)


def running() -> bool:
    with _slot.lock:
        return _slot.current is not None and _slot.current.process.is_alive()


def shutdown() -> None:
    """Stop the worker (tests, and a REFPROP path change)."""
    with _slot.lock:
        if _slot.current is not None:
            _kill(_slot.current)
            _slot.current = None
