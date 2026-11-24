# ADR-0023 · Runs, comparison, set-up modules and application templates

**Status:** accepted · 2026-10-05 · relies on ADR-0006 (run bundles), ADR-0010 (API first),
ADR-0011 (engines), ADR-0012 (quantities and expressions), ADR-0015 (the workspace registry),
ADR-0016 (studies, design tables and Pareto), ADR-0017 (shape optimisation), ADR-0018 (materials)
and ADR-0022 (analysis set-up; this ADR adds two condition kinds and a field-map node). Owner
review requested on the assumptions in `docs/00-open-questions.md` S3.

## Context

The owner read `.mailbox/plan/research-toffeex.md`, a study of a thermo-fluid generative-design
product, and decided on 2026-10-05: **add all of its ideas to the plan; build the non-optimisation
ones now; keep the shape-optimisation ones for later, but design the GUI from the beginning so the
later features slot in without rework.** Build on the app and style that exist (`web/DESIGN.md` is
the look contract; add, do not restyle).

The transferable ideas are a Toolbox of named, versioned set-up modules; a Runs panel with
auto-named sweeps, study folders, a queue and parallel slots; a Compare view with a metrics table
and a Pareto plot; a free-slip wall and non-uniform thermal boundary conditions from a map; guided
application templates; shareable material libraries; and explainability (objective weights,
constraint values, convergence history, why a run stopped). The shape-optimisation ideas are a
seed-design deviation limit, patterning and periodicity, manufacturing constraints during
optimisation, shape-evolution playback, a field viewer with free slicing, and CAD plug-in round
trips.

What Stokes already has: studies as nodes with a design table kept beside the project
(ADR-0016), the `function` engine so the table, samplers and plots run without a solver, Pareto
utilities (`stokes.explore.pareto`), immutable run bundles (ADR-0006), the condition catalogue and
the set-up check (ADR-0022), materials with export and import (ADR-0018), and a panel registry
with schema-driven forms (`SchemaFields`, T-041). `stokes/run/` is an empty package; `/runs` is
already a tree group name in the workspace defaults but no run is modelled.

The constraints that shape this ADR: runs are jobs and must not be undoable (ADR-0016 decision 9),
so a run cannot be a mutable tree node; the look is frozen; every numeric value is a quantity
(constitution rule 6); the case is a pure function of the model (rule 9); heavy work leaves the UI
thread (rule 7); a free, weaker coding model builds most of this, so the pieces are cut small and
tested; and M3 owns the real solver, so the runs layer must be generic enough that M3 attaches an
executor without touching the panel.

## Decision

### 1. Runs are records, not nodes, and the runs layer is generic over run kinds

A new `stokes.run` package holds a `RunStore` (one per session, `session.extensions["run"]`,
beside `StudyStore`) with one **run record** per run:

```
{id, kind, name, state, stale, engine, study?, base?, parent?, inputs{}, outputs{}, objectives{},
 constraints{}, stop?, history[], timings{}, bundle?, module_versions{}, error?, created, updated}
```

