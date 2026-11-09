"""How a run reaches a host: create the job, listen to it, finish it (ADR-0023, T-214b).

`FoamRunExecutor` is the one executor the skeleton has. It writes the case, stages it beside
the mesh
result, starts `foamRun`, and turns the agent's events into `run.*` ones.

## The listener does no work

`_on_event` is called on the **runner's event thread**, so it maps and forwards and returns.
Reading the
agent's files is a round trip, so it happens on a worker thread once the job is over — a
listener that
blocked would stall the reader for every other job on the host.

## The file is the authority, the events are the live view

The agent rate-limits its events, so a fast run's history **on the wire** is incomplete by
design. Once the
job is over this reads `monitors/residuals.jsonl` back — which has every block — and replaces
the in-memory
history with it. That is what makes a finished run's chart show the whole run.

## What a monitor file is worth, and what it is not

The controlDict's function objects write `postProcessing/<function>/<time>/<file>`, and the
last value in
each is what a user wants to see. **[VERIFY-OF14]**: the paths below were measured from
T-212's duct run,
not assumed. A file that is absent is absent from `monitors` rather than defaulted to zero — a
flow rate of
0 and no flow rate at all are different, and only one of them is true.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

from stokes.compile.of14 import fluid as fluid_writer
from stokes.compile.of14 import steps as solve_steps
from stokes.compile.of14 import write as solve_write
from stokes.log import fields, get_logger
from stokes.run import staging
from stokes.run.errors import SolveInvalid
from stokes.run.records import FINAL_STATES

if TYPE_CHECKING:  # pragma: no cover - typing only
    from stokes.api.session import Session
    from stokes.compile.snapshot import SolveSnapshot
    from stokes.run.records import RunRecord

log = get_logger(__name__)

#: The name this executor answers to, and the kind of run it makes (ADR-0023).
NAME = "of14.incompressibleFluid"
KINDS = ("simulation",)

#: Where the parser writes every block (T-213). Read back once the job ends.
HISTORY_FILE = "monitors/residuals.jsonl"

#: The function-object output a run leaves, and the key each becomes in `monitors`.
#:
#: **[VERIFY-OF14], measured on T-212's duct run.** `patchFlowRate` writes
# : `postProcessing/<name>/<time>/surfaceFieldValue.dat` with a `# Time` header and one row
# per write;
#: `patchAverage` writes the same shape for `p`. The directory name carries the function's ARGUMENTS
#: (`patchFlowRate(patch=inlet)`), so the patch is read from the file's own header line rather than
#: parsed out of the path.
MONITOR_FILES: tuple[tuple[str, str, str], ...] = (
    ("flow_rate", "patchFlowRate", "surfaceFieldValue.dat"),
    ("p_mean", "patchAverage", "surfaceFieldValue.dat"),
)

# : The most monitor files to read, and the most lines to take from each. A run writes one per
# patch per
#: function, and the last value is what is wanted.
MAX_MONITOR_FILES = 32
MAX_MONITOR_BYTES = 256 * 1024


class FoamRunExecutor:
    """ADR-0023's `RunExecutor` for `of14.incompressibleFluid`."""

    name = NAME
    kinds = KINDS

    def __init__(self, session: Session, store: Any) -> None:
        self._session = session
        self._store = store
        #: The snapshot per run in flight, so the listener can build the monitor paths.
        self._snapshots: dict[str, SolveSnapshot] = {}
        #: The runs already collected, so a second final event cannot finish one twice.
        self._collected: set[str] = set()
        #: Guards `_collected`, which the runner's event thread and a worker both touch.
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        """Whether a host can run this. Answered by the caller's probe, not here."""
        return True

    # ------------------------------------------------------------------ starting

    def start(
        self,
        *,
        run: RunRecord,
        snap: SolveSnapshot,
        runner: Any,
        host: str,
        engine: str | None,
        mesh_job: str,
        resume_job: str = "",
    ) -> dict[str, Any]:
        """Write the case, create the job, subscribe, and start it.

        **`copy_from` carries the mesh**, so the agent never re-meshes: the polyMesh is copied
        out of the
        finished mesh job into this one. Without it every solve would need its own mesh and
        the two could
        silently disagree.

        **`resume_job` continues a stopped run** (`run.resume`): the agent copies that job's newest
        time folder in beside the mesh (`latest_time_from`), and the controlDict starts from it
        (`startFrom latestTime`). Everything else is the case a fresh run writes.
        """
        files, steps = case_for(snap)
        copy_from: dict[str, Any] | None = None
        if mesh_job:
            copy_from = {"job": mesh_job, "paths": ["constant/polyMesh"]}
        if resume_job:
            from stokes.compile.of14.control import resume_control_text

            if copy_from is None:
                raise SolveInvalid(
                    "a run can only be resumed on a generated mesh", code="run.no_resume"
                )
            files["system/controlDict"] = resume_control_text(files["system/controlDict"])
            copy_from["latest_time_from"] = resume_job
        # **`staging.stage`**: a case carrying a large staged surface goes up in chunks through
        # `job.put`, so a solver run does not hit the agent's 32 MiB `job.create` cap either.
        created = staging.stage(
            runner.call,
            files,
            steps=steps,
            install=engine,
            label=run.id,
            copy_from=copy_from,
            timeout=300.0,
        )
        job = str(created["job"])
        self._store.bind_job(run.id, job)
        self._snapshots[run.id] = snap
        runner.subscribe(lambda name, data: self._on_event(run.id, job, name, data, snap))
        runner.call("job.start", job=job)
        return {"job": job, "steps": len(steps), "files": sorted(files)}

    # **A `cancel` method lived here and hardcoded `runner("local")`.** It had no callers - the
    # cancel path is `RunStore.cancel_run` -> the job's own agent - so it is deleted rather than
    # fixed: a method that would have signalled the LOCAL agent's job id for a remote job is worse
    # than absent, because the next person to wire it up would inherit a silent no-op. The five
    # live sites in `stokes/mesh/service.py` are fixed in place, where the host is resolvable.

    def reattach_and_collect(self, record: Any, state: str, snap: SolveSnapshot | None) -> None:
        """Collect a re-attached run the host already finished. **Opus review item 10.**

        The state comes from the host and the report, history and monitors come from the agent's
        files - the same three reads a live run makes when its job ends. `_collect` is deliberately
        not reused: it is guarded by `_collected`, and a re-attach is a new session's first
        look at a run it never saw start, so the guard would decide what it cannot know.
        """
        from stokes.run.hosts import HostRegistry

        def work() -> None:
            found: dict[str, Any] = {}
            history: list[dict[str, Any]] = []
            status: dict[str, Any] = {}
            try:
                runner = HostRegistry.of(self._session).runner(record.host or "local")
                status = runner.call("job.status", job=record.job, timeout=60.0)
                history = _read_history(runner, record.job)
                # **Monitors need the snapshot; the rest does not.** A re-attach whose project has
                # lost its mesh collects the state, the converged split and the residual
                # history, and goes without the monitor values rather than without all of it.
                if snap is not None:
                    found = _read_monitors(runner, record.job, snap)
            except Exception as exc:
                log.warning(
                    "could not read a re-attached run's files",
                    extra=fields(run=record.id, error=str(exc)[:200]),
                )
            report = solver_report(status)
            self._store.finish(
                record.id,
                state=state,
                report=report,
                history=history,
                monitors=found,
                status=status,
            )

        threading.Thread(target=work, name="stokes-run-reattach", daemon=True).start()

    def reattach(self, record: Any, snap: SolveSnapshot) -> None:
        """Take over a run this session did NOT start: one still going, or one already done.

        **Both cases go through the same collection as a live run** (Opus review item 10). Re-attach
        used to set the record's state and stop there, so a host-`done` job came back as `done` with
        **no `converged`, no history and no fields** - and `reusable()` then handed it to the next
        solve as though it had been collected. Routing it through `_collect` reads the same report,
        history and monitors a live run would, which is what makes a re-opened project show what
        actually happened.

        **A running job is re-subscribed**, not left alone. Nothing was listening to it, so it
        stayed `running` for ever: the events went to no one and the record never changed again.

        `_collected` deliberately does not stop this: a re-attach runs once per open, and the set is
        per-executor - a new session has a new executor with an empty set.
        """
        from stokes.run.hosts import HostRegistry

        self._snapshots[record.id] = snap
        try:
            runner = HostRegistry.of(self._session).runner(record.host or "local")
            runner.subscribe(
                lambda name, data: self._on_event(record.id, record.job, name, data, snap)
            )
        except Exception as exc:
            # **A subscription that fails must not lose the run.** The record stays as it is and the
            # caller is told; the alternative - raising - would make opening a project fail over one
            # job on a host that has gone away.
            self._store.update(record.id, error=f"run.reattach: {exc}")

    # ------------------------------------------------------------------ the runner's thread

    def _on_event(
        self, run_id: str, job: str, name: str, data: dict[str, Any], snap: SolveSnapshot
    ) -> None:
        """One agent event. **On the runner's thread: map it and return.**

        The store's `on_event` does the mapping; this only decides what needs a worker. A
        final state
        means the agent's files must be read, which is a round trip, so it is handed to a thread.
        """
        if str(data.get("job") or job) != job:
            return
        self._store.runner_event(run_id, name, {**data, "job": job})
        if name == "job.state" and data.get("step") is None:
            state = str(data.get("state") or "")
            if state in FINAL_STATES:
                self._collect(run_id, job, state, snap)

    def _collect(self, run_id: str, job: str, state: str, snap: SolveSnapshot) -> None:
        """Read the agent's files on a worker thread, then finish on the session thread.

        **At most once per run.** The agent emits a final `job.state` with `step: None` for
        the JOB as
        well as per-step ones, and a listener that acted on each would spawn two workers — the
        second
        reading a report the first had already consumed, and writing `converged: False` over a
        run that
        converged. `_collected` makes the collection idempotent, which is what a listener on a
        shared
        event stream has to be.
        """
        from stokes.run.hosts import HostRegistry

        with self._lock:
            if run_id in self._collected:
                return
            self._collected.add(run_id)

        def work() -> None:
            record = self._store.record(run_id)
            host = (record.host if record is not None else "") or "local"
            found: dict[str, Any] = {}
            history: list[dict[str, Any]] = []
            status: dict[str, Any] = {}
            try:
                runner = HostRegistry.of(self._session).runner(host)
                status = runner.call("job.status", job=job, timeout=60.0)
                history = _read_history(runner, job)
                found = _read_monitors(runner, job, snap)
            except Exception as exc:
                log.warning(
                    "could not read the run's files",
                    extra=fields(run=run_id, error=str(exc)[:200]),
                )
            report = solver_report(status)
            self._store.finish(
                run_id,
                state=state,
                report=report,
                history=history,
                monitors=found,
                status=status,
            )

        threading.Thread(target=work, name="stokes-run-collect", daemon=True).start()


