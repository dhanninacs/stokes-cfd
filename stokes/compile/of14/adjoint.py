"""The adjoint case writer (ADR-0038 decisions 0 to 2, ADR-0041): a `SolveSnapshot` in, the case of
an adjoint solver out. **The objective picks the program**: the pressure loss is Foundation 14's
`adjointShapeOptimisationFoam`; the flow split is `adjointFoam`, the separate open-source solver
(`of14-adjoint`), which Stokes drives only through the files written here and its argv.

The case is the ordinary steady incompressible one (this package's `write`) with what the adjoint
solver adds, every keyword copied from its own tutorial
(`tutorials/legacy/incompressible/adjointShapeOptimisationFoam/pitzDaily`):

- `controlDict` names the application instead of `foamRun`'s solver;
- `physicalProperties` gains `lambda 0` and `alphaMax 0`, so the solver's porosity never grows:
  the run is one primal solve and its adjoint, which is all a sensitivity needs;
- `fvSchemes` gains the adjoint's convection and stress terms, `fvSolution` its solvers, the
  SIMPLE reference cell and the relaxation of `pa` and `alpha`;
- `0/Ua` and `0/pa`: at an inlet, Ua is fixed at the unit vector against the inlet velocity; at an
  outlet, `adjointOutletVelocity` and `adjointOutletPressure` (the pressure-loss objective); at a
  wall, no slip and zero gradient.

For the flow split (ADR-0041) the inlet's Ua is zero (J does not depend on the pressure), every
outlet takes `objectiveOutletVelocity` and `objectiveOutletPressure`, and `system/adjointDict`
names the objective (`flowRatePartition`, its outlets and equal target fractions), the viscous
outlet term, the tangential outlet condition and the `UaGradU` transpose convection. The
pressure-loss case is byte for byte what it was (golden files).

Measured on FoamWin (2026-11-12): the solver's adjoint is stable for laminar flow and diverges for
turbulent flow without its porosity, so only a **laminar, steady** case with velocity inlets,
pressure outlets and walls is written; anything else is `compile.unsupported`.

Pure functions, as the rest of the package.
"""

from __future__ import annotations

import dataclasses
import math
from typing import Any

from stokes.compile.errors import CompileUnsupported
from stokes.compile.foam import (
    CaseTree,
    Comment,
    Dict,
    Entry,
    FuncInclude,
    Include,
    Raw,
    Tuple_,
    Word,
    render_file,
)
from stokes.compile.snapshot import (
    ADJOINT_OBJECTIVES,
    CONSTRAINT_KINDS,
    PatchCondition,
    SolveSnapshot,
)

from . import _control_dict, _fv_schemes, _fv_solution, _physical_properties, write

type Item = Entry | Include | FuncInclude | Comment

ENGINE = "of14.adjoint"
APPLICATION = "adjointShapeOptimisationFoam"
APPLICATION_OPEN = "adjointFoam"
"""The open-source adjoint (ADR-0041): the flow split's program, installed beside the solvers."""
FLOW_SPLIT = "flow_split"
_BASE_ENGINE = "of14.incompressibleFluid"
_KINDS = ("velocity_inlet", "pressure_outlet", "wall", "unassigned", *CONSTRAINT_KINDS)


