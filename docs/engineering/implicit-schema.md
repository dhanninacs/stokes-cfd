# Implicit geometry: the contract (ADR-0035)

What is built today (slice 1) and how its pieces talk. Decisions and their reasons are in
`docs/adr/ADR-0035-implicit-geometry.md`; this file is what code and clients can rely on.

## Operation types (slice 1, ADR-0036's modelling tools, and the lattice)

Eight operation types under `/geometry/operations`, created and edited with the existing
`operation.*` commands. Their outputs are parts with `family: "implicit"`, `derived`, `units: "mm"`,
`faces: 1` (one face per part until slice 2) and a display mesh. Parameters are quantities in SI.

| Type | Inputs | Parameters |
|---|---|---|
| `implicit_primitive` | 0 | `kind` (`box`, `rounded_box`, `cylinder`, `cone`, `sphere`, `torus`, `capsule`, `half_space`), `size` (a box's edge lengths), `radius`, `length`, `top_radius` (cone), `tube_radius` (torus), `corner_radius` (rounded box), `position` (the centre, or a point on a half-space's plane), `axis` (`x`, `y`, `z`), `normal` (`+x` … `-z`, a half-space's outward normal) |
| `implicit_transform` | 1 | `kind` (`translate`, `rotate`, `mirror`, `scale`), `offset`, `axis`, `angle`, `centre`, `factor` |
| `implicit_boolean` | ≥ 1, plus `tools` | `op` (`union`, `intersection`, `difference`: the union of the inputs minus the tools), `tools` (part ids), `blend_radius` (0 for sharp; above 0 rounds every seam with a polynomial smooth minimum of that radius) |
| `implicit_sketch` | 0 | `profile` (`rectangle`, `rounded_rectangle`, `circle`, `ellipse`, `slot`, `polygon`, or `custom` for a drawn one), `entities` (custom: lines, three-point arcs, circles and Catmull-Rom splines in the plane, metres, at most 64 KiB), `plane` (`xy`, `yz`, `zx`), `origin`, the named profiles' sizes (`width`, `height`, `radius`, `corner_radius`, `slot_length`, `sides`), `method` (`extrude`: `depth`, `symmetric`; `revolve`: `angle`, and `axis_distance` for a named profile; a drawn one revolves about its own v axis) |
| `implicit_split` | 1 | `normal` (`+x` … `-z`), `position` (a point on the plane). Two outputs: the side behind the normal (`below`), then the side in front (`above`); a plane outside the body is refused |
| `implicit_offset` | 1 | `kind` (`offset`: grow by `distance`, shrink when negative; `shell`: a wall of `distance` inside the surface, or outside with `outward`). Refused on a body whose field is not a distance (a lattice, an ellipse) |
| `implicit_pattern` | 1 | `kind` (`linear`: `count` copies `pitch` apart along `axis`; `polar`: `count` copies about `axis` through `centre`, over a full turn or over `angle` from the first to the last), at most 1000 copies |
| `implicit_lattice` | 1 | `kind` (`gyroid`, `schwarz_p`, `diamond`), `form` (`sheet`: a wall of mean `thickness` on the surface, with a channel network either side; `network`: one side of the surface, `bias` in -0.8 to 0.8 moving it, 0 being half the volume), `cell` (the period along every axis), `origin` (a point where a cell starts). The output is what of the lattice lies inside the input body |

*Lattice (2026-11-19):* the triply periodic field is divided by its mean slope on the surface (gyroid
1.525, Schwarz P 1.306, diamond 1.489 per radian, measured on a 160^3 grid), so its values are about
millimetres and a sheet's wall is its `thickness` on average (thinner and thicker by up to about
15 % over a gyroid or diamond cell, 30 % over a Schwarz P cell). Its metric is `scaled`, so it
cannot be offset or shelled (`implicit.not_a_distance`): the wall thickness is the lattice's own
parameter. A sheet's thickness must be below a third of the cell, and a body takes at most 48
cells along its longest side. It takes one input (the body it fills), not the bounding box ADR-0035's
table first proposed: the card is opened on a picked body, and a box is one of the shapes.

