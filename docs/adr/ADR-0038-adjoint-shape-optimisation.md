# ADR-0038 · Adjoint shape optimisation on Foundation 14, and the open-source adjoint solver track

**Status:** proposed · 2026-11-12 · builds on ADR-0001 (GPL process boundary), ADR-0016 (design exploration), ADR-0017 (shape optimisation), ADR-0024 (jobs and results), ADR-0025 (native Windows), ADR-0035 decisions 25–30, ADR-0037. Amends ADR-0035 decision 28 ("Foundation 14 has no shape adjoint").

## Context

The owner wants an optimiser driven by adjoint gradients in the app, with no second OpenFOAM install and no new FoamWin. ADR-0035 assumed Foundation 14 had no shape adjoint, but the FoamWin package ships `adjointShapeOptimisationFoam`, Othmer's continuous adjoint, filed under `applications/legacy/incompressible`.

What that solver offers:
- It is steady and incompressible, with frozen turbulence.
- Its objective is the total-pressure loss between inlet and outlet, set by its adjoint outlet conditions `adjointOutletPressure` and `adjointOutletVelocity`.
- It writes U, p, Ua and pa.
- It also grows a porosity field `alpha` (topology optimisation), controlled by `lambda` and `alphaMax`.

Its pitzDaily tutorial was measured on FoamWin with `lambda 0`: 1,000 iterations took 49 s on the owner's laptop and ended with exit code 0. The adjoint continuity error stayed large (local sum about 400), and the `pa` residual ended near 0.04. The owner then asked for this route first, to be improved later with code borrowed from ESI's adjoint. The improved solver is to be published as open source on its own.

## Decision

0. **Measured stability (2026-11-12, FoamWin).** pitzDaily was run three ways with this solver:
   - With the tutorial's porosity (`lambda 1e5`) it is stable: `Ua` is O(1) and the adjoint continuity error is about 4e-5.
   - Turbulent (k-epsilon, frozen) with `lambda 0`, it **diverges**: `Ua` reaches about 5e4 and the cumulative continuity error about 4e8.
   - **Laminar at Re ≈ 250** with `lambda 0`, it is stable: `Ua` is about 0.1–0.25 and the continuity error about 2e-3.

   The built-in route is therefore for **laminar internal flows**. A turbulent case is refused with `compile.unsupported`, which names the open-source solver of decision 7. That solver brings ESI's adjoint-transpose-convection treatment and its differentiated turbulence models.
1. **Engine `of14.adjoint`.** A shape-optimisation cycle runs FoamWin's `adjointShapeOptimisationFoam` with `lambda 0` and `alphaMax 0`. The porosity therefore never grows, and the run is one primal solve plus its adjoint. Stokes uses it for sensitivities only, never for the porosity update. The porosity mode stays available later as a topology suggestion (ADR-0035 decision 30).
2. **The case is derived from the ordinary one.** `stokes.compile.of14.adjoint.write` is a pure function from the incompressible case tree to the adjoint case tree. It adds `0/Ua` and `0/pa`, the `lambda`, `alphaMax` and `nu` entries, and the Ua and pa schemes and solver settings, and it sets the application. The adjoint boundary values follow the solver's tutorial:
   - `Ua` is fixed at −1 along the inlet flow direction;
   - the outlet uses `adjointOutletVelocity` and `adjointOutletPressure`;
   - walls are no-slip for `Ua` and zero-gradient for `pa`.

   Only an incompressible, steady case with inlets, outlets and walls is accepted. Anything else is refused with `compile.unsupported`.
3. **The wall sensitivity is computed by Stokes from the case's own fields.** It is not computed in the solver.
   - Formula (Othmer 2008, frozen turbulence): s = −ν (∂U/∂n)·(∂Ua/∂n) on each wall face.
   - Both normal derivatives are one-sided: (φ_face − φ_owner) / d, where d is the distance from the owner-cell centre to the face along the face normal.
   - The cell centre is the mean of the cell's face centres. That is exact for parallelepipeds and first order otherwise.
   - The code is pure numpy over the ASCII case (`stokes_agent.foamascii`, `stokes_agent.sensitivity`). It runs where the case is, in the agent, which answers `job.sensitivity` with the arrays.
   - s is defined as ADR-0035 decision 26 states it, and `implicit.gradient` sums it.
   - *Amended by ADR-0041 decision 8 (2026-11-15):* when the case holds the open-source solver's written `shapeSensitivity`, the agent returns that instead of the reconstruction, and says which (`source`).
