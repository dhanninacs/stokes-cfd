# OF14 test fixtures — what these are and why they may live here

**This folder is OpenFOAM tutorial content and OpenFOAM's own `-help` output. It is TEST DATA. It never
ships, and no product code reads it.**

## Licensing: these files are GPL-3 and that is deliberate

`CLAUDE.md` §1 rule 1 forbids GPL code **in the application or the agent**. Tutorial dictionaries and
utility help text are neither: they are data used by `tests/` to check that a parser reads the real
format, in the same way a golden file is data. `tools/check_forbidden_imports.py` and
`tools/audit_licenses.py` police *imports and dependencies*, and nothing here is imported.

**The OpenFOAM licence headers are kept verbatim, on purpose.** Stripping them would make GPL content look
like ours, which is the one thing that would actually be wrong about copying it. Every `.step`-like
artefact under `pitzDaily/`, `cavity/` and `help/` still carries the

    OpenFOAM: The Open Source CFD Toolbox ... Website: https://openfoam.org ... Version: 14

header it came with. `tools/check_ip.py` exempts `docs/` from its scan; `tests/` is **scanned**, so if a
term in the private blocklist ever appeared in tutorial content this folder would fail the gate — and that
is the right outcome, because it would mean the blocklist term is too broad rather than that the fixture
is wrong.

**Packaging: the answer is not the same for both targets, and it is worth stating exactly.** From
`pyproject.toml`:

    [tool.hatch.build.targets.wheel]
    packages = ["stokes", "agent/stokes_agent"]

    [tool.hatch.build.targets.sdist]
    include = ["stokes", "agent", "tools", "docs", "tests", "README.md", "LICENSE", "CLAUDE.md"]

So **the wheel excludes `tests/`** (only two packages ship) and **the sdist includes it** — by name, on
purpose, as it includes `tools` and `docs`. These fixtures therefore travel in a source distribution.
That is a fact for the owner to weigh rather than a defect I fixed: **nothing was changed**, per the
brief, and the sdist is a developer artefact rather than what a user installs. If GPL tutorial data in an
sdist is unwanted, the fix is a line of packaging config and a decision that is not mine — recorded here so
it is not discovered later.

## Provenance, file by file

| Path | Where it came from |
|---|---|
| `help/<utility>.txt` | the first 60 lines of `<utility> -help`, run on thinkyboy with the install's environment script sourced |
| `pitzDaily/{0,constant,system}` | `$FOAM_TUTORIALS/incompressibleFluid/pitzDaily` — see "which pitzDaily" below |
| `cavity/system/blockMeshDict` | `$FOAM_TUTORIALS/incompressibleFluid/cavity/system/blockMeshDict` |
| `logs/blockMesh.pitzDaily.log` | `blockMesh -dict $FOAM_TUTORIALS/resources/blockMesh/pitzDaily`, exit 0, 0.33 s, 12,225 cells |
| `logs/checkMesh.pitzDaily.log` | `checkMesh`, exit 0, 0.43 s, `Mesh OK.` |
| `logs/foamRun.pitzDaily.log` | `foamRun`, exit 0, 54.21 s |
| `logs/blockMesh.bad.log`, `logs/checkMesh.bad.log` | the cavity dict with vertex 6 moved to `(2 2 0.1)`; blockMesh exit 0, checkMesh exit **0** with `Failed 2 mesh checks.` |
| `logs/foamRun.fatal.log` | pitzDaily with `0/U` deleted, no time directories; exit **1**, `cannot find file ".../0/U"` |

## Which pitzDaily, and why

`find $FOAM_TUTORIALS -maxdepth 3 -name pitzDaily` returns **four**:

    tutorials/resources/blockMesh/pitzDaily     mesh only, not a case
    tutorials/incompressibleFluid/pitzDaily     <- THIS ONE
    tutorials/potentialFoam/pitzDaily           a different (potential) solver
    tutorials/fluid/pitzDaily                   the legacy multi-physics solver

**`incompressibleFluid`**, and **it is a TRANSIENT run, not the steady one** — corrected 2026-10-14
(round 52). The earlier text here called it "the steady one" and cited `adjustTimeStep yes`, which is
itself a **transient** control: it is what makes `deltaT` adapt, exactly as the log shows
(`Time = 0.000119048s` at the first step, `Time = 0.3s` at the last).

The evidence that it is transient, and that it cannot contain a convergence line:

* `system/fvSolution` has **`PIMPLE { nNonOrthogonalCorrectors 0; nCorrectors 2; }`** and **no `SIMPLE`
  section at all** — which is why every step does **two** `p` solves;
* `logs/foamRun.pitzDaily.log` has **zero** `SIMPLE solution converged` lines in its 17,219.

**T-212's duct is the steady one** and its engine test measured `SIMPLE solution converged in 64
iterations`. So a task needing a convergence line must take it from the duct, not from this fixture —
which is what T-213's brief says to do and why. The `controlDict` facts that do hold:
`solver incompressibleFluid;`, `stopAt endTime`, `endTime 0.3`.

## Two things these fixtures exist to catch, both measured

1. **`checkMesh` exits 0 while reporting failure.** `logs/checkMesh.bad.log` ends with
   `Failed 2 mesh checks.` and the process returned **0**. A quality gate that trusts the exit code
   accepts a mesh OpenFOAM has just failed.
2. **A run can exit 0 having done nothing.** pitzDaily sets `startFrom latestTime;`, so a case copied with
   time directories present restarts from the latest one and never reads `0/U`. The clean case fails
   (exit 1); the contaminated one succeeds at nothing. `logs/foamRun.fatal.log` is the **clean** case.

## Reproducing any of this on thinkyboy

```bash
source $STOKES_OPENFOAM_BASHRC
cp -r $FOAM_TUTORIALS/incompressibleFluid/pitzDaily /tmp/pd && cd /tmp/pd
blockMesh -dict $FOAM_TUTORIALS/resources/blockMesh/pitzDaily   # NOT a bare blockMesh: the case
checkMesh                                                        # has no system/blockMeshDict
foamRun
```

**A bare `blockMesh` fails** in this case with `Cannot find file "system/blockMeshDict"` — the mesh lives
in `tutorials/resources/blockMesh/pitzDaily`, and the case's own `Allrun` passes `-dict`. Worth knowing
before the first attempt.