A drawn profile's entities must chain into closed loops that do not cross; loops inside loops are
holes (even-odd). Each loop's field is a union of convex pieces, each the largest of its edges'
half-planes, and a whole circle is the exact circle; holes are subtracted with `max` (ADR-0036,
addendum of 2026-11-14). The sign is exact and the value a bound, and every face of a pull is a
plane, so it meshes to flat faces and straight edges (`stokes.implicit.profile2d`). An open chain is
`implicit.open_profile`, a crossing `implicit.self_intersecting`. A split's outputs are addressed by
index: a recipe that reads one carries `"output": 0 | 1`.

*Addendum (2026-11-17):* a drawn arc keeps its exact circle, as a whole circle does. Its field is the
polygon through the loop's ends (each arc replaced by its chord) with each arc's circular segment
added where the arc bulges out and removed where it bulges in, the segment being the arc's disc
intersected with the half-plane on the arc's middle-point side. A curved face is then one smooth
circular face; before, the arc was a polyline and drew as a row of facets (ragged on screen). The
polyline is kept for the open-chain and crossing checks, loop building and nesting. Splines remain
polylines.

Which parameters each kind reads: box `size`; rounded box `size`, `corner_radius`; cylinder and
capsule `radius`, `length`, `axis`; cone `radius`, `top_radius`, `length`, `axis`; sphere `radius`;
torus `radius` (the ring), `tube_radius`, `axis`; half-space `normal` and `size` (it is a block of
that size on the inner side of the plane, so it stays bounded). Every kind reads `position`.
The forms show only the parameters the chosen kind reads (`web/src/state/implicitFields.ts`).

Implicit operations read implicit parts only (`geometry.op_invalid` otherwise), and every B-rep
operation, the B-rep check and the STEP export refuse implicit parts (`geometry.mesh_only`).

## In the app

**Create** opens the build ribbon along the bottom of the viewport, from the viewport toolbar
(first button), Guided mode's Geometry card ("Create shape"), the empty project's hint ("Create
shape…"); the same tools are in the Create menu and the palette (`create.sketch`, `create.shape`,
`create.transform`, `create.pattern`, `create.combine`, `create.modify`; no default chords). Six
groups (`web/src/app/createEntries.ts`, icons in `web/src/app/buildTools.ts`):

- **Sketch**: Sketch… (the 2D sketcher, below), then the named profiles: Rectangle, Rounded
  rectangle, Circle, Ellipse, Slot, Polygon. Always available.
- **Shape**: Box, Rounded box, Cylinder, Cone, Sphere, Torus, Capsule, Half-space. Always available;
  a shape reads no part, whatever is picked.
- **Transform**: Move, Rotate, Mirror, Scale, on one picked body made with Create.
- **Pattern**: Linear, Polar, on one picked body.
- **Modify**: Offset, Shell, Lattice (gyroid by default; the card offers Schwarz P and diamond, as a
  sheet or a network), on one picked body.
- **Combine**: Union, Subtract, Intersect, on two or more picked bodies made with Create, and Split
  (by a plane) on one. The first picked body is the input and the others are the tools, so
  Subtract takes the later picks away from the first. In the tree, Ctrl-click adds a body to the
  pick.

An imported body in the pick disables every tool that reads a body, with the reason on the item.

**The sketcher** (`web/src/sketch/`) covers the viewport while it is open (the ribbon steps aside).
It draws on the XY, YZ or ZX plane in millimetres: Line (a chain; clicking its first point closes
it), Rectangle, Circle, three-point Arc (the two ends, then a point on it), Spline (double-click or
Enter ends it) and Polygon (centre, then a corner; the sides are set beside the tools). Points snap
to the ends, midpoints and centres of what is drawn, then to the grid, which follows the zoom; the
cursor shows its coordinates and the length or radius being drawn. Select picks entities and drags
points (joined ends move together); Delete removes, Ctrl+Z undoes, the wheel zooms about the cursor
and a right-drag pans. The table on the right lists every entity with editable coordinates. The
status line says how many closed loops there are, or marks the open ends and the branch points in
red; Finish is offered only on closed loops. Finish on a new sketch opens the `implicit_sketch`
card with `profile: custom` and the entities (metres), to choose pull or revolve; on an existing one
it sets `plane` and `entities`, which re-runs it and everything built from it. **Edit sketch…**
reopens it, from the card, from the sketch's Details page or from the page of a body it made.