def _base(snap: SolveSnapshot) -> SolveSnapshot:
    """The snapshot the incompressible writer is given, after the adjoint's own refusals."""
    if snap.turbulence != "laminar":
        raise CompileUnsupported(
            "the Foundation adjoint is stable for laminar flow only; a turbulent case needs the "
            "open-source adjoint solver (planned)",
            details={"code": "compile.unsupported", "turbulence": snap.turbulence},
        )
    if snap.transient:
        raise CompileUnsupported(
            "the adjoint solves a steady flow", details={"code": "compile.unsupported"}
        )
    if snap.cores != 1:
        raise CompileUnsupported(
            "the adjoint runs on one core for now", details={"code": "compile.unsupported"}
        )
    kinds = {p.kind for p in snap.patches}
    unsupported = sorted(kinds - set(_KINDS))
    if unsupported:
        raise CompileUnsupported(
            f"the adjoint takes velocity inlets, pressure outlets, walls and symmetry planes; "
            f"not {', '.join(unsupported)}",
            details={"code": "compile.unsupported", "kinds": unsupported},
        )
    if "velocity_inlet" not in kinds or "pressure_outlet" not in kinds:
        raise CompileUnsupported(
            "the pressure-loss objective needs an inlet and an outlet",
            details={"code": "compile.unsupported"},
        )
    if snap.adjoint_objective not in ADJOINT_OBJECTIVES:
        raise CompileUnsupported(
            f"the adjoint objective is one of {', '.join(ADJOINT_OBJECTIVES)}; "
            f"not {snap.adjoint_objective!r}",
            details={"code": "compile.unsupported", "objective": snap.adjoint_objective},
        )
    if snap.adjoint_objective == FLOW_SPLIT:
        split_outlets(snap)
    return dataclasses.replace(snap, engine=_BASE_ENGINE)


def split_outlets(snap: SolveSnapshot) -> tuple[str, ...]:
    """The flow split's outlets, in the mesh's patch order: the chosen ones, or every pressure
    outlet. Refused (`compile.unsupported`) with fewer than two, with a name that is not a pressure
    outlet, or with a pressure outlet left out: an outlet outside the objective would need an
    adjoint condition of its own, which the solver does not have (ADR-0041)."""
    pressure = [p.name for p in snap.patches if p.kind == "pressure_outlet"]
    chosen = list(snap.adjoint_outlets) or pressure
    unknown = sorted(set(chosen) - set(pressure))
    if unknown:
        raise CompileUnsupported(
            f"not a pressure outlet of this mesh: {', '.join(unknown)}",
            details={"code": "compile.unsupported", "outlets": unknown, "known": pressure},
        )
    if len(set(chosen)) < 2:
        raise CompileUnsupported(
            "the flow split's adjoint needs at least two outlets",
            details={"code": "compile.unsupported", "outlets": chosen},
        )
    left_out = [name for name in pressure if name not in chosen]
    if left_out:
        raise CompileUnsupported(
            "the flow split's adjoint shares the flow among every pressure outlet; "
            f"{', '.join(left_out)} is left out (use finite differences for a subset)",
            details={"code": "compile.unsupported", "left_out": left_out},
        )
    return tuple(name for name in pressure if name in chosen)


def application(snap: SolveSnapshot) -> str:
    """The program the case is written for: the objective picks it (ADR-0041)."""
    return APPLICATION_OPEN if snap.adjoint_objective == FLOW_SPLIT else APPLICATION


def write_adjoint(snap: SolveSnapshot) -> CaseTree:
    """The adjoint case tree; refuses what the solver cannot do (module docstring)."""
    base = _base(snap)
    tree = write(base)
    tree["system/controlDict"] = render_file("controlDict", _control(base), location="system")
    tree["system/fvSchemes"] = render_file("fvSchemes", _schemes(base), location="system")
    tree["system/fvSolution"] = render_file("fvSolution", _solution(base), location="system")
    tree["constant/physicalProperties"] = render_file(
        "physicalProperties", _properties(base), location="constant"
    )
    tree["0/Ua"] = render_file("Ua", _adjoint_field(base, "Ua"), cls="volVectorField", location="0")
    tree["0/pa"] = render_file("pa", _adjoint_field(base, "pa"), cls="volScalarField", location="0")
    if base.adjoint_objective == FLOW_SPLIT:
        tree["system/adjointDict"] = render_file(
            "adjointDict", _adjoint_dict(base), location="system"
        )
    return tree


def adjoint_steps(snap: SolveSnapshot) -> list[dict[str, Any]]:
    """The job step that runs the adjoint case."""
    base = _base(snap)
    return [
        {
            "name": "adjoint",
            "argv": [application(base)],
            "timeout_s": 7200,
            "parse": "foamRun",
            "parse_params": {"end_time": base.iterations},
        }
    ]


