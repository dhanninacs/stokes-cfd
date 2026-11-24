# ADR-0036 · A 2D sketcher and the direct-modelling tools of the implicit modeller

**Status:** proposed · 2026-11-12 · builds on ADR-0035 (implicit CAD, the in-app modeller); brings
forward its slice 3 (blends, offsets and shells, patterns) and replaces its "free polygon and spline
sketches wait for their own ADR" (decision 7) with the decisions below.

## Context

The owner asked (2026-11-11/12) for geometry creation in the manner of a direct modeller such as
SpaceClaim: a ribbon of tools, a fully featured 2D sketcher whose profiles are pulled or revolved
into solids, splitting a body by a plane, and the other everyday tools, enough to build a part such
as the two-inlet mixing elbow from scratch in the app. ADR-0035 gave the implicit modeller named
profiles only (rectangle, circle, …), primitives, transforms and sharp booleans. Its decision 7 left
free sketches for later because a free polygon needs a structured parameter and a field whose sign
is right inside a non-convex profile.

The design reference is the organisation of a direct modeller's design tab (sketch tools, then pull
and move, then intersect tools such as combine and split, then create tools such as shell, offset
and pattern), not its look: icons are lucide's, styling is `web/DESIGN.md`'s.

## Decision

### Sketches

1. **A sketch is a set of entities on a sketch plane** (`xy`, `yz`, `zx` through an origin, as
   ADR-0035's named profiles). `implicit_sketch` gains the profile `custom`, which reads the
   parameter `entities`: JSON, coordinates in metres in the plane's (u, v) (the stopgap of ADR-0035
   G20: a json parameter with the dimension fixed by the schema). Entity kinds:
   - `line` `{a: [u, v], b: [u, v]}`;
   - `arc` `{a, m, b}`: the circular arc from a through m to b;
   - `circle` `{c: [u, v], r}`;
   - `spline` `{points: [[u, v], …]}`: a Catmull-Rom curve through the points, at least three;
   - the editor's rectangle, polygon and polyline tools write lines, so the stored form stays small.
   At most 2,000 entities and 64 KiB of JSON.
2. **Profiles are closed loops.** Entities are chained end to end by equal endpoints (within 1e-9 m;
   the editor snaps them equal); a circle is a loop by itself. A chain that does not close, a loop
   that crosses itself, two loops that cross, or a point shared by more than two entities is refused
   (`implicit.open_profile`, `implicit.self_intersecting`). Loops nest: a loop inside an odd number
   of others is a hole (even-odd rule), so a washer is two circles.
3. **The field of a profile is exact in sign and distance.** Arcs and splines are tessellated into
   chords no further than 1e-3 of the profile's size from the curve (and at most 4,096 segments in
   all). For a point p the field is `s(p) · d(p)`: d is the distance to the nearest segment
   (`sqrt` of the smallest squared distance, which is zero only on the boundary itself), and s is
   -1 inside and +1 outside, from the winding number Σ atan2(cross, dot) over the segments
   (oriented outer counter-clockwise, holes clockwise). A loop that is a single circle uses the
   exact circle. The metric is `exact` up to the chord tolerance.
4. **The sketcher is a 2D editor beside the viewport**, not drawing in the 3D view (that, with
   sketching on a picked face, is later work). Tools: Line (chains until Esc or a click on the
   start), Rectangle, Circle, Arc (3 points), Spline, Polygon (n sides), Select (drag points),
   Delete; grid with snapping, snapping to endpoints and midpoints, horizontal and vertical
   guides; a table of the entities with editable coordinates; the profile's closure and errors
   shown live; zoom and pan. There is no constraint solver: dimensions are typed, as a direct
   modeller does. Finish writes the entities to the sketch operation (one undo step); the
   operation card then chooses the plane, pull or revolve and their values.

### Modelling tools

5. **Split by a plane**: `implicit_split` reads one implicit body and a plane (normal `±x|±y|±z`, a
   point) and makes **two** parts, the side behind the normal and the side in front, named
   `<body>_below` and `<body>_above`. Implicit operations may have several outputs: the worker's
   implicit job meshes and sends each, as a B-rep operation's outputs.
6. **Offset and shell** (`implicit_offset`, ADR-0035 slice 3): offset grows or shrinks a body by a
   distance (`φ - t`); shell keeps a wall of thickness t inside (or outside) the surface
   (`max(φ, -φ - t)`); exact on `exact` fields, approximate on `bound` ones (the card says so),
   refused on `scaled` ones.
7. **Pattern** (`implicit_pattern`): linear (a count and a pitch along x, y or z; up to three
   directions at once by chaining) and polar (a count about an axis through a point, over 360° or a
   given angle). A finite pattern is the union of moved copies of the body's graph: the graph is
   shared, so its size grows with the count by a few nodes per copy. At most 1,000 copies.
