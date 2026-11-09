"""Pure Foundation 14 single-fluid case writer with explicit thermodynamics.

Constant-density energy cases retain the validated buoyant p_rgh formulation (ADR-0027).
Ideal gases use absolute pressure and sensible enthalpy, with zero gravity. Shared momentum
transport supplies all supported RAS and LES models. Time controls distinguish steady
iterations from physical transient seconds. The module retains the existing thermal boundary
and hydrostatic initialisation machinery, hence its size beyond the usual 500-line guideline.
"""

from __future__ import annotations

import math
from typing import Any

from stokes.compile.errors import CompileInvalid, CompileUnsupported
from stokes.compile.foam import (
    WORD_RE,
    Dict,
    Entry,
    FuncInclude,
    Raw,
    Tuple_,
    Word,
    render_file,
)
from stokes.compile.parallel import decompose_par_dict, decompose_step, reconstruct_fields_step
from stokes.compile.snapshot import (
    CONSTRAINT_KINDS,
    THERMAL_KINDS,
    PatchCondition,
    SolveSnapshot,
    laplacian_scheme,
    sn_grad_scheme,
)

from . import fluid_thermo, turbulence
from .boundaries import validate_patch, velocity_field
from .control import average_function, stop_function, transient_write

#: Changes in thermodynamics, time controls and model fields invalidate earlier generated cases.
WRITER_VERSION = "of14-fluid/6"  # 6: every equation under-relaxed (the turbulence fields too)

#: The one engine this writer expresses (ADR-0027 decision 1).
ENGINE = "of14.fluid"

_SOLVER = "fluid"
_FLOW_RATE_FUNC = "patchFlowRate"
_PATCH_AVERAGE_FUNC = "patchAverage"

_DIMS_VELOCITY = Raw("[0 1 -1 0 0 0 0]")
_DIMS_PRESSURE = Raw("[1 -1 -2 0 0 0 0]")
_DIMS_TEMPERATURE = Raw("[0 0 0 1 0 0 0]")
_DIMS_ACCELERATION = Raw("[0 1 -2 0 0 0 0]")


def write(snap: SolveSnapshot) -> dict[str, str]:
    """The case files, with the thermodynamic formulation and turbulence fields it needs.

    Raises `CompileUnsupported` for unsupported physics and `CompileInvalid` for invalid values.
    """
    _validate(snap)
    written: dict[str, str] = {
        "system/controlDict": render_file("controlDict", _control_dict(snap), location="system"),
        "system/fvSchemes": render_file("fvSchemes", _fv_schemes(snap), location="system"),
        "system/fvSolution": render_file("fvSolution", _fv_solution(snap), location="system"),
        "constant/physicalProperties": render_file(
            "physicalProperties", _physical_properties(snap), location="constant"
        ),
        "constant/momentumTransport": render_file(
            "momentumTransport", turbulence.momentum_transport(snap), location="constant"
        ),
        "constant/g": render_file(
            "g", _gravity(snap), cls="uniformDimensionedVectorField", location="constant"
        ),
        "constant/pRef": render_file(
            "pRef", _p_ref(snap), cls="uniformDimensionedScalarField", location="constant"
        ),
        "0/U": render_file("U", _velocity(snap), cls="volVectorField", location="0"),
        "0/p": render_file("p", _pressure(snap), cls="volScalarField", location="0"),
        "0/p_rgh": render_file("p_rgh", _p_rgh(snap), cls="volScalarField", location="0"),
        "0/T": render_file("T", _temperature(snap), cls="volScalarField", location="0"),
        "system/fvConstraints": render_file("fvConstraints", _fv_constraints(), location="system"),
    }
    if fluid_thermo.is_gas(snap):
        # The presence of p_rgh selects the buoyant formulation in the external solver.
        for name in ("0/p_rgh", "constant/g", "constant/pRef"):
            del written[name]
    if _hydrostatic(snap):
        written["0/ph_rgh"] = render_file(
            "ph_rgh", _p_rgh(snap), cls="volScalarField", location="0"
        )
    written.update(turbulence.field_files(snap))
    if snap.cores > 1:
        written["system/decomposeParDict"] = decompose_par_dict(snap.cores)
    return written


