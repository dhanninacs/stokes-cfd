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
| `fast.check` | — | `{available, adapter: {name, backend, kind, max_binding}}` or `{available: false, error}`; asked once per process; no project needed |
| `fast.start` | `fidelity` (1 to 5, default 2), `axis` (0, 1, 2, or -1 for the automatic plane: the fluid box's shortest side, the default), `offset` (a length such as `"2 [mm]"`, or a number of metres; default the middle), `turbulence` (`auto`, `smagorinsky`, `shear_improved`) | `{started, fluid, ports, zones, thermal}`; one run per session, a second start restarts |
| `fast.configure` | `fidelity`, `axis`, `offset`, `turbulence` | the status; `fast.not_running` when none runs |
| `fast.status` | — | `{state, fidelity, frame, adapter, density, turbulence, automatic_plane, grid, resolution, tau, dt, les, closure, cell_reynolds, zones, thermal, error}`; `{state: "off"}` with none |
| `fast.stop` | — | `{stopped}` |

`state` is `starting`, `running`, `stopped` or `failed` (with `error: {code, message}`). `fidelity` is
the one asked for and `grid.fidelity` the one the grid has (the same, or higher when the flow is too
fast for the asked-for cells); `grid` is `{nx, ny, nz, cells, fluid_cells, origin, dx, fidelity,
coarsened}`; `density` is the fluid's (kg/m³). `resolution` has `{name, cells, cells_across}` per fluid: the cells across its passages (its hydraulic diameter in cells; a single throat is narrower); below 10 a client should say the preview is under-resolved. None of the commands changes the model, so none is an
undo step.

## Events

| Event | Payload | When |
|---|---|---|
| `fast.frame` | `{step, mlups, dp, dp_pa, inlet_speed, flow_in, flow_out, imbalance, guard, unstable, steady, holding, catching_up, restrictive, geometry_version, axis, plane, vertices, streams, heat_balance}` | at most 10 a second (4 while holding) |
| `fast.warning` | `{code, message}` | an edit that no longer reads; the run stays on the last good case |
| `fast.stopped` | the status | the run ended (stop, failure, project closed or another opened) |

`dp` is the mean inlet pressure minus the mean outlet pressure (kinematic, m²/s²; `dp_pa` times the
density), each the mean over the fluid cells next to the port. `flow_in` and `flow_out` are the volume
flows through the inlets and the outlets (m³/s, summed over the layer of cells next to each port),
`imbalance` is `(flow_out - flow_in) / flow_in`, and `inlet_speed` is `flow_in` over the inlets' area.
`steady` is true once the pressure drop and `flow_out` have each stayed within 1 % over a window of at
least two seconds and half a flow-through in lattice steps. `streams` has one entry per fluid zone, as the Results stage's Streams table: `{name, flow, flow_out, mass_flow, inlets, outlets, dp, t_in, t_out, heat}` (m³/s, kg/s, Pa, K, W; `dp` only with an inlet and an outlet, the last three only with the temperature solved; `t_out` is the outflow's mixing-cup mean and `heat` what the stream gained). `heat_balance` is the sum of the heats, or null. `catching_up` says the flow is steady and the temperature is taking sixteen steps to each of the lattice's. `restrictive` says the lattice speed was lowered because the guard held the flow. `holding` says a steady run is stepping slowly (one batch, then a quarter of a second's wait) until a request or an unsteady frame. `guard` counts the cells the stability guard held in the last tenth of a second, `unstable` says
the lattice went NaN and was restarted, `restrictive` that it runs at the slower lattice speed.

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

- **Fidelity** sets the cells across the fluid box's smallest side (24, 32, 48, 64, 96), within 8 M
  cells and the adapter's per-buffer limit (the grid is coarsened and `grid.coarsened` says so).
- **The lattice** is D3Q19 with the incompressible equilibrium; two relaxation times while laminar
  (walls exact whatever the viscosity), the regularised collision with the Smagorinsky model as a
  turbulent preview. Below a cell Reynolds number of 0.3 the lattice speed is lowered so the relaxation
  time stays at 1.
- **Stability** follows the cell Reynolds number `U dx / nu`: laminar up to 8 (exact walls); above it, or
  when a laminar lattice needed the guard for ten frames in a row (the flow ran faster somewhere than
  the inlet), the Smagorinsky LES (`les: true`; it reads a laminar duct's friction 5 to 7 % high); above 200, the fidelity is raised on its own, and
  `fast.too_fast_for_fidelity` only when even fidelity 5 is too coarse.
- **Turbulence**: `auto` takes the shear-improved closure up to a cell Reynolds number of 30 (a laminar
  shear gets no eddy viscosity) and plain Smagorinsky above (the smoother at coarse cells).
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
| `fast.invalid` | a fidelity, an axis, an offset or a closure that cannot be read; no fluid region; no inlet with a speed; a fluid with an inlet and no outlet (detail `fast.no_outlet`); more than 16 ports |
| `fast.not_implicit` | the fluid is not a part made with Create |
| `fast.unsupported_boundary` | a boundary kind slice 1 does not model (slip wall, symmetry, mass-flow inlet, ...) |
| `fast.port_not_planar` | an inlet or outlet that is not one plane |
| `fast.too_fast_for_fidelity` | the flow is too fast even at fidelity 5 |
| `fast.not_running` | `fast.configure` with no run |

## In the app

The **Fast** button of the top bar (lit while a preview runs; also palette "Open Fast mode") opens the
**Fast mode** panel, which starts and stops the preview, sets the fidelity, the plane (automatic or
normal to an axis, moved along it with a slider) and the value shown, and reads the frames: model
(laminar or LES), fluid cells, cell updates per second, the pressure drop, the flow in and out, steady
or settling, a legend of the plane's colours (pressure in Pa, from the kinematic value and the density)
and the warnings. The viewport's `FastLayer` (`web/src/viewport/fastLayer.ts`) draws the plane in the
results' colour map, in every stage, and draws the bodies see-through while it shows.
`web/walk_fast.mjs` walks all of it in a browser.
