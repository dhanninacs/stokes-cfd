"""The ``run.*`` commands: start a solve, watch it, stop it (ADR-0023, T-214b).

Seven commands, and the shape of each is decided by one rule: **a run is an operation with a
lifetime,
not a parameter of the model**. So `run.start` writes no node and adds no undo step — `history.undo`
right after it undoes whatever model edit came before, which is what a user who changed a
velocity and
pressed Run expects.

## Why `run.cancel` is not a mutation

It changes no model state and adds no undo step: cancelling a run is not something a user wants to
*undo*, it is something they want to have done. `mesh.cancel` is `mutates=False` for the same
reason.

## Why `run.start` may return `skipped`

ADR-0024: a machine without OpenFOAM is a fact about the machine. **Recording the run as `skipped`
rather than refusing** means a journal recorded on a machine with OpenFOAM replays on one without,
reproducing the same project state instead of stopping. The refusal would be a replay that cannot be
replayed.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from stokes.api.registry import CommandContext, ParamDef, command
from stokes.engines.model import SETTINGS_PATH
from stokes.errors import StokesError
from stokes.log import fields
from stokes.mesh.errors import NO_INSTALL_ADVICE
from stokes.run.errors import SolveInvalid
from stokes.run.records import RunRecord

if TYPE_CHECKING:
    import numpy as np
from stokes.run.snapshot import build_solve_snapshot, solve_basis
from stokes.run.store import RUN_TIMEOUT_S, RunStore

log = logging.getLogger(__name__)

#: How long `wait=True` may block. A steady duct run is seconds; a real case is hours.
WAIT_LIMIT_S = RUN_TIMEOUT_S


def _store(ctx: CommandContext) -> RunStore:
    """The session's store, with its events going to the session.

    **`Session.emit` takes `(event, **data)` and the store emits `(event, dict)`**, so the
    adapter is
    here rather than a second `emit` signature on the session.
    """
    store = RunStore.of(ctx.session)
    store.bind(lambda name, data: ctx.session.emit(name, **data))
    return store


def _settings(ctx: CommandContext) -> Any:
    found = ctx.session.require_project().find(SETTINGS_PATH)
    if found is None:
        raise SolveInvalid(
            "there are no solver settings; run solve.configure first",
            code="solve.no_settings",
        )
    return found


def _mesh_status(ctx: CommandContext) -> dict[str, Any]:
    from stokes.mesh.service import MeshService

    return MeshService.of(ctx.session).status()


@command(
    "run.start",
    params=(
        ParamDef(
            "host", "string", required=False, description="The host; default: the settings' own."
        ),
        ParamDef("wait", "boolean", required=False, description="Wait for the run to finish."),
        ParamDef(
            "force", "boolean", required=False, description="Run even if a result is reusable."
        ),
        ParamDef(
            "run_id", "string", required=False, description="The id to use (journals replay this)."
        ),
        ParamDef(
            "case",
            "string",
            required=False,
            description="An imported OpenFOAM case (a foam_case node) to run instead of the model.",
        ),
        ParamDef(
            "mesh_only",
            "boolean",
            required=False,
            description="With case: run only the case's mesh steps (Generate in the Mesh step).",
        ),
    ),
)
def run_start(
    ctx: CommandContext,
    host: str | None = None,
    wait: bool = False,
    force: bool = False,
    run_id: str | None = None,
    case: str | None = None,
    mesh_only: bool = False,
) -> dict[str, Any]:
    """Start a solve of the current mesh, or return the run that already matches.

    The order matters and is the brief's: the settings, then the snapshot (whose `SolveInvalid`
    propagates, because a replay must hit the same refusal), then whether the host can run at
    all, then
    reuse, then the record. With `case`, an imported OpenFOAM case runs instead (`_start_case`).
    """
    from stokes.api.commands.compute import default_host
    from stokes.run.hosts import HostRegistry

    if case is not None:
        return _start_case(ctx, case, host, wait, force, run_id, mesh_only=mesh_only)
    if mesh_only:
        raise SolveInvalid(
            "mesh_only runs an imported case's mesh steps; a model's mesh is mesh.generate",
            code="run.invalid",
        )
    store = _store(ctx)
    settings = _settings(ctx)
    chosen = host or str(settings.params.get("host") or "") or default_host(ctx)
    snap = build_solve_snapshot(ctx.session, settings, _mesh_status(ctx))
    mesh_status = _mesh_status(ctx)
    mesh_result = str(mesh_status.get("result") or "")
    mesh_job = str(mesh_status.get("job") or "")
    earned = solve_basis(
        snap,
        str(mesh_status.get("basis") or ""),
        mesh_job,
        str(settings.params.get("engine") or ""),
    )

    registry = HostRegistry.of(ctx.session)
    runner = registry.runner(chosen)
    # **`probe` returns a WRAPPER — `{available, host, record, why}`** — and the installs live
    # inside
    # `record`. Reading `probe["default_install_id"]` returned None and every run came out
    # `skipped` on a
    # host that meshes fine. Found by running it against the real duct.
    probe = registry.probe(chosen)
    record = probe.get("record") or {}
    install = record.get("default_install_id")
    if not runner.available or not install:
        # **Recorded, not refused** (ADR-0024): a replay on a machine without OpenFOAM
        # reproduces the
        # same project instead of stopping at a step it cannot take.
        why = runner.why or str(record.get("why") or "") or NO_INSTALL_ADVICE
        record = store.add(
            RunRecord(
                id=run_id or store.new_id(),
                name=store.name_for(ctx.session.require_project()),
                state="skipped",
                engine=str(settings.params.get("engine") or ""),
                host=chosen,
                basis=earned,
                settings_id=str(settings.id),
                mesh_result=mesh_result,
                mesh_job=mesh_job,
                error=f"run.no_host: {why}",
            )
        )
        store._emit("run.skipped", {"run_id": record.id, "code": "run.no_host", "message": why})
        return {
            "run": record.id,
            "state": "skipped",
            "job": "",
            "basis": earned,
            "reused": False,
            "name": record.name,
        }

    if not force:
        reusable = store.reusable(earned)
        if reusable is not None:
            return {
                "run": reusable.id,
                "state": reusable.state,
                "job": reusable.job,
                "basis": reusable.basis,
                "reused": True,
                "name": reusable.name,
            }

    # **One run at a time.** A second solve would fight the first for the mesh and the host, so the
    # running one is stopped first — the same rule `mesh.generate` follows for its result.
    running = store.running()
    if running is not None:
        store.cancel_run(running.id)

    record = store.add(
        RunRecord(
            id=run_id or store.new_id(),
            name=store.name_for(ctx.session.require_project()),
            state="queued",
            engine=str(settings.params.get("engine") or ""),
            host=chosen,
            basis=earned,
            settings_id=str(settings.id),
            mesh_result=mesh_result,
            mesh_basis=str(mesh_status.get("basis") or ""),
            mesh_job=mesh_job,
        )
    )
    ctx.record(run_id=record.id)

    from stokes.run.executors import FoamRunExecutor

    executor = FoamRunExecutor(ctx.session, store)
    try:
        started = executor.start(
            run=record,
            snap=snap,
            runner=runner,
            host=chosen,
            engine=str(install),
            mesh_job=mesh_job,
        )
    except StokesError as exc:
        # **A refused start ends the run.** The agent refusing the staged case (its lint, a bad
        # flag) raised past a record already written as `queued`; nothing ever finished it, so the
        # project reopened on "Run · starting" with a Stop button for a run that never existed.
        _fail_unstarted(store, record.id, exc)
        raise
    if wait:
        _refuse_wait_on_the_session_thread(ctx)
        store.settle(WAIT_LIMIT_S)
    current = store.record(record.id)
    return {
        "run": record.id,
        "state": current.state if current is not None else "queued",
        "job": started["job"],
        "basis": earned,
        "reused": False,
        "name": record.name,
    }


def _start_case(
    ctx: CommandContext,
    case: str,
    host: str | None,
    wait: bool,
    force: bool,
    run_id: str | None,
    *,
    mesh_only: bool = False,
) -> dict[str, Any]:
    """`run.start` for an imported case (ADR-0029, T-309): the case's own files and recipe.

    With ``mesh_only`` only the recipe's mesh steps run (the Mesh step's Generate for a case with
    no CAD): the record's ``kind`` is ``mesh`` and its basis is the case's with ``:mesh``, so it is
    never handed back as a solved result, nor a solved result as a mesh.

    The same order as a model run: the case, then whether the host can run at all (a machine
    without OpenFOAM records `skipped`, so a journal replays), then reuse by basis, then the
    record. The reply adds `adapted` and `skipped`: what this version changed in the recipe or
    left out of the staged files.
    """
    from pathlib import Path

    from stokes.api.commands.case import _case_node, _project_folder
    from stokes.api.commands.compute import default_host
    from stokes.run.external import (
        ENGINE,
        ExternalCaseExecutor,
        ExternalRunInvalid,
        basis,
        engine_lacks,
    )
    from stokes.run.hosts import HostRegistry

    store = _store(ctx)
    node = _case_node(ctx, case)
    chosen = host or default_host(ctx)
    # The solve's cores are the solver settings' (the Run card's Cores); a mesh-only run is serial.
    cores = 1 if mesh_only else _case_cores(ctx)
    earned = basis(node, cores) + (":mesh" if mesh_only else "")
    kind = "mesh" if mesh_only else "simulation"
    registry = HostRegistry.of(ctx.session)
    runner = registry.runner(chosen)
    probe = registry.probe(chosen)
    record = probe.get("record") or {}
    install = record.get("default_install_id")
    name = store.name_for(ctx.session.require_project())
    if not runner.available or not install:
        why = runner.why or str(record.get("why") or "") or NO_INSTALL_ADVICE
        skipped = store.add(
            RunRecord(
                id=run_id or store.new_id(),
                kind=kind,
                name=name,
                state="skipped",
                engine=ENGINE,
                host=chosen,
                basis=earned,
                settings_id=node.id,
                error=f"run.no_host: {why}",
            )
        )
        store._emit("run.skipped", {"run_id": skipped.id, "code": "run.no_host", "message": why})
        return {
            "run": skipped.id,
            "state": "skipped",
            "job": "",
            "basis": earned,
            "reused": False,
            "name": skipped.name,
        }
    lacking = engine_lacks(node, record)
    if lacking is not None:
        raise ExternalRunInvalid(lacking, code="run.engine_lacks", details={"host": chosen})
    if not force:
        reusable = store.reusable(earned)
        if reusable is not None:
            return {
                "run": reusable.id,
                "state": reusable.state,
                "job": reusable.job,
                "basis": reusable.basis,
                "reused": True,
                "name": reusable.name,
            }
    running = store.running()
    if running is not None:
        store.cancel_run(running.id)
    added = store.add(
        RunRecord(
            id=run_id or store.new_id(),
            kind=kind,
            name=name,
            state="queued",
            engine=ENGINE,
            host=chosen,
            basis=earned,
            settings_id=node.id,
        )
    )
    ctx.record(run_id=added.id)
    executor = ExternalCaseExecutor(ctx.session, store)
    try:
        started = executor.start_case(
            run=added,
            node=node,
            project_dir=Path(_project_folder(ctx)),
            runner=runner,
            engine=str(install),
            mesh_only=mesh_only,
            cores=cores,
        )
    except Exception as exc:
        # **A refused case is a failed run, not a lost one**: the record says why, and the
        # error still reaches the caller with its own code.
        store.update(added.id, state="failed", error=f"{getattr(exc, 'code', 'run.start')}: {exc}")
        raise
    if wait:
        _refuse_wait_on_the_session_thread(ctx)
        store.settle(WAIT_LIMIT_S)
    current = store.record(added.id)
    return {
        "run": added.id,
        "state": current.state if current is not None else "queued",
        "job": started["job"],
        "basis": earned,
        "reused": False,
        "name": added.name,
        "steps": started["steps"],
        "adapted": started["adapted"],
        "skipped": started["skipped"],
    }


def _case_cores(ctx: CommandContext) -> int:
    """The solver settings' `cores` for an imported case's solve; 1 when unset or unreadable."""
    settings = ctx.session.require_project().find("/solver/settings")
    raw = settings.params.get("cores") if settings is not None else None
    if isinstance(raw, bool) or not isinstance(raw, int) or not 1 <= raw <= 256:
        return 1
    return raw


def _refuse_wait_on_the_session_thread(ctx: CommandContext) -> None:
    """**Waiting on the session thread would deadlock it.** The server IS that thread, so a
    `wait=True`
    from a client is refused with the events to follow instead."""
    from stokes.geometry.store import GeometryStore

    store = GeometryStore.of(ctx.session)
    if getattr(store, "_session_thread", None) == __import__("threading").get_ident():
        raise SolveInvalid(
            "wait=true would block the session thread; follow run.finished instead",
            code="run.busy",
        )


@command(
    "run.cancel",
    params=(
        ParamDef("run", "string", required=False, description="The run; default: the current one."),
    ),
    mutates=False,
)
def run_cancel(ctx: CommandContext, run: str | None = None) -> dict[str, Any]:
    """Ask a run to stop. **Not a mutation**: cancelling is not something a user undoes."""
    store = _store(ctx)
    return {"cancelled": store.cancel_run(run)}


@command(
    "run.stop",
    params=(
        ParamDef("run", "string", required=False, description="The run; default: the current one."),
    ),
    mutates=False,
)
def run_stop(ctx: CommandContext, run: str | None = None) -> dict[str, Any]:
    """Stop a run and KEEP what it solved: the solver writes the iteration it is on and ends.

    The run then finishes as a result (``done_unconverged``, ``stopped: true``) whose fields can be
    plotted and which `run.resume` can continue. A run that has no solver going yet is cancelled
    instead, as `run.cancel` does. ``stopping`` says which happened.
    """
    outcome = _store(ctx).stop_run(run)
    return {"stopping": outcome == "stopping", "cancelled": outcome == "cancelled"}


@command(
    "run.resume",
    params=(
        ParamDef(
            "run",
            "string",
            required=False,
            description="The finished run to continue; default: the newest one with a result.",
        ),
        ParamDef("host", "string", required=False, description="The host; default: the run's own."),
        ParamDef(
            "run_id", "string", required=False, description="The id to use (journals replay this)."
        ),
    ),
)
def run_resume(
    ctx: CommandContext,
    run: str | None = None,
    host: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Continue a stopped (or unconverged) run from the last iteration it wrote.

    A new run starts from the previous run's newest time folder (`startFrom latestTime`) on the
    same mesh, with the model's current settings, and runs to the settings' end - so a run
    stopped at 120 of 500 iterations goes on to 500, and raising Iterations first runs longer.
    Refused when the mesh has changed since, because the saved fields belong to the old mesh.
    """
    from stokes.api.commands.compute import default_host
    from stokes.run.executors import FoamRunExecutor
    from stokes.run.hosts import HostRegistry

    store = _store(ctx)
    previous = store.record(run) if run else store.newest_result()
    if previous is None or not previous.successful or not previous.job:
        raise SolveInvalid(
            "there is no finished run to resume; stop a run or let one finish first",
            code="run.no_resume",
        )
    settings = _settings(ctx)
    mesh_status = _mesh_status(ctx)
    mesh_job = str(mesh_status.get("job") or "")
    if not mesh_job or mesh_job != previous.mesh_job:
        raise SolveInvalid(
            "the mesh has changed since that run, so its fields no longer fit; start a new run",
            code="run.no_resume",
            details={"run": previous.id},
        )
    snap = build_solve_snapshot(ctx.session, settings, mesh_status)
    earned = solve_basis(
        snap,
        str(mesh_status.get("basis") or ""),
        mesh_job,
        str(settings.params.get("engine") or ""),
    )
    chosen = host or previous.host or default_host(ctx)
    registry = HostRegistry.of(ctx.session)
    runner = registry.runner(chosen)
    install = (registry.probe(chosen).get("record") or {}).get("default_install_id")
    if not runner.available or not install:
        raise SolveInvalid(
            runner.why or NO_INSTALL_ADVICE, code="run.no_host", details={"host": chosen}
        )
    running = store.running()
    if running is not None:
        store.cancel_run(running.id)
    record = store.add(
        RunRecord(
            id=run_id or store.new_id(),
            name=store.name_for(ctx.session.require_project()),
            state="queued",
            engine=str(settings.params.get("engine") or ""),
            host=chosen,
            basis=earned,
            settings_id=str(settings.id),
            mesh_result=str(mesh_status.get("result") or ""),
            mesh_basis=str(mesh_status.get("basis") or ""),
            mesh_job=mesh_job,
            resumed_from=previous.id,
        )
    )
    ctx.record(run_id=record.id)
    executor = FoamRunExecutor(ctx.session, store)
    try:
        started = executor.start(
            run=record,
            snap=snap,
            runner=runner,
            host=chosen,
            engine=str(install),
            mesh_job=mesh_job,
            resume_job=previous.job,
        )
    except StokesError as exc:
        _fail_unstarted(store, record.id, exc)
        raise
    current = store.record(record.id)
    return {
        "run": record.id,
        "state": current.state if current is not None else "queued",
        "job": started["job"],
        "basis": earned,
        "resumed_from": previous.id,
        "basis_changed": earned != previous.basis,
        "name": record.name,
    }


