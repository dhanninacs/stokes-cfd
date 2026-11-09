"""The flag allow-list: what may appear after a utility's name (ADR-0024 §1.2, T-202b).

## Why flags need their own list

`ALLOWED_UTILITIES` says *which program* may run. This says **which arguments** it may be given,
and the
distinction matters because an OpenFOAM utility's flags are not switches: several of
them redirect the
run. `-case` points the utility at another directory (so a job could read or write outside its
folder),
`-roots`/`-hostRoots` do the same for the file handler, `-fileHandler` changes how every path in
the case
is interpreted, `-libs` loads a shared library into the process, and `-parallel` starts MPI.
**Each is a
door out of the sandbox**, so they are refused for every utility.

The list is **built from the install's own `-help` output**, captured under
`tests/fixtures/of14/help/`,
not from memory. That is why it is short: the skeleton drives these utilities through a case
directory, so
it needs very few arguments, and a flag that is not needed is a flag that should not be allowed.

## The rule for an argv item after the program name

Every item must be either a listed flag, or the value of a one-argument flag immediately before
it. An unrecognised item is `agent.bad_params` rather than being passed through — so the
boundary is a **closed** list, and a new flag has to be added here deliberately with its reason.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Flags refused for EVERY utility. Each one is a way out of the job folder or into the process.
#:
#: * `-case` — run against a directory the job does not own.
#: * `-roots`, `-hostRoots` — the same for the file handler, and less obviously.
#: * `-fileHandler` — changes how every path in the case is read, which can move the case root.
#: * `-libs` — loads a shared library into the process. Arbitrary code execution by another name.
#: * `-parallel` — starts MPI, which spawns processes the supervisor's group kill must then chase.
FORBIDDEN_FLAGS = frozenset(
    {
        "-case",
        "-libs",
        "-fileHandler",
        "-roots",
        "-hostRoots",
        "-parallel",
    }
)


#: The one positional argument a utility may be given, as a full-match pattern.
#:
#: **Only `polyDualMesh`, and only a number of degrees.** Its usage is
#: `polyDualMesh [OPTIONS] <featureAngle [0-180]>` (the install's `-help`), so the angle cannot be
#: a flag. The pattern admits 0 to 180 with up to three decimals and nothing else - no sign, no
#: exponent, no path - so the positional slot cannot carry anything a flag could not.
POSITIONAL: dict[str, str] = {
    "polyDualMesh": r"(180(\.0{1,3})?|1[0-7][0-9](\.[0-9]{1,3})?|[1-9]?[0-9](\.[0-9]{1,3})?)",
    # **`foamDictionary` edits one file, the boundary list** (the polyhedral mesher's symmetry
    # planes, `stokes.mesh.drivers.cfmesh`): no other path can be named.
    "foamDictionary": r"constant/polyMesh/boundary",
}


@dataclass(frozen=True)
class FlagSpec:
    """One permitted flag.

    ``arity`` is 0 for a switch and 1 for a flag that takes a value. ``pattern`` restricts that
    value: a flag whose value names a program, a solver or a path is only safe if the value is
    checked too — an allow-listed flag with an unchecked value is a hole with a polite name.
    """

    arity: int
    pattern: str | None = None
    why: str = ""


#: What each utility may be given, after its name.
#:
#: **Short on purpose.** The skeleton passes a utility nothing, or one `-solver`, because the case
#: directory is the interface: `cwd` selects the case and the dictionaries say the rest. Every
#: entry here
#: is a thing the e2e plan actually runs — checked against
#: `tests/fixtures/of14/help/<utility>.txt`, which
#: is the install's own output.
ALLOWED_FLAGS: dict[str, dict[str, FlagSpec]] = {
    "decomposePar": {
        "-noFields": FlagSpec(0, why="decompose background mesh before snappy"),
    },
    "reconstructPar": {
        "-constant": FlagSpec(0, why="reconstruct snappy's constant mesh"),
        "-noFields": FlagSpec(0, why="reconstruct geometry without solution fields"),
    },
    # `blockMesh` needs no arguments: it reads `system/blockMeshDict` from the working directory.
    "blockMesh": {},
    # `checkMesh` likewise, and T-200's inventory shows the skeleton uses none of its quality
    # switches —
    # it parses the log instead (T-208), which is why the thresholds are NOT passed as flags.
    "checkMesh": {},
    # snappy needs `-overwrite` for a re-run that must not stop to ask, which is the one flag
    # the mesh
    # service passes (T-150).
    "snappyHexMesh": {
        "-overwrite": FlagSpec(0, why="replace the mesh instead of refusing to overwrite it"),
    },
    # `surfaceFeatures` reads `system/surfaceFeaturesDict`; no flags.
    "surfaceFeatures": {},
    # The 2D snappy recipe (ADR-0031): reads `system/extrudeMeshDict` and overwrites
    # `constant/polyMesh` by default in OF14, so it needs no flag.
    "extrudeMesh": {},
    # **The cfMesh family reads `system/meshDict` and nothing else** (ADR-0032): measured with
    # `cartesianMesh`, `tetMesh` and `pMesh` on the FoamWin build, none needs an argument.
    "cartesianMesh": {},
    "tetMesh": {},
    "pMesh": {},
    # `polyDualMesh <featureAngle>`: its one argument is positional (`POSITIONAL` below), and
    # `-overwrite` is "Deprecated option, this is now default behaviour" in the install's `-help`.
    "polyDualMesh": {},
    # cfMesh's `improveSymmetryPlanes` reads `constant/polyMesh` and flattens the `symmetryPlane`
    # patches in place; it needs no argument.
    "improveSymmetryPlanes": {},
    # **`foamDictionary` may only set a boundary patch's type to `symmetry`**: the file is pinned by
    # `POSITIONAL`, the entry to `entry0/<patch>/type` and the value to the one word. That is the
    # whole of what the polyhedral mesher needs after flattening (`cfmesh.RESTORE_STEP`); any other
    # edit of any other dictionary is refused here.
    "foamDictionary": {
        "-entry": FlagSpec(
            1,
            pattern=r"^entry0/[A-Za-z][A-Za-z0-9_]{0,63}/type$",
            why="the type of one patch in constant/polyMesh/boundary",
        ),
        "-set": FlagSpec(1, pattern=r"^symmetry$", why="set back to symmetry after flattening"),
    },
    # **The one flag with a value**, and its value is pinned: `foamRun` cannot run without a
    # solver name, and any solver other than the ones Stokes writes a case for would need a
    # different case. `fluid` is the energy-capable solver of `of14.fluid` (ADR-0027 step 4).
    "foamRun": {
        "-solver": FlagSpec(
            1,
            pattern=r"^(incompressibleFluid|fluid)$",
            why="the solvers Stokes writes a case for (incompressibleFluid, fluid)",
        ),
    },
}
