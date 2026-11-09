"""Is the background `restore` warming the cache for the page, or racing it?

`tools/bench_duplicate_reads.py` counted 5,620 cache reads for 4,500 parts during one page load:
1,797 on `stokes-geometry-restore` and 3,823 on the request worker threads. This tool puts a
timestamp on each read, so the overlap is visible: if restore finishes before the requests need
those parts it is warming; if the two interleave over the same ids it is duplicated work on the
same per-open bottleneck.

Writes `t096_timeline.json`: a list of (seconds since the first read, thread, node_id).

Usage:
    python tools/bench_read_timeline.py --project <folder.stokes> [--seconds 45]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench_viewport import free_port

ROOT = Path(__file__).resolve().parent.parent

INSTR = r"""
import os, sys, threading, json, time
sys.path.insert(0, os.environ["STOKES_ROOT"])
from stokes.geometry import cache as _cache
_real = _cache.read_cache
_events = []
_lock = threading.Lock()
_t0 = [None]
_out = os.path.join(os.environ.get("TEMP", "/tmp"), "t096_timeline.json")


def read_cache(project_dir, node_id, source):
    now = time.perf_counter()
    with _lock:
        if _t0[0] is None:
            _t0[0] = now
        _events.append([round((now - _t0[0]) * 1000, 3), threading.current_thread().name, node_id])
        if len(_events) % 10 == 0:
            try:
                with open(_out, "w", encoding="utf-8") as fh:
                    json.dump(_events, fh)
            except OSError:
                pass
    return _real(project_dir, node_id, source)


_cache.read_cache = read_cache
import stokes.geometry.store as _store
_store.read_cache = read_cache

import atexit


def _dump():
    try:
        with open(_out, "w", encoding="utf-8") as fh:
            json.dump(_events, fh)
    except OSError:
        pass


atexit.register(_dump)
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bench-read-timeline", description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=45.0)
    args = parser.parse_args(argv)

    out = Path(tempfile.gettempdir()) / "t096_timeline.json"
    out.unlink(missing_ok=True)
    port = free_port()
    # The helper files go in the temp folder: a bench must not litter the repository.
    instrument_file = Path(tempfile.gettempdir()) / "_stokes_t096_tl.py"
    bootstrap = Path(tempfile.gettempdir()) / "_stokes_t096_tl_boot.py"
    bootstrap.write_text(
        "import os, sys\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        f"os.environ['STOKES_ROOT'] = {str(ROOT)!r}\n"
        "g = globals()\n"
        f"exec(compile(open({str(instrument_file)!r}, encoding='utf-8').read(), 'tl', 'exec'), g)\n"
        "import runpy\n"
        "sys.argv = ['stokes.server', '--project', "
        + repr(str(args.project))
        + ", '--port', "
        + repr(str(port))
        + "]\n"
        "runpy.run_module('stokes.server', run_name='__main__')\n",
        encoding="utf-8",
    )
    instrument_file.write_text(INSTR, encoding="utf-8")

    log_path = Path(tempfile.gettempdir()) / "t096_tl_server.log"
    log = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, str(bootstrap)],
        cwd=str(ROOT),
        stdout=log,
        stderr=subprocess.STDOUT,
        text=True,
    )
    token = ""
    deadline = time.monotonic() + 120
    while not token and time.monotonic() < deadline:
        found = re.search(
            r"token=([A-Za-z0-9_-]+)", log_path.read_text(encoding="utf-8", errors="replace")
        )
        if found:
            token = found.group(1)
        time.sleep(0.2)
    if not token:
        proc.terminate()
        raise SystemExit("no token")

    sys.path.insert(0, str(ROOT / "tools"))
    from bench_scene import Chrome, DevTools, find_browser

    chrome = Chrome(
        find_browser(), f"http://127.0.0.1:{port}/?token={token}&debug=1", swiftshader=False
    )
    try:
        dev = DevTools(chrome.page_url)
        try:
            began = time.monotonic()
            while time.monotonic() - began < args.seconds:
                snap = dev.evaluate(
                    "(() => { const p = window.__stokesScene; return p ? p.perf() : null; })()"
                )
                if isinstance(snap, dict) and int(snap.get("parts", 0)) >= 4500:
                    print(f"all parts drawn after {time.monotonic() - began:.1f} s")
                    break
                time.sleep(0.25)
        finally:
            dev.close()
    finally:
        chrome.stop()
        time.sleep(1.5)
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()

    if not out.is_file():
        print("no timeline written")
        return 1
    events = json.loads(out.read_text(encoding="utf-8"))
    by_thread: dict[str, list[float]] = {}
    for ms, thread, _nid in events:
        by_thread.setdefault(thread, []).append(ms)
    print(f"{len(events)} reads")
    for thread, times in sorted(by_thread.items(), key=lambda kv: min(kv[1])):
        print(
            f"  {thread:<26} {len(times):5d} reads, "
            f"first at {min(times):8.1f} ms, last at {max(times):8.1f} ms"
        )
    ids_seen: dict[str, int] = {}
    for _ms, _t, nid in events:
        ids_seen[nid] = ids_seen.get(nid, 0) + 1
    dupes = {k: v for k, v in ids_seen.items() if v > 1}
    extra = sum(v - 1 for v in dupes.values())
    print(
        f"  distinct ids: {len(ids_seen)}, ids read more than once: {len(dupes)}, "
        f"extra reads: {extra}"
    )
    print(f"  wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