@command(
    "run.status",
    params=(
        ParamDef("run", "string", required=False, description="The run; default: the current one."),
    ),
    mutates=False,
)
def run_status(ctx: CommandContext, run: str | None = None) -> dict[str, Any]:
    """One run's record, plus the step and fraction the record itself does not hold."""
    return _store(ctx).status(run)


@command("run.list", params=(), mutates=False)
def run_list(ctx: CommandContext) -> dict[str, Any]:
    """Every run of this project, newest first."""
    return {"runs": _store(ctx).all_records()}


@command(
    "run.log",
    params=(
        ParamDef("run", "string", required=False, description="The run; default: the current one."),
        ParamDef("lines", "integer", required=False, description="How many lines, 1..5000."),
    ),
    mutates=False,
)
def run_log(ctx: CommandContext, run: str | None = None, lines: int = 200) -> dict[str, Any]:
    """The running step's log, through `log.tail` on the host."""
    from stokes.run.hosts import HostRegistry

    if not 1 <= int(lines) <= 5000:
        raise SolveInvalid(f"lines must be 1..5000; got {lines}", code="solve.invalid")
    found = _store(ctx).record(run)
    if found is None:
        from stokes.run.errors import RunNotFound

        raise RunNotFound(f"no run {run or '(current)'}")
    if not found.job:
        return {"step": "", "lines": [], "truncated": False}
    runner = HostRegistry.of(ctx.session).runner(found.host or "local")
    # **The solver's log first, then the step the run stopped in.** An imported case that failed
    # in `s2_foamRun` (or in its blockMesh step) answered an EMPTY log here, because the agent's
    # `solve` alias finds nothing for a step that never became the solve, and the Run card showed
    # nothing where the `FOAM FATAL ERROR` was (the tutorial sweep, 2026-11-01, finding 8).
    steps = ["solve"]
    # The step the run is in (or stopped in) is live state beside the record, not a field of it.
    current = str(_store(ctx).status(found.id).get("step") or "")
    if current and current not in steps:
        steps.append(current)
    for step in steps:
        try:
            reply = runner.call(
                "log.tail", job=found.job, step=step, lines=int(lines), timeout=30.0
            )
        except Exception as exc:
            # **A log that cannot be read is empty, not an error.** A step that has not written
            # one yet is the ordinary case at the start of a run.
            log.debug("no log for a step", extra=fields(run=found.id, step=step, error=str(exc)))
            continue
        found_lines = list(reply.get("lines") or [])
        if found_lines:
            return {"step": step, "lines": found_lines, "truncated": bool(reply.get("truncated"))}
    return {"step": "solve", "lines": [], "truncated": False}


