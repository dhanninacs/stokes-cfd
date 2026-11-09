# Analysis set-up: regions, boundaries and the condition catalogue

**Format 1** · ADR-0022 · node types: `stokes/geometry/model.py` (`region`, `boundary`, beside
`tag`) · catalogue: `stokes/physics/conditions/catalogue.py` · the two flow templates:
`stokes/physics/conditions/templates.py` · errors:
`stokes/physics/conditions/__init__.py` · commands: `stokes/api/commands/boundary.py`,
`region.py` (their shared planning: `setup_plan.py`), `setup.py` (T-037, T-039) · CLI:
`stokes/cli/setup.py`

A **boundary** gives a condition kind and its values to the faces of a boundary-kind tag. A
**region** gives the bodies of a region-kind tag a kind (fluid or solid) and a material. Both are
nodes, so they are undoable, journaled, addressable from expressions and studies, and saved with
the project. M3's compiler consumes them unchanged: boundary → tag → patch (ADR-0021 decision 2.5),
region → tag → mesh region, region → material → `physicalProperties` (ADR-0018 decision 8). This
document is the contract for the Python core, the set-up check, the value panel, the conditions
list, the glyphs and the compiler.

**Status.** T-035 built the node types, the catalogue, its errors and `boundary.kinds`; T-036 the
`boundary.*` and `region.*` commands, the reference guard, `tag.delete`'s cascade and
`material.list`'s regions, with the CLI; T-037 the Checks section below, `setup.check` and
`stokes setup check`; T-039 `setup.defaults`, its two tables
(`stokes/physics/conditions/templates.py`) and `stokes setup defaults`.

All values in parameters, events and results are **SI** (`QuantityValue` for quantities; SI
numbers in arrays). Temperature is kelvin everywhere inside the model.

## Nodes

```
/regions                        group, part of the skeleton (cannot be removed or renamed)
  <region>                      region
    boundaries                  group, created with the region
      <boundary>                boundary
/geometry/tags/<tag>            tag: kind boundary (a boundary's faces) or region (its bodies)
/physics/materials/<material>   material (docs/engineering/materials-schema.md)
```

`stokes/geometry/model.py` names the paths and the vocabulary: `REGIONS_PATH` (`/regions`),
`BOUNDARIES_GROUP` (`boundaries`), `REGION_TYPE`, `BOUNDARY_TYPE`, `REGION_KINDS` (`fluid`,
`solid`), `DEFAULT_REGION` (`fluid`), `CONDITION_KINDS` (the eight kind ids), `DEFAULT_CONDITION`
(`wall`), and the choices of the boundary's other enums: `DIRECTIONS`, `TURBULENCE_INPUTS`,
`SHEARS`, `THERMALS`. The schema does not pin the paths: a `region` accepts `group` children
and a `boundary` can be added under any group with `node.add` (with its `tag`, which is
required). The commands create regions and boundaries only at these paths, and `boundary.list`,
`region.list` and the set-up check read only these paths.

Both types register through `register_geometry_types`, so `register_builtin_types` gives every
project and every fresh registry `tag`, `region` and `boundary`; `node.types` lists them.

### `region`

| Parameter | Kind | Default | Meaning |
|---|---|---|---|
| `kind` | enum `fluid` \| `solid` | `fluid` | the mesh region's kind; its material must be of the same kind (`materials.kind_mismatch`) |
| `tag` | ref `tag`, required | — | the region-kind tag that holds the bodies; `region.create` makes it with the region (empty members allowed), so it is never null |
| `material` | ref `material`, optional | null | the material of the bodies; null until one is assigned (`setup.no_material`) |

One material per region; bodies reach a material only through a region (ADR-0022 decision 4.1).

### `boundary`

One type carries a `kind` from the catalogue and the union of every kind's parameters (ADR-0022
decision 2.1, the `property` pattern of ADR-0018). The catalogue says which parameters a kind uses;
unused ones keep their values and are ignored. Changing the kind therefore keeps the node id, the
name, the tag and every expression reference: `regions.fluid.boundaries.inlet.velocity` stays
addressable from fields and study variables. The JSON schema of `boundary` lists every parameter
below, in this order; it does not say which kind uses which.

