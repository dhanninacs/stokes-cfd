"""Helper processes that tessellate the batches of a large import in parallel.

## Why

Reading a STEP file takes one process one pass, but tessellating it does not: the solids are
independent. Measured on the 4,500-solid pin grid (52 MB, 24 logical CPUs, nothing else running),
2026-11-09: the read takes 26 s, the coarse pass 21 s and the fine pass 27 s, 75 s in all, in one
process. OCCT's own ``isInParallel`` flag buys only 1.27x on these many small solids (12.5 s to
9.8 s for one pass), so the cores sit idle. Separate processes do not share its locks.

## How

The worker (:mod:`stokes.geometry.worker_main`) starts :class:`HelperPool` before it reads the file,
so each helper's seconds of OCP loading overlap the read. Each batch of solids goes to a helper as
a binary B-rep file in the job's work folder (writing a batch takes 0.35 s for all 4,500 solids,
reading it back 0.8 s). The helper tessellates it with the same code the worker runs itself
(:func:`stokes.geometry.worker_main.mesh_batch`) and answers with the same frames a solid is sent to
the parent in, so the worker relays them without decoding. A helper that fails, or dies, hands its
batches back: the worker meshes them itself, so an import never depends on a helper.

## Process model

Helpers are ``subprocess`` children of the worker, not ``multiprocessing`` ones: the worker is a
daemon process, and a daemon may not start ``multiprocessing`` children. A helper reads its
requests from standard input and exits when that closes, which is what happens when the worker is
cancelled or crashes, so no helper outlives its import. Standard output carries only frames; OCCT's
chatter is redirected to the null device before OCP loads.

## Frames

An 8-byte little-endian length, then that many bytes. A request is one JSON frame
(``{"op": "mesh", ...}`` or ``{"op": "quit"}``). A reply is one JSON header (``ready``,
``batch`` with the number of solids that follow, or ``batch_failed`` with a message), then for
``batch`` seven frames per solid, in the order ``worker_main.send_solid`` writes them.
"""

from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

from stokes import workspace_root
from stokes.log import fields, get_logger

log = get_logger(__name__)

_LENGTH = struct.Struct("<Q")
MAX_FRAME = 4 * 1024**3
"""A frame is one array of one solid's mesh; nothing legitimate is larger than this."""
MAX_REQUEST = 16 * 1024 * 1024
FRAMES_PER_SOLID = 7
"""The JSON header, then positions, normals, indices, face ids, adjacency pairs and cosines."""
MIN_FILE_BYTES = 8 * 1024 * 1024
"""A STEP file smaller than this is read and meshed in seconds; helpers are not started for it."""
MIN_FACES = 3000
"""Below this many faces the read-and-mesh is quick and a helper's startup is not repaid."""
MAX_HELPERS = 8
BATCH_FACES = 600
"""Faces per batch when helpers are used: small enough to keep every helper busy."""
ENV_COUNT = "STOKES_GEOMETRY_HELPERS"
"""Overrides the helper count; ``0`` turns helpers off."""


def helper_count(file_bytes: int, cpus: int | None = None, env: str | None = None) -> int:
    """How many helpers an import of a file of ``file_bytes`` should start.

    ``STOKES_GEOMETRY_HELPERS`` wins when set to a whole number (clamped to 0..8).
    Otherwise a file under :data:`MIN_FILE_BYTES` gets none, and a larger one gets the CPUs minus
    two (one for the worker, one for the rest of the machine), at most :data:`MAX_HELPERS`; a
    machine with fewer than four CPUs gets none, since two helpers would only share the worker's
    cores.
    """
    raw = os.environ.get(ENV_COUNT) if env is None else env
    if raw is not None and raw.strip() != "":
        try:
            return max(0, min(MAX_HELPERS, int(raw.strip())))
        except ValueError:
            log.warning("ignoring a bad helper count", extra=fields(value=raw[:20]))
    count = cpus if cpus is not None else (os.cpu_count() or 1)
    if file_bytes < MIN_FILE_BYTES or count < 4:
        return 0
    return min(MAX_HELPERS, count - 2)


# ---------------------------------------------------------------------------- frames
def write_frame(stream: IO[bytes], data: bytes | memoryview) -> None:
    """Write one frame."""
    stream.write(_LENGTH.pack(len(data)))
    stream.write(data)


def read_frame(stream: IO[bytes], limit: int = MAX_FRAME) -> bytes | None:
    """One frame, or None at a clean end of stream; a frame cut short or over ``limit`` raises."""
    head = _read_exact(stream, _LENGTH.size)
    if head is None:
        return None
    (size,) = _LENGTH.unpack(head)
    if size > limit:
        raise ValueError(f"a frame of {size} bytes is over the limit")
    body = _read_exact(stream, size) if size else b""
    if body is None:
        raise EOFError("the stream ended inside a frame")
    return body