@command(
    "run.residuals",
    params=(
        ParamDef("run", "string", required=False, description="The run; default: the current one."),
        ParamDef(
            "since", "integer", required=False, description="The iteration after which to return."
        ),
    ),
    mutates=False,
)
def run_residuals(ctx: CommandContext, run: str | None = None, since: int = 0) -> dict[str, Any]:
    """The residual history, for a chart. `since` is the iteration AFTER which to return.

    `loading: true` means a reopened project's finished run is having its history read back from its
    host; the blocks are empty until then and a client asks again.
    """
    return _store(ctx).residuals(run, since=int(since))


@command(
    "run.fields",
    params=(
        ParamDef("run", "string", required=False, description="The run; default: the current one."),
    ),
    mutates=False,
)
def run_fields(ctx: CommandContext, run: str | None = None) -> dict[str, Any]:
    """Which fields this run has, their ranges, and whether a slice was cut (T-216).

    **Reads the cache, never the host.** A finished run's fields were fetched once, when the job
    ended; asking a host again on every query would make a legend's numbers depend on whether the
    agent is still up. `surfaces: "missing"` is the honest answer for a project whose cache was
    cleaned up, and the results routes 404 from it.
    """
    return _store(ctx).fields_status(run)


def _run_host(ctx: CommandContext, run: str | None) -> tuple[Any, Any]:  # Any: record, runner
    """A run's record and its host's runner, refused before the run has a job."""
    from stokes.run.errors import RunNotFound
    from stokes.run.hosts import HostRegistry

    found = _store(ctx).record(run)
    if found is None:
        raise RunNotFound(f"no run {run or '(current)'}")
    if not found.job:
        raise SolveInvalid(f"run {found.id} has no job on a host", code="run.no_job")
    return found, HostRegistry.of(ctx.session).runner(found.host or "local")


