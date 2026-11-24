"""Running the ``stokes-implicit-mesh`` program (ADR-0035 decisions 13, 16, 17 and 19).

The mesher is a separate program: a crash or a runaway evaluation cannot take the server down, and
a superseded preview is killed at once. It is started from an argv list, never through a shell,
with an explicit working folder, a minimal environment and a timeout; on Windows it is put in a
kill-on-close Job Object so it dies with the process that started it.

Results are cached by content: the key is the hash of the binary, the text-format version, the
model text and the cube, depth and simplification. The cache is a folder of ``<key>.mesh`` files
and their ``<key>.json`` statistics, pruned least recently used to :data:`CACHE_CAP` bytes.

Finding the program: ``STOKES_IMPLICIT_MESHER`` (development), then ``bin/`` beside this package
(an installed build), then ``native/implicit-mesh/bin/`` of a source checkout. A path ending in
``.py`` is run with the current interpreter: that is the reference mesher the tests use, never a
shipped configuration.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from stokes.implicit.errors import (
    ImplicitTooFine,
    MesherCancelled,
    MesherFailed,
    MesherMissing,
)
from stokes.implicit.graph import TEXT_VERSION
from stokes.implicit.meshfile import KernelMesh, read
from stokes.log import fields, get_logger

log = get_logger(__name__)

PROGRAM = "stokes-implicit-mesh"
ENV_OVERRIDE = "STOKES_IMPLICIT_MESHER"
TIMEOUT_S = 30 * 60
"""A run longer than this is killed (decision 16)."""
CACHE_CAP = 4 * 1024**3
"""Bytes of meshes kept in one cache folder (decision 17)."""
MAX_DEPTH = 15
"""The deepest octree (decision 6): at least 256 f32 positions across a cell."""
MIN_DEPTH = 4
MAX_LINE = 4096
"""Characters of one line of the mesher's stdout that are read (decision 17)."""
SHIFT = (math.sqrt(2.0) - 1.0) / 7.0
"""The cube is shifted by this fraction of a cell on every axis, so no cell corner lies on a round
coordinate where two touching bodies have a zero of their union inside the solid (decision 19)."""
MARGIN_CELLS = 2.0
"""Cells of empty space kept around the body inside the meshing cube."""

Progress = Callable[[float, str], None]
Vec3 = tuple[float, float, float]


# ------------------------------------------------------------------------------- the program
def program_name() -> str:
    return PROGRAM + (".exe" if os.name == "nt" else "")


