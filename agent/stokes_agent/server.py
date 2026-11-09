"""The agent server: localhost only, token handshake, concurrent requests per connection."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import inspect
import math
import secrets
import threading
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from stokes.errors import ProtocolError, StokesError
from stokes.log import fields, get_logger
from stokes_agent import __version__, protocol
from stokes_agent.jobs import JobBadParams, JobError, JobStore, Step, default_root
from stokes_agent.probe import probe_host

log = get_logger(__name__)

Handler = Callable[..., Any]
AGENT_NAME = f"stokes-agent/{__version__}"

#: The frame the protocol will carry. A payload over this cannot be sent whole, so it is refused
#: here rather than failing inside `protocol.encode`.
MAX_FRAME_PAYLOAD = protocol.MAX_FRAME
#: The most `job.create` accepts across all its files. Bigger inputs go through `job.put`, chunk by
#: chunk, which is the whole reason that method exists.
MAX_CREATE_BYTES = 32 * 1024 * 1024
#: The most one `job.put` chunk may be.
MAX_PUT_BYTES = 8 * 1024 * 1024
#: How long one writer may take before it is dropped. **A stalled client must never hold the loop.**
BROADCAST_TIMEOUT_S = 2.0
#: The heartbeat interval while at least one client is connected.
HEARTBEAT_INTERVAL_S = 5.0


def _translate(exc: JobError) -> StokesError:
    """A supervisor error as the protocol's own type, keeping the CODE.

    The store raises `agent.bad_params`, `agent.too_large` and `agent.not_found` already; this
    maps each
    to the server's class so a client sees one family of errors whether the refusal came from
    parameter
    validation or from the store."""
    by_code: dict[str, type[StokesError]] = {
        "agent.bad_params": AgentBadParams,
        "agent.too_large": AgentTooLarge,
        "agent.not_found": AgentNotFound,
        "agent.unsupported": AgentUnsupported,
        "agent.busy": AgentBusy,
    }
    cls = by_code.get(exc.code, AgentBadParams)
    return cls(exc.message, details=exc.details)


def _slice_plane(raw: Any) -> dict[str, Any]:
    """A client's `slice` as `{"origin": [3 numbers], "normal": [3 numbers]}` or a refusal.

    **Anchored and bounded, like every other input this server takes**: exactly two keys, each a
    list of exactly three finite real numbers, and a normal that is not the zero vector — a plane
    with no direction is not a plane, and VTK's answer to one is undefined rather than an error.
    """
    if not isinstance(raw, dict):
        raise AgentBadParams("slice must be an object with origin and normal, or null")
    unknown = set(raw) - {"origin", "normal"}
    if unknown:
        raise AgentBadParams(
            f"slice has unknown keys: {', '.join(sorted(unknown))}; known: normal, origin"
        )
    plane: dict[str, list[float]] = {}
    for key in ("origin", "normal"):
        value = raw.get(key)
        if not isinstance(value, list) or len(value) != 3:
            raise AgentBadParams(f"slice.{key} must be a list of three numbers")
        numbers: list[float] = []
        for item in value:
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                raise AgentBadParams(f"slice.{key} must be a list of three numbers")
            if not math.isfinite(float(item)):
                raise AgentBadParams(f"slice.{key} must be finite")
            numbers.append(float(item))
        plane[key] = numbers
    if all(component == 0.0 for component in plane["normal"]):
        raise AgentBadParams("slice.normal must not be the zero vector")
    return plane


class AgentBadParams(StokesError):
    """The request is malformed: a wrong type, an unknown key, too many steps, too
    large a payload."""

    code = "agent.bad_params"


class AgentNotReady(StokesError):
    """The job exists but is not in a state where the call makes sense: its surfaces cannot be
    extracted until it is `done`."""

    code = "agent.not_ready"


class AgentTooLarge(StokesError):
    """A payload exceeds what the method accepts."""

    code = "agent.too_large"


class AgentNotFound(StokesError):
    """No such job."""

    code = "agent.not_found"


class AgentUnsupported(StokesError):
    """The host cannot do this — jobs need POSIX."""

    code = "agent.unsupported"


class AgentBusy(StokesError):
    """The job is running, so this request cannot be honoured."""

    code = "agent.busy"


def _token_bytes(value: object) -> bytes:
    """A token from a message as bytes, **never raising**.

    `secrets.compare_digest` needs two of the same type, and it refuses `str` values with non-ASCII
    characters. A token arrives as JSON, so it is text that has to become bytes before it can be
    compared - and a value that cannot be encoded must produce bytes that **cannot match the real
    token** rather than an exception, because a malformed token is a refused client, not a bug in
    the agent.
    """
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8", errors="replace")
    # **Anything that is not a string is not a token.** `str(None)` would be `"None"`, which a
    # client could send deliberately; the empty bytes match nothing.
    return b""


class AgentServer:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 0,
        token: str | None = None,
        jobs: JobStore | None = None,
        mode: str = "local",
        idle_exit_s: int | None = None,
    ) -> None:
        if host not in {"127.0.0.1", "::1", "localhost"}:
            raise ProtocolError("the agent binds to loopback only; use an SSH tunnel for remote")
        self.host = host
        self.port = port
        # **The mode is fixed at construction and never chosen by a client** (T-159a): a serve-mode
        # agent refuses a bare-token hello, so nobody who can reach the port can opt out of the
        # mutual handshake by asking for the older version.
        from stokes_agent.handshake import Mode

        self.mode = Mode(mode)
        self.token = token or secrets.token_urlsafe(24)
        self._server: asyncio.AbstractServer | None = None
        self._writers: set[asyncio.StreamWriter] = set()
        #: The reader for each writer, for the handshake and for anything else that must read after
        #: the handler has already consumed the first frame.
        self._readers: dict[asyncio.StreamWriter, asyncio.StreamReader] = {}
        self._write_locks: dict[asyncio.StreamWriter, asyncio.Lock] = {}
        self._methods: dict[str, Handler] = {}
        self._stop = asyncio.Event()
        #: The supervisor. **Given, or built lazily on first use** — a server that only probes needs
        #: no jobs directory, and creating one at construction would make every `ping`-only client
        #: leave a `~/.stokes/jobs` behind.
        self._jobs = jobs
        # **A store that was GIVEN must be wired too.** The lazy path below passes `on_event` to the
        # constructor, but the given one was simply stored — so a caller that built its own
        # `JobStore` (which is what the desktop and every protocol test do) got a server that
        # emitted nothing, and a job ran to `done` in silence. My first probe of this hid the bug by
        # assigning `_on_event` itself.
        if jobs is not None:
            # **`_on_event`, the store's own attribute.** Writing `on_event` created a NEW attribute
            # and left the real one None, so the fix looked applied and emitted nothing — the same
            # class of mistake as a patch that names the wrong module.
            jobs._on_event = self._job_event
        self._jobs_lock = threading.Lock()
        #: The loop the job threads schedule their events on, recorded by `start()`.
        self._loop: asyncio.AbstractEventLoop | None = None
        self._heartbeat: asyncio.Task[None] | None = None
        #: **The 12 h idle exit, wired in at last** (Opus M1). `should_exit_idle` existed, was
        #: tested, and **had no caller** - so a detached agent started for one mesh ran for ever.
        #: `None` disables it, which is what the in-process local agent wants: its lifetime is
        #: the app's.
        self._idle_exit_s = idle_exit_s
        self._idle_task: asyncio.Task[None] | None = None
        #: When a client last spoke, so idleness is measured rather than assumed. **Left as None
        #: until `start`**, because the value it is compared against - `time.monotonic()` - is
        #: seconds since BOOT, not since the epoch. Measured on this machine: uptime is 97 hours, so
        #: `0.0` made `now - last_active` about 349,000 s, and an agent with no client would have
        #: exited on its **FIRST poll** - roughly 60 s after starting, never mind the 12-hour rule
        #: (Opus recheck round 3, finding 3). `None` makes "not started yet" impossible to confuse
        #: with "idle since boot".
        self._last_active: float | None = None
        #: The clock, in one place so a test can **inject** one instead of waiting on the real
        #: thing (claude-win2's 0930, round-4 item 7). `time.monotonic()` is read several times in
        #: this module and the idle rule is a comparison of two of those readings, so a test that
        #: cannot choose "now" can only wait - or assert on the source text, which proves nothing
        #: about the decision. Overridden in tests; production never assigns it.
        self._now = time.monotonic
        self.register("ping", lambda: {"pong": True})
        self.register("probe", probe_host)
        # **Sourcing a bashrc is a process launch**, so the host record is slow and must not run
        # on the event loop: `_dispatch` calls a synchronous handler inline, and a one-second
        # capture would block every other request including a health check. Registered as an
        # `async def` that hands the work to a thread. Proved by a test that times a `ping`
        # against an in-flight `host.probe`.
        self.register("host.probe", self._host_probe)
        self.register("agent.info", self._agent_info)
        # **The owner's close, over the wire** (ADR-0024 §3.4): a WSL agent is a child of the
        # desktop and cannot be reached by `server.shutdown_jobs()` the way the in-process local
        # agent is. Its runner sends this once; the agent stops its jobs and then its loop, so a
        # window closing does not leave a mesher holding a case directory. A detached SSH agent
        # never receives it - it is meant to outlive the window that started it.
        self.register("agent.shutdown", self._agent_shutdown)
        # **Every job method returns at once.** A job must not live inside a request: the server
        # cancels a connection's request tasks when the client disconnects, so awaiting a job here
        # would cancel it the moment its creator closed the window.
        self.register("job.create", self._job_create)
        self.register("job.put", self._job_put)
        self.register("job.start", self._job_start)
        self.register("job.cancel", self._job_cancel)
        self.register("job.stop", self._job_stop)
        self.register("job.times", self._job_times)
        self.register("job.status", self._job_status)
        self.register("job.list", self._job_list)
        self.register("job.delete", self._job_delete)
        self.register("job.read", self._job_read)
        self.register("job.outputs", self._job_outputs)
        self.register("log.tail", self._log_tail)
        self.register("extract.surfaces", self._extract_surfaces)
        self.register("mesh.fidelity", self._mesh_fidelity)
        self.register("extract.fields", self._extract_fields)
        self.register("slice.cut", self._slice_cut)
        self.register("iso.cut", self._iso_cut)
        self.register("stream.trace", self._stream_trace)
        self.register("vector.sample", self._vector_sample)
        self.register("artifact.get", self._artifact_get)

    # ------------------------------------------------------------------ extraction

    async def _extract_surfaces(self, job: str) -> dict[str, Any]:
        """Extract the job's boundary patches into one artifact, or return the cached one.

        **Slow work off the event loop**: a million-cell surface takes seconds, and blocking the
        loop would stall every other client. A second call returns the cached artifact (its
        `index.json` entry) without reading the mesh again — the same job's mesh cannot have
        changed, because the job is `done` and nothing writes to a finished job.
        """
        from stokes_agent import extract

        def work() -> dict[str, Any]:
            store = self._job_store()
            state = store.status(job)
            if state.get("state") != "done":
                raise AgentNotReady(
                    f"the job is {state.get('state')!r}; "
                    "its surfaces can be extracted once it is done",
                    details={"job": job, "state": state.get("state")},
                )
            job_dir = store.job_dir(job)
            name = "surfaces.stkmsh"
            # **A read-only cache lookup must not CREATE anything.** This used
            # `extract.artifacts_dir`, which does `mkdir(parents=True, exist_ok=True)` — so asking
            # whether an artifact exists made a folder, and it followed a symlinked `artifacts` to
            # do it. The paths come from `extract.cached_artifact`, which goes through the same
            # containment check as the writes.
            #
            # **And only an artifact the agent's own record says it wrote is trusted.** The file
            # and `index.json` sit on disk where a pre-staged or planted copy would look exactly
            # like a cached one; the record (`.records/`, agent-written) is the proof, and the
            # file must still match the size and digest recorded there.
            recorded = store.recorded_artifact(job, name)
            cached = extract.cached_artifact(job_dir, name) if recorded is not None else None
            if (
                cached is not None
                and recorded is not None
                and (cached["size"], cached["sha256"]) == (recorded["size"], recorded["sha256"])
            ):
                return {
                    "artifact": name,
                    "size": cached["size"],
                    "sha256": cached["sha256"],
                    "patches": cached["patches"],
                    "cached": True,
                }
            data, header = extract.extract_surfaces(job_dir)
            if len(data) > extract.MAX_ARTIFACT_BYTES:
                raise AgentTooLarge(
                    f"the artifact would be {len(data) / 2**30:.1f} GiB",
                    details={"job": job, "bytes": len(data)},
                )
            written = extract.write_artifact(job_dir, name, data)
            store.record_artifact(job, name, written["size"], written["sha256"])
            return {
                "artifact": name,
                "size": written["size"],
                "sha256": written["sha256"],
                "patches": header["patches"],
                "cached": False,
            }

        try:
            return await asyncio.to_thread(work)
        except (AgentBadParams, AgentTooLarge, AgentNotReady):
            raise
        except FileNotFoundError as exc:
            raise AgentNotFound(str(exc), details={"job": job}) from exc
        except ValueError as exc:
            raise AgentBadParams(str(exc), details={"job": job}) from exc

    async def _artifact_get(
        self, job: str, name: str, offset: int = 0, length: int = 8 * 1024 * 1024
    ) -> dict[str, Any]:
        """A chunk of an artifact, as bytes.

        The chunk size is capped by `extract.read_chunk`, and the reply carries the **whole file's**
        size and sha256 so a client can verify what it assembled rather than each piece.
        """
        from stokes_agent import extract

        def work() -> dict[str, Any]:
            job_dir = self._job_store().job_dir(job)
            found = extract.read_chunk(job_dir, name, offset, length)
            found["job"] = job
            return found

        try:
            return await asyncio.to_thread(work)
        except FileNotFoundError as exc:
            raise AgentNotFound(str(exc), details={"job": job, "name": name}) from exc
        except ValueError as exc:
            raise AgentBadParams(str(exc), details={"job": job, "name": name}) from exc

    async def _slice_cut(
        self,
        job: str,
        plane: dict[str, Any],
        time: float | None = None,
    ) -> dict[str, Any]:
        """Cut one plane through a solved case. **No patches, so no surfaces artifact.**

        The slices contract needs any plane, on demand. `extract.fields` cuts one too, but it
        re-reads every boundary patch to do so - work that has nothing to do with the plane and is
        the expensive part. This op reads the internal mesh only, which is why a first cut costs
        seconds rather than the full extraction.

        `time` defaults to the reader's latest, so a caller with no opinion gets the same time
        `extract.fields` would have used for the same case.

        **`plane` is validated by the same `_slice_plane` `extract.fields` uses**, so a NaN, an
        infinity or a zero normal is refused identically on both paths - the two cannot drift.
        """
        from stokes_agent import extract

        wanted = _slice_plane(plane)

        def work() -> dict[str, Any]:
            store = self._job_store()
            state = store.status(job)
            if state.get("state") != "done":
                raise AgentNotReady(
                    f"the job is {state.get('state')!r}; its result can be cut once it is done",
                    details={"job": job, "state": state.get("state")},
                )
            job_dir = store.job_dir(job)
            # **The job's own engine picks the fields** (ADR-0027 step 4): an energy job's cut
            # carries `T` and absolute `p`, an incompressible one exactly what it always did.
            engine = extract.engine_of_job(state)
            cut = extract.cut_slice(
                job_dir,
                [float(v) for v in wanted["origin"]],
                [float(v) for v in wanted["normal"]],
                time,
                engine=engine,
            )
            # **Packed here, not shipped as arrays.** `pack_fields` is pure numpy and already
            # produces exactly the artifact the `.stkfld` reader understands, with no patches - so
            # the server writes what it receives and the cut travels as one blob rather than as a
            # structure the server would have to re-encode. `time` goes in because the header keeps
            # it and a client shows the time a cut belongs to.
            data, header = extract.pack_fields(
                [], cut, cut.get("time", 0.0), _cut_units(extract, engine, job_dir, cut)
            )
            if len(data) > extract.MAX_ARTIFACT_BYTES:
                raise AgentTooLarge(
                    f"the cut would be {len(data) / 2**30:.1f} GiB",
                    details={"job": job, "bytes": len(data)},
                )
            # **Written and recorded like every other artifact**, so the server fetches it by name
            # with its size and digest checked - the same path `extract.fields` takes. Returning the
            # bytes inline would be a second transport with no verification, which is how a
            # truncated cut would reach a client as a plausible-looking one.
            # **The agent names the file from the plane it was given, and does not hash it the way
            # the server's cache does.** The two are allowed to disagree about the name - the server
            # renames it into its own cache key on arrival - and they MUST, because the agent is a
            # separate side: it imports nothing from `stokes.*` (the layering rule), so sharing one
            # hashing function would mean shipping the server's module to the host.
            parts = (*wanted["origin"], *wanted["normal"])
            name = "slice-" + "-".join(f"{float(v):.6g}" for v in parts)
            name = name.replace(".", "_") + ".stkfld"
            written = extract.write_artifact(job_dir, name, data)
            store.record_artifact(job, name, written["size"], written["sha256"])
            # **The host keeps a SMALL LRU of slice artifacts** (Opus M4). Every distinct
            # plane wrote a new `slice-*.stkfld` that was **never removed**, so dragging a slice
            # across a model left hundreds of files with nothing to clean them: the server has its
            # own 16-plane cache and this side had none. The newest few are kept, because a drag
            # revisits its own recent planes constantly.
            _prune_slice_artifacts(job_dir, job, store)
            return {
                "artifact": name,
                "size": written["size"],
                "sha256": written["sha256"],
                "plane": wanted,
                "time": header["time"],
                "slice": {
                    "points": (header.get("slice") or {}).get("points", 0),
                    "triangles": (header.get("slice") or {}).get("triangles", 0),
                    "edges": (header.get("slice") or {}).get("edges", 0),
                },
            }

        return await asyncio.to_thread(work)

    async def _iso_cut(
        self,
        job: str,
        field: str,
        value: float,
        time: float | None = None,
    ) -> dict[str, Any]:
        """The surface where one scalar point field equals `value`, from a solved case.

        **The same contract as `slice.cut`**: no patches, a packed artifact written and recorded so
        the server fetches it with its size and digest checked, and the newest few kept. Only the
        surface differs - it is the contour of a field rather than a plane.

        **Refused here, before any read**: a non-finite value, a field name that is not a plain
        identifier, and a value the field never takes (an empty surface is not an error - it is
        reported with zero triangles - but a value the case never reached is the caller's question).
        """
        from stokes_agent import extract

        is_number = isinstance(value, (int, float)) and not isinstance(value, bool)
        if not is_number or not math.isfinite(value):
            raise AgentBadParams("the iso value must be a finite number", details={"job": job})
        if not (isinstance(field, str) and field.replace("_", "").isalnum()):
            raise AgentBadParams("the iso field must be a field name", details={"job": job})

        def work() -> dict[str, Any]:
            store = self._job_store()
            state = store.status(job)
            if state.get("state") != "done":
                raise AgentNotReady(
                    f"the job is {state.get('state')!r}; its result can be cut once it is done",
                    details={"job": job, "state": state.get("state")},
                )
            job_dir = store.job_dir(job)
            engine = extract.engine_of_job(state)
            try:
                cut = extract.cut_iso(job_dir, field, float(value), time, engine=engine)
            except ValueError as exc:
                raise AgentBadParams(str(exc), details={"job": job, "field": field}) from exc
            data, header = extract.pack_fields(
                [], cut, cut.get("time", 0.0), _cut_units(extract, engine, job_dir, cut)
            )
            if len(data) > extract.MAX_ARTIFACT_BYTES:
                raise AgentTooLarge(
                    f"the surface would be {len(data) / 2**30:.1f} GiB",
                    details={"job": job, "bytes": len(data)},
                )
            # **The name is built from the request, as the slice's is**, with the agent and the
            # server free to disagree about it: the server renames the file on arrival. **Hashed,
            # not spelled out**: an artifact name is lower case without `+`, so `iso-T-300` and
            # `iso-p-1e+05` were refused by `_contained` after the cut had been paid for.
            digest = hashlib.sha256(f"{field}|{float(value)!r}".encode()).hexdigest()
            name = f"iso-{digest[:24]}.stkfld"
            written = extract.write_artifact(job_dir, name, data)
            store.record_artifact(job, name, written["size"], written["sha256"])
            _prune_slice_artifacts(job_dir, job, store, prefix="iso-")
            return {
                "artifact": name,
                "size": written["size"],
                "sha256": written["sha256"],
                "field": field,
                "value": float(value),
                "time": header["time"],
                "surface": {
                    "points": (header.get("slice") or {}).get("points", 0),
                    "triangles": (header.get("slice") or {}).get("triangles", 0),
                    "edges": (header.get("slice") or {}).get("edges", 0),
                },
            }

        return await asyncio.to_thread(work)

    async def _stream_trace(
        self,
        job: str,
        seeds: list[list[float]] | None = None,
        direction: str = "forward",
        max_length: float | None = None,
        step_cells: float = 0.2,
        max_step_cells: float = 0.5,
        time: float | None = None,
        max_points: int = 1000,
        seed_patches: list[str] | None = None,
        seed_count: int = 50,
    ) -> dict[str, Any]:
        """Streamlines of a solved case's velocity through its 3-D volume (ADR-0030).

        **Answered in the reply, not as an artifact**: a trace is at most 200 lines of 1,000 points
        (a few MB as float32), and it is not worth a file to fetch. `seeds` are `[x, y, z]` points
        in metres, or `seed_patches` names boundary patches (the inlets) to spread `seed_count`
        seeds over; the step is in cells and the length in metres (`stokes_agent.trace`). Refused
        before any read: bad seeds, a bad direction, a non-positive length, patch names that are
        not names.
        """
        import numpy as np

        from stokes_agent import extract, trace

        patches: list[str] | None = None
        if seed_patches:
            if not isinstance(seed_patches, list) or not all(
                isinstance(name, str) and name for name in seed_patches
            ):
                raise AgentBadParams("seed_patches must be a list of patch names")
            patches = list(seed_patches)[: trace.MAX_SEEDS]
        else:
            try:
                trace._check(np.asarray(seeds, dtype=float), direction, max_length)
            except (trace.TraceInvalid, ValueError, TypeError) as exc:
                raise AgentBadParams(str(exc), details={"job": job}) from exc

        def work() -> dict[str, Any]:
            store = self._job_store()
            state = store.status(job)
            if state.get("state") != "done":
                raise AgentNotReady(
                    f"the job is {state.get('state')!r}; its result can be traced once it is done",
                    details={"job": job, "state": state.get("state")},
                )
            engine = extract.engine_of_job(state)
            try:
                return trace.trace_case(
                    store.job_dir(job),
                    None if patches else np.asarray(seeds, dtype=float),
                    time=time,
                    engine=engine,
                    seed_patches=patches,
                    seed_count=int(seed_count),
                    direction=direction,
                    max_length=max_length,
                    step_cells=float(step_cells),
                    max_step_cells=float(max_step_cells),
                    max_points=max(2, min(int(max_points), 1000)),
                )
            except (trace.TraceInvalid, ValueError) as exc:
                raise AgentBadParams(str(exc), details={"job": job}) from exc

        return await asyncio.to_thread(work)

    async def _vector_sample(
        self, job: str, count: int = 400, time: float | None = None
    ) -> dict[str, Any]:
        """The velocity on an even lattice through a solved case's 3-D volume (`sample`).

        Answered in the reply like `stream.trace`: at most 2,000 points and vectors as float32
        bytes, with the lattice spacing in metres.
        """
        from stokes_agent import extract, sample

        if not isinstance(count, int | float) or not 0 < count <= 100_000:
            raise AgentBadParams("count must be a positive number of samples")

        def work() -> dict[str, Any]:
            store = self._job_store()
            state = store.status(job)
            if state.get("state") != "done":
                raise AgentNotReady(
                    f"the job is {state.get('state')!r}; its result can be sampled once it is done",
                    details={"job": job, "state": state.get("state")},
                )
            engine = extract.engine_of_job(state)
            try:
                return sample.sample_case(store.job_dir(job), int(count), time=time, engine=engine)
            except (sample.SampleInvalid, ValueError) as exc:
                raise AgentBadParams(str(exc), details={"job": job}) from exc

        return await asyncio.to_thread(work)

    async def _mesh_fidelity(
        self,
        job: str,
        characteristic_length: float,
        cad_surfaces: list[str],
        cad_volume: float | None = None,
        cell_size: float | None = None,
    ) -> dict[str, Any]:
        """Measure the finished mesh's boundary against the CAD it came from.

        **A separate call rather than a step**, because it is not part of meshing: it reads a
        finished job and answers a question about it. Keeping it out of `job.create`'s steps means a
        fidelity measurement that fails cannot mark a successful mesh as failed - `surfaceFeatures`
        or `snappyHexMesh` failing IS a failed mesh, but this is a report about one.

        ``cad_surfaces`` names the staged surfaces under `constant/geometry/` - the same files
        `snappyHexMeshDict` refined against, so the comparison is against what the mesh was actually
        built from rather than the source CAD, whose tessellation may differ.

        **ALL of them, not one.** A snappy case stages one STL per boundary tag, so `inlet.stl` is
        the inlet patch alone: measuring the whole boundary against it reported a max deviation of
        500 mm on a 500 mm duct, which is the patch-to-patch distance and means nothing. The union
        of every staged surface is the CAD the mesh was made from.

        Runs off the event loop: a 5 M-cell boundary takes seconds to measure.
        """
        import numpy as np

        from stokes_agent import extract, fidelity

        def work() -> dict[str, Any]:
            store = self._job_store()
            state = store.status(job)
            if state.get("state") != "done":
                raise AgentNotReady(
                    f"the job is {state.get('state')!r}; fidelity is measured once it is done",
                    details={"job": job, "state": state.get("state")},
                )
            job_dir = store.job_dir(job)
            # **The staged path goes through `jobpaths`**, which is the one place the staging
            # whitelist is enforced; a hand-built path here would be a second copy of a security
            # rule, and this argument arrives from a client.
            from stokes_agent.jobpaths import job_path

            if not cad_surfaces:
                raise AgentBadParams(
                    "no CAD surfaces were given; fidelity needs the surfaces the mesh "
                    "was built from"
                )
            points: list[Any] = []
            triangles: list[Any] = []
            offset = 0
            for name in cad_surfaces:
                cad_p, cad_t = extract.read_stl(job_path(job_dir, name))
                points.append(cad_p)
                # **Indices are per file**, so each surface's triangles are shifted by the points
                # already collected or every surface after the first refers to the wrong vertices.
                triangles.append(cad_t + offset)
                offset += len(cad_p)
            mesh_points = extract.boundary_points(job_dir)
            measured = fidelity.measure(
                mesh_points=mesh_points,
                mesh_triangles=[],
                cad_points=np.concatenate(points, axis=0),
                cad_triangles=np.concatenate(triangles, axis=0),
                characteristic_length=characteristic_length,
                cad_volume=cad_volume,
                cell_size=cell_size,
            )
            return measured.to_json()

        return await asyncio.to_thread(work)

    async def _extract_fields(
        self,
        job: str,
        surfaces_job: str | None = None,
        # **`slice` is the wire's key**, from the brief's method table, so the handler's parameter
        # is named after it: the protocol dispatches on keyword names, and renaming it here would
        # make the documented method answer "bad parameters: unexpected keyword".
        slice: dict[str, Any] | None = None,  # noqa: A002
        time: float | None = None,
    ) -> dict[str, Any]:
        """Extract the job's point fields and one cut plane into one artifact, or return the cached
        one.

        ``time`` picks a written time (the nearest the case has), or the latest when null; the
        reply's `times` lists every time the case wrote.

        `surfaces_job` is the job whose `artifacts/index.json` holds the surfaces header — the mesh
        job, which `copy_from`s the same `polyMesh` as this solve job, so the two agree by
        construction. It may equal `job`, which is the ordinary case when one job meshed and solved.

        **Slow work off the event loop**, exactly as `extract.surfaces` is: reading a solved case is
        seconds of VTK, and blocking the loop would stall every other client including a health
        check.

        ``slice`` is `{"origin": [x, y, z], "normal": [x, y, z]}` or null for the default plane.
        """
        from stokes_agent import extract

        if time is not None and (
            isinstance(time, bool) or not isinstance(time, int | float) or not math.isfinite(time)
        ):
            raise AgentBadParams("time must be a finite number or null", details={"job": job})

        def work() -> dict[str, Any]:
            store = self._job_store()
            state = store.status(job)
            if state.get("state") != "done":
                raise AgentNotReady(
                    f"the job is {state.get('state')!r}; "
                    "its fields can be extracted once it is done",
                    details={"job": job, "state": state.get("state")},
                )
            job_dir = store.job_dir(job)
            mesh_job = surfaces_job or job
            mesh_dir = store.job_dir(mesh_job)
            # **The surfaces header is the authority on patch order and point counts**, so it is
            # read (or produced) before anything else: extracting fields against a mesh job with no
            # surfaces yet must fail on the missing surfaces, and finding out after the solve has
            # been read would waste the whole read.
            name = "surfaces.stkmsh"
            recorded = store.recorded_artifact(mesh_job, name)
            cached = extract.cached_artifact(mesh_dir, name) if recorded is not None else None
            if cached is None:
                surfaces_data, surfaces_header = extract.extract_surfaces(mesh_dir)
                if len(surfaces_data) > extract.MAX_ARTIFACT_BYTES:
                    raise AgentTooLarge(
                        f"the surfaces artifact would be {len(surfaces_data) / 2**30:.1f} GiB",
                        details={"job": mesh_job, "bytes": len(surfaces_data)},
                    )
                written = extract.write_artifact(mesh_dir, name, surfaces_data)
                store.record_artifact(mesh_job, name, written["size"], written["sha256"])
                surface_patches = surfaces_header["patches"]
            else:
                surface_patches = cached["patches"]

            plane = None
            if slice is not None:
                plane = _slice_plane(slice)

            data, header = extract.extract_fields(
                job_dir,
                {"patches": surface_patches},
                plane,
                engine=extract.engine_of_job(state),
                time=time,
            )
            if len(data) > extract.MAX_ARTIFACT_BYTES:
                raise AgentTooLarge(
                    f"the artifact would be {len(data) / 2**30:.1f} GiB",
                    details={"job": job, "bytes": len(data)},
                )
            written = extract.write_artifact(job_dir, "fields.stkfld", data)
            store.record_artifact(job, "fields.stkfld", written["size"], written["sha256"])
            result: dict[str, Any] = {
                "artifact": "fields.stkfld",
                "size": written["size"],
                "sha256": written["sha256"],
                "time": header["time"],
                "times": header.get("times") or [],
                "units": header.get("units") or {},
                "patches": [
                    {
                        "name": entry["name"],
                        "index": entry["index"],
                        "points": entry["points"],
                        "fields": list(entry["fields"]),
                    }
                    for entry in header["patches"]
                ],
                "slice": None,
            }
            if header["slice"] is not None:
                result["slice"] = {
                    "points": header["slice"]["points"],
                    "triangles": header["slice"]["triangles"],
                    "edges": header["slice"]["edges"],
                }
            return result

        try:
            return await asyncio.to_thread(work)
        except (AgentBadParams, AgentTooLarge, AgentNotReady):
            raise
        except FileNotFoundError as exc:
            raise AgentNotFound(str(exc), details={"job": job}) from exc
        except ValueError as exc:
            raise AgentBadParams(str(exc), details={"job": job}) from exc

    # ------------------------------------------------------------------ registry
    async def _host_probe(self, refresh: bool = False) -> dict[str, Any]:
        """The host record, computed off the event loop.

        ``refresh`` re-captures every environment rather than using the cache."""
        from stokes_agent.engine_env import host_record

        return await asyncio.to_thread(host_record, refresh)

    def _agent_info(self) -> dict[str, Any]:
        """The agent, the protocol, and **every method it has**.

        ``methods`` is what makes protocol changes additive-only in practice: a client asks
        what this
        agent can do instead of assuming, so an older agent answers a newer client with a list
        rather
        than an error."""
        return {
            "agent": AGENT_NAME,
            "protocol": protocol.PROTOCOL_VERSION,
            "methods": sorted(self._methods),
            "jobs_root": str(default_root()),
        }

    async def _agent_shutdown(self) -> dict[str, Any]:
        """Stop every job, then stop serving: the owner's close for a child agent.

        **Jobs first**, for the same reason `LocalRunner.close` does it in that order: a job must
        not outlive the agent that supervises it, and `stop()` deliberately leaves jobs running.

        **The reply is written before the stop event fires.** `_dispatch` writes the response after
        this returns, so stopping here would close the socket under the reply; the short delay lets
        the write reach the caller, which is the only thing that makes this a graceful shutdown
        rather than a dropped connection. A caller that does not get the reply in time can still
        terminate the process - the runner does exactly that.
        """
        await self.shutdown_jobs()
        asyncio.get_running_loop().call_later(0.25, self._stop.set)
        return {"stopping": True}

    def register(self, method: str, handler: Handler) -> None:
        self._methods[method] = handler

    # ------------------------------------------------------------------ jobs
    def _job_store(self) -> JobStore:
        """The supervisor, built on first use.

        **Lazy, and built once.** A server that only ever probes must not create a jobs
        directory, and two threads racing to build it must not each call `recover()` — which
        would kill a live job's process group twice and report it `interrupted` twice."""
        with self._jobs_lock:
            if self._jobs is None:
                self._jobs = JobStore(default_root(), on_event=self._job_event)
                # **Once, when the store is created** (ADR-0024): a job never outlives the agent
                # that started it, so anything a previous agent left `running` is killed and marked.
                self._jobs.recover()
            return self._jobs

    def _job_event(self, name: str, data: dict[str, Any]) -> None:
        """Called from a JOB THREAD. Schedules the broadcast and returns.

        **It must not block and must not raise.** This runs on the thread that is supervising a
        mesh: a slow consumer here would stall the solver's log, and a raise would kill the job. So
        the event is
        handed to the loop and any failure is logged and dropped."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            asyncio.run_coroutine_threadsafe(self.broadcast(name, data), loop)
        except (RuntimeError, ValueError):
            # The loop stopped between the check and the call, which is a shutdown race and not
            # an error.
            log.warning("job event dropped", extra=fields(event=name))

    def _steps_from(self, raw: Any) -> list[Step]:
        """The wire's step maps as `Step` objects, with every refusal the brief names."""
        if not isinstance(raw, list) or not raw:
            raise AgentBadParams("steps must be a non-empty list")
        if len(raw) > 32:
            raise AgentBadParams(f"a job has at most 32 steps; got {len(raw)}")
        known = {"name", "argv", "timeout_s", "parse", "parse_params", "ranks", "threads", "action"}
        steps: list[Step] = []
        for index, item in enumerate(raw):
            if not isinstance(item, dict):
                raise AgentBadParams(f"step {index} is not an object")
            unknown = set(item) - known
            if unknown:
                raise AgentBadParams(
                    f"step {index} has unknown keys: {', '.join(sorted(unknown))}; "
                    f"known: {', '.join(sorted(known))}"
                )
            name = item.get("name")
            argv = item.get("argv")
            if not isinstance(name, str):
                raise AgentBadParams(f"step {index} has no name")
            action = item.get("action")
            if action is not None and not isinstance(action, dict):
                raise AgentBadParams(f"step {index} action must be an object")
            if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
                raise AgentBadParams(f"step {index} argv must be a list of strings")
            timeout = item.get("timeout_s", 3600)
            if not isinstance(timeout, int) or isinstance(timeout, bool):
                raise AgentBadParams(f"step {index} timeout_s must be an integer")
            parse = item.get("parse")
            ranks = item.get("ranks", 1)
            if isinstance(ranks, bool) or not isinstance(ranks, int) or not 1 <= ranks <= 256:
                raise AgentBadParams(f"step {index} ranks must be an integer from 1 to 256")
            threads = item.get("threads", 1)
            if isinstance(threads, bool) or not isinstance(threads, int) or not 1 <= threads <= 256:
                raise AgentBadParams(f"step {index} threads must be an integer from 1 to 256")
            if parse is not None and not isinstance(parse, str):
                raise AgentBadParams(f"step {index} parse must be a string")
            params = item.get("parse_params") or {}
            if not isinstance(params, dict):
                raise AgentBadParams(f"step {index} parse_params must be an object")
            steps.append(
                Step(
                    name=name,
                    argv=tuple(argv),
                    timeout_s=timeout,
                    parse=parse,
                    parse_params=params,
                    ranks=ranks,
                    threads=threads,
                    action=action,
                )
            )
        return steps

    async def _job_create(
        self,
        files: dict[str, Any] | None = None,
        steps: list[Any] | None = None,
        install: str | None = None,
        label: str = "",
        copy_from: dict[str, Any] | None = None,
        profile: str | None = None,
    ) -> dict[str, Any]:
        """Stage a job. **Returns at once**: the job runs on the store's thread, never in this one.

        ``profile`` is null (a case Stokes wrote) or ``"external"`` (a case the user brought,
        ADR-0029): the store then judges files and steps against this agent's own probe of the
        install.

        ``install`` is an ENGINE ID from `host.probe` (`installs[].id`, `default_install_id`), or
        null for the default install. **Never a path**: the store refuses anything but an id the
        agent's own probe found.

        Writing a 20 MiB case is file work, so it goes through `to_thread` — a `job.create` that ran
        on
        the loop would block every other client, including a health check."""
        if files is None:
            files = {}
        if not isinstance(files, dict):
            raise AgentBadParams("files must be a map of path to bytes or str")
        total = 0
        for key, value in files.items():
            if not isinstance(key, str):
                raise AgentBadParams("every file key must be a string")
            if isinstance(value, bytes):
                total += len(value)
            elif isinstance(value, str):
                total += len(value.encode("utf-8"))
            else:
                raise AgentBadParams(f"file {key!r} must be bytes or str")
        if total > MAX_CREATE_BYTES:
            raise AgentTooLarge(
                f"the files total {total} bytes, over the {MAX_CREATE_BYTES}-byte limit; "
                "send large files with job.put",
                details={"bytes": total},
            )
        checked = self._steps_from(steps)
        if install is not None and not isinstance(install, str):
            raise AgentBadParams("install must be an engine id (a string) or null")
        if not isinstance(label, str):
            raise AgentBadParams("label must be a string")
        if copy_from is not None and not isinstance(copy_from, dict):
            raise AgentBadParams("copy_from must be an object or null")
        if profile is not None and not isinstance(profile, str):
            raise AgentBadParams("profile must be a string or null")

        def work() -> str:
            return self._job_store().create(
                files,
                checked,
                install=install,
                label=label,
                copy_from=copy_from,
                profile=profile,
            )

        try:
            job = await asyncio.to_thread(work)
        except JobBadParams as exc:
            raise _translate(exc) from exc
        except JobError as exc:
            raise _translate(exc) from exc
        return {"job": job}

    async def _job_put(
        self, job: str, path: str, data: bytes, offset: int, final: bool
    ) -> dict[str, Any]:
        """Append a chunk to a staged file."""
        if not isinstance(data, (bytes, bytearray)):
            raise AgentBadParams("data must be bytes")
        if len(data) > MAX_PUT_BYTES:
            raise AgentTooLarge(
                f"one job.put chunk is at most {MAX_PUT_BYTES} bytes; got {len(data)}",
                details={"bytes": len(data)},
            )
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise AgentBadParams("offset must be a non-negative integer")
        if not isinstance(final, bool):
            raise AgentBadParams("final must be a boolean")

        def work() -> int:
            return self._job_store().put(job, path, bytes(data), offset, final)

        try:
            size = await asyncio.to_thread(work)
        except JobBadParams as exc:
            raise _translate(exc) from exc
        except JobError as exc:
            raise _translate(exc) from exc
        return {"size": size}

    async def _job_start(self, job: str) -> dict[str, Any]:
        """Start the job. **It does not wait for it.**"""

        def work() -> str:
            return self._job_store().start(job)

        try:
            state = await asyncio.to_thread(work)
        except JobBadParams as exc:
            raise _translate(exc) from exc
        except JobError as exc:
            raise _translate(exc) from exc
        return {"job": job, "state": state}

    async def _job_cancel(self, job: str) -> dict[str, Any]:
        """Ask the job to stop. Returns whether the request changed anything."""

        def work() -> bool:
            return self._job_store().cancel(job)

        try:
            cancelled = await asyncio.to_thread(work)
        except JobBadParams as exc:
            raise _translate(exc) from exc
        except JobError as exc:
            raise _translate(exc) from exc
        return {"job": job, "cancelled": cancelled}

    async def _job_stop(self, job: str) -> dict[str, Any]:
        """Ask a running job's solver to write the current iteration and end (graceful Stop)."""

        def work() -> bool:
            return self._job_store().stop(job)

        try:
            stopping = await asyncio.to_thread(work)
        except JobError as exc:
            raise _translate(exc) from exc
        return {"job": job, "stopping": stopping}

    async def _job_times(self, job: str) -> dict[str, Any]:
        """The job's written time folders, oldest first (`0` excluded)."""

        def work() -> list[str]:
            return self._job_store().times(job)

        try:
            times = await asyncio.to_thread(work)
        except JobError as exc:
            raise _translate(exc) from exc
        return {"job": job, "times": times}

    async def _job_status(self, job: str) -> dict[str, Any]:
        """The job's state file, plus its reports."""

        def work() -> dict[str, Any]:
            return self._job_store().status(job)

        try:
            data = await asyncio.to_thread(work)
        except JobBadParams as exc:
            raise _translate(exc) from exc
        except JobError as exc:
            raise _translate(exc) from exc
        data.setdefault("reports", {})
        return data

    async def _job_list(self) -> dict[str, Any]:
        """Every job, newest first."""

        def work() -> list[dict[str, Any]]:
            return self._job_store().jobs()

        return {"jobs": await asyncio.to_thread(work)}

    async def _job_delete(self, job: str) -> dict[str, Any]:
        """Remove a finished job's folder and record. **A running job is refused.**"""

        def work() -> bool:
            return self._job_store().delete(job)

        try:
            deleted = await asyncio.to_thread(work)
        except JobBadParams as exc:
            raise _translate(exc) from exc
        except JobError as exc:
            raise _translate(exc) from exc
        return {"deleted": deleted}

    async def _job_read(self, job: str, path: str, max_bytes: int = 1_048_576) -> dict[str, Any]:
        """A text file inside a job folder, for a `postProcessing/` result."""

        def work() -> dict[str, Any]:
            return self._job_store().read(job, path, max_bytes)

        try:
            return await asyncio.to_thread(work)
        except JobBadParams as exc:
            raise _translate(exc) from exc
        except JobError as exc:
            raise _translate(exc) from exc

    async def _job_outputs(self, job: str) -> dict[str, Any]:
        """The files a job's function objects wrote under `postProcessing/` (T-311)."""

        def work() -> dict[str, Any]:
            return self._job_store().outputs(job)

        try:
            return await asyncio.to_thread(work)
        except JobBadParams as exc:
            raise _translate(exc) from exc
        except JobError as exc:
            raise _translate(exc) from exc

    async def _log_tail(self, job: str, step: str, lines: int = 200) -> dict[str, Any]:
        """The last lines of a step's log."""

        def work() -> dict[str, Any]:
            return self._job_store().tail(job, step, lines)

        try:
            return await asyncio.to_thread(work)
        except JobBadParams as exc:
            raise _translate(exc) from exc
        except JobError as exc:
            raise _translate(exc) from exc

    async def shutdown_jobs(self) -> None:
        """Cancel every running job and mark it interrupted. The OWNER calls this at exit.

        `stop()` deliberately does not: a server may be restarted while its jobs keep running,
        and the
        decision belongs to whoever owns the process (T-144c)."""
        if self._jobs is None:
            return
        await asyncio.to_thread(self._jobs.shutdown)

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> tuple[str, int]:
        self._server = await asyncio.start_server(self._handle, self.host, self.port)
        sockets = list(self._server.sockets or ())
        if sockets:
            self.port = int(sockets[0].getsockname()[1])
        # **The loop is recorded here** so a job thread can schedule its events onto it. Before
        # `start()` there is no loop to schedule onto, which is why `_job_event` checks for None.
        self._loop = asyncio.get_running_loop()
        # **The idle clock starts HERE**, not at construction and not at zero (Opus finding 3).
        # `time.monotonic()` is seconds since boot, so a `0.0` baseline made a fresh agent look
        # idle since the machine came up - and on this box, up 97 hours, it would have exited on its
        # first poll. An agent that has just started has been idle for no time at all.
        self._last_active = time.monotonic()
        self._heartbeat = asyncio.create_task(self._heartbeat_loop())
        log.info("agent listening", extra=fields(host=self.host, port=self.port))
        return self.host, self.port

    async def _heartbeat_loop(self) -> None:
        """A `heartbeat` every `HEARTBEAT_INTERVAL_S` **while a client is connected**.

        A client that hears nothing cannot tell a quiet agent from a dead one, and a mesh runs for
        minutes without saying anything. With no client there is no one to tell, so the task
        sleeps —
        which is also what keeps an idle agent from waking up every five seconds for ever."""
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=HEARTBEAT_INTERVAL_S)
                return
            except TimeoutError:
                pass
            if not self._writers:
                continue
            running: list[str] = []
            if self._jobs is not None:
                with contextlib.suppress(JobError):
                    running = [
                        str(item.get("id"))
                        for item in self._jobs.jobs()
                        if item.get("state") in ("queued", "running", "cancelling")
                    ]
            await self.broadcast(
                "heartbeat",
                {"t": datetime.now(UTC).isoformat(timespec="seconds"), "running": running},
            )

    async def serve_forever(self) -> None:
        if self._server is None:
            await self.start()
        # **The idle exit runs WITH the server** (Opus M1). The task is created here rather than in
        # `start` so a test that only starts and stops a server never leaves a timer behind, and it
        # is cancelled in `stop` for the same reason.
        if self._idle_exit_s is not None and self._idle_task is None:
            self._idle_task = asyncio.create_task(self._exit_when_idle())
        await self._stop.wait()
        await self.stop()

    async def _exit_when_idle(self) -> None:
        """Stop the agent once it has had no client and no job for `idle_exit_s`.

        **Both conditions, every time round** (the spec's rule): a client that is connected is a
        reason to live whatever the clock says, and a job that is running is a reason to live even
        with nobody watching. Checking only the clock would kill a mesh whose client had dropped,
        and checking only the jobs would leave a forgotten agent running for ever.

        **Polled rather than scheduled**, because the wake-up has to be re-evaluated: a client that
        connects resets the deadline, and a single timer set at start-up would fire into a server
        that is busy.
        """
        from stokes_agent import daemon

        # **A short idle timeout polls quickly, so a test need not wait a minute.** The poll is
        # at most a sixtieth of the timeout and never more than a minute.
        span = float(self._idle_exit_s or 60)
        interval = max(0.05, min(60.0, span / 60.0))
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
                return  # the stop event fired; `serve_forever` is already finishing
            except TimeoutError:
                pass
            if self._writers:
                # **AUTHENTICATED clients keep it alive**, and they say so by resetting the
                # clock. `_writers` is populated only AFTER `_greet` succeeds, which is exactly
                # the rule Opus asked for: **an unauthenticated TCP connection must NOT count as
                # a client**, or anyone who can reach the port holds the agent open for ever by
                # connecting and doing nothing. `_readers` was the wrong set - it holds every
                # connection from the moment it is accepted.
                self._last_active = self._now()
                continue
            if self._jobs_running():
                self._last_active = self._now()
                continue
            if daemon.should_exit_idle(
                now=self._now(),
                last_active=(self._last_active if self._last_active is not None else self._now()),
                idle_s=self._idle_exit_s or daemon.IDLE_EXIT_S,
            ):
                log.info(
                    "the agent is idle and is exiting",
                    extra=fields(seconds=self._idle_exit_s),
                )
                self._stop.set()
                return

    def _jobs_running(self) -> bool:
        """Whether any job this agent supervises is still going.

        **False when there is no store at all**, which is the probe-only client: it has no jobs, so
        it cannot have a running one, and the idle exit applies to it normally.
        """
        store = self._jobs
        if store is None:
            return False
        from stokes_agent.jobs import TERMINAL_STATES

        # **Best effort: an unreadable store does NOT keep the agent alive.** The two answers are
        # not symmetric - treating a broken store as "a job is running" would make the idle exit
        # never fire, which is the defect this exists to fix. A job supervises its own lifetime
        # through the supervisor, so being wrong here is cheap and the other direction is a
        # forgotten agent running for ever.
        with contextlib.suppress(Exception):
            return any(entry.get("state") not in TERMINAL_STATES for entry in store.jobs())
        return False

    async def stop(self) -> None:
        """Stop serving. **Jobs are deliberately left running** — `shutdown_jobs()` is the owner's
        call, made at exit.

        A server restart must not kill a mesh that is halfway through: the desktop decides
        whether it is
        exiting or reconnecting."""
        self._stop.set()
        # **The idle task is cancelled here**, or a test that stops a server would leave a task
        # waking up against a closed loop - the same reason the heartbeat is cancelled below.
        if self._idle_task is not None:
            self._idle_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._idle_task
            self._idle_task = None
        if self._heartbeat is not None:
            self._heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._heartbeat
            self._heartbeat = None
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        for writer in list(self._writers):
            writer.close()
        self._writers.clear()
        self._write_locks.clear()

    def request_stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------ connections
    async def _send(self, writer: asyncio.StreamWriter, message: dict[str, Any]) -> None:
        lock = self._write_locks.setdefault(writer, asyncio.Lock())
        async with lock:
            writer.write(protocol.encode(message))
            await writer.drain()

    async def broadcast(self, name: str, data: dict[str, Any]) -> None:
        """Send an event to every connected client, **dropping any that will not read**.

        A client that connects and never reads would otherwise fill its socket buffer, and the
        `drain()`
        would then wait for ever — taking the mesh's log stream with it, because broadcast is
        called from
        the job's event path. So each writer gets a bounded window and is closed on timeout."""
        message = protocol.event(name, data)
        for writer in list(self._writers):
            try:
                await asyncio.wait_for(self._send(writer, message), timeout=BROADCAST_TIMEOUT_S)
            except (TimeoutError, ConnectionError, OSError):
                log.warning("dropping a stalled client", extra=fields(event=name))
                self._writers.discard(writer)
                self._write_locks.pop(writer, None)
                with contextlib.suppress(Exception):
                    writer.close()

    async def _greet(self, writer: asyncio.StreamWriter, first: Any) -> bool:
        """Complete the handshake, or refuse.

        **The one place the agent decides who may talk to it.**

        Two paths, and **which one is allowed is a property of the mode, not of what arrived**:

        * **v3** - a nonce hello starts the mutual exchange. The agent proves itself first, then
          checks the client's proof. A host that does not hold the secret never gets a proof to
          attack, and a client that does not hold it is refused.
        * **v2** - a bare token, accepted **only in local mode**. A serve-mode agent refuses it with
          `protocol 3 expected`, which is what stops anyone reaching the port from skipping the
          mutual handshake by asking for the older version.

        Every refusal is the same message to the peer; only the local log distinguishes them.
        """
        from stokes_agent import handshake

        if not isinstance(first, dict) or first.get("kind") != "hello":
            await self._send(writer, protocol.error_response("", handshake.REFUSED))
            return False
        if not handshake.accepts(self.mode, first):
            # The message the peer gets: a serve-mode agent names the version it wants, everything
            # else gets the one indistinguishable refusal.
            message = handshake.refusal_for(self.mode, first)
            await self._send(writer, protocol.error_response("", message))
            return False
        if first.get("protocol") == protocol.LEGACY_PROTOCOL_VERSION:
            # **COMPARED AS BYTES** (Opus residue 7). `secrets.compare_digest` raises `TypeError`
            # for a `str` holding a non-ASCII character - measured: "comparing strings with
            # non-ASCII characters is not supported" - so a client sending `caf\u00e9` as its token
            # made the handler **die with a traceback instead of refusing it**. Measured: the
            # connection closed with no reply at all.
            #
            # A token is a sequence of bytes on the wire, so comparing bytes is both the correct
            # model and the one that cannot raise. `errors="replace"` means an undecodable token
            # becomes something that cannot equal the real one, which is the right answer.
            if not secrets.compare_digest(_token_bytes(first.get("token")), self.token.encode()):
                await self._send(writer, protocol.error_response("", handshake.REFUSED))
                return False
            await self._send(writer, protocol.hello_ok(AGENT_NAME))
            return True
        return await self._mutual(writer, first)

    async def _mutual(self, writer: asyncio.StreamWriter, first: dict[str, Any]) -> bool:
        """The v3 exchange: prove ourselves, then check the client (T-159a).

        **Order matters.** The agent sends its proof before reading the client's, so a caller
        that does not hold the secret learns nothing it could replay - and the reply carries the
        agent's own nonce, so a recorded exchange cannot be used twice.
        """
        from stokes_agent import handshake

        nonce_c = str(first.get("nonce") or "")
        if not nonce_c:
            await self._send(writer, protocol.error_response("", handshake.REFUSED))
            return False
        nonce_a = handshake.new_nonce()
        mine = handshake.agent_proof(self.token, nonce_c, nonce_a)
        await self._send(writer, protocol.hello_challenge(nonce_a, mine))
        try:
            reader = self._reader_for(writer)
            reply = await asyncio.wait_for(protocol.read_message(reader), timeout=10)
        except (TimeoutError, ProtocolError, ConnectionError, OSError):
            return False
        expected = handshake.client_proof(self.token, nonce_c, nonce_a)
        if not isinstance(reply, dict) or not handshake.check_proof(expected, reply.get("proof")):
            # **The same message for every failure.** Saying which half failed tells an attacker
            # which half to work on.
            await self._send(writer, protocol.error_response("", handshake.REFUSED))
            return False
        await self._send(writer, protocol.hello_proof(mine, AGENT_NAME))
        return True

    def _reader_for(self, writer: asyncio.StreamWriter) -> asyncio.StreamReader:
        """The reader paired with a writer.

        `asyncio.start_server` hands the handler both, and the handshake needs the reader after it
        has already read the first frame - so the pair is kept when the connection opens rather than
        threaded through every call.
        """
        return self._readers[writer]

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        self._readers[writer] = reader
        try:
            first = await asyncio.wait_for(protocol.read_message(reader), timeout=10)
            if not await self._greet(writer, first):
                log.warning(
                    "handshake rejected",
                    extra=fields(peer=str(peer), mode=self.mode.value),
                )
                return
            self._writers.add(writer)
            tasks: set[asyncio.Task[None]] = set()
            while True:
                message = await protocol.read_message(reader)
                if message is None:
                    break
                if message.get("kind") != "request":
                    continue
                task = asyncio.create_task(self._dispatch(writer, message))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
            for task in tasks:
                task.cancel()
        except (ProtocolError, TimeoutError, ConnectionError, OSError) as exc:
            log.warning("connection ended", extra=fields(peer=str(peer), reason=str(exc)))
        finally:
            self._writers.discard(writer)
            self._write_locks.pop(writer, None)
            # **AND THE READER, which was never removed** (Opus L1). Every connection left one entry
            # in `_readers` for the life of the process. Measured: five connections that closed
            # WITHOUT authenticating left `_readers` at 5 while `_writers` correctly fell to 0 - so
            # **anyone who can reach the port can grow this dict without a handshake**, which is the
            # cheapest possible denial of service against an agent meant to run unattended.
            self._readers.pop(writer, None)
            writer.close()

    async def _dispatch(self, writer: asyncio.StreamWriter, message: dict[str, Any]) -> None:
        request_id = str(message.get("id", ""))
        method = str(message.get("method", ""))
        params = message.get("params") or {}
        handler = self._methods.get(method)
        if handler is None:
            await self._send(
                writer, protocol.error_response(request_id, f"unknown method {method!r}")
            )
            return
        if not isinstance(params, dict):
            await self._send(writer, protocol.error_response(request_id, "params must be a map"))
            return
        try:
            result = handler(**params)
            if inspect.isawaitable(result):
                result = await result
            await self._send(writer, protocol.response(request_id, result))
        except StokesError as exc:
            await self._send(writer, protocol.error_response(request_id, exc))
        except TypeError as exc:
            await self._send(writer, protocol.error_response(request_id, f"bad parameters: {exc}"))
        except Exception as exc:
            log.exception("handler failed", extra=fields(method=method))
            await self._send(writer, protocol.error_response(request_id, f"internal error: {exc}"))