`web/build_sketch_gui.mjs` drives all of it in a browser (draw an L with a round end, pull it,
split it, shell one half, offset the other, a polar pattern, reopen the sketch) and
`web/build_elbow_gui.mjs` builds the mixing elbow from the named tools. Each leaf
opens the ordinary operation card with the kind chosen. The card and the Details page show only
the parameters the kind reads (`web/src/state/implicitFields.ts`): a sphere's radius, a cone's two
radii and length, a rotation's axis, angle and centre. Changing the kind changes the fields at
once. A change in Details re-runs the operation and everything built from it, and Undo reverts
it. The tree's value column says the kind and its main value ("sphere · 0.008 m", "difference").

The display mesh is shaded smooth within regions that meet below 30°, and the narrow triangles
dual contouring leaves along a sharp edge are shaded with the face they belong to
(`stokes.geometry.worker_main.implicit_faces`), so edges stay sharp without dark ticks. A region
of at most four triangles is merged only when it is a sliver: narrower than a quarter of the
mesher's cell (width = 2 × area / perimeter), or narrower than a cell with its normal within 30°
of the bisector of two triangles across its border (a facet between two faces). A small flat
region that is neither, such as a 10 mm port cap the simplifier left as four triangles, stays a
face of its own that a tag can name (ADR-0040 asked for this; round ports are no longer needed).
On a body thinner than the coarse first look's cell the coarse pass shows more face ids than
before; the display pass is unchanged. A one-cell
sawtooth can still be seen along a sharp curved edge (the rim of a hole) when zoomed in far: the
vertices there follow the cells, which slice 4's resolution rule and later feature work address.

On 2026-11-17 a step was added before the sliver merge. The feature-angle split links two triangles
whenever they meet below the feature angle, so a flat face can be joined to another through a curved
surface that crosses it at a shallow angle, and the two come out as one face. Before the merge,
`stokes.geometry.faceleaks.separate_flat_faces` takes such flat faces apart again. A flat region is
a set of triangles whose normals agree with their neighbour to 0.1 deg (`FLAT_LINK_DEG`) and with
the region's mean to 0.5 deg (`FLAT_SPREAD_DEG`), that is at least two cells wide
(`FLAT_WIDTH_CELLS`), and whose border mostly ends in sharp edges (under half of it runs on
smoothly, `SMOOTH_BORDER_SHARE` = 0.5). Each such region becomes a face of its own, and the curved
rest is split again by the feature angle; the narrow strips left over go to the sliver merge.
Measured on a 20 x 10 x 10 mm block joined with a 6 mm sphere, the block's +y, -y, +z and -z faces
and the sphere came out as one face of 15,272 triangles, although the four flat faces meet each
other at 90 degrees; they are now separate faces.

## How a run works

1. `stokes.geometry.opplan.build_plan` gives each implicit input a **recipe** instead of a B-rep
   spec: `{"type", "params" (SI), "inputs": [recipe…], "tools": [recipe…]}`, built from the tree
   (`implicit_recipe`). Nothing of an implicit part is stored on disk except its meshes; its field
   is rebuilt from the operation nodes every time.
2. The worker's `_implicit_job` (`stokes.geometry.worker_main`) turns the recipe into a body
   (`stokes.geometry.operations.implicit.body_of`), in millimetres. The graph is written in a
   frame centred on the body's box (decision 6), checked against the limits and written as text.
3. `stokes.implicit.runner.mesh` meshes it with `stokes-implicit-mesh` at the display cell: the
   largest side over 192 cells, or a quarter of the smallest feature if that is finer, never finer
   than the largest side over 1024 cells.
4. The worker sends `shape` (with `family: "implicit"`) and the display mesh, as every operation
   does; the part's version is the run's seed, as for B-rep operations, so tags on an implicit
   part go stale on a parameter change until slice 2's basis rule (decision 12).

## The mesher

`native/implicit-mesh` (see its README). Command line:

```
stokes-implicit-mesh --model FILE --centre X Y Z --half H --depth D --out FILE
                     [--simplify TOL] [--angle DEG] [--threads N] [--progress]
```

Output: little-endian `STKIMSH1`, u32 vertex count, u32 triangle count, f32 positions, u32
triangles, u32 label per triangle (0 until slice 2). Stdout: JSON lines, progress
`{"stage", "done", "total"}`, then `{"stats": {vertices, triangles, welded, open_edges,
nonmanifold_edges, folds, folds_raw, unfold_removed, unfold_flips, unfold_moves, unfold_collapses,
simplified, seconds}}` or `{"error", "message"}`. `folds` is what is left after fold removal
(decision 18), zero on every body measured.

