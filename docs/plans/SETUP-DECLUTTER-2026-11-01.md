# Full-mode set-up declutter — design and briefs, 2026-11-01

Owner's ask (2026-11-01): "the set-up panel is cluttered". Guided mode is done
(`GUIDED-MODE-2026-11-01.md`); this is the Full-mode half. Full mode is the tree plus the Details pane
(`workspace/defaults.ts:145`; stage panels hidden, `:172`), so "the set-up panel" is the Details pane.
Counts are by code read at `6e4d250` for the demo pipe (4 bodies, water, inlet, outlet, wall).

## 1. Inventory

**Boundary page** (velocity inlet; `PropertiesPanel.tsx`, split by `conditions.ts:83-102`)

| Item | Count | First run needs |
|---|---|---|
| Heading, status, Name, path line | 4 | none |
| Condition select (`NodeVerbs.tsx:111`) | 1 | yes |
| Main fields: tag, direction, velocity, layers, mesh_size | 5 | velocity |
| More…: temperature, turbulence, intensity, viscosity ratio | 4 | none |
| **Total** | **14** | **2** |

`layers` and `mesh_size` show on every kind because no kind lists them; `layers` is walls-only and an
inlet carrying it is refused at the snapshot (`stokes/geometry/model.py:639-656`). Temperature shows with
energy off; turbulence inputs show for a laminar run.

**Region, material, body**

| Page | Items | First run needs |
|---|---|---|
| Region: header 4, Material select, kind, tag | 7 | Material |
| Material: header 4, 16 schema fields (`physics/materials/model.py`) | 20 | a density/viscosity readout |
| "What each body is" (`BodiesPage.tsx`): purpose sentence, 4 bodies × 4 role buttons, the same solid note ×3 (`:120-122`) | 20 | one Fluid click |

**Mesh page** (`/mesh/settings` selected; `DetailsPanel.tsx:383,401,407` stacks stepper + `MeshPanel`)

| Block | Items | First run needs |
|---|---|---|
| Stepper line + Hide | 2 | no |
| Stepper: heading, meta, 9 rows, Generate, reason (`MeshStepper.tsx:231-274`) | 13 | no |
| Run section: state, bar, **Generate again**, engine line, **reason again** (`MeshPanel.tsx:264-340`) | 5 | Generate, state |
| Settings tools: category select, search, Show all (`SettingsFields.tsx:57-67`) | 3 | no |
| 7 section titles + 7 description sentences (`settingsPresentation.ts:82-96`) | 14 | no |
| Visible fields, snappy/part (`meshSettingIncluded`) | 19 | base size, layers |
| Mesher "why" + "Unavailable options" (`SettingsNodeFields.tsx:53-56`) | 2 | no |
| Log toggle | 1 | no |
| After a mesh: counts 4, verdict, quality rows, fidelity, Show in view, note | ~12 | cells + verdict |
| **Total before / after a mesh** | **~59 / ~71** | **4** |

Choosing the Sizing task also renders `SizingPage` above `MeshPanel` (`DetailsPanel.tsx:404+407`), so
base size and surface size appear twice.

**Solve page** (`RunPanel.tsx`, `solver_settings` has 21 parameters, `stokes/engines/model.py`)

| Block | Items | First run needs |
|---|---|---|
| Run: state, bar, Run, reason, findings | 5 | Run, state |
| Residuals: title + placeholder sentence | 2 | the plot |
| Settings: title, tools 3, 6 titles + 6 sentences (`settingsPresentation.ts:67-74`) | 16 | no |
| Fields: 12 sectioned + 9 in "Other settings" (transient, time step, end time, density model, molar mass, 4 initial-turbulence) | 21 | turbulence, iterations |
| Engine "why" + "Unavailable options", Job log | 3 | no |
| **Total** | **47** | **4** |

`solverSettingIncluded` returns true for every name (`settingsPresentation.ts:99-101`): time step and end
time show for a steady run.

**Tree (outline) set-up sections** (`outlineModel.ts:96-165`, `stageTreeModel.ts:387-393`)

| Section | Rows | First run needs |
|---|---|---|
| Geometry: Bodies, Tags, Operations (+4 bodies, ~4 tags) | ~11 | Bodies |
| Mesh: Global size, Capping, Mesher (+2), Mesh, 5 task rows incl. a second "Boundaries" | 10 | Global size, Mesh |
| Physics: Materials (+1), Regions (+1), Boundary conditions (+3), Parameters, Reference values | 10 | material, boundaries |
| Solve: Methods, Controls, Initialisation, Run, Runs | 5 | one settings row, Runs |

