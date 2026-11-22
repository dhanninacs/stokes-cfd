"""The `coupled.simple` writer: the incompressible case plus `COUPLED` (ADR-0044).

coupledSimpleFoam reads an ordinary steady incompressible OpenFOAM 14 case and **one** extra
sub-dictionary, `COUPLED` in `system/fvSolution`; `foamRun` ignores `COUPLED` and the coupled
solver ignores `SIMPLE` and the p/U `relaxationFactors`. So this writer is the `of14` writer's case
with three changes, and the same directory still runs under `foamRun` for a comparison:

* `system/fvSolution` gains `COUPLED`: the settings' `iterations` as `nOuterIterations` (the solver
  runs its own outer loop; `controlDict` does not stop it), the settings' `residual_target` as
  `residualControl` for `U` and `p` (and `T` with energy), the backend, and the pressure pinned
  at cell 0 for a closed domain. On one core the backend is the serial direct `SparseLU`; on more
  it is PETSc over MPI with `PETSC_OPTIONS` and the settings beside it (ADR-0044 decision 9).
* `system/fvSchemes` gains the two stress-term entries the coupled assembly reads,
  `div((nu*dev2(T(grad(U)))))` and `div(devTauCorrFlux)` (both in the solver's own tutorials), and
  `div(phi,T)` with energy on.
* With energy on, `0/T`, a `T` solver, and in `COUPLED` `solveEnergy yes` with
  `Pr = mu*cp/kappa`: the solver's **passive** temperature (no buoyancy, constant properties). A
  heat-flux wall is a `fixedGradient` of `q/kappa`, the flux the passive equation's
  `alphaEff*grad(T)` carries once multiplied by `rho*cp`.

The steps are `coupledSimpleFoam` (decomposed, over MPI and reconstructed when `cores > 1`), then
`foamPostProcess -latestTime`: the solver never calls the `controlDict` functions (its loop is not
`Time::run()`), so the run's flow and pressure monitors are evaluated on the final fields
afterwards, from the same functions an `of14` run uses.

Refused by name, before a file is written: a transient run, a 3D mesh above `DIRECT_MAX_CELLS_3D`
on one core (the serial direct solver's measured limit), gravity with energy on (no buoyancy), and
anything the `of14` writer refuses. A host whose coupledSimpleFoam has no PETSc (FoamWin) refuses
a parallel run in the agent.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from stokes.compile.errors import CompileInvalid, CompileUnsupported
from stokes.compile.foam import Dict, Entry, Quoted, Raw, Word, render_file
from stokes.compile.snapshot import SolveSnapshot
from stokes.engines.turbulence import names_for

ENGINE = "coupled.simple"
#: Enters the run basis: a change to the case this writes marks earlier coupled runs stale.
#: `coupled/2`: MPI runs on the PETSc backend, and a looser `T` inner tolerance.
WRITER_VERSION = "coupled/2"
APPLICATION = "coupledSimpleFoam"
#: The momentum models coupledSimpleFoam has been exercised with (its README): laminar, k-epsilon
#: and k-omega SST. The catalogue offers exactly these.
TURBULENCE: tuple[str, ...] = names_for(ENGINE)

#: The largest 3D mesh the serial direct solver (Eigen `SparseLU`) is offered on. Measured on a
#: laminar 3D tee (ADR-0044 decision 3, 2026-11-21, 24-core WSL host): 72,965 cells converged
#: in 18 iterations, 17.8 min, 10.4 GB; 136,878 cells took 26 GB and ~440 s per iteration and
#: had not converged after 30 min; 347,604 cells exhausted ~45 GB in the first factorisation.
#: The iterative alternatives are no rescue in serial: Eigen `BiCGSTAB` took ~585 s and serial
#: PETSc ~100 s per iteration at 347k cells. A 2D mesh's fill-in is far smaller, so 2D is
#: not limited.
DIRECT_MAX_CELLS_3D = 80_000

#: **The parallel backend's settings** (ADR-0044 decision 9), used whenever `cores > 1`: PETSc
#: `KSPFBCGS` with a Schur field split, the velocity block ILU under block Jacobi and the pressure
#: (Schur) block one hypre BoomerAMG V-cycle with hypre's three-dimensional settings. The inner
#: tolerance adapts per outer iteration (Eisenstat-Walker, 1e-8..1e-2), the preconditioner is
#: rebuilt every 8 outer iterations, and pseudo-transient continuation (`pseudoCo 50`) damps the
#: outer loop. Measured on 6 ranks (2026-11-21, WSL): a 1M-cell square duct converged in 16 outer
#: iterations and 70 s against SIMPLEC's 795 iterations and 280 s, with fRe identical to 4 digits;
#: a 3D lid-driven cavity needed the damping (without it no setting converged in 200 iterations).
#: `selfp` rather than the solver's `a11` large-case option: `a11` stalled on a snappy-style
#: mesh (prisms and polyhedra, 53 degree non-orthogonality) where `selfp` converged.
PETSC_OPTIONS = " ".join(
    (
        "-ksp_type fbcgs",
        "-pc_type fieldsplit -pc_fieldsplit_type schur",
        "-pc_fieldsplit_schur_fact_type upper -pc_fieldsplit_schur_precondition selfp",
        "-pc_fieldsplit_0_fields 0,1,2 -pc_fieldsplit_1_fields 3",
        "-fieldsplit_0_ksp_type preonly -fieldsplit_0_pc_type bjacobi",
        "-fieldsplit_0_sub_pc_type ilu",
        "-fieldsplit_1_ksp_type preonly -fieldsplit_1_pc_type hypre",
        "-fieldsplit_1_pc_hypre_type boomeramg",
        "-fieldsplit_1_pc_hypre_boomeramg_strong_threshold 0.5",
        "-fieldsplit_1_pc_hypre_boomeramg_coarsen_type HMIS",
        "-fieldsplit_1_pc_hypre_boomeramg_interp_type ext+i",
        "-fieldsplit_1_pc_hypre_boomeramg_P_max 4",
        "-fieldsplit_1_pc_hypre_boomeramg_truncfactor 0.3",
        "-fieldsplit_1_pc_hypre_boomeramg_agg_nl 1",
        "-fieldsplit_1_pc_hypre_boomeramg_relax_type_all SOR/Jacobi",
    )
)

#: The incompressible writer this one builds on, and the engine id it is written for.
_BASE_ENGINE = "of14.incompressibleFluid"


def write(snap: SolveSnapshot) -> dict[str, str]:
    """The case: the `of14` incompressible files, with `COUPLED`, the schemes and `0/T`."""
    validate(snap)
    from stokes.compile import of14

    base = dataclasses.replace(snap, engine=_BASE_ENGINE, energy=False)
    files = of14.write(base)
    files["system/fvSolution"] = render_file("fvSolution", fv_solution(snap), location="system")
    files["system/fvSchemes"] = render_file("fvSchemes", fv_schemes(snap), location="system")
    if snap.energy:
        files["0/T"] = render_file("T", temperature_field(snap), cls="volScalarField", location="0")
    # `of14.write` already added the decomposition for `cores > 1`.
    return files


def steps(snap: SolveSnapshot) -> list[dict[str, Any]]:
    """Solve (decomposed above one core), then evaluate the monitors on the final fields."""
    from stokes.compile.parallel import decompose_step, reconstruct_fields_step

    validate(snap)
    run: list[dict[str, Any]] = []
    if snap.cores > 1:
        run.append(decompose_step())
    run.append(
        {
            "name": "solve",
            "argv": [APPLICATION],
            **({"ranks": snap.cores} if snap.cores > 1 else {}),
            "timeout_s": 7200,
            "parse": "foamRun",
            "parse_params": {"end_time": snap.iterations},
        }
    )
    if snap.cores > 1:
        run.append(reconstruct_fields_step())
    run.append(
        {
            "name": "monitors",
            "argv": ["foamPostProcess", "-latestTime"],
            "timeout_s": 1800,
        }
    )
    return run


def basis(snap: SolveSnapshot) -> str:
    """This writer's part of the run basis."""
    return f"{WRITER_VERSION}:{snap.engine}:{snap.turbulence}"


