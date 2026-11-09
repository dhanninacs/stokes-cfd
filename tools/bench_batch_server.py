"""Where the cold batch request's wall clock actually goes, on the server side, per request.

`tools/bench_viewport.py` gives the aggregate (cold 2.92 s identity for 18 requests). That number
says the cold path is expensive; it does not say *which* work inside a request is. This tool opens
the per-request wall clock and the await boundaries inside the route, so the profile names a line
instead of a theory.

Two modes:

``--breakdown``   Times `/api/geometry/batch` itself, cold, 256 ids a request, one request at a
                  time, with the server's event loop and the anyio worker-thread pool instrumented.
                  Reports, per request: the route's own wall clock, how long the request waited
                  before the route body ran, and the server-side per-phase costs.
``--sleep N``     A control: each part's store read is replaced by a sleep, the way the task asks
                  (the cost between awaits). Not a fix; a measurement of what the reply costs when
                  the work is free.

The per-phase instrumentation is a monkeypatch applied inside the server process, behind
``--instrument``, so it never ships:

  * ``stokes.geometry.cache.read_cache``  -> file read + ``unpack`` + ``is_fresh`` (which stats the
    *source* file, not the cache file)
  * ``stokes.model.project.Project.find`` -> the per-id index lookup the store does per part
  * ``stokes.geometry.transport.pack_part`` -> only for parts whose mesh is not already packed

Usage:
    python tools/bench_batch_server.py --project <folder.stokes> [--ids 4500] [--chunk 256]
                                       [--instrument] [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import queue
import re
import statistics
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench_viewport import free_port, part_ids

ROOT = Path(__file__).resolve().parent.parent
START_TIMEOUT = 120.0
REQUEST_TIMEOUT = 300.0

INSTRUMENT = r'''
import os, sys, time, json, threading
sys.path.insert(0, os.environ["STOKES_ROOT"])

T = threading.local()

# The route is a sync `def`, so FastAPI runs its body on an anyio worker thread while the ASGI
# middleware runs on the event loop. Timing therefore lands in a per-thread bucket, and the
# middleware sums every worker thread that was active during the request's window.
BUCKETS = {}
_EPOCH = [0]


def _bucket():
    ident = threading.get_ident()
    entry = BUCKETS.get(ident)
    if entry is None or entry.get("epoch") != _EPOCH[0]:
        entry = {"epoch": _EPOCH[0], "thread": ident}
        BUCKETS[ident] = entry
    return entry

# --- 1. anyio worker-thread pool: how long a worker start takes, and how busy the pool is --------
try:
    import anyio.to_thread as _at
    _orig_run_sync = _at.run_sync
    import anyio

    async def _run_sync(func, *args, **kwargs):
        return await _orig_run_sync(func, *args, **kwargs)

    _at.run_sync = _run_sync
except Exception:
    pass

# --- 2. the store read path, per part ------------------------------------------------------------
from stokes.geometry import cache as _cache
from stokes.geometry.cache import read_cache as _read_cache

_reads = []

def read_cache(project_dir, node_id, source):
    b = _bucket()
    t0 = time.perf_counter()
    out = _read_cache(project_dir, node_id, source)
    b["cache_ms"] = b.get("cache_ms", 0.0) + (time.perf_counter() - t0) * 1000
    b["cache_n"] = b.get("cache_n", 0) + 1
    _reads.append((node_id, out is not None))
    return out

_cache.read_cache = read_cache
import stokes.geometry.store as _store
_store.read_cache = read_cache

# Split `read_cache` into its two costs: the file read and the decode. `read_cache` does
# `path.read_bytes()` then `unpack(data)` in one function, so both are wrapped.
from stokes.geometry import transport as _tr2
_unpack = _tr2.unpack


def unpack(data):
    b = _bucket()
    b["unpack_n"] = b.get("unpack_n", 0) + 1
    t0 = time.perf_counter()
    out = _unpack(data)
    b["unpack_ms"] = b.get("unpack_ms", 0.0) + (time.perf_counter() - t0) * 1000
    return out


_tr2.unpack = unpack
import stokes.geometry.cache as _cache_mod
_cache_mod.unpack = unpack

# `is_fresh` stats the *source* file (SourceStamp.of) when the cache header has no stamp. Count it:
# one stat per part is the kind of thing that never shows in an aggregate figure.
from stokes.geometry import files as _files
_stamp_of = _files.SourceStamp.of


def _stamp_of_counted(path):
    b = _bucket()
    t0 = time.perf_counter()
    try:
        return _stamp_of(path)
    finally:
        b["stat_n"] = b.get("stat_n", 0) + 1
        b["stat_ms"] = b.get("stat_ms", 0.0) + (time.perf_counter() - t0) * 1000


_files.SourceStamp.of = staticmethod(_stamp_of_counted)

from stokes.model.project import Project
_find = Project.find
_find_ms = [0.0]
_find_n = [0]

def find(self, ref):
    t0 = time.perf_counter()
    out = _find(self, ref)
    dt = (time.perf_counter() - t0) * 1000
    b = _bucket()
    b["find_ms"] = b.get("find_ms", 0.0) + dt
    b["find_n"] = b.get("find_n", 0) + 1
    return out

Project.find = find

from stokes.geometry import transport as _tr
_pack = _tr.pack_part

def pack_part(part):
    b = _bucket()
    b["pack_n"] = b.get("pack_n", 0) + 1
    t0 = time.perf_counter()
    out = _pack(part)
    b["pack_ms"] = b.get("pack_ms", 0.0) + (time.perf_counter() - t0) * 1000
    return out

_tr.pack_part = pack_part
_store.pack_part = pack_part
import stokes.geometry.transport
_counters = {"n": 0}
_counts = []

from stokes.geometry.store import GeometryStore
_transport = GeometryStore.transport

def transport(self, node_id):
    b = _bucket()
    t0 = time.perf_counter()
    out = _transport(self, node_id)
    b["store_ms"] = b.get("store_ms", 0.0) + (time.perf_counter() - t0) * 1000
    b["store_n"] = b.get("store_n", 0) + 1
    return out

GeometryStore.transport = transport

# --- 3. the ASGI entry: the wall clock of the request, and where it waited -----------------------
# Wrapping the route endpoint breaks FastAPI's dependency solving (it calls the endpoint with the
# values it resolved), so the wall clock is taken around the whole ASGI call instead, which is
# also the figure the page sees. The route body's own cost is measured from inside the store,
# through `store_ms`, and the difference between the two is the middleware + threadpool + framing.
import stokes.server.__main__ as _main
from stokes.server import app as _app

import os as _os
import tempfile as _tempfile

_dump_path = _os.path.join(_tempfile.gettempdir(), "t096_dump.json")
try:
    _os.remove(_dump_path)
except OSError:
    pass

_original_create_app = _app.create_app
_requests = []

def _wrap_asgi(app):
    outer = app.router

    async def middleware(scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/geometry"):
            return await outer(scope, receive, send)
        qs = scope.get("query_string", b"").decode("utf-8", "replace")
        n = 0
        if "ids=" in qs:
            part = qs.split("ids=", 1)[1].split("&", 1)[0]
            n = len([i for i in part.split(",") if i])
        body = [0]
        status = [0]

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status[0] = message["status"]
            elif message["type"] == "http.response.body":
                body[0] += len(message.get("body", b""))
            await send(message)

        loop_lag = _loop_lag()
        # Which worker threads were running this request: a sync route's body runs on one, and it
        # is the only place the store's cost is visible.
        _EPOCH[0] += 1
        epoch = _EPOCH[0]
        t0 = time.perf_counter()
        await outer(scope, receive, send_wrapper)
        dt = (time.perf_counter() - t0) * 1000
        bucket: dict = {}
        for key, entry in list(BUCKETS.items()):
            if entry.get("epoch") != epoch:
                BUCKETS.pop(key, None)
                continue
            for name, value in entry.items():
                if name.startswith("__"):
                    continue
                if isinstance(value, (int, float)):
                    bucket[name] = round(bucket.get(name, 0) + value, 3)
        _counts.append({
            "path": scope["path"], "ids": n, "status": status[0],
            "wall_ms": dt, "bytes": body[0], "loop_lag_ms": loop_lag, **bucket,
        })
        # Written after every request: a terminated process gets no atexit hook on Windows.
        try:
            with open(_dump_path, "w", encoding="utf-8") as fh:
                json.dump(_counts, fh)
        except OSError:
            pass

    app.router = middleware
    return app


def _loop_lag() -> float:
    """How far behind a 5 ms timer the event loop was while the last request ran."""
    return _lag[0]


_lag = [0.0]


def _start_lag_probe():
    import asyncio

    async def probe():
        while True:
            t = time.perf_counter()
            await asyncio.sleep(0.005)
            late = (time.perf_counter() - t - 0.005) * 1000
            _lag[0] = max(_lag[0] * 0.5, late)
    try:
        asyncio.get_event_loop_policy().get_event_loop()
    except Exception:
        pass
    return probe


def create_app(*args, **kwargs):
    app = _original_create_app(*args, **kwargs)
    return _wrap_asgi(app)

_app.create_app = create_app
'''

BOOTSTRAP = r"""
import os, sys, atexit, json
sys.path.insert(0, {root!r})
os.environ["STOKES_ROOT"] = {root!r}
g = globals()
exec(compile(open({instrument!r}, encoding="utf-8").read(), "t096_instr", "exec"), g)


