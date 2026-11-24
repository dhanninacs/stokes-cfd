# ADR-0020 · Geometry: face-level selections, operations as parametric nodes, picking on the GPU

**Status:** accepted; decisions 2, 3, 4, 5 and 7 amended by ADR-0021 (tags instead of named selections, the CPU raycaster before the id buffer, the repair operations, persisted B-reps with a basis per part, `tags` in the export) · 2026-10-04 · relies on ADR-0003 (OCCT through OCP), ADR-0012 (quantities and expressions), ADR-0014 (STEP import, parts per solid, face ids), ADR-0016 (studies vary any parameter), ADR-0017 (CAD-parametric shape optimisation), ADR-0019 (many-part rendering)

## Context

M1 turns imported geometry into a meshable fluid domain with named boundaries: pick faces,
group them into named selections, build enclosures and extract fluid volumes, extend inlets and
outlets, remove small features, split faces, check the result, and write one STL per selection
for snappyHexMesh. Today a part is one `part` node per STEP solid with a tessellation whose
triangles carry a face id (0 … faces−1 per part, ADR-0014); the viewport highlights the hovered
face from that buffer; `named_selections` are plain groups in the demo; there is no selection
model, no geometry operation and no CAD history. STAR-CCM+ users expect operations that re-run
when their inputs change; Fluent and SpaceClaim users expect named selections that survive
edits; shape optimisation (ADR-0017 route A) needs geometry-driving parameters that studies can
vary. OCCT provides boolean, defeaturing, unify-same-domain and shape-history services, and OCP
exposes them; everything heavy already runs in the geometry worker process.

## Decision

1. **Faces are addressed by (part id, face id)**, where the face id is the index of the face in
   the part's `TopExp` face map, as the transport already does. Ids are stable while the part's
   B-rep is unchanged. Every part also stores a per-face **fingerprint** (surface type, area,
   centroid, mean normal) so selections can be re-matched after a rebuild or a reimport, with a
   reported confidence; unmatched faces are listed, never silently dropped.
2. **Named selections are nodes**: `/geometry/named_selections/<name>` of type `named_selection`
   with parameters `members` (a list of part id → face id ranges; whole parts allowed), `kind`
   (`boundary` | `region` | `plain`), `color`, `visible`. Boundaries in `/regions/*/boundaries`
   reference a selection by node id instead of a patch name. A selection becomes one STL solid
   (and later one mesh patch) under its name.
3. **Selection tools**, all commands on `selection.*` so the palette, the CLI and agents can use
   them: `create`, `add`, `remove`, `set`, `invert` (within the parts in view), `grow(angle)`
   (feature-angle region growing over the tessellation's face adjacency, numpy first, a native
   kernel when measured), `by_color`, `by_name` (STEP colour and label), `by_part`, `rename`,
   `delete`. In the viewport: click (face), shift-click (add), box and lasso over projected face
   centroids and triangles, double-click (grow at the default angle), a Selection mode island
   (face | part) and the context toolbar (Name, Grow, Invert, Hide, Isolate).
4. **Picking moves to the GPU**: an offscreen id render (part id and face id packed into RGBA)
   read back under the cursor on hover and for box and lasso, replacing the CPU raycast. It is
   client code in `web/src/viewport`, not a native kernel; it works with the batched meshes of
   ADR-0019 through a per-instance id attribute.
5. **Operations are parametric nodes** under `/geometry/operations/<name>` with a type per
   operation and quantity parameters (ADR-0012), so a study or an optimiser can vary them:
   `primitive` (box, cylinder, sphere, with placement), `boolean` (fuse, cut, common),
   `enclosure` (box or cylinder around chosen parts with per-side margins), `extract_fluid`
   (the enclosure minus the solids, or the internal volume of a closed shell with capping faces
   chosen from selections), `extend` (extrude a selected planar face by a length, for inlets
   and outlets), `defeature` (remove selected faces and heal: `BRepAlgoAPI_Defeaturing`, then
   `ShapeUpgrade_UnifySameDomain`), `split` (split faces of a part by feature angle or by a
   plane, for selection only). An operation's **outputs are parts** under `/geometry/parts`,
   marked `derived` with the operation's id; its inputs stay in the tree and are hidden by
   default. Operations re-run in the worker when an input parameter or an upstream part
   changes (the dependency graph of ADR-0012 extended to node references), with progress and
   cancellation; results are cached by a hash of inputs. OCCT's shape history
   (`BRepTools_History`: generated, modified, deleted) maps input faces to output faces, so
   selections on inputs are carried to outputs automatically, with the fingerprint fallback.
6. **Checks** are a command and a tree status: `geometry.check(part)` reports closedness, free
   edges, self-intersection, tiny faces and edges, sliver triangles in the tessellation, and the
   watertightness a mesher needs; results appear on the part's status dot and card.
7. **Export**: `geometry.export_stl(selections | parts, path, binary)` writes one STL solid per
   selection (or per part), in metres, with the selection's name as the solid name; this is what
   M2 passes to snappyHexMesh (`constant/triSurface`). STEP and BREP export of parts come with it.
8. **Model and compile**: `named_selection` and the operation types are registered node types
   (one module, `stokes/geometry/model.py`, following `stokes/explore/model.py`); M3's case
   compiler maps boundary nodes to selections to patches, so a renamed selection renames the
   patch everywhere.
9. **Validation**: ten public manifold STEP files form the M1 test set (the roadmap's risk
   item); the gate is a public STEP to fluid domain plus inlet, outlet and wall selections in
   under five minutes through the UI, with the same steps replayed from the journal through
   the CLI and giving the same STL byte-for-byte.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Faces as tree nodes | Thousands of nodes per part; the tree becomes unusable and every refresh pays for them. Face ids plus fingerprints give the same addressability without the nodes. |
| Operations that replace their inputs (no history) | Users cannot change a margin and re-run; shape optimisation needs re-execution. Keeping inputs hidden costs little. |
| A full feature tree per part (CAD-style) | Stokes imports and prepares geometry; it is not a modeller (vision non-goal). Operations between parts cover the preparation workflow. |
| CPU raycast with a BVH | Workable for hover but not for lasso over thousands of parts; the GPU id buffer handles both in one pass and scales with pixels, not triangles. |
| Native C++ for growth and picking first | Picking belongs on the GPU; growth in numpy is adequate for the M1 gate and can move to a kernel when measured (CLAUDE.md rule: C++ only where measured). |

## Consequences

- New: `stokes/geometry/model.py` (node types), `selection.py` (membership, fingerprints,
  growth), `operations/` (one module per operation over OCP, run in the worker), `checks.py`,
  `export.py`; commands `selection.*`, `geometry.check`, `geometry.export_stl`,
  `geometry.op.*`; events `selection.changed`, `geometry.op_progress`, `geometry.op_done`.
- Viewport: id-buffer picking, box and lasso, selection tint by selection colour, hidden inputs;
  Geometry stage task strip and Selection island; schema-driven operation cards.
- Transport: no change for faces; derived parts stream like imported ones.
- Slices for builders, in order: (1) selection model, fingerprints, `selection.*` commands,
  STL export, checks (Python); (2) GPU picking, box and lasso, selection island and context
  toolbar (front end); (3) operations: primitives, boolean, enclosure, extract fluid, extend,
  with history mapping and re-execution (Python worker); (4) defeature and split, operation
  cards and the task strip (both sides); (5) the M1 test set and the gate.
- Open: whether `split` should also write the split back into the B-rep (needed for per-face
  boundary conditions on a face that spans two regions); default no, selections suffice.
