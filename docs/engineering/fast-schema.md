# Fast mode: the contract (ADR-0047)

What is built (slice 1) and what clients can rely on. Decisions and their measurements are in
`docs/adr/ADR-0047-fast-mode-gpu-preview.md`.

Fast mode solves the open project's case on the desktop host's GPU (`stokes.fast`, through `wgpu`) on a
lattice of cubic cells, straight from the fluid's implicit field: no mesh. It follows model edits while
it runs. It is a **preview**: nothing it computes is a run, and the engines are what a result rests on.

## What it reads

| From the model | How |
|---|---|
| The bodies | Every body the regions hold, each a zone (`stokes.mesh.conjugate.region_bodies`: fluids first, a body left out of the simulation skipped). Each must be made with Create (`family: implicit`); its field is `body_of(implicit_recipe(...)).field()`, written with `to_text`. Fluids never mix; solids are walls to the flow and conduct heat. |
| The temperature | Solved when the case has more than one body or the solver settings have `energy` on. Specific heat and conductivity are the regions' materials' at their reference temperatures; an inlet's `temperature`; a wall's `thermal` (`adiabatic`, `temperature`, `heat_flux`): a thermal wall where its faces are one plane, else the condition of the rest of its body's outside. |
| Viscosity, density | The region's material at its reference temperature, through `material.evaluate`, as the solve does. |
| Inlets | `velocity_inlet` boundaries on the fluid: `normal` (against `tag.anchor`'s outward normal), `vector` or `components`, as the solve reads them. |
| Outlets | `pressure_outlet` boundaries: the gauge pressure, divided by the density (kinematic). |
| Walls | `wall` with `no_slip`. |
| Anything else | Refused: `fast.unsupported_boundary` names the boundary and its kind. |

Each inlet and outlet must be flat: its tag's triangles (selected as `tag.anchor` selects them) within 2
degrees of one normal and 2 % of the port's size off one plane (area-weighted). A port may face any
direction anywhere; boundary links whose wall crossing lies on its footprint take its condition.

## Commands

| Command | Parameters | Result |
|---|---|---|
| `fast.check` | — | `{available, adapter: {name, backend, kind, max_binding, f16}}` or `{available: false, error}`; asked once per process; no project needed |
| `fast.start` | `fidelity` (1 to 5, default 2), `axis` (0, 1, 2, or -1 for the automatic plane: the fluid box's shortest side, the default), `offset` (a length such as `"2 [mm]"`, or a number of metres; default the middle), `turbulence` (`auto`, `smagorinsky`, `shear_improved`, `wale`, `vreman`), `warm_start` (default true), `storage` (`f32`, the default, or `f16`) | `{started, fluid, ports, zones, thermal}`; one run per session, a second start restarts |
| `fast.configure` | `fidelity`, `axis`, `offset`, `turbulence` | the status; `fast.not_running` when none runs |
| `fast.status` | — | `{state, fidelity, warm_start, warming, warmed_from, storage, frame, adapter, density, turbulence, automatic_plane, grid, resolution, tau, dt, les, closure, cell_reynolds, zones, thermal, error}`; `{state: "off"}` with none |
| `fast.stop` | — | `{stopped}` |

`state` is `starting`, `running`, `stopped` or `failed` (with `error: {code, message}`). `fidelity` is
the one asked for and `grid.fidelity` the one the grid has (the same, or higher when the flow is too
fast for the asked-for cells); `grid` is `{nx, ny, nz, cells, fluid_cells, origin, dx, fidelity,
coarsened}`; `density` is the fluid's (kg/m³). `resolution` has `{name, cells, cells_across}` per fluid: the cells across its passages (its hydraulic diameter in cells; a single throat is narrower); below 10 a client should say the preview is under-resolved. None of the commands changes the model, so none is an
undo step.

## Events

| Event | Payload | When |
|---|---|---|
| `fast.frame` | `{step, mlups, dp, dp_pa, inlet_speed, flow_in, flow_out, imbalance, guard, unstable, instability, steady, holding, catching_up, warming, warmed_from, fidelity, fluid_cells, restrictive, geometry_version, axis, plane, vertices, streams, heat_balance}` | at most 10 a second; fewer on a large lattice (sampling a frame takes at most a fifth of the worker's time: about 2 a second at 11 million cells); 4 while holding |
| `fast.warning` | `{code, message}` | an edit that no longer reads; the run stays on the last good case |
| `fast.stopped` | the status | the run ended (stop, failure, project closed or another opened) |

`dp` is the mean inlet pressure minus the mean outlet pressure (kinematic, m²/s²; `dp_pa` times the
density), each the mean over the fluid cells next to the port. `flow_in` and `flow_out` are the volume
flows through the inlets and the outlets (m³/s, summed over the layer of cells next to each port),
`imbalance` is `(flow_out - flow_in) / flow_in`, and `inlet_speed` is `flow_in` over the inlets' area.
`steady` is true once the pressure drop and `flow_out` have each stayed within 1 % over a window of at
least two seconds and half a flow-through in lattice steps. `streams` has one entry per fluid zone, as the Results stage's Streams table: `{name, flow, flow_out, mass_flow, inlets, outlets, dp, t_in, t_out, heat}` (m³/s, kg/s, Pa, K, W; `dp` only with an inlet and an outlet, the last three only with the temperature solved; `t_out` is the outflow's mixing-cup mean and `heat` what the stream gained). `heat_balance` is the sum of the heats, or null. `catching_up` says the flow is steady and the temperature is taking sixteen steps to each of the lattice's. `restrictive` says the lattice speed was lowered because the guard held the flow. `holding` says a steady run is stepping slowly (one batch, then a quarter of a second's wait) until a request or an unsteady frame. `guard` counts the cells the stability guard held in the last tenth of a second. `unstable` says the
frame is **not valid** (ADR-0047 addendum 5): the lattice went NaN, or, once the inlets have ramped and
the flow had three seconds to settle, it shows a sign of a runaway; `instability` names the sign
(`not_finite`, `density` for a port's lattice density more than 0.5 off 1, `guard` for the guard holding
more than 1 % of the cell-steps, `imbalance` for outflow and inflow more than 25 % apart while the guard
holds cells or without dying away, so a core filling with pressure is not one; in a creeping flow, cell
Reynolds number below 1, only both; "" otherwise).
An unstable frame is never `steady` and never part of a steady window. `restrictive` says the lattice runs
at the slower lattice speed.

## Routes

| Route | Payload |
|---|---|
| `GET /api/fast/slice?field=` | the mesh the value is drawn on, in transport version 2 (one `mesh` group, face ids 0, metres): the fluid cells' for the flow's values, every body's cells for `T` |
| `GET /api/fast/field?field=` | one value of the plane in the field codec: `U`, `Ux`, `Uy`, `Uz` (m/s), `p` (m²/s², kinematic) or `T` (K; 204 on a case that does not solve it) |

Both carry `X-Fast-Key: <geometry version>:<axis>:<plane>:<mesh>` (`f` the flow's mesh, `t` the temperature's); a mesh held under another key is fetched
again. **204 (no content) when no preview runs or the plane holds no fluid yet**; 400 for an unknown
field. The session token is checked as on every route. The plane's vertices are the fluid cells'
centres and one ring of wall vertices round them (at rest, half a cell towards the fluid from the solid
cells next to it), so the colours reach the walls.

## How a run behaves

- **Fidelity** sets the cells across the fluid box's smallest side (24, 32, 48, 64, 96), within what one
  buffer of the adapter holds (`adapter.max_binding` over 76 bytes a cell, or 38 in 16-bit storage: 28
  and 56 million cells at 2 GiB; never above 64 million). A larger grid is coarsened and
  `grid.coarsened` says so. A cell takes 108 bytes on the GPU (70 in 16-bit storage).
- **Storage** (`storage`, at `fast.start` only): the populations are one buffer, read and written in
  place (`stokes.fast.inplace`). `f16` keeps them in half precision scaled by 2^15 and computes in 32
  bits; it needs the adapter's `shader-f16` feature (`adapter.f16`), and a run on a GPU without it stays
  in `f32` and says so (`fast.warning`, code `fast.no_f16`). The status has the storage in use. A 16-bit
  store rounds up or down at random, in proportion to where the value lies between its two 16-bit
  neighbours, with a draw of its own at every place on every step (rounded to nearest, a steady duct
  read 2.5 % low); a duct, a pipe, a turned duct and a ball read within 0.02 % of the 32-bit lattice.
  **When 16 bits pay**: for the memory (the cap doubles, a cell takes 70 bytes against 108) and on
  grids of about half a million cells and up, where the lattice is bound by memory traffic (a duct of
  3.6 million cells steps a third faster). On a smaller grid, or with the regularised collision on a
  quarter of a million cells, the rounding's arithmetic makes it slower than 32 bits.
- **The lattice** is D3Q19 with the incompressible equilibrium; two relaxation times while laminar
  (walls exact whatever the viscosity), the regularised collision with the Smagorinsky model as a
  turbulent preview. Below a cell Reynolds number of 0.3 the lattice speed is lowered so the relaxation
  time stays at 1.
- **Stability** follows the cell Reynolds number `U dx / nu`: laminar up to 8 (exact walls); above it, or
  when a laminar lattice needed the guard for ten frames in a row (the flow ran faster somewhere than
  the inlet), the Smagorinsky LES (`les: true`; it reads a laminar duct's friction 5 to 7 % high); above 200, the fidelity is raised on its own, and
  `fast.too_fast_for_fidelity` only when even fidelity 5 is too coarse.
- **Turbulence**: `auto` takes the shear-improved closure up to a cell Reynolds number of 30 (a laminar
  shear gets no eddy viscosity) and plain Smagorinsky above (the smoother at coarse cells). `wale` and
  `vreman` (2026-11-23) take the velocity gradient from the neighbours' velocities and give a laminar
  shear none either, with no running mean: a laminar duct or pipe run as a turbulent preview reads its
  friction within 0.4 % with them (Smagorinsky +5.5 %); above a cell Reynolds number of 30 their flow
  along a duct strays 3 to 7 % at any instant (Smagorinsky 0 to 1.3 %, shear-improved 7 to 9 %), which
  is why `auto` does not take them; they cost 8 to 12 % of the speed.
- **The warm start** (`warm_start`, on unless `fast.start` says false; 2026-11-23): a lattice built while
  another one runs (a fidelity raised or lowered) starts from that one's velocity, pressure and
  temperature, interpolated to its cells, and its inlets do not ramp again. A run asked for at fidelity 4
  or 5 first runs at fidelity 2 until its flow is steady (or eight steady windows have passed), then
  starts the asked-for lattice from it: `warming` is true in the status and the frames meanwhile, `steady`
  is never true while it is, `fidelity` stays the one asked for and `grid.fidelity` the one running. A
  fidelity asked for by hand ends the coarse pass. `warmed_from` (status and frames) is the fidelity of
  the lattice the running one took its flow from, until it is steady or for ten seconds, whichever is sooner; null from rest, and after any restart from rest (a NaN, the hand-over to the LES, a lowered lattice speed). A frame's `fidelity` and `fluid_cells` are those of the grid it was sampled on (`fluid_cells` 0 until they are counted), so a client need not wait for a status to head its lines. It does not help
  where the coarse lattice cannot carry the flow (passages a few cells across) or where the lattice
  speed is then lowered, which starts the lattice from rest again.
- **A seam inside a body is fluid** (2026-11-24, ADR-0047 addendum 5): a union of parts that share a
  face exactly has a field of zero on that face; a cell whose field is within a thousandth of a cell of
  zero is looked at a hundredth of a cell to either side (along each axis and one oblique direction),
  and is inside when both sides are. A true wall through a cell centre is unchanged.
- **A fluid in pieces is refused** (2026-11-24): after every classification each fluid's inlets must
  reach an outlet, and its outlets an inlet, through its own cells (joined by faces and edges, as the
  lattice's links join them); otherwise `fast.invalid` with detail `fast.disconnected`, naming the
  fluid, its pieces, the ports cut off and the fidelity. A start fails; an edit is refused with a
  `fast.warning` and the run stays on the last case; the coarse pass of a warm start that closes a
  throat is skipped and the asked-for fidelity judged instead.
- **A runaway is not reported** (2026-11-24): when at least half of the last 20 judged frames are not
  valid (a NaN at once), the lattice restarts from rest at half the lattice speed and says so
  (`fast.warning`, code `fast.unstable`); after two such restarts, or at the slowest lattice speed, the
  run fails with `fast.unstable` (details: `reason`, `fidelity`, `cell_reynolds`, `tau`,
  `lattice_speed`, `restarts`); at once when halving the speed would not lower the lattice speed (a
  viscous flow's is already held lower). A warm start's coarse pass that runs away is skipped instead. Halving the lattice speed lowers the density swing fourfold but moves tau
  towards 0.5, so it cures a pressure out of range, not a cell Reynolds number too high for the cells.
- **Holding**: a steady run steps slowly so the GPU is the viewport's; any request resumes it.
- **A restrictive core** (throats that run many times the inlet's speed) halves the lattice speed each
  time the guard holds cells, down to 0.004, and takes that much longer to settle. The density is the
  pressure alone and is not capped. Interpolated walls keep each cell's mass.
- **Under-resolved passages** (`resolution` below 10 cells across, or a cell Reynolds number far above 8
  in a throat): the pressure drop of a duct at Re 225 reads +15 to +30 % at 16 cells across and +50 to
  +250 % at 8 and fewer. A gyroid plate exchanger with 1.2 mm throats reads about twice the solved
  pressure drop and three times its duty at fidelity 4; a coarse gyroid core without a throat, +16 % duty.
- **The temperature's accuracy** (2026-11-22): exact for conduction and for passages square to the
  lattice once resolved (a duct's Nu -0.2 %, a counter-flow exchanger +4 % against effectiveness-NTU);
  +7 to +20 % at 16 to 8 cells across; +40 to +60 % in passages at 30 degrees to the lattice at that
  resolution. A fine lattice core's duty is not to be read from the preview (the ADR's second addendum).
- **Edits**: a node event or `geometry.op_done` re-reads the case. The same field and ports keep the
  grid and the flow (a speed or a pressure rewrites the port table only); a new field re-classifies
  the cells in place (new fluid cells start at rest); a new box or fidelity starts again.
- **Inlets ramp** from rest over 2,000 steps after a start, so the start sends no pressure wave. An
  inlet's speed falls to zero at the walls over one cell and is raised elsewhere so that the inlet
  delivers its area times its speed (within 1 % on the ducts and the pipe measured, at any angle).
- **Accuracy measured** (2026-11-22, fidelity 2): square duct fRe -0.05 %, pipe -0.5 %, ducts at 17 to 45
  degrees to the lattice +0.3 to +0.9 %, a slow viscous flow (Re 0.5) -0.07 %.

## Error codes

| Code | When |
|---|---|
| `fast.unavailable` | no `wgpu` package, no adapter, only a software adapter, or `STOKES_FAST_DISABLE` set |
| `fast.invalid` | a fidelity, an axis, an offset, a closure or a storage that cannot be read; no fluid region; no inlet with a speed; a fluid with an inlet and no outlet (detail `fast.no_outlet`); a fluid whose inlets and outlets the lattice's cells do not join (details `reason: "fast.disconnected"`, `fluid`, `pieces`, `ports`, `fidelity`); more than 16 ports |
| `fast.not_implicit` | the fluid is not a part made with Create |
| `fast.unsupported_boundary` | a boundary kind slice 1 does not model (slip wall, symmetry, mass-flow inlet, ...) |
| `fast.port_not_planar` | an inlet or outlet that is not one plane |
| `fast.too_fast_for_fidelity` | the flow is too fast even at fidelity 5 |
| `fast.not_running` | `fast.configure` with no run |
| `fast.unstable` | the run's state when its lattice ran away and two restarts at a lower lattice speed did not bring it back; also a `fast.warning` code at each restart |

## In the app

The **Fast** button of the top bar (lit while a preview runs; also palette "Open Fast mode") opens the
**Fast mode** panel, which starts and stops the preview, sets the fidelity, the plane (automatic or
normal to an axis, moved along it with a slider), the value shown, the turbulence closure, and before
a start the storage (32-bit or 16-bit) and the warm start (a tick box, on), and reads the frames (Start,
Stop and these lines stay in view under the options, which scroll when the window is short): model
(laminar or LES), fluid cells, cell updates per second, the pressure drop, the flow in and out, steady
or settling, a legend of the plane's colours (pressure in Pa, from the kinematic value and the density)
and the warnings. The viewport's `FastLayer` (`web/src/viewport/fastLayer.ts`) draws the plane in the
results' colour map, in every stage, and draws the bodies see-through while it shows.
`web/walk_fast.mjs` walks all of it in a browser.