`kind` is `simulation` (one case, one point), `sweep` (one point of a study's sweep) or `optimisation`
(reserved for ADR-0017; its record carries the best point and the generation history). A run
record is **not an undoable node**: its state is written by the queue, which would conflict with
the undo model. It is addressable by id, kept under `<name>.stokes/runs/<run_id>/record.json`,
and exposed only through `run.*` commands. The Runs panel and the Compare view read `run.list`
and are generic over `kind`: a later optimisation run is data plus the same rows, not a new
panel. Study folders in the Runs panel are the existing `study` nodes under `/studies`; a
standalone simulation run sits at the top level of the panel. The tree keeps `/runs` as a stage
mapping (Solve and Results) and may later show derived rows; the panel is the authoritative list.

### 2. The run queue and the M3 plug-in point

The `RunStore` owns a queue with a configurable number of **parallel slots**
(`run.slots`, default `max(1, cpu_count - 1)`, capped by a setting). Commands `run.enqueue`,
`run.cancel`, `run.retry`, `run.reorder` and `run.slots`; events `run.queued`, `run.started`,
`run.progress`, `run.finished`. An executor is chosen through one protocol:

```
RunExecutor: name; available; kinds; start(run, case) -> job; cancel(job); parse(job) -> events
```

Until M3 the registered executor is the **function engine** (ADR-0016 decision 8), so the queue,
the panel and the comparison are built and tested now with no solver. M3 registers the `foamRun`
executor through the same registry the engine adapters use (ADR-0011), and the Runs panel needs
no change. The compile step and the run bundle stay as ADR-0006; a run record points at its
bundle. A run that a solver cannot start fails with a stable code and a sentence, never a
traceback.

### 3. Auto-named runs

A run's name is derived from its inputs: the parameter's bare name, `=`, and the value in the
project's display unit without its unit (`U_in=2.0`, `nu=1e-5`); several inputs join with `, `
(`U_in=2.0, nu=1e-5`). A standalone simulation run is named after its case when it has no varied
input. A name the user gives with `run.rename` is kept. Names are made unique among the sibling
runs with a numeric suffix. The name is data on the record, not a node name, so it never has to
satisfy the node-name rule and can carry the `=`.

### 4. Branch a sweep from a finished run

`run.branch(run, variables?, overrides?)` makes a new study whose base case is the finished run's
case, seeded with that run's point, optionally sweeping chosen variables; the new run records
`parent`. This is the researched product's "branch from a completed run" with Stokes' study model:
the branch is an ordinary `study` node plus a `sweep` run record, so the Design table, Plots and
Compare view work on it unchanged.

### 5. The Compare view: a metrics table and a Pareto plot over any responses

`run.compare(runs, responses?)` is a pure query returning a **metrics table** (one row per run;
columns: the inputs, the responses, the state, the stop reason and the timing) and the data for a
**Pareto plot over any two responses** (not only declared objectives), computed with
`stokes.explore.pareto`. The Compare view draws the table and the plot side by side with the runs
selection synchronised with the Runs panel and the viewport. A sweep contributes its points, an
optimisation run its history; a simulation run is one point. `run.compare` reuses the study's
`pareto` machinery, so no second Pareto implementation exists.

### 6. Set-up modules: named, versioned snapshots of one facet

A new skeleton group `/modules` holds `module` nodes. A module is a named, reusable, **versioned**
bundle of one facet of the set-up, `kind` = `conditions` | `materials` | `solver` | `mesh`. Its
parameters are `kind`, `version` (int, starts at 1), `description` and `source`
(`project` | `library` | `file`). Its **snapshot** is not in the tree: it is a file under
`<name>.stokes/modules/<module_id>/v<version>.json` holding the owned nodes by stable path, with
their parameter values (a quantity stays SI plus its expression). `module.create` captures the
current facet from the project; `module.list` and `module.get` read modules and a chosen version;
`module.save_version` bumps the version and keeps the old snapshot, so a run that named v2 stays
reproducible after v3 exists. This is the Toolbox idea mapped onto Stokes' tree: a module is a
snapshot with a version, not a second tree. A run records the module versions it used
(`module_versions`).

### 7. Applying a module

`module.apply(module, version?, target?, keep?)` swaps a module's facet into the project (or a
named case) in **one undo step**, planned like ADR-0022's `setup_plan.py`: it names what it
replaces, keeps the target's identity where the module allows (`keep`), and refuses a snapshot
that does not fit the project (a module of the wrong kind, a reference to a node that is gone) with
a stable code and a fix. Applying a module never silently discards hand edits: the plan reports
every node it removes or re-values, and undo restores the whole set-up. `module.export` and
`module.import` move a `.stokesmod` file (JSON, versioned, with the snapshot and its citations).
A module applied to a run that is queued marks the run stale, not failed.

### 8. A free-slip wall kind and a map-driven thermal wall

The condition catalogue (ADR-0022 decision 2.3) gains two entries, both available now:

- **`slip_wall`** — family `wall`, no parameters, colour `#7f8c99`, glyph `none`. It is a wall
  with zero shear, distinct from `wall` with `shear: slip` so it is discoverable in the value
  panel and the templates; the compiler maps both to the same `slip` patch. The `wall` kind keeps
  its `shear` switch unchanged, so existing projects and expressions do not move.
- The **`wall`** kind's `thermal` enum gains **`map`**, using a new `map` parameter (ref
  `field_map`) and `map_field` (enum `temperature` | `heat_flux`). This is the researched
  product's non-uniform thermal boundary condition driven by an STL surface map or a CSV.

A new node type **`field_map`** under `/physics/maps` holds a source file (an STL surface map, or a
CSV of `x, y, z, value`), the `field` and its `dimension`, the file's `units`, a `coordinate` basis
and the `tolerance` used to associate a face with a sample. `field_map.import` reads and validates
the file (in a worker for a large map, with progress and a size cap), `field_map.list`, `get` and
`delete` complete it. The map is data: it is stored in the project and referenced by id, so a case
compiles without the original file. A wall that uses a map whose node is gone, or whose field does
not match `map_field`, is an error (`setup.map_missing`, `setup.map_field`) and a warning while the
project is incomplete. M3's compiler samples the map onto the patch faces and writes the boundary
field; until then the check reports the map as set-up information, exactly as `setup.needs_physics`
does.

### 9. Application templates: cold plate, heat sink, manifold, heat exchanger

