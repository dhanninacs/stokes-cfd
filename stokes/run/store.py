"""The run store: the records, their files, and the hooks the session calls (ADR-0023, T-214b).

`RunStore.of(session)` is the session's single store, created on first use like `GeometryStore.of`.

## Records live on disk, because a run outlives a session

A solver run takes minutes; the desktop is closed, the agent keeps going, and the user reopens the
project. So every record is written to `<runs_dir>/<run_id>/record.json` **after every change**, and
`project_opened` reads them back. A record in memory only would make a finished run vanish on
reopen.

`<runs_dir>` is `<project folder>/runs` when the project has been saved, and a per-session
scratch folder
otherwise — a project that has no folder yet still gets a working run list, and moving it on
Save as moves
the runs with it.

## Re-attaching a run that outlived the session

`project_opened` finds a record still marked `running` and asks the host about its job. Three
answers:
`done` finishes it, `failed`/`cancelled` record that, and **no answer at all means
`interrupted`** — the
one state that says "we do not know", which is the truth after a restart and is what a user
needs to see
rather than a stale `running` that will never change.

## Threading

Every method here is called on the **session thread** except `on_event`, which the runner
calls from its
own event thread. `on_event` therefore does no work: it maps the event and hands it to the session's
dispatch, which is what keeps a slow listener from stalling the agent's reader.
"""

from __future__ import annotations

