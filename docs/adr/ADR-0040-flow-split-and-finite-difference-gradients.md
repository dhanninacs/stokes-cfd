# ADR-0040 · Flow-split objective, finite-difference gradients and the verified-adjoint gate

**Status:** proposed · 2026-11-14 · amends ADR-0038 (decisions 4 and 5); builds on ADR-0016, ADR-0035, ADR-0037 · amended by ADR-0041 (decision 2: the flow split may use the open-source adjoint where the host has it; finite differences stay its default)

## Context

The owner wants a demonstration that ends with a shape: a manifold with one inlet and four outlets whose header is reshaped until the four outlet flows are equal. ADR-0038 has one CFD objective (the total-pressure loss) and one gradient source (the legacy Foundation adjoint, whose outlet conditions hard-code that objective). A Windows walk-through of ADR-0038's loop (2026-11-14 review) showed what an unchecked gradient does: SLSQP went to a bound and halved back about nine times, twice, because the wall-only adjoint sensitivity could not see that the variable resized the inlet; the 0.7 % "gain" it reported was remeshing noise. So a second objective needs a gradient source that is right by construction, and the adjoint needs rules that keep a wrong gradient from being called one.

## Decision

1. **Objective `flow_split`.** J = Σᵢ (Qᵢ/Q − 1/N)² over the N outlets, Qᵢ the volumetric flow through outlet i (outward positive) and Q their sum. It is computed on the desktop (`stokes.explore.shape.flow_split`) from per-port flows the agent reads where the case is (`stokes_agent.sensitivity.port_flows`, returned by `job.sensitivity` as `flows`): from the solver's face flux `phi` when the time has it, else U·S_f. The outlets are the patches of boundaries of kind `pressure_outlet`. Each row also carries the fractions, the largest deviation max|Qᵢ/Q − 1/N|, the mass balance and the mean pressure on each port.
2. **Gradient source `finite_difference`.** `FiniteDifferenceObjective` makes N + 1 ordinary primal solves per cycle (engine `of14.incompressibleFluid`, no adjoint): forward differences, backward where the forward step would leave the upper bound. Rows are labelled `finite_difference`; they need no gate, and they are the reference the adjoint is judged against. `shape.optimise` takes `gradient` (`adjoint` | `finite_difference`; `finite_difference` is the default for `flow_split` and the only one accepted for it until decision 6 passes for it) and `fd_step` (SI per variable; 10 % of the span by default). Every solve is reported as a `shape.probe` event.
3. **Stop at the target.** A least-squares J flattens near its optimum and a line search there chases noise, so `flow_split` ends as `target_reached` at the first cycle with max|Qᵢ/Q − 1/N| ≤ `target` (default 0.01, one percentage point).
4. **A cycle that is not a steady answer is refused** (`shape.cycle_failed`, step `solve`): when |Σ Q_ports| / Q_in > 0.5 %, or when an outlet has net inflow or more than 1 % of its |flux| entering. Backflow also breaks the adjoint outlet condition.
5. **Coplanar ports keep their own faces.** A plane rule of `stokes.geometry.retag` carries the area-weighted centre of its tagged faces; a rebuilt face that lies in the plane of several rules goes to the nearest centre. Four outlet caps in one plane stay four tags.
6. **The verified-adjoint gate.** A gradient is called `adjoint` (without "unverified") only after a `shape.check` of the same part, the same quantities and the same objective has passed:
   - each row's forward and backward differences, from the check's own three runs, agree within 30 % of the central one, and |J₊ − J₋| is at least five noise floors; otherwise the check is `inconclusive` (`passed` null) and suggests a step: twice it when the signal is weak (cause `noise`), half it when the signal is strong but the two differences disagree (cause `curvature`);
   - every ratio adjoint / finite difference lies in [0.8, 1.25] and, with two or more quantities, the cosine of the two gradients is at least 0.95; a negative ratio is reported as `wrong_sign`.
   The verdict is stored per session; an adjoint row says `gate: verified | unchecked`, and a **failed** gate blocks `gradient: adjoint` for those quantities. The default step is 5 % of the value and, for a length, at least four base cells of the mesh: a quantity at zero no longer gets a 0.1 µm step.
7. **Safety rules of the adjoint loop.**
   - A quantity that moves or resizes a port (an inlet, an outlet or a symmetry plane: any plane-rule tag) is refused for the adjoint before the first cycle, with the ports named; the part is rebuilt at the quantity plus a small step and a port whose area changes by more than 0.2 %, or that leaves its plane, counts. The wall sensitivity cannot see a port.
   - At the second cycle the secant (J₁ − J₀) is compared with the first gradient's prediction g₀·(x₁ − x₀); when they disagree in sign beyond the noise floor the run ends as `gradient_disagrees` instead of spending its budget on a line search. This check is not applied to finite differences, whose disagreement there is curvature, not a wrong gradient.
   - Every CFD result says `improvement` = (J₀ − J_best)/|J₀| and `significant` = improvement > `noise_floor` (default 0.01, or the measured one): a change below the floor is not reported as an improvement.
8. **Housekeeping of long work.** The per-part running, cancelled and active-cycle sets, the history and the checks live in one per-session state behind a lock (`stokes.api.shape_state`), not in module globals keyed by node id: two sessions on copies of one project share ids. `shape.cancel(now)` stops a cycle through the mesh service and the run store, never through `session.execute` from a second thread. A finished run removes its cycle folders except the best and the last. An unexpected error reaches the client as `shape.internal` with a sentence, the trace goes to the log.

