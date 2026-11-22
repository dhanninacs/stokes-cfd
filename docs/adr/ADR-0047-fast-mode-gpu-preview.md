# ADR-0047 · Fast mode: a GPU lattice-Boltzmann preview that follows the implicit geometry live

**Status:** proposed · 2026-11-21 · addenda 2026-11-22 (below: the first changes decisions 4, 5 and 8; the second adds zones, the temperature and decisions 10 to 14; the third adds 15 to 19 and says what limits a fine core) · (ADR-0013 front end, ADR-0023 runs, ADR-0035 implicit geometry, ADR-0044 coupled engine, decision B5)

## Context

The owner asked for a fast, GPU-based mode in the spirit of Ansys Discovery's Explore mode: results
within seconds of a change, while the geometry is being edited, with the full OpenFOAM path kept for
the accurate answer (Discovery's "Refine"). Discovery's Explore discretises the geometry on a
Cartesian (voxel) grid on an NVIDIA GPU, so it needs no mesh; its resolution follows a fidelity slider
bounded by GPU memory, thin walls must span cells, and its accuracy is a preview's.

A prototype (2026-11-21, `Desktop/stokes/livegpu`, outside the repository) measured the idea on this
machine (i9-13900, RTX 4000 Ada 20 GB):

- **Speed.** A D3Q19 lattice-Boltzmann kernel in WGSL ran at 2,200 million lattice updates per second
  from 1.8 M to 14 M cells, about 93 % of the card's memory bandwidth.
- **Accuracy.** On the public DFG 3D-1Z benchmark (Schaefer and Turek 1996; reference delta p
  0.171007 Pa, Braack and Richter 2006) the pressure difference across the cylinder converged to the
  reference as the grid was refined: -2.0 % at 16 cells per diameter (34 s), -1.3 % at 20 (65 s),
  -0.8 % at 24 (132 s). Staircase walls scattered around it (-3.1, -1.1, +0.45 %). FoamWin on a 390k
  cell snappyHexMesh of the same case gave -3.35 % in 71 s with meshing.
- **Live geometry.** The implicit modeller's field text (ADR-0035, `to_text`) compiles line for line
  to a WGSL function. Re-classifying 1.4 to 2.7 M cells after an edit took 4 to 21 ms; recompiling the
  shader about 0.3 s.
- **Limits met.** A dense core (a pin bank at three quarters blockage, a TPMS sheet) pushes the lattice
  past its speed limit at the inlet speed that suits an open channel; a lower lattice speed and a guard
  were needed.

ADR-0013 says the front end never computes physics, and rule 4 of CLAUDE.md that behaviour is an API
command first. The owner chose (2026-11-21) to run the solver in the Stokes server, not the browser.

## Decision

1. **Fast mode is a preview solver in the Stokes server process, on the desktop host's GPU,** through
   `wgpu` (wgpu-py, BSD-2-Clause, wrapping wgpu-native, MIT/Apache-2.0) and WGSL compute shaders. It
   runs on Vulkan, Direct3D 12 or Metal: any vendor's GPU, not NVIDIA only (decision B5's "NVIDIA only"
   concerned the CUDA build of coupledSimpleFoam and is unchanged). `wgpu` is the optional extra `fast`
   and is in `dev`; without it, or without a GPU adapter, `fast.*` commands refuse with `fast.unavailable`
   and the client hides the mode. A software adapter (CPU fallback) is refused: it would be slower than
   the real solve.
2. **It is a set of API commands, not a widget** (rule 4): `fast.start`, `fast.stop`, `fast.status`,
   `fast.configure`. A GUI, the CLI, agents and the MCP server all drive the same session. One Fast run
   per session. Its output is events and a slice route; it is **not a run record** (ADR-0023) in slice 1
   and never claims to be a solution of the case: every number it shows says "preview".
3. **Geometry comes from the implicit fields.** The fluid region's bodies must be implicit parts in
   slice 1 (`fast.not_implicit` names the others): the fluid field is the union (`min`) of their fields
   (`body_of(implicit_recipe(...)).field()`, world millimetres), written with `to_text` and compiled to a
   WGSL function by `stokes.fast.wgsl` with the same closed operation set (ADR-0035 decision 4). A cell is
   fluid where the field is negative at its centre. A wall link uses interpolated bounce-back (Bouzidi)
   with the wall at the field's zero along the link, so walls are not staircases.