| Parameter | Kind | Dimension | Default | Rule | Used by |
|---|---|---|---|---|---|
| `kind` | enum: the eight ids below | | `wall` | | every kind |
| `tag` | ref `tag`, required | | — | a boundary-kind tag | every kind |
| `direction` | enum `normal` \| `components` \| `vector` | | `normal` | | velocity inlet |
| `velocity` | quantity | velocity | 0 | positive · required | velocity inlet, `direction` normal or vector (the magnitude) |
| `velocity_x`, `velocity_y`, `velocity_z` | quantity | velocity | 0 | any, not all three zero · required | velocity inlet, `direction` components |
| `direction_vector` | array(3) | dimensionless | `[1, 0, 0]` | nonzero length | velocity inlet, `direction` vector |
| `mass_flow` | quantity | mass_flow | 0 | positive · required | mass-flow inlet |
| `pressure` | quantity | pressure | 0 | any | pressure outlet (gauge, relative to the operating pressure of M3) |
| `total_pressure` | quantity | pressure | 0 | any | pressure inlet (gauge) |
| `temperature` | quantity | temperature | 300 K | positive | the inlets; a wall with `thermal` temperature |
| `backflow_temperature` | quantity | temperature | 300 K | positive | pressure outlet |
| `turbulence` | enum `intensity_ratio` \| `intensity_length` | | `intensity_ratio` | | the inlets and the outlet's backflow |
| `intensity` | number | | 0.05 | fraction | the turbulence group |
| `viscosity_ratio` | number | | 10 | positive | `turbulence` intensity_ratio |
| `length_scale` | quantity | length | 0.01 m | positive | `turbulence` intensity_length |
| `shear` | enum `no_slip` \| `slip` | | `no_slip` | | wall |
| `thermal` | enum `adiabatic` \| `temperature` \| `heat_flux` \| `convection` | | `adiabatic` | | wall |
| `heat_flux` | quantity | heat_flux | 0 | any (positive into the fluid) | wall, `thermal` heat_flux |
| `heat_transfer_coefficient` | quantity | heat_transfer_coefficient | 10 W/(m²·K) | positive | wall, `thermal` convection |
| `ambient_temperature` | quantity | temperature | 300 K | positive | wall, `thermal` convection |
| `partner` | ref `boundary`, optional | | null | required by its kinds | cyclic, interface |

The turbulence group is `turbulence`, `intensity`, `viscosity_ratio` and `length_scale`. The
dimensions all exist in `stokes.units`; none was added. Vectors have no parameter kind of their own
(decision 2.5): a velocity inlet takes a magnitude normal to the faces, three components, or a
magnitude along `direction_vector`.

**References** (decision 2.8). Every reference starts as null (a `ref` has no default), so a
default of null cannot tell a required reference from an optional one: `ParamSpec.required`
marks the required ones, `tag` on both types (the JSON schema says `x-required: true`). A node
is added only with its required references set, and setting one to null is refused ("required
reference", `validation`). `partner` and `material` are optional: null until set, and null
clears them: `parameter.set` takes `value: null` (its `value` is a nullable command parameter),
and `boundary.set` takes `values: {"partner": null}`. `parameter.set` checks only the
referenced node's type (a `partner` refuses a `tag`, a `material` refuses a `part`); that a
boundary's tag is of kind `boundary` and a region's of kind `region` is the commands' to keep
(they refuse a tag of another kind, `tag.invalid`) and the check's to report.

`Project.referrers(node_id, subtree=False)` lists `(node, parameter)` for every `ref` parameter
in the tree that points at the node, in tree order; with `subtree`, every reference into the node
or anything below it from a node outside it, which is what a removal would leave dangling. It
walks the tree on call. `node.remove` refuses a node so referenced (`model.referenced`, naming up
to five referrers in the message and up to a hundred in `details.referrers`); the cascading verbs
are `boundary.delete`, `region.delete` and `tag.delete` with `force`. They clear the optional
references into what they remove (a partner) in the same step and refuse a required one.
`Project.remove` itself stays unguarded: undo and redo use it, and a project edited by hand can
still hold a reference whose target is gone (a boundary then lists `missing: true`).

**Status** is derived, never stored (decision 2.10): a boundary or region is `stale` when its tag
is stale, `failed` when the tag is missing or the kind is invalid, `warning` when `setup.check`
has a warning for it. The front end computes it from the tag link and the check; `boundary.list`
and `region.list` compute the same for the CLI and MCP from the findings they can see alone
(Commands below).

## The catalogue

`KINDS` in `catalogue.py` is a tuple of `ConditionSpec(id, label, family, params, fields, needs,
color, glyph, arrives, note)`, the eight rows of ADR-0022 decision 2.3 in its order; `BY_ID` maps
ids to entries and `KIND_IDS` lists the ids. The ids and the enums' choices are the node type's
vocabulary in `stokes/geometry/model.py`; the catalogue imports them, so the schema and the `when`
mappings share one list, and it refuses to load when `KIND_IDS` differs from `CONDITION_KINDS`.
That keeps the catalogue out of `import stokes.api`: registering `boundary` needs only the
vocabulary (ADR-0022 decision 2.1), and the command modules load the catalogue.

| id | Label | Family | Parameters used (when) | Fields fixed | needs | Colour | Glyph |
|---|---|---|---|---|---|---|---|
| `velocity_inlet` | Velocity inlet | inlet | `direction`; `velocity` (normal, vector); `velocity_x`, `velocity_y`, `velocity_z` (components); `direction_vector` (vector); `temperature`; turbulence group | U, T, turbulence | energy: `temperature`; turbulence: the group | `#d64f4f` | `arrow_in` |
| `mass_flow_inlet` | Mass-flow inlet | inlet | `mass_flow`, `temperature`, turbulence group | U, T, turbulence | as above | `#d64f4f` | `arrow_in` |
| `pressure_inlet` | Pressure inlet | inlet | `total_pressure`, `temperature`, turbulence group | p, T, turbulence | as above | `#d64f4f` | `arrow_in` |
| `pressure_outlet` | Pressure outlet | outlet | `pressure`, `backflow_temperature`, turbulence group (backflow) | p, T, turbulence | energy: `backflow_temperature`; turbulence: the group | `#3b82c4` | `arrow_out` |
| `wall` | Wall | wall | `shear`, `thermal`; `temperature` (thermal temperature); `heat_flux` (heat_flux); `heat_transfer_coefficient`, `ambient_temperature` (convection) | U, T | energy: those four | `#7f8c99` | `none` |
| `symmetry` | Symmetry | constraint | — | — | — | `#a275c9` | `plane` |
| `cyclic` | Cyclic | constraint | `partner` | — | — | `#e0803a` | `pair` |
| `interface` | Fluid-solid interface | interface | `partner` | U, T | — | `#5aa452` | `diamond` |

`interface` is not available yet: `arrives` is `M5` (fluid–solid interface detection), and
`boundary.assign` and `boundary.set` refuse it (`boundary.invalid_kind`). Every other kind
has `arrives` empty.

- **`params`** are `ParamUse(name, rule, required, when)`, in the node type's order (the wall
  lists its `temperature` after its `thermal` switch).
