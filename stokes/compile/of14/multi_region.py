"""The multi-region conjugate case for OpenFOAM 14's `foamMultiRun` (ADR-0042).

Each fluid region's files are the ones `stokes.compile.of14.fluid` writes for that region's own
snapshot, moved under the region's folders (`0/<region>/`, `constant/<region>/`,
`system/<region>/`). A solid region gets its temperature, its thermophysical model
(`heSolidThermo`, ADR-0018) and the two solution dictionaries of the energy equation. One
`controlDict` names the solver module of every region, and the case-level `fvSolution` holds the
outer loop the regions share.

Measured on FoamWin 14 (2026-11-20, a gyroid counter-flow core, water and aluminium, 128 k cells):
steady, one outer corrector, `coupledTemperature` on the `mappedWall` interfaces; the heat the hot
stream lost and the cold stream gained agreed to 1 mW in 105 W after 500 iterations.

A pure function of the snapshot, like every writer here.
"""

from __future__ import annotations

from typing import Any

from stokes.compile.errors import CompileInvalid, CompileUnsupported
from stokes.compile.foam import Dict, Entry, FuncInclude, Raw, Word, render_file
from stokes.compile.of14 import fluid as fluid_writer
from stokes.compile.of14.control import stop_function
from stokes.compile.snapshot import MULTI_REGION_ENGINE, RegionCase, SolveSnapshot

#: Bumped whenever the files this module adds change, so a finished run goes stale.
WRITER_VERSION = "of14-multi-region/1"

ENGINE = MULTI_REGION_ENGINE

#: The files of a fluid region's own case that belong to the whole case instead.
_CASE_LEVEL = ("system/controlDict", "system/decomposeParDict")

#: The step that writes the whole-mesh fields from the regions' (`stokes_agent.regions`).
MERGE_STEP = "mergeRegions"


def write(snap: SolveSnapshot) -> dict[str, str]:
    """Every file of the case, by path."""
    _validate(snap)
    files: dict[str, str] = {
        "system/controlDict": render_file("controlDict", _control_dict(snap), location="system"),
        "system/fvSchemes": render_file("fvSchemes", _solid_schemes(), location="system"),
        "system/fvSolution": render_file(
            "fvSolution",
            Dict((Entry("PIMPLE", Dict((Entry("nOuterCorrectors", 1),))),)),
            location="system",
        ),
    }
    for region in snap.regions:
        if region.fluid is not None:
            for path, text in fluid_writer.write(region.fluid).items():
                if path in _CASE_LEVEL:
                    continue
                folder, rest = path.split("/", 1)
                files[f"{folder}/{region.name}/{rest}"] = text
            continue
        files.update(_solid_files(region))
    return files


