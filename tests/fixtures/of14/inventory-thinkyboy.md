# T-200 inventory: verified OF14 facts, each with its evidence

**Working note while T-200 runs.** Every `[VERIFY-OF14]` item from the brief is answered here with the
command or file that proves it. This becomes `tests/fixtures/of14/inventory-thinkyboy.md` once the
fixtures are copied; it is here first so the evidence is captured as it is found rather than
reconstructed later.

Machine: **thinkyboy**, install `$FOAM_INST_DIR`, env `$STOKES_OPENFOAM_BASHRC`.

---

## (1) Is the env script required, or is `etc/bashrc` enough?

**The env script is required.** Answer, with both commands run:

```
# sourcing ONLY $FOAM_INST_DIR/etc/bashrc
blockMesh on PATH: yes
blockMesh -help exit: 127
blockMesh: error while loading shared libraries: libmpi.so.40: cannot open shared object file

# sourcing $STOKES_OPENFOAM_BASHRC
blockMesh -help exit: 0
foamRun  -help exit: 0
```

**Why:** `etc/bashrc` puts `$FOAM_APPBIN` on `PATH` but does not export the MPI library path. OpenFOAM's
binaries link `libmpi.so.40`, which lives under `$OFDEP/usr/lib/x86_64-linux-gnu/…`,
and only `openfoam14-env.sh` adds `OPAL_PREFIX`/`PMIX_PREFIX`/`PRTE_PREFIX` and the `LD_LIBRARY_PATH`
that reaches it. So "the env script is the configured default" is not a convenience: without it the
utilities **on this install** cannot start at all. Recorded because the brief asks both answers, and the
answer is not the same for a system OpenFOAM.

## (b) Does `#includeFunc` accept `funcName=`? Which functions, and what do they write?

**No.** `grep -rl "funcName" $FOAM_SRC/` returns exactly three files, none of them the function-object
include machinery:

    src/lagrangian/basic/IOPosition/IOPosition.C
    src/finiteVolume/cfdTools/general/fvModels/includeFvModelEntry/includeFvModelEntry.C
    src/finiteVolume/cfdTools/general/fvConstraints/includeFvConstraintEntry/includeFvConstraintEntry.C

So the agent's dictionary lint is right to refuse `#includeFunc` with a `funcName=` argument.

**The names are `patchFlowRate` and `patchAverage`** (not `flowRatePatch`), both under
`$FOAM_ETC/caseDicts/functions/surfaceFieldValue/`. Their bodies, verbatim minus the licence header:

    # patchFlowRate
    Description
        Calculates the flow rate through a specified patch by summing the flux on
        patch faces.  For solvers where the flux is volumetric, the flow rate is
        volumetric; where flux is mass flux, the flow rate is mass flow rate.
    patch   <patchName>;
    fields  (phi);
    operation sum;
    #includeEtc "caseDicts/functions/surfaceFieldValue/patchValue.cfg"

    # patchAverage
    Description
        Calculates the average values of fields on a patch.
    patch   <patchName>;
    fields  (<fieldNames>);
    operation areaAverage;
    #includeEtc "caseDicts/functions/surfaceFieldValue/patchValue.cfg"

**Arguments, therefore:** `patchFlowRate` takes `patch` + `fields (phi)` (the flux field, not a velocity);
`patchAverage` takes `patch` + `fields (<names>)`. Both pull the rest from
`surfaceFieldValue/patchValue.cfg`.

## (c) The `Time = …` line format (Foundation 11+ prints a unit suffix)

**Confirmed `Time = 1s` style** — to be pasted verbatim from the run log once pitzDaily has been run (see
the fixtures). Recorded here as **to be confirmed from `logs/foamRun.pitzDaily.log`**, not from memory:
the brief's rule is that the exact line comes from the log.

## (d) pitzDaily's `controlDict` has NO `functions` block

Verified by reading
`$FOAM_TUTORIALS/incompressibleFluid/pitzDaily/system/controlDict`:

    solver          incompressibleFluid;
    startFrom       latestTime;
    startTime       0;
    stopAt          endTime;
    endTime         0.3;
    deltaT          0.0001;
    writeControl    adjustableRunTime;
    writeInterval   0.01;
    purgeWrite      0;
    writeFormat     ascii;
    writePrecision  6;
    writeCompression off;
    timeFormat      general;
    timePrecision   6;
    runTimeModifiable yes;
    adjustTimeStep  yes;