Methods and Initialisation open the same "Model" category (`stageTreeModel.ts:147`); the initial-turbulence
fields are not in it.

**Conditions panel** (hidden by default): a 3-sentence "Set up flow" list (`ConditionsPanel.tsx:86-133`)
and a **Fix** button that only toasts "not wired yet" (`:429-437`).

## 2. Diagnosis

The pipe needs about a dozen decisions (fluid body, material, inlet speed, outlet pressure, mesh
fineness, layers, flow model, iterations, Generate, Run); Full mode puts them among ~150 controls.
Four causes. **Duplicates**: two Generate buttons and two identical reason lines on one page, base size
twice under Sizing, a second Boundaries row under Mesh, a mesher select beside a Mesher catalogue
folder; the guided cards already own body role, material, boundary values and fineness, and Full mode
re-implements them with other words. **Every option regardless of choice**: walls-only layers on an
inlet, temperature with energy off, turbulence inputs for laminar, transient fields for steady.
**Long notes**: 13 section sentences, 7 task-purpose paragraphs, the engine line, and the solid note
three times, all visible text, against the owner's rule. **Settings that never change on a first run**:
domain, margin, max cells, quality limits, host, slice, write interval, the 16 material fields, all at
the level of base size.

## 3. Design

### 3.1 One model for both modes
New pure module `web/src/setup/basicSets.ts` (+ test). It owns, per node type:

- `BASIC[type]`: the names Basic shows;
- `visibleFields(type, params, ctx)`: hidden / basic / advanced per name, with `ctx` = solver settings
  (energy, turbulence, transient) and mesher context;
- `KIND_WORDS` and `INLINE_VALUE`, moved from `guided/guidedTree.ts:157-188`; `guidedTree` re-exports them.

Boundaries reuse the guided cards' form rules as they are: `formFields` / `moreFields` in
`panels/conditions.ts:44-74` (Brief B's `HEADLINE`; the brief's "boundaryForm" is this). `basicSets`
calls them and adds the physics filter; it never copies the table. A parity test asserts every guided
card control is a Basic field of the same node type, so the modes cannot drift. Commands stay the same:
Full Basic calls `guided/commands.ts` (`setBodyRole`, `assignLibraryMaterial`, `configureMesh`,
`setBoundaryValue`).

### 3.2 Basic / Advanced per panel
New `panels/BasicAdvanced.tsx`: Basic fields flat (no category select), then a closed **Advanced**
disclosure holding today's `SettingsFields` / `SchemaFields` unchanged, minus the Basic names. The open
state is kept per node type in `localStorage` (`stokes.setup.advanced.<type>`), like the guided mode flag;
an error on an Advanced field opens it (`SettingsFields.tsx:38-53` already does this).

| Panel | Basic | Advanced |
|---|---|---|
| Boundary | Kind; the kind's lead (inlet speed, outlet pressure, mass flow, total pressure; wall/symmetry none) | direction + components, tag, mesh size, layers (walls only), temperature family (energy on), turbulence family (turbulence ≠ laminar), shear, thermal |
| Region | Material | kind, tag |
| Material | readout "density · viscosity" (`materialReadout`), Change… | the 16 fields |
| Body | role chips Fluid / Solid / Hole / Leave out | facts, mesh size, refinement, colour, Technical |
| Mesh settings | Fineness `Segmented` Coarse/Medium/Fine with its length (`guided/sizing.ts`), Boundary layers switch (0 ↔ `SWITCH_LAYERS`), Generate, one status line | mesher, base size number, surface size, max cells, domain, edges, layer details, quality, execution |
| Solver settings | Flow model (turbulence), Iterations, Run | engine, energy, residual target, reference, output, execution, slice; Transient group only when transient; initial turbulence only when not laminar; density model / molar mass only with energy |

Basic for a first run: boundary 2, region 1, mesh 4, solver 3.

### 3.3 Notes to tooltips
A few words on the control, the sentence in the tooltip. Move guided `Note` (`guided/controls.tsx:68`) to
`components/Note.tsx` and use it everywhere. Section descriptions become the summary's tooltip
(`SettingsFields.tsx:88`); task-purpose paragraphs become the page heading's tooltip; the engine line reads
"OpenFOAM · local"; the solid note becomes one "Solid · not meshed" tip; the residual placeholder reads
"No run yet".

