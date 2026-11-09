# Analysis set-up: the task plan for ADR-0022

Decisions: `docs/adr/ADR-0022-analysis-setup.md` (D-numbers below are its sections). Briefs:
`.mailbox/tasks/T-NNN-*.md`. Owner **oc** = opencode (now a free model, Qwen 3.8 27B: weaker than the
paid worker, so its briefs are small, precise and name a test), **C** = Claude (Opus; Fable reviews).
Sizes: S under an hour, M a few hours, L a day or more. Server first, then the UI layers; the order is
the execution order for opencode, and Claude's tasks run beside it where their results are needed.

## Before anything

- The overnight chain (T-020, T-021, T-016, T-013) finishes untouched; everything here builds on
  what it delivers. T-020 owns `store.ts`, `scene.ts`, `Viewport.tsx` and `ContextToolbar.tsx`
  until it is merged: no front-end task below starts before that.
- Claude runs `cd web && npm install -D vitest` in the main copy before T-040 is dispatched (the
  worktree's `node_modules` is a link to the main copy and opencode never installs).
- T-033 is **superseded** by T-035 and T-036 (server) and T-047 (front end); T-022, T-029 and
  T-032 are amended (see "What changed in ADR-0021's tasks").
- `docs/02-architecture.md` has the register row; ADR-0021 2.5 is marked amended.

## Order

| # | ID | Task | Owner | Size | Depends on | ADR |
|---|---|---|---|---|---|---|
| 1 | T-035 | Condition catalogue, `boundary` and `region` node types, `boundary.kinds`, the set-up contract | C | M | main | D2 |
| 2 | T-036 | Assign commands: `boundary.*`, `region.*`, the reference guard, `tag.delete` cascade, `material.list` regions, CLI | C | L | T-035 | D3, D4.1 to 4.3, D2.8 |
| 3 | T-040 | Vitest and the pick helpers module (`state/pick.ts`) | oc | S | T-020 merged | D14 |
| 4 | T-041 | `SchemaFields`: the Properties panel's field loop as one component; `when` filter, ref select, array(3) | oc | M | main | D7.1 |
| 5 | T-037 | `setup.check`: readiness states and findings; `stokes setup check` | oc | M | T-036 | D5 |
| 6 | T-038 | `tag.anchor` and `tag.related` | oc | M | T-020 | D8.1, D6.4 |
| 7 | T-042 | Selection additions: mode switch keeps the pick, click escalation, hover cycling, pick history | oc | M | T-020, T-040 | D6.1 to 6.3, D6.7 |
| 8 | T-043 | Paint select and `pickMany` | oc | M | T-042 | D6.5 |
| 9 | T-039 | `setup.defaults`: default conditions for the two flow templates | oc | S | T-036, T-037 | D10.1 |
| 10 | T-044 | The value panel: Condition ▾, select-then-act and act-then-select, edit from the tree | C | M | T-036, T-041, T-042 | D7, D6.6 |
| 11 | T-045 | Material pick: Material ▾ menu, recent, Browse library…, the Physics task strip | oc | M | T-036, T-044 | D4.4 |
| 12 | T-046 | Condition glyphs: instanced markers off the part layer; the preview overlay | C | M | T-036, T-038, T-020 | D8 |
| 13 | T-047 | Tree rows for `region` and `boundary`: icons, values, proxy swatch and eye, menus, double-click (T-033's front end) | oc | M | T-022, T-036, T-044 | D2.10, D4.5, D7.2 |
| 14 | T-048 | The Conditions panel | oc | M | T-037, T-044, T-046 | D9 |
| 15 | T-049 | The status readout in the drawer strip | oc | S | T-037, T-042, T-048 | D11 |
| 16 | T-051 | Related ▾ on the context toolbar | oc | S | T-038, T-043, T-044, T-046 | D6.4 |
| 17 | T-050 | External flow… and Internal flow… cards | oc | M | T-029, T-039, T-044, T-025, T-026 | D10.2 |
| 18 | T-052 | Docs alignment: 03, 05 §2, 06, DESIGN.md, HANDOVER, CHANGELOG | oc (cheap model) | S | T-049 | consequences |

Claude's T-035 and T-036 start now; T-044 and T-046 as soon as their dependencies merge. Two
opencode tasks may run at once only when their briefs own disjoint files: T-040 with T-041, T-037
with T-038, T-039 with T-042, T-045 with T-047, T-048 with T-051.

### Hot files, in order

| File | Order |
|---|---|
| `web/src/state/store.ts` | T-020 → T-040 (extracts `pick.ts`) → T-042 → T-043 → T-044 → T-046 → T-048 → T-032 |
| `web/src/viewport/scene.ts`, `Viewport.tsx` | T-020 → T-042 → T-043 → T-046 → T-029 → T-032 |
| `web/src/app/ContextToolbar.tsx` | T-020 → T-044 → T-045 → T-051 → T-029 → T-050 |
| `web/src/panels/TreeRow.tsx`, `TreePanel.tsx` | T-022 → T-047 (→ T-029's stale Run entry) |
| `web/src/workspace/Ribbon.tsx` | T-029 (Geometry case), T-044 and T-045 (Physics case), T-050 (Geometry case): sequential |
| `web/src/panels/PropertiesPanel.tsx` | T-041 only |
| `web/src/app/Islands.tsx` | T-043 (paint toggle) → T-032 (lasso toggle) |
| `web/src/app/actions.ts`, `workspace/defaults.ts`, `stokes/workspace/defaults.py`, `workspace-schema.md` | every task that adds an action: one at a time, in the order above |

## What changed in ADR-0021's tasks

| Task | Change |
|---|---|
| T-033 | **Superseded.** The `boundary` type now has a kind and values (D2) and is created by `boundary.assign` (T-036), not `node.add`; the proxy rows, `tag.list` boundaries and the overlap warning move to T-036 and T-047. Its brief says so. |
| T-022 | Rows with controls include `region` and `boundary` by proxy through T-047, not T-033. |
| T-029 | The operation card renders its form with `SchemaFields` (T-041), not `PaletteForm`; the Prepare menu's items stay data-driven so T-050 adds two entries; `requestPoint` is reused by T-050. Depends on T-041 too. |
| T-032 | `pickMany` comes from T-043 (no second bulk path); the lasso toggle sits beside T-043's paint toggle. Depends on T-043 too. |
| T-024 | One line added: journal replay waits for an operation to finish before the next record (D10.1 relies on it). |

## Where the owner can try something new

| After | What to try |
|---|---|
| T-036 | Palette or CLI: `boundary.assign target=/geometry/tags/inlet kind=velocity_inlet values={velocity: "2 [m/s]"}`: one undo step makes the region, the boundaries group and the typed boundary; the tag turns red. `region.assign material=water target=[part ids]`. |
| T-037 | `stokes setup check demo.stokes` says what is missing and what to do next. |
| T-042, T-043 | Alt + wheel cycles faces under the pointer; a triple-click picks the whole body's faces; switching Body/Face keeps the pick; `P` paints faces. |
| **T-044** | **Pick faces, Condition ▾, Velocity inlet, type `2 m/s`, Enter**: the faces turn red, the boundary appears under `/regions/fluid/boundaries`, undo removes it all in one step. From the Physics task strip: Add condition first, then pick the faces. **This is the first point the owner asked for.** |
| T-045 | Pick bodies, Material ▾, pick water (or Browse library…): the region appears with its material. |
| T-046 | Red arrows into the inlet, blue arrows out of the outlet, constant size while orbiting; hover one to see its faces. |
| T-047, T-048, T-049 | Region and boundary rows in the tree with swatch and eye; the Conditions panel with the unassigned faces and the findings; the drawer strip says "Set-up · needs an outlet". |
| T-050 | Prepare ▸ External flow… around a body: enclosure, air, inlet, outlet, far field and walls in one run; Prepare ▸ Internal flow… on a capped pipe: pick the inlet cap, then the outlet cap. |
| T-051 | Pick one bolt hole face, Related ▾ Coaxial: every hole of that size lights; click to add. |

## Decisions and risks that need the owner

1. **`pressure_inlet`** is added to the kinds and will need a row in `docs/05` §2 for M3's
   compiler. Say if the inlet set should stay at velocity and mass flow.
2. **Kind colours** (D2.3): red inlets, blue outlet, grey wall, violet symmetry, orange cyclic,
   green interface, from the tag palette. Assigning recolours the tag (the toast says so;
   `keep_color` keeps a hand-picked colour).
3. **Helper materials**: `air` for external and `water` for internal flow, made from the built-in
   templates when the project has no fluid material (D4.3). Say if the helpers should ask instead.
4. **Chords**: `P` paint, `Alt` + wheel cycling, `selection.previous` unbound.
5. **Readout placement**: the drawer's collapsed strip (D11.1), not the top bar.
6. **Vitest** as the front end's test runner (MIT devDependency; `npm test`), installed by Claude in
   the main copy before T-040.
7. **Statuses derived, not stored** (D2.10): the tree's dot for a boundary follows its tag and the
   check; nothing is written back. Say if you want stored statuses for agents reading the tree.
8. **The free worker on UI tasks**: T-042 to T-051 touch hot files in sequence; expect more review
    rounds than on the Python tasks, and say if a task should move to Claude when its first report
    misses.

## Edge and point picking (ADR-0022 amendment 15; T-054)

T-053 adds disabled Edge and Point rows to the mode pull-up. Keep them disabled until the
topology, tag, pick and highlight paths are tested. This is an M1 addition to the current
selection, not a replacement for Body/Face or an early M3 boundary compiler. Briefs live in
`.mailbox/tasks/T-055-*.md` through `T-060-*.md`; T-054 is the design only.

| Order | ID | Deliverable | Depends on | Owner | Hot files |
|---|---|---|---|---|---|
| 1 | T-055 | B-rep edge/vertex maps, fingerprints and lazy topology cache/endpoint, versioned independently of mesh v2 | T-024 shape provider (or its agreed equivalent) | C | geometry worker, server geometry route |
| 2 | T-056 | Backward-compatible typed tag members, validation, history and rematch; face-only callers unchanged | T-055, T-024 | C | `selection.py`, tag commands |
| 3 | T-057 | Pure pick conversion and screen-space CPU edge/point tests, async topology lifecycle | T-055, T-042, T-043; coordinate with T-032 | C | `store.ts`, `scene.ts` |
| 4 | T-058 | On-demand edge/point hover and selection overlay, orbit probe | T-057; after T-046 scene changes | C | `scene.ts`, viewport highlights |
| 5 | T-059 | Cap-loop and seed command consumers; disabled split/probe actions until their milestone | T-056, T-058, T-026, T-028 | C | operation commands; do not edit T-026/T-028 concurrently |
| 6 | T-060 | Enable Edge and Point in T-053's list and wire mode-specific toolbar actions | T-053, T-056–T-059; after T-044/T-045/T-051/T-029 toolbar changes | C | `Islands.tsx`, `ContextToolbar.tsx` |

T-032's GPU buffer remains Body/Face only. Neither it nor T-057 may use its face id field for
edges or vertices; merge its `scene.ts`/`store.ts` changes before T-057 or reconcile both explicitly.
No edge work runs in the normal orbit path; the first use may load a per-part topology cache in a
worker, with progress and cancellation. At each viewport task, compare loaded and unloaded orbit
p95 with the 4,500-part probe and record the hardware, scene and measurements. A rise exceeding
the headroom in ADR-0022 §15.3 needs a design revision, not a relaxed budget.

After T-058, the owner can name an edge or vertex using a plain tag and pick it through the CPU
path; after T-059, a tagged opening loop or seed is handed to a real operation. After T-060, the
two menu rows become enabled only on B-rep targets. Mesh-only parts keep Body/Face. M2/M3 validate
an interior `locationInMesh` and compile it, M4 adds probes, and split and a GPU topology pass wait
for their separate work.
