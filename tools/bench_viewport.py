"""Benchmark a project's geometry import and mesh transfer.

Headless and dependency-free, so it runs anywhere and can gate a regression. The numbers are the
ones that decide whether a huge CAD file feels fast:

- **import**    cold: STEP -> cached meshes, wall clock of the whole job (``--step``)
- **reopen**    opening the finished project again, meshes already cached (``--project``)
- **transfer**  how the meshes reach the viewport, asked for the way it asks
                (web/src/viewport/parts.ts): ``GET /api/geometry/batch`` with at most 256 ids per
                request and two requests in flight. Every row gets a fresh server, so its first pass
                reads every mesh from the project's cache (**cold**, as when a project opens); the
                second pass repeats the requests on the same server (**warm**, the meshes in memory,
                as for a reconnecting client). Every request offers gzip, as a browser does, and
                each row is measured against two servers: the default, which does not compress for
                a loopback client, the desktop host's case (**identity**), and one started with
                ``--compress-loopback``, which compresses as it does for a remote browser
                (**gzip**). A reply whose encoding is not the row's stops the run, so a change that
                breaks the loopback exemption cannot go unseen. Request percentiles are per request
                within one row, never across rows of different sizes.

The client-side half (frame cost, pick latency, time to first part) needs a browser: development and
review builds, or any build opened with ``?debug=1``, expose ``window.__stokesScene`` (``perf()``,
``measureRender()``, ``measurePick()``, ``partBounds()``); see docs/HANDOVER.md.

Usage:
    python tools/bench_viewport.py --step <file.step> [--keep] [--json out.json]
    python tools/bench_viewport.py --project <folder.stokes> [--rows 1,50,256,all] [--json out.json]
"""

from __future__ import annotations

import argparse
import gzip
import json
import queue
import re
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHUNK = 256
"""Ids per request: ``BATCH_SIZE`` in web/src/viewport/parts.ts (the server takes at most 512)."""
IN_FLIGHT = 2
"""Requests under way at once: ``MAX_BATCHES`` in web/src/viewport/parts.ts."""
CLI_TIMEOUT = 3600.0
"""Seconds a CLI step may take; a cold import of a large file takes minutes."""
START_TIMEOUT = 120.0
REQUEST_TIMEOUT = 300.0


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def run_cli(*args: str, project: Path | None = None) -> tuple[float, str]:
    """Run one stokes CLI command; return (seconds, stdout+stderr)."""
    argv = [
        sys.executable,
        "-c",
        "import sys;sys.argv=sys.argv[1:];from stokes.cli.main import main;main()",
        "stokes",
        *args,
    ]
    if project is not None:
        argv += ["--project", str(project)]
    began = time.perf_counter()
    try:
        done = subprocess.run(
            argv, cwd=str(ROOT), capture_output=True, text=True, check=False, timeout=CLI_TIMEOUT
        )
    except subprocess.TimeoutExpired as exc:
        raise SystemExit(f"stokes {' '.join(args)} took longer than {CLI_TIMEOUT:.0f} s") from exc
    return time.perf_counter() - began, (done.stdout or "") + (done.stderr or "")


def parse_worker_seconds(output: str) -> float | None:
    found = re.search(r'"worker_seconds":\s*([0-9.]+)', output)
    return float(found.group(1)) if found else None