4. **Boundaries come from the case set-up, unchanged.** The fluid region's boundary nodes (setup-schema)
   are read as the solver reads them: `velocity_inlet` (normal or vector, a quantity), `pressure_outlet`
   (gauge pressure), `wall` (no slip; slip in slice 2). Every other kind is refused with
   `fast.unsupported_boundary` naming it. **Ports are found geometrically**: each inlet and outlet tag's
   triangles must be planar (normals within 2 degrees; `fast.port_not_planar` otherwise); the port is its
   plane plus its footprint rasterised into a 2D mask. A boundary link whose wall crossing lies on a
   port's footprint takes that port's condition: velocity bounce-back for an inlet, anti-bounce-back at
   the outlet pressure for an outlet. Ports may therefore face any direction anywhere, as in the model.
   The material's kinematic viscosity at its reference temperature is the fluid's.
5. **The lattice.** D3Q19, two-relaxation-time collision, populations stored as f - w in f32 in two
   buffers (pull streaming), one kernel per step. The lattice speed of the fastest inlet is 0.05 and
   is lowered to 0.02 when the ports' density swings by more than 10 % once the start-up transient has
   passed (a restrictive core). **Stability is set by the cell Reynolds number** U dx / nu, which the
   lattice speed does not change: measured on a square duct (2026-11-21), stable and within 1.5 % of
   fRe at 3.1, stable at 6.3, unstable at 15.6. Up to 8 the lattice runs as it is (magic parameter
   3/16, exact half-way walls); above 8 a Smagorinsky subgrid viscosity (Cs 0.17, Hou et al. 1996)
   keeps it stable as a turbulent preview (magic 1/4), and the status says `les: true`. **A laminar
   lattice that needs the guard for ten frames in a row hands over to the LES by itself**: in the app,
   a pin bank whose inlet said laminar (6.3) blew up between the pins until it did. Above 200
   the fidelity is raised on its own, and `fast.too_fast_for_fidelity` refuses only when even
   fidelity 5 is too coarse. **Inlets are an equilibrium layer** (the fluid cells next to an inlet hold
   the inlet's velocity at the local density): bounce-back with a wall velocity gave the first layer
   twice the inlet speed near tau's limit. Inlets ramp from rest over 2,000 steps after a start. A
   guard caps the local speed at 0.3 and the density to [0.5, 2] and counts when it acts; a NaN resets
   the populations and the frame says `unstable`.
6. **Fidelity.** One setting, 1 to 5 as in Discovery, sets the cells across the fluid box's smallest
   extent (24, 32, 48, 64, 96) within a cap of 8 M cells and the adapter's per-buffer limit (the grid
   is coarsened and says so); the grid is the fluid field's bounding box plus two cells each side.
   A warning for features thinner than two cells is left to slice 2.
7. **Live.** A worker thread owns the GPU device and steps the lattice continuously; commands and model
   edits only post requests to it. When the model changes (an operation re-run, a parameter, a boundary
   value), the session's change event makes Fast mode rebuild the field text and the boundary tables
   and **re-classify the grid in place, keeping the flow**: new fluid cells start at rest, cells that
   became solid are dropped. A value-only edit (a speed, a pressure) rewrites the port table without
   recompiling. A fidelity change rebuilds the grid. **An edit never ends the run**: a case that no
   longer reads, or no longer fits the lattice, leaves it on the last good case with a `fast.warning`. The worker is a documented background
   thread (CLAUDE.md section 8): it is the only owner of the device, it stops on `fast.stop`, on project
   close and on session end, and it never touches the model.
