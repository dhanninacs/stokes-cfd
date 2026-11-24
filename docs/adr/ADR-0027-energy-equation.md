# ADR-0027 · Energy equation: a thermal engine, its materials, its boundaries and its outputs

**Status:** Accepted, steps 1–6 done; convergence to 1e-4 not reached (laminar at Re ≈ 8e4) · 2026-10-22 · relies on ADR-0011 (engine attachment: the engine catalogue and the `solver_settings` node), ADR-0018 (materials: specific heat and conductivity are already catalogued), ADR-0022 (boundaries and conditions), ADR-0024 (run results and fields)

**Progress (2026-10-22).** Step 1 (the setting, the catalogue entry, the refusals) is in. Step 2:
the solve snapshot carries the energy data. `SolveSnapshot` gains `energy`, `mu`, `cp`, `kappa`,
`T_ref`, `gravity` and `p_ref`; `PatchCondition` gains `thermal` (`fixedValue`, `zeroGradient`,
`inletOutlet`, `heatFlux`), `temperature` and `heat_flux`, all defaulted, and filled by
`stokes.run.snapshot` (mapping in `stokes.run.thermal`) only with energy on. A convection wall is
refused as `solve.thermal_unsupported`. With energy off the snapshot and `solve_basis` are
unchanged, so existing runs stay current. Not yet: the gas/liquid energy form
(`sensibleEnthalpy` / `sensibleInternalEnergy`), which the writer step (c) decides.

**Step 3 (2026-10-22): the `of14.fluid` writer.** `stokes/compile/of14/fluid.py`
(`WRITER_VERSION = "of14-fluid/1"`) writes eleven files as pure functions of the snapshot, golden
under `tests/golden/compile/of14/elbow_energy/`; `stokes.run.executors.case_for` sends
`of14.fluid` snapshots to it and leaves `of14.incompressibleFluid` byte-identical. Decisions, from
the OpenFOAM 14 template and tutorials (no shipped case is this one): a constant-property liquid
(`heRhoThermo`, `pureMixture`, `const` transport with `Pr = mu*cp/kappa`, `eConst` with `Cv = cp`,
`rhoConst`, `sensibleInternalEnergy`); steady `PIMPLE` with `residualControl` on `p_rgh`, `U`,
`e`; `p_rgh` gauge (`prghPressure` at an outlet, `fixedFluxPressure` elsewhere), `p` absolute
(`calculated`, `pRef` = operating pressure); a heat-flux wall is `externalTemperature` with
`q constant <W/m2>`, positive into the fluid. Refused as `compile.unsupported`: a gas, a
convection wall, any turbulence but laminar, energy off. **Assumption:** the snapshot carries no
phase, so a density below 100 kg/m³ is taken to be a gas; a phase field on the snapshot is the
better test. The goldens prove determinism, not solver acceptance; step 5 runs the case. The engine
stays `built: false`.

**Step 4 (2026-10-22): the run path.** The agent's `foamRun -solver` allow-list accepts `fluid`.
The `foamRun` parser reads `Solving for e` as residual `e`, labelled `Energy` in a `labels` map on
the event and the report (present only when a labelled field was solved, so incompressible
reports are unchanged), and drops the `Solving for rho` rows whose initial residual is 0 (the
`rhoConst` density is not solved); `p_rgh`, `U` and `e` are kept. `extract` picks its fields per
job from the record's `foamRun -solver` argv (`engine_of_steps`): an `of14.fluid` job reads `U`,
`p` (absolute, Pa) and `T` (K), never `p_rgh`; any other job reads `U` and kinematic `p` as before.
`extract.fields`, `slice.cut` and `iso.cut` all use it, so an iso-surface of `T` reaches the
agent. `stokes.compile.parallel.reconstruct_fields_check` names what an energy run's reconstruct
must hold (`0/T`, `0/U`, `0/p`, `0/p_rgh`; `T`, `U`, `p`, `p_rgh` per time); `reconstructPar`
runs without `-fields`, so it brings them all back. **Not yet:** the check is a pure function the
executor does not call after a run; the front end does not show the `Energy` label or a `T`
legend yet (step e); the residual log is a synthetic fixture, as no energy case has been run
through the solver. **Assumption:** OpenFOAM 14's energy residual is named `e` for
`sensibleInternalEnergy` (it would be `h` for `sensibleEnthalpy`, which the writer refuses).

