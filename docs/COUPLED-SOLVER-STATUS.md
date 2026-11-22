# coupledSimpleFoam in Stokes: where it stands

> **Update, 2026-11-21: the engine exists.** `coupled.simple` is built (ADR-0044): it was
> written and measured on the Linux/WSL host and on native Windows, where FoamWin now ships
> coupledSimpleFoam (serial, and a PETSc build for MPI runs). It reproduces the heatedDuct
> Nu = 7.54 through Stokes, and on a heated square duct (800k cells, 6 ranks) the whole run took 73 s
> against `of14.fluid`'s 447 s with the same Nusselt numbers. ADR-0044 has the decisions and the
> measured limits (the serial direct solver stops at 80,000 3D cells). The rest of this note is the
> 2026-11-19 state, kept for the record.

Written 2026-11-19, from the native Windows host. The question was whether the coupled solver
works in the app and how much time it saves against the segregated one on the same cases. **It
could not be run here, and Stokes has no engine for it yet.** This note says exactly what exists,
what is missing, and what to do on the Linux host, where it can be built and measured.

## What exists

- The solver: `dhanninacs/coupledSimpleFoam` (GPL-3.0, version 0.10, OpenFOAM Foundation 14). A
  standalone application, not a `foamRun` module. Steady, incompressible, laminar or RANS
  (k-epsilon and k-omega SST exercised), optional passive temperature. Serial through Eigen
  (`SparseLU`, `BiCGSTAB`), parallel through PETSc.
- In Stokes: the agent's probe lists `coupledSimpleFoam` among the programs it looks for
  (`agent/stokes_agent/probe.py`), and the roadmap has the engine at milestone M5
  (`docs/06-roadmap.md`, decisions C3 and C4). Nothing else: no entry in the engine catalogue
  (`stokes/engines/catalogue.py`), no case writer, no executor, no allow-list entry in the agent
  (`engine_env.UTILITIES`, `flags.ALLOWED_FLAGS`, `jobs.ALLOWED_UTILITIES`), no choice in the GUI.

## Why it could not be run on this machine

- FoamWin 14 (`v14.0.0-win.5`, the install the app uses here) ships no `coupledSimpleFoam.exe`.
- Building it needs the OpenFOAM build system on Linux; FoamWin itself is cross-compiled from
  Linux, and this machine has no WSL and no Linux toolchain.
- So neither a correctness check nor a timing comparison was possible here. No number in this note
  was measured by this work; the figures in the solver's own README are its author's.

## What the engine needs (from the solver's `doc/USAGE.md`)

One case directory serves both solvers, which is what makes a fair comparison possible:
`foamRun` ignores the `COUPLED` sub-dictionary of `system/fvSolution`, and `coupledSimpleFoam`
ignores `SIMPLE` and the p and U relaxation factors. An engine `of14.coupledSimple` is therefore
the incompressible writer's case plus:

1. `COUPLED { nOuterIterations <iterations>; linearSolver SparseLU | PETSc; residualControl
   { U <target>; p <target>; } }` in `fvSolution` (every key has a default).
2. The job step `coupledSimpleFoam` in place of `foamRun -solver incompressibleFluid`; its log has
   the standard `Solving for ... Initial residual` lines, so the `foamRun` parser reads it.
3. **A post-processing step.** The solver does not execute `functions` entries (its outer loop is
   not the time loop), so the flow-rate and patch-average monitors Stokes reads for the pressure
   drop are not written during the run. The engine must run `foamPostProcess` on the last time
   directory afterwards, or the Results card shows no pressure drop.
4. Refusals in words: transient, the energy engine's physics beyond a passive scalar, `fvModels`,
   MRF; and more than one core unless the host's build has PETSc.
5. The host check the adjoint has (`run.py::_require_adjoint_program`): refuse before the job when
   the probed install has no `coupledSimpleFoam`, with the sentence that says how to get it.
6. Agent allow-lists, the turbulence table (`stokes/engines/turbulence.py`: laminar, kEpsilon,
   kOmegaSST only), the catalogue entry, the Solve page's engine choice, tests with a golden case.

## What to run on the Linux host

1. Build the solver against `/opt/openfoam14` (its `doc/INSTALL.md`: Eigen 3.4 from apt, PETSc
   optional) and run its `tutorials/cavity` to confirm the binary.
2. Add the engine as above, on `linux`, with the compile tests and one `engine`-marked test
   that solves the cavity with both engines and compares the fields.
3. The comparison walkthrough: the same project (the bend of `web/record_bend_walkthrough.mjs`, and
   a larger one of several hundred thousand cells, where the solver's README puts the crossover)
   solved with `of14.incompressibleFluid` and with `of14.coupledSimple` to the same residual
   target; report iterations, wall time and the difference in the pressure drop, side by side. On a
   mesh of tens of thousands of cells expect the coupled run to take fewer iterations and about
   the same or more wall time: the README's own claim is a wall-clock advantage above roughly
   300 k cells.