8. **Output.** At most 10 Hz (`fast.frame`): step count, lattice updates per second, the mean inlet
   speed and the pressure drop between inlets and outlets (area means over the ports, SI), the guard's
   activity and the cell counts. The display is a plane through the fluid (default: the mid-plane
   normal to the fluid box's shortest side; any plane through `fast.configure`), served as
   `GET /api/fast/slice?field=` in transport version 2 with the field codec, so the viewport draws it
   with the same colour maps as a results slice, and draws the bodies see-through while it shows (the
   plane lies inside them, as a results slice fades the walls). Velocity magnitude, pressure (SI,
   kinematic like an incompressible run) and the velocity components are offered. The contract is
   `docs/engineering/fast-schema.md`.
9. **Validation is a test.** `tests/test_fast_gpu.py` (marker `gpu`, skipped without a hardware
   adapter, `tests/conftest.py`): fully developed laminar flow in a square duct (Shah and London,
   fRe = 14.227) within 2.5 % at fidelity 2 (measured 1.5 %); a Re 1000 turbulent preview with the
   LES that delivers its inlet speed within 2 % and a positive pressure drop; and the compiled field
   classifying cells exactly as numpy evaluates the same text. DFG 3D-1Z, which needs a parabolic
   inlet the model does not have, stays the prototype's evidence. `tests/test_fast_core.py` covers
   the compiler's refusals, the lattice, the grid, the ports and the case without a GPU, and
   `tests/test_fast_api.py` the commands, the routes and a live edit on a real project.

## Alternatives considered

| Alternative | Why not |
|---|---|
| WebGPU in the browser (the prototype) | The fastest path to the screen, but the front end would compute physics (ADR-0013) and Fast mode would exist only in the GUI, not for agents or the CLI. Declined by the owner. |
| CUDA (CuPy, Numba) | NVIDIA only, and a large toolkit; wgpu reaches the same bandwidth on any vendor's GPU. |
| OpenFOAM on a coarse mesh as the "fast" solve | Meshing alone takes longer than the whole preview, and an edit restarts it. |
| FluidX3D | Its licence forbids commercial use. |
| A finite-volume projection solver on the Cartesian grid | Better suited to steady low-speed flow, but an implicit pressure solve per step is much more work to write and to make fast; lattice Boltzmann was measured first. Revisit if the steady-state time matters more than the live picture. |
| Staircase walls only | Measured to scatter around the reference instead of converging; the field gives the wall position for free. |
| A run record with a `.stkfld` artifact from slice 1 | The Results stage's slices and probes cut an OpenFOAM case on the host; a Fast result has none. Slice 3 writes one, from the lattice. |

## Consequences

- A new package `stokes.fast` (headless; numpy at module level is allowed, `wgpu` only inside
  `stokes.fast.device`), commands in `stokes/api/commands/fast.py`, a route in the server, a panel in
  the web client. The layering gate learns the package; `wgpu` is registered in
  `docs/engineering/dependencies.md` under the `fast` extra.
- The desktop host needs a GPU with Vulkan, D3D12 or Metal; nothing changes for engines or agents.
- **Slices after this one**: (2) slip walls, symmetry, a temperature field as a passive scalar for
  heated walls, imported (B-rep and mesh) bodies through a sampled distance field, the thin-feature
  warning, planes at any angle; (3) a run record and `.stkfld` so the Results stage (probes,
  streamlines, reports) works on a Fast result, and "Refine": hand the case to the chosen engine with
  the Fast field as its initial condition; (4) transient output and a wall model for the LES.
- **Measured on the owner's RTX 4000 Ada through Vulkan**: 2,400 to 3,900 million lattice updates per
  second, about a second from `fast.start` to the first frame, and a parameter edit in the next frame.
- Open: whether Fast mode is free or behind a feature flag (decision A4 is still open; assumed free,
  ungated, like the modeller).

## Addendum (2026-11-22): what a review against measurements changed

A review of slice 1 measured the lattice on a square duct, a round pipe, a duct turned 17, 30 and 45
degrees to the lattice, a slow viscous flow and a turbulent one (RTX 4000 Ada, Vulkan), and drove the
panel end to end in the app. Decisions 4, 5 and 8 change as follows; the rest stands.

**Decision 5, the lattice.**

1. **The equilibrium is the incompressible one** (He and Luo 1997): the velocity is the momentum, and
   the density carries the pressure only. With the usual equilibrium the volume flow followed the
   pressure: on the Re 50 square duct at fidelity 2 the density was 1.05 at the inlet and the volume
   flow 5.2 % below the inlet's along the duct; fRe read +1.58 %. Now the volume flow is the inlet's
   to four figures along the whole duct and fRe is -0.05 % (-0.01 % at fidelity 3); the developed
   peak is 2.090 times the mean (2.096, Shah and London).
2. **Every density is carried as its deviation from 1**, in the shaders and the port table. A sum
   started at 1 in f32 rounds to 6e-8, which was the whole signal of a slow flow: a pipe at a lattice
   speed of 2e-5 never settled, its flow rate wandering by 15 %. With the deviation it settles to
   fRe -0.24 %.
3. **A slow, viscous flow lowers the lattice speed to hold the relaxation time at 1**
   (`NU_LATTICE_MAX`, below a cell Reynolds number of 0.3). At the fixed speed of 0.05 a cell
   Reynolds number of 0.016 had a relaxation time of 10.1, the density reached the guard's cap of 2,
   the guard acted on a hundred million cells and 20 % of the flow got through (fRe -79 %). Now:
   fRe -0.07 % on the duct, -0.24 % on the pipe (Re 0.005).
4. **With the Smagorinsky model the collision is the regularised one** (Latt and Chopard 2006), not
   two relaxation times with the magic parameter 1/4. Two relaxation times near the relaxation time's
   limit left cell-to-cell noise of the inlet speed's size: behind the inlet at a cell Reynolds
   number of 62 the speed ran from -0.4 to 3.1 times the inlet's across one row of cells. Regularised,
   the same row is smooth (1.06 everywhere but the wall cells) and the largest speed in the duct
   falls from 4.0 to 1.5 times the inlet's. Stable with no guard at cell Reynolds numbers of 31, 62,
   156 and 195. **The Smagorinsky viscosity also acts on laminar shear**: a duct that is laminar in
   truth but runs as a turbulent preview (cell Reynolds number 9.4, Re 300) reads fRe +7.4 % (pipe
   +5.1 %); without the eddy viscosity +2.3 %, but then unstable at 156. Accepted for a preview.
5. **Outlets are an equilibrium layer** at the outlet's pressure, with the momentum the streaming
   brought (anti-bounce-back fills the missing links first). Anti-bounce-back alone let eddies pile
   up at the outlet: 14 times the inlet speed in the last layer and 2.6 million guarded cells at a
   cell Reynolds number of 62.

**Decision 4, the ports.**

6. **A port takes wall crossings within 0.3 of a cell of its plane**, not a whole cell: the wider
   test also took the side walls' links of the second layer of cells (32 cells on a pipe 32 across).
7. **An inlet's profile falls to zero at the walls over one cell, and a gain per inlet restores its
   flow rate.** The gain is the port's own area over the area its cells cover with their shares; the
   layer of cells next to a port is as thick as the longest lattice link along its normal (1 for a
   port square to the lattice, 1.41 at 45 degrees), which the cell count is divided by. Measured
   flow against the inlet's area times its speed: square duct 1.0000; pipe 0.995 to 0.997; ducts at
   17, 30 and 45 degrees within 1 % by the developed peak (fRe +0.3 to +0.9 %). A gain from the cell
   count alone delivered 25 % less through the duct at 30 degrees, and no gain at all about 2 % less
   through anything not square to the lattice.

**Decision 8, the output.**

8. **A frame reports the flow balance**: `flow_in`, `flow_out` (m3/s) and `imbalance`, their
   difference as a share of the inflow. `inlet_speed` is the inflow over the inlets' area. On the
   cases above the two agree within 0.1 % (ports square to the lattice) to 3.5 % (at an angle: the
   outlet's layer of cells is counted, not cut). The status has the fluid cell count, the density
   and whether the plane is the automatic one.
9. **Steady is judged over a window in lattice steps as well as seconds**: at least two seconds and
   at least half the steps the flow needs to cross the grid's longest side (or its viscosity the
   shortest, whichever is sooner), with the pressure drop and the outflow each within 1 % across the
   whole window. Two seconds alone called a large grid steady after a twelfth of a flow-through.
10. **The plane reaches the walls**: its mesh takes one more ring of vertices, the solid cells next
    to the fluid pulled half a cell towards it, at rest and at their neighbours' pressure
    (`stokes.fast.plane`). Triangles over cell centres alone stopped half a cell short of every wall.
11. **The plane's routes answer 204 when there is no plane** (no preview, or no fluid on the plane
    yet), not 404: the layer's last fetch loses the race with every Stop, and a 404 there put an
    error in the browser's console each time. `fast.configure` takes `axis: -1` for the automatic
    plane again.