**Two facts the brief's correction (d) assumes are only half right, and worth flagging:**

1. **`funcName` never appears in this file** — the correction says "the agent lint will refuse it", but
   this `controlDict` has **no `functions` block at all**, so there is nothing to strip from it. The lint
   concern applies to a controlDict *we* write, not to this one. Recorded because a test that strips a
   block that is not there would pass vacuously.
2. **`solver incompressibleFluid;`, not `application`.** OpenFOAM 14 selects the solver with the
   **`solver`** keyword here (`application` is the older spelling). That is a [VERIFY-OF14] item the brief
   asks for, answered.

## The pitzDaily path, and which of the four to use

`find $FOAM_TUTORIALS -maxdepth 3 -name pitzDaily` returns **four**:

    tutorials/resources/blockMesh/pitzDaily     <- mesh resources only, not a case
    tutorials/incompressibleFluid/pitzDaily     <- THE ONE (steady, solver incompressibleFluid)
    tutorials/potentialFoam/pitzDaily           <- potential flow, different solver
    tutorials/fluid/pitzDaily                   <- the legacy `fluid` solver

**Use `incompressibleFluid`**, because the brief says to take the steady `incompressibleFluid` one and the
`controlDict` above proves it: `solver incompressibleFluid;`, `stopAt endTime`, `endTime 0.3`, and
`adjustTimeStep yes` — a steady incompressible run. `potentialFoam` is a different (potential) solver and
`fluid` is the pre-14 multi-physics solver; neither is what the skeleton simulates.

The cavity for the blockMesh fixture:

    tutorials/incompressibleFluid/cavity       <- the one (also in tutorials/fluid/cavity)
    tutorials/legacy/incompressible/icoFoam/cavity

---

## MEASURED: pitzDaily, blockMesh → checkMesh → foamRun, on thinkyboy

Run in a copy at `/tmp/stokes-inv/pitzDaily` (the tutorial is read-only source). Wall times from
`/usr/bin/time`, exit codes from the shell.

| step | command | exit | wall | key output |
|---|---|---|---|---|
| blockMesh | `blockMesh -dict $FOAM_TUTORIALS/resources/blockMesh/pitzDaily` | **0** | **0.33 s** | `nCells: 12225` |
| checkMesh | `checkMesh` | **0** | **0.43 s** | `cells: 12225`, `Max skewness = 0.260575 OK.`, `Mesh OK.` |
| foamRun | `foamRun` | **0** | **54.21 s** | converged, `End` |

### A fact the brief does not anticipate: the pitzDaily case has NO blockMeshDict

`blockMesh` run from the case directory **fails**, exit 1:

    --> FOAM FATAL ERROR:
    Cannot find file "system/blockMeshDict"

The case's own `Allrun` says why:

    runApplication blockMesh -dict $FOAM_TUTORIALS/resources/blockMesh/pitzDaily
    runApplication foamRun

**So the mesh comes from `tutorials/resources/blockMesh/pitzDaily`**, and any tool that runs `blockMesh`
in a copied pitzDaily must pass `-dict` with that path (or copy the dict in). Recorded because T-213's
"pitzDaily as a job" and T-212's writer both have to do one of those, and a bare `blockMesh` is the
obvious first attempt.

### Correction (d) is right in substance and wrong in location: the function object is in `system/functions`

The brief says "pitzDaily's `controlDict` `functions` block verbatim (the agent lint will refuse it;
tests strip it)". Measured:

- `system/controlDict` has **no `functions` block** (full keys quoted earlier).
- The function object is in a **separate file, `system/functions`**, and its entire body is:

      #includeFunc patchAverage(patch=inlet, fields=(p U))

**So the lint concern applies to `system/functions`, not `controlDict`.** A test that strips a `functions`
block out of `controlDict` would find nothing and pass vacuously — the same trap as the `funcName=`
correction, and worth knowing before T-213 writes its fixtures.

### The log formats the parser needs, verbatim from `logs/foamRun.pitzDaily.log`

    Time = 0.000119048s
    smoothSolver:  Solving for Ux, Initial residual = 1, Final residual = 2.88483e-06, No Iterations 6
    GAMG:  Solving for p, Initial residual = 1, Final residual = 0.00840998, No Iterations 13
    time step continuity errors : sum local = 2.43308e-10, global = 5.46407e-11
    ExecutionTime = 0.346341 s  ClockTime = 1 s
    End

