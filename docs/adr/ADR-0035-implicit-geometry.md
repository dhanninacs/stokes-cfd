# ADR-0035 · Implicit CAD: the in-app modeller, its path to mesh and run, and optimisation that moves its geometry

**Status:** proposed · 2026-11-11 · relies on ADR-0001 (GPL process boundary), ADR-0003 (CAD kernel), ADR-0012 (units and expressions), ADR-0016 (design exploration), ADR-0017 (shape optimisation), ADR-0019 (many-part rendering), ADR-0020 and ADR-0021 (selections, operations, tags), ADR-0024 (jobs and results), ADR-0025 (native Windows), ADR-0032 (meshing workflow). **Amends** decision D1 and the vision's non-goal "sketch-based CAD modelling" (docs/00, docs/01), ADR-0021 decision 8.4 for one part family, and ADR-0017 decision 3 by adding a second way to use an adjoint. Each amendment is stated where it is made.

## Context

The owner asked on 2026-11-11 for an implicit-CAD modeller inside Stokes: users build geometry in
the app, mesh and run it, and optimise it, with the adjoint among the methods, by changing the
implicit geometry itself.

Stokes builds geometry in two ways today: import (ADR-0014, ADR-0021) and OCCT operations on B-rep
parts (ADR-0020, ADR-0021). Until now it was explicitly not a modeller (D1: "no sketching"; ADR-0020
rejected a feature tree per part). Three things make an implicit modeller a better fit than growing
the B-rep side:

- **Repeated features.** Pin and fin arrays, microchannels, manifolds, lattices and triply periodic
  minimal surfaces (TPMS) have thousands of features. As B-rep every one is a solid and every
  boolean grows with the count. As an implicit body (a field φ, negative inside) a repeat is one
  `mod` in the expression and costs the same at any count.
- **Regeneration.** Every design is a fresh evaluation of one expression. There is no topology for a
  parameter change to break, which is what Explore (ADR-0016) and shape studies (ADR-0017) need.
- **Shape derivatives.** The surface is φ(x; p) = 0, so the movement of the wall under a parameter
  change is −(∂φ/∂p)/|∇φ| along the normal. An adjoint's surface sensitivity therefore maps to
  parameter gradients by one surface integral, with no CAD kernel derivative and no mesh morphing.

**What exists.** The owner wrote a standalone implicit-CAD program elsewhere. Its generic layers are
a Python expression graph with a numpy evaluator, a process runner with a content cache and
cancellation, and a Rust mesher around **Fidget 0.5.1** (Matt Keeter, MPL-2.0): octree with dual
contouring, a weld, an edge check, a planar simplification and progress lines. Its application
layer (one specific part, its parameters, layout, reference data and documents) is fitted to that part
and is not portable (CLAUDE.md rule 3).

**What was measured there** (24-core Windows laptop; indicative only, every number is re-measured on
synthetic parts before it becomes a budget):

- a detail preview takes about 1 s; a closed surface of a plate with about a thousand repeated
  channels takes about 50 s, with no open and no non-manifold edge;
- the mesher's default is Fidget's interpreter, not its JIT: both give the same mesh bit for bit,
  and the JIT cost about 2.8 times the CPU time because it recompiles every simplified tape;
- dual contouring leaves a few folded triangles in thin features (about 0.3 % of the area), which a
  CFD importer's self-intersection check reports.

**What was measured here** (2026-11-11, this host, one thread): the numpy evaluator's kind of work
costs 3.65 ms per graph node per million points. Labelling 15 M triangles of a 1,000-node graph in
numpy would take about 55 s and a 5,000-node graph about 4.5 minutes, longer than the meshing
itself. Evaluating 1 M wall faces of a 1,000-node graph takes 3.7 s.

## Decision

### A. Scope and packages

1. **Implicit CAD is Stokes' in-app modeller.** This amends D1 and the vision's non-goal: Stokes
   models parts, by a feature tree of implicit operations. D2 stands as written for B-rep: OCCT is
   the only B-rep kernel, and STEP, IGES and BREP stay OCCT's. An implicit part is a third part
   family beside `brep` and `mesh` (decision 9); it is a second geometry representation, not a
   second B-rep kernel. There is still no constraint-solving sketcher (decision 7).