def _read_exact(stream: IO[bytes], size: int) -> bytes | None:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            if remaining == size:
                return None
            raise EOFError("the stream ended inside a frame")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


# ---------------------------------------------------------------------------- pool
@dataclass
class _Outcome:
    ok: bool
    frames: list[bytes] = field(default_factory=list)
    message: str = ""


class _Helper:
    """One helper process and the thread that drains its replies."""

    def __init__(self, index: int, process: subprocess.Popen[bytes]) -> None:
        self.index = index
        self.process = process
        self.pending: set[int] = set()
        self.alive = True
        self.thread = threading.Thread(
            target=self._drain, name=f"stokes-geometry-helper-{index}", daemon=True
        )

    def _drain(self) -> None:
        # The pool's lock is taken by `_deliver`, which is also where a dead helper's batches are
        # handed back; this thread does nothing but read frames.
        assert self.process.stdout is not None
        stream = self.process.stdout
        try:
            while True:
                head = read_frame(stream, MAX_REQUEST)
                if head is None:
                    break
                message = json.loads(head)
                kind = message.get("type")
                if kind == "ready":
                    continue
                if kind == "batch":
                    count = int(message["solids"]) * FRAMES_PER_SOLID
                    frames = [read_frame(stream) or b"" for _ in range(count)]
                    self.pool_deliver(int(message["id"]), _Outcome(True, frames))
                elif kind == "batch_failed":
                    outcome = _Outcome(False, message=str(message.get("message", "")))
                    self.pool_deliver(int(message["id"]), outcome)
        except (EOFError, OSError, ValueError) as exc:
            log.debug("a geometry helper's stream ended", extra=fields(error=str(exc)[:100]))
        finally:
            self.pool_fail_all(self, "the helper process ended")

    # Wired by the pool.
    pool_deliver: Callable[[int, _Outcome], None]
    pool_fail_all: Callable[[_Helper, str], None]