The display mesh is shaded smooth inside the regions that meet below 30° and sharp across the
edges between them; the same regions (`implicit_faces`, above) are the part's faces until slice 2's
faces by key, numbered afresh on every run.

The runner finds it through `STOKES_IMPLICIT_MESHER`, then `stokes/implicit/bin/`, then
`native/implicit-mesh/bin/`; a `.py` path runs under the current interpreter (the tests' reference
mesher, `tests/implicit/reference_mesher.py`). Meshes are cached in `<project>/cache/geometry/implicit`
by content (program hash, text version, text, cube, depth, simplification), pruned to 4 GiB.

## Error codes

| Code | When |
|---|---|
| `implicit.invalid` | a constant that is not finite or beyond ±1e9 mm, an unknown operation, a dimension that is not positive, an intersection of bodies that do not overlap |
| `implicit.open_profile` | a drawn profile with an open chain or a branch point; the details list the points |
| `implicit.self_intersecting` | a drawn profile whose entities cross |
| `implicit.not_a_distance` (in `details.code` of `implicit.invalid`) | an offset or a shell of a body whose field is not a distance |
| `implicit.too_large` | more than 100,000 graph nodes |
| `implicit.unbounded` | the body has no finite box (a half-space not intersected with a bounded body) |
| `implicit.too_fine` | the cell needs an octree deeper than 15; the details give the finest cell that fits |
| `implicit.mesher_missing` | the program is not built or installed |
| `implicit.mesher_failed` | the program failed, timed out (30 min) or wrote a file that fails the checks |
| `implicit.cancelled` | the run was cancelled |

Inside an operation these reach the user as `geometry.op_invalid` (or `geometry.op_failed` for a
cancelled or empty run) with the implicit code in `details.code`.

## Shape optimisation commands (ADR-0038, ADR-0040, ADR-0041)

Parts made with Create are what the shape optimiser moves. Values are SI throughout.

| Command | Parameters | Result / events |
|---|---|---|
| `shape.variables` | `part` | `{variables: [{reference, label, body, quantity, value, unit, lower, upper, node, name}]}`: the continuous quantities that shape the part and the project parameters their expressions read |
| `shape.optimise` | `part`, `variables` `[{reference, lower, upper}]`, `objective` (`volume` \| `pressure_loss` \| `flow_split`), `target_volume` (volume), `gradient` (`adjoint` \| `finite_difference`; the flow split defaults to finite differences, and its adjoint runs `adjointFoam` (ADR-0041): refused as `solve.adjoint_missing` before any meshing when the host has none, and refused when an outlet is left out of the split), `fd_step` (per variable; 10 % of the span), `target` (flow split: max \|Qᵢ/Q − 1/N\|, 0.01), `outlets` (flow split: the boundary names of the pressure outlets to share among, at least two; default all), `method`, `max_cycles`, `noise_floor`, `wait` | in the background: `shape.step {part, run: "optimise", step, cycle, step_number, steps, elapsed}`, `shape.probe` (each finite-difference solve), `shape.cycle {index, x, objective, gradient, gradient_norm, source, seconds, detail}` (a flow split's `detail` carries `fractions` per outlet, `deviation`, `total`, `flows`, `mass_balance`), `shape.finished {part, status, result}`; `result` adds `gradient`, `target`, `outlets`, `improvement`, `significant`, `noise_floor` to the cycles and `best`. Status: `converged`, `target_reached`, `gradient_disagrees`, `max_cycles`, `cancelled`, `failed`. An adjoint row's `detail` says `gate` (`verified` \| `unchecked`), `unverified` and `sensitivity_source` (`solver`: the adjoint program's written `shapeSensitivity`; `reconstructed`: Stokes' reconstruction from U and Ua; ADR-0041 decision 8); a flow-split adjoint row also carries the split's `fractions`, `deviation`, `flows` and the solver's `pressure_loss` |
| `shape.check` | `part`, `parameter` or `parameters`, `objective` (`pressure_loss` \| `flow_split`), `outlets` (flow split), `step`, `noise_floor`, `wait`. The flow split's adjoint is compared when the host has `adjointFoam` and every outlet is in the split, else the check is the finite differences alone (`source`) | 2N + 1 runs (`shape.step` with `run: "check"`), then `shape.checked`: `verdict` (`passed`, `failed`, `inconclusive`, `no_adjoint`), `passed`, `cosine`, `wrong_sign`, `gradients` per quantity (`finite_difference`, `forward`, `backward`, `consistent`, `cause`, `suggested_step`, and with the adjoint `adjoint`, `ratio`, `covered`), `suggested_step` when inconclusive; with the adjoint, `sensitivity_source` (`solver` \| `reconstructed`) |
| `shape.gradients` | (none) | `{objectives: {volume, pressure_loss, flow_split: [sources]}, defaults, adjoint_program: {host, program, found, why}}`: the gradient sources each objective may use on the host a cycle runs on; the flow split's adjoint only when the cached probe finds `adjointFoam` (ADR-0041) |
| `shape.cancel` | `part`, `now` | stops after the cycle under way, or (`now`) cancels its mesh or run |
| `shape.history` | `part` | the last optimisation and the last check of the part in this session |
| `shape.apply` | `part`, `values` `{reference: SI value}`, `wait` | sets the values (`parameter.assign`, one undo step), runs the implicit operations that make the part again in tree order, then finds each boundary tag's faces on the rebuilt part (`stokes.geometry.retag`: a planar tag by its plane, coplanar ones by the nearest tagged centre, one non-planar tag as the rest) and writes them with `tag.rematch`, so no tag is stale. `wait` false (the default, and the only choice on the server's event loop) returns `{part, started, rebuilding}` and ends with `shape.applied {part, status: "done", rebuilt, tags: {name: faces}}` or `{part, status: "failed", error, code}`. Journaled with `wait` true, so a replay rebuilds and re-finds before the next record; in the background the `tag.rematch` calls are journaled after it as well and replay to the same faces |