def solver_report(status: dict[str, Any] | None) -> dict[str, Any]:
    """The solver step's report out of a job's status: `reports["solve"]` for a model run, else
    the LAST step's report, which is where the agent files an imported case's (`s2_foamRun`).

    **Measured in the tutorial sweep (2026-11-01):** every imported case finished
    `done_unconverged` with `iterations` taken from the history fallback, because this read
    `reports["solve"]` and the agent keys a case's reports by step name — the report, with its
    `ended` and `converged`, was never seen. The last report wins because the solve is the last
    step of every recipe (mesh steps come first); a status with no reports gives an empty one.
    """
    if not status:
        return {}
    reports = status.get("reports") or {}
    if not isinstance(reports, dict):
        return {}
    chosen = reports.get("solve")
    if isinstance(chosen, dict):
        return chosen
    # **The agent's status keeps the steps as RECORDS** (`{name, state, rc, …}`, `jobs.py`), not
    # names. Sweep 2 (2026-11-01) measured every finished case still `done_unconverged` because
    # `str(record)` matched no report key and the empty report won (finding 11).
    names = [
        str(s.get("name", "")) if isinstance(s, dict) else str(s) for s in status.get("steps") or []
    ] or list(reports)
    for name in reversed(names):
        found = reports.get(name)
        if isinstance(found, dict):
            return found
    return {}