import builtins
import contextlib
import json
import logging
import math
import mmap
import os
import shutil
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from stokes.geometry.errors import MeshFormatError
from stokes.log import fields
from stokes.mesh.errors import MeshInvalid
from stokes.model.ids import new_id
from stokes.run.errors import RunError
from stokes.run.fields import SLICE, SLICE_INDEX, RunFieldsInvalid, field_array, read_header
from stokes.run.planes import MAX_CONCURRENT_CUTS
from stokes.run.records import (
    EXTERNAL_ENGINE,
    FIELD_STATES,
    FINAL_STATES,
    MAX_RECORD_BYTES,
    RunRecord,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from stokes.api.session import Session
    from stokes.run.iso import IsoRequest

log = logging.getLogger(__name__)

#: The session extension key, like `GeometryStore`'s.
EXTENSION = "run"

#: One lock for the whole module: a store is per session, and two sessions in one process must not
#: create two stores for the same session concurrently.
_store_lock = threading.RLock()

# : How long a caller may wait for a run to finish. A steady duct run is seconds; a big case
# is hours.
RUN_TIMEOUT_S = 7200.0

# : The most residual blocks kept in memory. A one-second duct run is ~30; this is for a run
# left going
#: overnight, and the file on disk is authoritative past it.
MAX_HISTORY = 200_000

#: Where a run's fields live: `<cache>/results/<run id>.stkfld` (T-216), the mesh cache's sibling.
RESULTS_FOLDER = "results"
RESULTS_SUFFIX = ".stkfld"
#: The cached planes of a run: `<cache>/results/slices/<run id>/<plane hash>.stkslice`. **One file
#: per plane**, so trimming to `MAX_CACHED_PLANES` is a delete rather than a rewrite, and a plane
#: that is already cached is served without touching the others.
SLICES_FOLDER = "slices"
SLICE_SUFFIX = ".stkslice"

#: How long `extract.fields` may take. It reads the solved case with VTK on the host, so it is a
#: read of the whole case rather than a round trip — the same class of call as `extract.surfaces`.
EXTRACT_FIELDS_TIMEOUT_S = 600.0
#: How long one plane cut may take. Shorter than the full extraction: a cut reads the internal mesh
#: and nothing else, so a host that needs this long is not slow, it is gone.
CUT_SLICE_TIMEOUT_S = 300.0

#: How many runs' field files are kept in memory as `memoryview`s. Two, like the surfaces cache:
#: a user looks at one run's fields, and comparing two is the reason for the second.
FIELD_LRU = 2


def runs_dir(session: Session) -> Path:
    """Where a project's run records live: **`<project folder>/runs`**.

    **One answer, because a project always has a folder** (claude-win2, reading 1, binding). The
    owner's rule is that nothing a project works with lives outside its folder, and the per-session
    `tempfile.mkdtemp` that used to serve an unsaved project is what put runs in `/tmp` - swept up
    afterwards by a 24-hour start-up pass over `SCRATCH_PREFIXES`, which is treating the symptom.

    `reset_scratch` and `discard_scratch` went with it: they gave each unsaved project its own
    scratch folder and deleted it once its records were copied out, and **both were already
    unreachable** - measured, nothing called either.
    """
    if session.project_path is None:
        # **A session with no project has no runs either.** Reaching here means a caller asked for
        # run records before opening or creating a project, which is a mistake in the call rather
        # than something to paper over with a temporary directory.
        from stokes.errors import StokesError

        raise StokesError(
            "this session has no project, so it has no runs folder; open or create a project first",
            code="run.no_project",
        )
    return Path(session.project_path) / "runs"


#: How long a request waits for a cut slot before giving up (Opus M4). **Bounded on purpose**: the
#: wait used to be `CUT_SLICE_TIMEOUT_S` per caller with no ceiling on how many callers queue, so
#: forty distinct planes against a hung host held every route thread for minutes each. A short wait
#: and a `Retry-After` tells the client the truth - "busy, come back" - instead of a request that
#: never returns.
CUT_QUEUE_TIMEOUT_S = 30.0
#: The most distinct planes ONE run may have queued at once. A drag can ask for many; a host can
#: only cut a few, and the rest are a queue that grows the memory and the wait without bound.
MAX_QUEUED_PLANES_PER_RUN = 8
#: The most distinct planes the whole server may have queued, across every run.
#:
#: **THE NUMBERS ARE SET BY THE THREAD POOL, NOT BY TASTE** (Opus round 3, item 6b). Every queued
#: cut and every waiter **holds a route thread** for up to `CUT_QUEUE_TIMEOUT_S`, and the server's
#: default pool is **40 threads**. The first version allowed 32 queued cuts + 16 waiters = **48**,
#: which is MORE THAN THE POOL - so a burst of slice requests could occupy every thread and stall
#: every other route, including the ones that would have answered. A cap above the resource it
#: protects is not a cap.
#:
#: So the two together must stay well under 40, and the headroom is deliberate: the pool also serves
#: ordinary commands while cuts are running. 16 + 8 = 24 leaves 16 threads for everything else.
MAX_QUEUED_PLANES = 16
#: The most requests that may WAIT on one plane. A duplicate is cheap to serve and expensive to
#: queue: each holds a route thread for `CUT_QUEUE_TIMEOUT_S`, and the caps above counted only the
#: first caller (Opus recheck item 4b).
MAX_WAITERS_PER_PLANE = 2
#: The most requests waiting on a cut, across every plane and run.
MAX_WAITERS = 8
#: The most slice-error reasons kept for the WHOLE store. **The per-run bound alone grows with the
#: number of runs** (Opus round 3, item 6c): every run that fails keeps its own allowance, so a
#: long session accumulates them one run at a time.
MAX_SLICE_ERRORS = 256


class SliceBusy(Exception):
    """No cut slot came free in time. The route turns this into 503 with a `Retry-After`.

    **Not a failure of the request.** The plane may be perfectly cuttable; the server is simply at
    its limit, and saying so is what lets a client back off rather than retry into the same wall.
    """

    code = "results.busy"
    retry_after = 5


@dataclass(frozen=True)
class _CutRequest:
    """One host cut, of either kind: the agent op, its arguments, and how its result is kept.

    `error_key` is what a failure is recorded under in `_slice_errors` (a plane's `plane_key`, an
    iso-surface's `iso-<iso_key>`); `accept` may refuse the reply before its artifact is fetched;
    `suffix` picks the cache files the LRU trims, so planes and iso-surfaces are bounded apart.
    """

    op: str
    args: dict[str, Any]
    error_key: str
    accept: Callable[[dict[str, Any]], None] | None = None
    suffix: str = SLICE_SUFFIX


def run_order_key(record: RunRecord) -> tuple[str, str]:
    """`(when, id)` - the order a run's place in the list is decided by.

    **`when` falls back to `updated`, then to the empty string.** Measured on the audit's project:
    its only run has `created = ""` and a real `updated`, and sorting on `created` alone put it
    before every dated run - a record that sorts equal to nothing and therefore always first.

    **The id breaks ties**, so two runs created in the same second keep a stable order rather than
    swapping between reads. A record with neither stamp still sorts first, which is the honest place
    for "we do not know when this was" - it cannot be placed later without inventing a time.
    """
    return (record.created or record.updated or "", record.id)


class RunStore:
    """The session's runs: records, their files, and the extension hooks."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._lock = threading.RLock()
        #: Serialises `_write`: two threads renaming the same `.tmp` over `record.json` is what
        #: Windows refused with "Access is denied" on nearly every run of the tutorial sweep.
        self._write_lock = threading.Lock()
        self._order: list[str] = []
        self._records: dict[str, RunRecord] = {}
        self._history: dict[str, dict[str, Any]] = {}
        self._events: Callable[[str, dict[str, Any]], None] | None = None
        #: The field cache: an LRU of at most `FIELD_LRU` `memoryview`s, keyed by path, stamped
        #: with `(size, mtime_ns)` so a file replaced under us is read again.
        self._field_views: OrderedDict[str, tuple[tuple[int, int], memoryview, dict[str, Any]]] = (
            OrderedDict()
        )
        #: `{run id: {field: (min, max)}}`, the ranges `run.fields` reports.
        self._field_ranges: dict[str, dict[str, tuple[float, float]]] = {}
        #: `{run id: time}`, the solved time the artifact was cut at.
        self._field_time: dict[str, float] = {}
        #: `{run id: time}` a user chose with `run.time`; every later cut of the run (fields,
        #: planes, iso-surfaces) is made at it. Absent means the latest, as a run starts with.
        self._chosen_time: dict[str, float] = {}
        #: Why a plane cut could not be made, **per (run, plane)**, so `slice_state` can tell a host
        #: that is gone from a plane that simply has nothing on it.
        #:
        #: **A single attribute was the bug** (Opus M2). It was one string for the whole store,
        #: set by any failure and read by any later question - so a host that refused once turned
        #: every subsequent "this plane has nothing on it" into "the host is unavailable", sending
        #: a user to check a machine that was fine. The docstring claimed it was "cleared at the
        #: start of every cut"; **it never was**, which is the kind of comment that hides a defect
        #: rather than describing one.
        #:
        #: Keyed by `(run id, plane key)` because that is the question being asked: the answer for
        #: one plane of one run says nothing about any other.
        self._slice_errors: dict[tuple[str, str], str] = {}
        #: At most `MAX_CONCURRENT_CUTS` cuts run at once across the server (the contract's rule).
        #: A cut reads a solved case with VTK, so two already saturate a small machine and more
        #: would only queue inside VTK where nothing can report progress or honour a limit.
        self._cut_slots = threading.Semaphore(MAX_CONCURRENT_CUTS)
        #: Which run owns each queued cut, so the per-run cap can be counted (Opus M4).
        self._cut_ownership: set[tuple[str, str]] = set()
        #: How many requests wait on each cache path, so a flood of DUPLICATES is counted too
        #: (Opus recheck item 4b). The caps above govern a queue, and a waiter is in the queue.
        self._cut_waiters: dict[str, int] = {}
        #: The cuts in flight, by cache path, so two identical requests compute ONE cut. Without
        #: this a client that asks twice while the first is running pays for both, which is exactly
        #: what a debounced drag does.
        self._cut_waiting: dict[str, threading.Event] = {}
        #: The finished runs whose history is being (or was) read back from their host after a
        #: reopen: `loading`, `done` or `failed`. One read per run per open project.
        self._history_reads: dict[str, str] = {}

    # ------------------------------------------------------------------ construction

    @classmethod
    def of(cls, session: Session) -> RunStore:
        """The session's store, created on first use.

        **A store created after the project is already open loads it.** The extension hooks arrive
        only at the moment the project changes, so a store made afterwards — which is what the
        results routes and `run.*` both do, lazily, on the first request after a reopen — would
        otherwise see an empty store while the records sat on disk. This is the difference between
        "no runs" and "the runs I saved", and it is why the lazy construction cannot be the whole
        story. Loading is idempotent, so an extra call costs a directory read.
        """
        with _store_lock:
            store = session.extensions.get(EXTENSION)
            if not isinstance(store, RunStore):
                store = cls(session)
                session.extensions[EXTENSION] = store
                if session.project is not None and not store._order:
                    store.project_opened(session.project, session.project_path)
            return store

    def bind(self, emit: Callable[[str, dict[str, Any]], None]) -> None:
        """Where events go. The server passes the session's emitter; a test passes a list
        appender."""
        self._events = emit

    def _emit(self, name: str, data: dict[str, Any]) -> None:
        if self._events is not None:
            self._events(name, data)

    # ------------------------------------------------------------------ reading

    def all_records(self) -> list[dict[str, Any]]:
        """Every record, newest first.

        **Not named `list`.** A method named after a builtin generic shadows it *inside the class*,
        including in annotations — mypy read `blocks: list[dict]` as an instance of this method and
        refused the file. The brief writes the signature as `list()`, and `run.list` is the COMMAND
        name; this is the store's own method and the call site reads better for the rename.
        """
        with self._lock:
            return [self._records[run_id].to_dict() for run_id in reversed(self._order)]

    #: The brief's spelling, kept working for callers that use it.
    def list(self) -> list[dict[str, Any]]:
        return self.all_records()

    def current(self) -> RunRecord | None:
        """The newest record, or None. "The current run" is always this."""
        with self._lock:
            return self._records[self._order[-1]] if self._order else None

    def record(self, run_id: str | None) -> RunRecord | None:
        """One record by id, or the current one when `run_id` is None."""
        with self._lock:
            if run_id is None:
                return self._records[self._order[-1]] if self._order else None
            return self._records.get(run_id)

    def status(self, run_id: str | None = None) -> dict[str, Any]:
        """A record as a dict plus the step and fraction the record itself does not hold."""
        found = self.record(run_id)
        if found is None:
            from stokes.run.errors import RunNotFound

            raise RunNotFound(f"no run {run_id or '(current)'}")
        with self._lock:
            extra = dict(self._live.get(found.id, {})) if hasattr(self, "_live") else {}
        return {**found.to_dict(), **extra}

    def residuals(self, run_id: str | None = None, since: int = 0) -> dict[str, Any]:
        """The residual history from the block after `since`.

        **The blocks are kept whole and the lists are built on read.** The first version stored one
        parallel list per field and indexed them by position, which raised `IndexError` the moment a
        field was absent from a block — and fields ARE absent: `p` is solved twice per step, a
        laminar
        run has no `k`, and the final block can report a field that earlier ones did not.

        **A missing field is `null`, not a shorter list**, so a chart's x and y stay aligned:
        one entry
        per iteration, `null` where the solver did not report that field.
        """
        found = self.record(run_id)
        if found is None:
            from stokes.run.errors import RunNotFound

            raise RunNotFound(f"no run {run_id or '(current)'}")
        loading = self._history_from_host(found)
        with self._lock:
            blocks = [
                block
                for block in (self._history.get(found.id, {}).get("blocks") or [])
                if int(block.get("iteration") or 0) > int(since)
            ]
            # **One pass to find the names, then one projection per name**:
            # `list.index(block)` inside
            # the loop would be O(n²), which minutes on a 200,000-block history.
            fields: dict[str, list[Any]] = {}
            continuity: dict[str, list[Any]] = {}
            for block in blocks:
                for name in block.get("residuals") or {}:
                    fields.setdefault(name, [])
                for name in block.get("continuity") or {}:
                    continuity.setdefault(name, [])
            for name in fields:
                fields[name] = [(block.get("residuals") or {}).get(name) for block in blocks]
            for name in continuity:
                continuity[name] = [(block.get("continuity") or {}).get(name) for block in blocks]
            return {
                "iterations": [block.get("iteration") for block in blocks],
                "time": [block.get("time") for block in blocks],
                "fields": fields,
                "continuity": continuity,
                "loading": loading,
            }

    def _history_from_host(self, record: RunRecord) -> bool:
        """Start reading a reopened run's history back from its host; True while that is going.

        **A reopened project holds no history**: `project_opened` loads the records only, and the
        blocks lived in memory, so every finished run said "history unavailable" after a reload. The
        agent's `monitors/residuals.jsonl` is complete and still on the host, so the first
        `run.residuals` for such a run reads it - **on a worker thread**, because this answers on
        the session thread, which is the server's event loop, and `job.read` against a slow host
        may take up to a minute. The reply says `loading: true` until it is in; a client asks again.
        """
        if not record.final or not record.job:
            return False
        with self._lock:
            if self._history.get(record.id, {}).get("blocks"):
                return False
            state = self._history_reads.get(record.id)
            if state is not None:
                return state == "loading"
            self._history_reads[record.id] = "loading"

        def work() -> None:
            from stokes.run.executors import _read_history
            from stokes.run.hosts import HostRegistry

            outcome = "failed"
            try:
                runner = HostRegistry.of(self._session).runner(record.host or "local")
                blocks = _read_history(runner, record.job)
                if blocks:
                    self.replace_history(record.id, blocks)
                    outcome = "done"
            except Exception:
                log.warning("could not read a run's history back", exc_info=True)
            with self._lock:
                if self._history_reads.get(record.id) == "loading":
                    self._history_reads[record.id] = outcome

        threading.Thread(target=work, name="stokes-run-history", daemon=True).start()
        return True

    # ------------------------------------------------------------------ writing

    def _record_folder(self, run_id: str) -> Path:
        """`runs/<id>`. **The id is validated first** (Opus review item 1).

        The id becomes a path segment, so `"../IMPORTANT"` wrote `record.json` into a sibling folder
        and a later `clear` **deleted that whole directory** - measured, not theorised. `fullmatch`
        rather than `match`, so a trailing newline cannot slip through, and the check is here rather
        than only at the command, because every caller of this method writes a path.
        """
        _check_run_id(run_id)
        return runs_dir(self._session) / run_id

    def _write(self, record: RunRecord) -> None:
        """Write the record atomically. **Called after every change**, so a crash loses at
        most one."""
        record.updated = _now()
        # **The folder the record was LOADED from wins** over one derived from the session's current
        # project. `project_opened` runs after `session.project_path` has changed, so a record being
        # closed out for the project being LEFT would otherwise be written into the incoming one
        # (Opus review item 11). `origin` is set when a record is LOADED, which is the case that
        # matters: a record added in this session stamps it in `add`.
        folder = record.origin or self._record_folder(record.id)
        try:
            # **One writer at a time, and the rename is retried.** Measured in the tutorial sweep
            # (2026-11-01, once the warning carried its error): `[WinError 5] Access is denied:
            # .record.json.tmp -> record.json` on nearly every run. `_write` runs after every
            # change, from the collect worker and from the event thread at once, and on Windows a
            # rename over a file another thread has open (a reader of `record.json`, or the other
            # writer's own rename) is refused rather than queued. The lock stops the writers
            # racing each other; the retry rides out a reader.
            with self._write_lock:
                folder.mkdir(parents=True, exist_ok=True)
                target = folder / "record.json"
                temp = folder / ".record.json.tmp"
                temp.write_text(record.encode(), encoding="utf-8")
                _replace_with_retries(temp, target)
        except OSError as exc:
            # **A run that cannot be written is still a run in memory.** Losing the record
            # file is bad;
            # refusing the run because of it would be worse, and the user would see a solve
            # that never
            # started for no visible reason.
            log.warning(
                "could not write the run record", extra=fields(run=record.id, error=str(exc))
            )

    def add(self, record: RunRecord) -> RunRecord:
        """Put a new record in, at the newest end, and write it.

        **The id is checked here too, not only in `run.start`** (Opus review item 1): the store is
        reachable from the journal, from `finish` and from tests, and one caller forgetting
        the check would reopen the same hole.
        """
        _check_run_id(record.id)
        if record.origin is None:
            record.origin = self._record_folder(record.id)
        # **`created`, stamped ONCE on the first write** (G-301 step 17). The field existed and
        # was never set by anybody, so `project_opened`'s `sort(key=...created)` sorted equal
        # empty strings - a no-op. The order fell back to the alphabetical folder walk, so a
        # reopened project listed its runs by ID: measured, `r_9e4f89ea` (the FIRST run) came
        # before `r_31f5e56f` (the second), and the Results panel named the older one because it
        # takes `runs[0]`.
        #
        # **Not `updated`**: that is the last touch and changes on every write, which would
        # reorder a project's history each time a run finished.
        if not record.created:
            record.created = datetime.now(UTC).isoformat(timespec="seconds")
        with self._lock:
            if record.id not in self._records:
                self._order.append(record.id)
            self._records[record.id] = record
            self._history.setdefault(record.id, _empty_history())
        self._write(record)
        return record

    def update(self, run_id: str, **changes: Any) -> RunRecord | None:
        """Change fields on a record and write it. Unknown names are ignored, as `from_dict`
        does.

        **A final state is final** (Opus review item 2). `finish` is the only thing that may
        set one, and this refuses to move a record back out of one. Without it the late-cancel
        race was real: `cancel_run` checks `found.final`, then makes a `job.cancel` call that
        may take up to 30 s, and a job that finished inside that window had `done` overwritten
        with `cancelling` - after which the record never became final again, `running()` kept
        returning it, and **no new solve could start**. Measured, and reproduced in the tests.

        The refusal is logged, because a caller trying to do this is a caller with a bug: silently
        ignoring it would hide the next one.
        """
        with self._lock:
            found = self._records.get(run_id)
            if found is None:
                return None
            wanted = changes.get("state")
            if found.final and wanted is not None and wanted != found.state:
                log.warning(
                    "refusing to move a final run out of its final state",
                    extra=fields(run=run_id, to=wanted, **{"from": found.state}),
                )
                changes = {k: v for k, v in changes.items() if k != "state"}
                if not changes:
                    return found
            for name, value in changes.items():
                if hasattr(found, name):
                    setattr(found, name, value)
            self._write(found)
            return found

    def append_residual(self, run_id: str, block: dict[str, Any]) -> None:
        """Add one completed block to the in-memory history.

        **Blocks, not parallel lists.** A per-field list indexed by position raised
        `IndexError` the first
        time a field was absent from a block, and fields ARE absent: `p` is solved twice per
        step, and a
        laminar run has no `k` to report at all. Keeping the block whole means a missing field is a
        missing key rather than a shifted index.

        **Capped, and the cap is the point**: a run left going for hours would otherwise grow
        without
        bound in a desktop process. Past the cap the agent's file is still complete, which is what
        `run.residuals` reads after a reload.
        """
        with self._lock:
            history = self._history.setdefault(run_id, _empty_history())
            blocks = history.setdefault("blocks", [])
            if len(blocks) >= MAX_HISTORY:
                return
            blocks.append(dict(block))

    def replace_history(self, run_id: str, blocks: Sequence[dict[str, Any]]) -> None:
        """Replace the in-memory history from the agent's file — **which is complete**.

        The event stream is rate-limited, so the file is the authority once a run is over.
        Reading it
        back is what makes `run.residuals` after a reload show the whole run rather than the
        part that
        survived the socket.
        """
        with self._lock:
            history = _empty_history()
            self._history[run_id] = history
        for block in blocks[:MAX_HISTORY]:
            self.append_residual(run_id, block)

    def clear(self, run_id: str) -> bool:
        """Forget a record and remove its folder, **and its cached fields** (T-216).

        The field artifact can be megabytes; a run the user removed must not leave one behind. The
        removal happens whatever the record's state, because a `run.clear` for a record this session
        never loaded still means "this run is gone".
        """
        with self._lock:
            known = run_id in self._records
            if known:
                del self._records[run_id]
                self._order.remove(run_id)
                self._history.pop(run_id, None)
            self._history_reads.pop(run_id, None)
        self._remove_fields(run_id)
        shutil.rmtree(self._record_folder(run_id), ignore_errors=True)
        return known

    # ------------------------------------------------------------------ reuse, one-at-a-time,
    # cancel

    def reusable(self, basis: str) -> RunRecord | None:
        """A finished run of this same basis, or None.

        **The newest, and only a run that WORKED**: `done` or `done_unconverged`. A failed or
        cancelled
        run must never be handed back as "already solved", and `done_unconverged` counts
        because the case
        is the same case — a user who wants it run again says `force=True`.
        """
        with self._lock:
            for run_id in reversed(self._order):
                found = self._records[run_id]
                if found.basis == basis and found.successful:
                    return found
        return None

    def newest_result(self) -> RunRecord | None:
        """The newest run that solved something and has a job to read: what Resume continues."""
        with self._lock:
            for run_id in reversed(self._order):
                found = self._records[run_id]
                if found.successful and found.job and found.kind != "mesh":
                    return found
        return None

    def running(self) -> RunRecord | None:
        """The run in flight, if there is one. "One run at a time" is a rule this answers."""
        with self._lock:
            for run_id in reversed(self._order):
                found = self._records[run_id]
                if found.state in ("queued", "running", "cancelling"):
                    return found
        return None

    def cancel_run(self, run_id: str | None) -> bool:
        """Ask a run to stop, and mark it `cancelling`.

        **The record moves to `cancelling` only if the call went out**, so a cancel for a run
        whose job
        has already gone does not leave it looking busy for ever. Returns whether there was a run to
        cancel.
        """
        found = self.record(run_id)
        if found is None or found.final:
            return False
        if not found.job:
            # A run that never reached the host has nothing to cancel; it is simply over.
            self.update(found.id, state="cancelled")
            return True
        from stokes.run.hosts import HostRegistry

        try:
            runner = HostRegistry.of(self._session).runner(found.host or "local")
            runner.call("job.cancel", job=found.job, timeout=30.0)
        except Exception as exc:
            log.warning("the cancel call failed", extra=fields(run=found.id, error=str(exc)))
            return False
        # **Only while the run is still going.** This check is HERE and not only at the top,
        # because the `job.cancel` call above can take up to 30 s and the job may have finished
        # inside that window (Opus review item 2). `update` refuses the change anyway; asking
        # only when it is still true keeps the answer honest - a run that finished while we were
        # cancelling was not cancelled, and `return True` would say it was.
        current = self.record(found.id)
        if current is None or current.final:
            return False
        self.update(found.id, state="cancelling")
        return True

    def stop_run(self, run_id: str | None) -> str:
        """Stop a run so that it KEEPS what it solved: ``"stopping"``, ``"cancelled"`` or ``""``.

        A running solve is asked through the agent's `job.stop`: the solver writes the iteration
        it is on and ends cleanly, so the run finishes as a result (``done_unconverged``, with
        ``stopped`` set) that can be plotted and resumed. A run the agent cannot stop that way - a
        queued one, one from an agent that predates `job.stop`, one whose solver is not running yet
        - falls back to `cancel_run`, which is what Stop did before. ``""`` means nothing to stop.
        """
        found = self.record(run_id)
        if found is None or found.final:
            return ""
        if found.job and found.state == "running":
            from stokes.run.hosts import HostRegistry

            try:
                runner = HostRegistry.of(self._session).runner(found.host or "local")
                answer = runner.call("job.stop", job=found.job, timeout=30.0)
            except Exception as exc:
                log.warning("the stop call failed", extra=fields(run=found.id, error=str(exc)))
                answer = {}
            if isinstance(answer, dict) and answer.get("stopping"):
                current = self.record(found.id)
                if current is not None and not current.final:
                    self.update(found.id, stopped=True)
                return "stopping"
        return "cancelled" if self.cancel_run(found.id) else ""

    # ------------------------------------------------------------------ the executor's two calls

    def bind_job(self, run_id: str, job: str) -> None:
        """Record the agent's job id on the run.

        **Every event is matched against this**, so it has to be written before the listener can see
        anything — a listener that started before the id was recorded would ignore the run's
        own first
        events, and the run would sit at `queued` while the solver worked.
        """
        self.update(run_id, job=str(job))

    def finish(
        self,
        run_id: str,
        *,
        state: str,
        report: dict[str, Any],
        history: Sequence[dict[str, Any]],
        monitors: dict[str, Any],
        status: dict[str, Any],
    ) -> None:
        """End a run: the state, the numbers, and the history from the agent's own file.

        **`done` vs `done_unconverged` is the report's call, not the job's.** A job that
        exited 0 did not
        necessarily converge: many real runs stop at the iteration limit with residuals still
        falling, and
        reporting that as `done` would tell a user something the solver never said.

        **The file replaces the in-memory history.** The event stream is rate-limited, so what
        arrived on
        the wire is a sample; `monitors/residuals.jsonl` has every block, and reading it back
        is what makes
        a finished run's chart whole.
        """
        from datetime import datetime

        found = self.record(run_id)
        if found is None:
            return
        if history:
            self.replace_history(run_id, history)
        converged = bool(report.get("converged"))
        iterations = int(report.get("iterations") or 0)
        last = dict(report.get("last_residuals") or {})
        if not last:
            # Fall back to the last block the history has, which is the same data from the
            # other side.
            with self._lock:
                blocks = self._history.get(run_id, {}).get("blocks") or []
            if blocks:
                last = dict(blocks[-1].get("residuals") or {})
                iterations = iterations or int(blocks[-1].get("iteration") or 0)
        final = state
        if state == "done":
            # **An imported case that reached its `End` is done, not "unconverged".** The report's
            # `converged` comes from `SIMPLE solution converged in N iterations`, which a transient
            # case (cavity, damBreak) or one without `residualControl` never prints; the tutorial
            # sweep (2026-11-01) reported every such case as `done_unconverged` and the strip said
            # "Stopped at 2000 iterations" of a run that finished exactly as written. For a case the
            # success criterion is the solver's own `End`; a CAD model's run keeps the residual
            # criterion, which is the one its solver settings ask for.
            ended = found.engine == EXTERNAL_ENGINE and bool(report.get("ended"))
            # A mesh-only run (`kind: mesh`, the Mesh step's Generate) solves nothing: its job
            # finishing is the whole success criterion.
            meshed = found.kind == "mesh"
            final = "done" if converged or ended or meshed else "done_unconverged"
            # **A SECOND finish must not downgrade a converged run.** An empty report means the read
            # raced or failed, not that the solver failed to converge — and a run already
            # recorded as
            # `done` with `converged: True` is evidence that it did. Without this, a late duplicate
            # collection overwrote the answer with `done_unconverged`, which is exactly the symptom
            # that cost two rounds.
            if not report and found.converged:
                log.warning(
                    "a finish with no report arrived after a converged result; keeping the result",
                    extra=fields(run=run_id),
                )
                return
        seconds = _seconds_from(status, report)
        # **The fields are fetched BEFORE the state goes terminal** (T-216), and the ordering is
        # load-bearing in both directions.
        #
        # *Before*, because `record.final` is the promise that everything about the run has landed:
        # `settle` returns the instant the record is final, so `run.start(wait=True)` and a
        # `run.status` polling loop both stop there. Fetching afterwards made `run.fields` answer
        # `surfaces: "missing"` for a run that had just converged, which the engine test caught as a
        # SKIPPED check 4 — a race that only shows when the fetch is slower than the caller.
        #
        # `finish` is the one seam every production path goes through, and it runs on the collect
        # worker thread, so the round trip never touches the session thread.
        #
        # **An UNCONVERGED run gets its fields like a converged one.** This asked for
        # `final == "done"` only, so a run that reached its iteration cap solved, wrote its
        # time directories, and then answered `run.fields` with `surfaces: "missing"` - no
        # plot and no explanation. Measured on the demo pipe: 300 time steps, residuals down
        # to 1e-4, artifacts on the host, and `record.fields == []`. Fetching by hand gave
        # 114 kB containing `U`, `U_mag`, `p` and a slice, so **the fields were there and
        # nothing had asked for them.**
        #
        # An unconverged run is an ordinary result, not a broken one - the residuals and
        # monitors are exactly what a user reads when a solve did not converge, and a plot of
        # the last iteration is what they look at next. Withholding it made the pipeline look
        # broken at the moment it is most useful.
        #
        # A run that genuinely did not work is still not asked: `failed` and `cancelled` have no
        # solved case to extract from, so the request would only add a refused round trip.
        failure = self.fetch_fields(run_id) if final in FIELD_STATES else ""
        if final in FIELD_STATES and found.engine == EXTERNAL_ENGINE:
            self._adopt_case_mesh(found)
        self.update(
            run_id,
            state=final,
            converged=converged,
            iterations=iterations,
            last_residuals=last,
            monitors=monitors,
            timings={"seconds": seconds} if seconds is not None else {},
            # **A fetch failure does not change the run's state, but it does get reported.** The
            # solve worked and its fields exist on the host, so the terminal state stays `done`; the
            # message goes in `error` and survives this write because `_error_text` is empty for a
            # run that worked.
            error=failure or _error_text(status, final),
            updated=datetime.now(UTC).isoformat(timespec="seconds"),
        )
        self._emit(
            "run.finished",
            {
                "run_id": run_id,
                "state": final,
                "seconds": seconds,
                "converged": converged,
            },
        )

    # ------------------------------------------------------------------ names

    def name_for(self, project: Any) -> str:
        """A unique run name in a project: the project's own name, with a numeric suffix.

        **`"duct"`, then `"duct 2"`** — a list of runs all called `duct` tells a user nothing
        about which
        is which, and the case name is the one thing every run has.
        """
        base = getattr(project, "name", "") or "run"
        taken = {record.name for record in self._records.values()}
        if base not in taken:
            return base
        index = 2
        while f"{base} {index}" in taken:
            index += 1
        return f"{base} {index}"

    def new_id(self) -> str:
        """A fresh run id, in the project's own id space."""
        del self
        return new_id("r")

    # ------------------------------------------------------------------ the fields (T-216)

    def fields_path(self, run_id: str, base: Path | None = None) -> Path:
        """`<cache>/results/<run id>.stkfld`, refused unless it really is inside that folder.

        **The id becomes part of a path**, so it must match `ID_RE` first, and neither the folder
        nor the file may be a link: a link would take a read or a write somewhere else. This is the
        same rule `MeshService._surface_path` follows, for the same reason — the cache is found
        through the session's current project, and a project folder is not ours to trust.
        """
        from stokes.model.ids import ID_RE

        if not ID_RE.match(run_id):
            raise MeshInvalid(f"{run_id!r} is not a run id")
        folder = self._cache_base() / RESULTS_FOLDER
        path = folder / f"{run_id}{RESULTS_SUFFIX}"
        resolved_parent = path.resolve().parent
        if folder.is_symlink() or path.is_symlink() or resolved_parent != folder.resolve():
            raise MeshInvalid(f"the fields of {run_id} are not inside the results cache")
        return path

    def slices_folder(self, run_id: str) -> Path:
        """`<cache>/results/slices/<run id>`. **The same id and symlink rules as `fields_path`.**

        A second path builder rather than a parameter on the first, because the checks are the
        point: an id that is not an id, or a folder that is a link, must be refused *before*
        anything is read or written, and sharing the builder would make it easy to add a caller
        that skips them.
        """
        from stokes.model.ids import ID_RE

        if not ID_RE.match(run_id):
            raise MeshInvalid(f"{run_id!r} is not a run id")
        results = self._cache_base() / RESULTS_FOLDER
        folder = results / SLICES_FOLDER / run_id
        if results.is_symlink() or folder.is_symlink():
            raise MeshInvalid(f"the slices of {run_id} are not inside the results cache")
        if folder.resolve().parent.parent != results.resolve():
            raise MeshInvalid(f"the slices of {run_id} are not inside the results cache")
        return folder

    def slice_path(self, run_id: str, plane: dict[str, Any]) -> Path:
        """The cached cut for one `(run, plane)`. **The plane's canonical key names the file.**"""
        from stokes.run.planes import plane_key

        return self.slices_folder(run_id) / f"{plane_key(plane)}{SLICE_SUFFIX}"

    def _trim_slices(self, run_id: str, suffix: str = SLICE_SUFFIX) -> builtins.list[str]:
        """Keep the newest `MAX_CACHED_PLANES` cuts of one kind (by `suffix`), deleting the least
        recently used. Returns what went.

        **Least recently used, by mtime**, which is why a cache hit touches the file: with sixteen
        planes to keep and a client that sweeps a slider, "oldest written" would evict the plane a
        user keeps returning to and keep the ones they tried once.
        """
        from stokes.run.planes import MAX_CACHED_PLANES

        folder = self.slices_folder(run_id)
        try:
            entries = [p for p in folder.glob(f"*{suffix}") if p.is_file()]
        except OSError:
            return []
        if len(entries) <= MAX_CACHED_PLANES:
            return []
        entries.sort(key=lambda path: path.stat().st_mtime)
        removed: list[str] = []
        for path in entries[: len(entries) - MAX_CACHED_PLANES]:
            try:
                path.unlink()
                removed.append(path.name)
            except OSError as exc:
                log.warning(
                    "could not trim a cached slice",
                    extra=fields(path=str(path), error=str(exc)),
                )
        return removed

    def _trim_slice_errors(self, run_id: str) -> None:
        """Keep the error map from outgrowing the cache it describes (Opus recheck 4d, round 3 6c).

        **An error is only meaningful while its plane might still be asked for.** The map is written
        by every failed cut and cleared only when that same plane is cut again, so a drag across a
        model that fails - a host that is gone, a plane off the mesh - left an entry for **every**
        distinct plane, for the life of the store.

        **Two bounds, because one is not enough** (Opus round 3, item 6c). The per-run bound
        matches `MAX_CACHED_PLANES`, the number the plane cache keeps: a reason for a plane whose
        artifact has been trimmed away can never be read, since a request for it cuts afresh and
        **that** is what records a reason. But that alone **still grows with the number of runs**,
        and a long session opens many - so the whole map has its own ceiling.

        **Ungarbled**: my previous docstring had a comment line spliced into the middle of a
        sentence, which Opus found and I had not.

        The caller holds `self._lock`, which is why this does not take it.
        """
        from stokes.run.planes import MAX_CACHED_PLANES

        # **Per run**: the newest `MAX_CACHED_PLANES` reasons for the run that just recorded one.
        mine = [key for key in self._slice_errors if key[0] == run_id]
        if len(mine) > MAX_CACHED_PLANES:
            # **Insertion order, not mtime.** These are reasons rather than files, so the order a
            # dict keeps is the order they were written - and the newest are the ones worth keeping,
            # because they describe the planes a client is asking about right now.
            for key in mine[: len(mine) - MAX_CACHED_PLANES]:
                self._slice_errors.pop(key, None)

        # **And globally**, because a session can open run after run and every one of them keeps its
        # because they describe the planes a client is asking about right now.
        # runs, which is the same defect one level up.
        if len(self._slice_errors) > MAX_SLICE_ERRORS:
            ordered = list(self._slice_errors)
            for key in ordered[: len(ordered) - MAX_SLICE_ERRORS]:
                self._slice_errors.pop(key, None)

    def _cache_base(self) -> Path:
        """The project's cache folder, or the session's scratch folder while it has none.

        The same choice `MeshService` makes, through the same helper, so a run's fields sit beside
        the mesh it was solved on and travel with the project on Save as.
        """
        from stokes.geometry.shapes import cache_dir

        folder = self._session.project_path
        if folder is not None:
            return cache_dir(folder)
        from stokes.geometry.store import GeometryStore

        return GeometryStore.of(self._session).shape_dir()

    def _adopt_case_mesh(self, record: RunRecord) -> None:
        """An imported case's run brings its own mesh to the viewport (`adopt_job_mesh`).

        **On the collect worker, before the state goes terminal**, like the fields: a client that
        sees `done` should find the patches the fields colour. Never raises: a run whose mesh could
        not be drawn has still run.
        """
        from stokes.mesh.service import MeshService

        project = self._session.project
        node = project.find(record.settings_id) if project is not None else None
        recipe = (node.params.get("recipe") or {}) if node is not None else {}
        steps = recipe.get("steps") if isinstance(recipe, dict) else None
        utilities = {str(step.get("utility")) for step in steps or [] if isinstance(step, dict)}
        mesher = "snappy" if "snappyHexMesh" in utilities else "blockmesh"
        if "cartesianMesh" in utilities:
            mesher = "polyhex" if "polyDualMesh" in utilities else "cfmesh"
        elif "tetMesh" in utilities:
            mesher = "cfmesh_tet"
        elif "pMesh" in utilities:
            mesher = "cfmesh_poly"
        try:
            MeshService.of(self._session).adopt_job_mesh(
                record.job,
                record.host or "local",
                str(node.name) if node is not None else "case",
                mesher,
                record.settings_id,
            )
        except Exception:
            log.warning("an imported case's mesh could not be shown", exc_info=True)

    def fetch_fields(self, run_id: str, time: float | None = None) -> str:
        """Ask the host for the run's fields and keep them in the cache. **A worker thread.**

        ``time`` is a written time to read (`run.time`); None reads the latest, as a finished run
        always has.

        Called from `finish`, on the executor's collect worker, **before** the terminal state is
        written — so `record.final` really does mean "everything about this run has landed" and a
        reader that sees `done` also sees `record.fields`.

        Everything it needs is on the record: the job, the mesh job whose surfaces artifact is the
        authority on patch order, and the host. The slice plane is the settings' `slice_origin`
        (empty meaning the mesh's bbox centre, which the agent computes) and `slice_normal`.

        **A failure never changes the run's state** (the brief's rule 5): the solve worked, its
        fields exist on the host, and reporting the run as failed because a *fetch* failed would
        tell the user something untrue about their solve. Returns the error text for `finish` to
        write — `""` when it worked — because `finish` owns the record's terminal write and doing
        it twice would let one clobber the other. `fields` stays empty in that case,
        `run.failed_extract` is emitted, and the unusable file is removed.
        """
        from stokes.run.artifacts import fetch_artifact
        from stokes.run.hosts import HostRegistry

        record = self.record(run_id)
        if record is None or not record.job:
            return ""
        target: Path | None = None
        try:
            runner = HostRegistry.of(self._session).runner(record.host or "local")
            extra: dict[str, Any] = {} if time is None else {"time": float(time)}
            summary = runner.call(
                "extract.fields",
                job=record.job,
                surfaces_job=record.mesh_job or record.job,
                slice=self.slice_plane(),
                timeout=EXTRACT_FIELDS_TIMEOUT_S,
                **extra,
            )
            if not isinstance(summary, dict):
                raise RunError("extract.fields returned no summary")
            path = self.fields_path(run_id)
            # **Fetched beside the file, then swapped in under the view lock** (T-310): a client
            # reading the run's fields keeps the old file mapped, and on Windows a mapped file
            # cannot be replaced. `_load_fields_file` maps under the same lock, so between the
            # view's release and the replace no reader can map the old file again.
            incoming = path.with_name(path.name + ".next")
            fetch_artifact(
                runner,
                record.job,
                str(summary["artifact"]),
                incoming,
                str(summary["sha256"]),
                int(summary["size"]),
            )
            try:
                with self._lock:
                    cached = self._field_views.pop(str(path), None)
                    if cached is not None:
                        self._close_view(cached[1])
                    incoming.replace(path)
            except OSError:
                # **The old file is still whole**, so it is kept: `target` is set only once the
                # new one is in place, and the handler below removes the cache only then.
                with contextlib.suppress(OSError):
                    incoming.unlink()
                raise
            target = path
            # **Read back what was stored, not what the host said.** The header is the authority on
            # which fields exist and what they range over, and reading it is also the first proof
            # that the file that landed is one this build can serve.
            data, header = self._load_fields(run_id)
            del data
            self._apply_fields(run_id, header, summary)
            return ""
        except Exception as exc:
            code = str(getattr(exc, "code", "") or "run.extract")
            message = str(exc) or type(exc).__name__
            log.warning(
                "the fields could not be fetched",
                extra=fields(run=run_id, job=record.job, error=message[:200]),
            )
            if target is not None or isinstance(exc, MeshFormatError):
                self._remove_fields(run_id)
            self.update(run_id, fields=[], slice=False)
            self._emit(
                "run.failed_extract",
                {"run_id": run_id, "code": code, "message": message},
            )
            return f"run.extract: {message}"

    def slice_plane(self) -> dict[str, Any] | None:
        """The slice plane from the current settings, or None for the agent's default cut.

        **An empty `slice_origin` sends `null`** and lets the agent nudge the mesh's bbox centre off
        the grid face (T-215 correction 1): the centre of a block-meshed duct is exactly a face
        plane, and a cut through a face's vertices gives duplicated or zero-area polygons. Sending
        zeros instead would claim the plane passes through the model's own origin, which it does
        not. The `normal` is meaningful either way — `[0, 0, 1]` and the mesh's own long axis are
        different cuts — but an all-zero one is not a plane, so it means the default too.
        """
        from stokes.engines.model import SETTINGS_PATH

        project = self._session.project
        settings = project.find(SETTINGS_PATH) if project is not None else None
        direction = _vector(settings.params.get("slice_normal") if settings is not None else None)
        origin = _vector(settings.params.get("slice_origin") if settings is not None else None)
        if direction is None or all(component == 0.0 for component in direction):
            return None
        if origin is None:
            return None
        return {"origin": origin, "normal": direction}

    def _apply_fields(self, run_id: str, header: dict[str, Any], summary: dict[str, Any]) -> None:
        """Record which fields exist, whether there is a slice, and the time. **Session thread.**

        `fields` is every field present on **at least one patch**, in the artifact's own order: a
        field one patch does not have is not missing from the run, it is missing from that patch,
        and a legend that dropped it would hide the field a user asked for.
        """
        names: list[str] = []
        patch_fields = self._ranges(header)
        for patch in header.get("patches") or []:
            for name in patch.get("fields") or {}:
                if name not in names:
                    names.append(str(name))
        # **The slice's fields are named too** — a field that exists only on the cut is a field the
        # run has, and a legend that hid it would drop what the user is looking at.
        slice_entry: Any = header.get("slice")
        has_slice = self._has_slice(header)
        if isinstance(slice_entry, dict):
            for name in slice_entry.get("fields") or {}:
                if name not in names:
                    names.append(str(name))
        time_value = summary.get("time", header.get("time", 0.0))
        self.update(run_id, fields=names, slice=has_slice, error="")
        with self._lock:
            self._field_ranges[run_id] = patch_fields
            self._field_time[run_id] = float(time_value or 0.0)
        self._emit("run.changed", {"run_id": run_id, "fields": names})

    def _ranges(self, header: dict[str, Any]) -> dict[str, tuple[float, float]]:
        """`{field: (min, max)}` over **every patch and the slice**, from the header's own entries.

        The agent measured these over the stored array — for `U` over the magnitude — so they are
        read rather than recomputed: two places doing that arithmetic is how a legend comes to
        disagree with the colours under it.
        """
        return self._spans(header, "min", "max")

    def _percentiles(self, header: dict[str, Any]) -> dict[str, tuple[float, float]]:
        """`{field: (p01, p99)}` the same way, from the entries that carry them (an agent from
        before the percentiles existed wrote none, and such a field is simply absent here)."""
        return self._spans(header, "p01", "p99")

    @staticmethod
    def _spans(
        header: dict[str, Any], low_key: str, high_key: str
    ) -> dict[str, tuple[float, float]]:
        found: dict[str, tuple[float, float]] = {}
        entries = [*(header.get("patches") or [])]
        if isinstance(header.get("slice"), dict):
            entries.append(header["slice"])
        for entry in entries:
            for name, field in (entry.get("fields") or {}).items():
                try:
                    low, high = float(field[low_key]), float(field[high_key])
                except (KeyError, TypeError, ValueError):
                    continue
                if name in found:
                    found[name] = (min(found[name][0], low), max(found[name][1], high))
                else:
                    found[name] = (low, high)
        return found

    @staticmethod
    def _volume_ranges(header: dict[str, Any]) -> dict[str, tuple[float, float]]:
        """`{field: (min, max)}` over the whole internal mesh, from the header's `volume` table.

        The agent measured it over every cell and point when it extracted the fields (ADR-0026
        amendment 2), so the desktop reads it and never opens the case. A cache written before
        the table existed has none, and an entry that is not two ordered finite numbers is skipped.
        """
        table = header.get("volume")
        found: dict[str, tuple[float, float]] = {}
        if not isinstance(table, dict):
            return found
        for name, entry in table.items():
            try:
                low, high = float(entry["min"]), float(entry["max"])
            except (KeyError, TypeError, ValueError):
                continue
            if math.isfinite(low) and math.isfinite(high) and low <= high:
                found[str(name)] = (low, high)
        return found

    def _remove_fields(self, run_id: str) -> None:
        """Delete a run's cache file, its cached planes, and any part left by a failed fetch.

        **The whole slice folder goes**, not just the one plane that happens to be loaded:
        `run.clear` means the run is gone, and sixteen cached cuts of a deleted run are exactly
        what the user asked to be rid of (the contract says so). **Never raises** - a cleanup
        that failed loudly would leave the record removed and the caller thinking it failed.
        """
        try:
            path = self.fields_path(run_id)
        except MeshInvalid:
            return
        # **Release the mapped view before unlinking the file.** A cached `mmap` keeps the file
        # open on Windows, so an unlink first fails with `WinError 32` and leaves the cache file
        # behind after `run.clear`. This is the same rule `_remove_slices` follows for planes.
        with self._lock:
            cached = self._field_views.pop(str(path), None)
            self._field_ranges.pop(run_id, None)
        if cached is not None:
            self._close_view(cached[1])
        with contextlib.suppress(OSError):
            path.unlink()
        with contextlib.suppress(OSError):
            path.with_name(path.name + ".part").unlink()
        self._remove_slices(run_id)

    def _remove_slices(self, run_id: str) -> None:
        """Delete a run's cached planes and release their views. **Never raises.**"""
        try:
            folder = self.slices_folder(run_id)
        except MeshInvalid:
            return
        # **The views are released before the files go.** A `memoryview` holds an `mmap` open,
        # and on Windows an open mapping makes the unlink fail with `WinError 32` -
        # claude-win2 hit exactly that with the `.stkfld` file. Releasing first is what makes
        # the delete work on both platforms.
        prefix = str(folder) + os.sep
        with self._lock:
            stale = [key for key in self._field_views if key.startswith(prefix)]
            views = [self._field_views.pop(key) for key in stale]
        for _stamp, view, _header in views:
            self._close_view(view)
        shutil.rmtree(folder, ignore_errors=True)

    def _load_fields(self, run_id: str) -> tuple[memoryview, dict[str, Any]]:
        """The run's own cache file, through the LRU below."""
        return self._load_fields_file(self.fields_path(run_id), run_id)

    def _load_fields_file(self, path: Path, run_id: str) -> tuple[memoryview, dict[str, Any]]:
        """An artifact on disk as a `memoryview` and its header, through a small LRU.

        **The file stays on disk and the view is cached**, because a route is asked for one field at
        a time and re-reading a megabyte per request would be the whole cost of a slice. The stamp
        is the size and mtime, so a file replaced under us is read again rather than served stale.

        `run_id` is only for the log lines: the slices cache holds many files per run, so the caller
        says which run the file belongs to rather than the path being derived from the id here.
        """
        info = path.stat()
        stamp = (info.st_size, info.st_mtime_ns)
        key = str(path)
        with self._lock:
            found = self._field_views.get(key)
            if found is not None and found[0] == stamp:
                self._field_views.move_to_end(key)
                return found[1], found[2]
        # **`mmap`, not `read_bytes`** (Opus review item 14). The whole file was read into memory -
        # up to the 2 GiB download cap, times `FIELD_LRU` - and a 2 GiB allocation is what the
        # process cannot afford. An anonymous mapping makes the file readable without materialising
        # it: the pages arrive on access, the kernel can evict them, and `read_header`
        # already accepts a `memoryview`, which `mmap` supports.
        #
        # **One loader per path, so two misses cannot read twice.** The read used to happen outside
        # the lock, so two concurrent requests for one run both opened and read the file - one to
        # disk, one wasted, and both allocated. The lock is held across the load now; it is a
        # `mmap`, which is a syscall rather than a copy, so holding it costs nothing the
        # readers below care about.
        with self._lock:
            found = self._field_views.get(key)
            if found is not None and found[0] == stamp:
                self._field_views.move_to_end(key)
                return found[1], found[2]
            loaded = self._field_views.get(key)
            if loaded is not None and loaded[0] != stamp:
                # A file replaced under us: drop the stale mapping before opening the new one.
                self._close_view(loaded[1])
                del self._field_views[key]
            try:
                handle = path.open("rb")
                mapping = mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ)
                handle.close()
            except (OSError, ValueError) as exc:
                raise MeshFormatError(f"cannot map {path}: {exc}") from exc
            view = memoryview(mapping)
            try:
                header = read_header(view)
            except BaseException:
                # **A mapping that does not hold an artifact is released at once.** Leaking one per
                # bad file would put the process back where it started.
                view.release()
                mapping.close()
                raise
            self._field_views[key] = (stamp, view, header)
            self._field_views.move_to_end(key)
            while len(self._field_views) > FIELD_LRU:
                # `popitem` gives `(key, (stamp, view, header))`, so the view is `[1][1]`.
                evicted = self._field_views.popitem(last=False)
                self._close_view(evicted[1][1])
        return view, header

    def _close_view(self, view: memoryview) -> None:
        """Release a cached view and close the mapping under it. **Never raises.**

        A `memoryview` holds the mapping open, so closing without releasing raises `BufferError`.
        An eviction that raises would leave the LRU over its bound, which is the thing it exists to
        prevent - so this is best-effort and logged.
        """
        try:
            mapping = view.obj
            view.release()
            if isinstance(mapping, mmap.mmap):
                mapping.close()
        except (BufferError, ValueError, OSError) as exc:
            log.warning("could not release a mapped field file", extra=fields(error=str(exc)[:120]))

    def _run_fields(self, run_id: str) -> tuple[memoryview, dict[str, Any]] | None:
        """The cache file of a run whose record says it has fields; None when unavailable."""
        record = self.record(run_id)
        if record is None:
            return None
        try:
            return self._load_fields(run_id)
        except (OSError, MeshFormatError, MeshInvalid):
            # **A missing cache file is an answer, not an error**: a reopened project whose cache
            # was cleaned up serves `surfaces: "missing"` and 404s, which is what the client shows.
            return None
        except Exception:
            log.warning("a field file is unreadable", extra=fields(run=run_id))
            return None

    def _patch_index(self, header: dict[str, Any], target: str) -> int | None:
        """Map a `mesh_patch` node id, or the word `slice`, to an artifact patch index.

        **By NAME, falling back to the node's `index`** (the brief's rule): a rename in the tree
        must not silently serve a different patch's array, and the index is what the agent wrote
        when the surfaces artifact listed a patch under a name the node does not carry.
        """
        if target == SLICE:
            # **The wire word maps to the internal index**, which is negative so it can never be
            # mistaken for a patch position. Returning the string here was a real bug: the caller
            # compares it against patch positions and the comparison raised.
            entry = header.get("slice")
            return SLICE_INDEX if isinstance(entry, dict) else None
        project = self._session.project
        node = project.find(target) if project is not None else None
        if node is None or node.id != target or node.type != "mesh_patch":
            return None
        patches = header.get("patches") or []
        wanted = str(node.name or "")
        for position, patch in enumerate(patches):
            if str(patch.get("name") or "") == wanted:
                return position
        raw = node.params.get("index")
        if type(raw) is int and 0 <= raw < len(patches):
            return raw
        return None

    def field_payload_for(
        self, run_id: str, target: str, field: str, plane: dict[str, Any] | None = None
    ) -> bytes | None:
        """One `(patch, field)` as the client codec, or None when it cannot be served.

        **None covers every "not here" answer**: an unknown run, a missing cache file, an unknown
        patch node, and a patch the artifact does not have. The route turns that into a 404 — the
        id was well formed, the thing simply is not there. An **unknown field name** is different
        and is raised, because the client asked for something the artifact never had and a 400 says
        so rather than a 404 that reads as "this run has no fields".

        **The payload's `min`/`max` are the RUN'S global range, not this patch's** (T-218,
        2026-10-15). A legend shared across patches needs one range, so the codec carries the global
        ends — and that is visible: `walls?field=U_mag` reports `max 0.157644` while every
        value in the
        payload is `0`, because the walls are no-slip and the fastest flow is elsewhere. **Do
        not scale
        a per-patch colour by these**; the same global range is on `run.fields` per field, and a
        per-patch range would be a new pair of keys. `field_payload`'s caller is the only place that
        decides this, and this docstring is where a consumer is warned.
        """
        from stokes.run.fields import field_payload

        # **The plane selects the artifact; the field names a point set inside it.** A cut that has
        # been cached is the same shape as the run's own, so everything below is unchanged.
        loaded = (
            self._run_fields(run_id) if plane is None else self.slice_artifact_for(run_id, plane)
        )
        if loaded is None:
            return None
        data, header = loaded
        index = self._patch_index(header, target)
        if index is None:
            return None
        entry = (
            header["slice"].get("fields") or {}
            if index == SLICE_INDEX
            else (header["patches"][index].get("fields") or {})
        )
        if field not in entry:
            raise RunFieldsInvalid(f"no field {field!r} on this patch")
        values = field_array(data, header, index, field)
        low, high = self._field_range(header, field)
        units = (header.get("units") or {}).get(field, "")
        return field_payload(values, field, str(units), low, high)

    def _field_range(self, header: dict[str, Any], field: str) -> tuple[float, float]:
        """The `(min, max)` of one field over every patch and the slice, from the header.

        The same arithmetic `_ranges` does for all of them, asked about one; kept as one expression
        of one rule so `run.fields` and a served payload cannot disagree about a legend's ends.
        """
        return self._ranges(header).get(field, (0.0, 0.0))

    # --------------------------------------------------------------- the cached planes

    def slice_artifact_for(
        self, run_id: str, plane: dict[str, Any] | None
    ) -> tuple[memoryview, dict[str, Any]] | None:
        """The cut artifact for one `(run, plane)`, fetching and caching it when it is not held.

        **`plane=None` is the run's own artifact**, which is what every client sent before this
        existed: the `.stkfld` already carries one default mid-plane cut, so a request with no
        `plane` must not compute anything. That is also what keeps the existing tests and routes
        working unchanged.

        **A named plane comes from the cache, or is asked for once.** The lookup is
        `slices/<run>/<planehash>.stkslice`; a hit is touched so the LRU keeps what is being used,
        and a miss asks the host to cut it. The cut is written to a temporary name and renamed, so a
        reader never sees a half-written artifact, and the LRU is trimmed after the write.

        None means "cannot serve": an unknown run, no fields at all, or a plane that is not held and
        whose host cannot cut it. The route decides the status code from which of those it was, via
        `slice_state`.
        """
        from stokes.run.planes import plane_from_header, plane_matches

        if plane is None:
            return self._run_fields(run_id)
        record = self.record(run_id)
        if record is None:
            return None
        path = self.slice_path(run_id, plane)
        loaded = self._load_slice_file(run_id, path)
        if loaded is not None:
            data, header = loaded
            stored = plane_from_header(header)
            # **The key is the identity, but the header is the truth.** A file whose name and
            # contents disagree is not one to serve: it would answer a different plane's question.
            if stored is not None and plane_matches(plane, stored):
                return data, header
            log.warning("a cached slice does not match its name", extra=fields(run=run_id))
        return self._cut_and_cache(run_id, plane, path)

    def _load_slice_file(self, run_id: str, path: Path) -> tuple[memoryview, dict[str, Any]] | None:
        """A cached plane's artifact, touching it so the LRU knows it was used."""
        try:
            if not path.is_file():
                return None
            data, header = self._load_fields_file(path, run_id)
        except (OSError, MeshFormatError, MeshInvalid):
            return None
        with contextlib.suppress(OSError):
            os.utime(path, None)
        return data, header

    def _cut_and_cache(
        self, run_id: str, plane: dict[str, Any], path: Path
    ) -> tuple[memoryview, dict[str, Any]] | None:
        """Cut this plane once and cache it: a plane's `_CutRequest` through `_cut_shared`."""
        from stokes.run.planes import plane_key

        request = _CutRequest("slice.cut", {"plane": plane}, plane_key(plane))
        return self._cut_shared(run_id, path, request)

    def _cut_shared(
        self, run_id: str, path: Path, request: _CutRequest
    ) -> tuple[memoryview, dict[str, Any]] | None:
        """Cut once, however many callers ask for it at the same time. **Planes and iso-surfaces.**

        Both kinds share this path: the slots, the queue caps, the in-flight map (keyed by the
        cache path, which differs by kind, field and value) and the error map (keyed by
        `request.error_key`).

        **One computation per plane.** A client that debounces a drag still fires several requests
        for the same plane; without this the first cut is paid for repeatedly and each one reads
        the solved case again. The first caller cuts, the others wait on an `Event` and then read
        the file it wrote, so a request arriving mid-cut gets the same answer for the price of a
        read.
        """
        from stokes.run.artifacts import fetch_artifact
        from stokes.run.hosts import HostRegistry

        record = self.record(run_id)
        if record is None or not record.job:
            return None
        # **Cleared at the start of the cut, which is what the docstring always claimed.** A retry
        # must not inherit the previous attempt's failure: without this, a plane that failed once
        # and then succeeds would still answer `no_host`, and a user who retried would be told the
        # host was down for a problem that had gone away.
        with self._lock:
            self._slice_errors.pop((run_id, request.error_key), None)
        key = str(path)
        ahead: threading.Event | None
        mine = threading.Event()
        with self._lock:
            ahead = self._cut_waiting.get(key)
            if ahead is None:
                # **The queue is capped before anything is added to it** (Opus M4). A drag asks for
                # many planes; each queued one costs a thread and a wait, and a hung host means none
                # of them ever finishes. Refusing early with a `Retry-After` is the honest answer.
                queued_run = sum(1 for _run, _key in self._cut_ownership if _run == run_id)
                if queued_run >= MAX_QUEUED_PLANES_PER_RUN:
                    raise SliceBusy(
                        f"this run already has {queued_run} plane cuts queued; try again shortly"
                    )
                if len(self._cut_ownership) >= MAX_QUEUED_PLANES:
                    raise SliceBusy(
                        f"the server has {len(self._cut_ownership)} plane cuts queued; "
                        f"try again shortly"
                    )
                self._cut_waiting[key] = mine
                #: Which run owns each queued key, so the per-run cap can be counted.
                self._cut_ownership.add((run_id, key))
        if ahead is not None:
            # **A WAITER IS COUNTED TOO** (Opus recheck item 4b). The caps above were checked only
            # when a NEW entry was created, so any number of callers asking for the SAME plane took
            # the `ahead` branch and waited 30 s each - **forty duplicates held forty route threads
            # and were counted by nothing**. The count is what the caps govern, so the count has to
            # include everyone waiting, not only the first.
            with self._lock:
                # **THE CALLER IS COUNTED IN BOTH SUMS** (Opus round 3, item 6a). `total_waiters`
                # was the sum BEFORE this caller was added, so the cap admitted one extra - a
                # cap that is one too generous is still a cap that does not hold, and the whole
                # point of these numbers is the thread count they imply.
                waiters = self._cut_waiters.get(key, 0) + 1
                total_waiters = sum(self._cut_waiters.values()) + 1
                # `_cut_ownership` holds `(run id, cache key)` pairs, so the unpack is two.
                queued_for_run = sum(1 for run, _key in self._cut_ownership if run == run_id)
                over_run = queued_for_run >= MAX_QUEUED_PLANES_PER_RUN
                if waiters > MAX_WAITERS_PER_PLANE or total_waiters > MAX_WAITERS or over_run:
                    raise SliceBusy(
                        "too many requests are already waiting for a plane cut; try again shortly"
                    )
                self._cut_waiters[key] = waiters
            try:
                if not ahead.wait(timeout=CUT_QUEUE_TIMEOUT_S):
                    raise SliceBusy(
                        f"another request is still cutting this plane after "
                        f"{CUT_QUEUE_TIMEOUT_S:.0f}s"
                    )
            finally:
                # **Decremented whatever happened**, or a timed-out waiter would leave a count
                # behind and refuse later callers for a queue that is no longer there.
                with self._lock:
                    remaining = self._cut_waiters.get(key, 1) - 1
                    if remaining <= 0:
                        self._cut_waiters.pop(key, None)
                    else:
                        self._cut_waiters[key] = remaining
            return self._load_slice_file(run_id, path)
        try:
            return self._cut_once(run_id, request, path, record, fetch_artifact, HostRegistry)
        finally:
            with self._lock:
                self._cut_waiting.pop(key, None)
                self._cut_ownership.discard((run_id, key))
            mine.set()

    def _cut_once(
        self,
        run_id: str,
        request: _CutRequest,
        path: Path,
        record: Any,
        fetch_artifact: Any,
        host_registry: Any,
    ) -> tuple[memoryview, dict[str, Any]] | None:
        """The actual cut: one host call under the concurrency limit, then cached to `path`.

        **The slot is ACQUIRED WITH A TIMEOUT, and this is why.** The queue caps bound how many
        callers may be WAITING, but a cut may take `CUT_SLICE_TIMEOUT_S` = 300 s - so with two
        slots and the maximum queue, a caller could wait many times 300 s while holding its route
        thread the whole time. Acquiring with a timeout turns that into the same `results.busy`
        answer the queue cap gives: a request the client can retry rather than one that never
        ends.

        **Repaired prose**: this arrived as a fragment with its opening sentence gone and a clause
        duplicated - "queued planes a caller could wait sixteen times 300 s" appeared twice, once
        with nothing before it. **Nothing checks a comment**, which is how it survived a green
        lint and a green test run; Opus found it by reading.
        """
        if not self._cut_slots.acquire(timeout=CUT_QUEUE_TIMEOUT_S):
            raise SliceBusy(
                f"no cut slot came free within {CUT_QUEUE_TIMEOUT_S:.0f}s; try again shortly"
            )
        try:
            # **Held only around the host call**, so the two-slot limit governs the expensive part
            # and not the file reads that a cache hit does.
            runner = host_registry.of(self._session).runner(record.host or "local")
            args = dict(request.args)
            with self._lock:
                chosen = self._chosen_time.get(run_id)
            if chosen is not None:
                args.setdefault("time", chosen)
            reply = runner.call(request.op, job=record.job, timeout=CUT_SLICE_TIMEOUT_S, **args)
        except Exception as exc:
            # **The caller needs to tell "the host is gone" from "the plane missed"**, so the reason
            # is recorded rather than flattened into a None - **against THIS cut of THIS run**.
            with self._lock:
                self._slice_errors[(run_id, request.error_key)] = f"{exc}"
                self._trim_slice_errors(run_id)
            log.warning("a cut could not be made", extra=fields(run=run_id, error=str(exc)))
            return None
        finally:
            # **Released in a `finally`, because `acquire` is not a context manager.** A cut that
            # raised would otherwise hold its slot for ever - and with only two slots the server
            # would stop cutting anything at all, which is worse than the stall this replaced.
            self._cut_slots.release()
        # **Checked before the fetch**, so a surface over its limit is refused without moving its
        # bytes. Outside the `except` above on purpose: a refusal is the caller's answer, not a
        # host failure to be recorded and flattened into None.
        if request.accept is not None:
            request.accept(reply)
        # **THE OLD VIEW GOES BEFORE THE NEW FILE, NOT AFTER** (Opus L5). The view cache is keyed
        # by `(size, mtime_ns)`, and a replacement can match both - measured: two writes of the
        # same length with the mtime restored give an identical stamp. Evicting afterwards leaves
        # a window in which a concurrent reader's stamp MATCHES and it is served the **old** bytes
        # under the new file's name. Evicting first closes it: from the moment the file starts
        # changing there is no cached view to match.
        #
        # **NO TEST COVERS THIS ORDERING, DELIBERATELY** (claude-win2's 0930, item 1). The shipped
        # path cannot reach the window on this platform: `_load_slice_file` calls
        # `os.utime(path, None)` on every successful read, so serving a view refreshes the stamp,
        # and this filesystem's `mtime_ns` granularity is fine enough that two consecutive writes
        # differ. A test that forces the collision by calling `_evict_slice_view` directly passes
        # against either ordering - it observes the helper, not the order - so it was deleted
        # rather than kept as false comfort. The one-line reorder stays: it removes the window
        # rather than relying on the touch, and it costs nothing.
        self._evict_slice_view(path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # **Fetched with its digest checked**, the same helper the run's own fields use: the
            # agent renames nothing and verifies nothing, so a truncated cut would otherwise land in
            # the cache looking exactly like a good one.
            fetch_artifact(
                runner,
                record.job,
                str(reply["artifact"]),
                path,
                str(reply["sha256"]),
                int(reply["size"]),
            )
        except OSError as exc:
            # A cache that cannot be written is not a reason to fail the request: the data is in
            # hand and the answer is the same either way. It will simply be computed again.
            log.warning("a cut could not be cached", extra=fields(run=run_id, error=str(exc)))
        # A trim that cannot run is a full cache folder, not a failed request.
        with contextlib.suppress(MeshInvalid):
            self._trim_slices(run_id, request.suffix)
        # **Not evicted here any more**: it is done BEFORE the fetch, which is the only ordering
        # that leaves no window. See the comment there.
        return self._load_slice_file(run_id, path)

    def _evict_slice_view(self, path: Path) -> None:
        """Drop a cached view of `path`, because the file under it has just been replaced."""
        with self._lock:
            found = self._field_views.pop(str(path), None)
        if found is not None:
            self._close_view(found[1])

    def slice_transport_for(self, run_id: str, plane: dict[str, Any] | None = None) -> bytes | None:
        """The run's slice as transport version 2, or None when there is no usable one.

        `plane` names a cut to serve instead of the run's own; None is the artifact's default
        mid-plane, which is what every pre-existing caller asks for.
        """
        return self.slice_transport_of(self.slice_artifact_for(run_id, plane))

    def slice_transport_of(self, loaded: tuple[memoryview, dict[str, Any]] | None) -> bytes | None:
        """A loaded cut artifact's surface as transport version 2, or None when it has none.

        **Shared by planes and iso-surfaces**: both keep their geometry in the artifact's `slice`
        section, so one packer serves both and the viewport has one reader.
        """
        from stokes.run.fields import slice_transport

        if loaded is None:
            return None
        data, header = loaded
        if not self._has_slice(header):
            return None
        try:
            return slice_transport(data, header)
        except MeshFormatError:
            log.warning("a cut surface could not be packed")
            return None

    def slice_edges_for(self, run_id: str, plane: dict[str, Any] | None = None) -> bytes | None:
        """The run's slice's cell edges in the T-210 edge codec, or None."""
        from stokes.run.fields import slice_edges

        loaded = self.slice_artifact_for(run_id, plane)
        if loaded is None:
            return None
        data, header = loaded
        if not self._has_slice(header):
            return None
        try:
            return slice_edges(data, header)
        except MeshFormatError:
            log.warning("a slice's edges could not be packed", extra=fields(run=run_id))
            return None

    def slice_state(self, run_id: str, plane: dict[str, Any] | None, *, cut: bool = True) -> str:
        """Why a plane request answered what it did: `ok`, `empty`, `no_fields` or `no_host`.

        **The route needs to tell these apart**, because the contract gives them different
        codes: a plane that misses the mesh is `results.slice_empty` and a client says "the
        plane misses the model", while a plane that cannot be cut because the host is gone is
        `results.host_unavailable` and retrying will not help. Returning only bytes would
        flatten the two into one 404 and lose the sentence the user actually needs.
        """
        found = self.record(run_id)
        if found is None or not found.job:
            return "no_fields"
        if plane is None:
            return "ok" if self.slice_transport_for(run_id) is not None else "no_fields"
        loaded = (
            self.slice_artifact_for(run_id, plane)
            if cut
            else self._load_slice_file(run_id, self.slice_path(run_id, plane))
        )
        if loaded is not None:
            _data, header = loaded
            return "ok" if self._has_slice(header) else "empty"
        # No artifact: either the host refused FOR THIS PLANE, or it has nothing there.
        from stokes.run.planes import plane_key

        with self._lock:
            reason = self._slice_errors.get((run_id, plane_key(plane)), "")
        return "no_host" if reason else "empty"

    def _has_slice(self, header: dict[str, Any]) -> bool:
        entry = header.get("slice")
        return isinstance(entry, dict) and int(entry.get("triangles") or 0) > 0

    # --------------------------------------------------------------- iso-surfaces (ADR-0026)

    def iso_path(self, run_id: str, field: str, value: float) -> Path:
        """The cached iso-surface for one `(run, field, value)`, beside the run's cached planes.

        **The key names the field and the value** (`iso_key`), and the file has its own prefix and
        suffix, so an iso-surface can never be read as a plane or as another field's surface.
        """
        from stokes.run.iso import ISO_SUFFIX
        from stokes.run.planes import iso_key

        return self.slices_folder(run_id) / f"iso-{iso_key(field, value)}{ISO_SUFFIX}"

    def iso_request(self, run_id: str | None, field: str, value: Any) -> IsoRequest:
        """A request checked against the run's fields and ranges (`stokes.run.iso.check_request`).

        `value` is a JSON number (SI) or quantity text. Raises `RunNotFound` for an unknown run
        and an `IsoInvalid` for everything a cut could not answer.
        """
        from stokes.run.iso import check_request

        return check_request(self.fields_status(run_id), field, value)

    def iso_artifact_for(
        self, request: IsoRequest, *, cut: bool = True
    ) -> tuple[memoryview, dict[str, Any]] | None:
        """The iso-surface artifact, from the cache or cut once through the shared cut path.

        **A cached file is served only when its header names the same field and value**, the rule
        `slice_artifact_for` applies to a plane. With `cut=False` a miss is None and nothing is
        asked of the host. A surface over `MAX_ISO_TRIANGLES` raises `IsoTooLarge` before its
        artifact is fetched.
        """
        from stokes.run.iso import ISO_SUFFIX, check_reply
        from stokes.run.planes import iso_from_header, iso_key

        key = iso_key(request.field, request.value)
        path = self.iso_path(request.run, request.field, request.value)
        loaded = self._load_slice_file(request.run, path)
        if loaded is not None:
            stored = iso_from_header(loaded[1])
            if stored is not None and iso_key(*stored) == key:
                return loaded
            log.warning(
                "a cached iso-surface does not match its name", extra=fields(run=request.run)
            )
        if not cut:
            return None
        cut_request = _CutRequest(
            "iso.cut",
            {"field": request.field, "value": request.value},
            f"iso-{key}",
            accept=check_reply,
            suffix=ISO_SUFFIX,
        )
        return self._cut_shared(request.run, path, cut_request)

    def iso_summary(self, request: IsoRequest, *, cut: bool = True) -> dict[str, Any] | None:
        """What `run.iso_surface` answers, or None when the surface is not held and cannot be cut.

        The triangle limit is checked on the cached header too, so a file that arrived by another
        route is refused the same way a fresh cut is.
        """
        from stokes.run.iso import check_triangles, summary
        from stokes.run.planes import iso_key

        loaded = self.iso_artifact_for(request, cut=cut)
        if loaded is None:
            return None
        _data, header = loaded
        entry = header.get("slice")
        check_triangles(int(entry.get("triangles") or 0) if isinstance(entry, dict) else 0)
        return summary(request, header, iso_key(request.field, request.value))

    def iso_failure(self, request: IsoRequest) -> str:
        """Why the last cut of this surface failed on the host, or "" when it did not."""
        from stokes.run.planes import iso_key

        with self._lock:
            return self._slice_errors.get(
                (request.run, f"iso-{iso_key(request.field, request.value)}"), ""
            )

    def trace_streamlines(
        self,
        run_id: str | None,
        seeds: Sequence[Sequence[float]] | None,
        *,
        direction: str = "forward",
        max_length: float | None = None,
        step_cells: float = 0.2,
        max_points: int = 400,
        seed_patches: Sequence[str] | None = None,
        seed_count: int = 50,
    ) -> dict[str, Any]:
        """Streamlines through the run's 3-D volume, traced by the run's host (ADR-0030).

        One `stream.trace` call to the host's agent; the answer is the agent's own (float32 bytes
        for the points and speeds, the offsets and why each line stopped). `None` for the run
        means the current one. The seeds are `seeds`, or `seed_count` points the host spreads over
        the boundary patches named in `seed_patches`. Raises `RunNotFound` for an unknown run and
        `StokesError` when the host cannot trace.
        """
        from stokes.errors import StokesError
        from stokes.run.errors import RunNotFound
        from stokes.run.hosts import HostRegistry

        record = self.record(run_id)
        if record is None or not record.job:
            raise RunNotFound(f"no run {run_id or '(current)'} to trace")
        runner = HostRegistry.of(self._session).runner(record.host or "local")
        # **Patch seeds only when asked**, so a host whose agent predates them is asked exactly
        # what it always was.
        where: dict[str, Any] = (
            {"seed_patches": list(seed_patches), "seed_count": int(seed_count)}
            if seed_patches
            else {"seeds": seeds}
        )
        reply = runner.call(
            "stream.trace",
            job=record.job,
            timeout=CUT_SLICE_TIMEOUT_S,
            direction=direction,
            max_length=max_length,
            step_cells=step_cells,
            max_points=max_points,
            **where,
        )
        if not isinstance(reply, dict):
            raise StokesError("the host answered a trace with something that is not a result")
        return reply

    def sample_vectors(self, run_id: str | None, count: int = 400) -> dict[str, Any]:
        """The velocity on an even lattice through the run's 3-D volume, sampled by its host.

        One `vector.sample` call; the answer is the agent's own (float32 bytes for the points and
        vectors, and the lattice spacing in metres). Raises like `trace_streamlines`.
        """
        from stokes.errors import StokesError
        from stokes.run.errors import RunNotFound
        from stokes.run.hosts import HostRegistry

        record = self.record(run_id)
        if record is None or not record.job:
            raise RunNotFound(f"no run {run_id or '(current)'} to sample")
        runner = HostRegistry.of(self._session).runner(record.host or "local")
        reply = runner.call(
            "vector.sample", job=record.job, timeout=CUT_SLICE_TIMEOUT_S, count=int(count)
        )
        if not isinstance(reply, dict):
            raise StokesError("the host answered a sample with something that is not a result")
        return reply

    def iso_in_background(self, request: IsoRequest) -> None:
        """Cut on a worker thread and announce the answer as `run.iso_ready` or `run.iso_failed`.

        **Why a thread**: `run.iso_surface` runs on the session thread, which is also the server's
        event loop, and a cut may take minutes against a slow host. The command answers "cutting"
        at once; the summary arrives as an event, the way a run's progress does. The thread holds
        one of the shared cut slots only for the host call, so it is bounded by the same limits a
        route's cut is.
        """
        from stokes.errors import StokesError

        identity = {"run_id": request.run, "field": request.field, "value": request.value}

        def work() -> None:
            try:
                found = self.iso_summary(request)
            except (StokesError, SliceBusy) as exc:
                code = getattr(exc, "code", "results.busy")
                self._emit("run.iso_failed", {**identity, "code": code, "message": str(exc)})
                return
            except Exception as exc:
                log.warning(
                    "an iso-surface cut failed", exc_info=True, extra=fields(run=request.run)
                )
                self._emit(
                    "run.iso_failed",
                    {**identity, "code": "results.iso_failed", "message": str(exc)},
                )
                return
            if found is None:
                reason = self.iso_failure(request) or "the run's host could not cut the surface"
                self._emit(
                    "run.iso_failed",
                    {**identity, "code": "results.host_unavailable", "message": reason},
                )
                return
            self._emit("run.iso_ready", {**identity, **found})

        threading.Thread(target=work, name="stokes-iso-cut", daemon=True).start()

    def fields_status(self, run_id: str | None = None) -> dict[str, Any]:
        """What `run.fields` answers: the names, their ranges, and whether the cache is there.

        **`surfaces: "missing"` is the honest answer after a cache cleanup**, and the routes 404
        from it. Nothing is fetched here: `project_opened` does not go to a host, so a reopened
        project shows what it has.
        """
        found = self.record(run_id)
        if found is None:
            from stokes.run.errors import RunNotFound

            raise RunNotFound(f"no run {run_id or '(current)'}")
        loaded = self._run_fields(found.id)
        if loaded is None:
            return {
                "run": found.id,
                "time": 0.0,
                "fields": [],
                "slice": False,
                "surfaces": "missing",
            }
        _data, header = loaded
        ranges = self._ranges(header)
        percentiles = self._percentiles(header)
        volume = self._volume_ranges(header)
        units = header.get("units") or {}
        ordered = [name for name in (found.fields or []) if name in ranges]
        ordered.extend(name for name in ranges if name not in ordered)
        entries: list[dict[str, Any]] = []
        for name in ordered:
            entry: dict[str, Any] = {
                "name": name,
                "units": str(units.get(name, "")),
                "min": ranges[name][0],
                "max": ranges[name][1],
            }
            if name in percentiles:
                # The legend's auto range (1-99 %); the web's `FieldInfo` reads `p01`/`p99`.
                entry["p01"], entry["p99"] = percentiles[name]
            if name in volume:
                entry["volume"] = {"min": volume[name][0], "max": volume[name][1]}
            entries.append(entry)
        time_value = float(header.get("time") or 0.0)
        times = [float(value) for value in header.get("times") or [time_value]]
        return {
            "run": found.id,
            "time": time_value,
            "times": times,
            "fields": entries,
            "slice": self._has_slice(header),
            "surfaces": "ok",
            "two_d": self.two_d(),
        }

    def two_d(self) -> bool:
        """Whether the mesh on screen is one cell thick: an OpenFOAM 2-D case has `empty` patches.

        **For the first picture of a result** (RESULTS-OVERHAUL §3.7): a 2-D case (the cavity,
        pitzDaily, most tutorials) wants its surface coloured and an orthographic camera, where a
        3-D one wants a slice; the browser cannot tell the two apart from a bounding box (the
        cavity is 10 % thin, a flat duct is thinner). The mesh patches' types are what the agent
        read from `constant/polyMesh/boundary`, so `empty` is the solver's own word for it.
        """
        project = self._session.project
        if project is None:
            return False
        return any(
            node.type == "mesh_patch" and str(node.params.get("patch_type") or "") == "empty"
            for node in project.root.walk()
        )

    def select_time(self, run_id: str, time: float | None) -> str:
        """Read a finished run's fields at another written time. **A worker thread.**

        The run's cached planes and iso-surfaces are dropped first, because they were cut at the
        old time and a cache keyed by plane would otherwise serve them for the new one; later cuts
        are made at ``time`` (`_cut_shared`). None goes back to the latest. Returns
        `fetch_fields`' error text, `""` when it worked.
        """
        with self._lock:
            if time is None:
                self._chosen_time.pop(run_id, None)
            else:
                self._chosen_time[run_id] = float(time)
        self._remove_slices(run_id)
        # The old file stays readable until the new one is swapped in (`fetch_fields`): a reader
        # asking meanwhile gets the previous time rather than nothing.
        return self.fetch_fields(run_id, time)

    # ------------------------------------------------------------------ the session hooks

    def project_saved(self, project: Any, path: Path, previous: Path | None) -> None:
        """Move the records to the project's new folder on Save as, and the cached fields with them.

        A saved project's records travel with it, which is what a user expects: the runs were
        made from this model. **The fields are part of that**: a project moved to a new folder whose
        results cache stayed behind would show every finished run as `missing`, and the host the
        fields came from may be gone (the brief's `project_saved(previous)`).

        **The first save copies out of the scratch folder too**, exactly as `MeshService` does for
        its surfaces: an unsaved project keeps its cache in a per-session scratch folder, and
        without the copy the fields of every run made before the save would be lost the moment the
        project got a home.

        The signature is the session's extension protocol (`project, path, previous`); this
        method used to take `previous` alone, so `Session.notify_extensions` raised `TypeError` on
        **every** save and the session logged it and carried on — a hook that never ran. The test
        that saves and reopens a project is what found it.
        """
        del project
        from stokes.geometry.shapes import cache_dir

        # **The scratch folder is the geometry store's own attribute**, not a session key: the
        # field cache lives under the same base as its shapes, so the source of a first save is
        # whatever that store is using.
        from stokes.geometry.store import GeometryStore

        scratch = getattr(GeometryStore.of(self._session), "_scratch", None)
        with self._lock:
            ids = builtins.list(self._order)
        for run_id in ids:
            with self._lock:
                found = self._records.get(run_id)
            if found is not None:
                # **The record MOVES with the project, so its origin moves too.** `_write`
                # prefers `origin`, which until here named the folder the record was loaded
                # from - the project being left. Re-stamping it is what sends the write to the
                # new folder; without it the copy went back to the old one and a reopened
                # project found no runs (caught by the test that saves and reopens).
                found.origin = self._record_folder(run_id)
                self._write(found)
        # **No runs-scratch discard here any more**: the scratch is gone, so the records were
        # written straight into the project by the loop above. The geometry store's scratch is the
        # third mechanism and is copied below until it goes the same way.
        old = cache_dir(previous) if previous is not None else scratch
        new = cache_dir(path)
        if old is None or old == new:
            return
        self._copy_fields(Path(old), new)

    def project_copied(self, project: Any, path: Path, previous: Path | None = None) -> None:
        """Session hook: put the cached fields in a COPY, **without moving this session's records**.

        **The fields travel, the records do not** (Opus F3). `project_saved` re-stamps every
        record's `origin` so later writes follow the project - correct for Save as, where the
        session moves, and wrong here: Save a copy leaves the session in the ORIGINAL, so
        re-stamping would send the original project's writes into the snapshot. This copies the
        cache bytes only.

        The records themselves are already in the copy: the folder copy carried `runs/`, and the
        in-flight run was left out of it by the caller."""
        del project
        from stokes.geometry.shapes import cache_dir
        from stokes.geometry.store import GeometryStore

        scratch = getattr(GeometryStore.of(self._session), "_scratch", None)
        source = previous if previous is not None else self._session.project_path
        old = cache_dir(source) if source is not None else scratch
        new = cache_dir(path)
        if old is None or old == new:
            return
        self._copy_fields(Path(old), new)

    def _copy_fields(self, old: Path, new: Path) -> None:
        """Copy `<old cache>/results/*.stkfld` into the new cache. **Never raises.**

        **Only a name this store could have written is copied**: `<run id>.stkfld` with an id
        matching `ID_RE`, which is the rule `fields_path` applies on the way in. A name that does
        not match is not ours, and copying it would move an unknown file into a project folder.
        The copy goes through a `.part` name and is renamed, so a reader never sees half a file.
        """
        from stokes.model.ids import ID_RE

        source, target = old / RESULTS_FOLDER, new / RESULTS_FOLDER
        if not source.is_dir() or source.is_symlink():
            return
        for entry in sorted(source.glob(f"*{RESULTS_SUFFIX}")):
            name = str(entry.name)
            if not ID_RE.match(name[: -len(RESULTS_SUFFIX)]) or entry.is_symlink():
                continue
            try:
                target.mkdir(parents=True, exist_ok=True)
                part = target / (name + ".part")
                shutil.copyfile(entry, part)
                part.replace(target / name)
            except OSError:
                log.warning("a field file was not copied", extra=fields(name=name))

    def project_opened(self, project: Any = None, path: Path | None = None) -> None:
        """Load the records for a project just opened, and re-attach a run that was left going.

        **The signature is the session's extension protocol** (`project, path`). This method used to
        take no arguments, so `Session.notify_extensions` raised `TypeError` on every open, the
        session logged it and carried on, and a reopened project had **no runs at all** — the
        records were on disk the whole time. Found by the test that saves a project and reopens it.

        Nothing is fetched here: the routes read the cache file, and a missing one is what makes
        `run.fields` answer `surfaces: "missing"` (the brief's rule). A host is asked only about a
        run still marked in flight, by `_reattach`.
        """
        del project, path  # the cache is found through the session's current project
        # **Runs still in flight are RECORDED as abandoned before the records are dropped**
        # (Opus review item 11). Clearing alone lost them: `running()` answered None, so a
        # second solve could start while the first job was still going on the host - and
        # nothing ever cancelled it. Measured: open a project with a run in flight, open
        # another, and `running()` is None with the job still live.
        #
        # **`interrupted` on disk, not `cancelled`.** This session is leaving the run, not stopping
        # it: the host may be another machine, and a solve the user asked for is not ours to kill
        # because they opened a different file. `interrupted` says what happened and leaves the job
        # findable; the re-attach path picks it up when the project is opened again.
        self._abandon_in_flight()
        # **A session with no project has no runs to reload** (claude-win2, reading 1). This hook
        # used to give an unsaved project a fresh scratch folder, because the folder was kept on the
        # SESSION and a second unsaved project otherwise found the first one's runs - measured. With
        # no scratch there is nothing to hand over, and the state above is already cleared.
        if self._session.project_path is None:
            return
        folder = runs_dir(self._session)
        with self._lock:
            self._order.clear()
            self._records.clear()
            self._history.clear()
            self._history_reads.clear()
        if not folder.is_dir():
            return
        entries = builtins.list(folder.iterdir())
        for entry in sorted(entries):
            if not entry.is_dir():
                continue
            path = entry / "record.json"
            if not path.is_file():
                continue
            try:
                if path.stat().st_size > MAX_RECORD_BYTES * 2:
                    raise ValueError("record file is implausibly large")
                record = RunRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError) as exc:
                # **A bad record is logged and skipped**, never fatal: one corrupt file must
                # not make a
                # project un-openable.
                log.warning(
                    "skipping an unreadable run record",
                    extra=fields(path=str(path), error=str(exc)),
                )
                continue
            if not record.id:
                continue
            # **The record's id must be the folder it came from** (Opus review item 1). A crafted or
            # shared project can put any id in the file, and `clear` deletes by that id, so a record
            # claiming to be `"../IMPORTANT"`, sitting in `runs/r_deadbeef/`, would delete a sibling
            # directory. The folder name is the fact; the field inside it is a claim.
            if record.id != entry.name or not _is_run_id(record.id):
                log.warning(
                    "skipping a run record whose id is not its folder",
                    extra=fields(folder=entry.name, id=record.id),
                )
                continue
            # **The folder it was loaded from, remembered on the record.** `_write` prefers it, so a
            # record closed out AFTER the session moved to another project still lands in the folder
            # it belongs to (Opus review item 11).
            record.origin = folder
            with self._lock:
                self._records[record.id] = record
                self._order.append(record.id)
                self._history[record.id] = _empty_history()
        with self._lock:
            # **Ordered by `run_order_key`**, not by `created` alone: a record whose `created` was
            # never stamped sorts as `""`, which put it FRONT of a list meant to be oldest-first.
            self._order.sort(key=lambda run_id: run_order_key(self._records[run_id]))
        self._reattach()

    def _reattach(self) -> None:
        """Ask the host about a run this session did not start.

        **`interrupted` is the honest answer when the host does not know the job**: the agent
        may have
        restarted, and a `running` record that will never change is worse than one that says what
        happened.
        """
        from stokes.run.hosts import HostRegistry

        records = builtins.list(self._records.values())
        for record in records:
            if record.state not in ("queued", "running", "cancelling"):
                continue
            if not record.job:
                # **No job, so nothing to ask the host about.** A run whose start was refused
                # before a job existed (the agent's lint, say) was left `queued` by older
                # versions, and reopened as "Run · starting" with a Stop button for ever.
                self.update(
                    record.id,
                    state="interrupted",
                    error=record.error or "the run never started on its host",
                )
                continue
            try:
                runner = HostRegistry.of(self._session).runner(record.host or "local")
                found = runner.call("job.status", job=record.job, timeout=30.0)
            except Exception as exc:
                log.warning(
                    "could not re-attach a run", extra=fields(run=record.id, error=str(exc))
                )
                self.update(record.id, state="interrupted", error=f"run.reattach: {exc}")
                continue
            state = str(found.get("state") or "")
            if state in FINAL_STATES:
                # **Collected, not merely labelled** (Opus review item 10). Setting the state alone
                # gave a host-`done` job `done` with no `converged`, no history and no fields, and
                # `reusable()` then handed it to the next solve as though it had been collected.
                self._collect_reattached(record, state)
            elif state in ("queued", "running", "cancelling"):
                # **A job still going is RE-SUBSCRIBED** (same item). Nothing was listening to
                # it, so it stayed `running` for ever: the agent's events reached no one and
                # the record never changed again.
                self._resubscribe(record)
            else:
                self.update(
                    record.id,
                    state="interrupted",
                    error="the host no longer knows this job",
                )

    def _collect_reattached(self, record: Any, state: str) -> None:
        """Run a re-attached finished job through the executor's collect path.

        **The snapshot is rebuilt from the record**, because the executor needs one to build the
        monitor paths: a re-attach has no live snapshot, and reading the project's current
        settings is what a solve would use. A snapshot that cannot be built sets the state
        (at least true) and records why, rather than raising into `project.open`.
        """
        from stokes.run.executors import executor_for

        executor_type = executor_for(record.engine)
        if executor_type is None:
            self.update(record.id, state=state)
            return
        # **A snapshot is needed only for the MONITOR paths**, which are built from the patches.
        # `_snapshot_for_reattach` raises when the project has no mesh, and refusing to collect
        # at all would leave a finished run with no converged flag, no history and no residuals -
        # the very gap item 10 is about - because a mesh had been deleted since. So the
        # collection goes ahead and only the monitors are lost, with the reason recorded.
        snap: Any = None
        try:
            snap = _snapshot_for_reattach(self._session)
        except Exception as exc:
            log.warning(
                "could not rebuild a snapshot for a re-attached run; collecting without monitors",
                extra=fields(run=record.id, error=str(exc)[:200]),
            )
        executor_type(self._session, self).reattach_and_collect(record, state, snap)

    def _resubscribe(self, record: Any) -> None:
        """Subscribe to a job that is still running on the host.

        A failure here is recorded rather than raised: opening a project must not fail because one
        job's host has gone away.
        """
        from stokes.run.executors import executor_for

        executor_type = executor_for(record.engine)
        if executor_type is None:
            self.update(record.id, state="interrupted", error="no executor for this engine")
            return
        try:
            snap = _snapshot_for_reattach(self._session)
            executor_type(self._session, self).reattach(record, snap)
        except Exception as exc:
            log.warning(
                "could not re-subscribe a running run",
                extra=fields(run=record.id, error=str(exc)[:200]),
            )
            self.update(record.id, error=f"run.reattach: {exc}")

    def _abandon_in_flight(self) -> None:
        """Write `interrupted` for every run still going, before the records are dropped.

        **Written to disk through `update`, which is why it happens first**: once `_records` is
        cleared there is no record left to change, and the state on disk would keep saying `running`
        for a run this session is no longer watching.

        A run with no final state and no job is left as it is: it never reached a host, so there is
        nothing to say about it.

        **The write goes to the folder the record CAME FROM.** `project_opened` runs after the
        session's `project_path` has changed, so `runs_dir(session)` names the
        NEW project - measured: the record became `interrupted` in memory and was written into the
        incoming project's folder, leaving the real one still saying `running`. Each record carries
        the folder it was loaded from, and `_write` uses that.
        """
        with self._lock:
            in_flight = [
                record
                for record in self._records.values()
                if not record.final and record.state in ("queued", "running", "cancelling")
            ]
        for record in in_flight:
            self.update(
                record.id,
                state="interrupted",
                error="run.reattach: the project was closed while this run was going",
            )

    def project_closed(self) -> None:
        """Detach: forget every record, and the scratch folder if the project was never saved."""
        with self._lock:
            self._order.clear()
            self._records.clear()
            self._history.clear()
            self._history_reads.clear()
        scratch = self._session.__dict__.get("_run_scratch")
        if scratch is not None and self._session.project_path is None:
            shutil.rmtree(scratch, ignore_errors=True)
            self._session.__dict__["_run_scratch"] = None

    # ------------------------------------------------------------------ waiting

    def settle(self, timeout: float | None = None, *, coalesce: bool = False) -> None:
        """Wait for the current run, as the session's own hook.

        **`coalesce` is accepted and does nothing**, which is deliberate and worth saying:
        replay passes
        it so a burst of edits inside one debounce window runs once. A *run* is not debounced
        — a user
        who pressed Solve wants that solve — so there is nothing to coalesce, and pretending
        otherwise
        would make a replay skip a run it should reproduce.
        """
        del coalesce
        found = self.current()
        if found is None or found.final:
            return
        deadline = time.monotonic() + (RUN_TIMEOUT_S if timeout is None else timeout)
        while time.monotonic() < deadline:
            current = self.record(found.id)
            if current is None or current.final:
                return
            time.sleep(0.02)

    def hold_debounce(self) -> Any:
        """No debounce window: a run is not an edit that can be coalesced."""
        import contextlib

        return contextlib.nullcontext()

    # ------------------------------------------------------------------ the runner's thread

    def on_event(self, name: str, data: dict[str, Any]) -> None:
        """One session event. **The signature every extension must have**: `Session.emit` calls
        `hook(event, data)`, so a third parameter makes the call raise `TypeError` on EVERY event —
        and the session logs that and carries on, so nothing anywhere fails loudly.

        **That is what this was, and it looked like a race for most of a round.** The method took
        `(run_id, name, data)`, so no event ever reached the store: a run's `iterations` came from
        nothing and its `converged` was never set. It presented as an intermittent
        `converged: False`, which is exactly what a lost event looks like from outside, and tracing
        it hid the fault because the trace changed which events arrived.

        The run id comes from the DATA, because that is the only thing that says which run an event
        belongs to.
        """
        run_id = str(data.get("run_id") or "")
        if not run_id:
            return
        self.runner_event(run_id, name, data)

    def runner_event(self, run_id: str, name: str, data: dict[str, Any]) -> None:
        """Map one AGENT event onto a run. **Called on the runner's event thread** by the executor.

        Nothing heavy happens here: the residual block is appended (a list append) and the rest is
        forwarded. Reading the agent's files happens on a worker thread, because it is a round trip.
        """
        record = self.record(run_id)
        if record is None or record.job != str(data.get("job") or record.job):
            return
        if name == "job.state":
            state = str(data.get("state") or "")
            step = data.get("step")
            if step is not None:
                with self._lock:
                    self._live_state(run_id, step=step, fraction=self._fraction(data))
                # **A step that runs means the run runs.** The job's own "running" event can come
                # before the subscription, and every later one names a step; returning here left
                # the elbow's record `queued` through 1,400 iterations ("Run · starting").
                if state == "running" and record.state == "queued":
                    self.update(run_id, state="running")
                return
            if state in ("running", "cancelling"):
                self.update(run_id, state=state)
            elif state in FINAL_STATES:
                # **A final state from the WIRE does not end the run — `finish` does.**
                #
                # This line used to be `self.update(run_id, state=state)` for every final
                # state, which
                # made the record final the instant the event arrived. `finish` needs three RPCs
                # (`job.status`, the history, the monitors) before it can set `converged`, and
                # `settle`/`run.status` both stop at `record.final` — so a caller read the record
                # between the two calls and found `iterations` right (counted live from
                # `monitor.residual`) and `converged` still at its default `False`. That is
                # the whole
                # race, and it is why every agent-side change to the tail read only moved the
                # timing.
                #
                # The record stays NON-final here, and `_collect`'s worker sets the real state
                # through
                # `finish`. The message is visible so a UI can say what is happening rather than
                # showing a run that looks finished and has no verdict.
                with self._lock:
                    self._live_state(run_id, message="collecting")
        elif name == "job.progress":
            with self._lock:
                self._live_state(
                    run_id, fraction=self._fraction(data), message=str(data.get("message") or "")
                )
        elif name == "monitor.residual":
            for block in data.get("blocks") or [data]:
                self.append_residual(run_id, block)
            # **The count is the number of BLOCKS**, which is what the history now holds —
            # reading the
            # old `"iterations"` key returned 0 on every event, so a finished run reported no
            # iterations
            # at all. Caught by the test that counts them.
            with self._lock:
                count = len(self._history.get(run_id, {}).get("blocks") or [])
            self.update(run_id, iterations=count)
        elif name == "log.lines":
            self._emit(
                "run.log",
                {"run_id": run_id, "step": data.get("step", ""), "lines": data.get("lines") or []},
            )

    def _fraction(self, data: dict[str, Any]) -> float:
        try:
            return float(data.get("fraction") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _live_state(self, run_id: str, **fields: Any) -> None:
        """The step and fraction, which are live rather than part of the record."""
        if not hasattr(self, "_live"):
            self._live: dict[str, dict[str, Any]] = {}
        entry = self._live.setdefault(run_id, {})
        entry.update({key: value for key, value in fields.items() if value is not None})


def _vector(value: Any) -> list[float] | None:
    """Three finite floats from a settings array, or None when it is not one.

    **Empty is None, not zeros.** `slice_origin` defaults to `[]` meaning "the mesh's bbox centre",
    and reading that as `[0, 0, 0]` would put the plane through the model's origin instead.
    """
    if not isinstance(value, list) or len(value) != 3:
        return None
    try:
        numbers = [float(item) for item in value]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(number) for number in numbers):
        return None
    return numbers


