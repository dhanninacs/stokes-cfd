# 05 · OpenFOAM and engine integration

*Revised 2026-10-02: engines are attached, never shipped; in-house solvers are
built from source by a helper; Fluent is a paid engine through PyFluent.*

## 1. Dialects

| Topic | OpenFOAM Foundation 14 (v1) | OpenFOAM ESI v2512 (v1.5) |
|---|---|---|
| Solver invocation | `foamRun -solver incompressibleFluid`, `foamRun -solver fluid`, `foamMultiRun` | `simpleFoam`, `pimpleFoam`, `rhoSimpleFoam`, `chtMultiRegionFoam`, `interFoam` |
| Turbulence | `constant/momentumTransport` | `constant/turbulenceProperties` |
| Properties | `constant/physicalProperties` | `constant/transportProperties`, `constant/thermophysicalProperties` |
| Sources and constraints | `constant/fvModels`, `constant/fvConstraints` | `system/fvOptions` |
| Multi-region control | `regionSolvers { … }` in `controlDict` | `constant/regionProperties` |
| CHT interface condition | `coupledTemperature` on `mappedWall` patches | `compressible::turbulentTemperatureCoupledBaffleMixed` |
| Non-conformal coupling | `nonConformalCyclic` | `cyclicAMI`, `cyclicACMI` |
| Post utility | `foamPostProcess` | `postProcess` |

A dialect implements `write(model_snapshot, engine) -> CaseTree`,
`capabilities`, `parsers`, and later `read(case_dir)`. A third "dialect",
`fluent`, writes a PyFluent settings script instead of dictionaries (§8).

## 2. Dictionary model and case compiler

- **AST**: entries, sub-dictionaries, lists, dimensioned values, `#include`,
  comments; canonical printer with the required `FoamFile` header.
- **Parser**: a `lark` grammar for foreign cases and round-trip tests.
- **Compiler**: `compile(model, dialect, engine) -> CaseTree`, deterministic
  and pure; overrides applied last; written to the staging directory; a diff
  view between compiles.
- **Multi-region**: per-region `0/`, `constant/`, `system/`; `regionSolvers`;
  a `splitMeshRegions -cellZones -overwrite` step; interface patches
  `<fluid>_to_<solid>`.
- **Mesh dictionaries**: `blockMeshDict`, `snappyHexMeshDict` (per-selection
  STL with `solid` blocks mapped through `regions` to patch names,
  `refinementSurfaces`, `refinementRegions`, features, `addLayersControls`),
  `meshQualityDict`, cfMesh's `meshDict`, a Gmsh `.geo` with physical groups
  named after selections, `decomposeParDict` (scotch), `createPatchDict`.
- **Boundary compilation** (engineer-facing kind → per-field entries). Since ADR-0022 there is one
  `boundary` node type carrying a condition `kind`; the `kind` is what selects the row below, and
  the kinds are the eight in the catalogue (`boundary.kinds`), not the pre-ADR-0022 "types":

| Stokes condition kind | U | p | T | k, ω, ε, νt |
|---|---|---|---|---|
| `velocity_inlet` (Velocity inlet) | `fixedValue` | `zeroGradient` | `fixedValue` | intensity and length scale |
| `mass_flow_inlet` (Mass-flow inlet) | `flowRateInletVelocity` | `zeroGradient` | `fixedValue` | as above |
| `pressure_inlet` (Pressure inlet) | `pressureInletVelocity` / `zeroGradient` | `fixedValue` / `totalPressure` | `fixedValue` | intensity and length scale |
| `pressure_outlet` (Pressure outlet) | `pressureInletOutletVelocity` | `fixedValue` / `totalPressure` | `inletOutlet` | `inletOutlet` |
| `wall` (Wall; no-slip, adiabatic / fixed T / heat flux / HTC) | `noSlip` | `zeroGradient` | `zeroGradient` / `fixedValue` / `externalWallHeatFluxTemperature` | wall functions |
| `symmetry` (Symmetry) | `symmetry` | `symmetry` | `symmetry` | `symmetry` |
| `cyclic` (Cyclic) | `cyclic` | `cyclic` | `cyclic` | `cyclic` |
| `interface` (Fluid–solid interface) | `noSlip` | `zeroGradient` | `coupledTemperature` (OF14) | wall functions |

