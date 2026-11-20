# ADR-0035 · The coupled engine: `coupled.simple` on Linux and WSL hosts

**Status:** accepted, step 1 in progress · 2026-11-20 · M5 slice 1; relies on ADR-0011 (engine
attachment), ADR-0023 (runs and executors), ADR-0024 (hosts and the agent), ADR-0027 (the energy
equation), ADR-0001 (the GPL process boundary)

## Context

`docs/05-openfoam-integration.md` §8 lists `coupled.simple`, `coupled.cht` and `coupled.gpu`, and
decision C3 puts coupledSimpleFoam's CPU solver in v1, but on 2026-11-20 none of it existed: the
catalogue held `of14.incompressibleFluid` and `of14.fluid`, and `solve.configure` refused
`coupled.simple`. coupledSimpleFoam (github.com/dhanninacs/coupledSimpleFoam, v0.10, GPL-3.0) is a
standalone OpenFOAM 14 application, not a `foamRun` module: it reads an ordinary steady
incompressible case plus one `COUPLED` sub-dictionary in `system/fvSolution`, runs its own outer
loop (so `controlDict` does not stop it and **function objects do not run**), prints stock
`Solving for Ux|Uy|Uz|p, Initial residual = ...` lines, and with `solveEnergy yes` solves `T` as a
**passive scalar** (no buoyancy, constant properties). It is built with `wmake` against an OpenFOAM
14 development install, Eigen and optionally PETSc. On the owner's machine it is built in WSL
Ubuntu 24.04 next to `/opt/openfoam14`. FoamWin ships no compiler, but it is cross-built with
MinGW the way FoamWin builds cfMesh: FoamWin's `scripts/build-coupled.sh` (from v14.0.0-win.6)
installs a serial, Eigen-only `coupledSimpleFoam.exe` beside `foamRun.exe`. Its heatedDuct run
matches the Linux build to every printed digit (11 iterations, inlet Δp 13.0918221595, wall flux
1.7944553015, Nu 7.5416). PETSc and hypre have no MinGW/MS-MPI build, so FoamWin has no parallel
coupled solve.

## Decision

1. **`coupled.simple` is an engine like the others**: a catalogue entry, a case writer
   (`stokes/compile/coupled/simple.py`), an executor branch and agent allow-list entries (the
   parser needed no change, decision 7). It is `built: true` once step 5 below passes, and its
   `why` names what it does not do.
2. **It runs where the program is.** The agent resolves `coupledSimpleFoam` like any utility (the
   captured `PATH`, which on Linux includes `FOAM_USER_APPBIN`); a host without it refuses at the
   first step with words that say how to get it. FoamWin finds it in `FOAM_APPBIN` like any
   other tool, so nothing in Stokes is Linux-only; a FoamWin older than v14.0.0-win.6 simply
   lacks it. A Stokes-side build helper for Linux hosts is slice 2, not this one.