AwaitableHandler = Callable[..., Awaitable[Any]]

#: How many slice artifacts the AGENT keeps per job. **Smaller than the server's 16**: this side
#: answers the server's misses, the server decides what a user sees, and a host holding more than
#: the client can ask for is disk spent on nothing.
MAX_HOST_SLICE_ARTIFACTS = 8


def _prune_slice_artifacts(
    job_dir: Path, job: str, store: Any = None, prefix: str = "slice-"
) -> None:
    """Delete all but the newest `MAX_HOST_SLICE_ARTIFACTS` cut artifacts of one kind for a job.

    **Best effort, and it never raises.** A prune that failed must not fail a cut that succeeded:
    the artifact is written and recorded, and the disk it saves is worth less than the answer the
    user is waiting for.

    **Only files named `<prefix>*` are considered** (`slice-` for planes, `iso-` for iso-surfaces),
    so a run's own field artifact - which the server fetches for the whole results view - can never
    be deleted by a cut request.

    **Only files named `slice-*` are considered**, so a run own field artifact - which the
    `state.json`, so the record grew without limit and listed artifacts that no longer existed -
    **the disk was bounded and the bookkeeping was not**. A prune that removed the file and kept the
    record would be worse than no prune at all: it would make the index a list of lies.
    """
    removed: list[str] = []
    with contextlib.suppress(OSError, ValueError):
        folder = job_dir / "artifacts"
        candidates = sorted(
            (path for path in folder.glob(f"{prefix}*") if path.is_file()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for stale in candidates[MAX_HOST_SLICE_ARTIFACTS:]:
            with contextlib.suppress(OSError):
                stale.unlink()
                removed.append(stale.name)
    # **After the files, and separately suppressed**, because a store that is unavailable must not
    # stop the deletion that already happened.
    if removed and store is not None:
        with contextlib.suppress(Exception):
            store.forget_artifacts(job, removed)


def _cut_units(
    extract: Any, engine: str | None, job_dir: Path, cut: dict[str, Any]
) -> dict[str, str]:
    """A plane or iso cut's unit table: the engine's, or read from an imported case's files."""
    if engine != extract.EXTERNAL_ENGINE:
        return dict(extract.units_for(engine))
    names = sorted(cut.get("fields") or {})
    return dict(extract.case_units(job_dir, float(cut.get("time", 0.0)), names))