def _seconds_from(status: dict[str, Any], report: dict[str, Any]) -> float | None:
    """How long the run took, from the job's own record.

    **The job's `started`/`ended` pair**, not the report's: `execution_s` is the solver's
    measure of work
    rather than wall time, and it also exists on a step that never finished. None when there
    is nothing to
    read, so the record says nothing rather than 0.
    """
    started, ended = status.get("started"), status.get("ended")
    if isinstance(started, (int, float)) and isinstance(ended, (int, float)) and ended >= started:
        return float(ended - started)
    execution = report.get("execution_s")
    return float(execution) if isinstance(execution, (int, float)) and execution > 0 else None


def _error_text(status: dict[str, Any], state: str) -> str:
    """The failure's code and message, or empty for a run that worked."""
    if state in ("done", "done_unconverged", "cancelled"):
        return ""
    error = status.get("error") or {}
    if isinstance(error, dict):
        code, message = str(error.get("code") or ""), str(error.get("message") or "")
        return f"{code}: {message}" if code else message
    return str(error) if error else ""


def _empty_history() -> dict[str, Any]:
    """**Blocks, not parallel lists.** A per-field list indexed by position raised
    `IndexError` the first
    time a field was absent from a block, and fields ARE absent: `p` is solved twice per step and a
    laminar run has no `k`."""
    empty: dict[str, Any] = {"blocks": []}
    return empty


