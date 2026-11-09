"""The OpenFOAM 14 case writer: a `SolveSnapshot` in, a `CaseTree` out (ADR-0008, T-212).

## One engine, deliberately

Incompressible flow with `foamRun -solver incompressibleFluid`: steady (SIMPLE) or transient
(PIMPLE), laminar or any momentum model Foundation 14 registers for this solver. The turbulence
files come from the shared `turbulence` module, the same one the fluid writer uses, so the two
cannot disagree about a model; a laminar steady case is written exactly as it always was. Anything
else — another solver, a condition kind that is not in `snapshot.KINDS` — is refused with
`compile.unsupported` rather than written approximately, because an unsupported condition that is
written as something similar produces a case that runs, converges and answers a different question.

## Everything is a pure function

`write(snapshot)` returns a `CaseTree` (relative path → text) and touches nothing: no tree, no
session, no
host, no filesystem. Two calls on equal snapshots return equal trees, which is what lets T-214
hash the
result as the run's basis and lets the goldens be byte-for-byte.

Every file goes through `stokes.compile.foam.render_file` — never a string template — so the
dictionaries
are written by the one printer that has a golden of its own and whose output has been run by
OpenFOAM
(`tests/golden/compile`).

## The keywords are copied from the install

Every key below was read from `$FOAM_TUTORIALS/incompressibleFluid/pitzDailySteady`, which is
the steady
reference — the plain `pitzDaily` is transient and has no `residualControl` or
`relaxationFactors` at all.
The evidence, with the exact lines, is in `.coordination/T200-INVENTORY.md`.

**Two corrections to the brief that this writer follows:** the flow-rate function is
**`patchFlowRate`**
(measured: `flowRatePatch` is `FOAM FATAL IO ERROR: Cannot find configuration file`), and
`#includeFunc`
does **not** take `funcName=` (measured: nothing in `functionObjects/` mentions it), so a monitor's
`postProcessing/` folder is its call text and is read through the `job.read` relaxation T-202b
added.
"""

from __future__ import annotations

from typing import Any

from stokes.compile.errors import CompileInvalid, CompileUnsupported
from stokes.compile.foam import (
    Dict,
    Entry,
    FuncInclude,
    Raw,
    Word,
    render_file,
)
from stokes.compile.parallel import decompose_par_dict, decompose_step, reconstruct_fields_step
from stokes.compile.snapshot import SolveSnapshot, laplacian_scheme, sn_grad_scheme
from stokes.engines.turbulence import model as momentum_model
from stokes.engines.turbulence import names_for

from . import turbulence
from .boundaries import pressure_field, validate_patch, velocity_field
from .control import average_function, stop_function, transient_write

#: The dialect and its version. **`WRITER_VERSION` enters the run basis** (T-214): two runs whose
#: cases differ only by this writer's output must not be confused for the same run.
DIALECT = "of14"
WRITER_VERSION = "of14/3"

#: The engines this writer can express, as `stokes/engines` will name them.
ENGINES: tuple[str, ...] = ("of14.incompressibleFluid",)

#: The momentum models this writer expresses: every one Foundation 14 registers for
#: `incompressibleFluid` (`stokes.engines.turbulence`). `laminar` is first and is written exactly as
#: before; a turbulence model adds its `momentumTransport`, initial fields, schemes and solvers from
#: the shared `turbulence` module, the same files the fluid writer uses.
TURBULENCE: tuple[str, ...] = names_for("of14.incompressibleFluid")

_SOLVER = "incompressibleFluid"

#: The non-linear eddy-viscosity models: their extra stress term needs `div(nonlinearStress)`
#: (measured: "Cannot find scheme for div(nonlinearStress)" on FoamWin 14 without it).
_NONLINEAR = frozenset({"LienCubicKE", "ShihQuadraticKE"})

#: The functions this writer uses for its monitors. **`patchFlowRate`, not `flowRatePatch`** — the
#: name is the FILE name under `$FOAM_ETC/caseDicts/functions/`, and OpenFOAM resolves
#: `#includeFunc` against exactly those.
_FLOW_RATE_FUNC = "patchFlowRate"
_PATCH_AVERAGE_FUNC = "patchAverage"