def steps(snap: SolveSnapshot) -> list[dict[str, Any]]:
    """The job steps that run this case: `foamRun -solver fluid`, decomposed above one rank.

    The shape matches the incompressible writer's so the executor can dispatch on the engine
    alone. The `foamRun` parser reads the energy residual as `e` or `h`, and the
    agent's flag list accepts `-solver fluid`.
    """
    _validate(snap)
    run: list[dict[str, Any]] = []
    if snap.cores > 1:
        run.append(decompose_step())
    run.append(
        {
            "name": "solve",
            "argv": ["foamRun", "-solver", _SOLVER],
            **({"ranks": snap.cores} if snap.cores > 1 else {}),
            "timeout_s": 7200,
            "parse": "foamRun",
            "parse_params": {"end_time": snap.end_time if snap.transient else snap.iterations},
        }
    )
    if snap.cores > 1:
        run.append(reconstruct_fields_step())
    return run


def basis(snap: SolveSnapshot) -> str:
    """This writer's part of the run basis, as `stokes.compile.of14.basis` is for its engine."""
    return f"{WRITER_VERSION}:{snap.engine}:{snap.turbulence}"


# ---------------------------------------------------------------------- validation


def _validate(snap: SolveSnapshot) -> None:
    """Everything this case cannot be, checked before a single file is printed."""
    if snap.engine != ENGINE:
        raise CompileUnsupported(
            f"engine {snap.engine!r} is not written by the fluid writer; it writes {ENGINE}",
            details={"code": "compile.unsupported", "engine": snap.engine},
        )
    if not snap.energy:
        raise CompileUnsupported(
            f"{ENGINE} always solves the energy equation; turn energy on or use "
            "of14.incompressibleFluid",
            details={"code": "compile.unsupported", "energy": False},
        )
    _validate_numbers(snap)
    fluid_thermo.validate(snap)
    turbulence.validate(snap)
    if not snap.patches:
        raise CompileInvalid("a case needs at least one patch", details={"code": "compile.invalid"})
    seen: set[str] = set()
    for condition in snap.patches:
        if condition.name in seen:
            raise CompileInvalid(
                f"two patches are called {condition.name!r}",
                details={"code": "compile.invalid", "patch": condition.name},
            )
        seen.add(condition.name)
        _word_or_refuse(condition.name, "patch name")
        _word_or_refuse(condition.patch_type, "patch type")
        validate_patch(condition, snap.rho)
        physical: tuple[float, ...] = condition.velocity or ()
        if condition.pressure is not None:
            physical += (condition.pressure,)
        if condition.centroid is not None:
            physical += condition.centroid
        if not all(math.isfinite(value) for value in physical):
            raise CompileInvalid(f"patch {condition.name!r} has non-finite physical values")
        _validate_thermal(condition)
        if _has_gravity(snap) and condition.kind == "pressure_outlet" and not condition.centroid:
            raise CompileInvalid(
                f"the pressure outlet {condition.name!r} has no position; with gravity on, its "
                "p_rgh value depends on its height",
                details={"code": "compile.invalid", "patch": condition.name},
            )
    for name in snap.monitor_patches:
        if name not in seen:
            raise CompileInvalid(
                f"monitor patch {name!r} is not one of the case's patches",
                details={"code": "compile.invalid", "patch": name},
            )


def _validate_numbers(snap: SolveSnapshot) -> None:
    """Counts, the residual target and the material: each must be a usable positive number."""
    if (
        isinstance(snap.cores, bool)
        or not isinstance(snap.cores, int)
        or not 1 <= snap.cores <= 256
    ):
        raise CompileInvalid(
            f"cores must be an integer from 1 to 256; got {snap.cores!r}",
            details={"code": "compile.invalid", "cores": snap.cores},
        )
    for name in ("iterations", "write_interval"):
        if getattr(snap, name) < 1:
            raise CompileInvalid(
                f"{name} must be at least 1; got {getattr(snap, name)}",
                details={"code": "compile.invalid", name: getattr(snap, name)},
            )
    positive: tuple[str, ...] = ("residual_target", "rho", "mu", "cp", "kappa", "T_ref", "p_ref")
    if snap.transient:
        positive += ("delta_t", "end_time")
    for name in positive:
        if not math.isfinite(getattr(snap, name)) or not getattr(snap, name) > 0:
            raise CompileInvalid(
                f"{name} must be positive for the energy equation; got {getattr(snap, name)}",
                details={"code": "compile.invalid", name: getattr(snap, name)},
            )
    if not all(math.isfinite(value) for value in snap.gravity):
        raise CompileInvalid("gravity components must be finite")