def steps(snap: SolveSnapshot, region_order: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    """`foamMultiRun`, then the merge of the regions' fields onto the whole mesh.

    ``region_order`` is the regions by the index the split gave them (its own log); without it
    the merge step is left out and only the regions' own fields exist.
    """
    _validate(snap)
    run: list[dict[str, Any]] = [
        {
            "name": "solve",
            "argv": ["foamMultiRun"],
            "timeout_s": 7200,
            "parse": "foamRun",
            "parse_params": {"end_time": snap.iterations},
        }
    ]
    if region_order:
        run.append(
            {
                "name": MERGE_STEP,
                "argv": [],
                "timeout_s": 900,
                "action": {"op": "merge_regions", "regions": list(region_order)},
            }
        )
    return run


def basis(snap: SolveSnapshot) -> str:
    """This writer's part of the run basis."""
    return f"{WRITER_VERSION}:{snap.engine}:{snap.turbulence}"


def _validate(snap: SolveSnapshot) -> None:
    if snap.engine != ENGINE:
        raise CompileUnsupported(
            f"engine {snap.engine!r} is not written by the multi-region writer",
            details={"code": "compile.unsupported", "engine": snap.engine},
        )
    if len(snap.regions) < 2:
        raise CompileInvalid(
            "a multi-region case needs at least two regions", details={"code": "compile.invalid"}
        )
    if snap.transient:
        raise CompileUnsupported(
            "a multi-region case is written steady for now",
            details={"code": "compile.unsupported", "transient": True},
        )
    seen: set[str] = set()
    for region in snap.regions:
        fluid_writer._word_or_refuse(region.name, "region name")
        if region.name in seen:
            raise CompileInvalid(
                f"two regions are called {region.name!r}",
                details={"code": "compile.invalid", "region": region.name},
            )
        seen.add(region.name)
        if region.fluid is None and not (region.rho > 0 and region.cp > 0 and region.kappa > 0):
            raise CompileInvalid(
                f"the solid region {region.name!r} needs a positive density, specific heat and "
                "conductivity",
                details={"code": "compile.invalid", "region": region.name},
            )
        for name in region.patches:
            fluid_writer._word_or_refuse(name, "patch name")
    for region in snap.regions:
        for other in region.neighbours:
            if other not in seen:
                raise CompileInvalid(
                    f"the region {region.name!r} names a neighbour {other!r} the case lacks",
                    details={"code": "compile.invalid", "region": region.name},
                )


def _control_dict(snap: SolveSnapshot) -> Dict:
    """One `controlDict`: the solver module of every region, steady iterations, the monitors."""
    solvers = Dict(
        tuple(
            Entry(region.name, Word("fluid" if region.fluid is not None else "solid"))
            for region in snap.regions
        )
    )
    monitors: list[Any] = []
    for region in snap.regions:
        if region.fluid is None:
            continue
        for patch in region.fluid.monitor_patches:
            monitors.append(
                FuncInclude(
                    f"patchFlowRate(patch={patch}, region={region.name})",
                    f"mass flow rate through {patch}",
                )
            )
            monitors.append(
                FuncInclude(
                    f"patchAverage(patch={patch}, fields=(p T), region={region.name})",
                    f"area-averaged p and T on {patch}",
                )
            )
    return Dict(
        (
            Entry("regionSolvers", solvers),
            Entry("startFrom", Word("startTime")),
            Entry("startTime", 0),
            Entry("stopAt", Word("endTime")),
            Entry("endTime", snap.iterations),
            Entry("deltaT", 1),
            Entry("writeControl", Word("timeStep")),
            Entry("writeInterval", snap.write_interval),
            Entry("purgeWrite", 2),
            Entry("writeFormat", Word("ascii")),
            Entry("writePrecision", 8),
            Entry("writeCompression", Word("off")),
            Entry("timeFormat", Word("general")),
            Entry("timePrecision", 6),
            Entry("runTimeModifiable", False),
            Entry("functions", Dict((stop_function(), *monitors))),
        )
    )


def _solid_schemes() -> Dict:
    """Conduction alone: a Laplacian and nothing convected."""
    return Dict(
        tuple(
            Entry(name, Dict((Entry("default", Raw(value)),)))
            for name, value in (
                ("ddtSchemes", "steadyState"),
                ("gradSchemes", "Gauss linear"),
                ("divSchemes", "none"),
                ("laplacianSchemes", "Gauss linear corrected"),
                ("interpolationSchemes", "linear"),
                ("snGradSchemes", "corrected"),
            )
        )
    )


def _solid_files(region: RegionCase) -> dict[str, str]:
    """A solid region: its temperature, its material and the energy equation's dictionaries."""
    name = region.name
    solution = Dict(
        (
            Entry(
                "solvers",
                Dict(
                    (
                        Entry(
                            '"e.*"',
                            Dict(
                                (
                                    Entry("solver", Word("GAMG")),
                                    Entry("smoother", Word("symGaussSeidel")),
                                    Entry("tolerance", 1e-08),
                                    Entry("relTol", 0.1),
                                )
                            ),
                        ),
                    )
                ),
            ),
            Entry("PIMPLE", Dict((Entry("nNonOrthogonalCorrectors", 1),))),
        )
    )
    properties = Dict(
        (
            Entry(
                "thermoType",
                Dict(
                    (
                        Entry("type", Word("heSolidThermo")),
                        Entry("mixture", Word("pureMixture")),
                        Entry("transport", Word("constIsoSolid")),
                        Entry("thermo", Word("eConst")),
                        Entry("equationOfState", Word("rhoConst")),
                        Entry("specie", Word("specie")),
                        Entry("energy", Word("sensibleInternalEnergy")),
                    )
                ),
            ),
            Entry(
                "mixture",
                Dict(
                    (
                        # A solid has no molar mass the equations use; the model needs the entry.
                        Entry("specie", Dict((Entry("molWeight", 1),))),
                        Entry("equationOfState", Dict((Entry("rho", region.rho),))),
                        Entry("transport", Dict((Entry("kappa", region.kappa),))),
                        Entry(
                            "thermodynamics",
                            Dict((Entry("hf", 0), Entry("Cv", region.cp))),
                        ),
                    )
                ),
            ),
        )
    )
    start = Raw(f"uniform {float(region.T_init)}")
    boundary: list[Entry] = [
        Entry(patch, Dict((Entry("type", Word("zeroGradient")),))) for patch in region.patches
    ]
    boundary += [
        Entry(
            f"{name}_to_{other}",
            Dict((Entry("type", Word("coupledTemperature")), Entry("value", start))),
        )
        for other in region.neighbours
    ]
    temperature = Dict(
        (
            Entry("dimensions", Raw("[0 0 0 1 0 0 0]")),
            Entry("internalField", start),
            Entry("boundaryField", Dict(tuple(boundary))),
        )
    )
    return {
        f"system/{name}/fvSchemes": render_file("fvSchemes", _solid_schemes(), location="system"),
        f"system/{name}/fvSolution": render_file("fvSolution", solution, location="system"),
        f"constant/{name}/physicalProperties": render_file(
            "physicalProperties", properties, location="constant"
        ),
        f"0/{name}/T": render_file("T", temperature, cls="volScalarField", location="0"),
    }
