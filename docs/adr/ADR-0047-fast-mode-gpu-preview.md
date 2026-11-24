# ADR-0047 · Fast mode: a GPU lattice-Boltzmann preview that follows the implicit geometry live

**Status:** proposed · 2026-11-21 · addenda 2026-11-22 (below: the first changes decisions 4, 5 and 8; the second adds zones, the temperature and decisions 10 to 14; the third adds 15 to 19 and says what limits a fine core) · fourth addendum 2026-11-23 (the research for high resolution and more of the OpenFOAM options, decisions 20 to 30; round 4 built and measured; corrected the same day after an independent check) · (ADR-0013 front end, ADR-0023 runs, ADR-0035 implicit geometry, ADR-0044 coupled engine, decision B5)

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

## Addendum 4 (2026-11-23): research for high resolution, the OpenFOAM 14 options, and round 4

The third addendum ended on a limit: the plate exchanger needs about twelve cells across a 1.2 mm
throat (34 million cells) and a collision that stays accurate above a cell Reynolds number of 8,
and the lattice had neither. The owner asked what it takes to reach high resolution and to carry
more of the options a solve has. This addendum records the research, maps the OpenFOAM 14 options
Stokes offers onto what a lattice can carry, ranks the work by the measurements of the earlier
addenda, and decides what round 4 builds. **Rule for all of it (the owner, 2026-11-23): clean
room.** Nothing is read, copied or ported from FluidX3D, Palabos, waLBerla, lbmpy or any other
lattice-Boltzmann code; every technique is written from the published method.

### What the measurements ask for

| Measured (addenda 1 to 3) | What it asks for |
|---|---|
| Plate exchanger: throat 5.5 cells, cell Reynolds number about 200 there; pressure drop about 2x, duty about 3x | more cells (memory), and a collision that is accurate at a high cell Reynolds number |
| 34 M cells wanted, 28 M is what one population buffer holds (2 GiB binding, 76 bytes a cell), 8 M the default cap | one population buffer instead of two, 16-bit storage, fluid cells only |
| "About an hour a run" at that size; a restrictive core's lattice speed falls to 0.004 and it settles slowly | start a fine lattice from a coarse solution; later, preconditioning |
| Smagorinsky reads a laminar duct +7.4 % (pipe +5.1 %); shear-improved wanders 7 to 9 % at coarse cells | a closure that vanishes in laminar shear by construction (WALE, Vreman) |
| Interpolated walls lost a tenth of the flow before the mass fix; a turned duct's Nu +40 to +60 % | mass-conserving curved walls with a sounder basis than the rest-population fix |
| y+ is whatever the cells give | a wall function |

### The research: what is known, and what was and was not checked

"Read" means the paper's text was read for this addendum; "abstract" that only its abstract was;
"not opened" that the publisher refused the request and the claim rests on secondary sources or on
general knowledge of the field. Nothing below is a clearance of any right: where a patent may
exist it is flagged, not searched.

