# ADR-0028 · Turbulence: the kOmegaSST model for the energy engine

**Status:** Accepted · 2026-10-22 · relies on ADR-0027 (the `of14.fluid` writer and its energy case), ADR-0022 (boundary conditions and their turbulence group)

## Context

The energy elbow (ADR-0027 step 6) runs laminar at a Reynolds number near 8e4. The laminar steady
solution does not settle, so the residuals stop near 0.04 and the case never reaches the 1e-4
target. A turbulence model is the physical fix. The model is already in the product: the
settings node offers `kOmegaSST`, and each inlet and outlet carries an intensity with a viscosity
ratio or a length scale (`intensity_ratio` by default, 5 % and 10 on the elbow). What is missing
is the writer, so `solve.check` refuses the choice with `solve.needs_turbulence`.

OpenFOAM 14's own `roomHeating` tutorial runs the `fluid` solver with `kOmegaSST` and energy
on, so the dictionaries for this combination are measured in the install, not invented. The
flow-only `incompressibleFluid` writer has no energy field to carry, and its kOmegaSST case is a
separate piece of work.

## Decision

1. **kOmegaSST is written for `of14.fluid` only.** The energy engine is the one this writer
   claims. `of14.incompressibleFluid` with `kOmegaSST` stays refused (`solve.needs_turbulence`),
   and the catalogue entry `of14.kOmegaSST` stays unbuilt. A flow-only kOmegaSST writer is a later
   task with its own ADR.
2. **The model:** `constant/momentumTransport` is `simulationType RAS;` with `RAS { model
   kOmegaSST; turbulence on; }`, as `roomHeating` writes it. The energy transport is the solver's
   default (eddy diffusivity from `alphat`), so no `constant/thermophysicalTransport` is written.
3. **Fields, all new under the kOmegaSST case:** `0/k`, `0/omega`, `0/nut`, `0/alphat`.
   - Walls (`wall`, `unassigned`): `kqRWallFunction`, `omegaWallFunction` with `blended true`,
     `nutkWallFunction`, and `compressible::alphatJayatillekeWallFunction` with `Prt 0.85`.
   - Velocity inlet: `k` and `omega` `fixedValue`; `nut` and `alphat` `calculated` (computed from
     the others).
   - Pressure outlet: `k`, `omega` and `alphat` `zeroGradient`; `nut` `calculated`.
   - Symmetry patches: the field's own symmetry type (`symmetryPlane` or `symmetry`) on every
     field. A slip wall takes `zeroGradient` on `k`, `omega` and `alphat`, and `calculated` on
     `nut`.
4. **Inlet turbulence from the boundary's own group.** With `I` the intensity, `|U|` the inlet
   speed and `nu = mu/rho`:
   - `k = 1.5 (I |U|)^2`.
   - `intensity_ratio` with viscosity ratio `R`: `omega = k / (R nu)`, so `nut = R nu`.
   - `intensity_length` with length scale `L`: `omega = sqrt(k) / (C_mu^0.25 L)`, `C_mu = 0.09`.
   The internal field of `k` and `omega` is the mean of the inlets' values. An inlet without its
   group is `compile.invalid`, not a silent default.
5. **Schemes and solvers:** `grad(k)` and `grad(omega)` are `cellLimited Gauss linear 1`;
   `div(phi,k)` and `div(phi,omega)` are `bounded Gauss limitedLinear 1`; `wallDist` is
   `meshWave`. `fvSolution` solves `k` and `omega` in the `(U|e|k|omega)` entry, with their
   relaxation at 0.7. Convergence is still tested on `p_rgh`, `U` and `e` only.
6. **The writer version** becomes `of14-fluid/4`, so cached energy runs are marked stale. The
   laminar energy case's files are unchanged except for the version string; the run basis of a
   flow-only project is unchanged, because the turbulence patch fields are dropped from the basis
   when they are not set.

## Alternatives considered

| Alternative | Why not |
|---|---|
| kOmegaSST in both writers now | The energy case is what needs it; the flow-only writer needs its own dictionaries and goldens. Doing both at once doubles the review. |
| A transient run instead | Changes the question (time-accurate versus steady), needs a time step and a longer run, and does not fix the modelling of a turbulent flow. |
| `inletOutlet` for `k` and `omega` at the outlet | More faithful for backflow, but the outlet has no speed to build a backflow `k` from (its velocity is zero). Deferred; `zeroGradient` is what the tutorials use, and the limitation is recorded here. |
| Default inlet turbulence when the group is missing | A silent guess about a physical input. The model already fills the group with defaults, so a missing group is a bug worth refusing. |

## Consequences

- The energy elbow can run a turbulent model, and the residual question becomes one about the
  turbulence model's convergence rather than the laminar flow's non-existence.
- The writer gains `0/k`, `0/omega`, `0/nut`, `0/alphat` and two constant files change. New
  goldens (`tests/golden/compile/of14/elbow_energy_kOmegaSST/`) are reviewed like code.
- Backflow turbulence at an outlet is not applied; a reversed-flow outlet with a strong
  inflow-turbulence setting will not match a backflow model. Recorded as a limitation.
- Flow-only kOmegaSST stays refused until its own ADR and writer land.
- Convergence to 1e-4 is not promised by this ADR. The run decides, and its result is reported
  as measured.

## Measured (native, 2026-10-23)

The elbow ran 300 iterations of `of14.fluid` with energy on and kOmegaSST, on three meshes. Each
row is the initial residual at the last iteration. The state is `done_unconverged` in all three.

| Mesh | Cells | p_rgh | Uz | k | omega | Outlet T (balance) |
|---|---|---|---|---|---|---|
| No wall layers | 88,280 | 3.2e-4 | 1.1e-4 | 1.6e-3 | 7.9e-4 | 294.92 K (294.86 K) |
| 3 wall layers | 142,087 | 3.9e-4 | 1.4e-4 | 1.4e-3 | 3.5e-4 | 294.93 K (294.91 K) |
| 5 wall layers | 170,898 | 5.0e-4 | 1.9e-4 | 1.5e-3 | 3.8e-4 | 294.95 K (294.91 K) |

- The wall layers did not bring the residuals to 1e-4. Layers on every wall (final-ratio
  defaults) lowered `omega` but raised `p_rgh` and `Uz`, and 5 layers were no better than 3.
- The layer counts were not tuned further. The y+ of the first layer was not checked.
- The 5-layer mesh reports 5,865 prism cells, the same as the 3-layer mesh. That has not been
  explained.
- The energy result holds on every mesh: both inlets stay within their temperatures, and the outlet
  matches the mixing balance within the 0.5 K tolerance.
- Convergence to 1e-4 at 300 iterations is therefore not met by this writer on this mesh. Whether
  more iterations close the gap is not measured.

## Amendment 2026-11-04: the writer is generalised

The turbulence writer (`stokes/compile/of14/turbulence.py`) now serves every model the engine
catalogue lists, not only kOmegaSST. What changed from the text above, and is kept: transported
fields `k` and `omega` get their own linear solver entry `"(k|omega)"` (not folded into
`"(U|e|k|omega)"`) with relaxation factors left to the defaults; `0/nut` and `0/alphat` start from
the inflow estimate instead of 0, and the `alphat` outlet is `calculated`; the writer version is
`of14-fluid/5`. kOmegaSST keeps `limitedLinear 1` and `turbulence on;`; the other models start
first order. A mass-flow inlet is refused with a turbulence model (no inlet speed), and a model
needs at least one velocity inlet. These were reviewed against the goldens, not yet run against
the OpenFOAM tutorials for every model.