### 3.4 Grouping and duplicates
One Generate and one reason line per page: the stepper foot hides when the page below it is
`MeshPanel`. A chosen task page replaces the settings block, not stacks on it. Rename the stepper heading
"Guided meshing" to "Mesh steps" (it clashes with Guided mode).

### 3.5 "More…" popover
A ⋯ button in the Details header (`Popover`, `components/Menu.tsx`): node path + Copy, Technical, engine
install path, "Unavailable options", box-side mapping, fidelity sample note, Log (opens Output), Create
settings.

## 4. Briefs (Sonnet)

Gates for each: `npm run typecheck`, `npx vitest run`, `npm run build`. Theme tokens only; no Python
changes; do not touch `web/src/guided/steps/**` beyond imports.

**S1 — boundaries, regions, materials, bodies.** Files: new `web/src/setup/basicSets.ts` (+ test),
new `web/src/panels/BasicAdvanced.tsx`, `panels/conditions.ts` (physics context into `boundaryFieldSplit`),
`panels/PropertiesPanel.tsx:278-299,416-505`, `guided/guidedTree.ts` (import the moved tables).
Acceptance: inlet page shows Kind + speed with Advanced closed; outlet Kind + pressure; wall Kind only;
layers only on walls; temperature family only with energy on; turbulence family only when not laminar;
region shows Material; material shows the readout; the Advanced state survives a reload.
Tests: `setup/basicSets.test.ts` (field lists per kind and physics context), `conditionFields.test.ts`
(no layers on an inlet), new `setup/guidedParity.test.ts` (guided controls ⊂ Basic).

**S2 — mesh and solver settings.** Files: `panels/settingsPresentation.ts` (`BASIC_MESH`, `BASIC_SOLVER`,
real `solverSettingIncluded`, sections "Transient" and "Initial turbulence"), `panels/SettingsNodeFields.tsx`
(Basic render: fineness + layers switch; flow model + iterations), `panels/MeshPanel.tsx:264-340,470-529`,
`panels/MeshStepper.tsx:241-273`, `panels/DetailsPanel.tsx:401-407`, `panels/RunPanel.tsx:203-237`.
Acceptance: before a mesh the Mesh page shows ≤ 8 controls with Advanced closed; one Generate; base size
once under Sizing; Solve Basic shows 2 fields + Run; time step and end time only with transient on.
Tests: `settingsPresentation.test.ts` (solver include rules, every Basic name exists in the schema
fixture), `meshPanelModel.test.ts` (fineness ↔ base size round trip through `guided/sizing.ts`).

**S3 — outline and notes sweep.** Files: `panels/outlineModel.ts` (an `advanced` flag on rows: Capping,
Mesher, Operations and Parameters when empty, Reference values, Fidelity, the Mesh task "Boundaries"; Solve
rows become Settings + Runs), `panels/stageTreeModel.ts:145-147`, `panels/TreePanel.tsx` (a "Show all
rows" item in the tree menu), `DetailsPanel.tsx:446` (it still says "Press the … chip in the top bar";
the strip replaced the chip in `bb51be7`), `ConditionsPanel.tsx:429-437` (hide Fix until it has a
command), the notes in §3.3, new `components/Note.tsx`.
Acceptance: Basic tree for the pipe shows ≤ 24 set-up rows; Show all restores every row; no visible
`<p>` set-up note over 8 words. Tests: `outlineModel.test.ts` (basic and full projections, ids still
unique), `userText.test.ts` rule 4 (literal JSX text in `panels/**` over 8 words fails, alerts exempt).

## 5. Risks

- **Hidden but wrong**: a value in Advanced the user never sees can break a run. Mitigation: an
  Advanced field differing from its default puts a dot on the disclosure; findings open it.
- **Drift returns** if someone edits a card's own table. The parity test is the guard; keep
  `HEADLINE` the only per-kind list.
- **Physics filter hides a value a case uses**: filters hide, they never clear; the snapshot still reads
  the stored value.
- **Persistence**: `localStorage` is per browser (the workspace schema refuses new keys).
- **Row/field-count tests** (`outlineModel.test.ts`, `settingsFields.test.ts`) need updates; keep the
  id-uniqueness test as strong.
- **Fineness on long bodies** gives large meshes; S2 shows `SizingPage`'s estimate beside the chips.