def part_ids(project: Path) -> list[str]:
    data = json.loads((project / "project.json").read_text(encoding="utf-8"))
    found: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            if node.get("type") == "part" and node.get("id"):
                found.append(node["id"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(data["root"])
    return found


class Server:
    """``python -m stokes.server`` on a free port; its output is drained by a thread, so a chatty
    server never blocks on a full pipe, and its first line with a token gives the URL.

    ``compress`` starts it with ``--compress-loopback``: it gzips for this script, which is on
    loopback, as it does for a remote browser. Without it the server sends meshes as they are.
    """

    def __init__(self, project: Path, *, compress: bool = False) -> None:
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
        if compress:
            argv.append("--compress-loopback")
        self.proc = subprocess.Popen(
            argv,
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        lines: queue.Queue[str] = queue.Queue()

        def drain() -> None:
            assert self.proc.stdout is not None
            for line in self.proc.stdout:
                lines.put(line)

        threading.Thread(target=drain, daemon=True).start()
        deadline = time.monotonic() + START_TIMEOUT
        self.token = ""
        while not self.token:
            left = deadline - time.monotonic()
            if left <= 0:
                self.stop()
                raise SystemExit(f"the server printed no token within {START_TIMEOUT:.0f} s")
            try:
                line = lines.get(timeout=min(left, 1.0))
            except queue.Empty:
                if self.proc.poll() is not None:
                    raise SystemExit("the server exited before printing its url") from None
                continue
            found = re.search(r"token=([A-Za-z0-9_-]+)", line)
            if found:
                self.token = found.group(1)

    def stop(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=10)


def fetch(server: Server, chunk: list[str], compressed: bool) -> tuple[float, int, int]:
    """One batch request: (ms, bytes on the wire, bytes of meshes).

    ``compressed`` is what the server is expected to answer with, not what is asked for: every
    request offers gzip, as a browser does and urllib does not, so it is set explicitly and undone
    here.
    """
    ids = ",".join(urllib.parse.quote(i, safe="") for i in chunk)
    url = f"http://127.0.0.1:{server.port}/api/geometry/batch?ids={ids}"
    headers = {"X-Stokes-Token": server.token, "Accept-Encoding": "gzip"}
    began = time.perf_counter()
    # A loopback http URL this script built, never a scheme from outside.
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:  # noqa: S310
        body = response.read()
        gzipped = response.headers.get("Content-Encoding") == "gzip"
    elapsed = (time.perf_counter() - began) * 1000
    if gzipped != compressed:
        raise SystemExit(
            f"expected a {'gzip' if compressed else 'identity'} reply, got "
            f"{'gzip' if gzipped else 'identity'}: does the server compress for loopback clients "
            f"only with --compress-loopback?"
        )
    return elapsed, len(body), len(gzip.decompress(body) if gzipped else body)


def transfer_pass(server: Server, ids: list[str], compressed: bool) -> dict[str, float]:
    """All of `ids` in chunks of CHUNK, IN_FLIGHT at a time, as the viewport asks for them."""
    chunks = [ids[i : i + CHUNK] for i in range(0, len(ids), CHUNK)]
    began = time.perf_counter()
    with ThreadPoolExecutor(max_workers=IN_FLIGHT) as pool:
        results = list(pool.map(lambda chunk: fetch(server, chunk, compressed), chunks))
    total = (time.perf_counter() - began) * 1000
    times = sorted(ms for ms, _, _ in results)
    wire = sum(w for _, w, _ in results)
    raw = sum(r for _, _, r in results)
    return {
        "requests": len(chunks),
        "total_ms": total,
        "request_p50_ms": statistics.median(times),
        "request_p95_ms": times[min(len(times) - 1, int(len(times) * 0.95))],
        "wire_mb": wire / 1e6,
        "payload_mb": raw / 1e6,
        "ratio": raw / wire if wire else 0.0,
    }


def measure_transfer(project: Path, counts: list[int]) -> list[dict[str, object]]:
    """Per row and encoding: a fresh server, a cold pass and a warm pass."""
    ids = part_ids(project)
    rows: list[dict[str, object]] = []
    for count in counts:
        for compressed in (True, False):
            server = Server(project, compress=compressed)
            try:
                for store in ("cold", "warm"):
                    row = transfer_pass(server, ids[:count], compressed)
                    rows.append(
                        {
                            "parts": count,
                            "encoding": "gzip" if compressed else "identity",
                            "store": store,
                            **row,
                        }
                    )
            finally:
                server.stop()
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--step", type=Path, help="STEP file to import into a fresh project")
    parser.add_argument("--project", type=Path, help="an existing project to profile")
    parser.add_argument("--rows", default="1,50,256,1000,all", help="part counts, 'all' for every")
    parser.add_argument("--keep", action="store_true", help="keep the temporary project")
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()

    report: dict[str, object] = {}
    tmp: Path | None = None

    if args.step:
        if not args.step.is_file():
            raise SystemExit(f"{args.step} is not a file")
        tmp = Path(tempfile.mkdtemp(prefix="stokes-bench-")) / "bench.stokes"
        seconds, output = run_cli("new", str(tmp))
        print(f"new project   : {seconds:6.2f} s")
        seconds, output = run_cli("geometry", "import", str(args.step), "--project", str(tmp))
        worker = parse_worker_seconds(output)
        parts = len(part_ids(tmp))
        report["import"] = {
            "file": str(args.step),
            "file_mb": args.step.stat().st_size / 1e6,
            "parts": parts,
            "wall_s": seconds,
            "worker_s": worker,
        }
        print(
            f"import (cold) : {seconds:6.2f} s wall"
            + (f"  ({worker:.2f} s in the CAD worker)" if worker else "")
            + f"   {parts} parts from {args.step.stat().st_size / 1e6:.1f} MB"
        )
        project = tmp
    elif args.project:
        project = args.project
        seconds, _ = run_cli("info", project=project)
        report["reopen"] = {"wall_s": seconds}
        print(f"reopen        : {seconds:6.2f} s   {len(part_ids(project))} parts")
    else:
        raise SystemExit("give --step or --project")

    total = len(part_ids(project))
    counts = sorted(
        {total if c.strip() == "all" else int(c) for c in args.rows.split(",") if c.strip()}
    )
    try:
        rows = measure_transfer(project, [c for c in counts if 0 < c <= total])
        report["transfer"] = rows
        print(
            f"\nmesh transfer: GET /api/geometry/batch, <= {CHUNK} ids per request, "
            f"{IN_FLIGHT} in flight; a fresh server per row and encoding "
            "(identity: the default for a loopback client; gzip: --compress-loopback)"
        )
        print(
            f"  {'parts':>6}  {'encoding':>8}  {'store':>5}  {'reqs':>4}  {'total ms':>9}  "
            f"{'req p50':>8}  {'req p95':>8}  {'wire MB':>8}  {'raw MB':>8}  {'ratio':>6}"
        )
        for row in rows:
            print(
                f"  {row['parts']:6d}  {row['encoding']:>8}  {row['store']:>5}  "
                f"{row['requests']:4d}  {row['total_ms']:9.1f}  {row['request_p50_ms']:8.1f}  "
                f"{row['request_p95_ms']:8.1f}  {row['wire_mb']:8.2f}  {row['payload_mb']:8.2f}  "
                f"{row['ratio']:5.1f}x"
            )
    finally:
        if tmp is not None and not args.keep:
            shutil.rmtree(tmp.parent, ignore_errors=True)

    if args.json:
        args.json.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