def _validate_thermal(condition: PatchCondition) -> None:
    """A patch's temperature condition: a kind this writer has, with the value that kind needs."""
    thermal = condition.thermal
    if thermal is None:
        if condition.kind in CONSTRAINT_KINDS:
            return
        raise CompileInvalid(
            f"patch {condition.name!r} has no temperature condition",
            details={"code": "compile.invalid", "patch": condition.name},
        )
    if thermal not in THERMAL_KINDS:
        # A convection wall lands here: refused, not written as adiabatic (ADR-0027).
        raise CompileUnsupported(
            f"patch {condition.name!r} has temperature condition {thermal!r}, which the fluid "
            f"writer cannot express; known: {', '.join(THERMAL_KINDS)}",
            details={"code": "compile.unsupported", "patch": condition.name, "thermal": thermal},
        )
    if thermal in ("fixedValue", "inletOutlet") and (
        not math.isfinite(condition.temperature or 0.0) or not (condition.temperature or 0.0) > 0
    ):
        raise CompileInvalid(
            f"patch {condition.name!r} needs a temperature above 0 K; got {condition.temperature}",
            details={"code": "compile.invalid", "patch": condition.name},
        )
    if thermal == "heatFlux" and (
        condition.heat_flux is None or not math.isfinite(condition.heat_flux)
    ):
        raise CompileInvalid(
            f"the heat-flux wall {condition.name!r} needs a finite heat flux",
            details={"code": "compile.invalid", "patch": condition.name},
        )


def _has_gravity(snap: SolveSnapshot) -> bool:
    return any(float(component) != 0.0 for component in snap.gravity)


def _hydrostatic(snap: SolveSnapshot) -> bool:
    """Whether the case starts from a hydrostatic `p_rgh` (see the module docstring).

    Only with gravity, and only with a pressure outlet: with every patch `fixedFluxPressure`
    the `ph_rgh` equation has no level and the closed volume needs no column held up.
    """
    return (
        not fluid_thermo.is_gas(snap)
        and _has_gravity(snap)
        and any(c.kind == "pressure_outlet" for c in snap.patches)
    )


def _word_or_refuse(text: str, what: str) -> None:
    """Refuse a name a dictionary key cannot be, naming the value rather than a printed line."""
    if not WORD_RE.match(text or ""):
        raise CompileInvalid(
            f"the {what} {text!r} is not an OpenFOAM word",
            details={"code": "compile.invalid", "value": text},
        )


# ---------------------------------------------------------------------- system/


def _control_dict(snap: SolveSnapshot) -> Dict:
    """Time and output controls; transient durations are physical seconds."""
    items: list[Any] = [
        Entry("solver", Word(_SOLVER)),
        Entry("startFrom", Word("startTime")),
        Entry("startTime", 0),
        Entry("stopAt", Word("endTime")),
        Entry("endTime", snap.end_time if snap.transient else snap.iterations),
        Entry("deltaT", snap.delta_t if snap.transient else 1),
        # timeStep/write_interval, or physical write times and the adaptive step (`control.py`).
        *transient_write(snap),
        Entry("purgeWrite", 2),
        Entry("writeFormat", Word("ascii")),
        Entry("writePrecision", 8),
        Entry("writeCompression", Word("off")),
        Entry("timeFormat", Word("general")),
        Entry("timePrecision", 6),
        Entry("runTimeModifiable", False),
        # **Stop writes rather than kills** (`control.py`): always present, monitors or not.
        Entry(
            "functions",
            Dict(
                (
                    stop_function(),
                    *_monitors(snap),
                    *average_function(snap, fluid_thermo.energy_field(snap)),
                )
            ),
        ),
    ]
    return Dict(tuple(items))


def _monitors(snap: SolveSnapshot) -> tuple[Any, ...]:
    """Flow rate and mean `p` and `T` per monitor patch. `phi` is a mass flux here, so kg/s."""
    items: list[Any] = []
    for name in snap.monitor_patches:
        items.append(
            FuncInclude(f"{_FLOW_RATE_FUNC}(patch={name})", f"mass flow rate through {name}")
        )
        items.append(
            FuncInclude(
                f"{_PATCH_AVERAGE_FUNC}(patch={name}, fields=(p T))",
                f"area-averaged p and T on {name}",
            )
        )
    return tuple(items)