def _replace(node: Dict, key: str, entry: Entry | None) -> Dict:
    """``node`` with the entry ``key`` replaced by ``entry`` (or dropped with None)."""
    items: list[Item] = []
    for item in node.items:
        if isinstance(item, Entry) and item.key == key:
            if entry is not None:
                items.append(entry)
            continue
        items.append(item)
    return Dict(tuple(items))


def _within(node: Dict, key: str, extra: tuple[Entry, ...]) -> Dict:
    """``node`` with the sub-dictionary ``key`` extended by ``extra`` (created when absent)."""
    found = False
    items: list[Any] = []
    for item in node.items:
        if isinstance(item, Entry) and item.key == key and isinstance(item.value, Dict):
            items.append(Entry(key, Dict((*item.value.items, *extra))))
            found = True
        else:
            items.append(item)
    if not found:
        items.append(Entry(key, Dict(extra)))
    return Dict(tuple(items))


def _control(snap: SolveSnapshot) -> Dict:
    control = _control_dict(snap)
    items: list[Item] = [Entry("application", Word(application(snap)))]
    items += [i for i in control.items if not (isinstance(i, Entry) and i.key == "solver")]
    return Dict(tuple(items))


def _schemes(snap: SolveSnapshot) -> Dict:
    return _within(
        _fv_schemes(snap),
        "divSchemes",
        (
            Entry("div(-phi,Ua)", Raw("bounded Gauss upwind")),
            Entry("div((nuEff*dev2(T(grad(Ua)))))", Raw("Gauss linear")),
        ),
    )


def _solution(snap: SolveSnapshot) -> Dict:
    solution = _fv_solution(snap)
    solution = _within(
        solution,
        "solvers",
        (
            Entry(
                "pa",
                Dict(
                    (
                        Entry("solver", Word("GAMG")),
                        Entry("tolerance", 1e-08),
                        Entry("relTol", 0.01),
                        Entry("smoother", Word("GaussSeidel")),
                    )
                ),
            ),
            Entry(
                "Ua",
                Dict(
                    (
                        Entry("solver", Word("smoothSolver")),
                        Entry("smoother", Word("GaussSeidel")),
                        Entry("nSweeps", 2),
                        Entry("tolerance", 1e-08),
                        Entry("relTol", 0.1),
                    )
                ),
            ),
        ),
    )
    solution = _within(solution, "SIMPLE", (Entry("pRefCell", 0), Entry("pRefValue", 0)))
    # The run stops on residualControl: the adjoint must converge too, not only the flow
    # (measured: with p and U alone the run stopped at iteration 134 of 400).
    solution = _residuals_with_adjoint(solution, snap.residual_target)
    return _replace(
        solution,
        "relaxationFactors",
        Entry(
            "relaxationFactors",
            Dict(
                (
                    Entry("fields", Dict((Entry('"(p|pa)"', 0.3), Entry("alpha", 0.1)))),
                    Entry("equations", Dict((Entry('"(U|Ua)"', 0.7), Entry('".*"', 0.7)))),
                )
            ),
        ),
    )


def _residuals_with_adjoint(solution: Dict, target: float) -> Dict:
    items: list[Item] = []
    for item in solution.items:
        if isinstance(item, Entry) and item.key == "SIMPLE" and isinstance(item.value, Dict):
            items.append(
                Entry(
                    "SIMPLE",
                    _within(
                        item.value, "residualControl", (Entry("Ua", target), Entry("pa", target))
                    ),
                )
            )
        else:
            items.append(item)
    return Dict(tuple(items))


def _properties(snap: SolveSnapshot) -> Dict:
    return Dict((*_physical_properties(snap).items, Entry("lambda", 0), Entry("alphaMax", 0)))