def _read_history(runner: Any, job: str) -> list[dict[str, Any]]:
    """Every block the parser wrote, or [] when there is no file.

    **This is the complete history**, not the rate-limited event stream: the parser appends a
    line per
    completed block and flushes, so reading it back is what makes a finished run's chart whole.
    """
    import json

    try:
        reply = runner.call(
            "job.read", job=job, path=HISTORY_FILE, max_bytes=1024 * 1024, timeout=60.0
        )
    except Exception:
        return []
    blocks: list[dict[str, Any]] = []
    for raw in str(reply.get("text") or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            found = json.loads(line)
        except ValueError:
            continue
        if isinstance(found, dict) and "iteration" in found:
            blocks.append(found)
    return blocks


def _read_monitors(runner: Any, job: str, snap: SolveSnapshot | None) -> dict[str, Any]:
    """The last value of each monitor function, keyed by patch.

    **[VERIFY-OF14], measured from T-212's duct run**: the file is
    `postProcessing/<function>(<args>)/<time>/surfaceFieldValue.dat`, with the function's
    ARGUMENTS in
    the directory name (`patchFlowRate(patch=inlet)`), because `#includeFunc` has no `funcName=`.

    **The paths are BUILT, not discovered.** The agent offers `job.read` by exact path and no
    directory
    listing, and the names are not guesses: our own writer emits one flow-rate and one mean-pressure
    function per `monitor_patches` entry, so the executor knows exactly what to ask for. A
    `job.list_files`
    would be a new protocol call for a list this code already has.

    A function that wrote nothing leaves its key out rather than reporting zero: a flow rate
    of zero and
    no flow rate at all mean different things, and only one of them is true.
    """
    found: dict[str, dict[str, Any]] = {}
    if snap is None:  # an imported case's run: its function objects are not Stokes' own
        return found
    # **The energy writer averages `p` and `T` together** (`fields=(p T)`), and OpenFOAM 14 drops
    # the space in the directory name: `patchAverage(patch=outlet,fields=(pT))`, whose rows are
    # `time p T`. Asking for `fields=(p)` there missed the file (ADR-0027 step 5).
    averaged = ("p_mean", "T_mean") if snap.energy else ("p_mean",)
    fields = "".join(key.split("_")[0] for key in averaged)
    for patch in snap.monitor_patches:
        entry: dict[str, Any] = {}
        for keys, function, args in (
            (("flow_rate",), "patchFlowRate", f"patch={patch}"),
            # **No space after the comma.** The real directory is
            # `patchAverage(patch=inlet,fields=(p))`; writing `patch=inlet, fields=(p)` with a space
            # produced a path the agent correctly refused, so `p_mean` came back absent from
            # every run
            # and check 3 reported SKIPPED. Found by listing the job tree of a real solve.
            (averaged, "patchAverage", f"patch={patch},fields=({fields})"),
        ):
            for time_dir in _time_dirs():
                path = f"postProcessing/{function}({args})/{time_dir}/surfaceFieldValue.dat"
                values = _last_values(runner, job, path, len(keys))
                if values is not None:
                    entry.update(zip(keys, values, strict=True))
                    break
        if entry:
            found[str(patch)] = entry
    return found


def _time_dirs() -> tuple[str, ...]:
    """The time directories to try, newest-looking first.

    `write_interval` decides how many there are, and the LAST one holds the value a user
    wants. Trying
    a bounded set of candidates costs one refused call per miss, which is cheaper than a protocol
    addition — and a steady run's final time is its iteration count, so `0` is tried last.
    """
    return ("0",)


def _last_values(runner: Any, job: str, path: str, count: int) -> list[float] | None:
    """The last `count` numbers of the last data row of a `surfaceFieldValue.dat`, or None.

    The file is `# Time\\n<time>\\t<value>...` per write, one column per averaged field in the
    order the function names them, so the last row's last `count` fields are the values.
    """
    try:
        reply = runner.call(
            "job.read", job=job, path=path, max_bytes=MAX_MONITOR_BYTES, timeout=60.0
        )
    except Exception:
        log.debug("monitor %s unreadable", path, exc_info=True)
        return None
    for raw in reversed(str(reply.get("text") or "").splitlines()):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        pieces = line.split()
        if len(pieces) < count + 1:
            continue
        try:
            return [float(piece) for piece in pieces[-count:]]
        except ValueError:
            continue
    return None


def case_for(snap: SolveSnapshot) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """The case files and job steps for a snapshot, from the writer its engine names.

    `of14.fluid` (ADR-0027) goes to the energy writer; everything else to the incompressible
    writer, which refuses an engine it does not know. The catalogue still marks `of14.fluid`
    unbuilt, so no run reaches the first branch until ADR-0027 step 5.
    """
    if snap.engine == fluid_writer.ENGINE:
        return fluid_writer.write(snap), fluid_writer.steps(snap)
    return solve_write(snap), solve_steps(snap)


#: Registered executors, by the engine id they make a case for. One today.
EXECUTORS: dict[str, type[FoamRunExecutor]] = {NAME: FoamRunExecutor}


def executor_for(engine: str) -> type[FoamRunExecutor] | None:
    """The executor for an engine id, or None when this build cannot run it.

    `of14.external` (an imported case, ADR-0029) is looked up here rather than registered above,
    because its module builds on this one.
    """
    if engine == "of14.external":
        from stokes.run.external import ExternalCaseExecutor

        return ExternalCaseExecutor
    return EXECUTORS.get(engine)