## 6. S1 done (boundaries, regions, materials, bodies), 2026-11-01

**Built.**
- `web/src/setup/basicSets.ts`: `BASIC`, `visibleFields(type, names, params, ctx, kinds)` (basic / advanced / hidden per
  schema name), `readSetupContext(tree)` (energy, turbulence, transient from `/solver/settings`), `changedFromDefault`
  (the dot), and the moved tables `KIND_WORDS` and `INLINE_VALUE` plus `BODY_ROLES`. `guided/guidedTree.ts` imports
  them (`BOUNDARY_KIND_WORDS` stays as an alias; `refTemperatureOf` is exported for the material readout).
- `panels/conditions.ts`: `boundaryFieldSplit` takes an optional physics context. Layers show only on a wall (or when a
  non-wall already holds a value, so it can be cleared); temperature (incl. wall `thermal`) hides with energy off,
  turbulence on a laminar run. Filters hide, never clear. `HEADLINE` is still the only per-kind list.
- `panels/BasicAdvanced.tsx` (+ css): Basic content, then a closed **Advanced** disclosure. Open state per browser in
  `localStorage` `stokes.setup.advanced.<type>` (`setup/advancedState.ts`, try/catch). A refusal on an Advanced field
  opens it; a dot on the title says an Advanced value is not its default (`tag` and `color` do not count).
- `PropertiesPanel.tsx`: boundary, region, material and body pages use it; every other page is unchanged.
  Boundary: Kind + lead value (inlet speed, outlet pressure, mass flow, total pressure; wall/symmetry Kind only).
  A switch at its default (inlet direction "normal") is Advanced; once changed it joins Basic with its components.
  Region: Material; kind, tag in Advanced. Material: `MaterialReadout` (name, density, viscosity) + Change… (opens
  Advanced). Body: `BodyRoleChips` (Fluid / Solid / Hole / Leave out via `setBodyRole`, same one-slot rule as the
  Bodies page); facts, fields, "what it is meshed as" line and Technical in Advanced. The old boundary "More…" is
  replaced by Advanced.

**Tested.** `setup/basicSets.test.ts` (field lists per kind and physics), `setup/guidedParity.test.ts` (kinds,
inline values, roles and material the guided cards use, read from the cards' source, are Basic fields),
`setup/advancedState.test.ts`, `panels/conditionFields.test.ts` (no layers on an inlet; physics families).
Gates: typecheck clean, vitest 157 files / 2112 tests pass, build ok (commit `59e9283`). Not driven in a browser (none was run, as briefed).

**Open.**
- Mesh and Run card controls are not in the parity test yet (S2 adds mesh_settings / solver_settings).
- "Findings open Advanced" is not wired (no findings on these pages; S2 for settings pages).
- The Conditions panel (`ConditionPanel.tsx`, hidden by default) still lists `moreFields` without the physics filter.
- Body page loses the "what it is meshed as" line from the first screen (now in Advanced); say if it should return.
- Browser check on the real server: inlet/outlet/wall pages, layers only on a wall, Advanced survives reload.

## 7. S2 done (mesh and solver settings), 2026-11-01

**Built.** (commit `3152ebb`)
- `panels/MeshBasic.tsx` + `setup/meshBasic.ts`: Coarse / Medium / Fine (radio group, the cell size in the tooltip and
  one muted line with the `mesh.estimate` cell range), and the Boundary layers switch (0 or `SWITCH_LAYERS`, drawn only
  with snappyHexMesh). Same command (`configureMesh`) and sizes (`guided/sizing.ts`) as the Mesh card.
- `SettingsNodeFields` takes `layout="basic"`: Basic content, then the closed Advanced disclosure (`BasicAdvanced`) with
  today's `SettingsFields` unchanged. Mesh Basic = `base_size` + `layers` (the exact fields also stay in Advanced, since
  the chips are presets of them); solver Basic = Flow model (`turbulence`, relabelled) + `iterations`. `flat` stays the
  default (the Properties page still uses it).
- `settingsPresentation.ts`: `BASIC_MESH`, `BASIC_SOLVER`, a real `solverSettingIncluded(name, ctx)` (delta_t, end_time
  need transient; the four `initial_*` need turbulence ≠ laminar; density_model, molar_mass need energy; unknown names
  stay visible), sections Transient and Initial turbulence (`solver-initial`), `MESH_TASK_FIELDS`, `findingsTouch`.
  `basicSets.ts` gains `BASIC.mesh_settings` / `BASIC.solver_settings`, `setupContextOf`, `advancedNames`.