def _inlet_ua(velocity: tuple[float, float, float] | None) -> str:
    """-U_in: the inlet velocity reversed, at its full speed (Othmer's inlet condition for the
    pressure-loss objective). The solver's tutorial fixes a unit vector, which is enough for the
    porosity update it was written for but scales s by 1/|U_in|: measured, a 20-fold error on a
    0.05 m/s inlet (ADR-0038 decision 4)."""
    if velocity is None:
        raise CompileUnsupported("an adjoint inlet needs its velocity vector")
    size = math.sqrt(sum(v * v for v in velocity))
    if size == 0.0:
        raise CompileUnsupported("an adjoint inlet needs a non-zero velocity")
    return "uniform ({:.12g} {:.12g} {:.12g})".format(*(-v + 0.0 for v in velocity))


def _adjoint_dict(snap: SolveSnapshot) -> Dict:
    """`system/adjointDict` of `adjointFoam` for the flow split (ADR-0041): no porosity, the wall
    sensitivity on, the flow-rate partition over the outlets with equal targets, the viscous outlet
    term, the Robin tangential outlet condition and the `UaGradU` transpose convection."""
    outlets = split_outlets(snap)
    share = 1.0 / len(outlets)
    return Dict(
        (
            Entry("porosity", False),
            Entry("sensitivity", True),
            Entry(
                "objective",
                Dict(
                    (
                        Entry("type", Word("flowRatePartition")),
                        Entry("outletPatches", Tuple_(tuple(Word(name) for name in outlets))),
                        Entry("targetFractions", Tuple_(tuple(share for _ in outlets))),
                        Entry("viscousTerm", True),
                        Entry("tangential", Word("robin")),
                    )
                ),
            ),
            Entry("ATC", Dict((Entry("type", Word("UaGradU")),))),
        )
    )


def _inlet_entry(snap: SolveSnapshot, patch: PatchCondition, vector: bool) -> Dict:
    """An inlet's Ua and pa: -U_in for the pressure loss, zero for the flow split (its J does not
    depend on the pressure, so the inlet carries no adjoint velocity, ADR-0041)."""
    if not vector:
        return Dict((Entry("type", Word("zeroGradient")),))
    value = "uniform (0 0 0)" if snap.adjoint_objective == FLOW_SPLIT else _inlet_ua(patch.velocity)
    return Dict((Entry("type", Word("fixedValue")), Entry("value", Raw(value))))


def _outlet_kind(snap: SolveSnapshot, vector: bool) -> str:
    """The outlet's adjoint condition: the legacy pressure-loss pair, or the flow split's."""
    if snap.adjoint_objective == FLOW_SPLIT:
        return "objectiveOutletVelocity" if vector else "objectiveOutletPressure"
    return "adjointOutletVelocity" if vector else "adjointOutletPressure"


def _adjoint_field(snap: SolveSnapshot, name: str) -> Dict:
    vector = name == "Ua"
    entries: list[Entry] = []
    for patch in snap.patches:
        if patch.kind in CONSTRAINT_KINDS:
            entries.append(Entry(patch.name, Dict((Entry("type", Word(patch.patch_type)),))))
        elif patch.kind == "velocity_inlet":
            entries.append(Entry(patch.name, _inlet_entry(snap, patch, vector)))
        elif patch.kind == "pressure_outlet":
            kind = _outlet_kind(snap, vector)
            value = "uniform (0 0 0)" if vector else "uniform 0"
            entries.append(
                Entry(patch.name, Dict((Entry("type", Word(kind)), Entry("value", Raw(value)))))
            )
        else:  # a wall
            entries.append(
                Entry(
                    patch.name, Dict((Entry("type", Word("noSlip" if vector else "zeroGradient")),))
                )
            )
    return Dict(
        (
            Entry("dimensions", Raw("[0 1 -1 0 0 0 0]" if vector else "[0 2 -2 0 0 0 0]")),
            Entry("internalField", Raw("uniform (0 0 0)" if vector else "uniform 0")),
            Entry("boundaryField", Dict(tuple(entries))),
        )
    )