def find_mesher() -> Path:
    """The mesher to run; :class:`MesherMissing` naming the build step when there is none."""
    override = os.environ.get(ENV_OVERRIDE, "").strip()
    if override:
        path = Path(override)
        if path.is_file():
            return path.resolve()
        raise MesherMissing(
            f"{ENV_OVERRIDE} names {override}, which is not a file", details={"path": override}
        )
    here = Path(__file__).resolve().parent
    candidates = [
        here / "bin" / program_name(),
        here.parent.parent / "native" / "implicit-mesh" / "bin" / program_name(),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise MesherMissing(
        "the implicit mesher is not built: build native/implicit-mesh (see its README) or set "
        f"{ENV_OVERRIDE}",
        details={"looked_in": [str(c) for c in candidates]},
    )


_HASHES: dict[tuple[str, int, int], str] = {}
_HASH_LOCK = threading.Lock()


def program_hash(path: Path) -> str:
    """The SHA-256 of the program, remembered per (path, size, mtime)."""
    stat = path.stat()
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    with _HASH_LOCK:
        found = _HASHES.get(key)
    if found is None:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
        found = digest.hexdigest()
        with _HASH_LOCK:
            _HASHES[key] = found
    return found


def _argv0(path: Path) -> list[str]:
    if path.suffix == ".py":
        return [sys.executable, str(path)]
    return [str(path)]


# ------------------------------------------------------------------------------- the cube
@dataclass(frozen=True)
class Cube:
    """The meshing cube in kernel units: its centre (in the kernel frame, whose origin is the
    centre of the part's box), half its side and the octree depth."""

    centre: Vec3
    half: float
    depth: int

    @property
    def cell(self) -> float:
        return 2.0 * self.half / (1 << self.depth)


def cube_for(box: tuple[Vec3, Vec3], cell: float) -> Cube:
    """The cube for a body whose box (in the kernel frame) is ``box``, at a cell of at most
    ``cell``; :class:`ImplicitTooFine` when that needs a deeper octree than :data:`MAX_DEPTH`."""
    low, high = box
    extent = max(h - lo for lo, h in zip(low, high, strict=True))
    if not (math.isfinite(extent) and extent > 0.0 and math.isfinite(cell) and cell > 0.0):
        raise ImplicitTooFine(
            "the part's box or the cell size is not usable", details={"cell": cell}
        )
    centre_box = tuple((lo + h) / 2 for lo, h in zip(low, high, strict=True))
    half = extent / 2 + MARGIN_CELLS * cell
    depth = max(MIN_DEPTH, math.ceil(math.log2(2 * half / cell)))
    if depth > MAX_DEPTH:
        fits = 2 * (extent / 2) / ((1 << MAX_DEPTH) - 2 * MARGIN_CELLS)
        raise ImplicitTooFine(
            f"a cell of {cell:.4g} mm needs an octree deeper than {MAX_DEPTH}; the finest that "
            f"fits this part is {fits:.4g} mm",
            details={"cell": cell, "finest": fits},
        )
    half = (1 << depth) * cell / 2
    shift = SHIFT * cell
    centre = (centre_box[0] + shift, centre_box[1] + shift, centre_box[2] + shift)
    return Cube(centre, half, depth)


# ------------------------------------------------------------------------------- a run
@dataclass(frozen=True)
class MeshRequest:
    """One body to mesh: its model text and cube, and the options of the program."""

    text: str
    cube: Cube
    simplify: float = 0.0
    """Largest distance from the unsimplified surface (kernel units); 0 keeps every triangle."""
    threads: int = 0
    """Threads; 0 is every core but one."""


@dataclass
class MeshOutcome:
    mesh: KernelMesh
    stats: dict[str, Any] = field(default_factory=dict)  # Any: JSON
    cached: bool = False
    seconds: float = 0.0


def cache_key(request: MeshRequest, binary_hash: str) -> str:
    cube = request.cube
    parts = [
        binary_hash,
        TEXT_VERSION,
        repr(cube.centre),
        repr(cube.half),
        str(cube.depth),
        repr(request.simplify),
        request.text,
    ]
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


def is_cached(request: MeshRequest, cache: Path) -> bool:
    """Whether ``cache`` holds the mesh of ``request`` (a run of it would read it, not mesh)."""
    try:
        key = cache_key(request, program_hash(find_mesher()))
    except (MesherMissing, OSError):
        return False
    return (cache / f"{key}.mesh").is_file() and (cache / f"{key}.json").is_file()


def mesh(
    request: MeshRequest,
    cache: Path,
    *,
    cancel: threading.Event | None = None,
    progress: Progress | None = None,
    timeout: float = TIMEOUT_S,
) -> MeshOutcome:
    """Mesh ``request``, from the cache in ``cache`` when it holds this exact request."""
    start = time.monotonic()
    program = find_mesher()
    key = cache_key(request, program_hash(program))
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / f"{key}.mesh"
    stats_path = cache / f"{key}.json"
    if target.is_file() and stats_path.is_file():
        try:
            found = read(target)
            stats = json.loads(stats_path.read_text(encoding="utf-8"))
            os.utime(target)
            return MeshOutcome(
                found, stats if isinstance(stats, dict) else {}, True, time.monotonic() - start
            )
        except (MesherFailed, OSError, ValueError) as exc:
            log.warning("implicit mesh cache entry dropped", extra=fields(key=key, error=str(exc)))
            target.unlink(missing_ok=True)
            stats_path.unlink(missing_ok=True)
    # A folder of its own per run, with a SHORT name. Two workers can mesh the same request at once,
    # and a shared `<key>.run` was emptied and removed under the other run. And Windows refuses a
    # working directory longer than MAX_PATH even where long paths are on: the 64-character key
    # pushed a project a few folders deep over it, and the mesher failed to start (WinError 267).
    work = Path(tempfile.mkdtemp(prefix="run-", dir=cache))
    try:
        model = work / "model.txt"
        model.write_text(request.text, encoding="utf-8", newline="\n")
        out = work / "mesh.bin"
        stats = _run(program, request, model, out, work, cancel, progress, timeout)
        found = read(out)
        written = work / "stats.json"
        written.write_text(json.dumps(stats, separators=(",", ":")), encoding="utf-8")
        _publish(written, stats_path)
        _publish(out, target)
    finally:
        for leftover in work.iterdir() if work.is_dir() else []:
            leftover.unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            work.rmdir()
    prune(cache, CACHE_CAP)
    return MeshOutcome(found, stats, False, time.monotonic() - start)


def _publish(source: Path, target: Path) -> None:
    """Move a finished file into the cache, atomically.

    Windows refuses to replace a file another run has open; that run meshed the same request, so
    the file already there is this one's equal and is kept.
    """
    try:
        source.replace(target)
    except PermissionError:
        if not target.is_file():
            raise


def _run(
    program: Path,
    request: MeshRequest,
    model: Path,
    out: Path,
    work: Path,
    cancel: threading.Event | None,
    progress: Progress | None,
    timeout: float,
) -> dict[str, Any]:  # Any: JSON statistics
    cube = request.cube
    threads = request.threads if request.threads > 0 else max(1, (os.cpu_count() or 2) - 1)
    argv = [
        *_argv0(program),
        "--model",
        str(model),
        "--centre",
        *(repr(float(c)) for c in cube.centre),
        "--half",
        repr(float(cube.half)),
        "--depth",
        str(cube.depth),
        "--out",
        str(out),
        "--threads",
        str(threads),
        "--progress",
    ]
    if request.simplify > 0.0:
        argv += ["--simplify", repr(float(request.simplify))]
    process = _start(argv, work)
    lines: list[dict[str, Any]] = []  # Any: JSON lines
    reader = threading.Thread(target=_read_lines, args=(process, lines, progress), daemon=True)
    reader.start()
    deadline = time.monotonic() + timeout
    try:
        while process.poll() is None:
            if cancel is not None and cancel.is_set():
                _kill(process)
                raise MesherCancelled("the meshing was cancelled")
            if time.monotonic() > deadline:
                _kill(process)
                raise MesherFailed(
                    f"the mesher ran longer than {timeout:.0f} s and was stopped",
                    details={"timeout": timeout},
                )
            time.sleep(0.05)
    except BaseException:
        _kill(process)
        raise
    reader.join(timeout=5)
    error = next((line for line in lines if "error" in line), None)
    if process.returncode != 0 or error is not None:
        message = str((error or {}).get("message") or f"exit code {process.returncode}")[:512]
        raise MesherFailed(
            f"the mesher failed: {message}",
            details={"exit": process.returncode, "error": (error or {}).get("error")},
        )
    stats = next(
        (line["stats"] for line in reversed(lines) if isinstance(line.get("stats"), dict)), None
    )
    if stats is None:
        raise MesherFailed("the mesher ended without its statistics line")
    return dict(stats)


def _start(argv: list[str], work: Path) -> subprocess.Popen[bytes]:
    env = {k: os.environ[k] for k in ("SYSTEMROOT", "TEMP", "TMP", "HOME") if k in os.environ}
    flags = 0
    if os.name == "nt":
        flags = 0x08000000 | 0x00004000  # CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS
    process = subprocess.Popen(  # noqa: S603 - argv list built above, no shell
        argv,
        cwd=work,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
        start_new_session=os.name != "nt",
    )
    if os.name == "nt":
        _confine(process.pid)
    return process


def _read_lines(
    process: subprocess.Popen[bytes], lines: list[dict[str, Any]], progress: Progress | None
) -> None:
    stream = process.stdout
    if stream is None:
        return
    for raw in stream:
        text = raw[:MAX_LINE].decode("utf-8", errors="replace").strip()
        if not text:
            continue
        try:
            line = json.loads(text)
        except ValueError:
            continue
        if not isinstance(line, dict):
            continue
        lines.append(line)
        if progress is not None and "done" in line and "total" in line:
            with contextlib.suppress(TypeError, ValueError, ZeroDivisionError):
                fraction = float(line["done"]) / float(line["total"])
                progress(min(1.0, max(0.0, fraction)), str(line.get("stage", ""))[:64])


def _kill(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        with contextlib.suppress(OSError):
            process.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=5)


_JOB: dict[str, int] = {}
_JOB_LOCK = threading.Lock()


def _confine(pid: int) -> None:
    """Put ``pid`` in this process's kill-on-close Job Object (Windows), so the mesher dies with
    the worker that started it however the worker ends. The job handle is never closed: Windows
    closes it, and kills the job, when this process exits. A failure is logged, not raised: the
    mesher still runs and is killed on cancel and timeout."""
    import ctypes
    from ctypes import wintypes

    class _Basic(ctypes.Structure):
        _fields_ = [
            ("process_time", ctypes.c_int64),
            ("job_time", ctypes.c_int64),
            ("flags", wintypes.DWORD),
            ("min_working", ctypes.c_size_t),
            ("max_working", ctypes.c_size_t),
            ("active_limit", wintypes.DWORD),
            ("affinity", ctypes.c_size_t),
            ("priority", wintypes.DWORD),
            ("scheduling", wintypes.DWORD),
        ]

    class _Extended(ctypes.Structure):
        _fields_ = [
            ("basic", _Basic),
            ("io", ctypes.c_uint64 * 6),
            ("process_memory", ctypes.c_size_t),
            ("job_memory", ctypes.c_size_t),
            ("peak_process_memory", ctypes.c_size_t),
            ("peak_job_memory", ctypes.c_size_t),
        ]

    def _last_error() -> int:
        return int(getattr(ctypes, "get_last_error")())  # noqa: B009 - Windows only

    try:
        kernel = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)  # noqa: B009 - Windows only
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.OpenProcess.restype = wintypes.HANDLE
        with _JOB_LOCK:
            if "job" not in _JOB:
                job = kernel.CreateJobObjectW(None, None)
                if not job:
                    raise OSError(_last_error(), "CreateJobObjectW")
                limits = _Extended()
                limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                ok = kernel.SetInformationJobObject(
                    wintypes.HANDLE(job), 9, ctypes.byref(limits), ctypes.sizeof(limits)
                )
                if not ok:
                    raise OSError(_last_error(), "SetInformationJobObject")
                _JOB["job"] = int(job)
            job_handle = _JOB["job"]
        handle = kernel.OpenProcess(0x0001 | 0x0100, False, pid)  # TERMINATE | SET_QUOTA
        if not handle:
            raise OSError(_last_error(), "OpenProcess")
        try:
            if not kernel.AssignProcessToJobObject(
                wintypes.HANDLE(job_handle), wintypes.HANDLE(handle)
            ):
                raise OSError(_last_error(), "AssignProcessToJobObject")
        finally:
            kernel.CloseHandle(wintypes.HANDLE(handle))
    except OSError as exc:
        log.warning("mesher not confined to a job object", extra=fields(pid=pid, error=str(exc)))


# ------------------------------------------------------------------------------- the cache
def prune(cache: Path, cap: int) -> int:
    """Delete the least recently used meshes until the folder holds at most ``cap`` bytes.
    Returns how many meshes went."""
    entries: list[tuple[float, int, Path]] = []
    total = 0
    for path in cache.glob("*.mesh"):
        try:
            stat = path.stat()
        except OSError:
            continue
        entries.append((stat.st_mtime, stat.st_size, path))
        total += stat.st_size
    removed = 0
    for _mtime, size, path in sorted(entries):
        if total <= cap:
            break
        with contextlib.suppress(OSError):
            path.unlink()
            path.with_suffix(".json").unlink(missing_ok=True)
            total -= size
            removed += 1
    return removed
