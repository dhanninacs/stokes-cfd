"""What the cold mesh transfer would cost if a request did not open one file per part.

The profile (tools/bench_batch_server.py + tools/bench_cache_read.py) puts 84% of a cold batch
request in `path.read_bytes()` on 256 files of about 5.7 KB, and shows that the same 25.71 MB read
through ONE handle takes 44.6 ms against 3,986 ms through 4,500 handles. This tool puts that
counterfactual through the real server, so the number is a server figure and not an extrapolation:

  baseline  the shipped `/api/geometry/batch` route, untouched
  single    the same route, but every part's cache bytes come from one already-open handle over a
            concatenated blob of the same files (a prototype of a single-file cache; it changes no
            stored format, it just shows what removing the per-part open is worth)

Run it on the same project and machine as `tools/bench_batch_server.py`; the instrumentation is
applied in the server process behind `--mode`, so nothing ships.

Usage:
    python tools/bench_single_cache.py --project <folder.stokes> [--runs 2]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench_batch_server import Server, one_request
from bench_viewport import part_ids

ROOT = Path(__file__).resolve().parent.parent

BOOTSTRAP = r"""
import os, sys, json
sys.path.insert(0, {root!r})
os.environ["STOKES_ROOT"] = {root!r}
g = globals()
exec(compile(open({instrument!r}, encoding="utf-8").read(), "single", "exec"), g)
import runpy

sys.argv = ["stokes.server", "--project", {project!r}, "--port", {port!r}]
runpy.run_module("stokes.server", run_name="__main__")
"""

SINGLE = r"""
import os, sys, time, json, threading
sys.path.insert(0, os.environ["STOKES_ROOT"])
from stokes.geometry.cache import cache_file, read_cache as _read_cache
from stokes.geometry.transport import unpack
from stokes.geometry.mesh import PartMesh

# One handle over every part's cache bytes, laid out in the order the project lists its parts.
BLOB = {}
_LOCK = threading.Lock()


def build(project, ids):
    path = os.path.join(os.environ["TEMP"], "t096_blob.bin")
    offsets = {}
    with open(path, "wb") as out:
        for pid in ids:
            data = cache_file(project, pid).read_bytes()
            offsets[pid] = (out.tell(), len(data))
            out.write(data)
    handle = open(path, "rb", buffering=0)
    BLOB["handle"] = handle
    BLOB["offsets"] = offsets
    BLOB["path"] = path


def read_cache(project_dir, node_id, source):
    off = BLOB["offsets"].get(node_id)
    if off is None:
        return _read_cache(project_dir, node_id, source)
    handle = BLOB["handle"]
    with _LOCK:
        handle.seek(off[0])
        data = handle.read(off[1])
    return unpack(data), data


import stokes.geometry.cache as _c
_c.read_cache = read_cache
import stokes.geometry.store as _s
_s.read_cache = read_cache

from stokes.server import app as _app
_orig = _app.create_app


def create_app(session, **kwargs):
    app = _orig(session, **kwargs)
    try:
        import json as _json
        from pathlib import Path as _P
        project = _P(session.project_path)
        data = _json.loads((project / "project.json").read_text(encoding="utf-8"))
        ids = []
        def walk(node):
            if isinstance(node, dict):
                if node.get("type") == "part" and node.get("id"):
                    ids.append(node["id"])
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)
        walk(data["root"])
        build(project, ids)
    except Exception as exc:
        sys.stderr.write("T096BLOBBUILD " + repr(exc) + "\n")
    return app


_app.create_app = create_app
"""


class SingleServer(Server):
    def __init__(self, project: Path) -> None:
        self.project = project
        self.port = 0
        self._start_single()

    def _start_single(self) -> None:
        import queue
        import re
        import subprocess
        import tempfile
        import threading

        from bench_viewport import free_port

        self.port = free_port()
        # The helper files go in the temp folder: a bench must not litter the repository.
        instrument_file = Path(tempfile.gettempdir()) / "_stokes_t096_single.py"
        bootstrap = Path(tempfile.gettempdir()) / "_stokes_t096_single_boot.py"
        bootstrap.write_text(
            BOOTSTRAP.format(
                root=str(ROOT),
                instrument=str(instrument_file),
                project=str(self.project),
                port=str(self.port),
            ),
            encoding="utf-8",
        )
        instrument_file.write_text(SINGLE, encoding="utf-8")
        log_path = Path(tempfile.gettempdir()) / "t096_single_server.log"
        argv = [sys.executable, str(bootstrap)]
        self.log = log_path.open("w", encoding="utf-8")
        self.proc = subprocess.Popen(
            argv, cwd=str(ROOT), stdout=self.log, stderr=subprocess.STDOUT, text=True
        )
        self.lines: list[str] = []
        self.log_path = log_path
        lines: queue.Queue[str] = queue.Queue()

        def drain() -> None:
            with log_path.open(encoding="utf-8", errors="replace") as handle:
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
        deadline = time.monotonic() + 120.0
        self.token = ""
        while not self.token:
            left = deadline - time.monotonic()
            if left <= 0:
                self.stop()
                print("".join(self.lines[-40:]))
                raise SystemExit("no token")
            try:
                line = lines.get(timeout=min(left, 1.0))
            except queue.Empty:
                if self.proc.poll() is not None:
                    print("".join(self.lines[-40:]))
                    raise SystemExit("server exited") from None
                continue
            found = re.search(r"token=([A-Za-z0-9_-]+)", line)
            if found:
                self.token = found.group(1)

    def dump(self) -> list[dict[str, object]]:
        return []


def transfer(server: Server, ids: list[str], chunk: int, inflight: int) -> tuple[float, float]:
    from concurrent.futures import ThreadPoolExecutor

    chunks = [ids[i : i + chunk] for i in range(0, len(ids), chunk)]
    began = time.perf_counter()
    with ThreadPoolExecutor(max_workers=inflight) as pool:
        list(pool.map(lambda c: one_request(server, c), chunks))
    return (time.perf_counter() - began) * 1000, 0.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bench-single-cache", description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument("--chunk", type=int, default=256)
    parser.add_argument("--inflight", type=int, default=4)
    args = parser.parse_args(argv)

    ids = part_ids(args.project)
    rows = []
    for run in range(args.runs):
        for mode in ("baseline", "single"):
            server = (
                Server(args.project, instrument=False)
                if mode == "baseline"
                else SingleServer(args.project)
            )
            try:
                wall, _ = transfer(server, ids, args.chunk, args.inflight)
            finally:
                server.stop()
            print(
                f"  run {run + 1} {mode:<9} {wall:8.1f} ms  "
                f"({len(ids)} parts, {args.inflight} in flight)"
            )
            rows.append({"run": run + 1, "mode": mode, "wall_ms": round(wall, 1)})
    for mode in ("baseline", "single"):
        values = [r["wall_ms"] for r in rows if r["mode"] == mode]
        print(f"  {mode:<9} p50 {sorted(values)[len(values) // 2]:.1f} ms  over {len(values)} runs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