4. **Verified before it is trusted.** The sign and the scale of s are fixed by one check: on a parametric duct, dJ/dp from decision 3 must agree with the central difference of J from two primal runs (ADR-0035 decision 31's test). Until that check passes, the optimiser marks adjoint gradients as **unverified** in its history and in the panel. A gradient method may also use finite differences, through the same loop.
5. **The loop** (`stokes.explore.optimise.gradient`, `stokes.explore.shape`). Each cycle:
   1. sets the variables on a private copy of the project;
   2. rebuilds and meshes the part;
   3. runs the adjoint case and reads J (the total-pressure loss, from the run's monitors);
   4. computes s and then dJ/dp;
   5. lets SLSQP or L-BFGS-B choose the next point inside the variables' bounds.

   The objective and gradient come from an `Evaluator` with a gradient, so the loop is tested without CFD: with the volume of an implicit part as J, its gradient is exact. Each cycle is one row of a study table with the study's variables, the objective, the gradient and its source (`adjoint`, `finite_difference` or `exact`).
6. **GUI.** The Explore stage gets an Optimise panel. It shows:
   - the objective (pressure loss) and the variables with their bounds;
   - Start, Stop and the cycle count;
   - a convergence plot of J and |∇J| per cycle;
   - the current parameters;
   - the wall sensitivity, coloured on the surface like any result field (ADR-0017 decision 4).
7. **The open-source adjoint solver track.** ESI's adjoint library, borrowed under its GPL-3.0 licence, is merged with Foundation 14's adjoint into a separate GPL program, built with FoamWin's toolchain. The merge adds objectives (drag, lift, flow uniformity), the differentiated Spalart–Allmaras model and a `shapeSensitivity` field written by the solver itself. It lives in its own public repository, with no Stokes code in it. Stokes drives it through files and argv, as it drives every OpenFOAM program (rule 1). The engine is `of14.adjoint` with a solver choice, and decisions 3 to 6 stay as they are, so the loop and the GUI do not change.

## Built and measured (2026-11-12)

- **The cycle is wired** (`stokes.api.shape_cycle`). Each cycle:
  1. copies the project folder (its geometry, not its runs) and opens it in a private session;
  2. re-runs the implicit operations;
  3. finds the boundary tags again by rule (`stokes.geometry.retag`): a planar tag by its plane, the one remaining tag as the rest. Stable face keys are ADR-0035 slice 2, not built;
  4. meshes and runs the `of14.adjoint` engine through the ordinary run queue;
  5. asks the agent for J and s (`job.sensitivity`), since the case lives where the agent ran it.

  On a channel of 60 × 20 × 20 mm with a spherical obstacle of R = 4 mm (snappy at 2 mm, laminar air at 0.05 m/s), a cycle took 32 s.
- **Two corrections, found by the finite-difference check** (decision 4):
  - s = +ν (∂U/∂n)·(∂Ua/∂n). The minus sign of the first draft gave dJ/dR the wrong sign.
  - The adjoint inlet is −U_in at full speed. The solver tutorial's unit vector scales s by 1/|U_in|: 20× too large at 0.05 m/s.

  After both corrections, dJ/dR from the adjoint is 2.45e-5 against a central difference of 1.84e-5 (h = 0.25 mm), a ratio of 1.33 on the 2 mm mesh and **1.24 on the 1 mm mesh** (adjoint 2.38e-5, central difference 1.92e-5). The ratio falls with the cell size, as a first-order wall gradient's error does; a second-order wall gradient (or s written by the open-source solver of decision 7) is the next improvement. The rest is the one-sided wall gradient and the coarse mesh, so gradients stay marked unverified, and the Optimise panel's **Check gradient** (`shape.check`) runs the same comparison on the user's own case.
- **The loop from the GUI:** minimising the pressure loss over R in [3, 5] mm took 3 cycles to reach R = 3 mm, with J falling from 1.29e-7 to 1.13e-7. SLSQP's tolerance is absolute, so the driver scales J and its gradient by |J| at the first cycle (otherwise it "converged" after one cycle).
- **Long work runs off the server's command thread.** Every command runs on the event loop, so `shape.optimise` and `shape.check` start a thread by default (`wait` false) and report through `shape.step`, `shape.cycle`, `shape.finished` and `shape.checked`. `shape.cancel` stops a run at the end of the cycle under way, or, with `now`, cancels the mesh or run of the cycle under way (the cycle raises `Cancelled` and is not recorded); a gradient check stops between or during its three evaluations the same way. Each `shape.step` names the cycle, the step's place among the six (`step_number`, `steps`), the seconds since the cycle began (`elapsed`) and, for a check, which evaluation it is (`check` of `checks`, `check_run`: base, plus, minus), so the panel shows a check as "Check 2/3" rather than as a further optimisation cycle (Windows walkthrough, 2026-11-13).
- `tag.rematch` with a given result now stamps the parts' current version even when the face ids are the ones the member had; otherwise an inlet that stays face 0 is refused as stale.

## Walked through the GUI (2026-11-12)

`web/e2e_adjoint_gui.mjs` builds a 60 × 20 × 20 mm channel with a 4 mm spherical obstacle from the start screen, tags its inlet and outlet in the guided Boundaries step (the rest stays "wall by default"), meshes it (15,033 cells), runs it (41 iterations), and optimises the obstacle's radius from the Optimise panel: the pressure loss falls from 1.29e-7 to 1.13e-7 in three cycles (radius 4 to 3 mm, the lower bound), and **Check gradient** reports an adjoint / finite-difference ratio of 1.36. The walk-through found that a cycle must tag the faces nobody tagged as the walls of its copy (decision 5 now says so), and led to the GUI fixes in the CHANGELOG.

## The open-source solver, started (2026-11-12)

`of14-adjoint` holds `adjointFoam`. It is cross-built for Windows by `wmake` against the FoamWin tree, without rebuilding FoamWin. It adds:
- the wall `shapeSensitivity`, from OpenFOAM's boundary snGrad. It agrees with Stokes' reconstruction to a median face ratio of 1.000, and gives the same J;
- the objective, logged every iteration;
- optional porosity;
- inlets found by their condition;
- ESI's adjoint transpose convection (ATC) forms and limiter, ported from `ATCModel`.

On the turbulent pitzDaily case, where the Foundation solver diverges without porosity, zeroing the ATC term next to the inlets and outlets keeps the adjoint bounded (|Ua| ≤ 6.35 against 1e5). So turbulent cases become possible once Stokes drives this solver. The next ESI pieces (objectives, E-SI sensitivity, differentiated Spalart–Allmaras) are in that repository's ROADMAP.md.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Port ESI's adjoint first | Weeks to months before the first gradient; the loop, the GUI and the verification do not depend on it. |
| Install ESI OpenFOAM beside FoamWin | The owner does not want a second OpenFOAM install. |
| Compute s inside OpenFOAM with a coded function object | FoamWin has no compiler for `#codeStream` (open question U2); the open-source solver of decision 7 writes it instead. |
| Read U and Ua as the STKFLD1 point fields | Point fields on a patch cannot give the wall-normal gradient. |

## Consequences

- Shape optimisation on implicit parts works on Foundation 14, with the pressure loss of an internal flow as the objective.
- Gradients carry an "unverified" label until decision 4's check passes.
- Open questions (`docs/00-open-questions.md`):
  - G25: the inlet adjoint velocity's magnitude and sign for other inlet conditions;
  - G26: the name and licence wording of the open-source solver repository (decided: `of14-adjoint`, GPL-3.0-or-later, https://github.com/dhanninacs/of14-adjoint);
  - G27: whether the legacy solver's convergence (adjoint continuity error) is good enough without the ESI merge.

## Amended by ADR-0040 (2026-11-14)

ADR-0040 adds the `flow_split` objective and the `finite_difference` gradient source, and makes decision 4 a gate: an adjoint gradient is called verified only after a passing `shape.check` of the same part, quantities and objective (forward and backward differences consistent, ratios in [0.8, 1.25], cosine at least 0.95), and a failed check blocks it. It also refuses the adjoint for quantities that move a port, stops a run at the second cycle when J moves against the gradient (`gradient_disagrees`), and reports no improvement below the noise floor.
