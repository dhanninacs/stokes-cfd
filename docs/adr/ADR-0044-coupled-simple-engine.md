# ADR-0044 · The coupled engine: `coupled.simple` on Linux and WSL hosts

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
4. **Serial first** (superseded by decision 9, 2026-11-21). `cores > 1` was refused in the first
   step, because MPI needs the PETSc backend and `decomposePar`.
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
9. **Above one core the engine runs PETSc over MPI, with settings measured on public cases**
   (2026-11-21). `cores > 1` writes a decomposition, runs `coupledSimpleFoam` over MPI and
   reconstructs, and `COUPLED` gets `linearSolver PETSc` with `PETSC_OPTIONS`: `KSPFBCGS`, a
   Schur field split (`selfp`, upper factorisation), ILU under block Jacobi on the velocity block,
   one hypre BoomerAMG V-cycle with three-dimensional settings on the pressure block, an adaptive
   inner tolerance (1e-8..1e-2), the preconditioner rebuilt every 8 outer iterations and
   pseudo-transient continuation at `pseudoCo 50`. The 3D cell limit of decision 3 applies to one
   core only. FoamWin builds the solver without PETSc, so the agent refuses a parallel coupled
   step on native Windows with what to do instead (`agent.coupled_mpi_unavailable`).

   Measured on 6 ranks of a 24-core WSL host, one run at a time, every case public and every
   segregated run exactly what Stokes writes (SIMPLEC, relaxation 0.9, GAMG pressure):

   | Case | Cells | SIMPLEC | Coupled | |
   |---|---|---|---|---|
   | Square duct, L = 100 Dh, Re 100 (Shah & London fRe 14.227) | 1.0M | 795 it, 280 s | 16 it, 70 s | **4.0x coupled**; fRe 14.1396 from both |
   | Lid-driven cube, Re 400 (Ku et al. 1987) | 250k | 276 it, 22 s | 158 it, 242 s | 11x segregated |
   | Lid-driven cube, Re 400 | 1.0M | 578 it, 261 s | 92 it, 1033 s | 4x segregated |
   | Laminar tee (snappy-style, 53 degree non-orthogonality) | 347k | 79 it, 9 s | not converged in 30 it (37 s) | segregated |

   **With heated walls, through Stokes end to end** (the same square duct on Stokes' own blockMesh,
   800k cells, Pr 7, inlet 300 K, walls 350 K, 6 ranks; Shah & London Nu_T = 2.976):

   | | `of14.fluid` (SIMPLE) | `coupled.simple` |
   |---|---|---|
   | Iterations | 1051 | 16 |
   | Solve | 428 s | 55 s |
   | Run, end to end | 447 s | 73 s (**6.1x**) |
   | Nu_T, x = 50..90 Dh | 2.9981 (+0.74 %) | 2.9966 (+0.69 %) |
   | Fanning fRe | 14.0918 | 14.0918 |
   | Bulk T at the outlet | 342.77 K | 342.76 K |

   The writer gives `T` a relaxation factor of 1 ahead of the SIMPLE case's `".*" 0.9`:
   coupledSimpleFoam relaxes `T` by the usual lookup, and relaxed it needed 267 outer iterations
   (472 s, slower than SIMPLE) where the flow had converged in 16. The convection scheme was not
   the cause (`upwind` 272 iterations; `limitedLinear 1` 1543 and Nu 3.2 % low).

   So the engine wins where SIMPLE's iteration count grows with the domain, long and large
   through-flow, and loses on closed recirculating flow, which needs the pseudo-transient damping
   (no setting converged without it in 200 outer iterations) and then many outer iterations. The
   solver's own large-case option (`a11` Schur approximation) stalled on the tee's polyhedral
   mesh, which is why `selfp` is used. The catalogue text says where the engine pays off.
10. **Native Windows runs it in parallel where FoamWin has the PETSc build** (2026-11-21).
    FoamWin's `scripts/build-coupled.sh` now also builds coupledSimpleFoam against a static
    MinGW PETSc (hypre, C BLAS/LAPACK, Microsoft MPI SDK; `scripts/build-petsc.sh`) into
    `bin/msmpi`, beside the serial build in `bin`, because it imports msmpi.dll. The agent
    resolves the `bin/msmpi` build when the FoamWin handshake reports Microsoft MPI, and
    `engine_env.coupled_has_petsc` permits an MPI coupled step only when the resolved build is
    that one (a POSIX install is trusted as before); otherwise the step is still refused with
    `agent.coupled_mpi_unavailable` and what to do. Measured natively on the 1M-cell square duct
    of decision 9 (6 MS-MPI ranks): 16 outer iterations and 77 s against 70 s on WSL, with fRe
    and the inlet pressure equal to every printed digit; native SIMPLEC had not converged after
    1762 iterations (927 s), where on WSL it took 795.
    Through Stokes end to end on the heated duct of decision 9 (800k cells, 6 cores, native):
    `coupled.simple` 16 iterations, 66 s solve, 107 s run; `of14.fluid` 1051 iterations, 662 s
    solve, 714 s run (6.7x); Nu 2.9966 and 2.9981 and fRe 14.0918 from both, as on WSL.

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
- Parallel coupled runs need a coupledSimpleFoam with PETSc: a Linux or WSL host, or a FoamWin
  with the `bin/msmpi` PETSc build and Microsoft MPI installed (decision 10).
- Next steps: (3) `of14.multiRegion` with `foamMultiRun`; (4) `coupled.cht` behind its gate and
  the GPU flag; a FoamWin release whose installer ships the PETSc build.

## Steps

1. Catalogue, setting and refusals (`coupled.simple`, laminar, k-epsilon, k-omega SST; energy).
2. Writer and goldens.
3. Run path: executor branch, agent allow-list and flags, missing-program message.
4. Front end: the engine in the list, its notices.
5. Verification: the heated duct (`tutorials/heatedDuct`: Re 20, Pr 7, 300 K inlet, 350 K walls)
   through Stokes reproduces Nu = 7.54, on the WSL host and on native FoamWin
   (`tests/engine/test_coupled_heated_duct_engine.py`, `STOKES_COUPLED_HOST=wsl:<distro>` or
   `local`). Both pass.
