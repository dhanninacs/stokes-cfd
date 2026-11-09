# ADR-0022 · Analysis set-up: conditions on tags, materials on regions, a set-up check, and the selection, value-panel, glyph and list interactions

**Status:** accepted · 2026-10-05 · relies on ADR-0012 (quantities and expressions), ADR-0015 (the
workspace registry), ADR-0016 (studies vary any parameter), ADR-0018 (materials; this ADR fulfils its
decision 1, "regions reference materials by id"), ADR-0020 and ADR-0021 (tags, picking, operations;
**this ADR amends ADR-0021 decision 2.5** and builds on its decisions 3, 6 and 7). **Amendment 15
(edge and point selection) added 2026-10-05, design only.**

## Context

The owner asked on 2026-10-05 to look at how a leading design-simulation tool selects faces and
bodies, assigns boundary conditions and materials and completes the rest of the analysis set-up, and
to adopt those ways as **additions** to the present app: "keep what we have already and try to build
on top of it or addition to it as I like the present style as well." The public-documentation
research (`.mailbox/plan/research-discovery.md`) condensed that workflow into fifteen generic
patterns; the code digest (`.mailbox/plan/research-setup-code.md`) says what Stokes has.

What exists: a pick of bodies and faces in the store with Shift and Ctrl (T-019); tags as nodes with
`tag.*` commands (ADR-0021 decision 2); T-020 under way adds Name…, `tag.region`, tag overlays in the
tag's colour and hidden faces; `geometry.style` (T-021) follows. Boundaries are plain groups named
after a patch, created by three undo steps keyed on a patch name with the region `fluid` hard-coded;
there are no condition kinds, no values, no vector parameter, no conditional fields and no check.
Materials are built to ADR-0018 slices 1 and 2 but nothing assigns them: `material.list` returns
`regions: []` hard-coded and no node carries a `material` reference. `stokes.physics` is a docstring
stub. The front end has no test runner. The constraints that shape this ADR: no vendor names, icons or
visuals, and Stokes's own vocabulary ("tag" for named face and body sets); the approved look in
`web/DESIGN.md` (tokens only, the accent in at most three places, red reserved for inlets and
errors, blue for outlets); nothing restyled or restructured; the orbit p95 at 4,500 parts has about
1 ms of headroom, so nothing per face may enter a frame; the coding worker is a free, weaker model, so
risky cores go to Claude and the rest is cut small and testable.

## Decision

### 1. What is built now and what waits for M3

Built now (this ADR, tasks T-035 to T-052): the condition data model and catalogue, the `boundary`
and `region` node types, the atomic assign commands, material assignment to regions, `setup.check`
with readiness states, the selection additions, the value panel, condition glyphs, the conditions
list, the two flow helpers and the status readout. Everything is consumed unchanged by M3's compiler:
boundary → tag → patch (ADR-0021 2.5), region → tag → mesh region, region → material →
`physicalProperties` (ADR-0018 decision 8).

Waits for M3 and later: the physics models node (energy, turbulence, transient) and the
compatibility rules that need it (decision 2.7 keeps their slot); the compiler's boundary mapping
(`docs/05-openfoam-integration.md` §2, which gains the `pressure_inlet` row); initial conditions,
solver settings, monitors and runs; the materials-to-OpenFOAM mapping (ADR-0018 slice 3, T-010);
fluid–solid interface detection (M5); mesh fidelity (M2). Readiness states beyond `ready` arrive
with the run queue (decision 5.2).

### 2. The condition data model

