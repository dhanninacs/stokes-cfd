"""The adjoint CFD cycle of a shape optimisation (ADR-0038 decision 5), as the ``solve`` callable
of :class:`stokes.explore.shape.AdjointObjective`.

One cycle is the whole pipeline a user would drive, on a private copy:

1. the live project's folder (its geometry caches, not its runs) is copied to a cycle folder, and
   the project with the cycle's variable values is written into it;
2. a private session opens it and runs the implicit operations again, so the part is rebuilt;
3. the part's boundary tags find their faces again (`stokes.geometry.retag`): a planar inlet or
   outlet by its plane, the walls as the rest;
4. the flow is meshed (`mesh.generate`) and the adjoint case run (`run.start` with the
   ``of14.adjoint`` engine) on the host the project names;
5. the agent computes J and the wall sensitivity where the case is (``job.sensitivity``): the
   solver's written ``shapeSensitivity`` when the case has it, else its reconstruction from U and
   Ua; the row's ``sensitivity_source`` says which (ADR-0041 decision 8).

With ``objective`` ``flow_split`` (ADR-0041) the adjoint case is written for ``adjointFoam``, the
open-source adjoint, with the flow-rate partition over ``outlets``; :func:`adjoint_program` says
whether the host has that program, and the commands refuse the adjoint before meshing when it does
not. With ``adjoint`` false (a finite-difference cycle, ADR-0040) step 4 runs the ordinary engine
and step 5 reads J and the port flows only; a cycle whose flows do not balance, or whose outlet
takes flow back in, is refused. The answer is a :class:`~stokes.explore.shape.CycleResult`.

The live project and session are never written to. A cycle that fails raises a typed error with
the step that failed; the optimiser stops on it. Each step is reported to ``on_step`` with the
cycle's number, the step's place in :data:`STEPS` and the seconds since the cycle began, so a client
can show what a long cycle is doing. :meth:`AdjointCycle.abort` stops the cycle under way: the mesh
or the run it is waiting on is cancelled and the cycle raises
:class:`~stokes.explore.errors.Cancelled`.

Headless: the API and the model; no Qt, VTK or OCP here (the workers do the geometry).
"""

from __future__ import annotations

import shutil
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from stokes.api.session import Session
from stokes.errors import StokesError
from stokes.geometry.errors import OperationInvalid
from stokes.log import fields, get_logger
from stokes.model.project import Project

log = get_logger(__name__)

COPIED = ("cache", "geometry")
"""What a cycle folder takes from the live project's folder: the geometry, not the runs."""
ADJOINT_ENGINE = "of14.adjoint"
PRIMAL_ENGINE = "of14.incompressibleFluid"
"""The engine of a cycle without an adjoint (finite differences, ADR-0040): the ordinary solve."""
MASS_IMBALANCE = 0.005
"""The largest |sum of the ports' flows| / |inflow| a cycle accepts (ADR-0040)."""
BACKFLOW = 0.01
"""The largest share of an outlet's |flux| that may enter the domain (ADR-0040)."""
OUTLET_KINDS = ("pressure_outlet",)
"""Boundary kinds whose patches are outlets for the flow split."""
STEPS = ("open", "rebuild", "tags", "mesh", "solve", "sensitivity")
"""The steps of one cycle, in order, as ``on_step`` names them."""

StepCallback = Callable[[str, dict[str, Any]], None]  # Any: JSON
"""``on_step(step, info)``: ``info`` holds ``cycle``, ``step_number``, ``steps`` and ``elapsed``."""