def write(snap: SolveSnapshot) -> dict[str, str]:
    """The case as a `CaseTree`: seven files, every one printed by `stokes.compile.foam`.

    Raises `CompileUnsupported` for an engine, turbulence model or condition kind this writer cannot
    express, and `CompileInvalid` for a value that cannot be a case.
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
        "0/U": render_file("U", _field(snap, "U"), cls="volVectorField", location="0"),
        "0/p": render_file("p", _field(snap, "p"), cls="volScalarField", location="0"),
    }
    # The turbulence model's own initial fields (k, epsilon, omega, nut, ...); none for laminar.
    written.update(turbulence.field_files(snap))
    if snap.cores > 1:
        written["system/decomposeParDict"] = decompose_par_dict(snap.cores)
    return written


def steps(snap: SolveSnapshot) -> list[dict[str, Any]]:
    """The job steps that run this case, for the agent (T-203).

    **`-solver` is passed even though `controlDict` also names the solver.** Both are real — the
    tutorial
    sets `solver incompressibleFluid;` and `foamRun -help` lists `-solver` — and passing it
    makes the
    command line say what the job does, which is worth more in a log than saving four arguments.
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
    """A short string naming this case's writer and engine, for the run basis (T-214).

    The caller combines it with a hash of the tree; this is the part that says *how* the case
    was written,
    so a change to this module invalidates a cached run even when the snapshot is identical.
    """
    return f"{WRITER_VERSION}:{snap.engine}:{snap.turbulence}"


# ---------------------------------------------------------------------- validation


def _validate(snap: SolveSnapshot) -> None:
    """Everything a case cannot be, checked before a single file is printed."""
    if (
        isinstance(snap.cores, bool)
        or not isinstance(snap.cores, int)
        or not 1 <= snap.cores <= 256
    ):
        raise CompileInvalid(
            f"cores must be an integer from 1 to 256; got {snap.cores!r}",
            details={"code": "compile.invalid", "cores": snap.cores},
        )
    if snap.engine not in ENGINES:
        raise CompileUnsupported(
            f"engine {snap.engine!r} is not supported; known: {', '.join(ENGINES)}",
            details={"code": "compile.unsupported", "engine": snap.engine},
        )
    if snap.turbulence not in TURBULENCE:
        raise CompileUnsupported(
            f"turbulence {snap.turbulence!r} is not registered for of14.incompressibleFluid in "
            "Foundation 14; choose another model or the of14.fluid engine",
            details={"code": "compile.unsupported", "turbulence": snap.turbulence},
        )
    # The model's own requirements: a transient run for LES/DES/SAS, the initialisation values,
    # and a velocity inlet with an intensity and a scale to start the turbulence from.
    turbulence.validate(snap)
    if snap.transient and not (snap.delta_t > 0 and snap.end_time > 0):
        raise CompileInvalid(
            "a transient run needs a positive time step and end time",
            details={"code": "compile.invalid"},
        )
    if snap.iterations < 1:
        raise CompileInvalid(
            f"iterations must be at least 1; got {snap.iterations}",
            details={"code": "compile.invalid", "iterations": snap.iterations},
        )
    if snap.write_interval < 1:
        raise CompileInvalid(
            f"write_interval must be at least 1; got {snap.write_interval}",
            details={"code": "compile.invalid", "write_interval": snap.write_interval},
        )
    if snap.residual_target <= 0:
        raise CompileInvalid(
            f"residual_target must be positive; got {snap.residual_target}",
            details={"code": "compile.invalid", "residual_target": snap.residual_target},
        )
    if snap.nu <= 0:
        raise CompileInvalid(
            f"nu must be positive; got {snap.nu}",
            details={"code": "compile.invalid", "nu": snap.nu},
        )
    if snap.rho <= 0:
        raise CompileInvalid(
            f"rho must be positive; got {snap.rho}",
            details={"code": "compile.invalid", "rho": snap.rho},
        )
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
        # **A patch name is a dictionary key**, so it must be a word the printer will accept.
        # Checking it
        # here names the patch; letting the printer refuse would name a line number in a file
        # the user
        # never wrote.
        _word_or_refuse(condition.name, "patch name")
        _word_or_refuse(condition.patch_type, "patch type")
        validate_patch(condition, snap.rho)
    for name in snap.monitor_patches:
        if name not in seen:
            raise CompileInvalid(
                f"monitor patch {name!r} is not one of the case's patches",
                details={"code": "compile.invalid", "patch": name},
            )


def _word_or_refuse(text: str, what: str) -> None:
    """Refuse a name a dictionary key cannot be, naming the value rather than a line in a printed
    file."""
    from stokes.compile.foam import WORD_RE

    if not WORD_RE.match(text or ""):
        raise CompileInvalid(
            f"the {what} {text!r} is not an OpenFOAM word",
            details={"code": "compile.invalid", "value": text},
        )


# ---------------------------------------------------------------------- the files