def validate(snap: SolveSnapshot) -> None:
    """What coupledSimpleFoam cannot be asked for, refused in the user's words."""
    if snap.engine != ENGINE:
        raise CompileUnsupported(
            f"engine {snap.engine!r} is not written by the coupled writer",
            details={"code": "compile.unsupported", "engine": snap.engine},
        )
    if snap.transient:
        raise CompileUnsupported(
            "coupledSimpleFoam is steady-state only; turn Transient off, or use the "
            "of14.incompressibleFluid engine for a transient run",
            details={"code": "compile.unsupported", "engine": ENGINE, "transient": True},
        )
    if snap.cores == 1 and snap.cells > DIRECT_MAX_CELLS_3D and not is_two_d(snap):
        raise CompileUnsupported(
            f"this mesh has {snap.cells:,} cells; coupledSimpleFoam's serial direct solver is "
            f"offered up to {DIRECT_MAX_CELLS_3D:,} cells in 3D (memory and time grow much faster "
            "than the cell count: 137k cells needed 26 GB and did not converge in 30 minutes). "
            "Set cores to 2 or more to run it on the parallel PETSc backend (a Linux or WSL "
            "host), use a coarser mesh, or use the of14.incompressibleFluid engine",
            details={
                "code": "compile.unsupported",
                "engine": ENGINE,
                "cells": snap.cells,
                "limit": DIRECT_MAX_CELLS_3D,
            },
        )
    if snap.turbulence not in TURBULENCE:
        raise CompileUnsupported(
            f"coupledSimpleFoam has been exercised with {', '.join(TURBULENCE)} only; "
            f"{snap.turbulence!r} is not offered with it",
            details={"code": "compile.unsupported", "turbulence": snap.turbulence},
        )
    if snap.energy:
        if any(abs(g) > 0.0 for g in snap.gravity):
            raise CompileUnsupported(
                "coupledSimpleFoam's temperature is a passive scalar with no buoyancy; set gravity "
                "to zero, or use the of14.fluid engine for buoyant flow",
                details={"code": "compile.unsupported", "engine": ENGINE, "gravity": True},
            )
        if snap.mu <= 0 or snap.cp <= 0 or snap.kappa <= 0:
            raise CompileInvalid(
                "the energy equation needs the fluid's viscosity, specific heat and conductivity",
                details={"code": "compile.invalid", "engine": ENGINE},
            )
        for condition in snap.patches:
            if condition.thermal == "heatFlux" and condition.heat_flux is None:
                raise CompileInvalid(
                    f"patch {condition.name!r} is a heat-flux boundary without a heat flux",
                    details={"code": "compile.invalid", "patch": condition.name},
                )