def adjoint_program(ctx: Any) -> dict[str, Any]:  # Any: CommandContext; JSON
    """Whether the host a cycle would run on has the flow split's adjoint program (ADR-0041):
    ``found`` true or false, or None when the host cannot be asked (``why`` says why), with the
    host's name and the sentence a refusal shows. Asks the cached probe, never a fresh one."""
    from stokes.api.commands.compute import default_host
    from stokes.run.hosts import (
        ADJOINT_PROGRAM,
        HostRegistry,
        adjoint_program_missing,
        install_utility,
    )

    project = ctx.session.require_project()
    settings = project.find("/solver/settings")
    host = str(settings.params.get("host") or "") if settings is not None else ""
    host = host or default_host(ctx)
    answer = HostRegistry.of(ctx.session).probe(host)
    record = answer.get("record")
    if not answer.get("available") or not isinstance(record, dict):
        why = str(answer.get("why") or "the host cannot be asked")
        return {"host": host, "program": ADJOINT_PROGRAM, "found": None, "why": why}
    if not record.get("default_install_id"):
        why = str(record.get("why") or "the host has no OpenFOAM install")
        return {"host": host, "program": ADJOINT_PROGRAM, "found": None, "why": why}
    path = install_utility(record, ADJOINT_PROGRAM)
    return {
        "host": host,
        "program": ADJOINT_PROGRAM,
        "found": path is not None,
        "why": "" if path is not None else adjoint_program_missing(host),
    }


class CycleFailed(StokesError):
    """One cycle of the adjoint loop could not finish; ``details.step`` says where."""

    code = "shape.cycle_failed"


def boundary_rules(session: Session, part: str) -> list[Any]:  # Any: retag.Rule
    """The rules that find the part's boundary tags again, taken from the live part."""
    from stokes.geometry.carry import tags_of
    from stokes.geometry.retag import rules_for
    from stokes.geometry.selection import load_members
    from stokes.geometry.store import GeometryStore

    project = session.require_project()
    mesh = GeometryStore.of(session).mesh(part)
    if mesh is None:
        raise OperationInvalid("the part has no mesh yet: run its operation first")
    tags: list[tuple[str, list[int]]] = []
    for tag in tags_of(project):
        raw = tag.params.get("members")
        members = load_members(raw) if isinstance(raw, str) and raw else ()
        for member in members:
            if member.part == part and member.faces is not None:
                tags.append((tag.id, [int(i) for i in member.ids(None)]))
    if not tags:
        raise OperationInvalid(
            "the part has no boundary tags: tag its inlet, outlet and walls first",
            details={"part": part},
        )
    return rules_for(mesh, tags)


def _free_name(session: Session, wanted: str) -> str:
    """``wanted``, or ``wanted_2``, ... when a node of that name is already a tag."""
    from stokes.geometry.carry import tags_of

    taken = {t.name for t in tags_of(session.require_project())}
    name, n = wanted, 1
    while name in taken:
        n += 1
        name = f"{wanted}_{n}"
    return name


def _balance(flows: dict[str, float]) -> float:
    """|sum of every port's flow| / inflow: zero for a converged incompressible run."""
    inflow = -sum(q for q in flows.values() if q < 0)
    return abs(sum(flows.values())) / inflow if inflow > 0 else 0.0


def check_flows(flows: dict[str, float], backflow: dict[str, float], outlets: list[str]) -> None:
    """Refuse a cycle whose mass does not balance or whose outlets take flow back in (ADR-0040):
    a flow split measured there is not the steady answer, and backflow breaks the adjoint's outlet
    condition as well."""
    imbalance = _balance(flows)
    if imbalance > MASS_IMBALANCE:
        raise CycleFailed(
            f"the flows do not balance: {imbalance:.2%} of the inflow is lost; the run has not "
            "converged",
            details={"step": "solve", "imbalance": imbalance, "flows": flows},
        )
    for name in outlets:
        if name not in flows:
            continue
        if flows[name] <= 0 or backflow.get(name, 0.0) > BACKFLOW:
            raise CycleFailed(
                f"outlet {name} takes flow back in ({backflow.get(name, 0.0):.1%} of its flux; "
                f"net {flows[name]:.3e} m³/s): lengthen the outlet or change the bounds",
                details={"step": "solve", "outlet": name, "flows": flows},
            )


def outlet_names(session: Session) -> list[str]:
    """The patch names (tag names) of the project's outlet boundaries, in tree order."""
    from stokes.api.commands.setup_plan import boundary_nodes

    project = session.require_project()
    out: list[str] = []
    for node in boundary_nodes(project):
        if node.params.get("kind") not in OUTLET_KINDS:
            continue
        tag = project.find(str(node.params.get("tag") or ""))
        if tag is not None:
            out.append(str(tag.name))
    return out