def _dump():
    try:
        path = os.path.join(os.environ.get("TEMP", "/tmp"), "t096_dump.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(g.get("_counts", []), fh)
    except Exception as exc:
        sys.stderr.write("T096DUMPERR" + repr(exc) + "\n")


atexit.register(_dump)
import runpy

sys.argv = ["stokes.server", "--project", {project!r}, "--port", {port!r}]
runpy.run_module("stokes.server", run_name="__main__")
"""


class Server:
    """`python -m stokes.server` with the T-096 instrumentation applied in-process."""

    def __init__(self, project: Path, instrument: bool) -> None:
        import tempfile

        self.port = free_port()
        argv = [
            sys.executable,
            "-m",
            "stokes.server",
            "--project",
            str(project),
            "--port",
            str(self.port),
        ]
        if instrument:
            # The two helper files go in the temp folder: a bench must not litter the repository.
            work = Path(tempfile.gettempdir())
            bootstrap = work / "_stokes_t096_boot.py"
            instrument_file = work / "_stokes_t096_instr.py"
            bootstrap.write_text(
                BOOTSTRAP.format(
                    root=str(ROOT),
                    instrument=str(instrument_file),
                    project=str(project),
                    port=str(self.port),
                ),
                encoding="utf-8",
            )
            instrument_file.write_text(INSTRUMENT, encoding="utf-8")
            argv = [sys.executable, str(bootstrap)]
        import os
        import tempfile

        env = dict(os.environ)
        self.log_path = Path(tempfile.gettempdir()) / "t096_server.log"
        self.log = self.log_path.open("w", encoding="utf-8")
        self.proc = subprocess.Popen(
            argv, cwd=str(ROOT), stdout=self.log, stderr=subprocess.STDOUT, text=True, env=env
        )
        lines: queue.Queue[str] = queue.Queue()
        self.lines: list[str] = []

        def drain() -> None:
            # The server writes to a file, not a pipe: a pipe here is closed to this process tree
            # on Windows and the child would block on its first log line.
            with self.log_path.open(encoding="utf-8", errors="replace") as handle:
                while True:
                    line = handle.readline()
                    if not line:
                        if self.proc.poll() is not None:
                            return
                        time.sleep(0.02)
                        continue
                    self.lines.append(line)
                    lines.put(line)

        threading.Thread(target=drain, daemon=True).start()
        deadline = time.monotonic() + START_TIMEOUT
        self.token = ""
        while not self.token:
            left = deadline - time.monotonic()
            if left <= 0:
                self.stop()
                print("".join(self.lines[-40:]))
                raise SystemExit(f"the server printed no token within {START_TIMEOUT:.0f} s")
            try:
                line = lines.get(timeout=min(left, 1.0))
            except queue.Empty:
                if self.proc.poll() is not None:
                    print("".join(self.lines[-40:]))
                    raise SystemExit("the server exited before printing its url") from None
                continue
            found = re.search(r"token=([A-Za-z0-9_-]+)", line)
            if found:
                self.token = found.group(1)

    def dump(self) -> list[dict[str, object]]:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=10)
        import tempfile

        path = Path(tempfile.gettempdir()) / "t096_dump.json"
        if path.is_file():
            rows: list[dict[str, object]] = json.loads(path.read_text(encoding="utf-8"))
            return rows
        return []

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)


def one_request(server: Server, chunk: list[str]) -> tuple[float, int]:
    ids = ",".join(urllib.parse.quote(i, safe="") for i in chunk)
    url = f"http://127.0.0.1:{server.port}/api/geometry/batch?ids={ids}&token={server.token}"
    began = time.perf_counter()
    request = urllib.request.Request(url, headers={"Accept-Encoding": "identity"})
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:  # noqa: S310
        body = response.read()
    return (time.perf_counter() - began) * 1000, len(body)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bench-batch-server", description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--ids", type=int, default=0, help="parts to ask for (0: every part)")
    parser.add_argument("--chunk", type=int, default=256)
    parser.add_argument("--inflight", type=int, default=1, help="requests under way at once")
    parser.add_argument("--instrument", action="store_true")
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)

    ids = part_ids(args.project)
    if args.ids:
        ids = ids[: args.ids]
    chunks = [ids[i : i + args.chunk] for i in range(0, len(ids), args.chunk)]

    server = Server(args.project, instrument=args.instrument)
    wall: list[float] = []
    sizes: list[int] = []
    began = time.perf_counter()
    try:
        if args.inflight <= 1:
            for i, chunk in enumerate(chunks):
                ms, nbytes = one_request(server, chunk)
                wall.append(ms)
                sizes.append(nbytes)
                print(
                    f"  request {i + 1:2d}: {ms:8.1f} ms  {len(chunk):4d} ids  "
                    f"{nbytes / 1e6:6.2f} MB",
                    flush=True,
                )
        else:
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=args.inflight) as pool:
                for i, (ms, nbytes, n) in enumerate(
                    pool.map(lambda c: (*one_request(server, c), len(c)), chunks)
                ):
                    wall.append(ms)
                    sizes.append(nbytes)
                    print(
                        f"  request {i + 1:2d}: {ms:8.1f} ms  {n:4d} ids  {nbytes / 1e6:6.2f} MB",
                        flush=True,
                    )
        elapsed = (time.perf_counter() - began) * 1000
        rows = server.dump() if args.instrument else []
    finally:
        server.stop()

    total = sum(wall)
    print(
        f"\ncold, {args.inflight} in flight: {len(chunks)} requests, "
        f"summed {total:.1f} ms, WALL {elapsed:.1f} ms, "
        f"p50 {statistics.median(wall):.1f} ms, min {min(wall):.1f}, max {max(wall):.1f}"
    )
    print(f"payload {sum(sizes) / 1e6:.2f} MB")
    for i, row in enumerate(rows):
        parts = " ".join(
            f"{k}={row[k]:.1f}"
            for k in (
                "wall_ms",
                "store_ms",
                "cache_ms",
                "unpack_ms",
                "stat_ms",
                "find_ms",
                "pack_ms",
                "loop_lag_ms",
            )
            if isinstance(row.get(k), (int, float))
        )
        counts = " ".join(
            f"{k}={row[k]}"
            for k in ("status", "ids", "bytes", "cache_n", "unpack_n", "stat_n", "find_n", "pack_n")
            if row.get(k) is not None
        )
        print(f"  srv {i + 1:2d}: {parts} | {counts}")
    if args.json:
        args.json.write_text(
            json.dumps({"wall_ms": wall, "rows": rows}, indent=2), encoding="utf-8"
        )
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