2.1 **One node type, `boundary`**, carries the union of every kind's parameters, a `kind` enum
and a `tag` reference (ADR-0018's `property` pattern). Changing the kind therefore keeps the node
id, the name, the tag and every expression reference (`regions.fluid.boundaries.inlet.velocity`
stays addressable from fields and study variables, ADR-0016); one node type per kind would replace
the node and break them (the alternative ADR-0018 rejected for the same reason). The type is
registered with the geometry types (`stokes/geometry/model.py`), as ADR-0021 2.5 said, because
`stokes.model` may import that module and nothing else of physics.

2.2 **The catalogue** `stokes.physics.conditions` (`KINDS`, a tuple of `ConditionSpec(id, label,
family, params, fields, needs, color, glyph, arrives, note)`) is the declarative table the schema,
the checks, the panels and later the compiler read: `family` is `inlet | outlet | wall | constraint
| interface`; `params` are `ParamUse(name, rule, required, when)` with the rules of the materials
catalogue (`any`, `positive`, `nonnegative`, `nonzero`, plus `fraction` for 0 < x < 1) and `when`
a mapping of another parameter's name to the values under which this one is used; `fields` names
the solver fields the kind sets (`U`, `p`, `T`, `turbulence`); `needs` names the physics the
parameter group depends on (`energy`, `turbulence`); `color` is the tag colour the kind gives
(2.4, 8.3); `glyph` the viewport marker (8.1); `arrives` the milestone of a kind that cannot be
chosen yet. `boundary.kinds` returns the catalogue; **amended 2026-11-04**: with an `engine` (default: the open
project's) each kind carries `supported` and `unsupported_reason`, from the engine catalogue's own
`kinds` (taken from the case writer's list). An unsupported kind is listed and shown disabled, not
hidden, and `setup.check` reports one a boundary holds as `setup.kind_unsupported`. The module imports
nothing heavy; `import stokes.api` stays under its budget.

2.3 **The kinds** are the engineer-facing types of `docs/05` §2 plus a pressure inlet:

| id | Label | Family | Parameters used | needs | Tag colour | Glyph |
|---|---|---|---|---|---|---|
| `velocity_inlet` | Velocity inlet | inlet | `direction`, `velocity` (normal, vector), `velocity_x/y/z` (components), `direction_vector` (vector), `temperature`, `turbulence`, `intensity`, `length_scale`, `viscosity_ratio` | temperature: energy; turbulence group: turbulence | `#d64f4f` | arrow in |
| `mass_flow_inlet` | Mass-flow inlet | inlet | `mass_flow`, `temperature`, turbulence group | as above | `#d64f4f` | arrow in |
| `pressure_inlet` | Pressure inlet | inlet | `total_pressure`, `temperature`, turbulence group | as above | `#d64f4f` | arrow in |
| `pressure_outlet` | Pressure outlet | outlet | `pressure`, `backflow_temperature`, turbulence group (backflow) | as above | `#3b82c4` | arrow out |
| `wall` | Wall | wall | `shear`, `thermal`, `temperature` (thermal = temperature), `heat_flux` (heat_flux), `heat_transfer_coefficient` and `ambient_temperature` (convection) | thermal ≠ adiabatic: energy | `#7f8c99` | none (the tag tint suffices) |
| `symmetry` | Symmetry | constraint | — | — | `#a275c9` | plane |
| `cyclic` | Cyclic | constraint | `partner` (another boundary) | — | `#e0803a` | pair |
| `interface` | Fluid–solid interface | interface | `partner` | arrives M5 | `#5aa452` | diamond |

Fans, porous jumps, rotating zones, radiating walls and heat on bodies are not kinds in this ADR
(decision 12). Red is the inlet family and blue the outlet, as `web/DESIGN.md` reserves them; the
other colours come from `SELECTION_PALETTE`, so no new colour enters the viewport.

2.4 **The parameters** of `boundary` (SI stored; every physical value a quantity, rule 6):

| Parameter | Kind | Dimension | Default | Rule | Used by |
|---|---|---|---|---|---|
| `kind` | enum (the ids above) | | `wall` | | every kind |
| `tag` | ref `tag` | | | kind `boundary` | every kind |
| `direction` | enum `normal` \| `components` \| `vector` | | `normal` | | velocity inlet |
| `velocity` | quantity | velocity | 0 | positive | velocity inlet, `direction` normal or vector (the magnitude) |
| `velocity_x`, `velocity_y`, `velocity_z` | quantity | velocity | 0 | any; not all zero | velocity inlet, `direction` components |
| `direction_vector` | array(3) | dimensionless | `[1, 0, 0]` | nonzero length | velocity inlet, `direction` vector |
| `mass_flow` | quantity | mass_flow | 0 | positive | mass-flow inlet |
| `pressure` | quantity | pressure | 0 | any | pressure outlet (gauge, relative to the operating pressure of M3) |
| `total_pressure` | quantity | pressure | 0 | any | pressure inlet |
| `temperature` | quantity | temperature | 300 K | positive | inlets; wall with `thermal` temperature |
| `backflow_temperature` | quantity | temperature | 300 K | positive | pressure outlet |
| `turbulence` | enum `intensity_ratio` \| `intensity_length` | | `intensity_ratio` | | inlets and the outlet's backflow |
| `intensity` | number | | 0.05 | fraction | turbulence group |
| `viscosity_ratio` | number | | 10 | positive | `turbulence` intensity_ratio |
| `length_scale` | quantity | length | 0.01 m | positive | `turbulence` intensity_length |
| `shear` | enum `no_slip` \| `slip` | | `no_slip` | | wall |
| `thermal` | enum `adiabatic` \| `temperature` \| `heat_flux` \| `convection` | | `adiabatic` | | wall |
| `heat_flux` | quantity | heat_flux | 0 | any (into the fluid positive) | wall, `thermal` heat_flux |
| `heat_transfer_coefficient` | quantity | heat_transfer_coefficient | 10 | positive | wall, `thermal` convection |
| `ambient_temperature` | quantity | temperature | 300 K | positive | wall, `thermal` convection |
| `partner` | ref `boundary`, optional | | null | | cyclic, interface |

Unused parameters keep their defaults and are ignored, as on `property`. The dimensions all exist in
`stokes.units`; none is added.

2.5 **Vectors** get no new parameter kind. A velocity inlet takes a magnitude with `direction`
`normal` (the common case and what a quick study varies), three quantity components, or a magnitude
along a dimensionless `array(3)`. A `vector` kind (three quantities under one dimension) would touch
the schema, persistence, `parameter.set`, the expression resolver and every field renderer at once;
it is listed as an alternative to revisit if a third node type needs it.

2.6 **Conditional fields** live in the catalogue (`ParamUse.when`), not in `ParamSpec`: the panels
show a parameter only when its `when` holds (the Properties panel and the value panel share one
filter, decision 7.1), `boundary.set` refuses a value for a parameter the kind does not use
(`boundary.invalid_value`, as `material.set_method` refuses), and `setup.check` applies the rules
to the parameters in use. A generic `when` on `ParamSpec` stays an alternative.

2.7 **Compatibility with physics models**: `needs` is recorded now and judged later. Until
`/physics/models` exists (M3), `setup.check` lists a thermal wall or an inlet temperature under
`setup.needs_physics` as **info** ("uses the energy equation; set the physics models in M3"), never
as an error; M3 turns the rows into errors against the models node and the engine's capability
predicate, as ADR-0018's `materials.energy_required` does.

2.8 **References and removal.** `Project.referrers(node_id) -> [(node, parameter)]` scans the
`ref` parameters of the tree (built on call; the tree is small beside the meshes). `node.remove`
refuses a referenced node with `model.referenced`, naming the referrers; `tag.delete` refuses a tag
a boundary or region uses unless `force: true`, which removes those nodes in the same step;
`boundary.delete(boundary, delete_tag=false)` and `region.delete(region, delete_tag=false)` are the
cascading verbs. A `ref` accepts `null` to clear an optional reference (today a reference can be set
but never cleared); a required reference (`boundary.tag`, `region.tag`) is refused `null`. A
boundary whose tag is gone (a project edited by hand) is listed with `missing: true` and shown
`failed`, "tag missing".

2.9 **Regions and paths.** A new node type **`region`** at `/regions/<name>` carries `kind`
(`fluid` \| `solid`, default `fluid`), `tag` (ref `tag`, kind `region`: the bodies) and `material`
(ref `material`, optional). `region.create` always makes the region's tag with it (empty members
allowed), so `tag` is never null. Each region holds a `boundaries` group, created with it, and
boundaries live at `/regions/<region>/boundaries/<name>` (ADR-0021 2.5, now typed). The default
region is `fluid`. Boundaries and regions that are plain groups (made before this ADR) stay valid as
legacy entries; `boundary.assign` with a legacy group as its target replaces it by a typed node of the
same id and name in one step (remove and add inside the Composite).

2.10 **Status** is derived, not written: the tree and the conditions list show a boundary or region
`stale` when its tag is stale, `failed` when the tag is missing or the kind invalid, `warning` when
`setup.check` has a warning for it, computed by the front end from the tag link (T-033's proxy rule)
and the check result; `boundary.list` and `region.list` compute the same for the CLI and MCP. No
session service writes statuses, so nothing depends on the operations framework's `on_event` hook.
The contract for all of this is **`docs/engineering/setup-schema.md`**.

### 3. The commands

3.1 **`boundary.assign(target, kind, values?, name?, region?, color?, node_id?, tag_node_id?,
region_node_id?)`** is the one atomic, replayable assign. `target` is a tag (id or path), an existing
boundary (re-kinded and re-valued), a legacy boundary group (converted, 2.9) or `members` in the
tag commands' input form (the live pick). One `Composite`, "assign <kind> to <name>": the
`/geometry/tags` group when missing, the tag when `members` are given (kind `boundary`, `name` or
the kind's default label made unique, `inlet`, `inlet_2`), the region when missing (`region` or the
project's only fluid region, else `fluid`, 2.9), its `boundaries` group, the boundary node with
`kind` and `values` (validated against the catalogue), and the tag's `color` set to the kind's
colour unless `color` is given. Ids are stable for replay: the three `*_node_id` parameters are
recorded by `ctx.record`, as `tag.create` records `node_id`; members are recorded as given, as
`tag.create` does, because part ids are stable per import (`part_id_for`). Result `{boundary, tag,
region, created: [ids]}`; events `node.added` per node and `boundary.changed {node_id}`; errors
`boundary.invalid_kind`, `boundary.invalid_value`, `tag.invalid`, `validation`.

3.2 **`boundary.set(boundary, kind?, values?, tag?, members?, name?, keep_color?)`**: one undo
step for any mix of a kind change (recolours the tag to the new kind's colour unless `keep_color`), a
value change, a new tag (the "replace location" of the researched tool) or new `members` (a
`tag.set` on the boundary's tag inside the step). Values for a parameter the kind does not use are
refused. `parameter.set` still works on every parameter, as on materials.

3.3 **`boundary.delete(boundary, delete_tag=false)`**, **`boundary.list(region?)`** →
`[{id, path, name, region, kind, label, tag: {id, name, color, parts, faces, stale, missing},
primary: {name, value, display}, warnings: [codes]}]` plus legacy entries `{id, path, name, legacy:
true}`, and **`boundary.kinds(engine?)`** → the catalogue with each parameter's `ParamSpec` JSON
schema beside its `ParamUse`, so a client renders a kind's fields without a second query.

3.4 **Regions**: `region.create(name, kind, bodies?, material?, node_id?, tag_node_id?)`,
**`region.assign(material, target, name?, node_id?, tag_node_id?)`** (decision 4.1),
`region.set(region, material?, kind?, members?, name?)`, `region.delete(region, delete_tag=false)`,
`region.list()` → `[{id, path, name, kind, material: {id, name, kind} | null, bodies, boundaries,
uncovered_faces, stale}]`. `material.list` fills `regions` with the regions that reference each
material.

3.5 **Undo, journal, CLI.** Every command is one undo step and one journal record; every one is
tested through `Session.execute` and replayed into a fresh session (constitution §6). The CLI gains
`stokes boundary` (`assign`, `set`, `delete`, `list`, `kinds`), `stokes region` (`create`,
`assign`, `set`, `delete`, `list`) and `stokes setup` (`check`, `defaults`), JSON output as
`stokes tag`.

### 4. Material assignment

4.1 **Bodies reach a material through a region**: body → region-kind tag → `region` node →
`material`. One material per region. `region.assign(material, target)` with `target` a region,
a region tag, or bodies (part ids, a group, or the live pick as members with `faces: "all"`): bodies
already in a region whose material is the one given stay; a body in another region moves to the
region for this material (the region named after the material, created when none exists: `water`,
`aluminium`), and a region left empty stays and warns (`setup.empty_region`). This is the researched
tool's shared assignment ("one assignment covers all bodies using that material") expressed with
nodes Stokes already needs for multi-region meshing. One `Composite`.

4.2 **Kind check**: a region's `kind` follows the material's on creation; `region.set` and
`region.assign` refuse a fluid material on a solid region and the reverse
(`materials.kind_mismatch`, the code ADR-0018 reserved); `setup.check` reports it when a project
loaded from disk disagrees.

4.3 **No silent default material.** Nothing is assigned until the user or a helper does it;
`setup.check` says "no material" (error). `setup.defaults` (decision 10.1) creates `air` (external
flow) or `water` (internal) from `material.create`'s built-in templates (decisions log F6 (a)) when
the project has no fluid material, and says so in its result.

4.4 **The pick**: bodies picked → **Material ▾** on the context toolbar (also the Physics task strip
and the palette action `material.assign`) opens a menu: the project's materials (kind-filtered to
what the picked bodies' regions allow; a fluid and a solid section), the five most recent names
(a per-browser convenience in `localStorage`, wrapped in try/catch), and **Browse library…**, a
small list over `material.library_search` with a search field, provider chips and Add. Add runs
`material.add_from_library` and then `region.assign`: two undo steps, stated in the toast
("Added water · assigned to 3 bodies"); the common path (an existing material) is one. Drag-and-drop
of a material onto a body is not adopted now: it needs drag-and-drop outside Customise mode and a
public viewport hit test, and the select-then-pick path covers the case; revisit after T-032.

4.5 **Editing**: a region row shows its tag's swatch and eye by proxy (ADR-0021 4.3) and its
material's name as the value; double-click opens the Properties panel on the material (the
Materials panel of ADR-0018 slice 4 when it exists). Edits apply to the material node; "edits create
a new assignment" is not adopted: `material.duplicate` (ADR-0018, not yet built) is the explicit
copy.

### 5. The set-up check and readiness states

5.1 **`setup.check()`** is a pure query (`mutates: false`) over the project and the tag staleness
the session knows: `{state, next, counts: {regions, materials, boundaries, uncovered_faces,
legacy}, findings: [{code, severity, message, suggestion?, nodes: [ids], fix?: {command, params} |
{action}}]}`, errors first. No session service and no event hook: the front end calls it, debounced
300 ms, after `node.*`, `tag.changed` and `boundary.changed` events; the CLI and MCP call it
directly. Cost is O(tags + boundaries) with range arithmetic over `members`; a perf test records the
time on the 4,500-part tree (not a budget).

5.2 **States**, in precedence: `not_defined` (no region holds a body), `incomplete` (any error
finding), `stale` (a region or boundary tag is stale or missing), `ready` (no error; warnings
allowed). The run queue adds `meshed`, `running`, `done` and `done_unconverged` in M2 and M3
(docs/05 §6); "ready" then reads "ready to mesh". `next` is the first error's `suggestion`, else the
first warning's, else "Set-up complete".

5.3 **Findings**:

| Code | Severity | Condition | Fix |
|---|---|---|---|
| `setup.no_region` | error | no `region` holds a body | pick bodies → Material… |
| `setup.no_material` | error | a region has no material | Material… |
| `setup.material_kind` | error | region and material kinds disagree | `region.set` |
| `setup.no_inlet` | error | a fluid region has bodies and no inlet-family boundary | add an inlet |
| `setup.no_outlet` | error | … no outlet-family boundary | add an outlet |
| `setup.no_pressure` | warning | no boundary of a fluid region fixes pressure (no pressure inlet or outlet) | add a pressure outlet |
| `setup.value_invalid` | error | a parameter in use breaks its rule (velocity 0, mass flow negative, intensity 1.5, components all zero) | the value panel |
| `setup.tag_missing` | error | a boundary or region references a tag that is gone | Replace faces… |
| `setup.empty_tag` | warning | a boundary's tag has no faces, or a region's tag no bodies | Replace faces… / Delete |
| `setup.stale_tag` | warning | a referenced tag is stale (ADR-0021 8.4) | `tag.rematch` |
| `setup.overlap` | warning | a face lies in two boundary tags (`tag.overlap`; refused at compile, decisions log G10) | edit the tags |
| `setup.uncovered_faces` | info | faces of a region's bodies in no boundary tag (they become no-slip walls at compile) | pick them → Wall |
| `setup.partner_missing` | error | a cyclic or interface boundary without a valid partner, or partners not mutual | the value panel |
| `setup.needs_physics` | info | a parameter in use needs energy or turbulence (2.7) | M3 |
| `setup.legacy` | warning | a plain-group boundary or region from before this ADR | `boundary.assign` on it |
| `setup.empty_region` | warning | a region with a material but no bodies | `region.set` members / Delete |

5.4 The check never writes to the tree (2.10). Codes are stable strings as every `StokesError`
code is; messages are sentences; `fix` carries an executable command when one exists, so an agent
can repair the set-up from the result.

### 6. Selection additions

Every addition keeps the pick model of ADR-0021 3.1 (`pickMode`, `picked`, `selectedPatch` as the
active member), the Modes island and the gestures that exist; nothing is re-bound.

6.1 **Hover cycling.** The CPU raycaster already returns every hit under the pointer, sorted;
`Alt` + wheel cycles through them (distinct bodies in body mode, distinct faces in face mode), the
hover highlight follows the current candidate, the status strip (11.2) shows "2 of 3 under the
pointer", a click picks the current candidate, and moving the pointer by more than 4 px resets to
the nearest. The wheel alone keeps zooming. The id buffer (T-032) gives one hit, so cycling stays on
the raycaster even after T-032 moves hover over.

6.2 **Click escalation.** Face mode: a click picks the face; a double-click grows it to the tangent
chain (`tag.region` at the default angle, ADR-0021 3.1 as T-020 builds it); a triple-click picks
every face of the body (`pickAllFaces(part, how)` in the store, drawn as the body's tint overlay).
Body mode: a click picks the body; a double-click picks every part of the same import group
(components); a triple-click does nothing more. Shift and Ctrl apply to the escalated set as they do
to a click.

6.3 **Type filter.** The Body | Face control stays the always-visible filter with an explicit
default; a "smart" mode that infers the type from the cursor is not adopted (in set-up the
distinction is deliberate). Switching converts the pick instead of dropping it: faces → the bodies
that own them; bodies → all their faces. Act-then-select (6.6) sets the mode its tool needs and
restores the previous one on commit or cancel.

6.4 **Find related.** `tag.related(members, relation, parts?, tolerance?)` returns members:
`same_area` (areas within 2 %), `coplanar` (mean normals within 2° and plane offsets within 0.1 % of
the part diagonal), `parallel`, `coaxial` (facets perpendicular to one axis fitted from the picked
faces' facet normals, radius within 2 %), `tangent` (`tag.region`) and `same_color`
(`tag.by_color`), over the shown parts by default. It uses the fingerprints and facet data the
geometry store holds; cylinder detection from a tessellation is approximate and the B-rep surface
type refines it once the shape cache exists (ADR-0021 8.3). The UI is **Related ▾** on the context
toolbar: hovering an entry previews the matches as the hover overlay, a click adds them to the pick,
Shift-click replaces.

6.5 **Box, lasso, paint.** Box and lasso stay as ADR-0021 3.4 and T-032 define them (id buffer,
drag on empty space, Alt-drag). **Paint** is a Modes-island toggle (brush icon, chord `P`): while
on, a left-drag over faces (face mode) or bodies (body mode) adds everything the hover pick crosses,
Ctrl-drag removes, the orbit is suspended, Esc or the toggle leaves. Paint rides the hover pick path
(the raycaster today, the id buffer when T-032 switches it), so it needs no id buffer and lands first;
it introduces the store's `pickMany`, which T-032 then uses. Boundary flood-fill (a perimeter, then a
seed) is deferred: `tag.region` with a stop set can do it on T-005's adjacency later.

6.6 **Select-then-act and act-then-select**, both. Select-then-act: pick faces → **Condition ▾**
(context toolbar, lead button in face mode) → a kind → the value panel (7) → Enter runs
`boundary.assign(members, kind, values)`; pick bodies → **Material ▾** (4.4). Act-then-select: the
Physics task strip, the palette actions `condition.add` (a kind) and `material.assign`, and the
fixes of the conditions list open the value panel in a waiting state ("Pick the faces"), force the
mode the tool needs, fill the target line as the pick changes, and commit on Enter. The picker is
filtered to valid targets: toolbar entries are disabled with a `why` when the pick does not fit
(faces for conditions, bodies for materials), as T-029's Prepare entries are.

6.7 **Revert.** The store keeps the last ten picks; the action `selection.previous` (palette, no
default chord) restores one.

### 7. The value panel

7.1 **`ConditionPanel`** is a floating glass card of the properties panel's kind (12 px radius,
360 px wide), anchored under the context toolbar and clamped as it is; without an anchor (act-then-
select in a preset without a pick) it docks at the right of the viewport where T-029's operation
card sits. Left column: the kinds as a vertical list grouped by family (Inlet: Velocity, Mass flow,
Pressure · Outlet: Pressure · Wall · Symmetry · Cyclic · Interface, the last disabled with its
`arrives`), each with a 12 px dot in the kind's colour. Right column: the name (defaulting from the
kind, unique), the target line ("12 faces of 3 bodies · Replace"), and the parameters of the kind
filtered by `when`, rendered by **`SchemaFields`**, the Properties panel's field loop extracted into
one component (QuantityField with the unit chip and expressions, enum as `select.field`, bool as the
Switch, number, `ref` as a select over the nodes of the referenced type, `array(3)` as three
fields). Enter commits (`boundary.assign` for a pick, `boundary.set` for an existing boundary), Esc
cancels, a refused value stays with the server's message as the rename input does. One panel at a
time; the accent appears once in it (the primary button).

7.2 **Later edits**: double-click a boundary row (tree or conditions list) opens the panel for it
with its faces highlighted; the Properties panel shows the same filtered fields for a selected
boundary, so the Minimal preset has both routes; **Replace faces** enters act-then-select and calls
`boundary.set(members)`; **Replace tag** offers the project's boundary-kind tags.

7.3 Nothing is restyled: the card, fields, tokens and radii are the existing ones; T-029's operation
card uses the same `SchemaFields`, so one component renders operations, conditions and properties.

### 8. Glyphs

8.1 **One glyph per boundary, never per face.** `ConditionGlyphs` (`web/src/viewport/glyphs.ts`)
draws one `THREE.InstancedMesh` per glyph shape (arrow in, arrow out, plane, pair, diamond) on its
own layer `GLYPH_LAYER`, outside `PART_LAYER`: excluded from the outline occluder pass, the id pass,
the contact shadow and the batches. Placement comes from **`tag.anchor(tags)`** → per tag
`{centroid, normal, area, extent}` (area-weighted over the member faces from the fingerprints the
store already computes). The instance sits at the centroid, points along the normal (inward for
inlets, outward for outlets) and keeps a constant 28 px on screen: the per-frame work is one scale
update over tens of instances, never over faces. Colour is the tag's colour (the kind colour by
default, a user's override follows). Rebuilt, coalesced to one per frame, on `boundary.changed`,
`tag.changed`, a part's mesh arrival (the anchor is fetched again), the tag's `visible` and the
part's. Gate: with 50 glyphs the probe's orbit p95 rises by at most 0.3 ms.

8.2 **Interaction**: hover lights the boundary's faces (the tag link) and names it in the status
strip; click selects the boundary node; double-click opens the value panel. The tag's eye hides its
glyph; a store toggle and palette action `view.glyphs` hide them all (not persisted; a workspace key
can follow).

8.3 **Tag colour by kind** (decision 3.1): assigning gives the tag the kind's colour and a kind
change follows it unless `keep_color`, so assigned faces read red-inlet / blue-outlet persistently
("tint assigned boundaries", `docs/03` §7) while unassigned faces keep the part's colour and the
check lists them.

### 9. The conditions list

9.1 **Panel `conditions`** ("Conditions", icon `ListChecks`), registered like the Explore panels
(ADR-0015), default a tab beside Properties, mirrored by hand in `web/src/workspace/defaults.ts`,
`stokes/workspace/defaults.py`, the five presets and `workspace-schema.md`. Rows: each region (kind
dot, name, material or "no material", body count, proxy swatch and eye) with its boundaries beneath
(swatch, name, kind label, primary value in display units, derived status dot); then **Unassigned
faces** ("12 faces of 3 bodies": hover highlights them, click picks them); then **Findings** from
`setup.check` (severity dot, message, a Fix button where `fix` exists: a command runs, an action
opens act-then-select). Hover highlights faces in 3D through the tag link; click selects the node;
double-click opens the value panel; Delete removes with the undo toast.

9.2 One tree stays. The researched tool keeps separate geometry and physics trees; the owner likes
the present tree, so the list is a flat, set-up-stage view beside it and the tree keeps `/regions`
with typed rows (T-047).

### 10. The internal and external flow helpers

10.1 **`setup.defaults(target, template, inlet?, outlet?, material?, velocity?)`** turns a
prepared body into a set-up in one undo step. `template: external` (after an enclosure, ADR-0021 6.2):
tags named `inlet` → `velocity_inlet` (`velocity` given or left for the check), `outlet` →
`pressure_outlet`, `symmetry` → `symmetry`, `far_field` → `wall` slip, `ground` → `wall` no-slip,
`walls` → `wall` no-slip. `template: internal` (after an extraction, ADR-0021 6.1): `inlet` and
`outlet` members (picked cap faces) become tags and a velocity inlet and a pressure outlet; a `caps`
face in neither becomes a wall with a warning in the result; every face of the body in no boundary
tag goes to a `walls` tag → `wall` no-slip. Both create region `fluid` with the body when none
holds it and assign `material`, or the `air` (external) / `water` (internal) template when no fluid
material exists (4.3); boundaries that already use a tag are left alone. The journal replays it
after the operation it follows, which relies on journal replay waiting for an operation to finish
before the next record (T-024).

10.2 **UI.** Prepare ▸ **External flow…** is T-029's enclosure card with a velocity field and an
"Add default conditions" switch (on); on `geometry.op_done` the front end calls `setup.defaults`
and opens the conditions list. Prepare ▸ **Internal flow…** is the extract-fluid card with its seed
pick, then an **Openings** step: the output's `caps` faces are highlighted, the user picks the inlet
cap or caps → Next → the outlet caps → Finish calls `setup.defaults(internal, inlet, outlet)`. Both
reuse `OperationCard`, `SchemaFields` and the act-then-select mechanism; there is no wizard
framework.

### 11. The status readout

11.1 In the output drawer's collapsed strip, right end (the window's thin status line, `web/DESIGN.md`
§3b): a 7 px dot in the state's colour (muted for `not_defined`, red `incomplete`, amber `stale`,
green `ready`) and muted text "Set-up · needs an outlet · 12 faces unassigned" (the `next` text and
the first count), text and not a badge. Click opens the conditions list, or shows the findings in a
popover when that panel is hidden. The readout refreshes with the debounced `setup.check` (5.1).
When the drawer is hidden (Minimal preset) the palette's `setup.check` prints the same.

11.2 The same strip's left part carries the transient pointer text: the hovered face or body and
"2 of 3 under the pointer" while cycling (6.1). It replaces nothing in the strip; the tabs stay.

### 12. What is deliberately not adopted

| Pattern or feature | Why not |
|---|---|
| Vendor names, icons, text and visuals | the owner's rule; Stokes's words: tag, condition, region, material |
| A "smart" type filter that infers the type under the cursor | in set-up the body/face choice is deliberate; the explicit Modes control stays (6.3) |
| Wheel or arrow cycling without a modifier | the wheel zooms and arrows move the tree; `Alt` + wheel (6.1) |
| A radial menu, a stage navigator with Model/Explore/Refine, live re-solve on edit | the six stage chips and Explore (ADR-0016) already structure this; no second navigation |
| Separate geometry and physics trees | one tree, by the owner's taste; the conditions list is a view (9.2) |
| Drag-and-drop of materials onto bodies | needs drag-and-drop outside Customise and a public hit test; select-then-pick covers it (4.4); revisit after T-032 |
| Material edits that create a new assignment | one material node per material (ADR-0018); `material.duplicate` is the explicit copy |
| Silent default materials | nothing is assumed; the check says so and the helpers create a cited built-in material (4.3) |
| Fans, porous jumps, rotating zones, radiating walls, heat on bodies | not in F1's v1 physics as conditions; porous and MRF arrive as region models in M3, radiation in v1.5 |
| Fidelity slider, monitors, result tools, auto-captured scenes, parameters-to-variations toggle, tours and overlay help | M2 (mesh), M3 (monitors), M4 (results), ADR-0016 (any quantity is already a study variable; a "vary" toggle beside a field is M3.5 polish), M9 (guidance); the palette already runs tools |
| Rule-built sets (a query editor) and folders for tags | `tag.by_color` and `tag.by_name` exist; groups are the tree's folders; a rules editor waits for the B-rep surface types |
| Boundary flood-fill | deferred (6.5) |
| A server-side set-up service writing statuses | a pure query is replayable, cheap and needs no hook (5.1, 2.10) |

### 13. The fifteen patterns

| # | Pattern | Verdict | Where |
|---|---|---|---|
| 1 | Hover pre-highlight with cycling; escalating clicks | adopt | 6.1, 6.2 |
| 2 | Visible type filter; direction-coded box, lasso, paint, flood-fill; revert | adopt in part: filter kept explicit, box and lasso per ADR-0021, paint and revert added; flood-fill deferred | 6.3, 6.5, 6.7 |
| 3 | "Find related" with preview | adopt | 6.4 |
| 4 | Named sets in one keystroke, foldered, rule-built; conditions link to sets | adopt: tags are the sets (T-020's Name…), conditions reference tags; folders and rules deferred | 2.1, 12 |
| 5 | Object-first and tool-first; picker filtered to valid targets | adopt | 6.6 |
| 6 | In-viewport value panel, Enter commits, Esc cancels; later edits from the tree or by replacing the location | adopt | 7 |
| 7 | Auto-advancing wizards building the fluid volume and default conditions | adopt as two helpers on ADR-0021's operations | 10 |
| 8 | Persistent status cluster with explicit readiness states and messages | adopt | 5, 11 |
| 9 | Separate geometry and physics trees; hover highlights; double-click edits | adopt the behaviours, not the second tree | 9.2, 7.2 |
| 10 | Materials: pick bodies, in-model and recent list with search, favourites, defaults; shared assignment; edits copy | adopt the pick, list and shared assignment; favourites later; defaults and edits-copy rejected | 4 |
| 11 | Fast and accurate stages sharing one set-up; fidelity slider | defer to M2 and M3 | 1 |
| 12 | Monitors auto-created per physics | defer to M3 | 1 |
| 13 | Result tools, hover probing, scenes, comparison | defer to M4 | 1 |
| 14 | Parameter toggle feeding variations | exists in substance (ADR-0016); the toggle is M3.5 polish | 12 |
| 15 | Layered guidance: overlay help, tours, next-step text, a search that runs tools | adopt the next-step text (11.1); the palette runs tools; tours and overlay help are M9 | 11 |

### 14. Ownership and sequencing

Claude builds the cores that are easy to get subtly wrong: the catalogue and node types (T-035), the
assign commands with the reference guard (T-036), the value panel with both pick flows (T-044) and
the glyph layer (T-046). opencode builds, from briefs that each stand alone and name a test:
`setup.check`, `tag.anchor` and `tag.related`, `setup.defaults`, the front-end test runner, the
`SchemaFields` extraction, the selection additions, paint, the material pick, the tree rows, the
conditions list, the status readout, the helper cards, the Related menu and the docs sweep. Hot files
are sequenced in `docs/plans/analysis-setup.md`: `store.ts` and `scene.ts` after T-020 and before
T-032; `ContextToolbar.tsx` T-020 → T-044 → T-045 → T-051 → T-029 (Prepare) → T-050; `TreeRow.tsx`
T-022 → T-047; `Ribbon.tsx` T-029's Geometry case and T-045's Physics case in sequence.

## Alternatives considered

| Alternative | Why not |
|---|---|
| One node type per condition kind | a kind change would replace the node and break ids, expression references and study variables (ADR-0018's reasoning) |
| A `vector` parameter kind | touches schema, persistence, `parameter.set`, the resolver and every renderer at once; the enum-plus-quantities form covers inlets and keeps magnitudes addressable |
| `when` on `ParamSpec` | the catalogue already drives the forms for materials; a second mechanism would split the rule; kept as a later option |
| A set-up service on `SessionExtension.on_event` writing statuses | couples set-up to the operations framework's hook, adds state to keep in step, and a pure query gives the same answer to UI, CLI and MCP |
| Conditions keyed by patch name (as today) | a rename or a re-import breaks them; tags carry the fingerprints that re-match |
| Default region `fluid` hard-coded in the front end | the server owns the default and the only-fluid-region rule, so agents and the CLI behave the same |
| Glyphs per face or a glyph mesh per tag rebuilt from faces | per-face work in the frame is forbidden by the orbit budget; one instance per boundary is tens of instances |
| The readout in the top bar | the top bar's measured compaction would need a new step and the bar is full; the drawer strip is the thin status line the design praises |
| A second tree for physics | the owner keeps the present tree; a flat list serves the stage |
| Drag-and-drop materials now | no drag-and-drop outside Customise mode and no public hit test; cost without a gap in capability |
| Clearing a reference by deleting and re-adding the node | loses the id; `null` for an optional ref is the honest model change |

## Consequences

- New or changed modules: `stokes/physics/conditions/` (`catalogue.py`, `checks.py`); the `boundary`
  and `region` specs in `stokes/geometry/model.py`; `stokes/api/commands/boundary.py`, `region.py`,
  `setup.py`; `Project.referrers` and the `null` ref rule in `stokes/model`; `tag.anchor` and
  `tag.related` in `selection.py`; CLI verbs; `web/src/panels/ConditionPanel.tsx`,
  `ConditionsPanel.tsx`, `SchemaFields.tsx`, `MaterialMenu.tsx`, `web/src/viewport/glyphs.ts`,
  `web/src/state/pick.ts`; Vitest in `web/`.
- Contracts: **`docs/engineering/setup-schema.md`** (new), `geometry-schema.md` (tag queries,
  `tag.delete` cascade), `materials-schema.md` (`regions` filled), `workspace-schema.md` (panel
  `conditions`, actions), `web/DESIGN.md` (value panel, glyphs, conditions list, readout, paint,
  cycling), `docs/03-ui-design.md` §4 (regions and boundaries rows), `docs/05` §2 (`pressure_inlet`),
  `docs/06-roadmap.md` (M3's "regions and boundaries with tinting, BC library" moves up to this ADR).
- Budgets as tests or probe runs before merging: `import stokes.api` under 300 ms with the new
  command modules; orbit p95 with 50 glyphs within +0.3 ms; `setup.check` timed on the 4,500-part
  tree; the tree budgets with `region` and `boundary` rows.
- ADR-0021 decision 2.5 is amended (a typed boundary with kind and values, created by
  `boundary.assign`); T-033 is superseded by T-035, T-036 and T-047; T-022, T-029 and T-032 are
  amended as `docs/plans/analysis-setup.md` records.
- Risks: the free coding model on UI tasks with hot files, mitigated by sequencing and the Vitest
  helpers; `coaxial` from a tessellation is approximate until the shape cache; the kind colours
  change tags the user may have coloured (`keep_color` exists, and the behaviour is in the toast).

## Owner decisions (2026-10-05, assumed until confirmed)

The ADR proceeds on these assumptions, listed as S1 in `docs/00-open-questions.md`: `pressure_inlet`
joins the kinds and `docs/05` §2; the kind colours of 2.3; `air` and `water` from the built-in
templates as the helpers' materials; `P` for paint and `Alt` + wheel for cycling; the readout in the
drawer strip; Vitest (MIT) as the front end's test runner; condition statuses derived, not stored.

## Amendment · 15. Edge and point selection (T-054, design only)

T-053 exposes Body, Face, Edge and Point in the existing pull-up list, with Edge and Point disabled.
This section specifies how to enable them without changing the look, the face transport or the
meaning of existing tags. Implementation is sequenced in T-055 to T-060. Here **point means a B-rep
vertex**, not an arbitrary point on a triangle or a mesh node. A mesh-only part has no OCCT edge or
vertex topology: leave its Edge/Point targets unavailable, with an explanation, rather than invent
identities from tessellation. The sample mesh follows the same rule.

15.1 **Identity and history.** An edge or vertex is `(part node id, topology kind, zero-based id,
basis)`. In the worker, enumerate each part's shape with `TopExp.MapShapes_s(shape, TopAbs_EDGE /
TopAbs_VERTEX, TopTools_IndexedMapOfShape)` in that order; the first unique occurrence wins. Do not
use a triangle index, display-polyline index or `hash(shape)`. In particular, faces currently use
`TopExp_Explorer`, which counts repeats: do not change face numbering to `MapShapes` as part of this
work. Coarse and fine meshes of one B-rep share the map and basis; neither tessellation nor an
ordinary view change renumbers its topology. A changed source or a derived operation may renumber
it, even when the shape looks the same. Explicit edge/vertex members carry the existing part
`basis` and go stale on a mismatch; whole-body face members keep their existing meaning.

For re-matching, archive versioned per-edge `(length, curve centre of mass, endpoints, tangent at
mid-parameter, curve type when available)` and per-vertex `(position, incident edge lengths and
degree)` in SI alongside the face fingerprints. Match within the same part by normalised distance
and local incidence, one to one; use the face matcher’s confidence discipline (ambiguous or missing
matches stay unmatched with a report, never silently chosen). Do not mistake an edge's polyline
length for its B-rep length. The operation worker must report `Modified`, `Generated` and `IsDeleted`
for edges and vertices through the same OCCT history as faces (ADR-0021 8.6), including one-to-many
splits. Map all valid successors, report removals and ambiguities, then use fingerprints only when
history is unavailable. No in-place rewrite of a stale tag on import; `tag.rematch` is explicit.

15.2 **Lazy transport.** Keep mesh transport v2, `.mesh` caches and `/api/geometry/batch` unchanged.
Extra mesh frames or a version-3 mesh record would put topology on the ordinary path, either eagerly
for every part or by complicating a format built for bulk mesh load; both raise the 4,500-part cost.
A separate endpoint costs nothing until Edge or Point mode asks for one part and leaves the existing
decoders and caches untouched. Add a version-1, size-limited `/api/geometry/topology/<part id>`
binary response (or an equivalent authenticated geometry route) with a bounded header
`{version, part, basis, units: "m", edges, vertices, positions, indices}` followed by little-endian
float32 vertex positions and per-edge polyline offsets and float32 points. A closed edge may repeat
its first point at the end; a
degenerate edge with no useful curve is reported but not pickable. Do not infer OCCT vertex IDs
from tessellated polyline endpoints. The server validates the id and project containment and reads
an atomic, regenerable `<part id>.topology` cache keyed by `basis`, topology format version and
sampling policy. Generate a missing cache in the geometry worker from the shape provider's B-rep,
never on the request/UI thread; errors for missing B-rep or mesh-only parts are explicit. The old
mesh cache remains readable and needs no migration. Fetch only visible B-rep parts when entering
Edge/Point mode or when a selected part becomes visible; prioritise the hovered/selected part and
cap in-flight requests and retained overlay data. On leaving the mode, cancel pending requests and
release unneeded overlays; no eager fetch across 4,500 parts. Cache headers and network payloads
must be bounded and checked before allocation. Changes to this independent format bump its version;
unknown versions are refused and regenerated, not passed to a v2 mesh decoder.

15.3 **Pick and draw.** On entry to Edge/Point mode, use the existing part raycaster to narrow
candidate parts around the pointer and test only their loaded screen-projected polylines/vertices
in CPU screen space; a small spatial index per visible part can limit segments tested. Include
silhouette and free edges using a projected-bounds candidate path: a ray hit on a face is *not*
required, since a vertex or edge may lie outside a shaded face's pixels. Select the nearest
visible candidate within a device-pixel-aware tolerance, breaking ties by depth then stable id;
occluded candidates are exposed by the existing Alt + wheel cycle, not picked through unrelated
visible bodies by default. Establish and test the tolerance on both normal and HiDPI canvases;
measure hover latency on the 4,500-part case before accepting defaults. Hidden/tag-hidden faces
must not make a hidden edge or point pickable; translucent parts remain pickable. No topology scan
or extra draw in body/face mode. T-032's face/body RGBA8 id buffer stays intact: its 16-bit face
field cannot encode edge/vertex topology. After measuring, an optional separate on-demand GPU
pass with kind plus a wide-enough part/topology id may replace the CPU narrow phase; it must not
consume the body/face id namespace or add an orbit pass. Keep hover cycling on CPU. Orbit p95 has
about 1 ms of headroom: probe before/after with unloaded and loaded topology, and reject or revise
an implementation that exceeds it. No invented timing claim substitutes for that measurement.

Hover draws one accent line (edge) or marker (point) above the surface; the persistent picked set
uses a batched line/point overlay rebuilt on pick or camera/mesh changes, not one draw call per
edge, vertex or part and not new attributes in `BatchedMesh`. Apply the current accent and tag
colours and reuse existing highlight depth and visibility rules. Scale the point marker and line
pick width in screen space; any camera-dependent updates must be bounded to visible overlays.
The tag tint on faces and the outline on bodies stay as they are.

15.4 **Pick and tag contract.** Extend transient `pickMode` to `edge | point` and `picked` with
`edges` and `vertices` keyed by part, retaining `bodies`, `faces` and `selectedPatch` as the active
part's existing patch. Switching to a coarser mode converts: point → incident edges (when choosing
Edge) or adjacent faces (Face), edge → adjacent faces (Face), and any mode → owning body (Body).
Switching to a finer mode expands body → all its faces/edges/vertices and face → bounding edges or
vertices; edge → endpoints (Point). Union across multiple picked parts, deduplicate, preserve the
active owner, and omit topology not yet loaded with a visible pending state rather than clearing the
pick silently. Undoable model state is untouched by transient mode changes. Click replaces,
Shift-click adds, Ctrl-click toggles, Esc clears, as today; double-click edge selects its connected
wire/loop (prefer a closed wire, otherwise the connected chain, with branching reported rather than
guessed), double-click point selects incident edges; triple-click escalates to all edges/vertices
of that body, following the existing body/face escalation. Alt + wheel cycles same-kind candidates.
The context toolbar keeps the current placement and style: Edge offers Name…, Loop, Close openings…
and Split… only when the target operation exists; Point offers Name…, Seed for fluid…, and Probe…
only when implemented. Disabled actions explain the missing operation; no fake action or direct
model mutation from the UI. Any persistent action must be a tested API command with a journalled
result before the widget calls it.

The existing canonical `members` list and `{part, faces: "all" | ranges, basis?}` stay valid byte
for byte. Extend each member optionally with `edges` and `vertices` as sorted, merged inclusive id
ranges. A member may hold any combination of explicit faces, edges and vertices; absent fields mean
none, `faces: "all"` still means the whole body, and new edge/point-only members omit `faces`.
Explicit ids require a matching part basis; validate counts and bounds on write and read. Commands
retain their existing behaviour on face-only tags and old journals; `tag.members` adds typed counts
and stale/unmatched detail without changing existing fields. `plain` tags may hold any topology;
`boundary` tags may hold faces and edges (edges guide cap creation but never become a boundary
patch); `region` tags remain whole bodies only, never edges or vertices. A point seed is a plain
tag (one vertex) or an operation's three SI length quantities; it is not a boundary or region.
`tag.overlap`, `setup.check` and `geometry.export_stl` consider **faces only**, and a boundary tag
with edges but no faces is incomplete, not an STL patch. Store explicit cap loops as edge members
of a boundary or plain tag; the cap operation consumes the loop and creates new face members via
history. Split uses tagged edges as a B-rep tool when that operation is implemented. A vertex seed
can suggest an editable `locationInMesh` for snappyHexMesh; validate that it is strictly inside the
intended fluid region before compilation, not just on a wall vertex. Probes consume point tags only
when probe commands arrive (M4). Never reinterpret a vertex on a surface as an interior seed.

15.5 **Sequencing and deferrals.** T-055 supplies the separate topology contract/cache and
fingerprints; T-056 extends tag members and rematching; T-057 extends the transient pick and CPU
hit tests; T-058 draws hover and selected overlays; T-059 adds tested cap/seed consumers where the
operations exist, otherwise documents the disabled actions; T-060 finally enables Edge and Point
in T-053's list. T-032 need not land before the CPU path, but must not overwrite it. M2/M3 own
snappyHexMesh `locationInMesh` validation and compilation and any mesh-only topology; M4 owns
probes; split waits for ADR-0021's deferred split operation. A GPU edge/point pass, bulk edge box
and lasso, and arbitrary points on faces wait for profiling and a separate design. This amends
ADR-0022 6.2–6.3 for the new modes; the existing Body/Face gestures and assignments stand.
