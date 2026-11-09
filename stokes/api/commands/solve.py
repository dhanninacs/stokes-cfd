"""The ``solve.*`` commands: the engine list, the settings, and the set-up check (ADR-0011, T-214a).

Three commands, and a deliberate absence:

* ``solve.engines`` — what this build can write and run.
* ``solve.configure`` — create or edit ``/solver/settings``. **One `Composite`**, like
  ``mesh.configure``, so a refusal anywhere leaves the tree untouched: the commands are built
  first and
  pushed as one undo step, and a validation failure while building pushes nothing.
* ``solve.check`` — is this set-up ready to run? The findings `run.start` would raise on, reported
  instead, so a user sees them before pressing Run.

**There is no ``solve.run``.** Starting a run is `run.start` (T-214b), which owns the record; a
``solve.*`` command that started one would be a second entry point to the same machinery.
"""

from __future__ import annotations

from typing import Any

from stokes.api.registry import CommandContext, ParamDef, command
from stokes.engines.catalogue import ENERGY_ENGINE, ENGINES
from stokes.engines.catalogue import engine as find_engine
from stokes.engines.model import SETTINGS_PATH, SOLVER_PATH
from stokes.errors import ValidationError
from stokes.model.ids import stable_id
from stokes.run.errors import SolveInvalid


def _settings(project: Any) -> Any:
    """The solver settings node, or a refusal naming the command that makes one."""
    found = project.find(SETTINGS_PATH)
    if found is None:
        raise SolveInvalid(
            "there are no solver settings; run solve.configure first",
            details={"code": "solve.invalid"},
        )
    return found


@command("solve.engines", params=(), mutates=False)
def solve_engines(ctx: CommandContext) -> dict[str, Any]:
    """Every engine this build knows, with whether it can run and why not.

    **The unbuilt ones are listed**, so a client shows what is coming rather than a shorter
    menu with no
    explanation.
    """
    del ctx
    return {
        "engines": [
            {
                "id": spec.id,
                "label": spec.label,
                "built": spec.built,
                "turbulence": list(spec.turbulence),
                "why": spec.why,
            }
            for spec in ENGINES
        ]
    }


# : The settings' own parameters, and the coercions a command applies. `mesh_settings` is the
# pattern.
_VALUES_HELP = "Settings to set, by name; only the ones given change."


def _params(values: dict[str, Any] | None, engine: str | None) -> dict[str, Any]:
    """The name → value map a `Composite` will set, with the checks the schema cannot express.

    **The schema states kinds and defaults; a range is a rule about the model**, so
    `iterations >= 1`,
    `residual_target > 0` and integer `cores` in 1..256 are checked here
    where a message can say why.
    """
    out: dict[str, Any] = {}
    if engine is not None:
        out["engine"] = engine
    out.update(values or {})
    if "engine" in out:
        out["engine"] = find_engine(str(out["engine"])).id
        if "energy" not in out:
            out["energy"] = out["engine"] == ENERGY_ENGINE
        if out["engine"] != ENERGY_ENGINE and "density_model" not in out:
            out["density_model"] = "constant"
        find_engine(str(out["engine"]))  # raises SolveInvalid for an unknown id
    if "iterations" in out and int(out["iterations"]) < 1:
        raise ValidationError(
            f"iterations must be at least 1; got {out['iterations']}",
            details={"code": "solve.invalid", "iterations": out["iterations"]},
        )
    if "write_interval" in out and int(out["write_interval"]) < 1:
        raise ValidationError(
            f"write_interval must be at least 1; got {out['write_interval']}",
            details={"code": "solve.invalid", "write_interval": out["write_interval"]},
        )
    if "residual_target" in out and float(out["residual_target"]) <= 0.0:
        raise ValidationError(
            f"residual_target must be positive; got {out['residual_target']}",
            details={"code": "solve.invalid", "residual_target": out["residual_target"]},
        )
    if "cores" in out:
        from stokes.cores import core_count

        out["cores"] = core_count(out["cores"])
    return out


#: The thermal settings that only mean something with the energy equation on (ADR-0027 step a).
THERMAL_ONLY: tuple[str, ...] = ("gravity", "operating_pressure")


def _refuse_thermal_without_energy(wanted: dict[str, Any], current: dict[str, Any]) -> None:
    """Refuse a gravity or operating-pressure value when the energy equation is off.

    **The flow alone does not read them**, so writing them would be a setting that silently does
    nothing; refusing names the switch that makes them count.
    """
    energy_on = bool(wanted.get("energy", current.get("energy", False)))
    named = [name for name in THERMAL_ONLY if name in wanted]
    if named and not energy_on:
        raise SolveInvalid(
            f"{' and '.join(named)} only apply with the energy equation on; turn energy on first",
            details={"code": "solve.invalid", "settings": named, "energy": False},
        )