@command(
    "run.monitors",
    params=(
        ParamDef("run", "string", required=False, description="The run; default: the current one."),
    ),
    mutates=False,
)
def run_monitors(ctx: CommandContext, run: str | None = None) -> dict[str, Any]:
    """The tables a run's function objects wrote (T-311): `{run, monitors: [{name, size}]}`.

    Each name is a file under the job's `postProcessing/` (forces, probes, flow rates, residuals
    written by the case itself); `run.monitor` reads one. Asks the host, so it answers what the
    job holds now, including while the run is going.
    """
    from stokes.run.monitors import is_table, monitor_name

    found, runner = _run_host(ctx, run)
    reply = runner.call("job.outputs", job=found.job, timeout=60.0)
    files = reply.get("files") if isinstance(reply, dict) else None
    monitors = [
        {"name": monitor_name(str(item["path"])), "size": int(item.get("size") or 0)}
        for item in files or []
        if isinstance(item, dict) and is_table(str(item.get("path") or ""))
    ]
    return {"run": found.id, "monitors": monitors}


@command(
    "run.monitor",
    params=(
        ParamDef("run", "string", required=False, description="The run; default: the current one."),
        ParamDef("name", "string", description="A monitor from run.monitors."),
    ),
    mutates=False,
)
def run_monitor(ctx: CommandContext, name: str, run: str | None = None) -> dict[str, Any]:
    """One monitor table: `{run, name, columns, values: {column: [floats]}, truncated}`.

    The first column is the time (or iteration) the function object wrote. A vector is one
    column per component (`U_x`, `U_y`, `U_z`). At most the first MiB of the file is read and at
    most 20,000 rows are kept, every n-th past that.
    """
    from stokes.run.monitors import MAX_TABLE_BYTES, is_table, parse_table

    found, runner = _run_host(ctx, run)
    if not is_table(name) or name.startswith(("/", "\\")) or ".." in name.split("/"):
        raise SolveInvalid(f"{name!r} is not a monitor of this run", code="run.no_monitor")
    reply = runner.call(
        "job.read",
        job=found.job,
        path=f"postProcessing/{name}",
        max_bytes=MAX_TABLE_BYTES,
        timeout=60.0,
    )
    table = parse_table(str(reply.get("text") or "") if isinstance(reply, dict) else "")
    out: dict[str, Any] = {"run": found.id, "name": name, **table.to_json()}
    out["truncated"] = bool(table.truncated or (isinstance(reply, dict) and reply.get("truncated")))
    return out