def _run_facts(session: Session, meshed: dict[str, Any]) -> dict[str, Any]:  # Any: JSON
    """What a row says of its own run: converged or stopped at the cap, iterations, cells."""
    status = session.execute("run.status")
    facts: dict[str, Any] = {}  # Any: JSON
    for key in ("converged", "iterations", "iteration"):
        if status.get(key) is not None:
            facts[key] = status.get(key)
    cells = meshed.get("cells")
    if cells is None:
        mesh_info = meshed.get("quality") or meshed.get("stats") or {}
        cells = mesh_info.get("cells") if isinstance(mesh_info, dict) else None
    if cells is not None:
        facts["cells"] = cells
    return facts


SENSITIVITY_SOURCES = ("solver", "reconstructed")
"""Where the agent's wall sensitivity came from (ADR-0041 decision 8): the solver's written
``shapeSensitivity``, or Stokes' reconstruction from U and Ua."""


def cycle_result(
    found: dict[str, Any],
    outlets: list[str],
    facts: dict[str, Any],
    *,
    adjoint: bool = True,
) -> Any:  # Any: JSON in; stokes.explore.shape.CycleResult out
    """The agent's ``job.sensitivity`` answer as a cycle's result: the flows checked (mass balance,
    backflow), the wall arrays shaped, and the run's facts with, for an adjoint cycle, where s came
    from (``sensitivity_source``). An agent from before ADR-0041 sends no ``source``: it only
    reconstructs, so that is what the row says."""
    import numpy as np

    from stokes.explore.shape import CycleResult

    entries = found.get("flows") or {}
    flows = {str(name): float(entry["flow"]) for name, entry in entries.items()}
    backflow = {str(name): float(entry.get("backflow") or 0.0) for name, entry in entries.items()}
    pressures = {
        str(name): entry["pressure"]
        for name, entry in entries.items()
        if entry.get("pressure") is not None
    }
    if flows:
        check_flows(flows, backflow, outlets)
    detail: dict[str, Any] = {  # Any: JSON
        **facts,
        "backflow": backflow,
        "pressures": pressures,
        "mass_balance": _balance(flows),
    }
    if adjoint:
        source = found.get("source") or "reconstructed"
        if source not in SENSITIVITY_SOURCES:
            raise CycleFailed(
                f"the agent named an unknown sensitivity source {source!r}",
                details={"step": "sensitivity", "source": str(source)},
            )
        detail["sensitivity_source"] = source
    n = len(found["areas"])
    return CycleResult(
        float(found["objective"]),
        np.asarray(found["centres"], dtype=np.float64).reshape(n, 3),
        np.asarray(found["normals"], dtype=np.float64).reshape(n, 3),
        np.asarray(found["areas"], dtype=np.float64),
        np.asarray(found["values"], dtype=np.float64),
        flows,
        tuple(name for name in outlets if name in flows),
        detail,
    )