- **`rule`** is checked on the SI value by `check_rule(use, value)`: `any`, `positive`,
  `nonnegative`, `nonzero`, and `fraction` (0 < x < 1). Every value must be finite. A sequence is a
  vector: it needs three finite numbers and the rule applies to its length (`direction_vector`
  takes `nonzero`). The result is the message (naming the parameter and the rule: "velocity must
  be positive; got 0") or None. The velocity components carry `any`; that all three are not zero
  is a rule across parameters, which `setup.check` applies (the velocity inlet's `note` says so).
- **`required`** marks a parameter whose default makes no working condition: `velocity`, the
  three components, `mass_flow`, `partner`. The value panel asks for it first; `setup.check`
  reports it until it is given. Every other parameter's default passes its rule as it is.
- **`when`** maps a switch to the choices under which the parameter is used; every entry must
  hold, and an empty `when` means always. The switches are the enum parameters `direction`,
  `turbulence` and `thermal` (`SWITCHES`, with their choices; the first choice is the default).
  `ConditionSpec.uses(values)` returns the parameters in use for a boundary's values (a node's
  `params` as stored, or a partial mapping: a switch that is missing counts as its default). The
  Properties panel and the value panel show only those (one filter, decision 7.1); `boundary.set`
  refuses a value for a parameter not in use; `setup.check` applies the rules to those.
- **`fields`** are the solver fields whose boundary value the kind fixes (`U`, `p`, `T`, and
  `turbulence` for k, epsilon, omega and nut); a constraint kind fixes none, every field takes the
  constraint. `setup.check` reads `p` here for `setup.no_pressure`.
- **`needs`** maps a physics model (`energy`, `turbulence`) to the parameters that need it. It is
  recorded now and judged in M3 (decision 2.7): until `/physics/models` exists, `setup.check`
  lists a parameter in use that needs a physics as `setup.needs_physics`, information only. An
  adiabatic wall uses nothing that needs energy; every other `thermal` choice does.
- **`color`** is the tag colour the kind gives (decision 8.3): red for the inlet family and blue
  for the outlet, as `web/DESIGN.md` reserves them; every colour is a member of the tag palette
  (`SELECTION_PALETTE`), so no new colour enters the viewport. **`glyph`** is the viewport marker
  of decision 8.1, one instance per boundary; `none` draws nothing (the tag's tint suffices).
- **`note`** is a sentence for people: the component rule, gauge pressures, the sign of a heat
  flux, the partner rule.

`kind(id)` returns the entry or raises `boundary.invalid_kind`; it returns a kind that is not
available too, with its `arrives`. The catalogue checks its own definitions when it loads (rules,
switches and their choices, families, glyphs, colours, fields, needs) and refuses a malformed one
with `ConditionError`.

**`boundary.kinds`** returns the table with each parameter's `ParamSpec` JSON schema on
`boundary` beside its use, so a client renders a kind's fields without a second query:

```json
[{"id": "velocity_inlet", "label": "Velocity inlet", "family": "inlet",
  "fields": ["U", "T", "turbulence"],
  "needs": {"energy": ["temperature"],
            "turbulence": ["turbulence", "intensity", "viscosity_ratio", "length_scale"]},
  "color": "#d64f4f", "glyph": "arrow_in", "arrives": null, "available": true,
  "note": "velocity_x, velocity_y and velocity_z may each be zero, but not all three: ...",
  "params": [
    {"name": "direction", "rule": "any", "required": false, "when": {},
     "schema": {"title": "Direction", "type": "string",
                "enum": ["normal", "components", "vector"], "default": "normal",
                "description": "..."}},
    {"name": "velocity", "rule": "positive", "required": true,
     "when": {"direction": ["normal", "vector"]},
     "schema": {"title": "Velocity", "type": ["string", "number"], "x-kind": "quantity",
                "x-dimension": "velocity", "default": 0.0, "description": "..."}}]}]
```

`arrives` is null for an available kind. `engine` is accepted and ignored until M3, which lists
only the kinds that engine supports.

## Commands

Every command that changes the tree is one undo step and one journal record, is tested through
`Session.execute` and replays into a fresh session to the same ids and tree (constitution §6).
A command plans its model commands first, against the tree as it will be, then pushes them as one
`Step` (a `Composite` that also runs the command's check; `stokes/api/commands/setup_plan.py`):
a refusal while planning changes nothing, a model command or the check that refuses reverts the
whole step, and nothing is announced or journaled unless all of it applied. Removals apply last
in a step, so that undo puts a removed node back before it restores a reference to it.

**Ids are stable for replay.** A command records the ids it used with `ctx.record`: `node_id`
(the boundary, or the region), `tag_node_id` (the tag it made, or used) and, for
`boundary.assign`, `region_node_id`, and a replay passes them back. What a region brings with it
takes ids derived from the region's: its tag `stable_id("n", "<region id>/tag")` when the command
names none (`region_tag_id`), its `boundaries` group `stable_id("g", "<region id>/boundaries")`
(`boundaries_id`); `/geometry/tags` takes the structural id `tag.create` gives it. Members and
bodies are recorded as given, as `tag.create` records them: part ids are stable per import.

**References to nodes.** A boundary, a region or a material is named by its id or path; a bare
name names a tag (under `/geometry/tags`), a region (under `/regions`) or a material (under
`/physics/materials`), by the parameter. **Names** follow the node rule (letters, digits,
underscores, no leading digit). A name given for a node a command makes or renames that is
taken is refused (`validation`, `details.parameter: "name"`); a default name is made unique
(`inlet`, `inlet_2`).

**Errors name what was wrong.** The refusals these commands make carry `details.parameter`:
`target`, `kind`, `region`, `color`, `name`, `tag`, `material`, `members`, or the boundary
parameter a value was for, and so do the model's refusals of a value (a quantity that does not
parse or has the wrong unit), so a client shows each under its field. Members that do not parse
are refused by the tag internals (`tag.invalid`, naming the part).

| Command | Parameters | Result |
|---|---|---|
| `boundary.kinds` | `engine` (optional; default the open project's engine) | the catalogue above, each kind with `supported` and `unsupported_reason` for that engine; `mutates: false`, no project needed (without an engine every kind is supported) |
| `boundary.assign` | `target`, `kind`, `values`, `name`, `region`, `color`; journals: `node_id`, `tag_node_id`, `region_node_id` | `{boundary: <boundary.list entry>, tag: {id, path, name, color, kind}, region: {id, path, name, kind}, created: [ids], recoloured}` |
| `boundary.set` | `boundary`, `kind`, `values`, `tag`, `members`, `name`, `keep_color` = false | the `boundary.list` entry with `changed` and `recoloured` |
| `boundary.delete` | `boundary`, `delete_tag` = false | `{id, path, name, deleted: true, tag_deleted}` |
| `boundary.list` | `region` (optional) | `[<entry>]`, below; `mutates: false` |
| `boundary.overlap` | `members` (the tag commands' form: the live pick) | `{overlaps: [{tag: {id, name, path, color}, boundaries: [{id, name, path, kind}], part, faces}], faces}`: the boundary-kind tags already holding some of the faces (G10: warned, allowed, refused by the compiler); `faces` null when a whole part of unknown count; `mutates: false` |
| `boundary.uncovered` | `part` (optional: a part or a group; default every region's bodies) | `{parts: [{part, name, path, total, faces, ranges: [[first, last]]}], faces}`: per part the faces no boundary's tag holds (the compiler's `unassigned` wall), as merged inclusive ranges; `mutates: false` |
| `region.create` | `name`, `kind`, `bodies`, `material`; journals: `node_id`, `tag_node_id` | the `region.list` entry |
| `region.assign` | `material`, `target`, `name`; journals: `node_id`, `tag_node_id` | `{region: <region.list entry>, bodies: [part ids], moved: [part ids], created: [ids]}` |
| `region.set` | `region`, `material`, `kind`, `members`, `name` | the `region.list` entry with `changed` |
| `region.delete` | `region`, `delete_tag` = false | `{id, path, name, deleted: true, boundaries: [ids], tag_deleted}` |
| `region.list` | — | `[<entry>]`, below; `mutates: false` |
| `setup.check` | — | `{state, next, counts, findings}` (Checks below); `mutates: false`; T-037 |
| `setup.defaults` | `target`, `template` (`external` \| `internal`), `inlet`, `outlet`, `material`, `velocity`; journals: `node_ids` | `{region, material, material_created, boundaries: [{name, kind, tag}], walls_faces, skipped, warnings}`; one undo step; built (T-039) |

Three existing commands changed with them: `tag.delete` refuses a tag a boundary or region uses
unless `force` (`docs/engineering/geometry-schema.md`), `node.remove` refuses a referenced node
(References above), and `material.list` fills `regions` with the ids of the regions that use each
material, in tree order (`docs/engineering/materials-schema.md`). `parameter.set` takes
`value: null` to clear an optional reference.

### `boundary.assign`

The one atomic, replayable assign (decision 3.1); the undo step reads "assign <kind label> to
<boundary name>" ("assign velocity inlet to inlet"). `target` is one parameter, checked by hand:

| `target` | What happens |
|---|---|
| members `[{part, faces}]` (the live pick, the tag commands' input form; `faces: "all"` for a body) | a new boundary-kind tag holds them, parsed and stamped as `tag.create` does; an empty list, or faces that do not exist, is refused |
| a tag (id, path or name) no boundary uses | a new boundary on it |
| a tag one boundary uses, or that boundary (id or path) | that boundary takes the kind and values: a second assign re-kinds, it never duplicates; a tag two boundaries use is refused (give the boundary) |
| a plain group at `/regions/<region>/boundaries/<name>` (made before ADR-0022) | it becomes a `boundary` in place: the same id, name and place (`ConvertGroup`); its tag is the boundary-kind tag of the same name when no boundary uses one, else a new empty tag of that name made unique |

The tag must be of kind `boundary`; a region or plain tag is refused (`tag.invalid`). One step
makes, in this order, what is missing: the `/geometry/tags` group; the region with its tag (kind
`region`, the region's name made unique among the tags, no bodies, a palette colour that is not
the inlet red or the outlet blue) and its `boundaries` group; the new tag (named `name`, or the
kind's default made unique among both the tags and the region's boundaries: `inlet` for the inlet
family, `outlet`, `wall`, else the kind's id, so `symmetry`, `cyclic`); the boundary (named
`name`, or its tag's name, or the same default) with `kind` and `tag`; then one `SetParam` per
value, so a refusal names its value. The tag takes the kind's colour unless `color` is given
(`recoloured` says whether it changed; a new tag starts in it).

**The region** of a new boundary (decision 2.9): `region` when given (a typed region; a plain
group under `/regions`, made a `region` of kind fluid in place; or, for a name no region has, a
new fluid region); else the project's only `region` of kind `fluid`; else the plain group
`/regions/fluid`, made typed in place; else a new region `fluid` (`fluid_2` when a solid region
holds the name). Several fluid regions and no `region` is refused (`details.parameter: "region"`).
A boundary that exists stays in its region: a `region` that names another is refused, and the
region it lives in is made typed when it is a plain group.

**Values** are checked against the catalogue: `kind(id)` refuses an unknown kind and one not
available yet (`interface`, `boundary.invalid_kind`); a value for a parameter the kind does not
use with the stored values merged with the given ones is refused (`boundary.invalid_value`:
"velocity is used only with direction normal or vector; this is a velocity inlet with direction
components"), and so are `kind` and `tag` given as values. Once the step has applied, each given
value is read back in SI (an expression evaluated) and `check_rule` refuses one that breaks its
rule, which reverts the whole step. Values not given keep their stored values or defaults: a
velocity inlet assigned without a velocity is made, and lists `setup.value_invalid` until it has
one.

### `boundary.set`, `boundary.delete`

**`boundary.set`** (decision 3.2) is one undo step ("edit boundary <name>") for any mix of a new
kind, values (checked as above, against the new kind), a new boundary-kind `tag` (the "replace
location"), new `members` for the boundary's tag (after `tag` when both are given: `tag.set`
inside the step) and a new `name`. A new kind or tag gives the tag the kind's colour unless
`keep_color`. A plain-group boundary is refused (`boundary.assign` makes it typed). Nothing to
change adds no step (`changed: false`). `parameter.set` still sets any one parameter.

**`boundary.delete`** removes a boundary (a plain-group one too) in one step; the partners that
named it become null in the same step. With `delete_tag` its tag goes too, refused
(`model.referenced`) while anything else uses the tag.

### `boundary.list`

```
{id, path, name, region: {id, path, name}, kind, label, family,
 tag: {id, name, path, color, visible, parts, faces, stale, missing},
 primary: {name, value, display} | null, warnings: [codes], status}
```

Plain-group boundaries are listed as `{id, path, name, region, legacy: true}`. `tag.faces` is
null while a whole part's face count is unknown; `stale` is known for meshes in memory. `primary`
is the value a row shows: the first required parameter in use, else the first quantity in use,
else the first switch (`value` in SI, or the reference's id; `display` in the project's display
units, or the referenced node's name, "none" when null); null for a kind without parameters.
`warnings` are the findings a boundary has on its own, with `setup.check`'s codes:
`boundary.invalid_kind`, `setup.tag_missing`, `setup.empty_tag`, `setup.stale_tag`,
`setup.overlap` (its tag shares faces with another boundary-kind tag, as `tag.list` warns),
`setup.value_invalid` (a value in use breaks its rule, or the three velocity components are zero)
and `setup.partner_missing` (no partner, or one that does not name it back). `status` is `failed`
(tag missing or kind invalid), else `stale`, else `warning` (any of the codes), else `ok`.

### Regions

**`region.create`** makes `/regions/<name>` with its tag (kind `region`, the region's name made
unique among the tags; `tag_node_id`, else the derived id) holding `bodies` whole, and its
`boundaries` group, in one step; a plain group of that name is made the region in place, keeping
its id and children. `kind` defaults to the material's kind, else fluid; a material of the other
kind is refused (`materials.kind_mismatch`, decision 4.2). Bodies leave any other region.

**`region.assign`** (decision 4.1) gives a material:

- to a **region** (id, path or name; or a region tag a region uses): the region takes the
  material, refused when the kinds differ;
- to **bodies**: a list of part or group ids or paths, members with `faces: "all"`, one part or
  group, or a region tag no region uses (its bodies). Faces of a body are refused (`tag.invalid`),
  and so is a target with no bodies. Bodies already in a region of that material stay. The others
  go to **the region for the material**: `name` when given (made when missing; a region holding
  another material is refused); else a region that has the material (the one named after it
  first); else the only region of the material's kind that has no material, which takes it (so
  a region `boundary.assign` made gets the material and the bodies); else a new region named
  after the material, made unique (`water`, `aluminium`). A body leaves every other region
  (`tag.remove` on its tag) and joins that one (`tag.add`), inside the step; a region left empty
  stays (`setup.empty_region`). `moved` lists the bodies that joined it.

A material of the other kind than its region is refused only where a region is given or named:
bodies picked for a solid material leave a fluid region for a solid one.

**`region.set`** changes the material (refused when the kinds differ, with the kind as it will be),
the kind (refused when the material stays of the other kind), the bodies (`members`: the region
tag's members are replaced, `tag.set`, and those bodies leave every other region) and the name, in
one step. `parameter.set` with `value: null` clears the material. A plain-group region is refused
(`region.create` with its name makes it typed).

**`region.delete`** removes a region with its `boundaries` group and boundaries (a plain-group
region too) in one step; partners in other regions that named one of its boundaries become null.
With `delete_tag` the region tag goes too, refused while another node uses it.

**`region.list`**:

```
{id, path, name, kind, material: {id, name, kind, missing} | null,
 tag: {id, name, path, color, visible} | null, bodies: [part ids], boundaries: [ids],
 uncovered_faces, stale, warnings: [codes], status}
```

Plain-group regions are listed as `{id, path, name, legacy: true}`. `boundaries` lists every
entry of its `boundaries` group, plain groups included. `uncovered_faces` counts the faces of its
bodies that no boundary tag of the region holds (they become no-slip walls at compile), by range
arithmetic; null while a body's face count is unknown. `warnings`: `setup.tag_missing`,
`setup.empty_region` (a material but no bodies) or `setup.empty_tag` (no bodies, no material),
`setup.no_material` (none, or one that is gone), `setup.material_kind`, `setup.stale_tag`.
`status` is `failed` (tag missing), `stale`, `warning` or `ok`.

### `setup.defaults` (T-039, built)

`setup.defaults` (decision 10.1) turns a prepared body — an enclosure output (ADR-0021 6.2) or an
extracted fluid (6.1) — into a region with a material and typed boundaries in one undo step. The
tables live in `stokes/physics/conditions/templates.py`: `external` maps `inlet` → `velocity_inlet`,
`outlet` → `pressure_outlet`, `symmetry` → `symmetry`, `far_field` → `wall` slip, `ground` and
`walls` → `wall` no-slip; `internal` maps `inlet` → `velocity_inlet`, `outlet` → `pressure_outlet`,
`walls` → `wall` no-slip.

- `target` is a `part` (id or path) or a region (id, path or name); a region stands for the parts
  its tag holds. A tag **qualifies** by its name, exactly, and only when it holds a face of the
  target.
- `inlet` and `outlet` are member lists in the tag commands' form: a tag named `inlet` / `outlet` is
  made, or set to those faces.
- `velocity` (quantity text such as `"2 [m/s]"`, or an SI number) is written to the velocity inlet's
  `velocity`; the result warns when it reached no boundary.
- Every face of the target that is in no boundary-kind tag afterwards goes to a `walls` tag (made or
  extended) → `wall` no-slip; `walls_faces` says how many faces the command gave it.
- For `internal`, the target's faces leave the extraction's `caps` tag: the ones the inlet and the
  outlet named, and the ones no boundary named, which then become walls. A face two boundary tags
  hold is refused when the case is compiled (`tag.overlap`), so the set-up the helper makes
  compiles; `warnings` says how many caps became walls. A `caps` tag a boundary already uses is left
  alone.
- Region: the region whose tag holds the part, else `fluid` — the plain group `/regions/fluid` made
  typed, or a new fluid region (`fluid_2` when a solid region holds the name) created with the part
  as its body. Material: `material` when given, else the region's own, else the project's only
  material of that kind, else a new one from the built-in templates — `gas` named `air` (external)
  or `liquid` named `water` (internal) — made with its properties inside the same step, which the
  result marks `material_created: true`.
- A boundary that already uses a qualifying tag is left alone and named in `skipped`.
- Result: `{region, material, material_created, boundaries: [{name, kind, tag}], walls_faces,
  skipped, warnings}`; `region` and `material` are `{id, path, name, kind}` and `tag` is the id of
  the boundary's tag.
- One undo step ("add default conditions (external)") and one journal record: the step is a `Plan`
  of `setup_plan.py`, and `node_ids` records the ids it made — `region`, `material`, and `tags` and
  `boundaries` keyed by the template's tag names — so a replay builds the same tree.
- Errors: `validation` (an unknown template, a target that is not a part or a region, a target
  region that holds no bodies, `inlet` / `outlet` faces that are not the target's),
  `materials.kind_mismatch` (a material of the other kind than the region).

### The CLI

`stokes boundary assign|set|delete|list|kinds` and `stokes region create|assign|set|delete|list`
(`stokes/cli/setup.py`), one API command each, JSON output as `stokes tag`; the project is the
first argument (`kinds` needs none) and a verb that changes it saves it. A target or a list of
bodies that starts with `[` is JSON:

    stokes boundary assign demo.stokes /geometry/tags/inlet velocity_inlet --values '{"velocity": "2 [m/s]"}'
    stokes boundary assign demo.stokes '[{"part": "/geometry/parts/pipe", "faces": [3]}]' pressure_outlet --name exit
    stokes region assign demo.stokes water '["/geometry/parts/pipe"]'
    stokes tag delete inlet --force --project demo.stokes

`stokes setup check <project>` (T-037) prints the check's JSON and exits 1 while the state is
`incomplete`; `stokes setup defaults <project> <target> <template> [--inlet JSON] [--outlet JSON]
[--material M] [--velocity "2 [m/s]"]` (T-039) gives a prepared body its default conditions and
saves the project.

## Checks (T-037, built)

`setup.check` is a pure query over the project and the tag staleness the session knows; it never
writes to the tree. The front end calls it, debounced 300 ms, after `node.*`, `tag.changed` and
`boundary.changed`; the CLI and MCP call it directly.

```
{state, next, counts: {regions, materials, boundaries, uncovered_faces, legacy},
 findings: [{code, severity, message, suggestion?, nodes: [ids],
             fix?: {command, params} | {action}}]}
```

Findings are listed errors first, and inside a severity in tree order (a finding about the project
as a whole comes first). **States**, in precedence: `not_defined` (no region holds a body),
`incomplete` (an error), `stale` (a region or boundary tag is stale or missing and nothing else is
wrong), `ready` (no error; warnings and information allowed). `setup.tag_missing` is the one error
that does not make the state `incomplete`: a tree someone edited by hand is mended by putting the
tags right, which is what `stale` says. The run queue adds `meshed`, `running`, `done` and
`done_unconverged` in M2 and M3. `next` is the first error's `suggestion`, else the first
warning's, else "Set-up complete".

| Code | Severity | Condition | Fix |
|---|---|---|---|
| `setup.no_region` | error | no `region` holds a body | pick bodies → Material… |
| `setup.no_material` | error | a region has no material | Material… |
| `setup.material_kind` | error | region and material kinds disagree | `region.set` |
| `setup.no_inlet` | error | a fluid region has bodies and no inlet-family boundary | add an inlet |
| `setup.no_outlet` | error | … no outlet-family boundary | add an outlet |
| `setup.no_pressure` | warning | no boundary of a fluid region fixes pressure (no kind with `p` in `fields`) | add a pressure outlet |
| `setup.value_invalid` | error | a parameter in use breaks its rule (`check_rule`), or the velocity components are all zero | the value panel |
| `setup.tag_missing` | error | a boundary or region references a tag that is gone | Replace faces… |
| `setup.empty_tag` | warning | a boundary's tag has no faces, or a region's tag no bodies | Replace faces… / Delete |
| `setup.stale_tag` | warning | a referenced tag is stale (ADR-0021 8.4) | `tag.rematch` |
| `setup.overlap` | warning | a face lies in two boundary tags | edit the tags |
| `setup.uncovered_faces` | info | faces of a region's bodies in no boundary tag (no-slip walls at compile) | pick them → Wall |
| `setup.partner_missing` | error | a cyclic or interface boundary without a valid partner, or partners that do not name each other | the value panel |
| `setup.needs_physics` | info | a parameter in use needs energy or turbulence (`needs`) | M3 |
| `setup.legacy` | warning | a plain-group boundary or region from before ADR-0022 | `boundary.assign` on it |
| `setup.empty_region` | warning | a region with a material but no bodies | `region.set` members / Delete |

Added since the table: `setup.kind_unsupported` (error: a boundary's kind is one the solver in
`/solver/settings` cannot write, e.g. `mass_flow_inlet`; the same condition `solve.unsupported_kind`
refuses at run time).

Codes are stable strings; messages are sentences; `fix` carries an executable command when there
is one, so an agent can repair the set-up from the result.

## Events

| Event | Payload | When |
|---|---|---|
| `node.added`, `node.changed`, `node.removed` | as everywhere | each node a step creates, changes or removes, in the order of its model commands (removals last); `node.changed` carries `param` for a parameter, none for a rename or a plain group made typed |
| `tag.changed` | `{node_id, path}` | a tag a step creates, gives new members or deletes |
| `boundary.changed` | `{node_id, path}` | once per boundary a command changes, creates or deletes (a cleared partner included) |
| `region.changed` | `{node_id, path}` | once per region a command changes, creates, types or deletes (bodies leaving it included) |

They follow the `node.*` events of the step, in that order. A command that changes nothing emits
nothing; undo and redo emit `history.changed`, as everywhere. `boundary.kinds`, `boundary.list`,
`region.list` and `setup.check` emit nothing.

## Errors

| Code | When |
|---|---|
| `boundary.invalid_kind` | a kind the catalogue lacks, or one not available yet (`interface`, `details.arrives`) |
| `boundary.invalid_value` | a value for a parameter the kind does not use with these values, `kind` or `tag` given as a value, or a value that breaks its rule once evaluated; the message names the parameter and the rule, `details.parameter` the parameter |
| `boundary.error` | the base code (`ConditionError`): a malformed catalogue definition, refused when the catalogue loads |
| `materials.kind_mismatch` | a fluid material on a solid region or the reverse (`region.create`, `region.assign`, `region.set`) |
| `model.referenced` | `node.remove` of a node something outside its subtree refers to; `tag.delete` of a used tag without `force`; `delete_tag` of a tag something else uses |
| `tag.invalid` | a target tag of another kind (a region tag for a boundary, a boundary tag for a region); members that do not parse or name faces that do not exist; faces of a body given to a region |
| `validation`, `model.unknown_node`, `units.*` | as everywhere: a bad name or target, a name taken, several fluid regions and no `region`, a quantity that does not parse |

The three `boundary.*` errors are `ConditionError`, `InvalidKind` and `InvalidValue` in
`stokes.physics.conditions`; they derive from `ValidationError`, as every refusal of bad input
does. `KindMismatch` (`stokes.physics.materials.errors`) and `ReferencedNode` (`stokes.errors`,
a `ModelError`) are the other two.

## Compatibility

Before ADR-0022 a boundary was a plain group, `/regions/fluid/boundaries/<patch>`, made by three
undo steps keyed on a patch name with the region `fluid` hard-coded. Such groups, and a plain
`/regions/fluid`, stay valid as **legacy** entries: `boundary.list` and `region.list` list them as
`{id, path, name, legacy: true}`, `setup.check` warns `setup.legacy` (T-037), and
`boundary.assign` with one as its target makes it a `boundary` in one step; the plain group of its
region, and `/regions/fluid` when it is the default region, become `region` nodes the same way.
That is `ConvertGroup`, a model command that gives a plain group a type in place: the same id,
name, place among its siblings and children, so a region keeps its `boundaries` group and the
plain boundaries in it (removing the group and adding a typed node, as ADR-0022 decision 2.9
first put it, keeps the id but drops the children). Undo makes it the plain group again; an
expression that read the typed node's parameters fails until redo. The project schema stays at
version 2: the two types are additions and nothing is migrated. ADR-0021 decision 2.5 is amended
accordingly: the boundary is typed, with a kind and values, and created by `boundary.assign`.

## Performance

`import stokes.api` does not load the catalogue: `stokes.model` registers `boundary` from the
vocabulary in `stokes.geometry.model`, so `tests/perf/test_import_time.py` and the materials guard
(`import stokes.api` loads no `stokes.physics` module but the material node types) are unchanged.
The command modules load it with `stokes.api.commands`: on the development laptop, with bytecode
caching off, the two `stokes.physics.conditions` modules that `boundary.kinds` needs take about
8 ms of self time together, about 3 ms of it compiling the source (`checks.py`, the check itself,
is imported with the command modules as well). `boundary.kinds` takes 0.05 ms. T-037 records the
time of `setup.check` on the 4,500-part tree (not a budget). `Project.referrers`, which
`node.remove` and `tag.delete` call once, is one walk of the tree; `tag.list` and `boundary.list`
find the users of the tags in one walk and the shared faces by range arithmetic, never expanded to
ids.