Templates are **data**, one JSON file per application under `stokes/physics/templates/` (no code
per template): each names the tag names it expects, the conditions to assign, the material to use,
a default study (variables, responses, objectives, constraints from the catalogues of decision 10),
and notes. `template.list`, `template.get` and `template.apply(target, template)` are the API;
`apply` turns a prepared body into an application set-up in one undo step, reporting what it made
and what it left (a tag it expected that is missing, a condition the target already has). Adding a
fifth template is one JSON file plus a test that it validates, not a new panel. Templates pre-fill
tags, conditions and a study, which is what the researched product's guided set-ups do.

### 10. Catalogue-driven objectives and constraints, and explainability

Objectives and constraints become **declarative catalogues** like conditions. A new module
`stokes.explore.catalogues` holds:

- **objective kinds** — `minimise` and `maximise` a response, `uniform_flow`, `thermal_resistance`,
  `pressure_loss`, `heat_transfer` and `mass`; each spec names the responses or the expression it
  expands to, the parameters it uses and their rules, and its label;
- **constraint kinds** — `bound` (an inequality over responses, today's `constraint`), `weight`,
  `volume_fraction`, and later the manufacturing kinds of decision 12; each with its parameters
  and the response expression it expands to.

`objective.kinds` and `constraint.kinds` return the catalogues with each parameter's JSON schema,
as `boundary.kinds` does. `study.set_objective` and `study.add_constraint` take a catalogue `kind`
plus its values; a raw `expression` stays allowed (today's form is the `bound` kind). An
`objective` node gains a `weight` (positive, default 1) and a `kind`; the existing `response` +
`direction` fields stay, so old studies keep working. Weights enter `study.optimise` as the weighted
sum of the researched product.

**Explainability** is a feature, not a debug view. `run.explain(run)` (and the same block inside
`study.results`) returns, for a run or a study: the objective weights, each constraint's value and
its scaled violation, the convergence history (the per-generation objective values `study.optimise`
already records), and the **stop reason** as a stable code plus a sentence (`converged`,
`max_iterations`, `time_limit`, `user_cancelled`, `constraint_infeasible`, `solver_failed`). The
Study card, the Runs panel and the readiness readout show the same data, so a user can see why a
run stopped and how a point was judged without opening a log.

### 11. Every new form is schema-driven

Every form this ADR adds (module apply, template apply, map pick, objective and constraint from the
catalogue, queue settings) is rendered by `SchemaFields` (T-041) from the command schema or the
catalogue JSON, with the `when` filter, the ref select and the array(3) field that component
already has. No bespoke field loop is added. A new catalogue row or template is data plus a panel.

### 12. What is later, and the slots designed now (shape optimisation)

The following are **designed here but built later**; each is a brief whose board status is
`later (shape optimisation)`:

- **Seed-design deviation limits** — a `seed` constraint kind in the constraint catalogue, taking a
  reference geometry and a maximum deviation; enforced by the ADR-0017 driver.
- **Patterning and periodicity** — a `pattern` parameter group (grid or radial, counts, spacing,
  axis, symmetry) on the geometry/operation, plus `symmetry`/`periodicity` constraint kinds.
- **Manufacturing constraints during optimisation** — `minimum_feature`, `overhang`, `bit_size`,
  `channel_clearance` and `wall_thickness` constraint kinds in the same catalogue, each with a
  driver that enforces it.
- **Shape-evolution playback** — the run record's `history` holds one geometry frame per
  generation (a bundle path per frame); a playback panel scrubs it. The field is in the record from
  day one, so the later panel is a consumer.
- **A field viewer with free slicing** — a `post.slice` viewport mode over the existing field
  data; it slots into the viewport, not into the Runs panel.
- **Plug-in round trips with external CAD** — a `plugin.*` command family with a manifest
  (name, version, capabilities, geometry exchange), registering panels and actions through the
  ADR-0015 registry. The Runs panel and Compare view stay generic, so a plug-in adds rows and
  buttons, not a second workflow.

The extension points are named so that a later feature is **data plus a panel**: catalogue rows for
objectives, constraints and conditions; a run-record field for history; a registry entry for a
panel; and a `SchemaFields`-rendered command for its form.

### 13. Contracts, storage and tests

New contracts: `docs/engineering/run-schema.md` (run records, queue, executors, `run.*` commands
and events), `docs/engineering/module-schema.md` (`module` nodes, snapshots, `.stokesmod`),
`docs/engineering/templates-schema.md` (the template files and `template.*`), `docs/engineering/map-schema.md`
(`field_map` nodes and the `thermal: map` wall), and additions to `docs/engineering/study-schema.md`
(the objective and constraint catalogues, weights, stop reasons) and `docs/engineering/setup-schema.md`
(the two new condition kinds). Storage: run records under `<name>.stokes/runs/<run_id>/record.json`,
module snapshots under `<name>.stokes/modules/<module_id>/v<N>.json`; both load lazily and are
carried on Save as, as the study tables are. Tests: naming and uniqueness, queue order and parallel
slots, cancel and retry, branch and parent link, `run.compare` against the demo study, module
snapshot and version round trips, `module.apply` planning and undo, `field_map.import` validation
and refusal, the two catalogue kinds through `Session.execute` and replay, template validation and
apply, library export/import conflict policy, the objective and constraint catalogues, `run.explain`
and the stop reasons, and `import stokes.api` under its budget with the new command modules.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Runs as undoable tree nodes | A run's state is written by the queue, and the queue is not undoable (ADR-0016 decision 9); a node would make every state change an undo step and bloat the tree with thousands of runs. |
| A second tree for runs | The owner keeps one tree; the Runs panel is a stage view beside it, like the Conditions list (ADR-0022 decision 9.2). |
| Build the queue only after M3 | The `function` engine (ADR-0016 decision 8) lets the queue, naming, branch, panel and comparison be built and tested now; only the executor attaches in M3. |
| A Toolbox that owns a second set-up tree | Duplicates the tree and drifts from it; a module as a versioned snapshot of owned nodes is reproducible and small. |
| Modules that store only a path | A path breaks when the project moves or a node is renamed; a snapshot by stable path with its values is self-contained. |
| A free-slip wall as only `wall` with `shear: slip` | Works, but hides the common case; a named kind is discoverable and templates can pre-fill it. The `wall` switch stays, so nothing is removed. |
| A non-uniform thermal condition as a coded field | A coded `Function1` links GPL OpenFOAM code to the case and stops the case being a pure function of the model (ADR-0018 rejected the same for materials); a map node is data. |
| A template as code | A fifth template would be a build; data files make it one JSON file and a validation test. |
| Objective and constraint kinds hard-coded in the study card | The panels would grow a branch per kind; a catalogue drives the forms and the compiler, as conditions do. |
| A separate comparison tool per run kind | The brief requires the Runs panel and Compare view to be generic; one record shape and one `run.compare` cover simulation, sweep and optimisation. |
| A bespoke form per new command | `SchemaFields` (T-041) already renders every parameter kind; a second field loop would drift. |

## Consequences

- New or changed modules: `stokes/run/` (`records.py`, `store.py`, `queue.py`, `executors.py`,
  `compare.py`, `explain.py`, `__init__.py`), `stokes/api/commands/run.py` and `module.py` and
  `template.py`, `stokes/api/commands/study.py` (catalogue kinds, weights, explain), `stokes/physics/`
  (`templates/`, `maps.py` and the `field_map` node type in `stokes/geometry/model.py`), the two new
  catalogue rows in `stokes/physics/conditions/catalogue.py`, `stokes/explore/catalogues.py`,
  `stokes/api/commands/material.py` (`library.export`/`library.import`), the CLI verbs `stokes run`,
  `stokes module`, `stokes template`, `stokes map`; the front end `web/src/panels/runs/`,
  `web/src/panels/compare/`, `web/src/panels/modules/`, the template and map pickers, the Study card
  explainability block and the readiness readout stop reason.
- Contracts: `run-schema.md`, `module-schema.md`, `templates-schema.md`, `map-schema.md` (new);
  `study-schema.md`, `setup-schema.md`, `workspace-schema.md`, `materials-schema.md`,
  `web/DESIGN.md` and `docs/03-ui-design.md` (additions); the ADR register gains this row.
- Roadmap: the Runs panel, queue, Compare view, set-up modules, templates, library sharing, the two
  condition kinds and explainability are added to the milestones that own their servers and panels;
  the shape-optimisation items stay with M3.5 and M8.5 (ADR-0017). M3's executor attaches through
  the `RunExecutor` protocol with no panel change.
- Budgets as tests or probe runs: `import stokes.api` under 300 ms with the new command modules;
  `run.list` and `run.compare` timed on a 5,000-point study (recorded, not a budget); the Runs panel
  and Compare view virtualise their rows like the tree (ADR-0019).
- Risks: the queue is built before a real solver, so the executor protocol may need an amendment in
  M3 (mitigated by keeping it to the six members above); module snapshots can go stale against a
  changed tree (the plan refuses a snapshot that does not fit, and `module.apply` reports every
  change); a free model on the UI tasks (mitigated by the small, tested server pieces first).

## Owner decisions (2026-10-05, assumed until confirmed)

The ADR proceeds on these assumptions, listed as S3 in `docs/00-open-questions.md`: runs are
records, not nodes; a run is named `parameter=value` in display units; the default parallel slots
are `cpu_count - 1`; modules are snapshots of one facet, versioned, with `.stokesmod` files;
templates ship as data for cold plate, heat sink, manifold and heat exchanger; the material library
import conflict policy defaults to keeping both with a rename; `run.compare` offers a Pareto plot
over any two responses, not only declared objectives; the shape-optimisation items are briefs whose
board status is `later (shape optimisation)`.