**Correction (c) confirmed:** the time line carries a **unit suffix** — `Time = 0.000119048s`, not
`Time = 0.000119048`. A parser splitting on whitespace and taking the last token gets `0.000119048s` and
must strip the trailing `s`.

And from `logs/checkMesh.pitzDaily.log`:

    cells:            12225
        Max skewness = 0.260575 OK.
    Mesh OK.

### THE `postProcessing` PATH TRAP, confirmed from a real run

The brief's trap list says "the default postProcessing path fails the `job.read` pattern". Measured, the
path the function object writes is:

    postProcessing/patchAverage(patch=inlet,fields=(pU))/0/surfaceFieldValue.dat

**The directory name contains `(`, `)`, `=`, and `,`**, and the field name in it is
`patchAverage(patch=inlet,fields=(pU))` — with the spaces of
`fields=(p U)` **removed**. So:

- a pattern expecting `postProcessing/<name>/<time>/` must handle a name that is not an identifier;
- a pattern built from the *source text* `patchAverage(patch=inlet, fields=(p U))` will **not** match,
  because OpenFOAM strips the spaces;
- and the file is `surfaceFieldValue.dat`, a **tab-separated** file with a `#` comment header, not CSV:

      # Selection   : patch(inlet)
      # Faces       : 30
      # Area        : 2.540000e-05
      # Time        	areaAverage(p)	areaAverage(U)
      0             	0.000000e+00	(1.000000e+00 0.000000e+00 0.000000e+00)

Note also that **`areaAverage(p)` on iteration 1 is `1.54e+04`** — a wild transient value that settles —
so a reader that takes the FIRST sample as the answer is wrong. Worth knowing for T-215/T-216.

### The residual stream is available as a function object

`$FOAM_ETC/caseDicts/functions/numerical/residuals`:

    Description
        For specified fields, writes out the initial residuals for the first
        solution of each time step; for non-scalar fields (e.g. vectors), writes
        the largest of the residuals for each component (e.g. x, y, z).
    #includeEtc "caseDicts/functions/numerical/residuals.cfg"
    fields  (<fieldNames>);

and `residuals.cfg`:

    type            residuals;
    libs            ("libutilityFunctionObjects.so");
    writeControl    timeStep;
    writeInterval   1;

**`type residuals;` with `writeControl timeStep;`** — so the T-213 monitor does not need to parse the
solver log for a residual stream; it can have OpenFOAM write one. Both routes are available and the
choice belongs to T-213.

### `fvSolution` has NO `residualControl` — the run stops on TIME, not on convergence

The brief asks "where `residualControl` sits in `fvSolution`". Measured: **it is not there at all.** The
whole of pitzDaily's `fvSolution` is `solvers` (p, pFinal, "(U|k|epsilon)", "(U|k|epsilon)Final") and

    PIMPLE
    {
        nNonOrthogonalCorrectors 0;
        nCorrectors         2;
    }

So pitzDaily converges because `endTime 0.3` is reached, **not** because a residual target was met. A
report that says "converged" must therefore say what it means: either `residualControl` is added by the
writer we produce, or "converged" means "reached the end time". Recorded because the brief's expected
duct check and the agent's report both use the word.

### `momentumTransport`: the tutorial is TURBULENT, so laminar is an edit we make

    model           kEpsilon;
    turbulence      on;

The brief says the tutorial is turbulent and `div((nuEff*dev2(T(grad(U)))))` is needed for the laminar
model. Both confirmed: `divSchemes` in `system/fvSchemes` contains, verbatim,

    div(phi,U)          Gauss linearUpwind grad(U);
    div(phi,k)          Gauss upwind;
    div(phi,epsilon)    Gauss upwind;
    div(phi,R)          Gauss upwind;
    div(R)              Gauss linear;
    div(phi,nuTilda)    Gauss upwind;
    div((nuEff*dev2(T(grad(U))))) Gauss linear;

and `constant/physicalProperties`:

    viscosityModel  constant;
    nu      1e-05;

---

## TWO TRAPS THE EXIT CODE DOES NOT TELL YOU ABOUT

Both measured, both matter to T-202b's supervisor and T-213's parser. **Neither is in the brief's trap
list.**

### 1. `checkMesh` exits **0** while reporting `Failed 2 mesh checks.`

The skewed cavity block (vertex 6 moved to `(2 2 0.1)`, as the brief asks):

    blockMesh exit: 0
    checkMesh exit: 0            <-- SUCCESS
    Mesh non-orthogonality Max: 82.5441 average: 56.7497
       *Number of severely non-orthogonal (> 70 degrees) faces: 242.
     ***Max skewness = 21.4007, 244 highly skew faces detected which may impair the quality
    Failed 2 mesh checks.       <-- FAILURE

