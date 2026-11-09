# Tags, checks and STL export

**Slice 1 of ADR-0020** (Python) · node type: `stokes/geometry/model.py` · members and growth:
`stokes/geometry/selection.py` · topology: `stokes/geometry/topology.py` · fingerprints and
re-matching: `stokes/geometry/fingerprints.py` · related predicates: `stokes/geometry/related.py`
· session cache and fingerprint archive: `stokes/geometry/selection_cache.py` · checks:
`stokes/geometry/checks.py`, and the B-rep check job: `stokes/geometry/checkstore.py` · B-rep
checks and STEP export: `stokes/geometry/brep.py` · STL:
`stokes/geometry/export.py` · readers: `stokes/geometry/readers.py`, `step.py`, `iges.py`,
`brepfile.py`, `meshfile.py` (STL, OBJ, 3MF; T-017) · commands: `stokes/api/commands/selection.py`
· operations (ADR-0021
decision 8, see [Operations](#operations)): `stokes/geometry/shapes.py`, `operations/`, `opplan.py`,
`opstore.py`, `carry.py`, `opcommands.py`, `stokes/api/commands/operation.py`

A tag groups B-rep faces of one or more parts under a name. It becomes one STL solid
when exported, and from M3 one boundary patch. Faces are addressed by (part node id, face id),
where the face id is the face's index in the part's `TopExp` face map, 0 to faces - 1
(ADR-0014). Every triangle of a part's mesh carries that id (transport v2, `faceIds`). The front
end renders tags and changes them only through the commands below.

## Tag members: faces, edges and vertices

Since ADR-0022 §15.1 a member may also name B-rep **edges** and **vertices**:

```json
[{"part": "n_3fa9c2d1", "faces": [[0, 3], [7, 7]], "basis": "1f3a9c2e5b7d"},
 {"part": "n_5e0b17aa", "edges": [[2, 2]], "basis": "1f3a9c2e5b7d"},
 {"part": "n_5e0b17aa", "faces": "all", "vertices": [4]}]
```

* The three id sets are **independent**, and absent means **none** — so a member that names only
  edges or vertices simply omits `faces` (§15.4). `faces: "all"` keeps its old meaning, every face.
* One `basis` covers all three sets: they are numbered against the same B-rep version, so a second
  basis could only disagree with itself. A basis is stored only when the member names some explicit
  id; a wholly whole member has none.
* Edge and vertex ids are positions in `TopExp.MapShapes` order over the part's shape, edges first
  then vertices, first unique occurrence winning. Faces keep their `TopExp_Explorer` numbering
  (**with** repeats), which is why the two sets are numbered differently.
* Text written before edges existed parses to exactly the same members it did then, and a face-only
  tag's stored JSON is byte-for-byte unchanged.
* `tag.rematch` re-matches faces across a re-import. Edge and vertex fingerprints are not archived
  yet, so a rematch **drops** edge and vertex ids across a basis change and reports the counts in
  `edges_dropped`/`vertices_dropped` rather than pointing them at the wrong B-rep.
* A mesh-only part (STL, OBJ, 3MF) has no B-rep and therefore no edges or vertices; asking for its
  topology raises rather than inventing ids from the tessellation.

The record travels on its own lazy endpoint, `GET /api/geometry/topology/{part}` (ADR-0022 §15.2),
with its own versioned codec — **not** on the mesh transport, whose decoders are unchanged. A part
with no B-rep is a 404, which the client treats as "no edges to pick" rather than an error.

## Import

`geometry.import` brings a geometry file in: one part per solid the file holds, and a file of
several solids turns the new part into a group of the same id. It is one undo step, runs in a
worker process, is journaled, and `geometry.import_step` is its alias (journals record the new
name).

| Format | Suffixes | Family | Reader (all in OCP, numpy or the standard library; no new dependency) | Units | Names and colours |
|---|---|---|---|---|---|
| STEP | `.step .stp` | B-rep | `STEPCAFControl_Reader` (as today) | from the file | product names and solid colours (as today) |
| IGES | `.iges .igs` | B-rep | `IGESCAFControl_Reader`, then the same XCAF walk as STEP | from the global section (unit flag), else mm with a warning in the result | entity names and colours |
| BREP | `.brep .brp` | B-rep | `BRepTools.Read_s` (binary files through `BinTools` when the header says so) | none in the file: the `units` parameter, default mm | none; parts named from the file |
| STL | `.stl` | mesh | `meshfile.read_stl` (T-017) | none: `units`, default mm | the binary header's `solid <name>`, or one body per ASCII `solid <name>` block |
| OBJ | `.obj` | mesh | `meshfile.read_obj` (T-017) | none: `units`, default mm | `o` and `g` names; faces before any take the file stem |
| 3MF | `.3mf` | mesh | `meshfile.read_3mf` (T-017) | the model's `unit` attribute (an unknown one is read as mm, with a warning) | object names; one body per build item; colours not in v1 |

- IGES bodies: the solids when the file has any (manifold solid B-reps, entity 186); otherwise
  one `shell` part for each shell or compound, and one part named from the file stem, with kind
  `faces`, that holds every loose face. CAD tools usually write a solid as separate surfaces, and
  the reader hands each lone face over as a shape of its own, so the six faces of a box are one
  part of six faces, not six parts. The faces are not sewn: that changes the topology, so it is a
  repair operation the user asks for (decision 5). A file that has a solid keeps its loose faces
  as the walk found them, one `shell` part each.
- The `part` node's `family` parameter (`brep` \| `mesh`, default `brep`) is what the import read
  the source as, written at the import; `units` is the length unit the source declares, or the
  import's for a file without one (the mesh is in metres, scaled from it). A solid the worker
  reports as `mesh` sets the family itself, so a re-import (undo-redo, a restored project) keeps it.
- **Mesh-only parts.** STL, OBJ and 3MF carry no B-rep, so their parts are `family: "mesh"` and
  have no faces to name. Their faces are the regions of the triangulation that meet below the
  feature angle (`meshfile.split_faces`, 30°, the region angle `tag.grow` and `tag.region` use):
  corners are welded by position, the dihedral angle at every edge shared by two triangles decides
  whether they are linked, and connected components are renumbered by first appearance. A box
  gives six faces (measured on the worker's own mesh), a 16- and a 64-segment cylinder three (side,
  and each cap). The import runs in one pass (`_mesh_job`): no tessellation, positions scaled from
  the file's units into metres, normals from `mesh.vertex_normals`. Everything that needs a B-rep
  refuses such a part with `geometry.mesh_only`: `operation.create` and the operations, and
  `geometry.export_step`. `geometry.check` still checks the tessellation and reports
  `brep: {checked: false, reason: "mesh-only part"}`; the check worker never reads a mesh file.
  `geometry.export_stl` and the tags work as on any part. `read_source` is the B-rep reader and
  refuses a mesh file; `readers.read_mesh` is the mesh one. Three input bounds apply before the
  geometry is touched: a binary STL's record count must match its size (`84 + 50 n`), a 3MF archive
  may hold no member over 512 MB uncompressed and no more than 2 GB in total, and a 3MF XML part
  may not contain a `<!DOCTYPE`; a NaN or infinite coordinate is a `geometry.unreadable` refusal.
- `units` given for STEP, IGES or 3MF is a `validation` error; the text must name a length unit
  (the pint registry is the unit table). BREP and the mesh formats without their own units (STL,
  OBJ) re-imports (undo-redo, a restored project) keep the part's `units`, and so does every job
  that reads a BREP file again: `geometry.check` and `geometry.export_step` load each part through
  the shape provider in its own units, so an inch BREP checks and exports at its own size and parts
  of one BREP file imported in different units go into one export, each at its own size.
- A reader's notes (an unknown IGES unit flag, an empty unit name) reach the parts in the
  import's result as `meta.warnings`.
- `geometry.formats` (no project needed) lists the formats and the picker's file filter, so the
  front end builds the dialog from the API:
  `{formats: [{id, label, suffixes, family, units}], filter}`.
- The source file is checked before any worker starts (`check_source_file` in
  `stokes/geometry/files.py`): a regular file, at most 1 GiB, a known suffix, and the format's
  header probe (STEP: `ISO-10303-21`; IGES: the `S` start marker in column 73 of the first
  80-byte line; BREP: the `DBRep_DrawableShape`, `CASCADE Topology` or `Open CASCADE Topology`
  header; 3MF: a zip signature. STL and OBJ have no header to probe: their readers validate the
  records). `check_step_file` keeps the same checks and the STEP-only suffix.
- The reader entry points, both in `stokes/geometry/readers.py`, dispatch on the suffix and are the
  only places the worker jobs (`import`, `check_brep`, `export_step`) and the shape provider read a
  file: `read_source` for the B-rep formats (STEP, IGES, BREP), `read_mesh` for the mesh ones (STL,
  OBJ, 3MF; `stokes/geometry/meshfile.py`, numpy only, no OCP). The B-rep modules load OCP only
  inside the readers, so the API process stays without it.
- When the file is read, every part gets its `basis` (the B-rep version, see
  [Operations](#operations)), before any of its meshes arrives, and the worker caches each
  solid's B-rep in the shape cache just before it first tessellates it.

## Nodes

```
/geometry/tags    group; tag.create adds it, in the same undo step, when missing
  <name>                      tag
```

The group is not part of the project skeleton. Projects that already hold it as a plain group
(the demo) keep it, and plain groups inside it stay valid: `tag.list` reports them as
`legacy`, and the tag commands refuse them with a hint.

| Parameter | Kind | Meaning |
|---|---|---|
| `members` | json | the faces (below); written by the `tag.*` commands |
| `kind` | enum `boundary` \| `region` \| `plain` | boundary: one mesh patch; region: whole parts that make one mesh region; plain: a working set |
| `color` | string `#rrggbb` | tint of the faces in the viewport; new tags take the next palette colour |
| `visible` | bool | |
| `opacity` | number | 0 transparent to 1 opaque; default 1.0 |

`kind`, `color`, `visible` and `opacity` change through `parameter.set`, as on any node.

**Regions and boundaries.** The geometry node types now include `region` and `boundary`
(ADR-0022 decisions 2.1 and 2.9), declared beside `tag` in `stokes/geometry/model.py` and
registered with it by `register_builtin_types`. A `boundary` gives the faces of a boundary-kind
tag a condition kind and its values; a `region` gives the bodies of a region-kind tag a kind
(fluid or solid) and a material. Both reference their tag by id, so a tag a boundary or region
uses is the boundary patch or mesh region it becomes. Their contract is
`docs/engineering/setup-schema.md`. A tag in use stays: `tag.delete` refuses it
(`model.referenced`) unless `force`, which deletes the boundaries and regions that use it in
the same step, and `node.remove` refuses it, or a group that holds it (ADR-0022 decision 2.8).

## Members

`members` is canonical JSON text under the `json` parameter kind (T-006). The kind holds
JSON-compatible data (an object, a list, a string, a number, a boolean or null); tags store
the canonical text and the `tag.*` commands parse and check its structure.

The structure is also checked when `members` is written (T-013): `parameter.set` and `node.add`
refuse a value that is not a list of `{part, faces, basis}` members, or the JSON text of one,
with a `validation` error that names the problem. `null` is refused too (a tag is cleared with
an empty list), although a null already stored reads as no members. Undo puts a value back as it
was, unchecked, and the `tag.*` commands check again when they read, for values written before
the check or by hand.

```json
[{"part":"n_3fa9c2d1","faces":[[0,3],[7,7]],"basis":"1f3a9c2e5b7d"},{"part":"n_5e0b17aa","faces":"all"}]
```

- `part`: a `part` node id. Parts appear once, in the order they were first added.
- `faces`: `"all"` means every face of the part, whatever its count, so a whole-part member
  needs no mesh and survives re-imports. Otherwise it is a list of **inclusive** `[first, last]`
  ranges, sorted and merged.
- `basis`: present on explicit faces only. It names the B-rep version the ids refer to (a hash
  of the source file's name, size and modification time, the solid index and the face count).
  Clients ignore it and never send it.

Equal tags have equal text, so a replayed journal stores the same bytes. A project saved
before the `json` kind held `members` as a patterned string; it reads back unchanged, because the
stored text is itself JSON.

**Command input** (`members`, `seeds`) is:
`[{"part": <part id or path>, "faces": "all" | [id | [first, last], ...], "edges": [...],
"vertices": [...], "basis": "..."}]`. A group reference with `"all"` stands for every part under
it. Ids are checked against the part's face count when it is known.

**A member must name at least one of `faces`, `edges` or `vertices`.** `{"part": x}` alone is
**malformed** and refused with `names nothing; give faces, edges or vertices` — it is not a
shorthand for "every face"; write `"faces": "all"` for that. The rule is the owner's (2026-10-12)
and it matches what the stored text already required: both paths agree now, where the command-input
path used to accept the bare form while the loader refused it.

`faces` defaults to `"all"` **only** when neither `edges` nor `vertices` is given. An edge-only or
vertex-only member therefore has **no** faces (the empty list), not all of them — absent fields mean
none (ADR-0022 §15.4).

**Expanded form** (`tag.members`): one entry per member,
`{part, path, name, exists, whole, faces: [ids], edges: [ids], vertices: [ids], count,
part_faces, missing: [ids], stale}`. `edges` and `vertices` were added with T-056-fix: the member
held them but this reply reported faces only, so an edge-only tag looked empty to a client.
`missing` lists ids the part no longer has; `stale` is true when the part's B-rep changed since
the faces were chosen.

## Fingerprints and re-matching

Each face has a fingerprint computed from the tessellation. All values are SI:

| Field | Meaning |
|---|---|
| `area` | m² |
| `centroid` | area-weighted, m |
| `normal` | area-weighted mean facet normal: length 1 on a plane, near 0 on a closed face (a whole cylinder) |
| `triangles` | triangles of the face |

The surface type comes in a later slice. When a command stamps explicit faces, the session keeps
the fingerprints of that version in memory. Saving the project writes one archive,
`<name>.stokes/cache/geometry/fingerprints.bin`, with exactly the versions the saved tags
use.

A member is **stale** when its part was re-imported from a changed file. Commands that would mix
the two numberings refuse with `tag.stale` (add, remove, invert, grow on that part; STL
export). `tag.rematch` maps every face to the new version by fingerprint. The distance
combines three terms, each divided by its tolerance: the centroid shift as a fraction of the
part's size (tolerance 0.03), the relative area change (0.1) and half the mean-normal change
(0.15). Matching is one to one. The confidence is `exp(-d²/2) · (1 - d/d_alt)`, so a face with an
equally good alternative scores near 0. Faces below `min_confidence` (0.5 by default) leave the
tag and are listed; they are never silently dropped.

## Growth

`tag.grow` floods each part's face adjacency. Two faces are adjacent when they share a
triangle edge (vertices are merged by position across face boundaries). The flood crosses a
boundary when the dihedral angle there is below `angle_deg` (default 30°). That angle is measured
between the facet normals on either side of the shared edges (mean cosine, weighted by edge
length), not between the faces' mean normals, which vanish on closed faces. Results:

- A box stops at every edge (90°).
- The halves of a split cylinder side merge: a fine tessellation jumps at most about 11.5°
  across a smooth edge.
- Caps stay apart.

## Style

A `part` holds its display style beside its import parameters; `tag` holds the same three beside
its own:

| Parameter | Kind | Meaning |
|---|---|---|
| `color` | string `#rrggbb` | the part's colour; new parts are `#c7ccd1` (a tag takes the next palette colour) |
| `visible` | bool | default true |
| `opacity` | number | 0 transparent to 1 opaque; default 1.0 |

A reader of `opacity` (the viewport, T-023) treats a value missing on a part saved before it
existed as 1, and clamps to [0, 1].

`geometry.style` writes them on many nodes at once:

- `nodes`: ids or paths of `part` nodes, `tag` nodes, groups (every part beneath, at any depth)
  or `boundary` nodes (their tag; a boundary that is still a plain group from before ADR-0022
  resolves to nothing). Duplicates collapse. An unknown reference is `model.unknown_node`, a
  node of another type `validation`.
- At least one of `visible`, `color`, `opacity` is required, else `validation`. `color` is
  `#rrggbb`, upper case normalised to lower; `opacity` must lie in [0, 1].
- It applies one `Composite` of `SetParam`s, one per changed node and parameter. A node already
  in the target state comes back unchanged, and a call that changes nothing pushes no undo step
  and emits no events. Otherwise it is one undo step and one journal record, and it emits
  `node.changed {node_id, path, param}` per changed node and parameter, as `parameter.set` does.
- Result: `{changed: [ids], unchanged: [ids], resolved: int}`, `resolved` the count of nodes the
  references named.

## Commands

`tag` accepts an id, a path (`/geometry/tags/inlet`) or a name (`inlet`).
Every mutating command returns the tag summary:
`{id, path, name, type, kind, color, visible, members, parts, faces, stale: [part ids], changed}`.
`faces` is null while a whole part's count is unknown.

| Command | Parameters | Result / notes |
|---|---|---|
| `geometry.import` | `path`, `name`, `units` (BREP only; default mm), `wait` = false, `node_id` | the part (one solid) or the group (several solids); one undo step; `geometry.import_step` is its alias |
| `geometry.formats` | | `{formats: [{id, label, suffixes, family, units}], filter}` |
| `tag.create` | `name`, `members` = [], `kind` = boundary, `color` (palette), `visible` = true, `node_id` | summary; one undo step, the group included |
| `tag.add` | `tag`, `members` | union |
| `tag.remove` | `tag`, `members` | faces out of a whole part leave the rest (needs the face count) |
| `tag.set` | `tag`, `members` | replaces the members |
| `tag.invert` | `tag`, `parts` (parts or groups, e.g. those in view) | within those parts, exactly the faces the tag lacks; a part with none becomes `"all"`, a whole part goes |
| `tag.grow` | `tag`, `angle_deg` = 30, `seeds` (members; default the tag's own faces) | adds the grown faces; `added` = faces added |
| `tag.region` | `members`, `angle_deg` = 30 | `{members}`: the region the given members grow to, by the same flood (ADR-0021 decision 3.5); whole parts stay whole; read-only |
| `tag.rematch` | `tag`, `min_confidence` = 0.5 | summary plus `report: [{part, path, from, to, matched, unmatched: [ids], faces: [{from, to, confidence}], reason}]` and `unmatched` (count) |
| `tag.rename` | `tag`, `name` | summary |
| `tag.delete` | `tag`, `force` = false | `{id, path, name, deleted, removed: [ids]}`. A tag a boundary or region uses is refused (`model.referenced`, naming them) unless `force`: then one undo step deletes those regions (with their boundaries) and boundaries, clears the partners that named a deleted boundary, and deletes the tag; `removed` lists the boundaries and regions deleted |
| `tag.list` | | summaries without `members`, each with `boundaries: [ids]` and `regions: [ids]` (the nodes that use it) and `warnings: [{code: "tag.overlap", tags: [a, b], part, faces, message}]`: faces of `part` this boundary-kind tag shares with another (`faces` null when both hold a whole part of unknown face count; a face in two patches is refused at compile, decisions log G10). Plain groups as `{id, path, name, type, legacy: true}`; malformed members as `error` |
| `tag.members` | `tag` | summary with the expanded members |
| `tag.by_color` | `color` (`#rrggbb`, any case), `part` (part or group; default every part) | `{color, members, parts: [{id, path, name, color}], count, level: "part"}`; pass `members` on to create, add or set |
| `tag.by_name` | `pattern` (shell style, case-insensitive; with a `/` matched against the path), `part` | as `by_color` |
| `tag.anchor` | `tags` (tag ids or paths; default every tag) | `{anchors: [{tag, centroid, normal, area, extent, faces, parts, partial}]}` in metres, one row per tag: `centroid` and `normal` area-weighted over the member faces, `normal` unit or `[0, 0, 1]` when the tag is closed (mean normal under 1e-6), `extent` the diagonal of the bounding box of the faces' centroids (0 for one face), `faces` and `parts` the meshed ones; `partial` true when a member part has no mesh yet (those parts are skipped, and a tag with none left gives `faces: 0` and the zeros); read-only, not journaled (ADR-0022 8.1) |
| `tag.related` | `members`, `relation` (`same_area`, `coplanar`, `parallel`, `coaxial`, `tangent`, `same_color`), `parts` (parts or groups; default every part with a mesh), `tolerance` | `{members, count, relation, tolerance}`: faces like the picked ones, the picked faces excluded. `same_area` within `tolerance` (relative) of a picked face's area (default 0.02); `parallel` below `tolerance` radians between mean normals, both open (default 0.035); `coplanar` parallel at the fixed 0.035 rad and the centroid within `tolerance` of the part's bounding-box diagonal of a picked face's plane (default 0.001); `coaxial` the picked facets wrap one axis, fitted as the smallest-eigenvalue eigenvector of their area-weighted normal covariance with a point from the least-squares intersection of the facet normal lines, refused with `tag.invalid` when the smallest eigenvalue exceeds 5 % of the largest (a pick whose facets share one direction, such as one plane, wraps no axis and finds nothing), the candidate with 95 % of its facet area within 0.035 rad of perpendicular and its centroid within `tolerance` (relative) of the picked radius (default 0.02); `tangent` the `tag.region` flood at `tolerance` degrees (default 30); `same_color` the `by_color` parts of the picked faces' colours (no tolerance). Over the fingerprints and facets the session holds; read-only, not journaled (ADR-0022 6.4) |
| `geometry.check` | `part` (part or group), `wait` = false, `force` = false | part: `{part, path, name, checked, worst, ok, findings, stats, brep}`; group: `{node, path, parts: [...], checked, unchecked, worst, with_findings}`. The tessellation findings come back at once; the B-rep pass is a background job, so `brep` says `{checked: false, reason: "running"}` until it has reported, unless `wait` is true |
| `geometry.cancel_check` | `part` (part or group; default every check) | `{cancelled: [part ids]}`: stops the running B-rep check jobs; a part keeps the findings it already has |
| `geometry.export_stl` | `path`, `tags`, `parts`, `binary` = true, `folder` | `{files: [{path, bytes, solids: [{name, triangles, faces}]}], solids, triangles, binary, units: "m"}` |
| `geometry.export_step` | `parts`, `path`, `folder` | `{file, solids, names, units: "m"}`: the chosen parts, written in metres under their **node** names. Every part is loaded through the shape cache's provider, so one call mixes parts and groups of any number of source files with the derived parts of operations |
| `geometry.style` | `nodes` (parts, tags or groups: ids or paths), `visible`?, `color`?, `opacity`? | `{changed, unchanged, resolved}`; one undo step and one journal record (see Style) |

**Undo and journals.** Every definition change is one undo step made of model commands. A
change that leaves the members as they were adds no step. `add`, `remove`, `invert`, `grow` and
`rematch` journal the members they produced as `result`, and a replay applies them as they were.
A replay therefore needs no mesh, and it gives the same STL bytes. The `result` parameter exists
only for journals; clients never send it. `by_color`, `by_name`, `region`, `anchor`, `related`,
`list`, `members`, `check` and the exports change nothing and are not journaled.

**`by_color` and `by_name`** work on whole parts. A part's colour is its STEP solid's (unless
changed since), and its name comes from the STEP labels. The import keeps neither colours nor
names per face.

## Checks

`findings`: `{code, severity, count, message, suggestion}`. Severity is `error`, `warning` or
`info`; `worst` is the most severe, or `ok`.

| Code | Severity | Meaning |
|---|---|---|
| `geometry.empty` | error | no triangles |
| `geometry.non_finite` | error | NaN or infinite coordinates |
| `geometry.size` | warning | under 1 µm or over 10 km across: check the file's units |
| `geometry.untriangulated_faces` | warning | B-rep faces without triangles |
| `geometry.free_edges` | warning | edges of one triangle: not closed (watertight) |
| `geometry.non_manifold_edges` | warning | edges of more than two triangles |
| `geometry.inconsistent_orientation` | warning | neighbours wound the same way |
| `geometry.inverted` | warning | closed with negative volume: normals inward |
| `geometry.degenerate_triangles` | warning | no area, or corners merged |
| `geometry.sliver_triangles` | info | quality 4√3·A/Σl² under 0.001 |

`stats`: `triangles, vertices, faces, edges, free_edges, non_manifold_edges, inconsistent_edges,
degenerate_triangles, sliver_triangles, non_finite_triangles, untriangulated_faces, closed,
volume (m³, when closed), area (m²), bbox [6], size (m), quality`.

The command stores `{worst, counts: {code: count}, at}` in the part's `meta.check` and emits
`node.changed` for that part. It sets the part's `status`: `warning` when there are findings (the
tessellation's `worst` is `error` or `warning`, or the B-rep is not `ok`) and `ok` when there are
none, leaving `failed`, `stale` and `new` alone.

**The B-rep pass is a background job** (ADR-0021 decisions 5 and 8.3). For every part whose
`meta.check.brep.basis` is not the version the part is now — or every one, with `force` — the
command starts one job per batch (`{kind: "check_brep", parts: [spec...]}`, the specs
`shapes.part_spec` builds) on a daemon thread. Each part loads through the shape cache's
`ShapeProvider`, so a part whose B-rep is cached costs no source read, a derived part of an
operation is checked without re-reading anything, and one call may mix both. Progress arrives as
`geometry.check_progress {node_id, path, index, count}`, one message per part. On completion
`meta.check.brep` holds the findings, the `basis` they belong to and `at`; the part's `status`
follows the rule above (the job re-reads the tessellation's `worst` from the same `meta.check`, so
it never clears a warning the tessellation gave); and `node.changed` is emitted. A part the job
could not check stores `{checked: false, reason, basis, at}` and emits `geometry.check_failed`.

`brep` is `{checked: true, index, valid, free_edges, open_shells, small_edges, small_faces,
free_edge_faces, small_face_ids, small_edge_faces, truncated, ok, basis, at}`. The three id lists
are the part's own face numbering (`operations/faces.ids_of`: `TopExp_Explorer` order with
repeats), each capped at 1,000 ids with `truncated: true` when one was cut:

| Field | The faces |
|---|---|
| `free_edge_faces` | faces that hold an edge fewer than two faces of the part use |
| `small_face_ids` | faces under a micrometre across |
| `small_edge_faces` | faces that hold an edge under a micrometre long |

The free edges are counted by walking the part's faces and asking how many of them each edge
belongs to, not through `ShapeAnalysis_FreeBounds`: that tool reports 0 free edges for an unsewn
compound of loose faces (measured by T-027), so an open surface model looked closed. The walk sees
the loose faces, agrees with it on shells and ignores a degenerate edge (a sphere's pole is a
point, not a free edge). The operations' own `check_shape` summary keeps the
`ShapeAnalysis_FreeBounds` count, so their reports are unchanged by the check's new count.

A part with no B-rep to load at all — no source file, no shape cache and no operation behind it —
stays `{checked: false, reason}` and starts no job. `wait=true` is refused with `validation` where
it would block the server, as `geometry.import` refuses it.

## STL export

`path` is a file or a folder:

- **A `.stl` file.** In ASCII it holds one `solid <name>` block per tag or part, which
  snappyHexMesh reads as named regions. In binary it holds a single solid, because binary STL
  carries no names. Two or more solids into one binary file is refused.
- **A folder.** It receives one `<name>.stl` per solid, in either format.

Other rules:

- Coordinates are in metres. Normals follow the winding (outward on a solid, as imported).
- Solid names are OpenFOAM words: the node name with anything outside `[A-Za-z0-9_]` replaced,
  no leading digit, and unique ignoring case (`_2`, `_3` ...).
- The target must resolve inside the project folder, or inside `folder` (the export folder the
  user chose). Relative paths start there. A symbolic link that leads out is refused, and only
  `.stl` files are written.
- An empty tag is refused. So is a stale one, and one listing faces its part no longer has.
- The bytes depend only on the meshes and the members.

## Events

| Event | Payload | When |
|---|---|---|
| `node.added` | `{node_id, path}` | `tag.create`: the group when new, then the tag |
| `node.changed` | `{node_id, path, param}` | members (`param: "members"`); a rename (no `param`); `geometry.check`, per part (also when the B-rep job finishes) |
| `geometry.check_progress` | `{node_id, path, index, count}` | `geometry.check`: one per part as the B-rep job reaches it |
| `geometry.check_failed` | `{node_id, path, code, message}` | `geometry.check`: per part the B-rep job could not check; code `geometry.cancelled` on cancel |
| `node.removed` | `{node_id, path}` | `tag.delete` (with `force`, each region and boundary first) |
| `tag.changed` | `{node_id, path}` | after every change of a tag's definition: create, add, remove, set, invert, grow, rematch, rename, delete |
| `boundary.changed`, `region.changed` | `{node_id, path}` | `tag.delete` with `force`: each boundary and region deleted, and each boundary whose partner was cleared |

The session adds the node summary to `node.*` events (`stokes/api/summaries.py`). After undo or
redo, clients receive `history.changed` and fetch the tree again.

## Errors

| Code | When |
|---|---|
| `tag.invalid` | members malformed or naming faces a part does not have; a group given face ids; a face count needed but unknown; an angle outside (0, 180]; nothing to export; nothing to relate; a bad `relation` of `tag.related`; a pick that is not cylindrical for `tag.related coaxial` |
| `tag.stale` | the tag refers to an earlier version of a part: run `tag.rematch` |
| `geometry.no_mesh` | a part has no mesh yet (import running or failed) |
| `geometry.mesh_only` | a mesh-only part (STL, OBJ, 3MF) where a B-rep is needed: an operation input, or `geometry.export_step` |
| `geometry.unreadable` | STEP export: a part has neither a cached B-rep nor a readable source file (the message names the part), or the provider could not read the source in the worker |
| `validation` | bad kind, colour or name; paths outside the allowed folder; a plain legacy group given as a tag; a bad `tolerance` of `tag.related` |
| `model.referenced` | `tag.delete` of a tag a boundary or region uses, without `force`; `node.remove` of such a tag or of a group holding one |
| `model.unknown_node`, `model.error` | as everywhere (unknown references, duplicate names) |

## Operations

ADR-0021 decision 8 (T-024): a geometry operation is a node whose run makes **derived parts**.
Operations work in **millimetres**: every input B-rep is scaled to mm, parameters are stored in
SI (quantities, as everywhere) and converted in the worker (`ctx.mm`), and outputs are stored
with `units: "mm"`; meshes stay in metres.

### Nodes

```
/geometry/operations         group; operation.create adds it, in its undo step, when missing
  <name>                     one node type per operation (boolean_1, primitive_2 ...)
/geometry/parts/<name>       the outputs: part nodes with derived = the op id
```

Twelve types, declared in `stokes/geometry/model.py` with every parameter their briefs name:
`primitive`, `boolean` (T-024, built), `enclosure` (T-025, built), `extract_fluid` (T-026, built),
`heal`, `sew`, `orient`, `solidify` (T-027, built), `cap`, `unify`, `slivers`, `defeature`
(T-028, built). All twelve are built; a type whose module is not built yet is refused with
`geometry.op_invalid` "not built yet", and
`operation.types` says `built: false`. `OPERATION_TYPES` gives what the framework needs besides
the parameters: `min_inputs` (a primitive takes none), `part_params` (json lists of further
parts the run reads, loaded, hidden and watched like the inputs: a boolean's `tools`) and
`tag_params` (references to a tag whose stored members the run receives as `<name>_members`:
`extract_fluid.caps`, `unify.keep`).

| Parameter | Kind | Meaning |
|---|---|---|
| `inputs` | json | the parts it reads, node ids (the command resolves ids, paths, groups and part names) |
| `outputs` | json | the parts its last run made, node ids; written by the run |
| `auto_run` | bool, true | run again 300 ms after a parameter or an input changes (decisions log G12) |
| `primitive` | | `shape` box \| cylinder \| sphere; `size` (3 lengths: box edges; cylinder radius and length; sphere radius); `position` (box lowest corner, cylinder base centre, sphere centre); `axis` x \| y \| z (cylinder) |
| `boolean` | | `op` fuse \| cut \| common; `tools` (json part ids); `fuzzy` (length, 0); `simplify` (bool, true: unify a fuse's or a cut's faces on one surface, its history merged) |
| `enclosure` | | `shape` box \| cylinder \| sphere; `relative` (bool, true: `factors` are multiples of the bodies' extent along each axis, false: the absolute `margins`); `factors` (6 numbers, dimensionless, `3 8 3 3 3 3`: the margins for `-x +x -y +y -z +z`); `margins` (6 lengths in the same order, used when `relative` is false); `flow` +x \| -x \| +y \| -y \| +z \| -z (names the `inlet`, the upstream face, and the `outlet`); `axis` x \| y \| z (the cylinder's axis); `symmetry` (json list of `"x"`, `"y"`, `"z"`: planes through the bodies' bounding-box centre, the positive half kept); `ground` (bool, false: the `-z` face is `ground` and its relative `-z` margin is zero, so it sits at the bodies' lowest point); `subtract` (bool, true: the output is the enclosure cut by the bodies, the fluid domain; false: the primitive alone); `fuzzy` (length, 0: the cut's fuzzy value); `tag_walls` (bool, true: wetted body faces without a tag get `walls`) |
| `extract_fluid` | | `mode` region \| cavity (region: the volume around the seed once the openings are capped; cavity: the inner voids of closed bodies); `seed` (3 lengths, a point inside the fluid); `caps` (a tag reference: the faces that seal the openings, optional); `auto_cap` (bool, true: cap the closed free-edge loops a planar surface fits); `fuzzy` (length, 0); `min_volume` (volume, 0: cavities below it are skipped); `cap_tag` (string, `caps`: the tag the cap faces of the output get). One output per cavity (`<input>_fluid`, `_fluid_2`, …), or the one region solid (`<input>_fluid`), which carries `geometry.seed_outside` with the candidates' volumes and centroids when it holds no seed point |
| `heal` | | `tolerance` (length, 1e-6 m: the precision the fixes work to, and their smallest allowed tolerance); `max_tolerance` (length, 1e-3 m: the largest tolerance the result may carry, enforced with `ShapeFix_ShapeTolerance`); `small_edges` (length, 0: `ShapeFix_Wireframe` merges and drops the edges below it; 0 is off). One output `<first input>_healed`, whose faces follow the fixer's re-shape context. Report: `wires_fixed`, `faces_fixed`, `shells_fixed`, `small_edges_dropped` (each 0 or 1: the fixer's `Status*` flags folded into counts), `changed`, and the largest tolerance before and after |
| `sew` | | `tolerance` (length, 1e-5 m: the gap below which faces are sewn); `make_solid` (bool, true: every closed shell of the result becomes a solid, open shells stay shells); `non_manifold` (bool, false: the sewing tool's non-manifold mode). One output from **all** the inputs, `<first input>_sewn`, its faces following the sewing tool's `Modified`. Report: `free_edges_before` and `free_edges_after` (counted across the faces of the compound: the count in `brep.py` reports 0 for an unsewn compound of loose faces), `shells`, `solids`, `degenerated` and `multiple_edges` |
| `orient` | | no parameters. Every solid goes through `BRepLib.OrientClosedSolid_s` and every shell no solid holds through `ShapeFix_Shell.FixFaceOrientation`. One output `<first input>_oriented`; faces map to themselves, so tags survive. Report: `solids_flipped` (the volume's sign, not the tool's answer) and `faces_reoriented` |
| `solidify` | | `tolerance` (length, 1e-5 m: the seam that runs first); `fuzzy` (length, 0: the volume maker's fuzzy value); `avoid_internal` (bool, true: faces between two volumes are left out). One output per solid the faces bound, `<first input>_solid`, `_solid_2`, …, with the seam's history merged into the maker's; `geometry.op_failed` "no closed volume could be built" when they bound none |
| `cap` | | `max_size` (length, 0: cap only the loops whose bounding-box diagonal is at most it, so a small opening is sealed without shutting the inlet; 0 caps all); `planar_only` (bool, false: leave an opening no plane fits open); `tolerance` (length, 1e-5 m: the free-edge search's and the seam's tolerance). `ShapeAnalysis_FreeBounds(shape, tolerance, False, False).GetClosedWires()` gives the loops; a plane through one (`BRepLib_FindSurface`) gives a planar cap, else `BRepFill_Filling` a smooth one, else the loop is left open and reported. One output from **all** the inputs, `<first input>_capped`, its faces following the sewing tool's `Modified`. The cap faces are the tag `<first input>_caps` (kind `boundary`, so the user renames it to inlet and outlet and hands it to `extract_fluid` as its `caps`); they are new faces and unmapped by design. Report: `loops`, `capped_planar`, `capped_smooth`, `left_open` and `closed` |
| `unify` | | `linear_tolerance` (length, 1e-6 m: the distance within which two faces count as one surface); `angular_tolerance` (angle, 1e-3 rad: the angle within which they do); `keep` (a tag reference: faces that stay apart — `ShapeUpgrade_UnifySameDomain.KeepShapes` needs their edges with them, which the run adds, because a face alone in the map is still merged on this OCP). `ShapeUpgrade_UnifySameDomain(shape, True, True, False)`. One output `<first input>_unified`, its faces following `History()`. Report: `faces_before`, `faces_after`, `edges_before`, `edges_after` (the edges are `TopExp.MapShapes`, each once) |
| `slivers` | | `size` (length, 1e-4 m: the face size and edge length below which a sliver goes); `holes` (bool, false: also remove every inner wire whose loop bounds less than `size²`, through `ShapeUpgrade_RemoveInternalWires`). `ShapeFix_FixSmallFace` (`FixSpotFace`, `FixStripFace`), then `ShapeFix_Wireframe` (`ModeDropSmallEdges`), then a `ShapeFix_Shape` at OCCT's default precision; the three share one re-shape context, so the faces follow it. One output `<first input>_slivered`. Report: `spot_faces`, `strip_faces`, `small_edges` and `wires_removed` (this OCP returns the shape from `FixSpotFace`/`FixStripFace` rather than OCCT's flag, so the faces each step dropped are counted as the change in the face count), `faces_before`, `faces_after` |
| `defeature` | | `faces` (json members: the faces to remove, resolved against the inputs); `unify` (bool, true: unify the faces the removal left on one surface, its history merged into the defeaturing's with `BRepTools_History.Merge`, so a tag reaches the merged face). `BRepAlgoAPI_Defeaturing` with `SetRunParallel(False)` and `SetToFillHistory(True)`. One output `<first input>_defeatured`; `geometry.op_failed` when OCCT cannot remove them (this OCP binds no `BRepAlgoAPI_Algo.DumpWarnings`, so the error carries no OCCT text), and `geometry.op_invalid` when no face was picked. Report: `faces_removed`, `faces_before`, `faces_after` |

An array with a dimension is stored in SI; `operation.create` also takes its items as quantity
text (`"20 [mm]"`). The op node's status: `new` until its first run ends, `ok`, `stale` (a
parameter, an input's version or a referenced tag changed, or a run was cancelled), `failed`
(`meta.error`). `meta.run` is the last run: `{signature, ok, at, seconds, worker_seconds,
version, outputs, warnings, mapped, report}`; `meta.derives_from` lists the parts and tags it
reads, an output's lists its op, so `stokes.model.graph.mark_stale` reaches them.

`part` gains two parameters: `basis` (hex text, the B-rep version: the import writes
`fingerprints.basis_for(stamp, solid, faces)` when the file is read, an operation writes
`output_basis(seed, OCP version, index)`; `basis_of` prefers it, so an old project falls back to
the hash of the mesh's stamp) and `derived` (the op id, empty for an imported part). An output is
`stable_id("n", f"{op_id}/output/{i}")`, named after the module's output name (made unique),
coloured as its first input, with `family: brep`, `faces`, `bbox`, `triangles` and no `source`.

### Shape cache

`stokes/geometry/shapes.py`: `<project>.stokes/cache/geometry/<part id>.brep` (OCCT binary,
`BinTools`, no triangulation; `BRepTools` text when this OCP has no `BinTools.Write_s`) and a
sidecar `<part id>.brep.json` `{version: 1, part, basis, units, scale, faces, format, bytes,
crc32}` (`scale` = metres per stored unit: mm for STEP, IGES and outputs, a BREP's own unit).
A write removes the old sidecar, writes the B-rep, then the sidecar, which commits the entry;
a reader takes it only when the B-rep's size and CRC-32 are the committed ones (no rename: a
rename costs about 8 ms on Windows, twice per part). Reads are capped at 2 GB. An unsaved project
keeps the cache in a session scratch folder; a save copies the entries over in the background.

`ShapeProvider.load(spec)` with `spec = {part, name, basis, source, solid, units, brep,
derived}` (`part_spec(node, folder, basis)` builds it): the cache when the sidecar's basis is
the spec's; else `read_source` (once per file and job), refused with `geometry.unreadable` when
the file changed since the part was imported, then written to the cache; a derived part without
its cache is `geometry.op_invalid` naming the op to run again. Every shape comes back in mm.
`source_reads`, `cache_hits` and `writes` count what a job did. The import worker writes each
solid's entry just before it first tessellates it, at the version the store writes on the part.

### Worker job

`GeometryWorker.operation(job, on_progress, on_shape, on_solid, cancel, timeout, label)`; the job
is `{kind: "operation", op_type, op, seed, params (SI, with <tag>_members), inputs: [spec...],
cache_dir, workdir, passes}`. Messages: `progress {stage, fraction, message}`; per output
`shape {index, count, part, basis, name, faces, valid, closed, volume (m³), bbox (m), history:
{input part: {input face id: [output face ids]}}, history_source: occt | same, tags: {name:
[output face ids]}, report}`; the `solid` messages (coarse, then fine, with adjacency) as an
import sends them; then `done {seconds, version, report, warnings}`. Face ids are `TopExp_Explorer`
order with repeats (`operations/faces.face_list`, `ids_of`, `map_history`). The parent checks
every frame and recomputes each output's id and basis. Errors carry their `details` (64 KB).

`GeometryWorker.export_step(specs, out, names=...)` (T-030): the job is `{kind:
"export_step", parts: [spec...], names: [the nodes' names], out}` and it answers `exported
{path, solids, names, provider: {source_reads, cache_hits}, seconds}` then `done`. Every part is
loaded through the shape provider, so one export mixes source files and derived parts; the writer
(`brep.write_step`) scales each shape from millimetres to metres and declares metres, and names
each solid the node names it. A part with neither a cached B-rep nor a readable source is
`geometry.unreadable`, naming the part; a mesh-only part is `geometry.mesh_only`.

### Module contract

`stokes/geometry/operations/<type>.py` exports `run(ctx: OpContext) -> OpResult` and nothing
else (`operations/base.py`): `ctx.params` (SI JSON), `ctx.inputs` (`OpInput(part, name, shape in
mm, family)`), `ctx.mm()`, `ctx.mm2()`, `ctx.mm3()`, `ctx.input(part)`, `ctx.faces(members)`
(stored members to the inputs' faces), `ctx.progress(stage, fraction, message)` and
`ctx.check(shape)` (`{valid, closed, volume, bbox, faces, free_edges, solids, shells}`, SI: the
before-and-after summary every report holds as `check_before` and `check_after`). An `OpOutput`
is `(name, shape in mm, history, tags, report)`: `history` a `BRepTools_History` or an
algorithm with `Modified`, `Generated` and `IsDeleted`, None when faces map only to themselves;
`tags` output faces to put under a tag. Fail with `OperationFailed` (OCCT could not) or
`OperationInvalid` (inputs or parameters). `tests/operations_support.py` has `run_op` (a module in
this process) and `create_and_wait` (through a session).

### Commands

| Command | Parameters | Result / notes |
|---|---|---|
| `operation.create` | `type`, `inputs` = [], `name`, `params`, `auto_run` = true, `wait` = false, `node_id` | `operation.info` of the new op. One undo step: the group when missing, the op node, `visible: false` on every part it reads; the run starts. Mesh-only inputs refuse with `geometry.mesh_only` before any worker starts. `wait` waits for the run (refused on the server's event loop) |
| `operation.run` | `op`, `wait` = false | runs it now, cancelling a run under way; refused when it cannot run; journaled, no undo step |
| `operation.cancel` | `op` | `{op, cancelled}`; the op goes stale and is not retried until something changes; not journaled |
| `operation.delete` | `op`, `keep_outputs` = false | one undo step: the op and its outputs go and the parts it read are shown again, or the outputs stay as plain parts (`derived` cleared, cache kept). Refused (`model.referenced`) while another op reads an output, unless kept |
| `operation.list` | | every op: `{id, path, name, type, label, status, stale, auto_run, inputs, outputs, error, run, state}` |
| `operation.info` | `op` | the same with `params` and `report` (the module's, plus `outputs`, `unmapped` and `provider` counts) and `state` (`idle`, `waiting` for its debounce, or `running` with stage and fraction) |
| `operation.types` | | `{types: [{type, label, built, min_inputs, part_params, tag_params, schema}]}`: the cards' forms; no project needed |

`op` is an id, a path or a name under `/geometry/operations`. **Undo** of a create cancels the
run, removes the op and every part it made (with their meshes), takes those parts out of every
tag, removes the tags its runs created that are left empty, and shows the inputs again; **redo**
creates it again and runs it: the same ids, versions and tags. Outputs are added where commands
run when their `shape` message arrives, never journaled. **Journal replay** settles between
records (`Session.settle` and the `settle` hook): a record after an operation runs once the
operation has, which `setup.defaults` relies on (ADR-0022 decision 10.1).

### Tags through the history

When an output arrives, each tag holding faces of a part the run read gets the output faces they
became, under the same tag, as the output's member at the output's basis (replacing the member
an earlier run gave it). A whole-part member maps every face. A member stale against its input
is not mapped. Faces no output received are listed in `report.unmapped` (`[{tag, name, part,
faces, count}]`, plus stale members with `reason: "stale"`), never dropped silently: a cut
lists the tool's end faces it cut away. `OpOutput.tags` create a tag of that name (kind
boundary, id derived from the op's) or extend it. These are model edits outside the undo stack,
emitted as `node.changed {param: "members"}` and `tag.changed`.

### Re-runs

`SessionExtension.on_event(name, data)`, called by `Session.emit` after every event, lets the
service hear `node.changed` of an op (any parameter but `outputs`), of a part it reads (its
`basis` changes on a re-import or an upstream run) and of a tag it references, `node.removed`, and
`history.changed` (undo and redo emit nothing else). An op is up to date when its last run
succeeded at the signature its parameters and its inputs' versions give now and no part it reads
is stale; otherwise it and its outputs go `stale`, and with `auto_run` it runs again 300 ms after
the last change, cancelling a run of it under way. A run waits for imports and operations still
making its inputs, and runs first the operations whose outputs lost their cached B-rep. After a
run, one worker is started ahead for the next. Opening a project runs the operations whose
outputs have no mesh, or that never finished, and judges the rest.

### Events and errors

| Event | Payload |
|---|---|
| `geometry.op_progress` | `{node_id, path, stage, fraction, message}`, at most ten a second |
| `geometry.op_done` | `{node_id, path, outputs, seconds}` |
| `geometry.op_failed` | `{node_id, path, code, message, details}`; `geometry.cancelled` for a cancel; nothing for a run replaced by the next, undone or closed |

Outputs arrive with `node.added` or `node.changed` and their meshes with `geometry.part_ready`.
Errors: `geometry.op_invalid` (inputs gone or not parts, too few, a type not built yet, an op
that would read its own output, a derived input without its cache and no op to rebuild it),
`geometry.mesh_only`, `geometry.op_failed` (OCCT could not), `geometry.seed_outside`
(`extract_fluid`), `geometry.worker_failed`, `validation`.

## Compatibility

ADR-0021 renames persisted names (decisions 2.3 and 2.4: named selections become tags). Projects
and journals written before a rename keep working. The old names are in tables in
`stokes/api/legacy.py`, and the API resolves them wherever they can come in:

| Was | Becomes |
|---|---|
| node type `named_selection` | `tag` (alias kept: `legacy.NODE_TYPES["named_selection"] = "tag"`) |
| `/geometry/named_selections` | `/geometry/tags` (`legacy.PATHS`) |
| `selection.create` … `selection.by_name` (13 commands) | `tag.create` … `tag.by_name`, same parameters and results (`legacy.COMMANDS`, one entry each) |
| parameter `selection` | `tag` |
| event `selection.changed` | `tag.changed` |
| error codes `selection.invalid`, `selection.stale` | `tag.invalid`, `tag.stale` |
| `geometry.export_stl(selections=...)` | `tags=...`; `selections` still accepted (both optional; refuse both given) |
| `stokes selection VERB` | `stokes tag VERB`; `stokes selection` stays as a hidden alias (registered without a help text, so it keeps out of `stokes --help`) |
| labels "Named selection", "named selections" | "Tag", "tags" |
| `SELECTIONS_GROUP`, `SELECTIONS_PATH`, `SELECTION_TYPE` in `geometry/model.py` | `TAGS_GROUP = "tags"`, `TAGS_PATH`, `TAG_TYPE = "tag"`; keep the old constant names as aliases of the new values for one release |

| Old name | Table | How it resolves |
|---|---|---|
| Command | `legacy.COMMANDS`: old to current | `CommandRegistry.alias`. `get(old)` returns the current command, and the session journals the current name. `names()` lists current names only; `schema.commands` lists each command's old names in its `aliases`. A name is a command or an alias, never both |
| Parameter | `legacy.PARAMETERS`: current command to {old to current} | `Session.execute` renames it before validation. Both names in one call is a `validation` error |
| Node path | `legacy.PATHS`: old prefix to current | Before validation, `Session.execute` rewrites every string parameter, and every string item of a list parameter, that equals an old prefix or starts with it followed by `/`. Node ids, names, nested objects and the dotted spelling (`geometry.named_selections.inlet`) are not rewritten |
| Node type | `legacy.NODE_TYPES`: old to current | `NodeTypeRegistry.alias`. `get(old)` returns the current spec, `node.add` stores the current type, and `node.types` lists current names only |

`legacy.NODE_TYPES` is the dict `stokes.model.schema.NODE_TYPE_ALIASES`: the model needs it and
must not import the API, so its entries are written there. Names chain: when a current name is
renamed again, add the new pair and keep the old one. Journals record current command names,
parameter names and paths. `node.add` records the `type` it was given, which replays through the
node-type alias.

**Project schema 2.** `project.json` carries `schema_version`, and Stokes writes 2. A version-1
file is migrated when it loads. Node types go through `legacy.NODE_TYPES`, and the group
`/geometry/named_selections` becomes `/geometry/tags`, with its id, children and parameters. When
the project has a `/geometry/tags` group already, the old group's children move into it after its
own, and a name that is taken there gets `_2`, `_3` and so on. Plain groups inside stay legacy
rows. A version-2 file loads as it is, and a newer file is refused (`model.persistence`). Stokes
builds from before schema 2 refuse a version-2 file.

## Performance

Measured on the development laptop, with other sessions loading it, on the 4,500-part M0 gate
project (20,250 faces, 585 k triangles):

| Operation | Time |
|---|---|
| Grow on one part (a double-click) | 1 to 7 ms |
| Grow over all 4,500 parts, adjacency cached | 0.9 s |
| Growth kernel alone | 2 ms |
| Building the adjacency of every part, once per mesh | about 2.2 s, mostly the weld and edge sort |
| `tag.create` with one face on each part | 2.3 s |
| `geometry.check` of all parts (tessellation) | 4 to 6 s |
| `geometry.check`'s B-rep pass | a background job over the shape cache: one source read per file the cache cannot serve, none on a repeat |
| `geometry.check` of a 200-part `pin_grid(10, 10)` (2.3 MB STEP, cache warm) | command returns in 0.33 s; the B-rep job 9.4 s; a repeat 0.17 s (T-031, 2026-10-06) |
| Binary STL of all triangles | about 3 s |
| Face adjacency, built in the import worker (one weld per tessellation batch) | 0.09 s for 300 solids; reading it for `grow` is 0.08 s against 0.18 s for the lazy build |

The face adjacency `tag.grow` needs is built in the import worker, one weld per tessellation
batch, and the store writes **one archive per project**, `<name>.stokes/cache/geometry/adjacency.bin`,
once when the import finishes (per-part files cost more to write and read than they save for
assemblies of many small parts). Each entry carries the mesh's face and triangle counts;
`selection_cache` uses an entry when those counts match the mesh in hand and builds adjacency when it
is absent, of another version or for another mesh. The format lives in
`stokes/geometry/adjacency_cache.py`.

The budget of 300 ms for 20 M triangles needs the adjacency built ahead of use, in the import
worker or in a native kernel (ADR-0020: numpy first, a kernel when measured).