2. **A new headless package `stokes.implicit`** holds the expression graph and its validation, the
   numpy evaluator, the feature builders, the face-key table, the mesher runner and the
   shape-derivative numerics. It imports the standard library, `stokes.errors` and `stokes.log`
   at module level and numpy inside functions; it imports nothing else of `stokes` and no Qt, VTK
   or OCP. `stokes.geometry`, `stokes.explore` and `stokes.api` may import it. The layering gate
   (`tools/check_forbidden_imports.py`) gets both rules. The node types live in
   `stokes/geometry/model.py` with the other operation types, and the operation modules in
   `stokes/geometry/operations/` translate node parameters into `stokes.implicit` calls.
3. **API first.** Authoring uses the existing `operation.*` commands; nothing is authored through a
   command of its own. New commands, each with a schema, a test through `Session.execute` and a
   journal replay: `implicit.info(part)` (node count, bounds, smallest feature, metric, face
   table), `implicit.surface(part, quality)` (the meshing surface of decision 20, a background job),
   `implicit.evaluate(part, points)` (φ at up to 1 M points, for checks and agents) and, with
   decision 27, `implicit.gradient`.

### B. The expression graph

4. **The graph is a closed set of operations**: `var-x|y|z`, `const`, `add`, `sub`, `mul`, `div`,
   `neg`, `abs`, `square`, `sqrt`, `min`, `max`, `mod`, `sin`, `cos`, `atan2`. (`sin` and `cos` are
   what TPMS need and `atan2` what a polar pattern needs; the draft's set could express neither.)
   A pure function, `to_text`, writes it in Fidget's text format: shared sub-graphs once, an
   iterative walk, constants as `repr` of a finite float. The graph is never parsed from user text
   and never `eval`ed. A project stores operation nodes, not graphs; graphs are rebuilt from them.
   Limits, checked while building: 100,000 nodes (`implicit.too_large`), constants finite and
   within ±1e9 (`implicit.invalid`), 50,000 faces in the face table (`implicit.too_many_faces`,
   the design size of D4).
5. **Builder rules**, each with a property test:
   - a feature's field changes sign across its surface and nowhere else;
   - no `sqrt` of a quantity that is zero on a surface, because its gradient is not finite there
     and the mesher then misplaces vertices (a sharp corner is written with `max`, a rounded one
     with `sqrt`, and the switch is at a radius of 1e-7 m);
   - affine transforms are composed in float64 in Python and applied at the coordinate leaves,
     never written as chains of `add` and `mul` nodes;
   - every feature reports a bounding box, its smallest dimension, and its **metric**: `exact` (a
     signed distance), `bound` (the sign is right and |∇φ| ≤ 1, as after `min` and `max`) or
     `scaled` (not a distance, as a TPMS field).
6. **Units.** Every operation parameter is a quantity (ADR-0012), stored in SI, so expressions,
   named parameters and studies work on it unchanged. The kernel works in **millimetres**, in a
   frame whose origin is the centre of the part's meshing cube. Millimetres are not chosen for
   precision (f32 is scale-free: the draft's reason was wrong) but because operations already run in
   millimetres with `units: mm` on their parts (ADR-0021, 8.2) and the mesher's absolute thresholds
   were tuned in them. The local origin is what protects precision: a part 10 m from the origin
   would otherwise lose three digits. The scale is one constant in the runner
   (`KERNEL_LENGTH = 1e-3 m`), not a project setting. Conversion to and from SI happens in the
   operation module in float64, and vertices go back to metres when the mesh is read. The octree
   depth is at most 15, which leaves at least 256 f32 positions across a cell.

### C. The authoring model

7. **A part is built by a tree of operations** on the ADR-0020/0021 framework. Eight operation
   types are added to `OPERATION_TYPES`, each with a `kind` enum where it has variants, as
   `primitive` and `boolean` have today:

   | Type | Inputs | Kinds and parameters | Slice |
   |---|---|---|---|
   | `implicit_primitive` | 0 | box, rounded box, cylinder, cone, sphere, torus, capsule, half-space; dimensions and placement | 1 |
   | `implicit_transform` | 1 | translate, rotate, mirror, uniform scale | 1 |
   | `implicit_boolean` | ≥ 1, `tools` | union, intersection, difference; `blend_radius` (0 is sharp) | 1 (blend: 3) |
   | `implicit_offset` | 1 | offset (grow or shrink), shell (wall thickness, inward or outward), round | 3 |
   | `implicit_pattern` | 1 | linear in one to three directions, polar, mirror; pitch and count, finite | 3 |
   | `implicit_sketch` | 0 | a named profile (rectangle, rounded rectangle, circle, ellipse, slot, regular polygon) extruded over a length or revolved over an angle | 3 |
   | `implicit_lattice` | 0 | gyroid, Schwarz P, diamond; sheet or network; cell size, wall thickness or volume fraction, bounding box | 3 |

   *Addendum (2026-11-19, built):* `implicit_lattice` takes **one input**, the body it fills, in
   place of a bounding box of its own (a box is one of the shapes, and the tool is opened on a
   picked body like Offset and Shell). A sheet has a wall thickness; a network has a `bias` that
   moves the surface, not a volume fraction (the relation between the two differs per surface and
   is not exact). The contract is in `docs/engineering/implicit-schema.md`.
   | `implicit_field` | 1 | a free-form offset field in a design box (decision 29) | M8.5 |

   Inputs must be implicit parts (`geometry.op_invalid` otherwise). The B-rep operations refuse an
   implicit part with the existing `geometry.mesh_only` error.
   **Blends and shells are in v1** (slice 3). A blend is the polynomial smooth minimum, which needs
   no operation beyond those of decision 4. An offset, shell or blend is exact where its input's
   metric is `exact`, approximate where it is `bound` (the card says so), and refused for `scaled`
   (`implicit.not_a_distance`): a lattice's wall thickness is a parameter of the lattice.
   **Sketches** are named parametric profiles. A free polygon or spline profile, a viewport sketch
   editor, lofts, sweeps along a path and bend or twist deformations are not in this ADR; a free
   polygon needs a structured quantity parameter the model does not have yet and a field with no
   zero inside the profile, which is a spike of its own.
8. **Operations run in the geometry worker, without OCCT.** A new worker job kind `implicit` builds
   the graph, starts the mesher and returns the mesh through the messages an import sends. The
   worker imports OCP only inside the functions that need it, so this job does not pay for it.
   `auto_run`, the 300 ms delay, cancellation of the run under way, staleness and undo are the
   framework's (ADR-0021, 8.8; G12) and do not change. An operation whose output is hidden (it is
   the input of another operation) builds and validates its graph but is not meshed.
9. **Outputs are parts** with `family: implicit`, `derived`, `units: mm`, `bbox`, `faces` and the
   stable ids of ADR-0021 8.2. An implicit part has no B-rep; what a mesh-only part can and cannot
   do (ADR-0021, 1.3) applies to it, except that its faces come from decision 10, not from a
   feature angle. A fluid volume is modelled directly, as the difference of an enclosure primitive
   and the solids, so `extract_fluid` is not needed for implicit parts.

### D. Faces, tags and identity

10. **Faces come from provenance, by key.** Every operation declares the faces it makes: a box six
    (`x_min` … `z_max`), a cylinder three (`side`, `start`, `end`), a boolean those of its inputs
    plus `blend` when it has a radius, a shell those of its input twice (`outer`, `inner`), a
    pattern or a transform those of its input. A face's **key** is the path of operation ids that
    lead to it plus its role. The face table is the list of keys in tree order; a face id is the
    index in it, so faces stay addressed by (part id, face id) as ADR-0020 decided. All instances
    of a pattern share their faces; tagging one pin of an array is not possible in v1. Faces are
    not split by feature angle.
11. **The mesher labels the triangles.** For each triangle it evaluates the graph at the centroid
    and walks from the root: at a `min` or `max` it follows the active branch, and at a blend it
    takes `blend` where the two inputs differ by less than the radius. The walk ends on a face
    key. This runs in the mesher process, in parallel, before simplification, in a small slice
    interpreter of our own over the same text (about 150 lines of Rust), not through Fidget's
    internal trace. The numpy evaluator does the same walk as the reference implementation, and a
    test compares the two on every tutorial part. **Budget, a perf test: labelling adds at most
    20 % to the meshing time, and under 10 s for 20 M triangles of a 2,000-node graph on 16
    cores.** Simplification never collapses an edge between two labels.
12. **Basis and tags.** This amends ADR-0021 8.4 for `family: implicit`: the part's `basis` is the
    hash of its face table and the text-format version, not of the parameter values. A parameter
    change therefore keeps every tag valid, which is what a study and an optimiser need. A change
    of the tree changes the table; tags are then carried by key equality through the carry step of
    ADR-0021 8.6, exactly and without fingerprints, and faces that no longer exist are listed in
    the operation's report. A face that has no triangle at the current parameter values keeps its
    id and gives a `tag.empty_face` warning.

### E. The mesher

13. **The mesher is a separate Rust program, `stokes-implicit-mesh`**, in `native/implicit-mesh/`
    with its own Cargo workspace and a committed `Cargo.lock`. It is a port of the owner's earlier
    mesher, renamed and scrubbed. libfive in `native/` through nanobind is rejected: see the
    alternatives. It is a program and not a `stokes.native` kernel, because it has no pure-Python
    fallback and must be killable; without the binary the tree still edits, validates and
    evaluates, and meshing raises `implicit.mesher_missing` naming the build step.
14. **What it does**, in order: Fidget's octree and dual contouring; a weld of vertices at the same
    f32 position (never by a tolerance; where a weld would join two sheets that touch along a line,
    those vertices are moved apart by 0.002 cell instead); the labels of decision 11; the edge
    check (every edge used twice, in opposite directions); fold removal (decision 18); planar
    simplification with a stated distance bound, kept only if the edge check still passes. It
    writes one little-endian file: magic `STKIMSH1`, u32 vertex and triangle counts, f32 positions,
    u32 triangles, u32 label per triangle. Progress is JSON lines on stdout, at most five a second,
    and the last line is the statistics. **The same input gives the same bytes at any thread
    count**; a test runs one and N threads.
15. **Interpreter only.** Fidget is built with `default-features = false, features = ["mesh"]`.
    The JIT was slower on the measured parts, and leaving it out removes `dynasmrt` and every
    writable-and-executable page from the process.
16. **Where it runs.** In v1, on the desktop host only, as a child of the geometry worker: argv
    list, explicit `cwd` in a job folder under the project's `cache/implicit/`, a minimal
    environment, a 30-minute timeout, a kill-on-close Job Object on Windows (ADR-0025) and the
    process group on Linux, cancellation by kill. Previews run below normal priority and on all
    cores but one. In M3.5 the agent gains the same binary as an allowed utility with a closed flag
    list, so a design point on a remote host stages the model text (kilobytes) and not a surface
    of up to a gigabyte. It is our own program with MPL parts, not GPL, so ADR-0001 is not
    involved; it stays a process because a crash or a runaway evaluation must not take the server
    down and a superseded preview must die at once.
17. **Inputs and outputs are checked like any other** (docs/engineering/security.md). The runner
    writes the model text itself. It reads stdout with a line limit of 4,096 characters, and the
    mesh file only after checking the magic, both counts against the file size, a cap of 64 M
    triangles, every index against the vertex count and every position for finiteness. The binary
    is found beside the package, or through `STOKES_IMPLICIT_MESHER` in development; its SHA-256
    is part of every cache key. The cache is content-addressed (binary, text, labels, cube, depth,
    simplification), least recently used, capped at 4 GiB per project.
18. **Folded triangles are removed, not only reported.** A fold is two triangles on one edge whose
    normals are more than 170° apart. Slice 1 counts them in the statistics and `geometry.check`
    reports them. Slice 4 adds the removal to the mesher: flip the shared edge where that removes
    the fold, otherwise collapse the flap, then repeat the edge check and keep the result only if
    it is no worse. Until slice 4, a surface with folds may go to snappyHexMesh with a `warning`
    and is refused by the cfMesh fills of ADR-0032 (`mesh.surface_folded`). From slice 4 on a
    remaining fold is an error for every mesher.
19. **Touching bodies.** The union of two bodies that share a face has a zero of the field inside
    the solid, and a cell corner that lands on it would be read as outside. The runner therefore
    shifts the meshing cube by a fixed irrational fraction of the finest cell, so no corner lies on
    a round coordinate. A test unions two abutting boxes at ten cell sizes and requires one closed
    shell with the boxes' volume.

### F. Resolution, display, mesh and run

20. **Two meshes per part.** The **display mesh** is what the viewport, picking and tags use: at
    most 2 M triangles, made on every re-run. The **meshing surface** is made on demand by
    `implicit.surface`, by `geometry.export_stl` and by the mesh pre-check, at *Draft* or
    *Standard* quality, stays on disk and is never sent to the page. Because faces are keys and not
    sets of triangles, a tag made on the display mesh applies to the meshing surface unchanged.
    This keeps the viewport inside ADR-0019 and D4 whatever the surface needs.
21. **Resolution follows the smallest feature.** The cell is at most a fifth of the part's smallest
    dimension for *Standard* and a half for *Draft*; the user may set a cell instead. The triangle
    count is estimated from the display mesh's area before meshing; above 60 M raw triangles or
    depth 15 the request is refused with `implicit.too_fine` and the cell that would fit. In the
    display mesh, a repeated feature thinner than two cells is replaced by its envelope and the
    part is marked `approximate`. A part with no bounding box cannot be meshed
    (`implicit.unbounded`).
22. **Mesh and run.** An implicit part enters meshing as every mesh-only part does: one STL solid
    per tag, in metres (ADR-0020 decision 7), from the meshing surface. The gate before meshing is
    `geometry.check` on that surface: no open edge, no non-manifold edge, no fold (decision 18),
    and every boundary tag non-empty. For several regions the shared wall is written once and used
    by both, as snappyHexMesh expects; two separately meshed copies of an interface are never
    staged.
23. **Exports.** STL and 3MF, like other mesh-only parts. **No STEP and no B-rep twin**: rebuilding
    an implicit part as B-rep is work per design, not a property of the engine.
24. **No implicit operation consumes a B-rep or mesh part in v1**, and no B-rep operation consumes
    an implicit part. The two meet at meshing, as separate surfaces of one case. Turning a B-rep
    or a sampled field into an implicit term needs a sampled-field leaf, which the closed-form
    graph does not have; it is left to a later ADR together with decision 30.

### G. Optimisation that moves the geometry

25. **Route A, parametric (ADR-0016, ADR-0017 route A; M3.5).** An operation parameter is a study
    variable like any other. The dependency graph already classifies it as geometry, so the point
    re-meshes. Nothing new is decided here except decision 16's mesher in the agent. Integer and
    enum parameters (counts, kinds) are variables for sweeps and for the gradient-free optimisers
    only.
26. **How an adjoint's sensitivity reaches Stokes (M8.5).** The adjoint engine driver writes one
    more scalar field, `shape_sensitivity`, on the wall patches of the run's surfaces artifact
    (ADR-0024; `docs/engineering/run-schema.md`). Its meaning is fixed by Stokes, and each driver
    converts to it: **s is the change of the objective per unit area and per unit displacement of
    the wall along the patch normal that points out of the fluid** (objective units per m³). The
    same field is the sensitivity map of ADR-0017 decision 4, so the viewport colours it with no
    further work.
27. **Route B on implicit parts (M8.5).** This adds to ADR-0017 decision 3; it does not replace the
    engine's own B-spline morphing, which remains the route for imported geometry.
    `implicit.gradient(run, parameters)` returns dJ/dp for each named parameter as

    dJ/dp = Σ over wall triangles of  s · (−∂φ/∂p / |∇φ|) · (n_φ · n) · A

    with n the patch normal, n_φ = ∇φ/|∇φ| and A the area. The factor n_φ · n is +1 where the
    implicit part is the fluid and −1 where it is a solid in the fluid, so the command needs no
    flag for which it is. ∂φ/∂p is a central difference of the graph rebuilt at p ± h
    (h = 1e-4 of the variable's range), and ∇φ a central difference in space, both in float64 in
    the numpy evaluator at the triangle centroids, in the geometry worker. Forward-mode
    differentiation of the graph is not built until this is measured to be the bottleneck: at the
    measured rate it costs about 7 s per parameter for 1 M wall faces and 1,000 nodes, against an
    adjoint solve of minutes. Only walls whose tags lie on the part contribute. Triangles where
    |∇φ| is not finite or below 1e-6 are left out and counted, and the result carries the covered
    share of the area. Integer and enum parameters are refused (`implicit.not_differentiable`). A
    parameter that moves a non-wall patch gives the warning `implicit.moves_boundary`.
28. **The loop.** A shape study with `gradient: adjoint` runs, per cycle: rebuild the part, re-mesh,
    solve the primal from the previous cycle's fields, solve the adjoint once, call
    `implicit.gradient`, and step with a bounded gradient optimiser from scipy (SLSQP, L-BFGS-B).
    The engine is asked for sensitivities only, never for its own shape update, so the same loop
    serves `adjointOptimisationFoam` (ESI) and Fluent's adjoint. The result of an optimisation is
    still a parametric part. **Foundation 14 has no shape adjoint** (ADR-0017; E2), so on it an
    implicit part is optimised by route A, with finite-difference gradients in parallel where a
    gradient method is chosen. All of decisions 25 to 29 as study types sit behind the `optimise`
    flag (ADR-0017 decision 6); the modeller itself is ungated, and every flag stays ungated until
    A4 is decided.
29. **Free-form change: `implicit_field` (M8.5).** The operation adds a smooth offset to its
    input: φ′ = φ − Σ a_k ψ_k(x), where ψ_k are compactly supported bumps (Wendland C2, written
    with the operations of decision 4) on a regular lattice of at most 16 × 16 × 16 points in a
    design box, and the amplitudes a_k are lengths within a stated bound. A positive amplitude
    grows the body. The gradient needs no finite difference: dJ/da_k is the sum of decision 27
    with −∂φ/∂p replaced by ψ_k. This is the implicit counterpart of a volumetric B-spline box:
    the wall may move anywhere in the box, passages may merge or split, and nothing can fail to
    re-mesh because every cycle meshes a fresh field. The bump radius is the smoothing length. It
    is the last operation of a tree (its output's metric is `scaled`).
30. **Topology results.** A porosity or level-set result of an adjoint topology optimisation is a
    sampled field. It enters Stokes as ADR-0017 already decided, as an exported STL and so a
    mesh-only part; it does not become an implicit term in this ADR, for the reason of
    decision 24.
31. **Verification.** (a) With s = 1 the sum of decision 27 is the derivative of the fluid volume (the part's own volume where the part is the fluid, minus
    it where the part is a solid): a sphere's radius, a box's side and a fin array's pitch must
    match the analytic value within 1 % at *Standard* quality; this test needs no solver and fixes
    every sign. (b) On an ESI adjoint
    benchmark with a parametric duct, dJ/dp from decision 27 agrees with the central difference of
    J over two re-meshed runs within 10 % for three parameters. (c) The loop of decision 28 lowers
    the objective of that duct over ten cycles.

### H. Licences and the IP boundary

32. **Fidget is a dependency under MPL-2.0**, which the allow-list admits, statically linked into
    our program. MPL is a file-level copyleft: our own files (the program, the mesh operations, the
    label interpreter) stay proprietary, and the MPL files, changed or not, must be available in
    source form to whoever receives the binary. Unchanged crates are named with their versions in
    `THIRD-PARTY-NOTICES.md`. The changed `fidget-mesh` (a finer task split for load balance, and
    progress counts) is vendored with its licence headers, its changes marked, and its
    environment-variable switches and timing prints removed; **its source ships inside every
    installer**, so the obligation does not depend on a public repository. The patch is offered
    upstream, and the vendored copy goes when upstream has it.
33. **Rust licences are audited by our own tool.** `tools/audit_rust_licenses.py` reads
    `cargo metadata --locked` for the two shipped targets, without development dependencies, and
    checks every crate's SPDX expression against `tools/license_allowlist.toml`: an `OR` passes if
    one side is allowed, an `AND` only if all are, a missing licence fails. `cargo-deny` is not
    used, because it would be a second allow-list. `cargo audit` (advisories) joins CI beside
    `pip-audit`, builds use `--locked`, and every crate of the lockfile that is linked is
    registered in `docs/engineering/dependencies.md`.
34. **IP boundary.** The port takes the generic layers only: the expression graph, the evaluator,
    the runner with its cache, the mesher and its mesh operations, and the progress reporting. It
    takes no application model, parameter set, layout, measured inventory, reference mesh,
    document, tool, identifier, name or path from the earlier project, and no feature builder that
    was written for that part. Comments and names that refer to it are rewritten.
    `tools/check_ip.py` runs over the ported files before the first commit. Tutorial and test
    parts are synthetic, with round-number dimensions: a pin-fin heat sink, a chevron channel
    plate, a gyroid heat-exchanger core and a parametric duct.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Grow the B-rep side into the modeller (OCCT features, patterns, sketches) | Cost grows with the feature count, regeneration under a study fails on degenerate topology, and OCCT gives no shape derivative. OCCT stays the path for imported CAD. |
| libfive (MPL-2.0, C++) as a nanobind kernel in `native/` | It would avoid the Rust toolchain. But it is Fidget's predecessor by the same author, it is not what was measured, and in process it fails decision 16's two requirements: a crash takes the server down, and a superseded preview cannot be killed. As a separate C++ program it would save only the toolchain and cost a second port. |
| Fidget in process through PyO3 | The same two failures as any in-process kernel. The process boundary costs a few milliseconds per run and one small text file. |
| Fidget's JIT | Slower than the interpreter on the measured parts, one more dependency, and executable memory in a process that reads generated input. |
| Manifold (Apache-2.0) mesh booleans | Robust and fast, but explicit: no TPMS, no blends, every repeat is still geometry, and no field to differentiate. |
| ImplicitCAD, Curv | AGPL-3.0, excluded by rule 1; and no mesher comparable to Fidget's, respectively. |
| OpenVDB (MPL-2.0) level sets as the representation | Sampled, not closed-form: memory grows with resolution, sharp edges round off at the voxel size, and a parameter is no longer a variable of the field. It is the natural base for the sampled leaf of decision 24, later. |
| Label triangles in numpy on the desktop | Measured: about a minute to several minutes for 15 M triangles, more than the meshing. Kept as the reference implementation only. |
| Label triangles from Fidget's evaluation trace | The trace's numbering of choices belongs to the tape, not to our graph, and is not a stable interface. |
| Faces split by feature angle, as for imported meshes | Ids would change with resolution and with every parameter; tags would not survive a study. |
| One operation holding the whole tree as JSON | Its numbers would not be quantities, so expressions, studies and undo per feature would be lost (rules 4 and 6). |
| Use the adjoint engine's own B-spline morphing on implicit parts | The optimised shape would be a morphed mesh, not a part: no parameters, no re-mesh, and mesh quality decays over the cycles. It remains the route for imported geometry (ADR-0017). |
| Forward-mode differentiation of the graph from the start | The builders compute constants in Python, so every builder would need a dual-number path. Finite differences of φ cost seconds per parameter; revisit with a profile. |
| A sampled level set as the free-form design variable | Needs the sampled leaf. Compact bumps stay closed-form, have an analytic gradient and are bounded in number. |
| Port the earlier project's application as a template | Rule 3. A template may come later from a public or synthetic design. |

## Consequences

- **Stokes becomes a modeller.** D1, the vision's non-goals, the CAD rows of docs/02 and
  CLAUDE.md §2 must be updated when this ADR is accepted, and the M10 item "a parametric-part
  generator ported from finmesh" becomes templates on this modeller.
- **A Rust toolchain joins the build** (Fidget 0.5.1 needs Rust 1.95). On Windows without MSVC the
  working toolchain is `stable-x86_64-pc-windows-gnullvm` with `rust-lld`; it needs `libunwind.dll`
  beside the program, and M8's packaging must ship both and the vendored source. CI gains a
  `cargo build --release --locked` job, the Rust licence audit and `cargo audit`. `native/README.md`
  must say that the directory now also holds a Rust program that is not a `stokes.native` kernel.
- **Tests.** Golden files for `to_text` of every feature under `tests/golden/implicit/`; property
  tests for the builder rules and for the evaluator against analytic distances; mesher tests
  (closed, manifold, no fold, volume within 0.5 % of the analytic value, byte-identical at one and
  N threads, labels equal to the reference), skipped with a reason when the binary is absent and
  run by the CI job that builds it; a journal replay of each tutorial part to a byte-identical STL.
- **Budgets to add to `docs/engineering/performance.md` once measured on the synthetic parts**:
  edit to display mesh under 2 s for a part of up to 20,000 nodes; the labelling budget of
  decision 11; *Standard* surface of the pin-fin tutorial under 90 s on 16 cores;
  `implicit.gradient` under 10 s per parameter for 1 M wall faces.
- **Documents to write or change with the code**: `docs/engineering/implicit-schema.md` (the
  contract: operation types, face keys, the file format, error codes), `geometry-schema.md`
  (`family: implicit`), `security.md` (the mesher's inputs and outputs), `dependencies.md` (a Rust
  section), `run-schema.md` (`shape_sensitivity`), `THIRD-PARTY-NOTICES.md`.
- **Front end.** The operation cards are generated from the schemas, so the eight types appear in
  the Prepare menu without new panels. A feature-tree view of one part, direct manipulation of
  dimensions in the viewport and the `approximate` badge are front-end work in slice 5.
- **Limits a user will meet**: all instances of a pattern share their faces; offsets on `bound`
  fields are approximate; a lattice cannot be shelled; no STEP out; no boolean between an implicit
  part and an imported one.
- **Work.** Milestone **M1.5 Implicit modeller**, about 6 engineer-weeks, off the critical path of
  the internal demo (K2); slices 1 and 2 touch only new files and can start at once.
  - slice 1: `stokes.implicit` (graph, evaluator, rules), the mesher port with the audit and CI
    job, the runner and cache, `implicit_primitive`, `implicit_transform`, `implicit_boolean`
    (sharp), fold counting;
  - slice 2: face keys, labels in the mesher, the basis rule and tag carry, the display mesh,
    `implicit.info` and `implicit.evaluate`;
  - slice 3: blends, `implicit_offset`, `implicit_pattern`, `implicit_sketch`, `implicit_lattice`,
    the metric rule;
  - slice 4: the meshing surface and resolution rule, fold removal, touching bodies, the path
    through `geometry.check` to snappyHexMesh and the ADR-0032 fills, a run of the pin-fin
    tutorial end to end;
  - slice 5: the feature-tree view, envelope previews, the three tutorials.

  **M3.5** (plus about 1 week): studies over operation parameters, the mesher as an agent utility,
  finite-difference gradients. **M8.5** (plus about 2 weeks): `shape_sensitivity` from the ESI and
  Fluent adjoint drivers, `implicit.gradient`, the adjoint loop, `implicit_field`, the verification
  of decision 31. **Later, own ADR**: the sampled-field leaf (B-rep and topology results as
  implicit terms), free polygon and spline sketches with a viewport editor.
- **Open** (`docs/00-open-questions.md`, G15 to G20, each with the assumption this ADR proceeds
  on; G14 is decided):
  - G14, decided by the owner on 2026-11-11: generic code only; the earlier project is a
    starter for how to implement implicit geometry, not a source of anything fitted to its part.
  - G15: that amending D1 is meant, and that D2 ("OCCT only, ever") is read as "the only B-rep
    kernel". Assumed: yes to both, from the owner's request of 2026-11-11.
  - G16: free or paid. Assumed: the modeller is free; studies and adjoint coupling follow the
    `optimise` flag; nothing is gated until A4.
  - G17: whether a STEP or B-rep twin is ever wanted. Assumed: no.
  - G18: whether shipping the changed `fidget-mesh` source in the installer, without a public
    repository, is the owner's preferred way to meet MPL-2.0. Assumed: yes; counsel reviews it
    with the rest before the first public release (ADR-0001).
  - G19: which sensitivity fields `adjointOptimisationFoam` and PyFluent write, and with which
    sign and normalisation. Assumed: both can write a per-face normal-displacement sensitivity on
    walls after one primal and adjoint solve; verified in M8.5 (and M7 for Fluent) by decision 31.
  - G20: the amplitudes of `implicit_field` are a list of lengths, for which the model has no
    quantity-list parameter. Assumed: a `json` parameter of SI values with the dimension fixed by
    the schema, like tag members, until a structured quantity parameter exists.