def _replace_with_retries(temp: Path, target: Path, tries: int = 6, wait: float = 0.02) -> None:
    """`temp.replace(target)`, retried on Windows' refusal while another handle holds the target.

    `PermissionError` (WinError 5 / 32) is what a rename gets while a reader has `target` open;
    the reader is a `run.status` or a project copy in flight and holds it for milliseconds, so a
    few short waits cover it. Anything else, and the last refusal, propagate to the caller.
    """
    for attempt in range(tries):
        try:
            temp.replace(target)
            return
        except PermissionError:
            if attempt == tries - 1:
                raise
            time.sleep(wait * (attempt + 1))


def _now() -> str:
    """An ISO-8601 UTC timestamp, which is what a record carries and a journal replays."""
    from datetime import datetime

    return datetime.now(UTC).isoformat(timespec="seconds")


def _is_run_id(run_id: object) -> bool:
    """Whether `run_id` is an id at all, without raising. `fullmatch`, as `_check_run_id` uses."""
    from stokes.model.ids import ID_RE

    return isinstance(run_id, str) and ID_RE.fullmatch(run_id) is not None


def _check_run_id(run_id: object) -> None:
    """Refuse an id that is not one, before it becomes a path segment.

    `ID_RE.fullmatch` — **fullmatch, not match**: `match` accepts a trailing newline, and a path
    segment with a newline in it is not the id the rest of the system validates against.
    """
    from stokes.model.ids import ID_RE

    if not isinstance(run_id, str) or ID_RE.fullmatch(run_id) is None:
        raise RunError(f"{run_id!r} is not a run id", details={"code": "run.invalid_id"})


def _snapshot_for_reattach(session: Session) -> Any:
    """The solve snapshot a re-attached run is collected with.

    **Built the same way `run.start` builds one**, from the project's settings and the mesh
    service's status, because the executor needs a snapshot to build the monitor paths -
    `patchAverage(patch=X)` and `patchFlowRate(patch=X)` are derived from it, and a wrong path
    reads nothing at all. Reusing the live path is what keeps the two from disagreeing about
    which files a run's monitors live in.
    """
    from stokes.engines.model import SETTINGS_PATH
    from stokes.mesh.service import MeshService
    from stokes.run.snapshot import build_solve_snapshot

    settings = session.require_project().find(SETTINGS_PATH)
    if settings is None:
        raise RunError("no solver settings to rebuild a snapshot from")
    return build_solve_snapshot(session, settings, MeshService.of(session).status())