# ------------------------------------------------------------------------------- dictionaries


def is_two_d(snap: SolveSnapshot) -> bool:
    """Whether the mesh is 2D: it has an `empty` or `wedge` patch (ADR-0031)."""
    from stokes.compile.snapshot import TWO_D_PATCH_TYPES

    return any(condition.patch_type in TWO_D_PATCH_TYPES for condition in snap.patches)


def prandtl(snap: SolveSnapshot) -> float:
    """The laminar Prandtl number of the domain's fluid, `mu*cp/kappa`."""
    return snap.mu * snap.cp / snap.kappa


def coupled_dict(snap: SolveSnapshot) -> Dict:
    """`COUPLED`: the outer loop, the backend, the pressure level, the convergence, the energy."""
    residuals = [Entry("U", snap.residual_target), Entry("p", snap.residual_target)]
    if snap.energy:
        residuals.append(Entry("T", snap.residual_target))
    entries: list[Entry] = [Entry("nOuterIterations", int(snap.iterations))]
    if snap.cores > 1:
        entries += [
            Entry("linearSolver", Word("PETSc")),
            Entry("pseudoTransient", Word("yes")),
            Entry("pseudoCo", 50),
            Entry("adaptiveTolerance", Word("yes")),
            Entry("relTolMax", 1e-2),
            Entry("relTolMin", 1e-8),
            Entry("adaptiveGamma", 0.9),
            Entry("adaptiveAlpha", 1.5),
            Entry("pcReuseInterval", 8),
            Entry("maxLinearIter", 500),
            Entry("petscOptions", Quoted(PETSC_OPTIONS)),
        ]
    else:
        entries.append(Entry("linearSolver", Word("SparseLU")))
    entries += [Entry("pRefCell", 0), Entry("pRefValue", 0)]
    if snap.energy:
        entries += [
            Entry("solveEnergy", Word("yes")),
            Entry("Pr", prandtl(snap)),
            Entry("Prt", 0.85),
        ]
    entries.append(Entry("residualControl", Dict(tuple(residuals))))
    return Dict(tuple(entries))


def fv_solution(snap: SolveSnapshot) -> Dict:
    """The `of14` steady `fvSolution`, with a `T` solver (energy on) and `COUPLED` appended."""
    from stokes.compile import of14

    base = of14._fv_solution(dataclasses.replace(snap, engine=_BASE_ENGINE, energy=False))
    items = list(base.items)
    if snap.energy:
        items = _add_to(
            items,
            "solvers",
            Entry(
                "T",
                Dict(
                    (
                        Entry("solver", Word("PBiCGStab")),
                        Entry("preconditioner", Word("DILU")),
                        # A loose relative tolerance, as for p and U: `residualControl` decides
                        # convergence, and solving T to 1e-12 every outer iteration only costs
                        # time (the solver's BENCHMARKS.md 9.5: no reported digit changed).
                        Entry("tolerance", 1e-10),
                        Entry("relTol", 0.01),
                    )
                ),
            ),
        )
        # `1`, not `1.0`: an unset factor writes the dictionary as before (`T 1;`).
        items = _temperature_relaxation(
            items, 1 if snap.relax_energy is None else snap.relax_energy
        )
    items.append(Entry("COUPLED", coupled_dict(snap)))
    return Dict(tuple(items))