def _control_dict(snap: SolveSnapshot) -> Dict:
    """`system/controlDict`, from `pitzDailySteady`'s keys.

    **`deltaT 1` with `writeControl timeStep`** is what makes `endTime` count iterations: a
    steady run has
    no physical time, so one outer iteration is one step.
    """
    items: list[Any] = [
        Entry("solver", Word(_SOLVER)),
        Entry("startFrom", Word("startTime")),
        Entry("startTime", 0),
        Entry("stopAt", Word("endTime")),
        # Steady: one outer iteration per unit time. Transient (LES, DES, SAS, or asked for):
        # physical seconds, as the fluid writer does.
        Entry("endTime", snap.end_time if snap.transient else snap.iterations),
        Entry("deltaT", snap.delta_t if snap.transient else 1),
        # timeStep/write_interval, or physical write times and the adaptive step (`control.py`).
        *transient_write(snap),
        # Two kept: the last two are useful and a long run must not fill the disk with every
        # iteration.
        Entry("purgeWrite", 2),
        Entry("writeFormat", Word("ascii")),
        Entry("writePrecision", 8),
        Entry("writeCompression", Word("off")),
        Entry("timeFormat", Word("general")),
        Entry("timePrecision", 6),
        # **False**, because nothing edits a written case: runTimeModifiable makes the solver
        # re-stat
        # every dictionary on every step for a change that never comes.
        Entry("runTimeModifiable", False),
        # **Stop writes rather than kills** (`control.py`): always present, monitors or not.
        Entry("functions", Dict((stop_function(), *_monitors(snap), *average_function(snap)))),
    ]
    return Dict(tuple(items))


def _monitors(snap: SolveSnapshot) -> tuple[Any, ...]:
    """One flow-rate and one mean-pressure function per monitor patch.

    **The `postProcessing` folder is the call text**, because `#includeFunc` has no `funcName=`
    (measured).
    That is why T-202b relaxes `job.read` for the segment after `postProcessing/` — without it every
    result written by a function taking arguments would be unreadable.
    """
    items: list[Any] = []
    for name in snap.monitor_patches:
        items.append(
            FuncInclude(f"{_FLOW_RATE_FUNC}(patch={name})", f"volumetric flow rate through {name}")
        )
        items.append(
            FuncInclude(
                f"{_PATCH_AVERAGE_FUNC}(patch={name}, fields=(p))",
                f"area-averaged p (p/rho) on {name}",
            )
        )
    return tuple(items)


def _fv_schemes(snap: SolveSnapshot) -> Dict:
    """`system/fvSchemes`, copied from the steady tutorial's structure.

    **The laminar stress term is not optional.** With `default none`, the laminar model needs
    `div((nuEff*dev2(T(grad(U))))) Gauss linear;`; without it `foamRun` stops with a FOAM FATAL that
    "keyword ... is undefined". A turbulence model's own entries (`div(phi,k)`, `wallDist`, ...)
    come from `turbulence.augment_schemes`; a Reynolds-stress model also reads `div(R)`. A
    transient run uses `Euler` and unbounded convection, as the transient `pitzDaily` does.
    """
    convection = "Gauss" if snap.transient else "bounded Gauss"
    div_schemes = [
        Entry("default", Word("none")),
        Entry("div(phi,U)", Raw(f"{convection} linearUpwind grad(U)")),
        Entry("div((nuEff*dev2(T(grad(U)))))", Raw("Gauss linear")),
    ]
    if "R" in momentum_model(snap.turbulence).fields:
        div_schemes.append(Entry("div(R)", Raw("Gauss linear")))
    if snap.turbulence in _NONLINEAR:
        div_schemes.append(Entry("div(nonlinearStress)", Raw("Gauss linear")))
    base = Dict(
        (
            Entry(
                "ddtSchemes",
                Dict((Entry("default", Word("Euler" if snap.transient else "steadyState")),)),
            ),
            Entry("gradSchemes", Dict((Entry("default", Raw("Gauss linear")),))),
            Entry("divSchemes", Dict(tuple(div_schemes))),
            Entry("laplacianSchemes", Dict((Entry("default", Raw(laplacian_scheme(snap))),))),
            Entry("interpolationSchemes", Dict((Entry("default", Word("linear")),))),
            Entry("snGradSchemes", Dict((Entry("default", Raw(sn_grad_scheme(snap))),))),
        )
    )
    return turbulence.augment_schemes(base, snap)


def _fv_solution(snap: SolveSnapshot) -> Dict:
    """`system/fvSolution`: the tutorial's solvers, with **our** `residualControl`.

    The residual target is the snapshot's, because that is the number a user set; the solver
    settings
    around it are the tutorial's, because they are what makes a steady incompressible case converge.
    A transient run takes the transient `pitzDaily`'s PIMPLE controls instead, and a turbulence
    model adds its transported fields' solvers (`turbulence.augment_solution`).
    """
    if snap.transient:
        return turbulence.augment_solution(_transient_solution(), snap)
    return turbulence.augment_solution(_steady_solution(snap), snap)