def _refuse_energy_on_another_engine(wanted: dict[str, Any], current: dict[str, Any]) -> None:
    """Refuse an energy equation on an engine that does not solve it (ADR-0027 decision 2).

    **The effective values decide**: a configure that sets `energy` alone is judged against the
    engine the node already has, and one that switches the engine away from `of14.fluid` is judged
    against the energy the node already has. Refusing before the push keeps the tree unchanged.
    """
    engine_id = str(wanted.get("engine", current.get("engine", "of14.incompressibleFluid")))
    energy_on = bool(wanted.get("energy", current.get("energy", False)))
    if energy_on and engine_id != ENERGY_ENGINE:
        raise SolveInvalid(
            f"the energy equation needs the {ENERGY_ENGINE} engine; this set-up uses {engine_id!r}",
            details={"code": "solve.invalid", "engine": engine_id, "energy": True},
        )


@command(
    "solve.configure",
    params=(
        ParamDef("engine", "string", required=False, description="The engine id."),
        ParamDef("values", "object", required=False, description=_VALUES_HELP),
    ),
)
def solve_configure(
    ctx: CommandContext, engine: str | None = None, values: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Create or edit ``/solver/settings``, as one undo step.

    **A refusal anywhere leaves the tree unchanged** — every command is built before any is
    pushed, so
    the settings node cannot end up half-edited.

    **`kOmegaSST` is accepted here and refused by `solve.check` unless the energy equation is on.**
    The brief is explicit about where that
    refusal lives, and the placement is right: the model's enum is the set of models a user may
    *choose*, while "the writer for this one is not built" is a fact about the build that a
    **check**
    reports, next to the other findings that stop a run. A configure-time refusal would also make a
    project saved with `kOmegaSST` impossible to open.
    """
    from stokes.errors import ValidationError
    from stokes.model.commands import AddNode, Composite, SetParam

    project = ctx.session.require_project()
    wanted = _params(values, engine)
    existing = project.find(SETTINGS_PATH)
    current = existing.params if existing is not None else {}
    _refuse_energy_on_another_engine(wanted, current)
    _refuse_thermal_without_energy(wanted, current)
    target_id = existing.id if existing is not None else stable_id("n", SETTINGS_PATH)
    commands: list[Any] = []
    if existing is None:
        if project.find(SOLVER_PATH) is None:
            commands.append(
                AddNode(
                    parent="/",
                    type="group",
                    node_name="solver",
                    node_id=stable_id("g", SOLVER_PATH),
                )
            )
        commands.append(
            AddNode(
                parent=SOLVER_PATH,
                type="solver_settings",
                node_name="settings",
                params={},
                node_id=target_id,
            )
        )
    for name, value in sorted(wanted.items()):
        commands.append(SetParam(node=target_id, param=name, value=value))

    if commands:
        try:
            ctx.session.undo.push(
                Composite(label="solve.configure", commands=list(commands)), project
            )
        except ValidationError as exc:
            # **The model refused a value the catalogue allowed** (a pattern, a range).
            # Surface it as a
            # solve error with the model's own message, so a client sees one error family here —
            # the same shape `mesh.configure` uses.
            raise SolveInvalid(str(exc)) from exc
    ctx.record(engine=wanted.get("engine"), values=wanted, node_id=target_id)

    node = project.find(SETTINGS_PATH)
    if node is None:  # pragma: no cover - the push above either created it or raised
        raise SolveInvalid("the settings node was not created")
    ctx.session.emit("node.changed", node_id=node.id, path=SETTINGS_PATH, param="engine")
    return {"settings": node.id, "path": SETTINGS_PATH, "created": existing is None}


@command("solve.check", params=(), mutates=False)
def solve_check(ctx: CommandContext) -> dict[str, Any]:
    """Whether the set-up can become a case, with the findings that stop it.

    **Reports rather than raises**, because a check is what a user runs to find out what is
    missing: the
    same conditions that make the snapshot raise are returned as findings naming the node to fix.
    """
    from stokes.mesh.service import MeshService

    project = ctx.session.require_project()
    findings: list[dict[str, Any]] = []
    settings = project.find(SETTINGS_PATH)
    if settings is None:
        findings.append(
            {
                "code": "solve.invalid",
                "severity": "error",
                "message": "there are no solver settings",
                "suggestion": "run solve.configure",
                "nodes": [],
            }
        )
    # **The snapshot's own material rules**, so a set-up that would be refused at solve time is
    # not ready now. The reason is the refusal's, with its code, so a client branches on one family.
    from stokes.run.snapshot import material_problem

    problem = material_problem(ctx.session)
    if problem is not None:
        findings.append(
            {
                "code": problem.code,
                "severity": "error",
                "message": str(problem),
                "suggestion": "give the domain's region a material with a density and a viscosity",
                "nodes": [],
            }
        )
    status = MeshService.of(ctx.session).status()
    if not status.get("result"):
        findings.append(
            {
                "code": "solve.no_mesh",
                "severity": "error",
                "message": "there is no mesh result",
                "suggestion": "run Mesh first",
                "nodes": [],
            }
        )
    elif status.get("state") != "done":
        findings.append(
            {
                "code": "solve.no_mesh",
                "severity": "error",
                "message": f"the mesh is {status.get('state')!r}",
                "suggestion": "wait for the mesh, or run it again",
                "nodes": [str(status.get("result"))],
            }
        )
    elif status.get("stale"):
        findings.append(
            {
                "code": "solve.mesh_stale",
                "severity": "warning",
                "message": "the mesh is stale: its settings changed since it was made",
                "suggestion": "mesh again, or run anyway",
                "nodes": [str(status.get("result"))],
            }
        )
    # **The energy equation needs a specific heat and a conductivity** (ADR-0027 decision 3). Asked
    # only with energy on, so a flow-only set-up with a material that lacks them stays ready.
    if settings is not None and bool(settings.params.get("energy")):
        from stokes.run.snapshot import thermal_problem

        thermal = thermal_problem(ctx.session)
        if thermal is not None:
            findings.append(
                {
                    "code": "solve.no_thermal_material",
                    "severity": "error",
                    "message": str(thermal),
                    "suggestion": "give the domain's material a specific heat and a conductivity",
                    "nodes": [],
                }
            )
    # **Energy is refused on an engine that is not built**, and the finding says so rather than
    # letting a run fail at the solver. The engine id decides; the fluid engine is built since
    # ADR-0027 step 5, so this guards a catalogue entry that is listed but not yet runnable.
    if settings is not None and bool(settings.params.get("energy")):
        energy_engine = str(settings.params.get("engine") or "")
        built = any(spec.id == energy_engine and spec.built for spec in ENGINES)
        if not built:
            findings.append(
                {
                    "code": "solve.energy_not_built",
                    "severity": "error",
                    "message": f"the energy equation needs the {ENERGY_ENGINE} writer, "
                    "which is not built yet",
                    "suggestion": "turn the energy equation off to run the flow alone",
                    "nodes": [settings.id],
                }
            )
    if settings is not None:
        findings.extend(_model_findings(settings))
    if settings is not None and not any(f["severity"] == "error" for f in findings):
        from stokes.errors import StokesError
        from stokes.run.executors import case_for
        from stokes.run.snapshot import build_solve_snapshot

        try:
            case_for(build_solve_snapshot(ctx.session, settings, status))
        except StokesError as exc:
            findings.append(
                {
                    "code": exc.code,
                    "severity": "error",
                    "message": str(exc),
                    "suggestion": str(exc),
                    "nodes": [settings.id],
                }
            )
    errors = [finding for finding in findings if finding["severity"] == "error"]
    return {
        "state": "not_ready" if errors else "ready",
        "findings": findings,
        "next": errors[0]["suggestion"] if errors else "run.start",
    }


def _model_findings(settings: Any) -> list[dict[str, Any]]:  # Any: model node
    """Check the same engine/model contract even before a mesh exists."""
    from stokes.engines.turbulence import model

    params = settings.params
    engine_id = str(params.get("engine") or "of14.incompressibleFluid")
    try:
        spec = find_engine(engine_id)
        turbulence = model(str(params.get("turbulence") or "laminar"))
    except (SolveInvalid, ValueError) as exc:
        message = str(exc)
    else:
        if spec.id not in turbulence.engines:
            message = f"{turbulence.name} is not registered for {spec.id} in Foundation 14"
        elif turbulence.transient_required and not params.get("transient", False):
            message = (
                f"{turbulence.name} requires a transient simulation; "
                "enable Transient simulation and set time step and end time"
            )
        elif params.get("density_model", "constant") == "perfectGas" and (
            spec.id != ENERGY_ENGINE or not params.get("energy")
        ):
            message = "Perfect-gas compressible flow requires the fluid engine and energy equation"
        elif spec.id == ENERGY_ENGINE and not params.get("energy"):
            message = "The fluid engine requires the energy equation"
        else:
            return []
    return [
        {
            "code": "solve.model_configuration",
            "severity": "error",
            "message": message,
            "suggestion": message,
            "nodes": [settings.id],
        }
    ]


@command("solve.turbulence_models", params=(), mutates=False)
def solve_turbulence_models(ctx: CommandContext) -> dict[str, Any]:
    """Foundation 14 registered models and their engine, field and time requirements."""
    from dataclasses import asdict

    from stokes.engines.turbulence import MODELS

    del ctx
    return {"models": [asdict(spec) for spec in MODELS]}