**In the app** (found by driving it; `web/walk_fast.mjs` is the walk):

- **The plane was never drawn in the stages that offer the panel.** It is a mesh source, and the
  scene draws mesh sources only where the stage shows the mesh layer; in Geometry, Fluid and
  Boundaries the body faded and nothing appeared inside it. It is now drawn in every stage
  (`fitTargets.ts` `isOverlaySource`).
- A fetch that returned after Stop could put the plane back; the layer now checks.
- The panel had no card (its text lay on the view), no legend, no way back to the automatic plane
  while running and no way to move the plane; it has all four, the flow balance, and a note when the
  fidelity was raised or the grid coarsened.
- The panel could only be found by typing "Open Fast mode" in full (the palette lists the raw
  `fast.*` commands first); the top bar has a **Fast** button, lit while a preview runs.

**Speed is unchanged**: 2,600 to 4,100 million lattice updates per second on the cases measured
(7,100 on the coarsest pipe).

**Still open.** The Smagorinsky bias on laminar flow (a model that vanishes in pure shear, or a
laminar regularised range between cell Reynolds numbers of 8 and about 30). The lattice steps flat
out while steady, on the GPU the viewport draws with: a batch is 25 ms of GPU time, so a frame can
wait that long; not measured as a frame rate. Walls at an angle leak 0.2 % of the flow per 380 cells
(interpolated bounce-back is not conservative). A flow with no outlet, or outlets at different
pressures, has not been measured.