**Step 5 diagnosis (2026-10-22): the start state with gravity.** The first native elbow run
(88,280 cells, gravity `(0,-9.81,0)`, outlet at y = 0.2 m) aborted on iteration 2 with a negative
temperature, after about 2,700 m/s in the cells under the outlet. Root cause, isolated by
one-iteration variants: the writer wrote the outlet `prghPressure` `value` as the gauge `p`, but
`isothermalFluid` builds its first `netForce` from the file's boundary values before any update,
and `fixedValue` patches ignore assignment, so the outlet face carried a stale jump of
`rho*g*h` (about 1,950 Pa). Zero gravity, or `hRef` at the outlet height, or a `fixedValue` of
the true value all ran cleanly. Fixed in the writer (`of14-fluid/2`): the outlet `value` is
`p - rho*g.c` at the outlet's centroid, which the snapshot now carries (`PatchCondition.centroid`
from `tag.anchor`, energy on only; a gravity case without it is `compile.invalid`), and a case with
gravity and an outlet starts hydrostatic (`hydrostaticInitialisation`, `0/ph_rgh`), without which
the elbow still diverged at iteration 24. With both, the elbow ran 60 iterations natively with
`T` from 293 to 353 K and the outlet flow within 0.3 % of the inlets. The executor now reads the
energy monitors from `patchAverage(...,fields=(pT))` as `p_mean` and `T_mean`. **Still open:** an
outlet not normal to gravity gets one uniform value from its centroid; a diagnostic with gravity
along x diverged even with exact per-face values, so that orientation is unverified.

**Step 5 verification (2026-10-22): the engine is built.** With the start fixed, a 300-iteration
native FoamWin run of the elbow (inlets at 293 K and 353 K, unequal areas of about 3.96e-3 m² and
2.53e-4 m²) completed: `T` stayed within 293–353 K, the outlet mean was 294.92 K against a
294.86 K mass-weighted mixing balance, the outlet carried 4.076 kg/s against 4.072 kg/s in, and
`p_rgh` ended at 0.045, still falling, so the 1e-4 target was not reached. `of14.fluid` is now
`built: true` and its catalogue `why` states that limit. `tests/engine/test_energy_elbow_engine.py`
repeats the run on a copy of a local project named by `STOKES_ELBOW_ENERGY_PROJECT` (the elbow is
not in the repository) and skips without it. **Still open:** parallel energy runs are untested;
gravity along x diverged in a diagnostic; a non-horizontal outlet gets one `p_rgh` value from its
centroid; residual convergence to 1e-4.

**Step 6 (2026-10-22): convergence, gravity direction, tilted outlets, parallel.** Writer
`of14-fluid/3`.

- *Convergence.* The writer's case was run natively on 1 core to 1,000 iterations, with three
  fvSolution variants beside it. Every residual plateaus by about iteration 200 and none reaches
  1e-4. At 1,000 iterations (first-solve initial residuals): baseline `p_rgh` 0.038, `Uz` 9.4e-3,
  `e` 3.9e-4; `nNonOrthogonalCorrectors 1` `p_rgh` 0.045, `Uz` 0.016, `e` 6.0e-4; `p_rgh` 0.2 and
  `U` 0.5 relaxation `p_rgh` 0.034, `Uz` 8.8e-3, `e` 3.6e-4 (two correctors: same plateau). The
  limiting fields are `p_rgh` and `Uz` (the out-of-plane velocity). The cause is the physics, not
  the dictionary: the main inlet's Reynolds number is about 8e4 (1 m/s, about 0.07 m, water), so a
  laminar steady solution does not exist and the residuals oscillate; the incompressible laminar
  run of the same elbow also stopped at `p` 1.9e-3 after 500 iterations. The writer now writes one
  non-orthogonal corrector (the mesh reaches 57°): it cut the continuity error from 8.3 to 1.3 for
  6 % more time and left the residuals where they were. Through `run.start`, the 1,000-iteration
  run of the shipped writer ended `done_unconverged` with `p_rgh` 0.050, `Ux` 2.0e-3, `Uy` 3.6e-3,
  `Uz` 0.016, `e` 6.2e-4; `T` 293.00–353.00 K; outlet mean 294.97 K against a 294.86 K mixing
  balance; 4.0719 kg/s out against 4.0719 kg/s in. Reaching 1e-4 needs a turbulence model
  (refused by this writer) or a transient run, not a relaxation factor.
