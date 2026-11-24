# ADR-0021 · Geometry preparation: import formats, tags, picking, viewing, repair, fluid volumes, enclosures and the operations framework

**Status:** accepted · 2026-10-04 · relies on ADR-0003 (OCCT through OCP), ADR-0006 (`.stokes` directory and JSON tree), ADR-0012 (quantities), ADR-0014 (import in a worker, `part` nodes, face ids), ADR-0019 (batched rendering), ADR-0020 (selections, operations, picking; **this ADR amends its decisions 2, 3, 4, 5 and 7**)

## Context

The owner asked on 2026-10-04 for six things in geometry import: the common free formats and
"Import geometry" as the name; a generic term instead of "named selection", with names on
faces and bodies that boundary conditions can use (today a click selects the whole body and
nothing in the UI calls `selection.*`); show, hide and transparency per body and per boundary;
repair tools; volume extraction from a watertight body; unions, subtractions and enclosures.
Slice 1 of ADR-0020 exists (selection model, fingerprints, 13 commands, checks, exports). There
are no operations, no derived parts, no repair and no face picking in the UI
(`.mailbox/plan/research-code.md`). OCP 8.0.1 reads STEP, IGES, BREP, STL and glTF, not OBJ or
3MF; every repair and boolean API named below is present and the main ones were smoke-tested
(`.mailbox/plan/research-external.md`). Constraints that shape the decisions: no GPL in the
process; persisted names (`named_selection`, `/geometry/named_selections`, `selection.*`,
`geometry.import_step`, the action id `project.importStep`) must keep old projects loading and
old journals replaying, while `persist.MIGRATIONS` is empty; the batched renderer orbits 4,500
parts at a p95 of 16.6 ms against a 16.7 ms budget, so nothing per face or per instance may be
added to the main pass; the worker that builds most of this is a cheaper model working from
briefs, so cores that are easy to get subtly wrong go to Claude and the rest is split small.

## Decision

### 1. Import geometry: formats and the `geometry.import` command

1.1 The command is **`geometry.import(path, name?, units?, wait?, node_id?)`**. `geometry.import_step`
stays as an alias of the same handler (decision 2.4), so journals written before this ADR
replay. New journals record `geometry.import`. The CLI verb `stokes geometry import` is
unchanged and accepts every format below. The workspace action id `project.importStep` and
its `Ctrl I` chord are kept, because users' workspace files store them; only its title,
labels, prompts and file filter change to "Import geometry…". `geometry.formats` (no project
needed) lists the formats so the front end builds the picker filter from the API.

1.2 The format matrix. Two families; the family is written to the part as the new `part`
parameter `family` (`brep` | `mesh`, default `brep` when absent, so old projects need nothing).