**`checkMesh` reports failure in its TEXT and exits 0.** So a supervisor that judges a mesh by its exit
code accepts a mesh OpenFOAM has just failed. The quality gate has to parse the log — for `Failed n mesh
checks`, `Mesh OK`, `Max non-orthogonality`, `Max skewness` — which is what T-208 (T-146) is for, and this
fixture is its case.

### 2. A run can exit **0** having simulated nothing

pitzDaily sets `startFrom latestTime;`. A case copied with time directories already present restarts from
the latest one; with `0/U` deleted in that state, `foamRun`:

    exit: 0
    Starting time loop
    End
    (no results written)

The missing field was never read, because the run started after it. The **clean** case — no time
directories — exits **1** with

    --> FOAM FATAL ERROR:
    cannot find file "/tmp/stokes-inv/fatal2/0/U"

**So the first attempt at this fixture was contaminated and produced exit 0**, and I only caught it by
re-running on a pristine copy. That is recorded rather than hidden because it is the more dangerous of the
two: a "did it run?" check that trusts the exit code passes a run that did nothing, and the resumed-from-
latestTime behaviour is exactly what a job re-run in the owner's project will hit. `logs/foamRun.fatal.log`
holds the **clean** case (exit 1); the contaminated one is described here and deliberately not shipped,
because a log whose behaviour cannot be reproduced from its own contents is worse than no log.

---

## The IP gate caught the fixtures, and was right to

`tools/check_ip.py` refused the first cut of these fixtures, **exit 1**, five hits:

    tests/fixtures/of14/logs/blockMesh.pitzDaily.log:9: contains a Linux home folder
    tests/fixtures/of14/logs/blockMesh.pitzDaily.log:24: …
    tests/fixtures/of14/logs/blockMesh.pitzDaily.log:27: …
    tests/fixtures/of14/README.md:44: …
    tests/fixtures/of14/README.md:77: …

The hits were `$FOAM_INST_DIR/tutorials/...` — the machine's install path, in a captured
`blockMesh` log and in my own README quoting the reproduction command. **Exactly what the gate exists to
keep out of the repository**, and it is why `tools/check_ip.py` scans `tests/` while exempting `docs/`.

**Fixed by normalising, not by exempting.** The path is `$FOAM_TUTORIALS/resources/blockMesh/pitzDaily`,
so the log now says that — which is both clean and **better test data**, because a fixture that hardcodes
one machine's install is a fixture that only works on that machine. The README's reproduction command uses
`$STOKES_OPENFOAM_BASHRC`, the variable the agent's own config names. Gate now `clean`, exit 0.

**The rule worth keeping:** when a gate refuses something, the first question is whether the gate is wrong
or the content is. Here the content was, and the fix improved it. (I changed nothing in the gate.)

---

## The dict-keyword facts (the brief's inventory list), each measured

### The length keyword is `units [mm];`, NOT `convertToMeters`