class AdjointCycle:
    """``solve(project)`` for :class:`~stokes.explore.shape.AdjointObjective` and, with ``adjoint``
    false, :class:`~stokes.explore.shape.FiniteDifferenceObjective`."""

    def __init__(
        self,
        live: Session,
        part: str,
        workdir: Path,
        *,
        on_step: StepCallback | None = None,
        adjoint: bool = True,
        objective: str = "pressure_loss",
        outlets: tuple[str, ...] | list[str] = (),
        solves_per_cycle: int = 1,
    ) -> None:
        """``adjoint`` false runs the ordinary engine and reads only J and the port flows (a
        finite-difference cycle, ADR-0040). ``objective`` and ``outlets`` (patch names; empty is
        every pressure outlet) choose the adjoint's objective, and so its program (ADR-0041)."""
        self._adjoint = adjoint
        self._objective = objective
        self._outlets = [str(name) for name in outlets]
        if live.project_path is None:
            raise OperationInvalid("save the project first: a cycle copies its folder")
        self._source = Path(live.project_path)
        self._part = live.require_project().get(part).id
        self._rules = boundary_rules(live, self._part)
        self._workdir = workdir
        self._count = 0
        # Solves one optimiser cycle takes: N + 1 for finite differences. The steps report the
        # optimiser's cycle and the solve within it, so "Cycle 2" means what Stop after this cycle
        # waits for (walkthrough 2026-11-18: each solve read as a cycle of its own).
        self._per_cycle = max(1, int(solves_per_cycle))
        self._on_step: StepCallback = on_step or (lambda step, info: None)
        self._began = 0.0
        self._aborted = threading.Event()
        self._lock = threading.Lock()
        self._session: Session | None = None

    @property
    def cycles(self) -> int:
        """How many cycles have begun."""
        return self._count

    @property
    def rules(self) -> list[Any]:  # Any: retag.Rule
        """The rules that find the part's boundary tags again."""
        return list(self._rules)

    def folder(self, number: int) -> Path:
        """Cycle ``number``'s private project folder."""
        return self._workdir / f"cycle{number:03d}.stokes"

    def prune(self, keep: set[int]) -> None:
        """Remove every cycle folder but those of the cycles in ``keep`` (a finished run keeps its
        best and its last), so twenty CFD cycles do not leave twenty projects behind."""
        for number in range(1, self._count + 1):
            if number in keep:
                continue
            path = self.folder(number)
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)

    def abort(self) -> bool:
        """Stop the cycle under way (called from another thread): its mesh or run is cancelled
        and the cycle raises ``Cancelled`` at its next step. True when a cycle was running."""
        self._aborted.set()
        with self._lock:
            session = self._session
        if session is None:
            return False
        # **The services, not ``session.execute``**: the cycle's thread is inside
        # ``execute("mesh.generate", wait=True)`` on this same session, and ``execute`` holds no
        # lock (it shares its depth counter), so a second ``execute`` from this thread could
        # interleave with it. The mesh service and the run store guard their own state.
        from stokes.mesh.service import MeshService
        from stokes.run.store import RunStore

        for name, cancel in (
            ("mesh", MeshService.of(session).cancel),
            ("run", lambda: RunStore.of(session).cancel_run(None)),
        ):
            try:
                cancel()
            except StokesError:
                log.info("nothing to cancel", extra=fields(what=name))
        return True

    def __call__(self, project: Project) -> Any:  # Any: stokes.explore.shape.CycleResult
        from stokes.explore.errors import Cancelled

        self._count += 1
        self._began = time.monotonic()
        if self._aborted.is_set():
            raise Cancelled("stopped")
        folder = self._prepare(project)
        session = Session()
        session.private = True
        with self._lock:
            self._session = session
        try:
            self._step("open")
            session.execute("project.open", path=str(folder))
            self._step("rebuild")
            self._rebuild(session)
            self._step("tags")
            self._retag(session)
            self._step("mesh")
            session.execute("solve.configure", values=self._solver_values())
            meshed = session.execute("mesh.generate", wait=True)
            self._check_aborted()
            if meshed.get("state") != "done":
                raise CycleFailed(f"meshing ended {meshed.get('state')}", details={"step": "mesh"})
            self._step("solve")
            run = session.execute("run.start", wait=True)
            self._check_aborted()
            if run.get("state") != "done":
                what = "adjoint run" if self._adjoint else "run"
                raise CycleFailed(f"the {what} ended {run.get('state')}", details={"step": "solve"})
            self._step("sensitivity")
            found = self._sensitivity(session, run)
            outlets = outlet_names(session)
            facts = _run_facts(session, meshed)
        finally:
            with self._lock:
                self._session = None
            self._close(session)
        return cycle_result(found, outlets, {"cycle": self._count, **facts}, adjoint=self._adjoint)

    # ------------------------------------------------------------------ steps
    def _solver_values(self) -> dict[str, Any]:  # Any: JSON
        """The cycle's solver settings: the adjoint engine with its objective and outlets, or the
        ordinary engine (the objective is then computed from the flows on the desktop)."""
        if not self._adjoint:
            return {"engine": PRIMAL_ENGINE, "turbulence": "laminar"}
        # **One core, set here.** The adjoint writer refuses any other count, and it refused AFTER
        # the cycle's mesh was built: a project solved on four cores lost five minutes to "the
        # adjoint runs on one core for now". The cycle's project is a private copy, so the setting
        # is the cycle's own and the user's four cores stay theirs.
        return {
            "engine": ADJOINT_ENGINE,
            "turbulence": "laminar",
            "cores": 1,
            "adjoint_objective": self._objective,
            "adjoint_outlets": list(self._outlets) if self._objective == "flow_split" else [],
        }

    def _check_aborted(self) -> None:
        from stokes.explore.errors import Cancelled

        if self._aborted.is_set():
            raise Cancelled("stopped")

    def _position(self) -> dict[str, int]:
        """The optimiser's cycle and, for finite differences, the solve within it."""
        per = getattr(self, "_per_cycle", 1)  # a cycle built by hand in a test has none
        if self._count <= 0:
            return {"cycle": 0}
        cycle = (self._count - 1) // per + 1
        if per == 1:
            return {"cycle": cycle}
        return {"cycle": cycle, "solve": (self._count - 1) % per + 1, "solves": per}

    def _step(self, name: str) -> None:
        self._check_aborted()
        log.info("shape cycle step", extra=fields(cycle=self._count, step=name))
        self._on_step(
            name,
            {
                **self._position(),
                "step_number": STEPS.index(name) + 1,
                "steps": len(STEPS),
                "elapsed": round(time.monotonic() - self._began, 3),
            },
        )

    def _prepare(self, project: Project) -> Path:
        from stokes.model.persist import save_project

        folder = self.folder(self._count)
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir(parents=True)
        for name in COPIED:
            source = self._source / name
            if source.is_dir():
                shutil.copytree(source, folder / name)
        save_project(project, folder)
        return folder

    def _rebuild(self, session: Session) -> None:
        """Run every implicit operation again, in tree order (inputs come before the operations
        that read them), so the part is the one the variables describe."""
        from stokes.geometry.model import OPERATIONS_PATH, is_implicit_operation

        project = session.require_project()
        group = project.find(OPERATIONS_PATH)
        for op in list(group.walk()) if group is not None else []:
            if is_implicit_operation(op.type):
                info = session.execute("operation.run", op=op.id, wait=True)
                if info.get("status") == "failed":
                    raise CycleFailed(
                        f"{op.path}: {info.get('error') or 'the operation failed'}",
                        details={"step": "rebuild", "op": op.id},
                    )

    def _retag(self, session: Session) -> None:
        from stokes.geometry.retag import apply_rules, untagged
        from stokes.geometry.store import GeometryStore

        mesh = GeometryStore.of(session).mesh(self._part)
        if mesh is None:
            raise CycleFailed("the rebuilt part has no mesh", details={"step": "tags"})
        found = apply_rules(mesh, self._rules)
        for tag, ids in found.items():
            session.execute("tag.rematch", tag=tag, result=[{"part": self._part, "faces": ids}])
        # **The faces nobody tagged are the walls**: the guided flow leaves them "wall by default",
        # and the adjoint needs them as a patch of type wall (its sensitivity and objective are
        # taken on wall and non-wall patches), so they become a tag of their own in this copy.
        rest = untagged(mesh, found)
        if rest and not any(r.kind == "rest" for r in self._rules):
            made = session.execute(
                "tag.create",
                name=_free_name(session, "walls"),
                members=[{"part": self._part, "faces": rest}],
                kind="boundary",
            )
            session.execute("boundary.assign", target=made["id"], kind="wall")

    def _sensitivity(self, session: Session, run: dict[str, Any]) -> dict[str, Any]:  # Any: JSON
        from stokes.mesh.service import MeshService
        from stokes.run.hosts import HostRegistry
        from stokes.run.snapshot import build_solve_snapshot

        project = session.require_project()
        status = session.execute("run.status")
        settings = project.find("/solver/settings")
        if settings is None:
            raise CycleFailed("no solver settings", details={"step": "sensitivity"})
        mesh_status = MeshService.of(session).status()
        mesh_status["part"] = self._part
        nu = float(build_solve_snapshot(session, settings, mesh_status).nu)
        runner = HostRegistry.of(session).runner(str(status.get("host") or "local"))
        found: dict[str, Any] = runner.call(
            "job.sensitivity",
            job=str(run["job"]),
            nu=nu,
            timeout=600.0,
            **({} if self._adjoint else {"adjoint": False}),
        )
        return found

    def _close(self, session: Session) -> None:
        from stokes.geometry.store import GeometryStore

        try:
            GeometryStore.of(session).close()
        except StokesError:
            log.warning("a cycle session did not close cleanly", exc_info=True)
