"""A transient run's field ranges over every written time (ADR-0022 addendum 2026-11-24).

A legend in Auto follows the time on screen, so each frame of a playback got its own range and the
colours of two frames could not be compared. This works out, once per run, each field's range over
**all** the times the run wrote: the host extracts the fields at each time in turn
(`extract.fields`), the header of each artifact gives that time's ranges (`min`/`max` over the
patches and the slice, the 1-99 % percentiles, the volume's), and the union is kept in
`<results cache>/<run id>.ranges.json` beside the run's fields. `run.fields` then reports it per
field as `all_times`, and the legend uses it by default for a run with several times.

**Background work, and only once**: a worker thread (one per run) does the reading and emits
`run.ranges` when the file is there; until then the legend uses the shown time's range and says
so. The agent keeps one fields artifact per job, so each extraction holds the store's extract lock
and a playback's own reads interleave with these rather than racing them. The initial condition
(time 0) is left out, as the time slider leaves it out: it is the set-up, not a result.

Import rule: the standard library and `stokes.run`; headless.
"""

from __future__ import annotations

import contextlib
import json
import math
import mmap
import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from stokes.log import fields, get_logger

if TYPE_CHECKING:
    from stokes.run.store import RunStore

log = get_logger(__name__)

#: The suffix of the cache file beside `<run id>.stkfld`.
RANGES_SUFFIX = ".ranges.json"
#: The most written times read; past this every n-th is read, the first and the last always.
MAX_TIMES = 200

Spans = dict[str, tuple[float, float]]


def ranges_path(store: RunStore, run_id: str) -> Path:
    """`<results cache>/<run id>.ranges.json`, with the fields file's id and link checks."""
    path = store.fields_path(run_id)
    return path.with_name(f"{run_id}{RANGES_SUFFIX}")


def times_to_read(times: list[float]) -> list[float]:
    """The written times whose ranges count: every one after the initial condition, thinned to at
    most `MAX_TIMES` (the first and the last kept)."""
    found = sorted({t for t in times if math.isfinite(t)})
    if len(found) > 1:
        found = [t for t in found if t > 0]
    if len(found) <= MAX_TIMES:
        return found
    step = (len(found) - 1) / (MAX_TIMES - 1)
    return sorted({found[round(i * step)] for i in range(MAX_TIMES)})


def union(into: Spans, spans: Spans) -> Spans:
    """`into` widened by `spans`, field by field."""
    out = dict(into)
    for name, (low, high) in spans.items():
        if name in out:
            out[name] = (min(out[name][0], low), max(out[name][1], high))
        else:
            out[name] = (low, high)
    return out


def read_cached(store: RunStore, run_id: str) -> dict[str, Any] | None:
    """The cached all-times ranges of a run, or None when they are not worked out yet."""
    try:
        path = ranges_path(store, run_id)
        if not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    except Exception:
        log.warning("the all-times ranges are unreadable", extra=fields(run=run_id), exc_info=True)
        return None
    return data if isinstance(data, dict) and isinstance(data.get("ranges"), dict) else None


def status(store: RunStore, run_id: str) -> str:
    """`ready`, `computing` or `none` (a run with one time, or none to read)."""
    if read_cached(store, run_id) is not None:
        return "ready"
    return "computing" if store.range_worker_alive(run_id) else "none"


def start(store: RunStore, run_id: str, times: list[float]) -> str:
    """Start working out the ranges in the background, unless they are there or on their way.

    Returns the state after the call: `ready`, `computing`, or `none` for a run with a single
    time (its range is the shown time's, so there is nothing to work out)."""
    if read_cached(store, run_id) is not None:
        return "ready"
    wanted = times_to_read(times)
    if len(wanted) < 2:
        return "none"
    worker = threading.Thread(
        target=_work, args=(store, run_id, wanted), name="stokes-time-ranges", daemon=True
    )
    if not store.claim_range_worker(run_id, worker):
        return "computing"
    worker.start()
    return "computing"