- *Gravity along x.* The diagnostic did not fail on its start value: the outlet condition was
  wrong. A `prghPressure` with a uniform `p` holds the same static pressure on every face; on the
  elbow's outlet (the plane y = 0.2 m) gravity along x makes the faces about 0.1 m apart in
  height, so that pressure differs from the hydrostatic column inside by up to about 960 Pa and
  drives an exchange flow. Re-run for 300 iterations it blew up at iteration 143 (outlet `T`
  3.9e5 K); exact per-face `value`s only fixed the first iteration. Fixed in the writer: the outlet
  is a `fixedValue` `p_rgh` of `p - rho*g.c` at the centroid, which holds the user's pressure at
  the centroid and a hydrostatic profile across the faces, as for an outlet into the same liquid
  at rest. With it, gravity along x ran 300 iterations with the residuals of gravity along y
  (`p_rgh` 0.043, `Uz` 0.012, `e` 5.5e-4), `T` 293.00–353.00 K, outlet 294.96 K, 4.069 kg/s. The
  hydrostatic initialisation already used the full vector (`g & h`), so it needed no change.
- *A tilted outlet.* Per-face `value`s are accepted by the solver (a 60-iteration diagnostic ran)
  but are the wrong condition for the reason above. The single centroid value, now a
  `fixedValue` `p_rgh`, is the right one for any tilt: no per-face field is written. For an outlet
  normal to gravity the old and new conditions are equal on every face, so the elbow with gravity
  along y is unchanged.
- *Parallel.* `cores` 2 through `run.start`: `decomposePar`, `foamRun -parallel -solver fluid`
  (2 ranks, MS-MPI) and `reconstructPar` ran 50 iterations in about 20 s; the reconstructed time
  holds `T`, `U`, `p`, `p_rgh`; `T` 293.00–353.00 K; outlet 4.0726 kg/s against 4.0719 kg/s in;
  outlet mean 300.02 K (at 50 iterations the outlet is still near its 300 K start, as in serial).
- *Run basis.* An `of14.fluid` run's basis now carries the fluid writer's `WRITER_VERSION`, not
  the incompressible writer's, so this change marks earlier energy runs stale.

### Open items

| Item | State | Evidence or next step |
|---|---|---|
| Residuals to 1e-4 | Not reached | Plateau `p_rgh` ≈ 0.04–0.05, `Uz` ≈ 0.01–0.02 at 1,000 iterations; laminar at Re ≈ 8e4. The turbulence model is kOmegaSST, written by ADR-0028; a native kOmegaSST run (300 iterations, 0, 3 and 5 wall layers) did not reach 1e-4; see ADR-0028. A transient run is the other option. |
| Gravity not normal to the outlet | Fixed (`of14-fluid/3`) | Gravity along x: 300 iterations, residuals as for gravity along y. |
| Tilted outlet | Resolved by the same condition | One `fixedValue` `p_rgh` at the centroid is hydrostatic across the faces; no per-face field. |
| Parallel energy runs | Verified, 2 ranks, 50 iterations | Decompose, solve, reconstruct; `T` in range, mass balance within 0.02 %. Not run above 2 ranks. |
| Reconstruct check | Open | `reconstruct_fields_check` is still not called by the executor. |
| Front end `T` legend | Open | Step (e). |

## Context

The owner asked for the energy equation to be enableable in Stokes, with the inputs and outputs it
needs. Today the only built engine is `of14.incompressibleFluid` with laminar turbulence
(`stokes/engines/catalogue.py`). It has no temperature field, no energy equation and no thermal
boundary condition, so `solver_settings` cannot express heat transfer.

The OpenFOAM 14 install on this machine (`pkg/windows/OpenFOAM-14`) shows how OpenFOAM 14 does it:

- The thermal fluid is the `fluid` solver, not `incompressibleFluid`. It is set per region in
  `constant/materialProperties` (`fluid { solver fluid; material <name>; }`), as in
  `etc/templates/singleFluidCHT`.