| Technique | Source | Checked | Not checked |
|---|---|---|---|
| In-place streaming, one population set | Geier and Schoenherr, "Esoteric Twist", Computation 5(2):19, 2017 (CC BY 4.0), [TU Braunschweig copy](https://www.tu-braunschweig.de/fileadmin/Redaktionsgruppen/Forschung/SFB880/Veroeffentlichungen/Veroeffentlichung_A5_2017_Kutscher_Esote_Twist_Compu_Vol._rev_1.pdf) | **Read**: populations moving in negative directions are pulled from the positive neighbours, those moving in positive directions are read at the node; after the collision each is written where its opposite was read; the two arrays of a pair swap roles every step; a node that is never touched bounces back by itself ("implicit bounce back"). The paper states no patent and declares no conflict of interest. | A patent search. |
| The same with whole opposite pairs | Lehmann, "Esoteric Pull and Esoteric Push", Computation 10(6):92, 2022, [doi](https://doi.org/10.3390/computation10060092) | **Not opened** (the publisher answered 403 to every request). A search summary of its abstract: thread-safe, one copy of the populations, a modification of Esoteric Twist, all velocity sets. | The paper's own statement of the scheme. The scheme built here was therefore **derived in this repository** from the principle Geier and Schoenherr state (decision 22), and proven by test against the two-buffer lattice. We believe it is what Lehmann calls Esoteric Pull; that is not verified. |
| 16-bit storage of the populations | Lehmann, Krause, Amati, Sega, Harting, Gekle, Phys. Rev. E 106, 015308, 2022, [arXiv:2112.08926](https://arxiv.org/abs/2112.08926) | **Read** (sections 2.2, 3.1, 6): arithmetic in 32 bits, storage in 16; populations must be stored less their weights ("DDF-shifting"), without which 16 bits "become unfeasible"; `FP16S` is IEEE half precision scaled by 2^15 so its range is +-2 ("should always be preferred over FP16", at no cost); `FP16C` is a custom format of 1 sign, 4 exponent and 11 mantissa bits with hand-written conversion and half the truncation error (4.9e-4 against 9.8e-4); memory traffic 171 bytes a cell and step in 32 bits against 95 in 16 (D3Q19, their code). In a Poiseuille channel the 16-bit error grows with the radius from about 15 cells and is largest at the centre. | Every figure's numbers; the Posit formats. |
| Fluid cells only (sparse) | [arXiv:2503.07898](https://arxiv.org/abs/2503.07898) | **Abstract**: dense, block-sparse and multi-resolution grids on GPUs, "up to 3x" over the state of the art. | The notes' "up to 26 %" is not in the abstract: unverified. Esoteric Twist's section 3 (read) gives the cost of indirect addressing: three links a node with pointer chasing. |
| WALE closure | Nicoud and Ducros, Flow Turb. Combust. 62:183, 1999, [doi](https://doi.org/10.1023/A:1009995426001); Baya Toda, Cabrit, Balarac, Bose, Lee, Choi and Nicoud, "A subgrid-scale model based on singular values for LES in complex geometries", Center for Turbulence Research, Proceedings of the Summer Program 2010, [pdf](https://web.stanford.edu/group/ctr/Summer/SP10/3_03_bayatoda.pdf) | The 1999 paper: **not opened** (the publisher's page has the abstract only, the HAL preprint and its mirrors sit behind an access check, the archive copy is not reachable from here). The 2010 paper, of which Nicoud is an author: **read**. It writes every model as `nu_t = (Cm D)^2 Dm(u)` and its table 1 gives, for the WALE operator ("Eq. 13" of the 1999 paper), `Cw ~ 0.50`, beside `Cs ~ 0.18` (Smagorinsky) and `Cv ~ 0.28` (Vreman), as "the values recommended by Nicoud & Ducros (1999) and Vreman (2004)"; the table's pure-shear row is 0 for WALE and for Vreman, and its own estimate of `Cw` from random velocity gradients is 0.63. That WALE vanishes in pure shear is also **proven here by a test**. | The 1999 paper's calibration against isotropic turbulence, often quoted as `Cw^2 ~ 10.6 Cs^2` (0.55 to 0.59 for `Cs` of 0.17 to 0.18): not in anything read. Other codes' constants belong to other forms of the model (OpenFOAM's 0.325 sits in a k-equation form) and do not transfer. |
| Vreman closure | Vreman, Phys. Fluids 16:3670, 2004, [doi](https://doi.org/10.1063/1.1785131) | **Abstract** (TU Eindhoven's record): first derivatives only, no filtering, averaging or clipping; zero sub-grid dissipation for a class of laminar flows. Formula as restated in [arXiv:2208.14217](https://arxiv.org/abs/2208.14217) and [arXiv:2309.05875](https://arxiv.org/abs/2309.05875): `nu_t = c sqrt(B_beta / (a:a))`, `c = 2.5 Cs^2`. Zero in pure shear: **proven here by a test**. | The paper itself. |
| Cumulant and central-moment collisions | Geier et al. 2015 (cumulants, D3Q27); Coreixas, Chopard, Latt, Phys. Rev. E 100, 033305, [arXiv:1904.12948](https://arxiv.org/abs/1904.12948) | **Abstract** of the comparison: raw-moment, central-moment, cumulant and regularised forms compared on D1Q3, D2Q9, D3Q27 (D3Q19 covered); its stated conclusion is the equilibrium's accuracy, not a ranking for under-resolved flow. | The periodic-hill and DrivAer results of the notes; that a cumulant collision fixes a 5.5-cell throat at a cell Reynolds number of 200 is **a hope, not a finding**. No patent turned up in one web search, which is not a clearance. |
| Entropic multi-relaxation (KBC) | Boesch, Chikatamarla, Karlin, [arXiv:1507.02518](https://arxiv.org/abs/1507.02518) | **Abstract**: "outstanding numerical stability" at high Reynolds numbers, no tunable parameter. | **Flag**: entropic lattice-Boltzmann methods are, from general knowledge of the field, the subject of patents; none was looked up. Not to be built without a search. |
| Preconditioning for steady flow | Yahia and Premnath, J. Comput. Sci. 2022, [arXiv:2208.03562](https://arxiv.org/abs/2208.03562) | **Abstract**: a preconditioned central-moment method, "significant reductions in the numbers of steps". | The notes' "610,000 steps down to 24,000" is not in the abstract: unverified. It needs a moment-space collision first. |
| Mass-conserving curved walls | Yu, Li, Wen, Comput. Fluids 208, 2020, [arXiv:1908.09235](https://arxiv.org/abs/1908.09235) | **Abstract**: three known curved-wall schemes leak mass (a droplet "evaporates"); the cause is the imbalance between what leaves and what enters at each wall node; two corrected schemes. | The correction's formulas. Our fix (decision 15) is of the same kind but puts the difference in the rest population; theirs was not compared. |
| Wall function | Han, Ooka, Kikumoto, [arXiv:2009.04352](https://arxiv.org/abs/2009.04352) | **Abstract**: Spalding's law gives the wall shear, and the bounced populations are altered to carry it ("wall-function bounce"); channel flow at Re_tau 640 and 2003 on coarse grids. | The scheme's formulas. **Flag**: wall models and turbulence models coupled to a lattice are, from general knowledge, a field with commercial patents; none was looked up. |
| Grid refinement | the notes' four links | nothing | all of it |
| Warm start (prolongation) | not from a paper | The non-equilibrium part between two lattices follows from the first-order Chapman-Enskog relation `Pi_neq = -2 cs^2 tau S` in lattice units (any text on the method); it is the relation the classical grid-refinement papers (Filippova and Haenel 1998; Dupuis and Chopard 2003) rescale by, **neither opened**. | - |

### The OpenFOAM 14 options Stokes offers, and what a lattice can carry

The options are those of `stokes/engines/turbulence.py` and `stokes/engines/catalogue.py`.

| Option | Fast mode | How, or why not |
|---|---|---|
| laminar | **has** | two relaxation times, exact half-way walls |
| LES Smagorinsky | **has** | the stress from the populations |
| LES WALE | **built in round 4** | needs the whole velocity gradient: differences of the neighbours' velocities |
| LES kEqn, dynamicKEqn | can carry, deferred | a transported scalar (the temperature's step is the pattern), or a test filter |
| RANS SpalartAllmaras | can carry, deferred, first of the RANS models | one transported scalar and the wall distance, which the implicit field gives exactly; the eddy viscosity goes into the relaxation time |
| RANS kOmegaSST, kOmega | can carry, later | two scalars, stiff source terms near walls |
| RANS kEpsilon family | can carry with wall functions only | not before the wall function |
| Reynolds-stress models, v2f, kkLOmega, the DES family | **refused** | a preview does not need them; a client is to name the model and say so |
| energy (passive temperature) | **has** | finite volumes on the lattice's cells |
| buoyancy | Boussinesq only, deferred | a body force from the temperature the step already has; a variable-density buoyant solve does not map and is refused |
| conjugate heat transfer | **has** (addendum 2) | harmonic conductivities; the solids' transient is not physical |
| porous zones | can carry, deferred | a Darcy-Forchheimer drag as a force |
| moving walls | can carry, deferred | a momentum term in the bounce-back |
| MRF, rotating zones | **refused** | little published for lattices; not searched further |
| compressible flow | **refused** | the lattice is a low-Mach method |
| slip and symmetry walls | can carry, small | specular reflection, for walls square to the lattice only |
| mass-flow inlets, total pressure, outlets with backflow | open | not researched in this round |

That a RANS eddy viscosity keeps the relaxation time away from its limit at a high cell Reynolds
number is an inference of ours, not a claim of any source read. Today Fast mode does not read the
solve's turbulence model at all (its closure is its own setting); mapping the model's name onto the
table above, with the refusals, comes with the first RANS model.

### The ranked plan

1. Warm start (prolongation from a coarser lattice). Cheap, no model risk.
2. One population buffer (in-place streaming) and 16-bit storage. Memory is the hard cap.
3. Fluid-only storage. The gyroid core's fluids are well under half its box.
4. WALE and Vreman closures. Local, small, removes a known bias.
5. A cumulant or central-moment collision on D3Q27. The one change expected to move the plate
   exchanger; days, and unproven.
6. Preconditioning (after 5).
7. Mass-conserving curved walls with a published basis.
8. A wall function (Spalding), then a transported-scalar RANS model (SpalartAllmaras first).
9. Boussinesq buoyancy, porous drag, moving walls.
10. Grid refinement.

### Decisions

20. **Round 4 builds, in this order: the WALE and Vreman closures, the warm start, in-place
    streaming with optional 16-bit storage, and fluid-only storage only if the first three are
    done and measured.** An item that regresses is stopped and reported, not forced. The closures
    come first because they are small and their test (a laminar duct run as a turbulent preview)
    exists.
21. **Deferred, each with its reason above**: the cumulant or central-moment collision,
    preconditioning, mass-conserving curved walls, the Spalding wall function, transported-scalar
    RANS (SpalartAllmaras first), Boussinesq buoyancy, porous drag, moving walls, grid refinement.
    The entropic collision and the wall function are not to be built before a patent search.
22. **In-place streaming is the pair-wise scheme derived here**: of each pair of opposite
    directions one is kept at the cell and the other at the cell's neighbour along the pair, and
    the two slots swap roles every step, so a cell reads and writes the same places and no two
    cells share one.
23. **16-bit storage is half precision scaled by 2^15** (Lehmann et al.'s `FP16S`), through the
    `shader-f16` feature where the adapter has it; arithmetic stays 32-bit. The custom `FP16C` is
    built only if the scaled half precision misses the tolerance.

What was built, the decisions it led to and the measurements follow in "Round 4: built and
measured".

### Provenance

Every technique of round 4 was **written from scratch for Stokes**, from the sources named; no code
of any other project was read.

| Technique | Implemented from |
|---|---|
| WALE closure | the published formula (Nicoud and Ducros 1999) as restated in the later literature, the original not opened; its constant, 0.5, from table 1 of Baya Toda et al. (CTR Summer Program 2010, Nicoud an author), read |
| Vreman closure | the published formula (Vreman 2004) as restated in arXiv:2208.14217 and arXiv:2309.05875; the abstract read |
| Velocity gradient on the lattice | our own: central differences of the neighbours' velocities, a wall taken half-way with the fluid at rest on it |
| Warm start | our own: trilinear interpolation of the velocity, the density's deviation and the temperature; the non-equilibrium part from the first-order Chapman-Enskog relation |
| In-place streaming | derived here from the principle in Geier and Schoenherr 2017 (read); Lehmann 2022 not opened |
| Boundary links under in-place streaming | our own |
| 16-bit storage | Lehmann et al. 2022 (read): `FP16S` |
| The 16-bit store's rounding at random in proportion | our own (decision 28); stochastic rounding is a standard technique and is not in Lehmann et al. |
| The share of its time the worker samples | our own (decision 29) |

**Patents and licences noticed.** None of the papers read states a patent or a restriction on the
method; the Computation paper and arXiv:2208.03562 are CC BY 4.0 (the text's licence, not a grant
of any patent). Flagged, not searched: entropic collisions, wall models and turbulence models on a
lattice. No dependency was added.

### Round 4: built and measured

All on the RTX 4000 Ada through Vulkan, fidelity 2 unless said, with a synchronous harness that
drives the runner's own steps (session scratch). "Updates a second" counts every cell of the
grid, the solid ones too, as the earlier addenda did.

**The closures (decision 20, item 1).** `turbulence` takes `wale` and `vreman`. The step keeps
each cell's velocity and density in a buffer of their own (the samples, the plane and the
outlets read them there, not from the populations), and the two closures difference the
neighbours' velocities for the gradient: a wall is half-way to a neighbour that is not this
fluid, with the fluid at rest on it. `stokes.fast.lattice.eddy_viscosity` is the same arithmetic
in numpy; its tests prove both vanish in a pure shear and in a duct's two-directional shear, and
hold a plane strain to the value worked by hand.

A flow that is laminar in truth, run as a turbulent preview (Re 300, 10 mm, 400 mm long so the
flow is developed where the friction is read, cell Reynolds number 9.4; "no model" is the same
regularised collision with its eddy viscosity switched off):

| Closure | Square duct fRe (14.227) | Pipe fRe (16) | Updates a second, duct |
|---|---|---|---|
| no model | +0.33 % | +0.08 % | 2,370 M |
| Smagorinsky | +5.61 % | +5.46 % | 2,370 M |
| shear-improved | +0.33 % | +0.08 % | 2,260 M |
| WALE | +0.33 % | +0.08 % | 2,190 M |
| Vreman | +0.36 % | +0.09 % | 2,180 M |

(The second addendum's +2.3 % and +7.4 % were read on a shorter duct, where the entrance still
adds about 2 %.) Turbulent previews of a 20 mm duct, 120 mm long: how far the volume flow along
the duct strays from the inlet's at one instant (it cannot in an incompressible flow, so this is
the lattice's own noise), the largest speed over the inlet's, and the speed:

| Cell Reynolds number (Re) | Smagorinsky | shear-improved | WALE | Vreman |
|---|---|---|---|---|
| 31 (1,000) | 0.0 %, 1.55 | -3.5 to +3.7 %, 1.49 | -3.7 to +3.9 %, 1.50 | -3.1 to +3.1 %, 1.50 |
| 62 (2,000) | 0.0 %, 1.48 | -9.3 to +7.2 %, 1.65 | -6.9 to +5.9 %, 1.58 | -5.9 to +6.6 %, 1.58 |
| 156 (5,000) | -1.2 to +1.2 %, 1.50 | -8.8 to +8.0 %, 2.18 | -5.2 to +4.7 %, 1.87 | -6.6 to +5.2 %, 1.96 |
| 195 (6,250) | -1.3 to +1.3 %, 1.49 | -6.9 to +8.2 %, 2.06 | -3.2 to +3.4 %, 1.98 | -2.6 to +3.6 %, 2.08 |
| Updates a second | 4,450 to 4,530 M | 4,210 to 4,310 M | 3,900 to 3,980 M | 3,950 to 4,020 M |

No guard and no NaN in any of the sixteen runs.

24. **`auto` stays as it was: shear-improved up to a cell Reynolds number of 30, Smagorinsky
    above. WALE does not replace Smagorinsky there.** Above 30 the flow along a duct strays 3 to
    7 % under WALE against 0 to 1.3 % under Smagorinsky, and the largest speed reads 1.5 to 2.0
    times the inlet's against 1.5: a preview is read off one frame, and the smoother field is the
    better preview. The price stays what it was: Smagorinsky reads a laminar shear 5.5 % high.
    Below 30, WALE and shear-improved read the same on every case measured, and WALE is 3 %
    slower (it reads six neighbours' velocities), so nothing is gained by a change. (The WALE
    constant is 0.5, the value its authors recommend for this form of the model, read in a
    table of Nicoud's group; with 0.55 and 0.6 the turbulent duct at a cell Reynolds number of
    156 reads 2.06 and 2.12 inlet dynamic heads against 2.05, its flow strays the same 5 to
    6 %, and the laminar duct reads the same +0.33 %: the choice above does not turn on it.) WALE and
    Vreman are there to be chosen by hand: no bias on laminar flow at any cell Reynolds number,
    less noise than the shear-improved closure above 60 (3 to 7 % against 7 to 9 %), and no
    running mean to wait for (the shear-improved closure takes 2,000 steps to learn a new mean
    after an edit).

**The warm start (item 2).** A lattice built while another one is running takes that one's
solution: a shader interpolates the old lattice's velocity, density deviation and temperature to
the new cells (trilinear between cell centres, among those of the eight old cells round a point
that belong to the same body, so nothing is taken across a wall), in the new lattice's units
(the two lattices' `dx/dt` differ), and one step in a start mode then builds the populations:
the equilibrium of that velocity and density plus the part the velocity's own strain gives at
the new relaxation time, `Pi_neq = -2/3 tau S`, as it stands after a collision. Building the
non-equilibrium part from the interpolated velocity on the new lattice is what rescaling the old
one by `tau dt` amounts to, and needs nothing but the velocities. The inlets do not ramp again.

25. **`warm_start` is on by default** (`fast.start`; the panel has a tick box). A fidelity raised
    or lowered on a running preview keeps its flow. A run asked for at fidelity 4 or 5 first runs
    at fidelity 2 until its flow is steady, or for eight steady windows at most (a turbulent
    preview never is), and the asked-for lattice starts from it; meanwhile the status and the
    frames say `warming` and no frame is called steady. A lattice speed the coarse pass had to
    lower (a restrictive core) is kept.

Seconds and lattice steps from `fast.start` to the first steady frame, through the runner as the
app drives it (the steady rule is unchanged: 1 % over at least two seconds and half a
flow-through). "Fine steps" are those on the asked-for lattice; the warm runs took about 15,500
(duct) and 10,800 (ball) coarse steps before them, in the seconds in brackets:

| Case, fidelity (cells) | Cold | Warm | Pressure drop, cold and warm |
|---|---|---|---|
| Square duct, 4 (3.57 M) | 90 s, 27,150 steps | 35 s (10 s coarse), 7,850 fine steps | 0.19953 and 0.19965 Pa (0.06 % apart) |
| Square duct, 5 (7.57 M, at the old 8 M cap) | 384 s, 35,070 steps | 131 s (9 s coarse), 10,170 fine steps | 0.195845 and 0.195846 Pa |
| Ball in a box, 4 (0.91 M) | 11.1 s, 11,800 steps | 8.7 s (5.5 s coarse), 5,080 fine steps | 9.0764 and 9.0768 mPa |
| Ball in a box, 5 (2.92 M) | 37 s, 13,950 steps | 19 s (4.5 s coarse), 5,950 fine steps | 9.1014 and 9.1033 mPa (0.02 % apart) |

The fine lattice's share is as short as the steady rule allows (half a flow-through is 7,700
steps on the duct at fidelity 4): the flow it starts from is already the answer to within the
tolerance, and the window is what remains. The duct's friction reads 14.257 warm and 14.244 cold
at fidelity 4 (14.227). The plate exchanger is measured with one population buffer, below.

**One population buffer, and 16-bit storage (item 3).** The lattice has one set of populations,
read and written in place (decision 22; `stokes/fast/inplace.py` has the layout, the proof that
no two cells share a place, and a numpy reference the tests hold against plain streaming). A
link has two places; in an even step each of its two cells reads what arrives from one and
writes what it sends to the same one, and in the next step the two places have swapped roles, so
what was written is what the neighbour reads. Everything the two-buffer step did is kept, term
for term:

26. **A link to a wall or a port keeps one place that never swaps.** An untouched solid cell
    would bounce back by itself (Geier and Schoenherr's implicit bounce-back), but a fluid's wall
    may be another fluid's cell, which computes too, so each side keeps its own place for the
    link. It holds what the cell sent to the wall. Where the interpolated wall is nearer than
    half a cell the returning population is completed on reading, with the one arriving from the
    fluid side, as before. Where it is farther, the second population it needs is one the cell
    itself sends in the same step, which in-place streaming has moved on by the next step; so
    the returning population is completed when it is stored, and what it adds to the cell's mass
    comes off the rest population there (decision 15's mass fix, moved from the read to the
    write for these links). Inlet and outlet links read the kept population as they did; the
    outlet's velocity, the samples and the plane come from the velocity buffer, not from the
    populations. A re-classification in place (a model edit) may read a link's place under the
    other rule for one step at the cells whose walls moved; that was not measured apart from the
    edit's own transient.
27. **`storage: "f16"` halves the populations** (`fast.start`; the panel's Storage choice):
    half precision scaled by 2^15 through the adapter's `shader-f16` feature, arithmetic in f32.
    Without the feature the run stays in f32 and says so (`fast.no_f16`): packing two halves in
    one word (`pack2x16float`) cannot be used, because under in-place streaming the two halves
    of a word belong to two cells that write at once. Lehmann et al.'s custom `FP16C` cannot be
    built in WGSL either (there is no 16-bit integer to hold its bits), and was not needed.
    **The cell cap is what one buffer holds**: the adapter's largest binding over 76 bytes a cell
    in f32, 38 in f16, and never more than 64 million. On this card (2 GiB binding) that is
    28.2 million cells in f32 and 56.5 million in f16, where the fixed cap was 8 million; a cell
    takes 108 bytes of GPU memory in all (70 in f16), where two population buffers made it 184.
28. **A 16-bit store rounds at random, in proportion.** Rounded to the nearer 16-bit number,
    the duct's friction read 2.5 % low and its outflow 1 % over its inflow: a steady flow stores
    the same value at the same place every step, so the same rounding error is made every step
    and adds up. The store now rounds up or down with the odds of where the value lies between
    its two neighbours, so the stored value is right on average. **The draw is a hash of two
    numbers: the place, and the number of steps the lattice has taken.** A run of steps is one
    compute pass that reads its params from a buffer written before it, so the step's number
    cannot come from the params alone: the device keeps a table of 2,048 step numbers and the
    step's bind group reads its own at a dynamic offset, and the params hold the step count at
    the table's first block. A pass starts at block 0 or 1, whichever has the parity of the
    steps taken (the block's lowest bit is the step's parity), and a run longer than the table
    is split into passes. The count is multiplied by an odd constant before it meets the
    place, which maps the 2^32 counts one to one: no two of 2^32 steps draw the same field.
    (As first built, a batch cycled through sixteen seeds, each place reusing eight of them
    per parity some 125 times in a 2,000-step batch, and the text said "every step". The
    first correction keyed the draw on the step's number in its batch and the batch's number,
    each multiplied and the two combined: two such pairs can combine to the same key, and an
    emulation by the independent check found them doing so at the birthday rate, about 500
    times in 1,000 batches, each a field of draws repeated. The single count cannot.) The rounding is done
    in f32 arithmetic and so does not depend on how a GPU converts f32 to f16. This is ours
    (stochastic rounding is a standard technique; it is not in Lehmann et al.).

**In place against two buffers** (the acceptance was 1e-5 for f32 and 1 % for f16; the same
steps, 40,000 on the duct and the pipe, 30,000 on the turned duct and the ball; differences
are relative to the two-buffer lattice of the commit before):

| Case | Two buffers | In place, f32 | In place, f16 | f16 rounded to nearest (not kept) |
|---|---|---|---|---|
| Square duct, fRe | 14.23366 | +4e-8 | +6e-5 (14.2346) | -2.5 % |
| Square duct, pressure drop | 1.99551e-4 m2/s2 | -1e-7 | +3e-5 | -2.7 % |
| Pipe, fRe | 15.98624 | -5e-8 | +2e-5 | -3.4 % |
| Duct at 30 degrees, pressure drop | 1.25277e-4 m2/s2 | -1e-7 | -1e-6 | +1.8 % |
| Ball in a box, pressure drop | 9.07933 mPa | +3e-9 | -5e-5 (9.0788 mPa) | -0.8 % |
| Outflow over inflow, duct | 1.000006 | the same to 2e-8 | 1.000004 | 1.0099 |

(The ball reads 9.08 mPa here against the 9.17 mPa of the walkthrough because this harness takes
air as 1.516e-5 m2/s and 1.204 kg/m3, not the material library's figures.)

**Speed**: steady-state throughput, in million cell updates a second over every cell of the grid.
Each figure is the steps finished in a ten-second window that starts after the 2,000-step ramp
and a warm-up, each batch waited for, on an idle GPU; "two buffers" is the code before
round 4. The laminar cases are two relaxation times, the turbulent duct the regularised collision
with Smagorinsky at a cell Reynolds number of 156 (78 at fidelity 4).

| Case (cells) | Two buffers | In place, f32 | In place, f16 |
|---|---|---|---|
| Square duct, fidelity 2 (0.50 M) | 2,020 | 2,840 | 3,140 |
| Square duct, fidelity 3 (1.57 M) | 1,930 | 2,060 | 2,630 |
| Square duct, fidelity 4 (3.57 M) | 1,890 | 2,020 | 2,660 |
| Ball, fidelity 3 (0.40 M) | 1,820 | 2,970 | **2,380** |
| Turbulent duct, fidelity 2 (0.25 M) | 3,570 | **3,330** | **2,550** |
| Turbulent duct, fidelity 4 (1.79 M) | 1,850 | 2,080 | 2,520 |

(The table first printed here, 2,240 to 5,800, timed 500-step batches from a harness that waited
for the GPU once a timing, and was not a steady-state measure; the pipe and the turned duct were
not re-measured and are dropped.) **The figures vary by about 10 % from one run to the next**:
an independent re-measurement read the duct at fidelity 4 at 1,890 (f32) and 2,440 (f16), and
the ball in f16 at 2,210, 6 to 8 % under the table; the order of the three columns was the same
in every case, and that is what the statements below rest on. In f32 the one buffer is faster than two on every laminar case,
by 7 to 63 %, and 7 % slower on the turbulent duct of a quarter of a million cells. **f16 is not
a speed option on small grids**: on the ball it is 20 % slower than f32 in place, and on the
small turbulent duct 23 % slower (29 % slower than two buffers), because a grid that small is
bound by arithmetic and the 16-bit store's rounding is arithmetic. It pays in two ways: for the
memory (the cap doubles), and on grids bound by memory traffic: 11 % faster than f32 in place
on the duct of half a million cells, 21 to 32 % from a million and a half cells up, with either
collision.

**The cells it now holds, and the time to the same steady state.** A duct far too long for
fidelity 5 is coarsened to the cap and stepped: 26.6 million cells in f32 (2,080 million updates a
second, 3.0 GiB of GPU memory by `nvidia-smi`, 120 bytes a cell with the device's own overhead)
and 54.0 million in f16 (2,960 million a second, 4.0 GiB, 78 bytes a cell). The coarsening goes
in steps of 5 %, which is why they stop short of 28.2 and 56.5 million.

29. **Sampling a frame takes at most a fifth of the worker's time** (`SAMPLE_SHARE`). The square
    duct at fidelity 5, which the old cap coarsened to 7.6 million cells, is 11.6 million now, and
    a frame of it costs 0.09 s to read and mesh: at ten frames a second the lattice stepped 34
    times a second of the 146 it can. The next frame now waits until the last one's cost is a
    fifth of the time since (two frames a second on that duct, 131 steps a second; five to seven
    frames a second and 10 to 19 % more steps on half a million to a million and a half cells).
    This was there before round 4; the larger grids made it the largest cost.

The square duct at fidelity 5, `fast.start` to the first steady frame:

| Lattice | Cells | Start | Seconds | Steps on the fine lattice | Pressure drop |
|---|---|---|---|---|---|
| two buffers, f32 (the commit before) | 7.57 M (coarsened at the 8 M cap) | cold | 384 | 35,070 | 0.195845 Pa |
| two buffers, f32 | 7.57 M | warm | 131 | 10,170 | 0.195846 Pa |
| in place, f32, frames at ten a second | 11.56 M | warm | 420 | 11,700 | 0.200312 Pa |
| in place, f32 | 11.56 M | cold | 319 | 40,420 | 0.200211 Pa |
| in place, f32 | 11.56 M | warm | 99 | 11,740 | 0.200311 Pa |
| in place, f16 | 11.56 M | warm | 75 | 11,740 | 0.200313 Pa |

The pressure drops of the two grids differ because their cells do (0.121 and 0.104 mm), not
their storage: f32 and f16 agree to 1e-5 on the same grid, cold and warm to 5e-4 (both inside
the steady rule's 1 %).

**The plate exchanger, after round 4: still not to be read.** (Reference as in the third
addendum: 2,512 Pa on the converged finer solve; 117.5 W hot and 107.1 W cold on the solved case.
The fluid box's smallest side is 14 mm with the ports, so fidelity 4 is 3.2 million cells of
0.219 mm, 5.5 cells across the throat, and fidelity 5 is 10.4 million of 0.146 mm, 8.2 across;
the old cap coarsened fidelity 5 to 8 million.)

| Run | What it read |
|---|---|
| Fidelity 4, f32, cold, 200 s | The guard held cells four times and each time the lattice speed was halved and the lattice **started again from rest** (between 10 and 20 s, 20 and 30 s, 50 and 60 s, and 80 and 90 s of the run; the log has a line every ten seconds). Between the third and the fourth restart it read 4,500 Pa with the outflow within 1 % of the inflow (70 to 85 s); after the fourth, 4,750 to 4,900 Pa with the outflow within 0.5 % (110 to 130 s). From about 140 s the guard holds cells again and the pressure runs away (20 kPa at 190 s), as in the third addendum |
| Fidelity 4, f32, warm, 200 s | After a 9.5 s coarse pass the fine lattice starts from the coarse flow with the guard holding 1.2 million cells; the lattice speed is halved within seconds, which starts it from rest. From there on it is the cold run again: four restarts in the same ten-second intervals of the run, 4,515 Pa before the fourth, 4,750 to 4,850 Pa with the outflow within 0.5 % after it (110 to 130 s), running away from about 140 s |
| Fidelity 5, f16, warm, 480 s (10.4 M cells, not coarsened) | Four restarts here too (at about 35, 80, 215 and 405 s). Between the third and the fourth it read a mean of 3,480 to 3,490 Pa with the outflow within 3 % of the inflow and the guard silent (270 to 370 s); at the end, 75 s after the fourth, hot 3,300 Pa and cold 3,920 Pa (+31 % and +56 %), hot outlet 326.2 K (342.6 K solved), heats -381 W and +397 W (3.2 and 3.7 times); never called steady |

**Nothing can be said of the warm start on this case.** Each halving of the lattice speed starts
the lattice from rest (`runner.py` resets it), so the flow the warm start gave was thrown away
within seconds, and the cold and the warm run are the same run from their first restart on.
The earlier text here ("the warm start gains nothing; the fine lattice filled later than from
rest") compared two points on either side of a restart and does not stand. What does stand: a
fidelity-2 lattice of this core has four cells across its passages, and the fine lattice it
started held 1.2 million cells at the guard at once; and a restrictive core, which is what
lowers the lattice speed, gets no benefit from a warm start as long as lowering the speed
means starting from rest. "Within seconds" is as close as the log gives it. Rescaling the populations to the new speed instead of resetting
them is the fix, and was not built.

More cells move the pressure drop towards the solve (from 4,500 to 4,900 Pa, +80 to +95 %, at
5.5 cells across the throat to 3,300 to 3,900 Pa, +31 to +56 %, at 8.2) and do nothing for the
duty, which the temperature's step over-predicts in passages at an angle to the lattice (second
addendum). The run that would put twelve cells across the throat is 34 million cells: it now
fits in 16 bits, and was not run (at the 1,550 million updates a second this case makes with
its temperature, about 45 steps a second). What the plate needs is still the collision and the
temperature's scheme, items 5 and after of the plan, not memory.

**Fluid-only storage (item 4) was not built**; the time went to the first three. Its design, for
the next round:

30. **Indirect addressing over the in-place layout.** A dense `u32` map gives each cell of the
    grid its row in the population buffer, or none; the rows are the fluid cells and the shell
    of non-fluid cells next to them (a wall link's place for a positive direction is in the wall
    cell's memory, decision 26, so the shell needs rows too). The step reads the map of its
    eighteen neighbours where it now adds an offset. Per grid cell the device then holds the
    flags, the map and the two temperatures (16 bytes: the temperature is solved in the solids
    too and stays dense); per row the populations, the velocity and the stress's mean (96
    bytes, 58 in f16). For a core whose fluids and their shell are half the box that is about
    64 bytes a grid cell against 108 (45 against 70 in f16), and, more to the point, the
    buffer's limit then counts rows: 28 million rows are 56 million grid cells. The cost is
    eighteen more reads a cell (72 bytes of traffic against the 152 the populations make), so
    it is expected to be slower per fluid cell, not faster; Geier and Schoenherr's section 3
    (read) reduces the links to three a cell by chasing pointers, at the price of dependent
    reads. To measure first: the gyroid plate's fluid share of its box, and the speed with the
    map on a dense duct (the worst case for it). None of this was measured.

### Round 4: corrected after an independent check (2026-11-23)

An independent verification of the branch confirmed the gates, the in-place streaming (no race,
equal to two buffers), the warm start's answer and the two closures on the laminar duct, and
found the following. The text above is corrected in place; this is the record.

| Found | What it was | Now | Measured |
|---|---|---|---|
| Host memory at the new cap | `Lattice.reset` built nineteen floats a cell in host memory and uploaded them, whatever the storage, on every start, fidelity change and recovery | the three buffers are cleared on the GPU (`clear_buffer`) | peak working set of the process across one reset: 4,697 MiB to 568 MiB at 26.6 M cells in f32; 5,483 MiB to 994 MiB at 54.0 M in f16; the reset 3.1 s and 2.5 s to 0.01 s. What is left is the grid's flags read back to count the cells (4 bytes a cell, and copies of it) |
| The rounding's seeds | sixteen a batch, reused about 125 times in a 2,000-step batch; the text said "every step" | a hash of the place and of the lattice's step count (decision 28), after a first correction whose two-part key could repeat | against two buffers: duct fRe +6e-5 (it was -5e-5), pipe fRe +2e-5 (+3e-7), turned duct pressure drop -1e-6 (+1.2e-4), ball pressure drop -5e-5 (+1.8e-4); f32 unchanged to the last figure. A first form of the fix, a counter on the GPU moved on by a one-cell dispatch after every step, cost 13 to 18 % of the speed on grids under half a million cells and was replaced by the table read at a dynamic offset, which costs nothing measurable |
| The WALE constant | 0.5 "in common use", the source unread | 0.5, read in a table of Nicoud's group for exactly this form of the model (the research table above); the 1999 paper still not opened | sensitivity: 0.5, 0.55, 0.6 give 2.05, 2.06, 2.12 dynamic heads on the turbulent duct; the laminar duct +0.33 % with each |
| The plate exchanger's rows | hid four restarts from rest, and drew a cold-against-warm conclusion across one | rewritten from the logs; the conclusion withdrawn | - |
| The speed table | 500-step batch timings, not steady state | a ten-second window after the ramp, on the baseline, f32 and f16 | the table above; f16 is slower than f32 in place on the ball (2,380 against 2,970) and on the small turbulent duct |
| The panel at 1600 x 900 | the last status lines ("Steady", "Warm start") were below the fold of a scrolling card | Start, Stop and the status are pinned under the options, which scroll | screenshots at 1600 x 900 and 1280 x 720 |
| A fidelity raised while running | kept the flow and did not say so | the status and the frames have `warmed_from`, and the panel says "Warm start from fidelity N" until the flow is steady, or for ten seconds where it never is (a turbulent preview) | a test, and the walk |
| 130 k cells, then 97 k, for one coarse pass | not two grids: the fluid's cells (97 k) and, before the lattice had counted them, the whole grid's (130 k: 100 x 36 x 36 with the solid and the padding); and for a moment after a new grid was built the status paired it with the old grid's count | the count is dropped when a grid is built and the panel shows the fluid's cells or none | a test |

Not fixed, noted: a reopened project's draft Inlet box in the Boundaries card shows 0.5 m/s where
the inlet is 0.05 m/s (there before round 4, and not Fast mode's).

A second check confirmed these and found six small things, fixed the same day: the seed's key
(above); `Lattice.step` now splits a run longer than its clock's table into passes, where
nothing stopped one; the three restarts from rest (a NaN, the hand-over to the LES, a lowered
lattice speed) clear `warmed_from`, which could go on saying "Warm start from fidelity N" over a
lattice that had started again; a frame carries its own grid's fidelity and fluid cells, so the
panel's header no longer shows the old grid for the second until the next status; a docstring
that had strayed from `holding`; and the speed table's run-to-run spread.

## Addendum 5 (2026-11-24): a seam is not a wall; a fluid in pieces and a runaway are refused

Found in a measured comparison with OpenFOAM on a gyroid block with plenums; both made Fast mode report numbers that
meant nothing, with no error.

**Seam.** The block was a union of a core box ending at x = 9 mm and plenum boxes starting there. A
union is the minimum of the fields, so on the shared face the field is exactly zero (or round-off),
inside the fluid; the classification takes a cell as inside only where the field is negative, and at
fidelity 2 the grid put a plane of cell centres on that face: the fluid was in four pieces, no flow
left, and the pressure climbed past 3.6 MPa. Reproduced on two boxes sharing a face with centres on
it: two pieces at every fidelity. **Decision 31:** a zone's field is read through `phiN`, which,
where the raw field is within `SEAM_TOLERANCE` (1e-3) cells of zero, looks `SEAM_STEP` (1e-2) cells
to either side along each axis and one oblique direction and takes the cell as inside when both
sides of some direction are (`stokes/fast/wgsl.py`). The classification and the step's wall
distances read the same function. A true wall through a centre has the outside on one side and is
unchanged. Now one piece at fidelities 1 to 5, and the flow through the seam is the inlet's.
Considered: shifting the grid off round numbers (moves the problem, and changes every result);
`<=` in the classification (makes every wall through a centre fluid).

**Disconnected fluid. Decision 32:** after each classification every fluid's inlets must reach an
outlet, and its outlets an inlet, through its own cells joined as the lattice's links join them
(faces and edges); otherwise `fast.invalid` with detail `fast.disconnected`, naming the fluid
(`stokes.fast.zones.disconnected`). The pieces are found in numpy (runs along x joined row to row,
labels settled by hooking and pointer jumping; 0.5 s on 7 million cells, the same partition as a
flood fill): scipy stays the explore package's (CLAUDE.md, the import gate). A warm start's coarse
pass that closes a throat is skipped rather than refused.

**Runaway.** The hi-flow case at fidelity 3 (throat cell Reynolds number about 400, tau 0.50027)
jumped from 13 kPa to 132 kPa after about 130,000 steps, outflow 1.1 to 2.1 times the inflow, for
minutes, as valid frames: the guard's speed halving had stopped or was not triggered, and NaN never
came. **Decision 33** (`stokes/fast/health.py`): each frame after the ramp and the settling time is
judged; a NaN, a non-finite figure, a port density more than 0.5 off 1, the guard on more than 1 %
of the cell-steps, or outflow and inflow more than 25 % apart make it not valid (`unstable`, with
`instability` naming the sign; never steady). When half of the last 20 judged frames are not valid
(a NaN at once) the lattice restarts from rest at half the lattice speed; after two restarts, or at
the slowest speed, the run fails with `fast.unstable`; a warm start's coarse pass that runs away goes to the asked-for fidelity instead. A test reproduces a runaway in seconds: a
laminar lattice at a cell Reynolds number of 167 with its hand-over to the LES switched off held
every cell with the guard and ran 13 to 46 % out of balance; it now fails after two restarts (each
made it worse: tau falls towards 0.5 as the speed halves). Not done: catching the instability
before it starts (the throat's cell Reynolds number, not the inlet's, deciding the LES and the
fidelity), and the thresholds are set from these two cases only.

**Corrected after an independent check (2026-11-24).** The rule above failed a stable flow: a
60 x 10 x 10 mm duct with a plate and a 2 mm hole at x = 50 mm, nu 1e-4, 0.02 m/s, fidelity 3 (hole
Reynolds number about 0.4, the guard never acting) fills with pressure for seconds after the ramp,
its outflow climbing smoothly from 0.4 to 1 of the inflow; every such frame was an `imbalance`, the
run restarted twice and failed in 21 s, where the code before the check was steady at 46 s with
682 Pa. The restarts could not have helped either: its lattice speed was already held by
`NU_LATTICE_MAX`, so halving the asked-for speed replayed the same lattice. **Decision 34:** an
imbalance past 25 % is a sign only with the guard holding cells in the same frame, or when it is
not dying away (`stokes.fast.health.settling`: it swung through balance and out again, or its
largest value over the newer half of the last 20 judged frames is no smaller than over the older
half); in a creeping flow (inlet cell Reynolds number below `CREEPING_REYNOLDS`, 1) only when both
hold, since the guard there is a restrictive core's, which the speed halving answers. And a runaway
is restarted only when halving the speed lowers the lattice speed the lattice runs at; otherwise
the run fails at once. Measured on the new rule: the laminar orifice is steady at 29 s (dp 680 Pa,
out/in 0.994, no frame unstable); at 0.05 m/s it is steady with no frame unstable (it had 46); the
orifice ducts at 0.1 m/s (5 mm hole, fidelity 3) and 0.05 m/s (2.5 mm hole, fidelity 4) still fail
with `fast.unstable`, and the forced-laminar duct still fails after two restarts. Considered: a
window scaled to the flow-through time (a fill-up can take several; the trend says it directly),
and keeping imbalance alone with a higher threshold (the fill-up started at 60 % out).
`tests/test_fast_safety_gpu.py` has the laminar orifice, which must reach steady.