#: Fluent's own default temperature limits, K: far outside any real solution, so they never
#: change a converged answer; they stop an energy overshoot from becoming a fatal error.
T_LIMITS = (1.0, 5000.0)


def _fv_constraints() -> Dict:
    """`system/fvConstraints`: `limitTemperature` over the whole domain at `T_LIMITS`.

    **Measured on the mixing elbow (2026-11-08):** k-omega SST on a polyhedral mesh and
    realizable k-epsilon on a tetrahedral one both stopped with "Negative initial temperature"
    (iterations 57 and 113): one cell's energy overshot below absolute zero during the first
    iterations, and the thermo's temperature inversion is fatal. The constraint bounds the energy
    after each solve so the run carries on and settles, which is what Fluent's limits do.
    """
    low, high = T_LIMITS
    return Dict(
        (
            Entry(
                "limitT",
                Dict(
                    (
                        Entry("type", Word("limitTemperature")),
                        # OpenFOAM 14 selects with `cellZone all;` (`generatedCellZone`); the
                        # older `selectionMode all;` aborts with "cellZone not specified".
                        Entry("cellZone", Word("all")),
                        Entry("min", low),
                        Entry("max", high),
                    )
                ),
            ),
        )
    )


def _grad_schemes(snap: SolveSnapshot) -> tuple[Entry, ...]:
    """`Gauss linear`, with the velocity gradient cell-limited on a non-orthogonal mesh."""
    default = Entry("default", Raw("Gauss linear"))
    if not snap.non_orthogonal:
        return (default,)
    return (default, Entry("grad(U)", Raw("cellLimited Gauss linear 1")))


def _relaxation(snap: SolveSnapshot) -> tuple[float, float]:
    """`(pressure, equations)` under-relaxation: tighter on a non-orthogonal mesh."""
    return (0.2, 0.5) if snap.non_orthogonal else (0.3, 0.7)


def _fv_schemes(snap: SolveSnapshot) -> Dict:
    """Energy-aware schemes with physical time derivatives for transient runs."""
    energy = fluid_thermo.energy_field(snap)
    convection = "Gauss" if snap.transient else "bounded Gauss"
    div_schemes = [
        Entry("default", Word("none")),
        Entry("div(phi,U)", Raw(f"{convection} linearUpwind grad(U)")),
        Entry(f"div(phi,{energy})", Raw(f"{convection} upwind")),
        # The kinetic energy in the energy equation: central differencing is what tipped the elbow's
        # tetrahedral mesh into a negative temperature, so a non-orthogonal mesh takes upwind.
        Entry("div(phi,K)", Raw(f"{convection} {'upwind' if snap.non_orthogonal else 'linear'}")),
    ]
    if energy == "e":
        div_schemes.append(Entry("div(phi,(p|rho))", Raw(f"{convection} upwind")))
    div_schemes.append(Entry("div(((rho*nuEff)*dev2(T(grad(U)))))", Raw("Gauss linear")))
    base = Dict(
        (
            Entry(
                "ddtSchemes",
                Dict((Entry("default", Word("Euler" if snap.transient else "steadyState")),)),
            ),
            Entry("gradSchemes", Dict(_grad_schemes(snap))),
            Entry("divSchemes", Dict(tuple(div_schemes))),
            Entry("laplacianSchemes", Dict((Entry("default", Raw(laplacian_scheme(snap))),))),
            Entry("interpolationSchemes", Dict((Entry("default", Word("linear")),))),
            Entry("snGradSchemes", Dict((Entry("default", Raw(sn_grad_scheme(snap))),))),
        )
    )
    return turbulence.augment_schemes(base, snap)