The compiler is M3 work; `pressure_inlet` was added to the catalogue by ADR-0022 and its row here is
the intended mapping, not a built one. A boundary whose tag holds **edges but no faces** is
incomplete and is not compiled as a patch (ADR-0022 §15.4); `region` tags never hold edges or
vertices.

## 3. Engine attachment (ADR-0011)

Stokes ships no engines. On first run and on demand it probes:

| Host | How it looks |
|---|---|
| Windows | `wsl.exe -l -v` for distros; inside each, `/opt/openfoam*`, `/usr/lib/openfoam/*`, `$HOME/OpenFOAM/*`, and any `etc/bashrc` the user points to |
| Linux | the same paths natively, plus `$FOAM_INST_DIR`, `which foamRun`, module files |
| Both | `cartesianMesh` (cfMesh), `gmsh`, `coupledSimpleFoam`, `paraview`, `nvidia-smi`, PETSc (`pkg-config`), Eigen, compilers, MPI |

The probe sources the environment, reads the version, and records a
capability record (dialect, version, available engines and meshers, cores,
memory, GPU) shown under Settings → Engines. v1 supports Foundation 14; other
versions are listed as "found, unsupported in this release" with the reason.

### The build helper

`stokes engine install coupled` (also offered as "Build now" in the Solver
tree) runs inside the chosen environment through the agent:

1. Check prerequisites: git, compilers, Eigen, MPI, optional PETSc with hypre,
   optional CUDA; offer the distro's package manager commands for what is
   missing (the user runs them, or Stokes does with permission).
2. Clone `github.com/dhanninacs/coupledSimpleFoam` at the pinned tag into
   `~/.stokes/engines/src/`; the `-gpu` layer when `nvidia-smi` and a CUDA
   PETSc are present (B5).
3. Run `Allwmake` with the detected OpenFOAM environment; capture the log.
4. Run the solver's own tutorials as a smoke test; record versions and hashes
   in `~/.stokes/engines/manifest.json`.

cfMesh is built the same way when not already installed, after M2 confirms
which OpenFOAM line its current source builds against.

## 4. Runners and hosts

`Runner` interface: `probe()`, `stage(case_tree, inputs) -> job`,
`launch(job, steps)`, `stream(job)`, `cancel(job)`, `fetch(job, artifact)`.

| Runner | Tier | How |
|---|---|---|
| `LocalLinuxRunner` | free | agent in-process; jobs under `~/.stokes/jobs/<id>` |
| `WslRunner` (Windows) | free | one long-lived agent per session: `wsl.exe -d <distro> --cd / -- stokes-agent --serve`; inputs staged to `//wsl.localhost/<distro>/home/<user>/.stokes/jobs/<id>`; only STL and the compiled case cross the bridge; artifacts return over the socket |
| `SshRunner` | paid (built early for the remote test workstation testing) | `ssh host stokes-agent --serve` with a port forward; `scp`/`rsync` staging; the user's keys |
| `SchedulerRunner` (SLURM, PBS) | paid, v1.5 | the agent on the login node submits and tracks jobs |
| `ContainerRunner` (Docker, Podman) | paid, v1.5 | the agent inside an image the user provides; also the macOS path |

Measured facts from fingen that shape this: the project share is SMB and
cannot be mounted in WSL; the UNC bridge moves about 21 MB/s; a Windows pid
from `wsl.exe` is unrelated to the Linux process, so the agent owns process
identity and liveness.

## 5. The agent protocol

- **Transport**: TCP on localhost (WSL or SSH tunnel) or a Unix socket;
  length-prefixed frames; msgpack payloads; request/response plus events.
- **Methods**: `probe`, `engine.install`, `job.create`, `job.start`,
  `job.cancel`, `job.status`, `job.list`, `artifact.get(job, name, lod)`,
  `extract.surfaces`, `extract.slice`, `extract.iso`, `extract.streamlines`,
  `extract.probe`, `report.values`, `log.tail`.
- **Events**: `job.state`, `job.progress`, `monitor.residual`,
  `monitor.report`, `log.lines`, `build.progress`, `heartbeat`.
- **Artifacts**: compact binary with LOD levels, cached by content hash on
  both sides.
- **Security**: localhost only; a per-session token; the agent checks the
  licence flag for `remote` when it is not on the local machine.

## 6. Job state machine

