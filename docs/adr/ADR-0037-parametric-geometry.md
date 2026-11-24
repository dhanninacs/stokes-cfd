# ADR-0037 · Parametric geometry: vector and integer quantities, sketch dimensions, the geometry study engine

**Status:** proposed · 2026-11-12 · builds on ADR-0012 (units and expressions), ADR-0016 (design exploration), ADR-0035 decisions 6 and 25–29, ADR-0036. Supersedes ADR-0036 decision 1's "entities are plain coordinates" and closes the gap behind ADR-0035 G20.

## Context

The owner asked for every geometric option to be parametric, and reachable by optimisers and adjoint methods. Several kinds of value were bare numbers, so no expression or study could reach them:
- vectors (position, size, offset, centre, origin, the split point) were `array`;
- counts were `int`;
- a drawn sketch's coordinates were JSON numbers.

Studies had only the `function` engine, which never rebuilds geometry. Also, an operation whose parameter is an expression was not re-run when the parameter it reads changed.

## Decision

1. **`vector` kind.** A vector parameter is three quantities, stored under dotted names (`position.x`, `position.y`, `position.z`), each `{value, unit, expr}` in SI.
   - The dependency graph, undo, persistence, summaries and study targets treat each component as an ordinary quantity.
   - `parameter.set` takes either `position` with three values or an object, all or nothing, or a single component such as `position.x`.
   - Expressions and studies name a component as `geometry.cyl.position.x`.
   - Every vector of the implicit operations uses this kind. The B-rep operations keep `array` until M1.
2. **Integer quantities.** Counts and polygon sides are dimensionless quantities flagged `integer`. A value off a whole number is refused (`model.not_integer`), never rounded. An expression such as `count = N` is allowed.
3. **Sketch dimensions.** `implicit_sketch` gains `dimensions`, a new `quantity_map` kind: named quantities, at most 64, each a length, an angle or a number, each possibly an expression.
   - Entries are stored as `dimensions.<name>`.
   - A coordinate or radius in `entities` may be an expression text over the sketch's dimensions only.
   - A dimension may in turn be an expression of project parameters, so the dependency graph stays exact.
   - `opplan` evaluates the texts when it builds the recipe (`stokes.geometry.sketchexpr`), so the worker and the mesher see numbers.
4. **Re-run on reach.** The operation store re-judges every operation whose parameters an edit re-evaluated (the event's `also`), and every operation on `resync`. Changing `R` therefore re-runs the sphere whose radius is `R`.
5. **`geometry` study engine** (`stokes.explore.engine_geometry`).
   - For each point, it sets the variables on a private copy of the project, rebuilds the implicit parts the responses name, and meshes them with the program a run uses (`stokes.implicit.bodymesh`, cached).
   - It then measures them: `volume`, `area`, `centroid.*`, `bbox_min.*`, `bbox_max.*` and `extent.*` (`stokes.geometry.measure`), named after the part, for example `geometry.parts.duct.volume`.
   - `study.create engine=geometry` selects it.
   - **Deviation from the architect's proposal:** measures are not stored on part nodes. The engine measures each point, and `geometry.measure` measures a part's current mesh. This avoids bloating every saved part and needs no cycle guard.
6. **Shape derivatives now** (ADR-0035 decision 27, early).
   - `implicit.normal_velocity(part, parameter)` returns V_n = −(∂φ/∂p)/|∇φ| for each triangle of the display mesh, in metres per SI unit.
   - `implicit.gradient(part, parameters, sensitivity?)` returns Σ s·V_n·(n_φ·n)·A. With s = 1 the result is dV/dp.
   - The parameter may be anything an expression names, including a project parameter that reaches the part through expressions.
   - Integer quantities are refused with `implicit.not_differentiable`.
   - The numerics are pure numpy in `stokes.implicit.sensitivity`.
   - Nothing is gated (ADR-0035 decision 28: flags stay ungated until A4).
7. **`expression.evaluate`** is a read-only command. It evaluates text with optional local names, so the sketcher previews bound coordinates without evaluating anything in the front end.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Vectors as three scalar parameters (`position_x`) | Clutters forms, journals and schemas; the dotted components give the same machinery with one field per concept. |
| Entity coordinates referring to project parameters directly | The dependency graph would need a coarse key per json parameter; going through dimensions keeps it exact. |
| Storing measures on every part | Every saved part grows by eight quantities, and an operation expression reading a downstream measure could loop. |

## Consequences

- The front end draws a vector as one row of three quantity inputs, each taking an expression, and a map as a dimensions table. The sketcher has a Dimensions panel, and its cells take `W / 2`.
- Tests: `tests/test_implicit_parametric.py`; `web/build_sketch_gui.mjs` steps 6 and 7.
- Still open (`docs/00-open-questions.md`):
  - G21: does the geometry engine run points locally or through the agent? Assumed local.
  - G22: does `vector` roll out to the B-rep operations? Assumed in M1.
  - G23: should samplers round integer variables? Assumed strict refusal.
  - G24: on-canvas dimensions and a constraint solver. Assumed deferred.