3. **The writer reuses the incompressible case and adds `COUPLED`.** The `of14` incompressible
   writer's files are kept (so one case directory still drives `foamRun` for a comparison, as the
   solver's own manual intends) and `system/fvSolution` gains `COUPLED` with:
   `nOuterIterations` = the settings' `iterations`; `residualControl { U, p }` = the settings'
   `residual_target` (and `T` with energy on); `linearSolver SparseLU` (serial); `pRefCell 0`,
   `pRefValue 0`. Steady only: a transient set-up is refused by name.
   **A 3D mesh above 80,000 cells is refused** (`DIRECT_MAX_CELLS_3D`; the snapshot carries the
   mesh's cell count, outside the run basis because the mesh basis already fixes it). Measured on
   a laminar 3D tee on a 24-core WSL host, 2026-11-21:

   | Cells | Backend | Result |
   |---|---|---|
   | 72,965 | SparseLU | converged, 18 iterations, 17.8 min, 10.4 GB |
   | 136,878 | SparseLU | 26 GB, ~440 s per iteration, not converged at 30 min |
   | 347,604 | SparseLU | ~45 GB, crashed in the first factorisation |
   | 347,604 | BiCGSTAB | ~585 s per iteration, 4 iterations in 40 min |
   | 347,604 | PETSc, serial | ~100 s per iteration, not converged at 40 min |
   | 347,604 | `foamRun` incompressibleFluid | converged in 28.3 s serial, 8.7 s on 16 ranks |

   So no serial backend rescues a large 3D case, and the refusal points to the segregated engine.
   2D meshes (an `empty` or `wedge` patch) have far less fill-in and are not limited.
4. **Serial first.** `cores > 1` is refused in this step with a message naming the reason (MPI
   needs the PETSc backend and `decomposePar`, which a later step adds and validates).
5. **Energy is the passive scalar the solver supports.** With `energy` on, `COUPLED` gets
   `solveEnergy yes` and `Pr = mu*cp/kappa` from the domain material, `0/T` is written from the
   boundaries' thermal conditions (`fixedValue` temperature, `zeroGradient`, and a heat flux as
   `fixedGradient` with `q/kappa`), and `T` gets a solver and its schemes. Gravity is refused with
   energy on (the solver has no buoyancy and would give a plausible wrong answer). The checks that
   said "the energy equation needs `of14.fluid`" now ask whether the engine *solves energy*.
6. **Monitors run after the solve.** Because coupledSimpleFoam never calls the function objects,
   the steps are `coupledSimpleFoam` then `foamPostProcess -latestTime`, which runs the
   `controlDict` functions on the final fields, so the run's flow and pressure monitors are the
   same ones an `of14` run reports.
7. **The `foamRun` parser reads the coupled log unchanged** (measured against the solver manual's
   lines): `EigenSparseLU:  Solving for Ux, Initial residual = ...` matches its residual pattern,
   `COUPLED solution converged in N iterations` its `converged in N iterations` pattern, and
   `COUPLED did NOT converge within N outer iterations` matches neither, so a capped run is not
   converged. The `coupled assembly check` and `coupled energy check` lines are not parsed yet.
8. **The agent says why a host cannot run it**: on FoamWin, that the install predates the build
   and to update it, build it with `scripts/build-coupled.sh` or use a WSL or Linux host; on
   Linux, how to build it. An agent whose Python lacks `msgpack` now stops
   before printing its address with the install command, where it used to accept the connection
   and fail on the first frame (the desktop saw only `CancelledError`).

## Alternatives considered

| Alternative | Why not |
|---|---|
| A separate writer from scratch | The case is the incompressible case plus one dictionary; a copy would drift. |
| Run function objects inside the solve | Not possible: the solver's outer loop does not call `Time::run()`. |
| Offer MPI from the start | It needs PETSc with hypre and `decomposePar`, and the serial path is the exit test (heatedDuct). |
| Energy through `of14.fluid` semantics (enthalpy, gravity) | The solver does not have them; pretending would hide a wrong answer. |

## Consequences

- A Windows user runs `coupled.simple` natively on FoamWin v14.0.0-win.6 or later, or through a
  WSL host (`WslRunner`).
- The run basis gains the coupled writer's version, so a coupled run is never reused for `of14`.
- Next steps: (2) the build helper; (3) `of14.multiRegion` with `foamMultiRun`; (4) `coupled.cht`
  behind its gate and the GPU flag; parallel PETSc runs once validated.

## Steps

1. Catalogue, setting and refusals (`coupled.simple`, laminar, k-epsilon, k-omega SST; energy).
2. Writer and goldens.
3. Run path: executor branch, agent allow-list and flags, missing-program message.
4. Front end: the engine in the list, its notices.
5. Verification: the heated duct (`tutorials/heatedDuct`: Re 20, Pr 7, 300 K inlet, 350 K walls)
   through Stokes reproduces Nu = 7.54, on the WSL host and on native FoamWin
   (`tests/engine/test_coupled_heated_duct_engine.py`, `STOKES_COUPLED_HOST=wsl:<distro>` or
   `local`). Both pass.