def _fv_solution(snap: SolveSnapshot) -> Dict:
    """PIMPLE controls with a separate final solve and no early stop for physical transients."""
    pressure = fluid_thermo.pressure_field(snap)
    energy = fluid_thermo.energy_field(snap)
    hydrostatic = _hydrostatic(snap)

    def pressure_solver(final: bool = False) -> Dict:
        return Dict(
            (
                Entry("solver", Word("GAMG")),
                Entry("smoother", Word("GaussSeidel")),
                Entry("tolerance", 1e-07),
                Entry("relTol", 0 if final else 0.1),
            )
        )

    def transport_solver(final: bool = False) -> Dict:
        return Dict(
            (
                Entry("solver", Word("smoothSolver")),
                Entry("smoother", Word("symGaussSeidel")),
                Entry("tolerance", 1e-07),
                Entry("relTol", 0 if final else 0.1),
            )
        )

    solvers = [
        Entry('"rho.*"', Dict((Entry("solver", Word("diagonal")),))),
        Entry(pressure, pressure_solver()),
    ]
    if hydrostatic:
        solvers.append(Entry("ph_rgh", pressure_solver()))
    solvers.append(Entry(f'"(U|{energy})"', transport_solver()))
    if snap.transient:
        solvers.extend(
            (
                Entry(f"{pressure}Final", pressure_solver(final=True)),
                Entry(f'"(U|{energy})Final"', transport_solver(final=True)),
            )
        )
    pimple = [
        Entry("momentumPredictor", Word("yes")),
        Entry("nNonOrthogonalCorrectors", 2 if snap.non_orthogonal else 1),
    ]
    if hydrostatic:
        pimple += [
            Entry("hydrostaticInitialisation", Word("yes")),
            Entry("nHydrostaticCorrectors", 5),
        ]
    if snap.transient:
        pimple += [Entry("nOuterCorrectors", 2), Entry("nCorrectors", 2)]
    else:
        pimple.append(
            Entry(
                "residualControl",
                Dict(
                    tuple(Entry(field, snap.residual_target) for field in (pressure, "U", energy))
                ),
            )
        )
    entries = [Entry("solvers", Dict(tuple(solvers))), Entry("PIMPLE", Dict(tuple(pimple)))]
    if not snap.transient:
        p_relax, eq_relax = _relaxation(snap)
        entries.append(
            Entry(
                "relaxationFactors",
                Dict(
                    (
                        Entry("fields", Dict((Entry(pressure, p_relax),))),
                        # `".*"` relaxes the turbulence equations too, as the incompressible
                        # writer's `".*" 0.9` does. Without it k-omega SST went to NaN in its
                        # first omega solve on the mixing elbow (1000 smoother sweeps, then nan).
                        Entry(
                            "equations",
                            Dict(
                                (
                                    Entry("U", eq_relax),
                                    Entry(energy, eq_relax),
                                    Entry('".*"', eq_relax),
                                )
                            ),
                        ),
                    )
                ),
            )
        )
    return turbulence.augment_solution(Dict(tuple(entries)), snap)


# ---------------------------------------------------------------------- constant/


def _physical_properties(snap: SolveSnapshot) -> Dict:
    """The explicit thermodynamic model, generated from SI material properties."""
    return fluid_thermo.physical_properties(snap)


def _gravity(snap: SolveSnapshot) -> Dict:
    """`constant/g`: the snapshot's gravity, m/s². Zero is still written: the solver reads `g`
    whenever `0/p_rgh` exists (`buoyancy.C`, `MUST_READ`)."""
    gx, gy, gz = snap.gravity
    return Dict(
        (
            Entry("dimensions", _DIMS_ACCELERATION),
            Entry("value", Tuple_((float(gx), float(gy), float(gz)))),
        )
    )


def _p_ref(snap: SolveSnapshot) -> Dict:
    """`constant/pRef`: the operating pressure, Pa. `p = p_rgh + rho*gh + pRef`."""
    return Dict((Entry("dimensions", _DIMS_PRESSURE), Entry("value", float(snap.p_ref))))


# ---------------------------------------------------------------------- 0/


def _uniform(value: float) -> Raw:
    return Raw(f"uniform {float(value)}")


def _boundary(snap: SolveSnapshot, field: Any) -> Dict:
    """One `boundaryField` entry per patch, in the snapshot's order."""
    return Dict(tuple(Entry(condition.name, field(condition)) for condition in snap.patches))


def _volume_field(dims: Raw, internal: Raw, boundary: Dict) -> Dict:
    return Dict(
        (
            Entry("dimensions", dims),
            Entry("internalField", internal),
            Entry("boundaryField", boundary),
        )
    )