- Duplicates: the stepper foot (Generate + reason + progress) hides when the Mesh panel is the page (`MeshStepper foot`);
  the stale banner lost its Regenerate button (the one Generate reads "Regenerate" when stale); a Mesh task page
  (Sizing, Surface, Layers) leaves its own fields out of Advanced, so the base size shows once under Sizing. Stepper
  heading is "Mesh steps". Notes shortened with the sentence in a tooltip: engine line ("OpenFOAM · host"), banners,
  residual placeholder ("No run yet"), empty-settings lines, patch count.
- Solve landing: `RunPanel` treats section `solver-model` as the whole page (Run, residuals, Basic settings); any other
  `solver-*` section is settings only, Advanced open at it. A `solve.check` finding that names the settings node opens
  Advanced.
- **Initialisation: kept, made to open Initial turbulence** (`DetailsPanel` maps a row id ending `:initialisation` to
  `solver-initial`; with a laminar run the section is absent and the page falls back to Model). Methods stays the landing.
  S3 should move the mapping into `openTargetOf` or fold the row (Solve rows become Settings + Runs).

**Count.** Mesh page before a mesh, Advanced closed, stepper rows aside: Coarse, Medium, Fine, layers switch, Generate,
Advanced, Log = 7 (≤ 8). Solve Basic: Flow model, Iterations, Run (+ Advanced, Log).

**Tested.** `settingsPresentation.test.ts` (solver include rules, Basic names exist in the server's own schema, read from
`stokes/**/model.py`), `basicSets.test.ts` (mesh and solver field levels), `meshPanelModel.test.ts` (fineness ↔ base size
round trip, layers switch), `SettingsNodeFields.test.ts` (Basic layout, hidden fields, `hideInAdvanced`),
`guidedParity.test.ts` (Mesh card writes `base_size`, `layers`; Run card writes `iterations`; both are Basic).
Gates on the working tree: typecheck clean, vitest 157 files / 2147 tests, build ok. Not driven in a browser.

**Commit note.** Several of these files already carried uncommitted settings-page work (SettingsFields category/search
tools, section descriptions, `RunPanel` `section`); that work is in `3152ebb` whole, because the Basic/Advanced pages call
it. `DetailsPanel.tsx` was committed with only the S2 hunks (its `useDetailsRow` hunk, which needs the untracked
`detailsRow.ts`, is still uncommitted). A clean checkout of `3152ebb` typechecks; `npm run build` there fails on the
untracked `StartScreen.css`, as `HEAD` already did.

**Open.**
- Tool bar inside Advanced (category select, search, Show all) and the section description `<p>`s are unchanged: S3.
- `gravity` and `operating_pressure` still show with energy off (their schema doc says they are used with energy only);
  `molar_mass` shows with energy even when the density model is constant.
- `tools/test_interactions.py` line 3192 now presses the page's Generate (the banner button is gone); the Python UI
  tests were not run.
- Browser check on the real server: Mesh page controls, Advanced survives reload, Methods / Controls / Initialisation rows.

## 8. S3 done (outline and notes sweep), 2026-11-01

**Built.** (commit `42cf7c1`)
- `panels/outlineModel.ts`: an `advanced` flag per row (`true`, or `"whenEmpty"`), `buildOutline` / `outlineRowsFor` take
  `showAll` (default true, so every lookup by id sees the whole outline). Basic-hidden: Capping, Mesher, Operations and
  Parameters while empty, Reference values (already omitted: its page names no panel), the Mesh task rows Boundaries and
  Fidelity (`ADVANCED_LEAF_IDS`), and the Solve rows Controls, Initialisation, Run. Solve reads **Settings + Runs** (`methods`
  relabelled). `OutlineProjection.folded` lists what Basic left out.
- `panels/outlineMode.ts`: the choice, kept per browser in `localStorage` `stokes.tree.showAll` (default Basic). `TreePanel`
  has a "Show all rows" toggle button beside "+" (a header button rather than an item in a menu: no tree menu exists);
  the reveal and stage-focus code pass the same flag.