def _temperature_relaxation(items: list[Any], factor: float | int) -> list[Any]:
    """`relaxationFactors/equations` with `T <factor>` (1 unless the user set `relax_energy`),
    ahead of the SIMPLE case's `".*" 0.9`.

    coupledSimpleFoam calls `TEqn.relax()`, which takes the first matching factor, and the `of14`
    case's `".*" 0.9` (written for SIMPLE) caught `T`. The temperature equation is linear once
    the coupled solve has fixed the flux, so it needs no relaxation; relaxed, each outer iteration
    moved it only part of the way. Measured on an 800k-cell heated duct (6 ranks): 267 outer
    iterations and 472 s relaxed, 16 and 57 s unrelaxed, with the same Nusselt number.
    """
    out: list[Any] = []
    for item in items:
        if not (
            isinstance(item, Entry)
            and item.key == "relaxationFactors"
            and isinstance(item.value, Dict)
        ):
            out.append(item)
            continue
        inner: list[Any] = []
        for sub in item.value.items:
            if isinstance(sub, Entry) and sub.key == "equations" and isinstance(sub.value, Dict):
                kept = [e for e in sub.value.items if not (isinstance(e, Entry) and e.key == "T")]
                inner.append(Entry("equations", Dict((Entry("T", factor), *kept))))
            else:
                inner.append(sub)
        out.append(Entry("relaxationFactors", Dict(tuple(inner))))
    return out


def fv_schemes(snap: SolveSnapshot) -> Dict:
    """The `of14` steady schemes, with the coupled assembly's stress terms and `div(phi,T)`."""
    from stokes.compile import of14

    base = of14._fv_schemes(dataclasses.replace(snap, engine=_BASE_ENGINE, energy=False))
    extra = [
        Entry("div((nu*dev2(T(grad(U)))))", Raw("Gauss linear")),
        Entry("div(devTauCorrFlux)", Raw("Gauss linear")),
    ]
    if snap.energy:
        extra.append(Entry("div(phi,T)", Raw("Gauss linearUpwind grad(T)")))
    items = list(base.items)
    for entry in extra:
        items = _add_to(items, "divSchemes", entry)
    return Dict(tuple(items))


def temperature_field(snap: SolveSnapshot) -> Dict:
    """`0/T` in kelvin: the boundaries' thermal conditions as the passive equation reads them."""
    boundary: list[Entry] = []
    for condition in snap.patches:
        boundary.append(Entry(condition.name, _thermal_patch(snap, condition)))
    return Dict(
        (
            Entry("dimensions", Raw("[0 0 0 1 0 0 0]")),
            Entry("internalField", Raw(f"uniform {_num(snap.T_ref)}")),
            Entry("boundaryField", Dict(tuple(boundary))),
        )
    )


def _thermal_patch(snap: SolveSnapshot, condition: Any) -> Dict:  # Any: PatchCondition
    """One patch of `0/T`."""
    if condition.patch_type == "empty":
        return Dict((Entry("type", Word("empty")),))
    if condition.patch_type in ("symmetry", "symmetryPlane"):
        return Dict((Entry("type", Word(condition.patch_type)),))
    kind = condition.thermal
    if kind == "fixedValue" and condition.temperature is not None:
        value = Raw(f"uniform {_num(float(condition.temperature))}")
        return Dict((Entry("type", Word("fixedValue")), Entry("value", value)))
    if kind == "inletOutlet" and condition.temperature is not None:
        value = Raw(f"uniform {_num(float(condition.temperature))}")
        return Dict(
            (
                Entry("type", Word("inletOutlet")),
                Entry("inletValue", value),
                Entry("value", value),
            )
        )
    if kind == "heatFlux" and condition.heat_flux is not None:
        # A wall flux q [W/m2], positive INTO the fluid (Stokes' convention, as `of14.fluid`), is
        # kappa*(T_face - T_cell)/d, and OpenFOAM's fixedGradient is exactly that snGrad: a wall
        # heating the fluid is hotter than the cell beside it, so the gradient is +q/kappa.
        gradient = float(condition.heat_flux) / snap.kappa
        return Dict(
            (
                Entry("type", Word("fixedGradient")),
                Entry("gradient", Raw(f"uniform {_num(gradient)}")),
            )
        )
    return Dict((Entry("type", Word("zeroGradient")),))


def _add_to(items: list[Any], key: str, entry: Entry) -> list[Any]:
    """`items` with `entry` added to (or replacing its key in) the sub-dictionary `key`."""
    out: list[Any] = []
    found = False
    for item in items:
        if isinstance(item, Entry) and item.key == key and isinstance(item.value, Dict):
            kept = tuple(
                i for i in item.value.items if not (isinstance(i, Entry) and i.key == entry.key)
            )
            out.append(Entry(key, Dict((*kept, entry))))
            found = True
        else:
            out.append(item)
    if not found:
        out.append(Entry(key, Dict((entry,))))
    return out


def _num(value: float) -> str:
    return repr(float(value))