- Its material file `constant/<region>/physicalProperties` carries an `energy` entry:
  `sensibleEnthalpy` for a gas and `sensibleInternalEnergy` for a liquid or solid. The template's
  air uses `sensibleEnthalpy`; water and copper use `sensibleInternalEnergy`.
- Its fields are `0/T` (temperature), `0/p_rgh`, `0/p`, `0/U`, `0/alphat` (turbulent thermal
  diffusivity, for a turbulent model) and `0/G` (the radiation field, present in the template).
- Its time-dependent constants are `constant/fluid/g` (gravity, `(0 0 -9.81)`) and `pRef`.
- The installed tutorials contain only a laminar `cavity` with `incompressibleFluid`. No installed
  case runs the thermal path, so the writer cannot be checked against a shipped tutorial; it has to
  be checked against the template and by running it.

The materials catalogue already has specific heat and conductivity methods (`specific_heat`,
`thermal_conductivity` in `stokes/physics/materials/catalogue.py`), and `material.methods` lists them.
Conditions have no temperature kinds yet.

## Decision

1. **A new engine, `of14.fluid`, is the energy-capable solver.** It is added to the engine
   catalogue as `built: false` until its writer passes the golden tests, with `why` naming the
   missing pieces. `of14.incompressibleFluid` stays the default and is unchanged, so every existing
   project still writes the same case.

2. **Energy is a setting, not a new node.** `solver_settings` gains a boolean `energy` (default
   `false`). It is refused unless the engine is `of14.fluid`, and `solve.configure` says why.
   Turning `energy` on does not change any id, path or other parameter.

3. **Inputs when energy is on.**
   - Every fluid region's material needs `specific_heat` and `thermal_conductivity` (the existing
     materials methods). A missing one is a `solve.no_thermal_material` error from `solve.check`,
     naming the region, the same way `solve.no_material` names a missing density.
   - Boundary conditions gain temperature kinds, as ADR-0022's catalogue does for velocity:
     `inlet` gets a temperature (fixed value), `outlet` a backflow temperature, and `wall` a
     temperature or a convective heat transfer coefficient with a free-stream temperature. Each kind
     is a parameter of the existing `boundary` node, so the node id and the expression references
     stay as they are.
   - An energy-on set-up with no temperature on an inlet is a `setup.check` error.

4. **Outputs when energy is on.** The field `T` (static temperature, K) is written with the other
   fields and shows in the Results Fields list with its range. The energy residual (`Th` or `Te`,
   from the solver's own name) joins the residuals row and the convergence plot. Temperature
   contours and the wall-temperature result are Results rows like the existing field rows, and
   surfaces follow ADR-0026.

5. **Build order, each step with its own test.** (a) the setting, the engine catalogue entry and
   the refusals in `solve.configure` and `solve.check`; (b) the thermal boundary kinds and their
   conditions tests; (c) the `of14.fluid` case writer with golden files under `tests/golden/`
   (`0/T`, `0/p_rgh`, `constant/fluid/physicalProperties`, `system/fvSolution` energy entries),
   reviewed against the template; (d) the agent run path (`foamRun` on the WSL or native host, with
   the energy residual parsed); (e) the result fields and residual plots.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Add `energy` to `incompressibleFluid` | The incompressible solver has no temperature field in OpenFOAM 14; the flag would write an energy equation that the solver does not solve. |
| A separate engine per thermal case | Duplicates the catalogue; the setting says what the case adds, and the engine says how it is solved. |
| Write the case from a hand-copied template now | The template's dictionaries are not ours to ship; the writer is a pure function of the model (CLAUDE.md §1 rule 9), so it has to be written and golden-tested, not copied. |

## Consequences

- Nothing changes for an existing project until `energy` is turned on.
- The `of14.fluid` writer needs golden files that a reviewer reads against the OpenFOAM 14 template;
  it cannot be checked by running a shipped tutorial on this machine.
- The temperature boundary kinds extend the boundary catalogue, which the compiler (M3) will map
  to patch BCs.
- Until steps (a) to (e) are done, `energy` can be stored and shown, and every run that needs it is
  refused with the reason. The GUI must say that, not offer a run that will fail.