```
queued → staging → meshing → preprocessing → solving → extracting → done
                      │            │             │                └→ done_unconverged
                      └→ failed    └→ failed     └→ failed / cancelled
```

A step is a declared process invocation with a success criterion and a
parser. `reconstructPar` is avoided: the agent reads decomposed data directly
with VTK. The agent keeps running when the desktop closes; reconnect is
first-class.

## 7. Monitors and convergence

- Residuals parsed from solver logs; per region for `foamMultiRun`;
  coupledSimpleFoam's block residual, `coupled assembly check` and
  `coupled energy check` lines parsed too; Fluent residuals read through
  PyFluent.
- Report definitions compile to stock function objects (or Fluent report
  definitions); values streamed as they appear.
- Stopping criteria evaluated by the agent: residual targets, stationarity
  windows, balance closure, max iterations; stops requested through
  `controlDict` (`stopAt writeNow`) or the PyFluent session.
- `done_unconverged` when the criteria did not all pass by the end.

## 8. Engines

| Engine id | Invocation | Physics | Dialect | Tier | Notes |
|---|---|---|---|---|---|
| `of14.incompressibleFluid` | `foamRun -solver incompressibleFluid` | steady and transient incompressible, laminar / RANS / LES, passive scalars, porous, MRF | OF14 | free | default |
| `of14.fluid` | `foamRun -solver fluid` | compressible, buoyant, full energy | OF14 | free | |
| `of14.multiRegion` | `foamMultiRun` | conjugate heat transfer | OF14 | free | |
| `of14.VoF` | `foamRun -solver incompressibleVoF` / `compressibleVoF` | free surface, phase change | OF14 | free, v1.5 | |
| `coupled.simple` | `coupledSimpleFoam` (built by the helper) | steady incompressible, laminar or RANS (k-ε, k-ω SST exercised), optional passive-scalar energy, pseudo-transient; Eigen serial or PETSc MPI | OF14 | free | not: transient, non-conformal patches, wedge, fvModels, MRF, multiphase |
| `coupled.cht` | `coupledSimpleFoam` cht branch | multi-region CHT | OF14 | free, when gated | |
| `coupled.gpu` | CUDA build via the `-gpu` layer | as `coupled.simple` | OF14 | free, flagged by hardware | one GPU is worth about ten CPU cores on the reference case |
| `fluent` | PyFluent session (meshing and solver) | Fluent's own models mapped from the Stokes tree; v1 covers the same physics list as OF14 | fluent | **paid** | user's licensed Fluent 2024 R2 SP05 or later |
| ESI set | `simpleFoam`, `pimpleFoam`, `rhoSimpleFoam`, `chtMultiRegionFoam`, `interFoam` | | ESI | free, v1.5 | |

An engine definition carries a capability predicate over the model, its
dictionary or script additions (for coupledSimpleFoam the `COUPLED`
sub-dictionary with `adaptiveTolerance`, `pcReuseInterval` and `petscOptions`
presets by size class), log parsers, and validation cases.

### The Fluent engine (paid)

- **Mesh**: either Fluent Meshing through a PyFluent meshing session
  (watertight workflow from the exported per-selection STL, with sizes mapped
  from the Stokes mesh model) or the OpenFOAM mesh converted with
  `foamMeshToFluent` (an OpenFOAM utility, a process).
- **Setup**: the `fluent` dialect emits PyFluent settings calls (models,
  materials, cell zones, boundary conditions, methods, controls, report
  definitions) from the same tree; the compiled-case preview shows the script.
- **Run and monitor**: iterations and residuals streamed from the session;
  stopping criteria applied by the agent; Fluent's licence-seat semaphore
  idea from fingen keeps concurrent Fluent runs within the user's seats.
- **Results**: PyFluent field data for surfaces and probes, or an EnSight
  export read by VTK for scenes.

## 9. Validation matrix

- Classic benchmarks: lid-driven cavity, backward-facing step, pitzDaily,
  cylinder drag, heated parallel plates (Nu = 7.54), a multi-region heater.
- coupledSimpleFoam's own tutorials: `cavity`, `finnedDuct`, `heatedDuct`,
  `stripChannel`, `stripChannelRAS`, byte-identical logs where the repository
  claims it.
- Public verification cases: NASA TMR flat plate and NACA 0012, ERCOFTAC
  periodic hills for RANS model checks.
- No employer cases or data.