8. **Smooth blends**: `implicit_boolean`'s `blend_radius` (0 sharp) uses the polynomial smooth
   minimum and maximum, `smin(a, b, k) = min(a, b) - h²k/4` with `h = max(k - |a - b|, 0)/k`, which
   needs no operation beyond ADR-0035 decision 4.
9. **The ribbon** (ADR-0035's build ribbon) has the groups Sketch (the sketcher and the named
   profiles), Shape, Transform, Pattern, Combine (union, subtract, intersect, split) and Modify
   (offset, shell).

## Alternatives considered

| Alternative | Why not |
|---|---|
| A constraint-solving sketcher | A solver (and its UI) is a project of its own, and a direct modeller works with typed dimensions. Entities stay plain coordinates, so a solver can be added over them later. |
| Non-convex profiles as a CSG of convex pieces | Pieces that share an edge give a zero of the field inside the material (the touching-bodies problem of ADR-0035 decision 19), and the decomposition is fragile. |
| The sign from a crossing count with `compare` | Ties at vertex heights; the winding number is exact away from the boundary with the operations we already have. |
| Exact distance to arcs and splines | Arcs need the angle test of their span and splines a root find; tessellation to 1e-3 of the size is below the display and meshing cells. |
| Sketching in the 3D viewport now | Needs picking on a plane, a 3D grid and snapping in view space; the 2D editor gives every tool now and the 3D one can reuse its model. |

## Consequences

- A sketch with thousands of segments has a large graph (about 25 nodes per segment) and slower
  octree pruning, because the winding sum's interval is wide; ADR-0035's 100,000-node limit holds.
- Split, offset, shell, pattern and blends complete ADR-0035's slice 3 in substance; slice 4's
  meshing path and slice 5's tutorials are unchanged.
- Tests: profile fields against analytic areas and point tests (non-convex, holes, arcs, splines),
  refusals of open and crossing profiles, split volumes summing to the body's, shell and offset
  volumes, pattern counts, blends; the editor's pure model in vitest; the GUI walkthrough sketches
  a profile, pulls it and splits it.

## Addendum (2026-11-13): the sketcher after the live review

The T0 live review of the sketcher found work-losing state bugs and a form-driven flow. Decision 4
stands (a 2D editor over the viewport); within it:

1. **Finish is one command.** `parameter.update` sets several parameters of one node (here the
   plane, dimensions, entities and profile, in that order) as one `Composite`: one journal record,
   one undo step, one rebuild; a refused value leaves every parameter as it was. The four
   `parameter.set` calls it replaces were four undo steps, the first of which changed nothing
   visible.
2. **Rectangles and polygons are one entity in the editor** (`{kind: "rectangle", c, w, h}`,
   `{kind: "polygon", c, r, n, angle}`) and keep decision 1's stored form: each is written as its
   lines, and every one of those lines carries a `shape` record (`kind`, `group`, and the shape's
   own centre and sizes, numbers in metres or expression texts) that the builder ignores and the
   editor reads back. A bound size makes the corners expressions (`(X0) - (W) / 2`), so the server
   sees what it always saw. Lines edited apart from their group read back as plain lines.
3. **One undo entry is the whole sketch** (entities, bindings, dimensions). An evaluation of the
   expressions refreshes the drawn values without an entry.
4. **The sketch owns the keyboard** while open (`claimKeyboard`, `web/src/workspace/keyOwner.ts`):
   the global keymap stands down, and the sketch maps one key per tool and takes typed sizes while
   a draft accepts them.
5. **One length unit in the sketch:** a bare number is millimetres in every field, dimension values
   included (stored as `n [mm]`); angles are typed in degrees.

Still open from the review: constraints beyond the shapes' own, and slot, tangent arc, fillet and
trim tools. **ADR-0039 amends decision 4**: the drawing is registered to the 3D view (the camera
held face-on to the plane), a new sketch starts by choosing its plane, Finish pulls at once with a
default depth instead of opening the card, and the body carries a Pull handle with a client-side
ghost. Sketching on a picked face is still later work.

## Addendum (2026-11-13): the ribbon, the cards and the bodies

Decision 9 changes: the ribbon's **Sketch** group is the sketcher and its drawing tools (Line,
Rectangle, Circle, Arc, Polygon, Spline), each opening the sketcher with that tool chosen
(`SketchSession.tool`); the named-profile forms (Rectangle, Rounded rectangle, Circle, Ellipse,
Slot, Polygon), which drew nothing, are no longer ribbon tools. The named profiles stay a choice of
the sketch operation's own page and of the API. The ribbon folds whole groups into one menu button
each when it does not fit (Shape first, Sketch last), and while it is open `S`, `P`, `M` and `C`
sketch, pull, move and combine (`build.*` actions in a `build` shortcut context that falls through
to the global actions of the same letters when the ribbon is closed).