def _header_of(path: Path) -> dict[str, Any]:
    """The header of a fields artifact on disk, through a mapping closed before it returns."""
    from stokes.run.fields import read_header

    with path.open("rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as m:
        view = memoryview(m)
        try:
            return read_header(view)
        finally:
            view.release()


def _work(store: RunStore, run_id: str, times: list[float]) -> None:
    """The worker: extract every time, widen the spans, write the file, say so."""
    try:
        found = _measure(store, run_id, times)
        if found is None:
            return
        target = ranges_path(store, run_id)
        part = target.with_name(target.name + ".part")
        part.write_text(json.dumps(found, sort_keys=True), encoding="utf-8")
        part.replace(target)
        store.emit_event("run.ranges", {"run_id": run_id, "times": len(times)})
    except Exception as exc:
        log.warning(
            "the all-times ranges could not be worked out",
            extra=fields(run=run_id, error=str(exc)[:200]),
            exc_info=True,
        )
        store.emit_event("run.ranges_failed", {"run_id": run_id, "message": str(exc)[:200]})
    finally:
        store.release_range_worker(run_id)


def _measure(store: RunStore, run_id: str, times: list[float]) -> dict[str, Any] | None:
    from stokes.run.artifacts import fetch_artifact
    from stokes.run.hosts import HostRegistry
    from stokes.run.store import EXTRACT_FIELDS_TIMEOUT_S

    record = store.record(run_id)
    if record is None or not record.job:
        return None
    runner = HostRegistry.of(store.session).runner(record.host or "local")
    spans: Spans = {}
    percentiles: Spans = {}
    volume: Spans = {}
    # **One scratch file per process and worker**, not per run: two Stokes processes on one
    # project (a second window, or a server left running) both work the ranges out, and a shared
    # name made one fail with `WinError 32` while the other held the file (2026-11-25).
    scratch = ranges_path(store, run_id).with_name(
        f"{run_id}.ranges.{os.getpid()}.{threading.get_ident()}.stkfld"
    )
    scratch.parent.mkdir(parents=True, exist_ok=True)
    for time in times:
        with store.extract_lock:
            summary = runner.call(
                "extract.fields",
                job=record.job,
                surfaces_job=record.mesh_job or record.job,
                slice=store.slice_plane(),
                time=float(time),
                timeout=EXTRACT_FIELDS_TIMEOUT_S,
            )
            if not isinstance(summary, dict):
                raise RuntimeError("extract.fields returned no summary")
            fetch_artifact(
                runner,
                record.job,
                str(summary["artifact"]),
                scratch,
                str(summary["sha256"]),
                int(summary["size"]),
            )
        try:
            header = _header_of(scratch)
        finally:
            with contextlib.suppress(OSError):
                scratch.unlink()
        spans = union(spans, store.spans_of(header, "min", "max"))
        percentiles = union(percentiles, store.spans_of(header, "p01", "p99"))
        volume = union(volume, store.volume_spans_of(header))
    return {
        "times": times,
        "ranges": {name: list(pair) for name, pair in spans.items()},
        "percentiles": {name: list(pair) for name, pair in percentiles.items()},
        "volume": {name: list(pair) for name, pair in volume.items()},
    }


def field_entry(cached: dict[str, Any], name: str) -> dict[str, Any] | None:
    """One field's all-times entry for `run.fields`: `{min, max, p01?, p99?, times}`, or None."""
    pair = _pair(cached.get("ranges"), name)
    if pair is None:
        return None
    count = len(cached.get("times") or [])
    entry: dict[str, Any] = {"min": pair[0], "max": pair[1], "times": count}
    low_high = _pair(cached.get("percentiles"), name)
    if low_high is not None:
        entry["p01"], entry["p99"] = low_high
    return entry


def _pair(table: Any, name: str) -> tuple[float, float] | None:  # Any: JSON
    if not isinstance(table, dict):
        return None
    value = table.get(name)
    if not (isinstance(value, list) and len(value) == 2):
        return None
    try:
        low, high = float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return None
    return (low, high) if math.isfinite(low) and math.isfinite(high) and low <= high else None