class HelperPool:
    """The helpers of one import. Use :meth:`start`, :meth:`submit`, :meth:`take`, :meth:`close`."""

    def __init__(self) -> None:
        self._helpers: list[_Helper] = []
        self._lock = threading.Condition()
        self._results: dict[int, _Outcome] = {}
        self._owner: dict[int, _Helper] = {}
        self._next = 0

    @classmethod
    def start(cls, count: int) -> HelperPool:
        """Start ``count`` helpers; those that cannot start are left out (the pool may be empty)."""
        pool = cls()
        for index in range(count):
            try:
                process = subprocess.Popen(
                    [sys.executable, "-m", "stokes.geometry.helpers"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    env={**os.environ, "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)},
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    close_fds=True,
                )
            except OSError as exc:
                log.warning("a geometry helper did not start", extra=fields(error=str(exc)[:100]))
                continue
            helper = _Helper(index, process)
            helper.pool_deliver = pool._deliver
            helper.pool_fail_all = pool._fail_all
            helper.thread.start()
            pool._helpers.append(helper)
        return pool

    @property
    def size(self) -> int:
        return len(self._helpers)

    def submit(self, request: dict[str, Any]) -> int:
        """Queue a ``mesh`` request on the next helper (round robin); returns its batch id."""
        with self._lock:
            live = [h for h in self._helpers if h.alive]
            batch = self._next
            self._next += 1
            if not live:
                self._results[batch] = _Outcome(False, message="no helper is running")
                return batch
            helper = live[batch % len(live)]
            self._owner[batch] = helper
            helper.pending.add(batch)
        body = json.dumps({**request, "op": "mesh", "id": batch}).encode("utf-8")
        try:
            assert helper.process.stdin is not None
            write_frame(helper.process.stdin, body)
            helper.process.stdin.flush()
        except (OSError, ValueError):
            self._fail_all(helper, "the helper's input closed")
        return batch

    def take(self, batch: int, timeout: float | None = None) -> _Outcome:
        """Wait for a batch's outcome. A helper that died gives a failed one, never a hang."""
        with self._lock:
            if not self._lock.wait_for(lambda: batch in self._results, timeout):
                return _Outcome(False, message="the helper did not answer in time")
            return self._results.pop(batch)

    def close(self) -> None:
        """Ask every helper to stop, then end any that has not by the time it is waited for."""
        for helper in self._helpers:
            try:
                if helper.process.stdin is not None:
                    write_frame(helper.process.stdin, json.dumps({"op": "quit"}).encode("utf-8"))
                    helper.process.stdin.close()
            except (OSError, ValueError):
                pass
        for helper in self._helpers:
            try:
                helper.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                helper.process.kill()
            for stream in (helper.process.stdout,):
                if stream is not None:
                    stream.close()

    # -- called from the drain threads
    def _deliver(self, batch: int, outcome: _Outcome) -> None:
        with self._lock:
            owner = self._owner.pop(batch, None)
            if owner is not None:
                owner.pending.discard(batch)
            self._results[batch] = outcome
            self._lock.notify_all()

    def _fail_all(self, helper: _Helper, message: str) -> None:
        with self._lock:
            helper.alive = False
            for batch in list(helper.pending):
                self._results.setdefault(batch, _Outcome(False, message=message))
                self._owner.pop(batch, None)
            helper.pending.clear()
            self._lock.notify_all()


# ---------------------------------------------------------------------------- helper side
def main() -> int:
    """The helper process: read requests from standard input, answer on standard output."""
    stdin = sys.stdin.buffer
    # The protocol goes on a private copy of standard output; fd 1 itself then points at the null
    # device, because OCCT prints transfer and mesh statistics there.
    proto = os.fdopen(os.dup(1), "wb", buffering=1 << 20)
    if sys.platform == "win32":
        import msvcrt

        msvcrt.setmode(proto.fileno(), os.O_BINARY)
        msvcrt.setmode(stdin.fileno(), os.O_BINARY)
    from stokes.geometry.worker_main import _silence_stdout, mesh_batch

    _silence_stdout()
    try:
        import OCP  # noqa: F401  (loads the libraries now, while the worker is still reading)
    except ImportError as exc:
        write_frame(proto, json.dumps({"type": "error", "message": str(exc)}).encode("utf-8"))
        proto.flush()
        return 1
    write_frame(proto, json.dumps({"type": "ready"}).encode("utf-8"))
    proto.flush()
    # Its own scratch folder for the STL a batch is read back from.
    scratch = workspace_root.scratch("stokes-helper-")  # under the workspace root, never /tmp
    try:
        return _serve(stdin, proto, scratch, mesh_batch)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _serve(
    stdin: IO[bytes], proto: IO[bytes], scratch: Path, mesh_batch: Callable[..., None]
) -> int:
    """Answer requests until standard input closes or a quit arrives."""
    while True:
        try:
            frame = read_frame(stdin, MAX_REQUEST)
        except (EOFError, OSError, ValueError):
            return 0
        if frame is None:
            return 0
        try:
            request = json.loads(frame)
        except ValueError:
            continue
        if request.get("op") == "quit":
            return 0
        if request.get("op") != "mesh":
            continue
        batch = int(request.get("id", -1))
        try:
            frames = _collect(request, mesh_batch, scratch)
        except Exception as exc:
            text = f"{type(exc).__name__}: {exc}"[:500]
            message = {"type": "batch_failed", "id": batch, "message": text}
            write_frame(proto, json.dumps(message).encode("utf-8"))
        else:
            header = {"type": "batch", "id": batch, "solids": len(frames) // FRAMES_PER_SOLID}
            write_frame(proto, json.dumps(header).encode("utf-8"))
            for part in frames:
                write_frame(proto, part)
        try:
            proto.flush()
        except OSError:
            return 0


class _Collector:
    """A Connection stand-in that keeps the frames a batch would send, so a reply is all or nothing:
    a batch that fails half way must not leave half its solids in the worker's relay."""

    def __init__(self) -> None:
        self.frames: list[bytes] = []

    def send_bytes(self, data: bytes | memoryview) -> None:
        self.frames.append(bytes(data))


def _collect(
    request: dict[str, Any], mesh_batch: Callable[..., None], workdir: Path
) -> list[bytes]:
    from OCP.BinTools import BinTools
    from OCP.TopoDS import TopoDS_Iterator, TopoDS_Shape

    path = Path(str(request["path"]))
    compound = TopoDS_Shape()
    BinTools.Read_s(compound, str(path))
    if compound.IsNull():  # Read_s reports a missing or damaged file by leaving the shape empty
        raise ValueError(f"the batch file {path.name} could not be read")
    shapes = []
    iterator = TopoDS_Iterator(compound)
    while iterator.More():
        shapes.append(iterator.Value())
        iterator.Next()
    # The solids' descriptions are in a file beside the batch (a pipe holds only 4 KB on Windows).
    bodies = json.loads(Path(str(request["bodies"])).read_text(encoding="utf-8"))
    if len(shapes) != len(bodies):
        raise ValueError(
            f"the batch file holds {len(shapes)} shapes, the request names {len(bodies)}"
        )
    collector = _Collector()
    mesh_batch(
        collector,
        shapes,
        bodies,
        quality=str(request["quality"]),
        unit=float(request["unit"]),
        workdir=workdir,
        count=int(request["count"]),
        final=bool(request["final"]),
        parallel=False,
    )
    return collector.frames


if __name__ == "__main__":
    raise SystemExit(main())