The modeller's forms show millimetres and degrees whatever the solver's display units, and an
implicit operation's output is named for what it is (`cylinder`, `extrusion`, `revolution`) or
after the body it changes (a transform, offset, pattern or combine keeps the target's name, made
unique; a split's halves are `<body>_below` and `<body>_above`, as decision 5 says). Deleting a
body or an operation that others are built from asks first, names them, and deletes them with it.

## Addendum (2026-11-14): a drawn profile is planes, not a distance

**Decision 3 changes.** The field `s(p) · d(p)` broke ADR-0035 decision 5's builder rule: `d` is
the `sqrt` of the smallest squared distance, which is zero on the boundary, and dual contouring
evaluates the gradient exactly there. Fidget's edge search lands within a few float32 steps of the
surface, where the squared distance often rounds to zero: at points within four float32 steps of a
face of a 60 × 20 mm rectangle, 7.4 % of the gradients were 0/0 (the mesher then snaps the vertex
to that point and cannot merge the cell) and 3.3 % of the rest were more than 1° off, up to 90°
(the QEF then treats a flat face as an edge or a corner). The pull came out with ridges, sawtooth
edges and dozens of feature-angle patches, worst at the coarse first pass (vertices up to 8.7 mm
off a 60 × 20 × 25 mm box at a 0.94 mm cell; 0.45 mm at the 0.31 mm display cell), while the
named rectangle, `max(|u| - a, |v| - b)`, meshed to six exact faces.

Now each loop is a **union of convex pieces** (ear clipping, then Hertel-Mehlhorn merging; a
convex loop is one piece) and each piece the `max` of its edges' half-planes; a hole is subtracted
with `max(outer, -hole)` and the regions are joined with `min`. A loop that is a whole circle uses
the exact circle, as the decision always said. Every face of a pull is then a plane of the graph and
every edge a sharp `max` or `min`, the form the named profiles have. The metric is `bound` (exact
for a lone circle), which the pull and the revolve made it anyway; offset and shell already treat a
pull as approximate.

The alternative rejected in this ADR, convex pieces, was rejected for the zero that two pieces give
on the diagonal they share. Pieces now overlap across each diagonal by 1e-5 of the profile's largest
coordinate or size: a hundred times float32 rounding, so the field is negative on the diagonal, and
a hundredth of the finest meshing cell, so the overlap cannot show. The decomposition runs once per
build in Python (at most 4,096 vertices; numpy for the ear test). Measured after the change: the
drawn 60 × 20 × 25 mm and 40 × 20 × 20 mm pulls give the named rectangle's six faces at both
passes, every vertex within 2e-5 mm of its plane and no triangle more than 0.5° off its face; an L
gives eight; a drawn circle gives the named circle's mesh bit for bit. Arcs and splines are still
chords, now with a crease at every chord end (below the 30° feature angle, so a rounded side stays
one face); exact arcs are later work. Tests: `tests/test_implicit_sketch_faces.py`.

## Addendum (2026-11-17): a drawn arc keeps its exact circle

**Decision 3 changes for arcs.** The arc is no longer a polyline in the field. A drawn arc's loop is
the polygon through its ends (each arc replaced by its chord), plus for each arc its circular
segment: the disc of the arc's circle intersected with the half-plane of its chord on the side of
its middle point. The segment is added (`min`) where the arc bulges out of the chord polygon and
removed (`max` with its negation) where it bulges in; a point halfway between the chord's middle and
the arc's middle point, inside the polygon, tells the two apart, which needs no loop orientation. A
major arc (more than 180 degrees) gets the disc less its small cap, as the same formula gives. The
field is `sqrt` of a squared distance to the centre less the radius, the same form as a whole
circle, so no zero of a `sqrt` argument and no `atan2`.

Why: the arc was a polyline of up to 720 flat facets, and the pulled curved face drew ragged (sawtooth
lines between facets) while a drawn circle drew clean. The polyline is kept where the polyline is
what is needed: the open-chain and crossing checks, loop building and nesting. The shape-derivative
code (ADR-0038) reads the body's field, not the loops, so it sees the exact arc. Splines stay
polylines (decision 3 otherwise stands).
Tests: `tests/test_implicit_sketch_faces.py` (the exact-field tests for a half disc, a concave arc
and a 270 degree arc, and the pulled half disc's four faces).