## Addendum 2 (2026-11-22): several bodies, the temperature, and the items left open

The owner asked for the first addendum's open items, for more models where they are easy, and for
conjugate heat transfer and heat exchangers to work in Fast mode (ADR-0042's projects: two fluids
unmixed and a solid between). This addendum adds decisions 10 to 14 and changes 5 and 8.

10. **A case is zones.** Every body the regions hold is a zone, each as it is a mesh region
    (`stokes.mesh.conjugate.region_bodies`: fluids first, a body left out skipped): a fluid with
    its own flow, inlets, outlets and material, or a solid. A cell's code (the low byte of its
    flags) names its zone; it is the zone the cell is deepest inside. **A fluid's wall is any
    neighbour whose code is not its own**, so two fluids never mix even where no solid cell lies
    between them, and a solid's cells are walls to both. One time step serves all: the cell
    Reynolds number is the largest of the fluids', the lattice viscosity that is capped the most
    viscous one's, each fluid has its own relaxation rates. A fluid body with no opening rests (a
    sealed pocket conducts and does not break the run); a fluid with an inlet and no outlet is
    refused (`fast.invalid`, detail `fast.no_outlet`): its flow has nowhere to leave.
11. **The temperature is solved with finite volumes on the lattice's cells**, through fluids and
    solids alike, one explicit step per lattice step (`stokes.fast.wgsl.thermal_shader`), when the
    case has more than one body or the solver settings have the energy equation on. Conduction
    between two cells goes through the harmonic mean of their conductivities, which keeps the
    heat flux continuous where the conductivity jumps; advection is within one fluid, second order
    with a limiter, written as `u . grad T` from the face fluxes. **A lattice scalar (D3Q7) was not
    taken**: beside water, aluminium needs a lattice diffusivity 600 times the stable one, and a
    relaxation time that far from 1 is not a wall one can trust. **A solid's heat capacity is not
    its own**: every solid is stepped at a lattice diffusivity of 1/8, which reaches the same
    steady field (only the conductivities set it); the transient through a solid is not physical.
    Temperatures are carried less the mean of the inlets', as densities are carried less 1.
    Inlets hold their temperature; outlets let the flow carry it out; a wall held at a temperature
    or given a heat flux is a thermal wall where its faces are one plane, else the condition of the
    rest of its body's outside; everything else is adiabatic. Material properties are the regions'
    materials' at their reference temperatures, as the solve reads them.