@command(
    "run.time",
    params=(
        ParamDef("run", "string", required=False, description="The run; default: the current one."),
        ParamDef(
            "time",
            "number",
            required=False,
            description="A written time (s) to show; the nearest the run wrote is used. "
            "Omit it for the latest.",
        ),
    ),
    mutates=False,
)
def run_time(
    ctx: CommandContext, run: str | None = None, time: float | None = None
) -> dict[str, Any]:
    """Show a finished run's fields at another written time (T-310).

    **Not a mutation**: which instant of a result is on screen is a view, like a camera, not model
    state. The fields are read on the host in the background; `run.changed` follows with the new
    field list, or `run.failed_extract` with the reason. The run's planes and iso-surfaces are cut
    again at the new time when next asked for. Refused with `run.not_finished` before the run ends.
    """
    import math
    import threading

    from stokes.run.errors import RunNotFound

    store = _store(ctx)
    found = store.record(run)
    if found is None:
        raise RunNotFound(f"no run {run or '(current)'}")
    if not found.successful:
        raise SolveInvalid(
            f"run {found.id} is {found.state}; its times can be shown once it has finished",
            code="run.not_finished",
        )
    if time is not None and not math.isfinite(float(time)):
        raise SolveInvalid("time must be a finite number", code="run.bad_time")
    chosen = None if time is None else float(time)
    threading.Thread(
        target=store.select_time,
        args=(found.id, chosen),
        name="stokes-run-time",
        daemon=True,
    ).start()
    return {"run": found.id, "time": chosen, "fetching": True}


def _probe_data(
    ctx: CommandContext, run: str | None, patch: str, field: str
) -> tuple[np.ndarray, np.ndarray]:
    """The probe's `(positions, values)`: **one component only**, because a line plot shows one.

    The component rule is the PROBE's, not the join's: a streamline needs all three of a vector, so
    `_patch_field` hands back whatever the field has and each command applies its own restriction.
    """
    from stokes.post.probe import ProbeInvalid

    positions, values = _patch_field(ctx, run, patch, field)
    if values.ndim != 1:
        raise ProbeInvalid(
            f"{field} has {values.shape[1]} components; probe one (speed, not velocity)"
        )
    return positions, values


def _slice_field(ctx: CommandContext, run: str | None, field: str) -> tuple[np.ndarray, np.ndarray]:
    """The SLICE's `(positions, values)` for `field`; see `_slice_cut` for the triangles too."""
    positions, _triangles, values = _slice_cut(ctx, run, field)
    return positions, values


