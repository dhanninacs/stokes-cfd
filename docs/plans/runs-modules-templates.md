# Runs, comparison, set-up modules and templates: the task plan for ADR-0023

Decisions: `docs/adr/ADR-0023-runs-modules-templates.md` (D-numbers below are its sections).
Briefs: `.mailbox/tasks/T-NNN-*.md`. Owner **oc** = opencode (a free model, so its briefs are
small, precise and name a test); **C** = Claude; **F** = Fable (design and review). Sizes: S under
an hour, M a few hours, L a day or more. Server first, then the UI layers; the order is the
execution order for opencode. Claude builds the cores that are easy to get subtly wrong and
reviews.

## Before anything

- T-062 (this design) is the ADR, this plan and the briefs T-063 to T-087.
- The runs layer is built on the `function` engine (ADR-0016 decision 8); it needs no solver, so it
  can start before M3. M3 attaches a real executor through `RunExecutor` (D2) with no panel change.
- The UI briefs depend on T-041 (`SchemaFields`) and T-044/T-045 (the value panel and the material
  pick), which are in flight. They are sequenced after their dependencies in the board rows.
- `docs/02-architecture.md` has the ADR register row; `docs/00-open-questions.md` gains S3.

## Order

| # | ID | Task | Owner | Size | Depends on | ADR |
|---|---|---|---|---|---|---|
| 1 | T-063 | Run records, kinds, naming, study folders; `run.list`/`run.get` | oc | M | main | D1, D3 |
| 2 | T-064 | Run queue: parallel slots, `run.enqueue`/`cancel`/`retry`, events, the function executor | oc | M | T-063 | D2 |
| 3 | T-065 | `run.branch` and `run.explain`: stop reasons and convergence history | oc | M | T-063, T-064 | D4, D10 |
| 4 | T-066 | Set-up modules: `module` node type, version, snapshot, `module.create`/`list`/`get` | oc | M | main | D6 |
| 5 | T-067 | `module.apply`, module export/import (`.stokesmod`) | oc | M | T-066, T-064 | D7 |
| 6 | T-068 | Free-slip wall kind `slip_wall` in the condition catalogue | oc | S | main | D8 |
| 7 | T-069 | `field_map` node, `field_map.import`, the `thermal: map` wall | oc | M | main | D8 |
| 8 | T-070 | Objective and constraint catalogues; weights; `objective.kinds`/`constraint.kinds` | oc | M | main | D10 |
| 9 | T-071 | Application templates as data; `template.list`/`get`/`apply` | oc | M | T-068, T-070 | D9 |
| 10 | T-072 | Material library export/import and the organisation folder | oc | M | main | D9 |
| 11 | T-073 | Runs panel: runs, study folders, rows, status | oc | M | T-063, T-041 | D1, D3 |
| 12 | T-074 | Run queue UI: slots, enqueue/cancel/retry/branch | oc | M | T-064, T-065, T-073 | D2, D4 |
| 13 | T-075 | `run.compare`; Compare view: side-by-side runs and the metrics table | oc | M | T-063, T-073 | D5 |
| 14 | T-076 | Compare Pareto plot over any responses | oc | S | T-075 | D5 |
| 15 | T-077 | Set-up modules UI: module list, apply, snapshot badge | oc | M | T-067, T-041 | D6, D7, D11 |
| 16 | T-078 | Value panel: the `slip_wall` kind and the map thermal picker | oc | M | T-068, T-069, T-041, T-044 | D8, D11 |
| 17 | T-079 | Template chooser UI | oc | M | T-071, T-041 | D9, D11 |
| 18 | T-080 | Material library sharing UI | oc | S | T-072 | D9 |
| 19 | T-081 | Study card, Runs panel and readiness readout explainability | oc | M | T-065, T-070, T-074, T-049 | D10 |
| 20 | T-082 | Seed-design deviation limits (later) | oc | M | T-070 | D12 |
| 21 | T-083 | Patterning and periodicity (later) | oc | M | T-070 | D12 |
| 22 | T-084 | Manufacturing constraints during optimisation (later) | oc | M | T-070, T-082 | D12 |
| 23 | T-085 | Shape-evolution playback (later) | oc | M | T-063, T-065 | D12 |
| 24 | T-086 | Field viewer with free slicing (later) | C | L | T-075, ADR-0022 15 | D12 |
| 25 | T-087 | External CAD plug-in round trips (later) | C | L | T-073, ADR-0015 | D12 |

Rows 20 to 25 are the shape-optimisation items of D12; their board status is
`later (shape optimisation)`.

Two opencode tasks may run at once only when their briefs own disjoint files: T-063 with T-066,
T-064 with T-068, T-069 with T-070, T-071 with T-072, T-077 with T-078.

### Hot files, in order