## Built and measured (2026-11-14, FoamWin Foundation 14, this laptop, 4 cores shared with other work)

- The demonstration case is built by `tools/manifold_demo.py` through API commands only: a header of four boxes (the first 16 mm high, the next three `h2`, `h3`, `h4`, project parameters), an 8 mm round inlet pipe, four 7 mm round outlet pipes made by a linear pattern, all blended (2 mm), and clipped by a sharp box so the inlet (x = 0) and the outlet caps (y = −20 mm) are exact planes that do not move with the variables. Laminar air, 0.2 m/s at the inlet, snappyHexMesh at 1 mm (about 72 k cells).
- **The pipes are round on purpose.** A planar cap with straight edges came out as four triangles after the display mesh was simplified, and the face grouping (`shading_groups`) merged it into its neighbour as a sliver, so no tag could name it; a disk cap keeps its curved edge. The same merging made symmetry planes cut through round stubs unusable, so the case has walls on both sides instead of symmetry planes. Reported to the geometry work; until it changes, small flat ports should be round.
- snappyHexMesh's background box for a `part` domain was a flat 0.05 m around the part (3.7 M background cells around a 130 mm manifold at 1 mm); it is now 5 % of the part's largest side and at least two base cells. Every `part`-domain mesh basis changes with it.
- **The run** (`tools/manifold_demo.py demo`, h2, h3, h4 in [6, 26] mm from 16 mm, 1 mm steps, SLSQP):

  | | h2, h3, h4 (mm) | outlet fractions | max deviation | J | cells |
  |---|---|---|---|---|---|
  | baseline | 16, 16, 16 | 0.2644, 0.2663, 0.2419, 0.2273 | 0.0227 | 1.052e-3 | 71,903 |
  | cycle 1 | 26, 26, 21.08 | 0.2516, 0.2603, 0.2495, 0.2386 | 0.0114 | 2.380e-4 | |
  | cycle 2 | 26, 26, 25.94 | 0.2512, 0.2598, 0.2492, 0.2398 | 0.0102 | 2.027e-4 | |
  | final (cycle 3) | 26, 26, 26 | 0.2512, 0.2598, 0.2492, 0.2398 | 0.0102 | 2.024e-4 | 87,506 |

  - Noise floor: the baseline solved twice from scratch gave the same J to every digit (serial snappyHexMesh is deterministic here); h2 + 0.05 mm moved J by +3.4e-6 (0.32 %), so a 1 mm step is far above the noise.
  - 4 cycles, 16 solves, 84 min of optimisation (3.5 to 4.6 min a solve: about 2.5 min of it snappyHexMesh, with the machine shared). SLSQP ended `converged` with every height at its upper bound: J fell 81 %, the largest deviation from 2.27 to 1.02 percentage points, just short of the 1.0 target. **The bound decides the answer**: deeper downstream segments are all the optimiser can use, so a higher bound or another variable is needed to reach the target. Mass balance stayed below 4e-7, no backflow, every solve converged (103 to 107 iterations).
  - Applied to the live project (`parameter.assign`, then the tags found again), solved again: the same fractions to four digits. Inlet pressure (kinematic) 0.0510 to 0.0500 m²/s².
  - Mesh independence at 0.7 mm: baseline 0.2642, 0.2660, 0.2425, 0.2273 (150,914 cells), final 0.2511, 0.2592, 0.2500, 0.2397 (184,022 cells); every fraction within 0.08 points of the 1 mm value, the final deviation 0.0103.
  - `shape.check` (flow_split, h3, ±1 mm, at the baseline): central −0.143 per m, forward −0.107, backward −0.179 at 2,700 noise floors: `inconclusive`, cause `curvature` (J is a sum of squares), and a step of half the size is suggested. A check that cannot tell curvature from noise would have asked for a larger step.
- Found on the way, left to the GUI phase: after `project.open`, `parameter.assign` did not re-run the implicit operations of the opened project in a headless session (the tool now runs them, as a cycle does), and after an apply every boundary tag on an implicit part is stale until it is found again; the panel's Apply needs both. **Done (2026-11-15):** `shape.apply` sets the values, runs the part's implicit operations again in tree order and re-finds its tags with this decision's rules (decision 5), in the background with a `shape.applied` event; the panel's Apply calls it (`tests/test_shape_apply.py`). The panel also gained the flow split (outlets, target), the gradient source, the check's verdict, the per-outlet shares and the deviation chart; `shape.optimise` takes `outlets`.

## Alternatives considered

| Alternative | Why not |
|---|---|
| A flow-split adjoint now (new outlet conditions in the legacy solver) | FoamWin has no compiler for coded conditions; the conditions belong in the open-source solver (ADR-0038 decision 7), and that adjoint must pass decision 6 before it is used. It is the next phase. |
| Central differences in the optimiser | 2N + 1 solves per cycle instead of N + 1; the check uses central differences, where the cost buys the reference. |
| A fixed split as the outlet condition | J would be zero by construction: equal pressure on the outlets is what makes the split a result. |
| Trust the adjoint and let SLSQP's line search cope | Measured: it went to the bound and back twice and reported noise as a gain. |

## Consequences

- Shape optimisation has a second CFD objective that works today, at N + 1 solves per cycle.
- An adjoint gradient is only called one after it has been checked on the user's case; an unchecked one says so, a failed one is not used.
- Open: `docs/00-open-questions.md` G28 (power loss with a resized velocity inlet is not a meaningful objective; offer J/Q or a fixed flow rate) and G29 (the noise floor measured per case or set per mesh size).