def _slice_cut(
    ctx: CommandContext, run: str | None, field: str
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The SLICE's `(positions, triangles, values)` for `field` - **where a streamline can run**.

    Measured on the audit's duct: its walls carry |U| of exactly zero (no-slip) and its inlet and
    outlet are planes perpendicular to the flow, so **no patch of that model can carry a
    streamline**. A slice through the fluid can, and it is the surface a user is already looking at
    - so the command takes the word `slice` as a patch and reads the cache's slice entry
    (`SLICE_INDEX`, -1).
    """
    from stokes.post.probe import ProbeInvalid
    from stokes.run.fields import SLICE_INDEX, field_array, read_header, slice_arrays

    store = _store(ctx)
    status = store.fields_status(run)
    if status.get("surfaces") != "ok":
        raise ProbeInvalid("this run has no field data; press Show results first")
    if not status.get("slice"):
        raise ProbeInvalid("this run has no slice; add one in the Slices section first")
    data = memoryview(store.fields_path(str(status["run"])).read_bytes())
    header = read_header(data)
    positions, triangles, _edges = slice_arrays(data, header)
    values = field_array(data, header, SLICE_INDEX, field)
    if positions.shape[0] != values.shape[0]:
        raise ProbeInvalid(
            f"the slice has {positions.shape[0]} points but {values.shape[0]} values"
        )
    return positions, triangles, values


#: The most points a sampled line returns: a plot cannot show more, and each is one search of the
#: surface's triangles.
MAX_LINE_SAMPLES = 1000


def _slice_probe_data(ctx: CommandContext, run: str | None, field: str) -> tuple[Any, np.ndarray]:
    """The slice's own triangulation and the field's values on it, for `run.probe`.

    **The slice's triangles, not a fresh Delaunay**: the cut already has the mesh's own cells, so a
    point in a hole or past a concave edge is outside, as it should be (a Delaunay fill of the
    convex hull would answer a number for it). Wrapping the arrays costs nothing, so there is no
    background build and nothing to cache - which also means a moved slice can never be answered
    from the old one's triangulation.
    """
    from stokes.post.probe import ProbeInvalid
    from stokes.post.triangulation import triangulation_of

    positions, triangles, values = _slice_cut(ctx, run, field)
    if values.ndim != 1:
        raise ProbeInvalid(
            f"{field} has {values.shape[1]} components; probe one (speed, not velocity)"
        )
    if triangles.shape[0] == 0:
        raise ProbeInvalid("the slice misses the model; move it onto the fluid")
    return triangulation_of(positions, triangles), values


def _patch_field(
    ctx: CommandContext, run: str | None, patch: str, field: str
) -> tuple[np.ndarray, np.ndarray]:
    """One patch's `(positions, values)` with **whatever components the field has**.

    The shared half of `run.probe` and `run.streamlines`: a probe wants ONE component (a line plot
    shows one number) while a streamline needs all three of a vector. Splitting the join from the
    component rule is what lets both use it without either loosening the other's check.
    """
    """One patch's `(positions, values)` for `field`, from the two caches the probe joins.

    **Two services, matched by patch INDEX.** The values are the run's `.stkfld` cache, where a
    patch is an index in the header; the positions are the MESH's surfaces artifact, where a patch
    is a `mesh_patch` node carrying that same index. Neither knows about the other, which is why
    this join is here rather than in either.
    """
    from stokes.mesh.service import MeshService
    from stokes.mesh.surfaces import patch_arrays
    from stokes.post.probe import ProbeInvalid
    from stokes.run.fields import field_array, read_header

    store = _store(ctx)
    status = store.fields_status(run)
    if status.get("surfaces") != "ok":
        raise ProbeInvalid("this run has no field data; press Show results first")
    data = memoryview(store.fields_path(str(status["run"])).read_bytes())
    header = read_header(data)
    project = ctx.session.require_project()
    node = project.find(patch)
    if node is None or node.type != "mesh_patch":
        raise ProbeInvalid(f"{patch!r} is not a mesh patch of this model")
    index = int(node.params.get("index", -1))
    if index < 0:
        raise ProbeInvalid(f"the patch {node.name} has no index to match a field against")
    values = field_array(data, header, index, field)
    source = MeshService.of(ctx.session)._patch_source(patch)
    if source is None:
        # **Not "preparing"**: a missing surface file is a fact about the cache, not a build in
        # progress, and telling a user to retry would be telling them to wait for nothing.
        raise ProbeInvalid(
            f"the surface of {node.name} is not in the mesh cache; remesh or re-fetch"
        )
    _node, mesh_data, mesh_header = source
    positions, _triangles, _edges = patch_arrays(mesh_data, mesh_header, index)
    if positions.shape[0] != values.shape[0]:
        raise ProbeInvalid(
            f"{node.name} has {positions.shape[0]} points but {values.shape[0]} values"
        )
    return positions, values


@command(
    "run.probe",
    params=(
        ParamDef("run", "string", required=False, description="The run; default: the current one."),
        ParamDef(
            "patch", "string", description="The mesh patch (a mesh_patch node id) to read on."
        ),
        ParamDef("field", "string", description="A one-component field, e.g. 'U_mag' or 'p'."),
        ParamDef("point", "array", required=False, description="One [x, y, z] in SI metres."),
        ParamDef(
            "polyline",
            "array",
            required=False,
            description="Several [x, y, z] in SI metres, for a line plot along a line.",
        ),
        ParamDef(
            "n",
            "integer",
            required=False,
            default=0,
            description=(
                "With a polyline of exactly two points: sample this many evenly spaced points "
                "along the segment, ends included (2 or more; below that it is ignored)."
            ),
        ),
    ),
    mutates=False,
)
def run_probe(
    ctx: CommandContext,
    patch: str,
    field: str,
    run: str | None = None,
    point: list[float] | None = None,
    polyline: list[list[float]] | None = None,
    n: int = 0,
) -> dict[str, Any]:
    """The value of one field at one point, or along a polyline - **exactly, or "preparing"**.

    Claude's 2026-10-20 contract: *"a displayed probe value with units must be right, not within
    2.5 %"*, and *"show 'preparing', never a less accurate number silently"*. So this command has
    three answers and no fourth:

    * the values, from **barycentric interpolation** on the patch's triangulation;
    * `post.triangulation_preparing` while that triangulation is being built in the background - the
      first probe after opening results can say this, and a retry succeeds;
    * `post.probe_outside` for a point past the edge of the patch, never an extrapolation.

    **`slice` is a patch the caller can name** (as for `run.streamlines`): the probe then reads the
    current slice, on the slice's own triangles. **`n`** turns a two-point polyline into a line
    plot: `n` evenly spaced points from the first to the second, and the answer also carries
    `points` (where they are) and `along` (the distance from the first, metres).
    """
    # **`numpy` at the point of use**, as the repo's hot-path rule asks: the annotation can be
    # deferred (`TYPE_CHECKING`), but the body needs the real module.
    import numpy as np

    from stokes.post.probe import ProbeInvalid
    from stokes.post.triangulation import OutsideSurface, TriangulationNotReady

    if point is None and polyline is None:
        raise ProbeInvalid("give a point or a polyline")
    try:
        if patch == "slice":
            tri, values = _slice_probe_data(ctx, run, field)
        else:
            positions, values = _probe_data(ctx, run, patch, field)
            cache = _triangulation_cache()
            key = f"{run or 'current'}|{patch}"
            cache.request(key, positions)
            tri = cache.get(key)
        wanted = [point] if point is not None else list(polyline or [])
        asked = np.asarray(wanted, dtype=float)
        if asked.ndim != 2 or asked.shape[1] != 3:
            raise ProbeInvalid("each probe point is [x, y, z] in metres")
        sampled = point is None and asked.shape[0] == 2 and int(n) >= 2
        if sampled:
            count = min(int(n), MAX_LINE_SAMPLES)
            fractions = np.linspace(0.0, 1.0, count)[:, None]
            asked = asked[0] + fractions * (asked[1] - asked[0])
        answers = [tri.interpolate(p, values) for p in asked]
    except TriangulationNotReady:
        # **An answer, not a failure**: the surface is being prepared. The caller retries.
        raise
    except OutsideSurface as exc:
        # **Never extrapolated**: a value guessed past the edge of the data is one a user would
        # believe.
        raise ProbeInvalid(str(exc)) from exc
    answer: dict[str, Any] = {
        "patch": patch,
        "field": field,
        "values": answers,
        "units": "",
    }
    if sampled:
        steps = np.linalg.norm(np.diff(asked, axis=0), axis=1)
        answer["points"] = [[float(c) for c in p] for p in asked]
        answer["along"] = [0.0, *[float(d) for d in np.cumsum(steps)]]
    return answer


@command(
    "run.clear",
    params=(ParamDef("run", "string", description="The run to remove."),),
)
def run_clear(ctx: CommandContext, run: str) -> dict[str, Any]:
    """Remove a run's record, its folder, and the host's job."""
    from stokes.run.errors import RunNotFound
    from stokes.run.hosts import HostRegistry

    store = _store(ctx)
    found = store.record(run)
    if found is None:
        raise RunNotFound(f"no run {run}")
    if found.job and found.state not in (
        "done",
        "done_unconverged",
        "failed",
        "cancelled",
        "skipped",
    ):
        # **Logged, never silent.** A `run.clear` that could not stop a job must still remove the
        # record — the user asked for it gone — but swallowing the failure with `pass` would leave a
        # solver running with nothing on screen that says so, which is the one outcome worse than an
        # error message. `contextlib.suppress` is not used for that reason: this needs a log line.
        try:
            HostRegistry.of(ctx.session).runner(found.host or "local").call(
                "job.cancel", job=found.job, timeout=30.0
            )
        except Exception as exc:
            log.warning(
                "could not stop the job while clearing its run",
                extra={"run": run, "job": found.job, "error": str(exc)[:200]},
            )
    store.clear(run)
    return {"removed": run}


class _TriangulationCaches:
    """The process's triangulation cache, built on first use.

    **A class rather than a module global**, so ruff's `PLW0603` has nothing to complain
    about and the "one per process" fact is stated once: a background build is shared by
    every probe.
    """

    _one: Any = None

    @classmethod
    def get(cls) -> Any:
        if cls._one is None:
            from stokes.post.triangulation import TriangulationCache

            cls._one = TriangulationCache()
        return cls._one


def _triangulation_cache() -> Any:
    return _TriangulationCaches.get()


@command(
    "run.streamlines",
    params=(
        ParamDef("run", "string", required=False, description="The run; default: the current one."),
        ParamDef(
            "patch", "string", description="The mesh patch (a mesh_patch node id) to trace on."
        ),
        ParamDef("field", "string", description="A vector field, e.g. 'U'."),
        ParamDef(
            "seeds",
            "array",
            required=False,
            description="Where to start: [x, y, z] points in SI metres, one line per seed. "
            "Needed unless a volume trace names seed_patches.",
        ),
        ParamDef(
            "direction",
            "string",
            required=False,
            description="forward | backward | both (default forward).",
        ),
        ParamDef(
            "max_length",
            "number",
            required=False,
            description="How far a line may run, in metres (default 1).",
        ),
        ParamDef(
            "step",
            "number",
            required=False,
            description="Surface traces: the integration step in metres (default 0.005). Volume "
            "traces: the first step as a fraction of a cell (default 0.2).",
        ),
        ParamDef(
            "source",
            "string",
            required=False,
            description="volume (through the solved case's 3-D mesh, on its host) | slice | "
            "patch. Default: the patch given, as before (patch 'slice' is the cut).",
        ),
        ParamDef(
            "max_points",
            "number",
            required=False,
            description="Volume traces: most points a line returns (default 400, at most 1000).",
        ),
        ParamDef(
            "seed_patches",
            "array",
            required=False,
            description="Volume traces: boundary patch names (the inlets) the host spreads the "
            "seeds over, in place of seeds - shared evenly by patch, by area within one.",
        ),
        ParamDef(
            "count",
            "number",
            required=False,
            description="With seed_patches: how many lines (default 50, at most 200).",
        ),
    ),
    mutates=False,
)
def run_streamlines(
    ctx: CommandContext,
    patch: str,
    field: str,
    seeds: list[list[float]] | None = None,
    run: str | None = None,
    direction: str = "forward",
    max_length: float = 1.0,
    step: float = 0.005,
    source: str | None = None,
    max_points: int = 400,
    seed_patches: list[str] | None = None,
    count: int = 50,
) -> dict[str, Any]:
    """Streamlines through a VECTOR field on a patch - the direction a contour cannot show.

    **Seeds are given as points, and the four seed KINDS live in the client** (a point, a line, a
    plane and a rake are four ways to produce a list of points, and none of them needs the server's
    help). The parameter is the resolved list, so a caller that already has points - a slice plane's
    grid, a user's clicks - uses the same command.

    **Refused with the reason rather than truncated**: too many seeds, a line longer than the cap, a
    scalar field, or a direction that is not one of the three.
    """
    import numpy as np

    from stokes.post.streamlines import MAX_LINES, StreamlinesInvalid, streamlines

    if seed_patches is not None:
        if source != "volume":
            raise StreamlinesInvalid("seed_patches seed a volume trace; use source 'volume'")
        if not seed_patches or not all(isinstance(n, str) and n for n in seed_patches):
            raise StreamlinesInvalid("seed_patches must name at least one boundary patch")
        if not (isinstance(count, int | float) and 1 <= count <= MAX_LINES):
            raise StreamlinesInvalid(f"count must be 1 to {MAX_LINES} lines")
        return _volume_streamlines(
            ctx, run, field, None, direction, max_length, step, max_points, seed_patches, count
        )
    if not seeds:
        raise StreamlinesInvalid("no seeds: give at least one [x, y, z] point")
    if len(seeds) > MAX_LINES:
        raise StreamlinesInvalid(
            f"{len(seeds)} seeds is more than the {MAX_LINES} this draws; use fewer"
        )
    if source == "volume":
        return _volume_streamlines(ctx, run, field, seeds, direction, max_length, step, max_points)
    # **`slice` is a patch the caller can name**, because on a model whose walls carry no flow it is
    # the only surface a streamline can run on - measured on the audit's duct.
    positions, values = (
        _slice_field(ctx, run, field) if patch == "slice" else _patch_field(ctx, run, patch, field)
    )
    if values.ndim != 2 or values.shape[1] != 3:
        raise StreamlinesInvalid(f"{field} is not a vector field; streamlines need a direction")
    lines = streamlines(
        positions,
        values,
        np.asarray(seeds, dtype=float),
        max_length=float(max_length),
        step=float(step),
        direction=direction,
    )
    return {
        "patch": patch,
        "field": field,
        "lines": [
            {"points": [list(p) for p in line.points], "speeds": list(line.speeds)}
            for line in lines
        ],
    }


def _volume_streamlines(
    ctx: CommandContext,
    run: str | None,
    field: str,
    seeds: list[list[float]] | None,
    direction: str,
    max_length: float,
    step: float,
    max_points: int,
    seed_patches: list[str] | None = None,
    count: int = 50,
) -> dict[str, Any]:  # Any: JSON
    """`run.streamlines` through the 3-D volume, traced by the run's host (ADR-0030).

    Only the velocity `U` can be traced. `max_length` is metres (0 or less means the host's own
    default, three times the mesh's diagonal); `step` is the first step as a fraction of a cell. The
    seeds are the points given or, with `seed_patches`, `count` points the host spreads over those
    boundary patches. The lines come back as `{points, speeds, reason}` with the reason each
    stopped.
    """
    import numpy as np

    from stokes.post.streamlines import StreamlinesInvalid

    if field != "U":
        raise StreamlinesInvalid("only the velocity U can be traced through the volume")
    reply = _store(ctx).trace_streamlines(
        run,
        seeds,
        direction=direction,
        max_length=float(max_length) if max_length and max_length > 0 else None,
        step_cells=float(step) if 0 < step < 5 else 0.2,
        max_points=max(2, min(int(max_points), 1000)),
        seed_patches=seed_patches,
        seed_count=int(count),
    )
    points = np.frombuffer(reply["points"], dtype="<f4").reshape(-1, 3)
    speeds = np.frombuffer(reply["speeds"], dtype="<f4")
    offsets = [int(v) for v in reply["offsets"]]
    lines = [
        {
            "points": points[offsets[i] : offsets[i + 1]].tolist(),
            "speeds": speeds[offsets[i] : offsets[i + 1]].tolist(),
            "reason": reply["reasons"][i],
        }
        for i in range(int(reply["count"]))
    ]
    return {"patch": "volume", "field": field, "source": "volume", "lines": lines}


#: The most arrows `run.vectors` returns (the agent's own cap, `stokes_agent.sample`).
MAX_VOLUME_VECTORS = 2000


@command(
    "run.vectors",
    params=(
        ParamDef("run", "string", required=False, description="The run; default: the current one."),
        ParamDef(
            "count",
            "number",
            required=False,
            description=f"About how many arrows (default 400, at most {MAX_VOLUME_VECTORS}).",
        ),
    ),
    mutates=False,
)
def run_vectors(ctx: CommandContext, run: str | None = None, count: int = 400) -> dict[str, Any]:
    """The velocity on an even lattice THROUGH the solved volume - arrows that fill the fluid.

    Sampled by the run's host (`vector.sample`): a regular lattice sized to the fluid, the points
    outside it dropped, `U` interpolated at the rest. **Even in space, not chosen by speed**, so a
    slow region shows slow arrows rather than none. Answers `{points, vectors, spacing}` in SI
    (metres, m/s; `spacing` is the lattice step, an arrow's natural length).
    """
    import numpy as np

    from stokes.post.streamlines import StreamlinesInvalid

    if not (isinstance(count, int | float) and 1 <= count <= MAX_VOLUME_VECTORS):
        raise StreamlinesInvalid(f"count must be 1 to {MAX_VOLUME_VECTORS} arrows")
    reply = _store(ctx).sample_vectors(run, int(count))
    points = np.frombuffer(reply["points"], dtype="<f4").reshape(-1, 3)
    vectors = np.frombuffer(reply["vectors"], dtype="<f4").reshape(-1, 3)
    if points.shape != vectors.shape:
        raise StreamlinesInvalid("the host answered points and vectors of different lengths")
    return {
        "field": "U",
        "points": points.tolist(),
        "vectors": vectors.tolist(),
        "spacing": float(reply.get("spacing", 0.0)),
    }


@command(
    "run.iso_surface",
    params=(
        ParamDef("run", "string", required=False, description="The run; default: the current one."),
        ParamDef("field", "string", description="A scalar field the run has: 'p' or 'T'."),
        ParamDef(
            "value",
            "any",
            description="The iso value as a quantity, e.g. '300 [K]'; a bare number is SI.",
        ),
        ParamDef(
            "wait",
            "boolean",
            required=False,
            description="Cut now and answer with the surface (not from the server's own thread).",
        ),
    ),
    mutates=False,
)
def run_iso_surface(
    ctx: CommandContext,
    field: str,
    value: Any,  # Any: a JSON number (SI) or quantity text, checked by `stokes.run.iso`
    run: str | None = None,
    wait: bool = False,
) -> dict[str, Any]:
    """The surface where one scalar field equals a value, cut by the run's host (ADR-0026).

    **Refused before any cut**: a field that is not `p` or `T` or that the run does not have, a
    value that is not a quantity of the field's dimension, and a value outside the field's range
    (`results.iso_out_of_range`). A surface over `MAX_ISO_TRIANGLES` is refused with
    `results.iso_too_large` before its artifact is fetched.

    **Three answers**: a cached surface's summary (`state` `ready`, or `empty` when the contour
    found nothing); with `wait`, the same after cutting it now; otherwise `state: cutting`, and the
    summary follows as `run.iso_ready` (or `run.iso_failed`). The VTK work runs on the host in
    every case; the session thread never waits on it.
    """
    from stokes.run.iso import IsoUnavailable, result_reference
    from stokes.run.planes import iso_key

    store = _store(ctx)
    request = store.iso_request(run, field, value)
    found = store.iso_summary(request, cut=False)
    if found is not None:
        return found
    if not wait:
        store.iso_in_background(request)
        return {
            "run": request.run,
            "field": request.field,
            "value": request.value,
            "units": request.units,
            "state": "cutting",
            "range": {"min": request.low, "max": request.high},
            "key": iso_key(request.field, request.value),
            "result": result_reference(request),
        }
    _refuse_wait_on_the_session_thread(ctx)
    found = store.iso_summary(request)
    if found is None:
        raise IsoUnavailable(
            store.iso_failure(request) or "the run's host could not cut the surface",
            details={"run": request.run, "field": request.field},
        )
    return found


@command(
    "run.report",
    params=(
        ParamDef("run", "string", required=False, description="The run; default: the current one."),
        ParamDef(
            "notes",
            "string",
            required=False,
            description="A sentence for the report's head - the set-up, an author, a caveat.",
        ),
    ),
    mutates=False,
)
def run_report(ctx: CommandContext, run: str | None = None, notes: str = "") -> dict[str, Any]:
    """Write the run's results report as one self-contained HTML file, and answer where it is.

    **The report's facts are the API's own**: the field table is what `run.fields` answers, the
    iteration count is `run.residuals`', and the state is the run's record - so a report cannot
    disagree with the panel a user is looking at (`stokes/post/report.py`).

    **The file goes beside the run's field cache**, not a temporary one: a report is about the run,
    and a user who finds one expects the other. It is regenerated on every call - a report describes
    the run as it is now, and a stale report is worse than none.
    """
    from stokes.post.report import report_from_run, report_html
    from stokes.run.errors import RunNotFound

    store = _store(ctx)
    found = store.record(run)
    if found is None:
        raise RunNotFound(f"no run {run}")
    run_id = found.id
    # **`fields_status` is the same answer `run.fields` gives**, so the table is the panel's.
    status = store.fields_status(run_id)
    fields = {"fields": status.get("fields") or []}
    residuals = store.residuals(run_id)
    project = ctx.session.require_project()
    report = report_from_run(
        project=project.name,
        run_id=run_id,
        record=found,
        fields=fields,
        residuals=residuals,
        notes=notes,
    )
    html = report_html(report)
    # **Beside the run's field cache**, which is where its other derived data already lives.
    path = store.fields_path(run_id).with_suffix(".report.html")
    path.write_text(html, encoding="utf-8")
    return {"run": run_id, "path": str(path), "bytes": len(html.encode("utf-8"))}


def _fail_unstarted(store: RunStore, run_id: str, exc: StokesError) -> None:
    """Record a run whose start was refused as ``failed``, with the refusal as its error."""
    store.finish(
        run_id,
        state="failed",
        report={},
        history=(),
        monitors={},
        status={"error": {"code": exc.code, "message": str(exc)}},
    )