| File | Order |
|---|---|
| `stokes/run/` | T-063 → T-064 → T-065 → T-067 (the module-version stamp) → T-075 (`run.compare`) |
| `stokes/api/commands/__init__.py` | every server task that adds a command module: one at a time |
| `stokes/geometry/model.py` | T-068 (kind vocabulary) → T-069 (`field_map` node type) |
| `stokes/physics/conditions/catalogue.py` | T-068 → T-069 (one at a time) |
| `stokes/explore/model.py`, `study.py` | T-070 only |
| `stokes/api/commands/material.py` | T-072 only |
| `web/src/workspace/registry.tsx`, `defaults.ts`, `stokes/workspace/defaults.py`, `workspace-schema.md` | every UI task that registers a panel or action: one at a time, in order T-073 → T-075 → T-077 → T-078 → T-079 → T-080 → T-081 |
| `web/src/app/ContextToolbar.tsx`, `Ribbon.tsx` | T-078 (value panel) and T-079 (template entry) after T-044/T-045/T-050 |
| `web/src/panels/PropertiesPanel.tsx`, `explore/StudyCard.tsx` | T-081 (after T-041) |
| `web/src/panels/runs/`, `web/src/panels/compare/` | T-073 → T-074 → T-081 (the stop line) and T-075/T-076; disjoint files |

## What changed in earlier tasks

| Task | Change |
|---|---|
| T-041 | `SchemaFields` is the renderer for every new form here (D11); no change to its contract. |
| T-048 | The Conditions panel gains the `slip_wall` row and the map thermal value (T-078). |
| T-049 | The readiness readout gains the stop reason and the explainability line (T-081). |
| T-063 | The `/runs` tree group stays a stage mapping; the Runs panel is the authoritative list (D1). |

## Where the owner can try something new

| After | What to try |
|---|---|
| T-063, T-064 | CLI: `stokes run enqueue /studies/velocity_sweep`; `stokes run list` shows `U_in=1.0`, `U_in=2.0`, `U_in=3.0` in a study folder and their states. |
| T-065 | `stokes run explain <run>` says why a run stopped and lists the objective values per generation. |
| T-066, T-067 | Capture the current conditions as a module, edit them, apply the module back; undo restores the edit. |
| T-068, T-078 | Pick faces, Condition ▾, Free-slip wall; the faces turn grey and the check stays ready. |
| T-069, T-078 | Import a CSV map, set a wall's thermal to Map, choose the map; the check names the map field. |
| T-070 | Set an objective from the catalogue with a weight, and a volume-fraction constraint; the Study card shows both. |
| T-071, T-079 | New from template ▸ Cold plate on a prepared body: tags, conditions and a study appear in one undo step. |
| T-072, T-080 | Export a library, import it on another project, or point the organisation folder at a shared drive. |
| T-073 to T-076 | The Runs panel lists the demo sweep; Compare shows the three points in a table and a Pareto plot over `dp_dyn` and `Q`. |
| T-081 | The Study card shows the weights and the constraint violation; the drawer strip says why the last run stopped. |

## Decisions and risks that need the owner

1. **Runs are records, not nodes** (D1): the Runs panel lists them, the tree keeps `/runs` as a
   stage mapping. Say if you want run rows in the tree instead.
2. **Auto-naming** (D3): `U_in=2.0` in the project's display unit, several inputs joined with `, `.
   Say if the unit should be kept or the parameter path used.
3. **Parallel slots** (D2): default `cpu_count - 1`. Say if the default should be lower on a
   laptop.
4. **Modules** (D6, D7): one facet per module, versioned snapshots, `.stokesmod` files. Say if a
   module should span several facets (a whole Toolbox) or stay one facet.
5. **Templates** (D9): cold plate, heat sink, manifold and heat exchanger as data. Say which tag
   names and default materials each should assume.
6. **Library sharing** (D9): import keeps both and renames on conflict. Say if skip or replace
   should be the default.
7. **Explainability placement** (D10): the Study card, the Runs panel and the readiness readout.
   Say if the objective weights belong in the Design table instead.
8. **The free worker on the UI tasks**: T-073 to T-081 touch hot files in sequence; expect more
   review rounds, and say if a task should move to Claude when its first report misses.

## The later shape-optimisation briefs (D12)

| Order | ID | Deliverable | Depends on | Owner | Hot files |
|---|---|---|---|---|---|
| 1 | T-082 | Seed-design deviation limits: a `seed` constraint kind and its driver | T-070, ADR-0017 route A | oc | explore catalogue, constraint command |
| 2 | T-083 | Patterning and periodicity: a `pattern` group and constraint kinds | T-070 | oc | geometry model, catalogue |
| 3 | T-084 | Manufacturing constraints: minimum feature, overhang, bit size, channel clearance, wall thickness | T-070, T-082 | oc | explore catalogue, driver |
| 4 | T-085 | Shape-evolution playback: the run history frames and a playback panel | T-063, T-065 | oc | run store, new panel |
| 5 | T-086 | Field viewer with free slicing: a `post.slice` viewport mode | T-075, ADR-0022 15 | C | viewport, post |
| 6 | T-087 | External CAD plug-in round trips: a `plugin.*` family and manifest | T-073, ADR-0015 | C | plugin package, registry |

Each later brief is data plus a panel: a catalogue row, a run-record field, a registry entry and a
`SchemaFields`-rendered command (D12). They start only after the now items are merged.