def _transient_solution() -> Dict:
    """PIMPLE for a physical-time run: the transient `pitzDaily`'s solvers and correctors."""

    def solver(kind: str, final: bool) -> Dict:
        if kind == "p":
            items = (
                Entry("solver", Word("GAMG")),
                Entry("smoother", Word("GaussSeidel")),
                Entry("tolerance", 1e-07),
                Entry("relTol", 0 if final else 0.01),
            )
        else:
            items = (
                Entry("solver", Word("smoothSolver")),
                Entry("smoother", Word("symGaussSeidel")),
                Entry("tolerance", 1e-05),
                Entry("relTol", 0 if final else 0.1),
            )
        return Dict(items)

    return Dict(
        (
            Entry(
                "solvers",
                Dict(
                    (
                        Entry("p", solver("p", final=False)),
                        Entry("pFinal", solver("p", final=True)),
                        Entry("U", solver("U", final=False)),
                        Entry("UFinal", solver("U", final=True)),
                    )
                ),
            ),
            Entry(
                "PIMPLE",
                Dict((Entry("nNonOrthogonalCorrectors", 0), Entry("nCorrectors", 2))),
            ),
        )
    )


def _steady_solution(snap: SolveSnapshot) -> Dict:
    """The steady SIMPLE controls this writer has always written."""
    return Dict(
        (
            Entry(
                "solvers",
                Dict(
                    (
                        Entry(
                            "p",
                            Dict(
                                (
                                    Entry("solver", Word("GAMG")),
                                    Entry("tolerance", 1e-06),
                                    Entry("relTol", 0.1),
                                    Entry("smoother", Word("GaussSeidel")),
                                )
                            ),
                        ),
                        Entry(
                            "U",
                            Dict(
                                (
                                    Entry("solver", Word("smoothSolver")),
                                    Entry("smoother", Word("symGaussSeidel")),
                                    Entry("tolerance", 1e-05),
                                    Entry("relTol", 0.1),
                                )
                            ),
                        ),
                    )
                ),
            ),
            Entry(
                "SIMPLE",
                Dict(
                    (
                        Entry("nNonOrthogonalCorrectors", 2 if snap.non_orthogonal else 0),
                        Entry("consistent", Word("yes")),
                        Entry(
                            "residualControl",
                            Dict(
                                (
                                    Entry("p", snap.residual_target),
                                    Entry("U", snap.residual_target),
                                )
                            ),
                        ),
                    )
                ),
            ),
            Entry(
                "relaxationFactors",
                Dict(
                    (
                        Entry(
                            "equations",
                            Dict((Entry("U", 0.9), Entry('".*"', 0.9))),
                        ),
                    )
                ),
            ),
        )
    )


def _physical_properties(snap: SolveSnapshot) -> Dict:
    """`constant/physicalProperties`: the tutorial's `viscosityModel constant;` and a bare `nu`.

    The tutorial writes `nu 1e-05;` with **no dimension set**, and the brief says to copy the
    form — so
    this does. `Dict` is not used for `nu`'s value because a plain float is exactly what the
    tutorial has.
    """
    return Dict((Entry("viscosityModel", Word("constant")), Entry("nu", snap.nu)))


def _field(snap: SolveSnapshot, name: str) -> Dict:
    """`0/U` or `0/p`: dimensions, an internal field and one boundary entry per patch.

    **Every patch in the mesh gets an entry.** A patch with none is a FOAM FATAL when the solver
    starts,
    which is why the snapshot carries the `unassigned` kind and why the caller must list every
    patch the
    mesher made.
    """
    if name == "U":
        # **Named dimensions, and square brackets.** Measured: the tutorial writes
        # `dimensions [velocity];` / `[kinematicPressure];` in OpenFOAM 14, and the solver
        # REFUSES the
        # parenthesised seven-number form with `expected a [ in dimensionSet`. `Raw` because the
        # printer
        # has no dimensioned-set node and this is one line verbatim.
        dims = Raw("[0 1 -1 0 0 0 0]")
        internal = Raw("uniform (0 0 0)")
    else:
        # p in an incompressible solver is p/rho, so its dimensions are m2/s2.
        dims = Raw("[0 2 -2 0 0 0 0]")
        internal = Raw("uniform 0")
    entries: list[Any] = []
    for condition in snap.patches:
        field = (
            velocity_field(condition, snap.rho)
            if name == "U"
            else pressure_field(condition, snap.rho)
        )
        entries.append(Entry(condition.name, field))
    return Dict(
        (
            Entry("dimensions", dims),
            Entry("internalField", internal),
            Entry("boundaryField", Dict(tuple(entries))),
        )
    )