**The wall sensitivity** comes from the agent's `job.sensitivity {job, nu, walls, ports, time,
adjoint}`, which answers `{time, objective, flows, source, patches, sizes, centres, normals, areas,
values}` (per wall face, patch by patch). `source` is `solver` when the solution time holds the
adjoint program's written `shapeSensitivity` (a `volScalarField`, m²/s³, with face values on
every wall patch asked for: `adjointFoam` writes it, Foundation's `adjointShapeOptimisationFoam`
does not) and `reconstructed` when the agent computed s = ν (∂U/∂n)·(∂Ua/∂n) from U and Ua; it is
null for a run without an adjoint. The two never mix within one answer. An agent from before
ADR-0041 decision 8 sends no `source`; the desktop then labels the row `reconstructed`.

A part with no boundary tags is applied and rebuilt with nothing to re-find. A boundary plane the
values moved away (a port that left its plane) fails the apply with `geometry.op_invalid` after
the values are set: undo restores them.

## Slice 1 deviations from the ADR, to settle in later slices

- **Hidden outputs are meshed.** Decision 8 says an output that is only another operation's input
  builds its graph but is not meshed; slice 1 meshes every output, at display resolution.
- **Fold removal came forward from slice 4** (decision 18), at the owner's request (2026-11-11:
  "smooth and no folds"). Fidget's dual contouring folds about a tenth of a sphere's triangles and
  leaves flaps along every sharp edge; the port gives the same vertices and triangles, byte for
  byte, as the earlier program, so they are the mesher's. The mesher now removes them
  (`native/implicit-mesh/README.md`, "Fold removal"): 198,870 of 198,870 on 36 synthetic bodies
  at four cell sizes. A fold left over is a warning on the run; decision 18's "error for every
  mesher" waits for the meshing path of slice 4.
- **The tests** run on the built program when `native/implicit-mesh/bin/` has it, else on the
  reference mesher (`tests/implicit/reference_mesher.py`); `tests/test_implicit_mesher_binary.py`
  needs the program and skips without it.
- **The kill-on-close Job Object** is a copy of the agent's in `stokes.implicit.runner`, because
  `stokes.implicit` may not import `stokes_agent`; a shared helper would remove the copy.
- **The `atan2`, `div`, `sin` and `cos` text names** are not used by slice 1 builders and are to be
  checked against Fidget's parser when the program is first built.