| Format | Suffixes | Family | Reader (all in OCP, numpy or the standard library; no new dependency) | Units | Names and colours |
|---|---|---|---|---|---|
| STEP | `.step .stp` | B-rep | `STEPCAFControl_Reader` (as today) | from the file | product names and solid colours (as today) |
| IGES | `.iges .igs` | B-rep | `IGESCAFControl_Reader`, then the same XCAF walk as STEP | from the global section (unit flag), else mm with a warning in the result | entity names and colours |
| BREP | `.brep .brp` | B-rep | `BRepTools.Read_s` (binary files through `BinTools` when the header says so) | none in the file: the `units` parameter, default mm | none; parts named from the file |
| STL | `.stl` | mesh | own numpy reader, binary and ASCII, size-capped | none: `units`, default mm | ASCII `solid <name>` blocks become parts |
| OBJ | `.obj` | mesh | own numpy reader (`v`, `f` with polygons fan-triangulated and negative indices; `o`/`g` become parts; materials ignored) | none: `units`, default mm | object and group names |
| 3MF | `.3mf` | mesh | own reader: `zipfile` with member and total size caps, `xml.etree.ElementTree` on `3D/3dmodel.model` (any `DOCTYPE` refused before parsing; Python 3.13's expat is safe against entity expansion), `object/mesh`, `components` to depth 8, `build/item` transforms | the model's `unit` attribute | object names; colours not in v1 |

An IGES file holds solids, shells or loose faces: a part is one solid when the file has solids,
else one shell, else one compound of all free faces (`kind` `faces`), which the user turns into
a solid with the Sew and Make solid operations (decision 5). glTF is readable by OCP and may be
added later on the mesh path; it is not in v1. Parasolid, ACIS, JT, native CAD, DXF, 3DS and
the other OCCT commercial add-ons are out (ADR-0003, docs/00 D3): the UI says "export STEP
from your CAD tool". `units` given for a format whose file declares units is a `validation`
error.

1.3 What a mesh-only part cannot do. It has no B-rep: its "faces" are regions of the
triangulation split at a 30° feature angle (the growth angle of `geometry-schema.md`), numbered
by first appearance. It can be tagged, checked (tessellation checks), exported to STL, shown,
hidden, coloured and made translucent, and it can be the body of an enclosure (decision 6)
without subtraction, which is what snappyHexMesh needs anyway. B-rep checks report
`brep: {checked: false, reason: "mesh-only part"}`; STEP and BREP export, repair, booleans,
`extract_fluid` and an enclosure with `subtract` refuse with `geometry.mesh_only` and that
hint.

1.4 One reader entry point, `read_source(path, units=None)`, dispatches on the suffix and is
the only place the three worker jobs (`import`, `check_brep`, `export_step`) and the shape
cache (decision 8.3) read a file. `check_step_file` becomes `check_source_file` with a header
probe per format and the same size limit; the STEP name stays as a wrapper.

### 2. Tags: the generic term, and the rename with compatibility

2.1 The term is **tag** (confirmed by the owner on 2026-10-04). A tag is a name put on faces and bodies alike, which is what the owner
asked for ("assign names to faces and bodies"); several tags can sit on one face (a face can be
`inlet` and `heated`); it is no vendor's term (Ansys owns "named selection", Fluent and
OpenFOAM mean something specific by "zone", SimScale says "saved selection", COMSOL
"selection"); it is a noun that is not also a verb in this UI, unlike "set" (`set.set`, "Set
value"); "group" is already the tree's folder type; and the tree draws these rows with the tag
icon today. Kinds stay `boundary` | `region` | `plain`: faces that become a patch, bodies that
become a mesh region, a working set.

2.2 The full rename. Node type `tag`; group `/geometry/tags`; commands `tag.*` with the verbs
of ADR-0020 decision 3 (`create`, `add`, `remove`, `set`, `invert`, `grow`, `rematch`,
`rename`, `delete`, `list`, `members`, `by_color`, `by_name`) plus `tag.region` (decision 3.5);
event `tag.changed`; error codes `tag.invalid` and `tag.stale`; the parameter name `tag`; the
CLI `stokes tag`; the labels "Tag" and "Tags". `geometry.export_stl` takes `tags` and still
accepts `selections`. Internal module names (`selection.py`, `selection_cache.py`,
`SelectionError`) need not change.

2.3 Old projects load through a **schema migration**: `SCHEMA_VERSION` becomes 2 and
`MIGRATIONS[1]` rewrites node type `named_selection` to `tag` and renames the group
`named_selections` under `/geometry` to `tags` (merging into an existing `tags` group). A
plain `group` left inside it stays a legacy row, as `geometry-schema.md` already says.

2.4 Old journals replay through **aliases** in three places, all in the API layer and the
model: `CommandRegistry.alias(old, new)` resolves `selection.*` and `geometry.import_step` in
`get()` (the schema lists aliases, `names()` lists canonical names only, and the journal records
the canonical name); `NodeTypeRegistry.alias("named_selection", "tag")` resolves the old type
and `AddNode` stores the canonical one, so `node.add type=named_selection` yields a `tag`; and
a legacy path rewrite in `Session.execute` maps every string parameter, and string item of a
list parameter, that starts with `/geometry/named_selections` to `/geometry/tags`. `stokes
selection` remains as a hidden alias of `stokes tag` for one release.

2.5 How boundary conditions consume tags (amends ADR-0020 decision 2). A new node type
**`boundary`** (registered with the geometry types) carries one parameter, `tag` (kind `ref`,
`ref_type` `tag`). "Assign boundary" creates `/regions/fluid/boundaries/<tag name>` of that
type pointing at the tag; a tag of kind `region` will reference a region the same way in M3.
The M3 compiler maps boundary → tag → patch, and the patch is named after the tag (ADR-0020
decision 8), so renaming the tag renames the patch everywhere. Boundaries created before this
ADR as plain groups stay valid and are listed as legacy. A boundary row in the tree shows its
tag's swatch and eye by proxy (decision 4), which is the "next to each body or bc" the owner
asked for.

### 3. Picking faces and bodies in the viewport (amends ADR-0020 decisions 3 and 4)

3.1 The store holds a **pick**, not a single patch: `pickMode` (`body` | `face`), `picked.bodies`
(part node ids, in picking order) and `picked.faces` (face ids per part node id). `selectedPatch`
stays as the active member (the last body picked, or the owner of the last face), so the
properties card, the context toolbar's anchor and the measure tool keep working. Gestures: click
replaces, Shift-click adds, Ctrl-click toggles, Esc clears; in face mode a double-click grows the
face by the default angle. The mode is a two-segment control at the start of the Modes island
(bottom-left) and a palette action `selection.mode`; it is not persisted.

3.2 The pick comes from the **CPU raycaster** that exists (3.4 to 3.8 ms at 4,500 parts), which
returns part and triangle; the face id is `faceIds[triangle]`. Hover and click stay on it until
the id buffer exists and is measured faster. The switch rule: box and lasso use the id buffer
from the start; hover and click move to it when the probe shows the raycast above 8 ms median
on the owner's models, or once the id buffer is in and measured below the raycast.

3.3 The **GPU id buffer** (ADR-0020 decision 4, made concrete): an offscreen RGBA8 render of the
part layer in which R and G carry a 16-bit part index and B and A a 16-bit face id, drawn on
demand (dirty on camera or content change), read back for the pointer or the gesture's
rectangle. The face id is a per-vertex attribute, which is exact because the import welds
vertices by position **and face id** (`step._mesh`), so no vertex is shared between faces. The
part index is a uniform on an own mesh and, for a batch, comes from the instance id through a
lookup texture in a patched shader. Hidden and isolated-away parts skip the pass; translucent
parts are drawn and therefore pickable, as in CAD tools. Parts above 65,535 faces lose face
picking beyond that id and say so in the console.

3.4 **Box** (drag on empty space beyond 4 px, where `onPointerUp` already hands over from the
orbit) replaces; Shift adds; Ctrl removes; **lasso** is the same with a polygon test. Both run
one read-back at gesture end. In body mode a part is picked when any of its pixels is inside; in
face mode a face is.

> **Amended 2026-10-06.** As first written this left a plain drag on empty space as the box, which
> takes the left button away from the camera: the shipped build could only orbit by pressing on a
> part, and a user reported "the left click only allows drag and selection now, there's no way to
> rotate the view". Rotation must never be unreachable, and there is no single mouse convention to
> copy: CAD tools rotate on the middle button (FreeCAD's default, SolidWorks), CFD
> post-processors rotate on the left (ParaView). So the bindings became a **choice**, offered by
> the Modes island's **Mouse** menu, and every scheme rotates from anywhere in the viewport:
>
> | Scheme | Rotate | Pan | A left drag |
> |---|---|---|---|
> | `cad` (default) | middle | right | orbits on a patch, box on empty space |
> | `paraview` | left, anywhere | middle | orbits; the box needs Shift |
> | `blender` | middle | Shift+middle | orbits on a patch, box on empty space |
> | `toggle` | middle | right | box, or orbits when the menu's *Orbit with left drag* is on |
>
> The box keeps its meanings — plain replaces, Shift adds, Ctrl removes — and Alt or the Lasso
> toggle still draws the lasso in every scheme. `view.nav.cycle` (`Shift N`) walks the schemes.
> The bindings live in `CameraRig.setNavMode` and `ViewportScene.setNavMode`; the list and its
> wording are `NAV_MODES` in `web/src/app/actions.ts`.
>
> **Amended again 2026-11-05: `paraview` ("Orbit") is the default.** The first amendment left `cad`
> as the default, in which only a drag that *starts on a part* orbits. The mixing-elbow walkthrough
> found it the same defect in a new place: the first-run hint says "Drag to orbit", a drag on the
> empty background drew a box instead, and in the Boundaries step that box silently changed the
> face selection a user was about to assign. A first-time user's first move is a left drag, so
> that now orbits from anywhere; a click still selects, the box is Shift-drag or the Lasso toggle
> (which now also works under this scheme: it used to be ignored while the left button orbited),
> and the middle and right buttons pan. `cad`, `blender` and `toggle` stay on offer in the Mouse
> menu for people whose hands expect the middle button to rotate. A saved `preferences.mouse.scheme`
> still wins; only a new workspace changes.
>
> **Third fix, same day: the left button was bound to nothing under `cad`, `blender` and `toggle`.** The
> text above said "a drag that starts on a part orbits", but `CameraRig.setNavMode` set `LEFT = null` there
> and `OrbitControls` ignores an unbound button, so such a drag never turned the camera; only the middle
> button did. A saved `cad` scheme (the old default, written to `~/.stokes/workspace.json`) therefore
> still looked broken after the default changed. `LEFT` is now ROTATE in every scheme; the scene already
> disables the controls for a box, lasso, stroke or handle drag. In the Results stage nothing is
> selectable (the surface under the pointer is a slice or a cut), so a plain left drag there always orbits.

3.5 **Drawing.** The live face selection extends `FaceHighlight` to many faces across parts: one
geometry of copied, re-indexed triangles in the accent colour at the present opacity and
polygon offset; the hovered face keeps the borrowed-buffer overlay. The active body keeps its
outline; other picked bodies get the tint overlay over all their faces. A **tag** is drawn as
one overlay mesh per visible tag, built the same way from its member faces, in the tag's
`color` with a lit material (the part's metal and roughness) at the tag's `opacity`; it is
rebuilt when the tag's definition or colour changes, when a part's mesh arrives or is replaced,
and when a part is hidden. A tag with `visible: false` hides its faces by filtering the index of
the parts that hold them (the batch copy is replaced), so the raycast, the id pass and the
outline follow without extra work; whole-part members hide the part. Nothing per face is added
to the batches. **Switch condition:** when overlays exceed 2 M triangles or the orbit p95 rises
by more than 1 ms in the probe, tag colour moves into a per-vertex RGBA8 `tint` attribute on
every part geometry mixed in the look's shader (`batchable()` then requires position, normal and
tint), which costs no copies and no draw calls. *Taken in T-020 (2026-10-05):* with one tag over
every face of the 4,500-part benchmark, an overlay per tag doubled the triangles drawn and added
10.6 ms to the GPU frame (median of seven paired runs); the tint draws the same calls and
triangles as no tag at all. `tag.region(members, angle_deg)` returns the
feature-angle region around the given faces without touching any tag; it is what the
double-click and the context toolbar's Grow use before a tag exists.

### 4. Visibility, colour and opacity per body, per tag and per boundary

4.1 `part` and `tag` gain **`opacity`** (`number`, 0 to 1, default 1; readers default an absent
value and clamp). `visible` and `color` exist. A boundary shows and edits its tag's values.

4.2 One bulk command, **`geometry.style(nodes, visible?, color?, opacity?)`**: `nodes` are parts,
tags, groups (every part beneath) or boundaries (their tag); one `Composite` of `SetParam`s,
one undo step, one journal record, `node.changed` per changed node. The eye in a tree row and
the style popover call it; a tree with 4,500 parts therefore hides in one step, not 4,500. The
transient Hide (`H`) and Isolate (`I`) stay as view filters that are not saved; the eye is the
model's `visible`.

4.3 **Tree rows.** `part`, `tag` and `boundary` rows carry a 12 px swatch before the name and
an eye at the right, before the status dot, that shows on hover and stays while the node is
hidden (a group's eye shows the state of the parts beneath). The swatch opens a small popover
with the system colour picker (the theme editor's swatch pattern), an opacity slider with a
percentage field and "Opaque". A drag writes once, at the end. Keyboard users reach the same
from the row menu ("Hide", "Show", "Colour and opacity…"). Density, radii, tokens and spacing
follow `web/DESIGN.md`; nothing else is restyled.

4.4 **Viewport.** Opacity enters the batch key as a bucket: `(kind, tint, round(opacity × 10))`
for opacity below 1, so at most ten translucent batches per look. A translucent batch uses a
clone of the look with `transparent`, `depthWrite: false` and the bucket's opacity, keeps the
opaque path's culling and sorting settings (no per-frame sort: within one bucket parts draw in
insertion order, which is acceptable for a viewing aid), and is excluded from the outline's
occluder pass, so a selection outline shows through glass; the contact shadow keeps it. Below
the batching threshold and for highlighted parts, the own mesh gets a per-part material clone.
Translucent parts stay pickable; hidden ones are not. Budget: 4,500 parts with 50 translucent
orbit within +1 ms p95 of all opaque, measured with the probe before merging.

### 5. The repair toolset

Every tool is an operation (decision 8): it reads its inputs' B-rep from the shape cache, writes
a derived part, hides the input, carries tags through OCCT's history, and stores a `report`
(counts before and after, OCCT statuses, `geometry.check` summary before and after). Tolerances
are quantities in the project and are converted to model units in the worker. The names avoid
SpaceClaim's (Stitch, Gaps, Missing Faces, Split Edges, Extra Edges, Merge Faces, Small Faces,
Duplicates, Inexact Edges, Tangency, Simplify, Fill, Volume Extract, Enclosure).

| Op type | UI name | Fixes | OCCT | Parameters | Changes geometry |
|---|---|---|---|---|---|
| `heal` | Heal | out-of-tolerance edges, wire order and gaps, face and shell orientation, pcurves | `ShapeFix_Shape` (`SetPrecision`, `SetMinTolerance`, `SetMaxTolerance`, fix modes on), `ShapeFix_Wireframe.FixSmallEdges` when asked, `BRepLib.SameParameter_s` | `tolerance` (1 µm), `max_tolerance` (1 mm), `small_edges` (length, 0 = off) | yes |
| `sew` | Sew | loose faces and shells into shells and solids | `BRepBuilderAPI_Sewing(tol, sewing, analysis, cutting, non_manifold)`, then `BRepBuilderAPI_MakeSolid` + `ShapeFix_Solid` for closed shells | `tolerance` (10 µm), `make_solid` (true), `non_manifold` (false) | yes; several inputs give one output |
| `cap` | Close openings | open bodies: each closed free-edge loop gets a face | `ShapeAnalysis_FreeBounds.GetClosedWires`, planar `BRepBuilderAPI_MakeFace(wire, True)` when `BRepLib_FindSurface` finds a plane, else `BRepFill_Filling` (degree 3, 15 points, tol 1e-4) with the edges `C0`; then sew and make solid | `max_size` (cap only loops up to this size; 0 = all), `planar_only` (false), `tolerance` | yes; the new faces are tagged `<part>_caps` so the user renames them inlet and outlet |
| `unify` | Unify faces | faces and edges on one surface merged | `ShapeUpgrade_UnifySameDomain` (`SetLinearTolerance`, `SetAngularTolerance`, `KeepShapes` from the `keep` tag, `History()`) | `linear_tolerance` (1 µm), `angular_tolerance` (1 mrad), `keep` (tag) | yes |
| `slivers` | Drop slivers | spot and strip faces, small edges, small inner loops | `ShapeFix_FixSmallFace` (`FixSpotFace`, `FixStripFace`), `ShapeFix_Wireframe.FixSmallEdges`, `ShapeUpgrade_RemoveInternalWires(MinArea = size²)` when `holes` | `size` (0.1 mm), `holes` (false) | yes |
| `defeature` | Remove faces | fillets, holes and pockets whose faces are chosen; neighbours extended | `BRepAlgoAPI_Defeaturing` (`AddFacesToRemove`, `SetToFillHistory(True)`, `Build`, `IsDone`, `HasWarnings`), then `unify` when asked | `faces` (members), `unify` (true) | yes |
| `orient` | Fix orientation | inside-out solids and shells | `ShapeFix_Shell.FixFaceOrientation`, `BRepLib.OrientClosedSolid_s` | none | orientation only |
| `solidify` | Make solid | loose faces (IGES) into solids | `sew`, then `BOPAlgo_MakerVolume` (`SetIntersect(True)`, `SetAvoidInternalShapes`, `SetFuzzyValue`) | `tolerance`, `fuzzy` (0), `avoid_internal` (true) | yes; one part per solid |
| (check) | Check | reports only | `geometry.check` as today, plus the face ids of free-edge and small faces in `brep`, so the UI highlights them | | no |

Later, planned and not briefed now: `split` (by angle or plane; ADR-0020's open question stands),
`connect` (shared topology for conformal multi-region meshes: `BOPAlgo_MakeConnected`), `extend`
(extrude an inlet or outlet face by a length; ADR-0020 decision 5), offsets. OCP traps the
worker must respect: `BRepAlgoAPI_*` have `IsDone` but no `HasErrors`; `label.FindAttribute`
segfaults when the attribute is absent, so `IsAttribute` first; `Bnd_Box.Get` fails, use
`CornerMin`/`CornerMax`.

### 6. Volume extraction, enclosures and booleans (amends ADR-0020 decision 5)

6.1 **`extract_fluid`** has two modes. `cavity`: for each input solid, every shell other than
`BRepClass3d.OuterShell_s` becomes a solid (`BRepBuilderAPI_MakeSolid`, then
`BRepLib.OrientClosedSolid_s` so the volume is positive); voids below `min_volume` are skipped;
one output part per cavity, named `<part>_fluid`, `_fluid_2`, …. `region`: the faces of the
inputs plus the faces of the `caps` tag (sealing faces the user made or tagged), plus planar
caps on the remaining free loops when `auto_cap` is true, go through `BOPAlgo_MakerVolume`
(with `fuzzy`); the solid that contains the **seed** point (`BRepClass3d_SolidClassifier`,
`TopAbs_IN`) is the fluid. No solid containing the seed is `geometry.seed_outside`, and the
result lists the candidate solids' volumes and centroids so the UI can offer them. The seed is
picked in the viewport by clicking a face: the face centroid moved along the inward normal by
1 % of the part's size, editable as three lengths. Cap faces on the output are tagged `caps`;
the body's tags carry over through the history (walls stay walls).

6.2 **`enclosure`**: `shape` box | cylinder | sphere around the `bodies`; six per-side
`margins` (for a cylinder: radial and two axial; a sphere: one), **relative** by default
(multiples of the bodies' extent along that axis: 3 everywhere, 8 downstream of the `flow`
axis, default `+x`) or absolute lengths; `symmetry` lists up to three planes (`x`, `y`, `z`)
through the bodies' bounding-box centre, each cutting the enclosure to the positive half with
`BRepAlgoAPI_Common` against a half-space box; `subtract` (default true) makes the output the
enclosure **cut by the bodies** (`BRepAlgoAPI_Cut` with `fuzzy`), so the fluid domain is one
step, while `subtract: false` gives the primitive alone (refinement regions, mesh-only bodies).
The output's faces are tagged by their plane: `inlet` (upstream face), `outlet` (downstream),
`symmetry` (cut faces), `ground` (the `−z` face when `ground` is true), `far_field` (the rest);
wetted body faces without a tag get `walls` when `tag_walls` is true (default). The result
reports the blockage ratio (bodies' frontal area over the enclosure's cross-section, from
bounding boxes) and warns above 1 %.

6.3 **`primitive`** (box, cylinder, sphere with a placement) and **`boolean`** (`fuse` | `cut` |
`common`, `targets`, `tools`, `fuzzy`, `simplify` runs `unify` on the result for fuse and cut)
are as ADR-0020 decision 5, with `IsDone` checked, `BRepCheck_Analyzer` run on every output and
the history of `Modified`, `Generated` and `IsDeleted` mapped face by face.

### 7. UI for operations

A **Prepare** menu on the Geometry task strip and the context toolbar: Enclosure…, Extract
fluid…, Boolean ▸ (Fuse, Cut, Common), Repair ▸ (Heal, Sew, Close openings, Unify faces, Drop
slivers, Remove faces, Fix orientation, Make solid). Each opens an **operation card** generated
from the op type's schema (`operation.types`; the palette's form generator), pre-filled with the
current pick as `inputs` (or `faces`), with Run; progress reuses the import progress line
(`geometry.op_progress`) and Cancel; a stale operation's row offers Run. Cards do not restyle
anything; they are the palette form inside a floating card of the properties panel's kind.

### 8. The operations framework (amends ADR-0020 decision 5; replaces its "cached by a hash of inputs")

8.1 **Node types.** One `NodeSpec` per op type in `stokes/geometry/model.py` (the light module
`stokes.model` may import; specs are built on call and cost nothing at `import stokes.api`):
`primitive`, `boolean`, `enclosure`, `extract_fluid`, `heal`, `sew`, `cap`, `unify`, `slivers`,
`defeature`, `orient`, `solidify`. Every one has its own quantity parameters (ADR-0012, so
studies can vary them) plus `inputs` (json: part node ids), `outputs` (json: part node ids,
written by the run) and `auto_run` (bool, default true). Nodes live at
`/geometry/operations/<name>`; the group is created on demand like `/geometry/tags`.

8.2 **Outputs are parts** under `/geometry/parts` with `derived` (the op id), `basis`
(decision 8.4), `family: brep`, `faces`, `bbox`, `units: mm` (operations run in millimetres,
the unit OCCT reads STEP in; inputs in other units are scaled in the worker, parameters arrive in
SI and are converted there, and the tessellation converts to metres as the import does) and no
`source`; their ids are
`stable_id("n", f"{op_id}/output/{i}")`, so a replayed journal rebuilds the same ids, as
`part_id_for` does for imported solids. Creating an operation sets `visible: false` on its
inputs in the same undo step; the user may show them again.

8.3 **The shape cache.** Every part's B-rep is persisted as `cache/geometry/<part_id>.brep`
(binary through `BinTools` when this OCP has it, else `BRepTools`; atomic write; 2 GB cap; a
sidecar `<part_id>.brep.json` records the `basis` it belongs to). Imported parts are written by
the import worker as soon as the file is read, derived parts by the operation job. A
`ShapeProvider` in the worker loads a part from the cache when the sidecar's basis equals the
node's, else re-reads the source (`read_source`, solid index) and rewrites the cache; a derived
part whose cache is gone is recomputed by re-running its operation. From now on `check_brep`,
`export_step` and every operation read parts through the provider, which ends the 84 s re-read
of a 4,500-solid STEP for a check or an export and lets an export mix source files and derived
parts.

8.4 **Basis.** `part` gains `basis` (string). Import writes the hash `fingerprints.basis_of`
computes today; an operation writes `sha1(op type, canonical SI params, the inputs' bases, the
OCCT version)[:12]` plus the output index. `basis_of` prefers the node's parameter and falls
back to the legacy computation for old projects. Because a changed parameter or a changed input
gives a new basis, tag members on a derived part go stale exactly when they should, and
`tag.rematch` or the history (8.6) brings them back.

8.5 **Worker job `operation`.** One job per run: `{kind: "operation", op, params (SI), inputs:
[{part, brep, source, solid, units}], outputs: [{part, name}], cache_dir, workdir, passes}`.
Messages: `progress {stage, fraction, message}` (new), then per output `shape {index, part,
faces, valid, volume, bbox, history, report}` followed by the `solid` messages the import
already sends (coarse and fine), then `done`. Face ids are the `TopExp_Explorer` order with
repeats, exactly as `step._triangle_counts` numbers the triangles. Cancel kills the process as
the import does; the timeout is the import's. The parent side is `GeometryWorker.operation(...)`
beside `check_brep` and `export_step`, streaming like `run`.

8.6 **History.** `history` maps every input face `(part, id)` to output face ids through
`BRepTools_History` (`Modified`, `Generated`, `IsRemoved`) or the algorithm's own `History()`.
After a run, every tag that holds an input face receives the mapped output faces under the
same tag; unmapped faces are listed in the op's `report`, never dropped silently; the
fingerprint re-match is the fallback for ops without a history. New faces no input made (caps,
enclosure faces) get the tags decision 5 and 6 name.

8.7 **Commands** (`stokes/api/commands/operation.py`): `operation.create(type, name?, inputs,
params?, auto_run?, node_id?)` (pushes one undo step: the group when missing, the op node, the
inputs hidden; then starts the run), `operation.run(op)`, `operation.cancel(op)`,
`operation.delete(op, keep_outputs=false)` (kept outputs become plain parts whose cache stays),
`operation.list`, `operation.info(op)` (report, timings, history summary), `operation.types`
(schemas for the cards). `operation.create` and `operation.run` are journaled; a replay re-runs
the operations, so a journal needs no mesh. Events: `geometry.op_progress {node_id, stage,
fraction, message}`, `geometry.op_done {node_id, outputs, seconds}`, `geometry.op_failed
{node_id, code, message}`; meshes arrive through the existing `geometry.*` events. Errors:
`geometry.op_failed`, `geometry.op_invalid` (inputs), `geometry.mesh_only`,
`geometry.seed_outside`.

8.8 **Undo and re-run.** The op node is the placeholder; the run adds the output parts on the
session thread when the job reports them, as the import builds its group, not as a journaled
command. `revert` cancels the job, removes the op node and its outputs and restores the inputs'
visibility; redo recreates and re-runs. A change to an op's own parameter or to an input part's
`basis` marks the op and its outputs `stale` (`stokes.model.graph.mark_stale`, which exists
unused) and, when `auto_run` is true, re-runs it 300 ms after the last change, cancelling a run
of the same op that is under way. The hook is `SessionExtension.on_event(name, data)`, a new
optional hook `Session.emit` calls, so the operations service sees `node.changed` without a
model dependency.

### 9. Ownership and sequencing (what goes to which worker)

Claude builds the cores that are easy to get subtly wrong: the alias and migration layer
(2.3, 2.4), per-body opacity in the batches (4.4), the operations framework with its shape
cache, history and undo (8), the GPU id buffer with box and lasso (3.3, 3.4), and the M1 gate.
opencode builds, from briefs, the readers, the mechanical rename, the import UI, the CPU pick
model with the multi-face highlight and tag overlays, the style command and tree controls, each
operation module on top of the framework, the operation cards, and the merged check and export
tasks. The order and the owner of every task are in `docs/plans/geometry-preparation.md`.

## Alternatives considered

| Alternative | Why not |
|---|---|
| "Set" as the generic term | `set.set` for the replace command and "Set" as the UI's verb for assigning a value; OpenFOAM `faceSet`s are transient mesh objects, which these are not. |
| "Group" | The tree's folder type is `group`; two meanings in one tree. |
| "Zone" | Fluent's and OpenFOAM's word with specific meanings (cell zone, faceZone); the owner asked to leave vendor vocabulary. |
| Rename without aliases, fix old journals by hand | The registry raises `UnknownCommand` and the loader fails on an unknown node type; every saved project and journal would break. |
| Read OBJ with VTK, 3MF with trimesh or lib3mf | VTK ships with the `extract` extra, not `cad`, and imports in seconds; trimesh and lib3mf are new dependencies for formats a hundred lines of numpy and the standard library read. |
| Per-face colour and visibility as a per-vertex attribute now | It touches the approved look's shaders and `batchable()`, where regressions hide and no test catches them; overlays reuse a proven pattern and the switch condition is written down. |
| Per-instance opacity in one batch | Not possible in `BatchedMesh` without a shader change and a per-frame sort that costs 5 to 7 ms at 4,500 parts; buckets keep the opaque path untouched. |
| Repair that edits the part in place | Face ids and the basis change, so every tag goes stale with no way back; a derived part keeps the input, undo and re-run. |
| Re-run the recipe instead of persisting B-reps | Every check, export and operation would re-read an 84 s file; the cache is small, regenerable and makes exports across files trivial. |
| Operations keyed by a hash cache (ADR-0020's wording) | The basis on the part is the same hash and does more: it makes staleness of tags and dependents fall out of the existing fingerprint rules. |
| GPU id buffer before any face naming | It is Claude-only work; the raycaster already returns the triangle, so the owner can name faces weeks earlier with the CPU path. |

## Consequences

- New or changed modules: `stokes/geometry/formats.py`, `readers.py`, `iges.py`, `brepfile.py`,
  `meshfile.py`, `shapes.py` (provider and cache), `operations/` (one module per op type),
  `stokes/api/commands/operation.py`, `stokes/api/legacy.py` (aliases and the path rewrite);
  `persist.MIGRATIONS[1]`; node types `tag`, `boundary` and the op types; `part` gains `family`,
  `basis`, `opacity`, `derived`; `web/src/viewport/{selection,tags,idbuffer}.ts`, the pick model
  in the store, `StylePopover`, the operation card and the Prepare menu.
- Contracts to update: `docs/engineering/geometry-schema.md` (import, tags, style, operations),
  `web/DESIGN.md` (rows, islands, context toolbar, Prepare), `docs/engineering/workspace-schema.md`
  (action titles), `docs/00` to `03` and `06`, README, CHANGELOG.
- Budgets that must hold, as tests or probe runs before merging: `import stokes.api` (new
  command modules keep handlers lazy); orbit p95 with overlays and translucent buckets; the tree
  budgets with `boundary` and op nodes; `test_geometry_transfer` unchanged.
- Disk: the shape cache roughly doubles the geometry cache of a project; it is regenerable and
  lives under `cache/`.
- Risks for the owner: OCCT issue 1564 (eight `TKGeomBase` files carrying a proprietary header
  since 7.7, open since 2026-09-27) sits under every B-rep feature here; watch it or ask counsel.
  Real dirty CAD is untested: the repair defaults above were set on generated files, so the first
  weeks will tune tolerances. A face carrying two `boundary` tags makes two overlapping patches;
  the M3 compiler must refuse that, and `tag.list` should warn before then.
- ADR-0020's status becomes "accepted, amended by ADR-0021" for decisions 2, 3, 4, 5 and 7; its
  open question on `split` stands.

## Owner decisions (2026-10-04)

The owner adopted the recommended defaults for the open points above: proceed on OCCT with issue
1564 as an accepted risk, re-checked at every OCCT upgrade and before the first public release;
overlapping `boundary` tags warn while preparing and are refused by the case compiler; enclosure
margins of 3 extents on every side and 8 downstream with a warning above 1 % blockage; operations
re-run automatically by default; repair tolerances tuned on the M1 test set. The decisions log
records them as G9 to G13 in `docs/00-open-questions.md`.