`grep -nE "convertToMeters|scale" $FOAM_TUTORIALS/resources/blockMesh/pitzDaily` returns **nothing**. What
the file has is:

    units [mm];
    vertices
    (
        (-20.6 0 -0.5)
        ...

**This is an OpenFOAM 14 change and it matters for T-212/T-206.** `convertToMeters 0.001;` is the
familiar pre-14 spelling and it is not what this install reads; `units [mm];` is a **dimensioned unit**,
which fits ADR-0012's quantities rather than a bare scale factor. A writer emitting `convertToMeters` would
produce a dict OpenFOAM 14 either rejects or silently ignores — and "silently ignores" is the dangerous
half, because the mesh would come out 1000× the intended size.

### `FoamFile` carries NO `version 2.0;` line in OF14

Verbatim from `pitzDaily/system/fvSchemes`:

    FoamFile
    {
        format      ascii;
        class       dictionary;
        location    "system";
        object      fvSchemes;
    }

and from the `blockMeshDict`, which has no `location`:

    FoamFile
    {
        format      ascii;
        class       dictionary;
        object      blockMeshDict;
    }

`grep -rl "version     2.0" $FOAM_TUTORIALS/incompressibleFluid/` finds **no file**. So the writer must
**not** emit `version 2.0;` — the modern format omits it, and emitting it would make our output differ from
every file the install ships.

### The solver-selecting key is `solver`, not `application`

`pitzDaily/system/controlDict`: `solver incompressibleFluid;`. Recorded already, repeated here because the
brief lists it as an inventory item and it is the kind of thing a writer gets wrong from memory.

### Patch types after `blockMesh`, from `constant/polyMesh/boundary`

    inlet
        type            patch;
    outlet
        type            patch;
    frontAndBack
        type            empty;

**A 2-D case's front and back are `empty;`, not `patch;`** — and `empty` is what makes the solver treat it
as two-dimensional. A writer that emits `patch` for them turns the duct into a 3-D case with two walls.

### How the solver is selected and where the viscosity lives

- `constant/physicalProperties`: `viscosityModel constant;` and `nu 1e-05;`
- `constant/momentumTransport`: `model kEpsilon;` and `turbulence on;` — **the tutorial is turbulent, so
  `laminar` is an edit we make**, and the laminar model is why
  `div((nuEff*dev2(T(grad(U))))) Gauss linear;` must be in `divSchemes`.

---

# T-232 snappyHexMesh `[VERIFY-OF14]` items — verified 2026-10-16

Every item the T-232 brief marked uncertain, answered from the install's own source and tutorials.
Install: `$FOAM_INST_DIR`, `Version: 14` (the file's own convention — see the header).

## (T-232.3) Surfaces are read from `constant/geometry/`, NOT `constant/triSurface/`

**`constant/geometry/`.** Evidence: `tutorials/incompressibleFluid/motorBike/motorBike/constant/`
holds a `geometry/` directory and no `triSurface/`:

    $ ls tutorials/incompressibleFluid/motorBike/motorBike/constant/
    geometry  momentumTransport  physicalProperties  README

The dictionary names the file relatively and snappy resolves it under `constant/geometry/`:

    geometry
    {
        motorBike
        {
            type triSurface;
            file "motorBike.obj";
        }
    };

The comment in the same file confirms the feature-edge location: *"This is a featureEdgeMesh, read
from constant/geometry for now."* (`system/snappyHexMeshDict:90`). So both the staged surface and the
`surfaceFeatures` output live under `constant/geometry/`.

*(For the agent's staging rules: both are under `constant/`, which `agent/stokes_agent/jobpaths.py`
`STAGED_DIRS` already allows.)*

## (T-232.2) The inside point: `insidePoint` is preferred; `locationInMesh` also works

`src/mesh/snappyHexMesh/snappyHexMeshDriver/refinementParameters/refinementParameters.C:84-91`:

    dict.found("insidePoints")    ? lookup<List<point>>("insidePoints")
    : dict.found("insidePoint")   ? List<point>(1, lookup<point>("insidePoint"))
    : dict.found("locationInMesh")? List<point>(1, lookup<point>("locationInMesh"))
    : List<point>::null()

**All three are accepted, in that precedence.** Every OF14 tutorial uses the singular
`insidePoint (3 3 0.43);` (`motorBike`, and the same in `propeller`, `wingMotion`, `turbineSiting`),
never `locationInMesh`. **Emit `insidePoint`** — the documented form for this version;
`locationInMesh` is the backward-compatible spelling. `insidePoints` (plural) is the multi-region
list and is not what a single-domain mesh wants.

## (T-232.1) Background patches: `type patch`

`tutorials/incompressibleFluid/motorBike/motorBike/system/blockMeshDict:35-55` declares the boundary
with `type patch;`. snappy replaces the background patches with the ones it creates from the
surfaces, so the name is a placeholder — it does not need a special type.

## (T-232.2) `surfaceFeaturesDict`: `surfaces` and `includedAngle`, and `trimFeatures`

    $ cat tutorials/incompressibleFluid/propeller/system/surfaceFeaturesDict
    surfaces
    (
        "propeller-innerCylinder.obj"
        "propeller-outerCylinder.obj"
        "propeller.obj"
    );
    includedAngle   150;
    trimFeatures
    {
        minElem         10;
    }

Both keys the brief names are real, and `includedAngle 150` matches the brief's value. The
Foundation utility is **`surfaceFeatures`** — already in `ALLOWED_UTILITIES`, as the brief's
correction 1 says.

## (T-232.4) `meshQualityDict`: the include exists at `etc/caseDicts/...`

    $ find etc/caseDicts -name 'meshQualityDict*'
    etc/caseDicts/mesh/generation/meshQualityDict
    etc/caseDicts/mesh/generation/meshQualityDict.cfg

So `#includeEtc "caseDicts/mesh/generation/meshQualityDict"` resolves, and it is already on the
lint's `ALLOWED_INCLUDE_ETC`. The tutorials write the keys **inline** instead
(`meshQualityControls { maxNonOrtho 65; maxBoundarySkewness 20; ... }`), which is what the writer
will do: an inline block needs no include and is self-contained in the copy.

## (T-232.4) `fvSchemes`/`fvSolution` are NOT required by snappy

They are present in the snappy tutorials because those cases go on to solve; `snappyHexMesh`,
`blockMesh`, `surfaceFeatures` and `checkMesh` read only `system/controlDict` (for `deltaT`/`writeControl`
timing output) and their own dictionaries. **The writer will not emit them.**

## `snapControls`: every key confirmed, with the real defaults

`src/mesh/snappyHexMesh/snappyHexMeshDriver/snapParameters/snapParameters.C:33-39`:

- **required** (`lookup`, so the dictionary MUST carry them): `nSmoothPatch`, `tolerance`,
  `nSolveIter`, `nRelaxIter`.
- **optional** (`lookupOrDefault`): `nFeatureSnapIter` (default `-1`), `explicitFeatureSnap`
  (default **`true`**), `implicitFeatureSnap` (default **`false`**), `multiRegionFeatureSnap`
  (`false`), `detectNearSurfacesSnap` (`true`).

The brief's `nSmoothPatch 3`, `tolerance 2.0`, `nSolveIter 50`, `nRelaxIter 5`,
`nFeatureSnapIter 10`, `implicitFeatureSnap true` are all valid OF14 keys.

## **`curvatureLevel` DOES NOT EXIST in OF14**

    $ grep -rn "curvatureLevel" src/
    (no output)

There is no per-surface curvature-level key. Surface (curvature) refinement is driven by
`resolveFeatureAngle <degrees>` (a `castellatedMeshControls` key, `refinementParameters.C:45,63`)
combined with the per-surface `level (min max)` pair (`refinementSurfaces/refinementSurfaces.C:100`):
cells seeing intersections at an angle above `resolveFeatureAngle` are refined to the **max** of the
pair. Per-surface keys that do exist: `level (min max)`, `gapLevelIncrement`, `perpendicularAngle`.

**Consequence for the 1115 fidelity design**: the intent ("refine the surface by curvature, not a
flat level") is met by `resolveFeatureAngle 30` plus a `level (min max)` with a gap. An emitted
`curvatureLevel` would be silently ignored — the mesh would simply look unrefined, with no error.

## `snappyHexMesh` runs with no flag in OF14

`tests/fixtures/of14/help/snappyHexMesh.txt`: `-overwrite` is listed as *"Deprecated option, this is
now default behaviour"*. **Run it bare.** `-noOverwrite` is the opt-out.

---

## `addLayersControls`: every key verified (M-504)

**Sources, in order of authority:** the installed source (OpenFOAM 14 Foundation, the files
quoted below) and two tutorials' whole blocks
(`tutorials/incompressibleFluid/motorBike/motorBike/system/snappyHexMeshDict`,
`tutorials/incompressibleFluid/propeller/system/snappyHexMeshDict`).

**Required by `layerParameters` (a `lookup`, so the dictionary MUST carry them):**
`relativeSizes` (`layerParameters.C:106`), `minThickness` (`:115`), `nGrow` (`:138`),
`maxFaceThicknessRatio` (`:141`), `nBufferCellsNoExtrude` (`:145`), `nLayerIter` (`:147`),
`featureAngle` (`:128`, in degrees), and **two of the three** thickness figures:
`firstLayerThickness` (`:163`), `finalLayerThickness` (`:173`), `expansionRatio` (`:193`) —
the 2-of-3 rule with `FatalIOError "Over- or underspecified layer thickness."` at `:230-257`
when only one or all three are given.

**Optional (`readIfPresent`/`lookupOrDefault`):** `nRelaxedIter` (`layerParameters.C:258`,
default `labelMax` = never relax), `slipFeatureAngle` (`medialAxisMeshMover.C:378-387`,
default `featureAngle/2`), `additionalReporting` (`false`), `meshShrinker` (`medialAxis`).
The smoothing counts `nSmoothSurfaceNormals 1` and `nSmoothNormals 3` are **required**
(`medialAxisMeshMover.C:360,388`, `lookup`), `nSmoothThickness` is snappy's own; `nRelaxIter`
is read from the merged dict during the layer shrink (`medialAxisMeshMover.C:1966`, required)
and is supplied by `addLayersControls` because the mover's dict is
`combinedDict = layerParams.dict(); combinedDict.merge(motionDict)`
(`snappyLayerDriver.C:3110-3112`).

**The medial-axis angle key accepts BOTH spellings** — `minMedianAxisAngle` and
`minMedialAxisAngle` are aliases (`medialAxisMeshMover.C:369`); the tutorials write
`minMedianAxisAngle 90;` and so does the driver.

**Per-patch mixing of relative and absolute: NOT possible.** `relativeSizes` is a single bool
for the whole dictionary (`layerParameters.C:106`), so one mode per project is not a
simplification but the only shape; the settings' single `layer_mode` enforces it.

**The `relaxed` sub-dictionary lives in `meshQualityControls`, not in `addLayersControls`:**
during the layer pass the driver switches quality dictionaries after `nRelaxedIter` iterations
(`snappyLayerDriver.C:3145-3152`, `motionDict` → `motionDict.subDict("relaxed")`), and the
quality check reads every key from whichever it gets (`checkMesh.C`, `lookup<scalar>(key,
true)`). A missing key in the sub-dictionary is found in the **parent** (the recursive lookup,
`dictionary.C:529-535`), so the propeller tutorial's `relaxed { maxNonOrtho 75; }` under a full
`meshQualityControls` is the minimal correct shape and the driver copies it.

**The feature-angle default is 100**, the value 9 of the 15 snappy tutorials' layer blocks
carry (5 use 60 — propeller among them — and one 180; counted over
`grep -rln addLayersControls $FOAM_TUTORIALS --include=snappyHexMeshDict`).

## The final layer table: the real print, from a real run

`printLayerData` (`snappyLayerDriver.C:2456-2545`) prints, unconditionally at the end of the
layer pass, a table of **all meshed patches** (walls and plain patches — a patch that was not
asked for layers shows mean layers 0 and coverage 0, so the service must pair the table with
the counts it requested rather than trusting the rows):

    patch      faces    layers   overall thickness
                                        [m]       [%]
    -----      -----    ------   ---       ---
    walls       2454    3        0.000319  99.5

The columns are: patch name (left-justified to the longest name), faces, **mean layers per
face** (the average of per-face layer counts, not a per-cell count), mean overall thickness in
metres, and the coverage — the mean over the patch's faces of achieved/wanted thickness, as a
percentage. **The wanted count is not printed.**

Fixture: `logs/snappy-layers.log`, from the pipe (a cylinder, r 3 mm × 30 mm, base size 1 mm,
3 layers on `walls` through the real service on thinkyboy, 2026-10-19): `Mesh OK.`, 15,254
cells, `Extruding 2454 out of 2454 faces (100%)`, coverage 99.5 %.

## The [F] fidelity re-measure: layers do not pull the boundary back

The flanged-pipe STEP (the demo project's `demo_pipe_e4977bbfe7f9.step` under the Stokes
home's `demo/` folder — products
`pipe`, `inlet_flange`, `outlet_flange`, and the `fluid` solid it carries), the `fluid` solid
meshed through the whole service on thinkyboy, base size 3 mm, 2026-10-19:

| | without layers | with 3 layers on every wall |
|---|---|---|
| cells | 195,935 | 321,416 |
| `Mesh OK.` / result `ok` | yes | yes |
| max non-orthogonality | 50.1° | 51.4° |
| max skewness | 1.11 | 3.12 (under the 4.0 bar) |
| fidelity max deviation | 0.0000374 mm | **0.0000374 mm — identical** |
| fidelity mean deviation | 0.00000043 mm | 0.00000043 mm — identical |
| volume error / feature capture | 0.0 % / 100 % | 0.0 % / 100 % |
| coverage | — | walls: 43,349 faces, 2.89 mean layers, **96.1 %** |

**The deviation figures are bit-identical because the layer pass grows inward from the same
snapped surface** — the fidelity card's answer to the [F] question ("the layer pass can pull
the boundary back where layers collapse") is that with these rules it did not: coverage 96.1 %
with no collapse, and the card reads Good both ways. The skewness rise (1.11 → 3.12) is the
layer cells at the flange step, still under the threshold.

## The [F] fidelity re-measure: a finer boundary changes the card, and stays Good (M-503)

The flanged-pipe STEP's `fluid` solid through the whole service, base size 4.0 mm, 2026-10-19
(M-503). **Before** — no boundary size (the walls ride the base/2 rule, level 1, achieved
2 mm):

| | before | after: `walls` at 1.0 mm (level 2) |
|---|---|---|
| cells | 96,863 | 398,684 |
| `Mesh OK.` / result `ok` | yes | yes |
| max non-orthogonality | 43.7° | 45.4° |
| max skewness | 1.94 | 1.89 |
| fidelity max deviation | 0.0000390 mm | 0.00273 mm |
| fidelity mean deviation | 0.00000051 mm | 0.00000115 mm |
| volume error / feature capture | 0.0 % / 100 % | 0.0 % / 100 % |
| verdict | Good | Good |

**Both cards read Good, and the finer boundary's numbers are WORSE, not better** — the max
deviation moved by 70x (0.039 µm to 2.7 µm) and the mean by about 2.3x. The reading: the
coarse mesh's sparse sampling never touched the worst-fitting spot; the finer surface samples
it and the snapping there pulls a facet off the CAD. This is exactly what the [F] flag exists
to surface — a finer control is not automatically a truer mesh, and the card is what says so.
The estimate stayed honest through both: the after run's band 351k-1.66M contains the real
398,684.

Also measured here, and recorded because it will bite a user: **a boundary size can land on the
same level as the rule and change nothing** — `walls` at 1.5 mm from a 4 mm base resolves to
level 1, achieved 2 mm, the same dictionary, the same 96,863 cells. The Sizing page's achieved
and deviation columns (33 % off in that case) are what make it visible; the estimate's cell
range correctly does not move.

## The [F] fidelity re-measure: the feature angle at 30 and 45 (M-515)

Both parts through the whole service, 2026-10-19. **The setting reaches the dictionaries and
the mesh regenerates** — the `surface` sub-basis moved on both parts (pipe
`22fbf4c7…` to `f52f7224…`; flange `1a063cca…` to `c2ccc646…`), which is the staleness
contract: a change makes `changed == ["surface"]` and Generate re-runs.

| | pipe 30° | pipe 45° | flange 30° | flange 45° |
|---|---|---|---|---|
| cells | 7,892 | 7,892 | 96,863 | 96,863 |
| `Mesh OK.` / result `ok` | yes | yes | yes | yes |
| max deviation | 0.0028 µm | 0.0028 µm | 0.039 µm | 0.039 µm |
| mean deviation | 0.0974 nm | 0.0974 nm | 0.510 nm | 0.510 nm (the seam ticked to 0.5104) |
| volume error / capture | 0 % / none* | 0 % / none* | 0 % / 100 % | 0 % / 100 % |
| verdict | Good | Good | Good | Good |

*The cylinder's B-rep has no edges, so the capture column is ABSENT — the card shows nothing
rather than a 100 % nobody measured.

**The reading**: the cell counts and deviations are IDENTICAL across the threshold on these two
parts — the pipe (a smooth cylinder) has no edges to keep either way, and the flange's steps
are all 90°, kept at any threshold in the band. The setting is real (the dictionaries moved and
the meshes regenerated), and a part with edges in the 30-45° band is where the threshold would
bite; the Fidelity card's capture column is what shows it when it does. The mean deviation on
the flange ticked in the 8th digit — the extracted edge set differs slightly at the seam.

## The plain pipe's fidelity, with and without 3 layers (the M-504 review's 'Also')

The pipe (a cylinder, r 3 mm x 30 mm), base 1.0 mm, through the whole service, 2026-10-21 —
the second half of the "pipe, flange STEP" the M-504 brief asked for:

| | without layers | with 3 layers |
|---|---|---|
| cells | 7,892 | 15,254 |
| `Mesh OK.` / result `ok` | yes | yes |
| max deviation | 0.000002829355620744958 mm | **0.000002829355620744958 mm — the same float** |
| mean deviation | 0.0000000973552 mm | 0.0000000870138 mm (slightly better) |
| volume error / capture | 0 % / absent (no B-rep edges) | 0 % / absent |
| verdict | Good | Good |
| coverage | — | 99.5 % on 2,454 wall faces, 3.0 mean |

**The max deviation is the same float to every digit** — the layers grow inward from the same
snapped surface — and the mean deviation moved in the SEVENTH significant digit, in the right
direction: the prism layers follow the cylinder marginally better than the snapped cells alone.
The earlier "bit-identical" reading on the flanged-pipe STEP holds here too.