- Initialisation: the mapping is in `openTargetOf` (`:initialisation` opens `solver-initial`); the `DetailsPanel` special case is gone.
- Details "Next step" card: the stale "press the chip" line is gone. The card shows the step's label (or the stage's few words
  plus "Next: <step>"); the sentence, with a pointer to the flow strip, is the tooltip (`STAGE_WORDS` in `stageLead.ts`).
- `ConditionsPanel`: **Fix** runs the finding's own command (`findingFix`: `region.set`, `tag.rematch`); a finding whose fix is a
  page step (`condition.add`, `condition.edit`, `material.assign`) has no button. Set-up flow list, "no regions" and the
  "why" line are a few words with tips (`firstClause`).
- `components/Note.tsx` (+css): the guided `Note` moved here, `guided/controls.tsx` re-exports it. Every literal note over 8 words
  in `panels/**` is a few words with the sentence as `title` / `aria-description`: seven mesh task pages, folder pages
  (`folderPurpose.ts`), Properties, body mesh card, Results (probe, slices, streamlines, iso, report, arrows, residual and monitor
  placeholders), Explore empties (`Empty tip=`), job log, solid note ("Solid · not meshed"), mesher/engine reasons, TreeGuide.
- `SettingsFields`: section descriptions are the summary's tooltip; `tools="search"` (used inside Advanced) keeps only the
  search and lists every section, the category select and "Show all sections" stay on the flat layout.
- `userText.test.ts` rule 5: literal JSX text over 8 words in `panels/**` fails, `role="alert"` exempt. It walks each opening
  tag with a brace and quote counter (a `>` in a prop expression or a generic is not a tag) and has a mutation case for each shape.

**Count.** Pipe (4 bodies, 3 tags, water, one region, 3 boundaries), Basic, all rows open: 24 set-up rows plus the four section
lines (28 lines); Show all restores the rest. A fourth tag would make 25: Tags stays visible because tags are what Assign works on.

**Tested.** `outlineModel.test.ts` (flags, Basic and full projections, ≤ 24, ids unique in both, Solve sections),
`outlineMode.test.ts`, `Note.test.ts` (Note, folder purpose), `setupFlowModel.test.ts` (`findingFix`, `firstClause`),
`SettingsNodeFields.test.ts` (Advanced has search only), `stageLead.test.ts`. Gates on the working tree: typecheck clean, vitest
159 files / 2171 tests, build ok. A clean checkout of `42cf7c1` typechecks and passes 157 files / 2166 tests. Not driven in a browser.

**Commit note.** `stageTreeModel.ts` (+ its test) carried the uncommitted Solve section routing in `openTargetOf` that the
Initialisation mapping extends; it is in `42cf7c1` whole. `DetailsPanel.tsx` and `TreePanel.tsx` carried unrelated hunks
(`useDetailsRow` with the untracked `detailsRow.ts`; `TreeGuide` with the untracked `TreeGuide.tsx`): committed without them,
they stay in the working tree. `TreeGuide.tsx` (untracked) was edited to short notes so rule 5 passes; it is not committed.

**Open.**
- `gravity` and `operating_pressure` still show with energy off; `molar_mass` with a constant density model (S2's note, not done).
- Not literal, so not seen by rule 5: sentences that reach the screen through `{variable}` (server findings, `reason` lines in
  MeshStepper, readouts). Section descriptions and engine/mesher reasons were moved by hand; the rest is unaudited.
- `app/**` dialogs (Settings, Hosts, Start screen) have long literal sentences; the rule covers `panels/**` only.
- Tags stay in Basic (see Count). The §3.5 "More…" popover is not built.
- Browser check on the real server: the Show all rows button, Solve rows, Fix on a `region.set` finding, tooltips.

## 9. Driven in the browser (2026-11-01, 22:05, pipe3 in Full mode)

S1: the inlet page is Condition + Velocity with Tag, Direction and Mesh size under a closed Advanced;
the wall page is Condition only, Layers inside Advanced. S2: the Mesh page's settings are
Coarse/Medium/Fine with the expected cell range, a Boundary layers switch and Advanced; the Solve page
is Run, residuals, result, Flow model + Iterations and Advanced. S3: the tree opens on the basic rows
(Physics: Materials, Regions, Boundary conditions), "Show all rows" brings Parameters and the rest
back, the Next-step card is a few words. Still open from the S3 note: `gravity`/`operating_pressure`/
`molar_mass` with energy off, the "More…" popover (§3.5), long sentences in `app/**` dialogs, and
sentences that reach the screen through variables.
