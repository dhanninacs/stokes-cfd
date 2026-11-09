"""Is the cold mesh-cache cost the file open, or the decode? One clean answer.

The T-096 profile of `/api/geometry/batch` (tools/bench_batch_server.py, 4,500 parts, cold) put
6,603 ms of a 6,959 ms server-side total in `read_cache`, and only 795 ms of that in `unpack`.
The rest is `path.read_bytes()` on 4,500 files of about 5.7 KB each. This tool measures the same
4,500 bytes with the file API alone, so the answer does not depend on the server being up:

  * open + read + close, one file at a time (exactly what `read_cache` does)
  * the same reads on a worker thread pool, to see whether the cost parallelises
  * a single open of a concatenated blob, read of every part's slice, to see what the same bytes
    cost without 4,500 separate handles
  * `unpack` on the buffers, for the decode's share of the same run

Usage:
    python tools/bench_cache_read.py --project <folder.stokes> [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench_viewport import part_ids

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _cache_files(project: Path) -> list[Path]:
    from stokes.geometry.cache import cache_file

    return [cache_file(project, pid) for pid in part_ids(project)]


def timed(label: str, fn: Callable[[], object]) -> float:
    began = time.perf_counter()
    fn()
    ms = (time.perf_counter() - began) * 1000
    print(f"  {label:<46} {ms:9.1f} ms")
    return ms


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bench-cache-read", description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)

    files = _cache_files(args.project)
    sizes = [f.stat().st_size for f in files]
    print(
        f"{len(files)} cache files, {sum(sizes) / 1e6:.2f} MB, "
        f"p50 {statistics.median(sizes):.0f} bytes"
    )
    out: dict[str, float] = {}

    # 1. Exactly what read_cache does.
    def serial_read() -> bytes:
        last = b""
        for path in files:
            last = path.read_bytes()
        return last

    out["serial_read_bytes"] = timed("open+read+close, one at a time", serial_read)

    # 2. The same reads across a pool.
    def pooled_read() -> None:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            list(pool.map(Path.read_bytes, files))

    out[f"pooled_read_bytes_x{args.workers}"] = timed(
        f"open+read+close, {args.workers} threads", pooled_read
    )

    # 3. The same bytes through one handle: a concatenated blob read in slices.
    blob = files[0].parent / "_t096_blob.bin"

    def blob_read() -> None:
        with blob.open("rb") as handle:
            offsets = []
            total = 0
            for size in sizes:
                offsets.append(total)
                total += size
            for offset, size in zip(offsets, sizes, strict=True):
                handle.seek(offset)
                handle.read(size)

    try:
        with blob.open("wb") as handle:
            for path in files:
                handle.write(path.read_bytes())
        out["blob_seek_read"] = timed("one handle, seek+read per part", blob_read)
    finally:
        blob.unlink(missing_ok=True)

    # 4. The decode alone, on buffers already in memory.
    from stokes.geometry.transport import unpack

    payloads = [path.read_bytes() for path in files]

    def decode() -> None:
        for data in payloads:
            unpack(data)

    out["unpack"] = timed("unpack, buffers in memory", decode)

    # 5. read+decode, the real cold path, no server in the way.
    from stokes.geometry.cache import read_cache

    def cold() -> None:
        for node_id in part_ids(args.project):
            read_cache(args.project, node_id, None)

    out["read_cache_serial"] = timed("read_cache, one at a time (the real path)", cold)

    def cold_pooled() -> None:
        ids = part_ids(args.project)
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            list(
                pool.map(
                    lambda pair: read_cache(args.project, pair[0], None),
                    zip(ids, files, strict=True),
                )
            )

    out[f"read_cache_x{args.workers}"] = timed(f"read_cache, {args.workers} threads", cold_pooled)

    if args.json:
        args.json.write_text(
            json.dumps({"files": len(files), "ms": out}, indent=2), encoding="utf-8"
        )
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