def _velocity(snap: SolveSnapshot) -> Dict:
    """`0/U`: the incompressible writer's velocity table, unchanged."""
    return _volume_field(
        _DIMS_VELOCITY,
        Raw("uniform (0 0 0)"),
        _boundary(snap, lambda c: velocity_field(c, snap.rho)),
    )


def _pressure(snap: SolveSnapshot) -> Dict:
    """Absolute pressure: solved directly for ideal gases, derived from p_rgh otherwise."""
    p_ref = _uniform(snap.p_ref)

    def entry(condition: PatchCondition) -> Dict:
        if condition.kind in CONSTRAINT_KINDS:
            return Dict((Entry("type", Word(condition.patch_type)),))
        if fluid_thermo.is_gas(snap):
            if condition.kind == "pressure_outlet":
                return Dict(
                    (
                        Entry("type", Word("fixedValue")),
                        Entry("value", _uniform(snap.p_ref + (condition.pressure or 0.0))),
                    )
                )
            return Dict((Entry("type", Word("zeroGradient")),))
        return Dict((Entry("type", Word("calculated")), Entry("value", p_ref)))

    return _volume_field(_DIMS_PRESSURE, p_ref, _boundary(snap, entry))


def _p_rgh(snap: SolveSnapshot) -> Dict:
    """`0/p_rgh`: gauge, in pascals (**not** `p/rho`: this solver's pressure is a pressure).

    **An outlet is a `fixedValue` of `p - rho*g.c`**, one value at its centroid `c` (`hRef` is
    0): the static pressure the user set holds at the centroid and varies hydrostatically across
    the faces, as it does where the outlet opens into the same liquid at rest. Without gravity the
    value is the gauge `p`. Every other non-constraint patch is `fixedFluxPressure`, which is what
    makes the buoyant pressure balance a wall. The same dictionary is `0/ph_rgh` when the case
    starts hydrostatic. Why not `prghPressure`: module docstring.
    """
    gx, gy, gz = (float(component) for component in snap.gravity)

    def outlet_value(condition: PatchCondition) -> float:
        gauge = float(condition.pressure or 0.0)
        if not _has_gravity(snap) or condition.centroid is None:
            return gauge
        cx, cy, cz = condition.centroid
        return round(gauge - snap.rho * (gx * cx + gy * cy + gz * cz), 6)

    def entry(condition: PatchCondition) -> Dict:
        if condition.kind in CONSTRAINT_KINDS:
            return Dict((Entry("type", Word(condition.patch_type)),))
        if condition.kind == "pressure_outlet":
            return Dict(
                (
                    Entry("type", Word("fixedValue")),
                    Entry("value", _uniform(outlet_value(condition))),
                )
            )
        return Dict((Entry("type", Word("fixedFluxPressure")), Entry("value", _uniform(0.0))))

    return _volume_field(_DIMS_PRESSURE, _uniform(0.0), _boundary(snap, entry))


def _temperature(snap: SolveSnapshot) -> Dict:
    """`0/T`: the material's `T_ref` inside, and one condition per patch from its `thermal`."""
    initial = _uniform(snap.T_ref)

    def entry(condition: PatchCondition) -> Dict:
        thermal = condition.thermal
        if (
            thermal is None
        ):  # a constraint patch (symmetry, empty, wedge): `_validate` refused every other `None`
            return Dict((Entry("type", Word(condition.patch_type)),))
        if thermal == "fixedValue":
            return Dict(
                (
                    Entry("type", Word("fixedValue")),
                    Entry("value", _uniform(condition.temperature or 0.0)),
                )
            )
        if thermal == "inletOutlet":
            backflow = _uniform(condition.temperature or 0.0)
            return Dict(
                (
                    Entry("type", Word("inletOutlet")),
                    Entry("inletValue", backflow),
                    Entry("value", backflow),
                )
            )
        if thermal == "heatFlux":
            # `q` is a Function1 of time, hence `constant`; positive heats the fluid.
            return Dict(
                (
                    Entry("type", Word("externalTemperature")),
                    Entry("q", Raw(f"constant {float(condition.heat_flux or 0.0)}")),
                    Entry("value", initial),
                )
            )
        return Dict((Entry("type", Word("zeroGradient")),))

    return _volume_field(_DIMS_TEMPERATURE, initial, _boundary(snap, entry))