12. **Streams.** A frame lists each fluid as the Results stage's Streams table does: flow, mass
    flow, pressure drop, inlet and mixing-cup outlet temperature, heat (mass flow times specific
    heat times the rise), and the sum of the heats. The plane has `T` as a value, on a mesh of its
    own that covers the solids.
13. **The turbulent preview has two closures** (changes decision 5): plain Smagorinsky, and the
    shear-improved one (Leveque et al. 2007), which takes the running mean of a cell's stress off
    before the eddy viscosity, so a steady laminar shear has none. `auto` takes the shear-improved
    one up to a cell Reynolds number of 30 and Smagorinsky above; `fast.start` and `fast.configure`
    take `turbulence`.
14. **A steady preview holds** (changes decision 8): after ten steady frames the lattice steps one
    batch and then waits a quarter of a second, until any request or a frame that is not steady.
    The lattice speed of a restrictive core is lowered as far as its density swing needs (to
    0.004), not in one step to 0.02. `STOKES_FAST_DISABLE` switches Fast mode off. A plane's
    `offset` is a length (`"2 [mm]"`) or a number of metres.

**Measured** (RTX 4000 Ada; scripts in the session's scratch folder, the checks below are tests in
`tests/test_fast_thermal.py`):

| Case | Reference | Fast mode |
|---|---|---|
| Square duct, walls at one temperature, Re 50, Pr 1 | Nu 2.976 (Shah and London) | 2.971 (-0.2 %) |
| Two solid layers, k 10 and 1, between 350 K and 300 K | interface 345.455 K, 9,091 W/m2 | 345.44 to 345.46 K, 9,094 to 9,096 W/m2 |
| Counter-flow channels and a wall, Pr 0.67 | 22.3 W (effectiveness-NTU, Nu 2.712) | 23.3 W (+4.3 %); hot and cold agree to 1 mW; the same at fidelity 1 and 2 |
| The same at Pr 6.7 | 3.7 W with the developed Nu | 5.8 W: the flow is still developing thermally over the whole length |
| A 2.5 mm water duct in a block at 350 K, 16 and 8 cells across, square to the lattice | mean Nu about 6.4 to 6.8 | 7.0 and 8.0 (+7 %, +20 %) |
| The same turned 30 degrees | the same | 9.2 and 10.6 (+40 %, +60 %) |
| Laminar duct run as a turbulent preview, Re 300 | fRe 14.227 | +2.3 % shear-improved (the entrance's share), +7.4 % Smagorinsky; pipe -0.1 % and +5.1 % |
| Turbulent preview at cell Reynolds numbers 62 to 195 | - | stable with no guard under both; the flow rate along a duct wanders 7 to 9 % shear-improved, 1.3 % Smagorinsky |
| A water channel on an aluminium base heated with 10 kW/m2 (3 W) | 3 W into the stream | 2.76 W after 500,000 steps (about three minutes) and still rising towards 3: a solid heated by a flux warms at its stepped heat capacity, six times water's here, so such a case is slow; steady is not claimed while the streams' heat moves |
| A tee, two outlets at 0 and 0.1 Pa | - | split 0.80 / 0.22 and 0.89 / 0.13, outflow 1.6 % over inflow |
| Speed with the temperature | - | 1,900 to 2,000 million cell updates per second (2,650 without) |

**The plate heat exchanger** (gyroid core 44 x 30 x 6 mm with 1.2 mm walls, split headers, four
ports, water at 0.1 m/s and 350 K / 300 K, aluminium): it runs, shows the temperature through
both streams and the plate and the streams' figures, and the hot and cold heats agree within 4 %.
**Its numbers are not the solved case's.** Solved (OpenFOAM, 1.23 M cells, not converged): 3.81
g/s, 1,911 Pa, 350 to 342.6 K, -117.5 W hot; +107.1 W cold; a converged flow-only run on a finer
mesh, 2,512 Pa. Fast mode at fidelity 4 (3.2 M cells, 0.22 mm, about 160 s): 3.82 g/s, 3,750 and
4,275 Pa, 350 to 329.5 K, -328 W hot, +341 W cold, with the outflow 10 % under the inflow. Three
causes, each measured above or on this case: the pressure drop is 250 inlet dynamic heads, so the
lattice speed must fall to 0.006 before the density stays inside the guard, and the run is then
slow to settle; interpolated walls lose mass in channels eight cells wide (with half-way walls the
flow is kept and the pressure climbs past 12 kPa without settling); and the temperature's step
over-predicts heat transfer by 40 to 60 % in passages at an angle to the lattice at this
resolution. A preview of such a core shows where the heat goes; its duty is not to be read.

**In the app.** The panel names the bodies it previews, offers the temperature, lists the streams
with their heat balance, says when it holds, has the closure, and uses the Results looks' colour
map. `Fast` in the palette finds the panel before the raw commands. With no GPU (or the variable
set) the panel says why and Start's tooltip repeats it. `web/walk_fast.mjs` walks all of it,
with a conjugate project as its optional last argument.

**Not built, with what each would take.**

| Model | Cost | Note |
|---|---|---|
| Buoyancy (Boussinesq) | half a day | a body force on the fluid from the temperature the step already has; needs gravity from the settings and a forcing term in both collisions |
| Force on a body (drag, lift) | half a day | momentum exchange summed over a zone's wall links, or the ports' momentum balance when steady |
| A history plot of the pressure drop | an hour or two | the client already receives every frame |
| Moving wall | half a day | the wall velocity in the bounce-back term; the model needs a wall-velocity value first |
| Porous (Darcy) zone | a day | a drag term per zone; a zone kind and its permeability in the model |
| Wall model for the turbulent preview | several days | the eddy viscosity at the first cell from a law of the wall; without it the y+ is whatever the cells give |
| Imported (B-rep, mesh) bodies | one to two days | a distance field sampled from the tessellation into a 3D buffer, read where the field's function is called now |
| Streamlines and arrows on the plane | a day | the plane's velocity through the Results looks' glyph and line layers (`glyphLayer.ts`, `streamlineLayer.ts`), which take any mesh with a vector per vertex |
| Probe and hover on the plane | a few hours | the layer keeps the values it painted, as `SliceLayer.valuesOf` does, and `HoverReadout` asks it |
| Implicit conduction in the solids | two to three days | removes the slow warm-up of a flux-heated solid (the explicit step's capacity floor); a few Jacobi sweeps per step on the solid cells |
| A less diffusive temperature step | days | the angle error above is the limiter's; a multidimensional scheme or a finer thermal grid |

**Still open.** Backflow through an outlet strong enough to exceed the inlet's speed relies on the
guard's hand-over to the turbulent preview; it was not measured through the runner. The frame rate
of the viewport while the lattice steps was measured as the same 33 ms a frame stepping and holding
in a headless browser, which caps at that; a desktop measurement is still wanted. The desktop host
(`python -m stokes.app`) was not driven.

## Addendum 3 (2026-11-22): the plate exchanger's numbers, and what limits them

The owner asked for the plate exchanger's pressure drop and duty within about 20 % of the solved
case before the second addendum's work is merged. **They are not, and at a preview's cell count
they cannot be with this lattice**; this addendum records what was changed, what it bought, and the
measurements that show the limit.

**Changed.**

15. **Interpolated walls keep a cell's mass.** What the interpolated bounce-back returns less what
    was sent to the wall is taken off the cell's rest population. Without it the plate exchanger's
    outflow was 10 % under its inflow; with it the two agree within 0.5 % once settled (0.998 to
    1.004). The rest population carries no momentum, so the wall stays where the interpolation
    puts it: the square duct, the turned duct and the pipe tests read as before.
16. **The guard holds the speed alone, and the density is not capped.** With the incompressible
    equilibrium the density is the pressure and nothing else; a core whose pressure drop is
    hundreds of inlet dynamic heads has a density deviation above 1 and the cap held its flow
    back. **The lattice speed is lowered on the guard, not on the density**: halved each time the
    guard has held cells of a turbulent preview for ten frames, down to 0.004.
17. **The temperature catches up once the flow is steady**: sixteen steps of the temperature to
    each lattice step while the pressure drop and the outflow are steady and the streams' heat is
    not. A solid heated by a flux closes: 3.12 W of 3 W after 59,000 steps (34 s), where it read
    2.76 W after 500,000.
18. **A fluid's cells across its passages are reported** (`resolution` in the status: its
    hydraulic diameter in cells, four times its cells over its wall faces), and below 10 the panel
    says the preview is under-resolved: "Read the picture, not the numbers".
19. **A port leaves out the slivers of faces round its rim** when the faces on its plane carry at
    least 90 % of the tagged area: the gyroid core's inlets are tagged with such faces, the solve
    takes them, and Fast mode refused the port as not flat.

**The plate exchanger, before and after** (fidelity 4: 3.2 M cells of 0.219 mm; reference: the
solved case, and for the pressure drop the converged finer run, 2,512 Pa):

| | Reference | Addendum 2 | Now, while settled (150 to 190 s) |
|---|---|---|---|
| Outflow over inflow | 1 | 0.89 to 0.90 | 0.998 to 1.004 |
| Pressure drop, hot / cold | 1,911 / 2,070 Pa (2,512 converged) | 3,750 / 4,275 Pa | 4,300 to 4,500 / 5,100 Pa (+75 to +100 %) |
| Hot outlet | 342.6 K | 329.5 K | 328.8 to 329.4 K |
| Heat, hot / cold | -117.5 / +107.1 W | -328 / +341 W | -330 to -339 / +353 to +361 W (about 3 times) |

It does not stay settled: after about 200 s the guard starts again and the pressure runs away
(19 kPa at 370 s). **What limits it, measured:**

- **The throat.** The narrowest passage between each stream's ports is 1.2 mm (the gap the
  headers' cut-back leaves): 5.5 cells at fidelity 4, found as the widest wall distance that still
  joins the two ports. The mean passage is 8.1 cells (4.2 at fidelity 2).
- **The speed in it.** At the inlet's lattice speed of 0.05 the guard held 200,000 cells, so the
  throats run more than six times the inlet's 0.1 m/s; their cell Reynolds number is about 200,
  where the rest of the core's is 25.
- **What this lattice does there.** A 2.5 mm water duct at Re 225, port to port (24 Pa at 32 cells
  across, where the lattice is laminar): 28 to 31 Pa at 16 cells, 48 to 74 Pa at 8, 36 Pa at 5.5,
  and at 30 degrees to the lattice 21, 39 and 84 Pa. Above a cell Reynolds number of 8 the
  two-relaxation-time collision is unstable (the guard holds tens of millions of cells), so these
  run the regularised collision with an eddy viscosity, and at so few cells that is neither
  laminar nor accurate.
- **The cell count that would do.** Twelve cells across the throat is 0.1 mm: 34 million cells,
  above the 28 million one buffer holds on this card, and about an hour a run.

The heat is in the same case: the same turned duct reads a mean Nusselt number of 9.2 at 16 cells
and 10.6 at 8 against 6.4 to 6.8, and an estimate with the core's own area (4.1e-3 m2 a side,
hydraulic diameter 1.5 mm) puts the duty between 140 W (Nu 4) and 280 W (Nu 10): the solved case's
117 W, on 1.23 M cells and not converged, is not a firm reference either.

**The coarse gyroid core** (the counter-flow core of the earlier work, water at 0.05 m/s; solved
101.9 W, analytic estimate 97.3 W): Fast mode at fidelity 4 (610,000 cells of 0.19 mm, 5.7 cells
across, steady after 85 s with no guard) reads -118.7 W hot and +116.6 W cold (+16 % on the solved
case), 141 and 132 Pa, outflow 0.97 of inflow. It has no throat.

**Not fixed.**

- **Backflow through an outlet**, through the real runner: a tee with its branch outlet at 2 Pa
  (ten inlet dynamic heads) does not go to NaN, but the guard holds 36,000 to 70,000 cells and the
  figures are wrong (240 Pa, outflow 1.7 times inflow). The panel says the guard is acting.
- **The turned duct's Nusselt number** (9.2 against 7.0 square to the lattice at 16 cells): the
  wall's stair-step area is not the cause (the longer path between the cells cancels it to first
  order); the flow is. The inlet of a duct at 30 degrees delivers 3 to 6 % less than its nominal
  flow at 16 to 8 cells, and the rest is the advection scheme across the grid.
- **A collision that stays accurate above a cell Reynolds number of 8** (cumulant or central
  moments on D3Q27): several days, and the one change that would move the plate exchanger.
