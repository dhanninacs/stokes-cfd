# Geometry preparation: the task plan for ADR-0021

Decisions: `docs/adr/ADR-0021-geometry-preparation.md` (D-numbers below are its sections).
Briefs: `.mailbox/tasks/T-NNN-*.md`. Owner **oc** = opencode (DeepSeek V4.1 Flash, one task at a
time, branch `oc/T-NNN` from `main`), **C** = Claude (Opus; Fable reviews). Sizes: S under an hour,
M a few hours, L a day or more. The order is the execution order for opencode; Claude's tasks run
beside it and are listed where their results are needed.

## Before anything

- `oc/T-005` (face adjacency at import, reworked into one archive per project) lands first: T-016,
  T-017, T-024 and T-031 edit the same worker and cache files.
- T-011 is **merged into T-031**, T-012 **into T-030**; T-013 stays and waits for the rename.
- `docs/02-architecture.md` has the register row; ADR-0020 is marked amended.

## Order

| # | ID | Task | Owner | Size | Depends on | ADR |
|---|---|---|---|---|---|---|
| 1 | T-014 | Rename core: command aliases, node-type alias, project migration v2, legacy path rewrite | C | S | T-005 merged (board hygiene only) | D2.3, D2.4 |
| 2 | T-015 | Tags: the mechanical rename of named selections (Python, CLI, front end, docs) | oc | M | T-014 | D2.1, D2.2 |
| 3 | T-019 | Face and body picking: pick model in the store, Shift and Ctrl, multi-face highlight, mode island | oc | M | — | D3.1, D3.2, D3.5 |
| 4 | T-020 | Name faces: the Name… action, `tag.region`, tag overlays in tag colour, hidden faces, tag rows highlight in 3D | oc | M | T-015, T-019 | D3.5, D2 |
| 5 | T-016 | Import geometry: IGES and BREP readers, `read_source`, `geometry.import` with its alias, `geometry.formats`, CLI | oc | M | T-005, T-014 | D1.1, D1.2, D1.4 |
| 6 | T-018 | Import geometry in the UI: titles, filter from `geometry.formats`, units prompt, docs | oc | S | T-016 | D1.1 |
| 7 | T-021 | `geometry.style` bulk command; `opacity` on `part` and `tag` | oc | S | T-015 | D4.1, D4.2 |
| 8 | T-022 | Tree rows: eye, swatch and the style popover | oc | M | T-021 | D4.3 |
| 9 | T-023 | Per-body opacity in the viewport: batch buckets, occluders, own meshes | C | M | T-021 | D4.4 |
| 10 | T-017 | Import geometry: STL, OBJ and 3MF as mesh-only parts | oc | M | T-016 | D1.2, D1.3 |
| 11 | T-024 | Operations core: node types, shape cache, basis, worker job, commands, history, undo, re-run; `primitive` and `boolean` | C | L | T-005, T-015, T-016 | D8, D6.3 |
| 12 | T-025 | Enclosure operation | oc | M | T-024 | D6.2 |
| 13 | T-026 | Extract fluid operation: cavity, region with seed and caps | oc | M | T-024 | D6.1 |
| 14 | T-029 | Operation cards, the Prepare menu, task strip, progress and cancel, seed picking | oc | M | T-024, T-025, T-026 | D7 |
| 15 | T-027 | Repair operations 1: heal, sew, orient, solidify | oc | M | T-024 | D5 |
| 16 | T-028 | Repair operations 2: close openings, unify, drop slivers, remove faces | oc | M | T-027 | D5 |
| 17 | T-030 | STEP export through the shape cache: node names, several sources, derived parts (T-012 merged) | oc | S | T-024 | D8.3 |
| 18 | T-031 | `geometry.check` through the shape cache, as a worker job with progress; face ids in findings (T-011 merged) | oc | M | T-024 | D8.3, D5 |
| 19 | T-013 | Validate `members` on write (existing brief, renamed symbols) | oc | S | T-015 | — |
| 20 | T-033 | Boundaries reference tags: `boundary` node type, Assign boundary from a tag, proxy rows | oc | M | T-020, T-022 | D2.5 |
| 21 | T-032 | GPU id buffer: box and lasso; hover and click move over when measured | C | L | T-019, T-023 | D3.3, D3.4 |
| 22 | T-034 | The M1 test set (ten public STEP files) and the gate run | C + owner | M | T-029, T-032 | ADR-0020 d9 |

Claude's T-023 can start as soon as T-021 is merged and T-024 as soon as T-016 is; both run
while opencode is on T-017 to T-022. Two opencode tasks may run at once only when their briefs
own disjoint files: T-017 with T-021, T-025 with T-026, T-030 with T-031.

## Where the owner can try something new

| After | What to try |
|---|---|
| T-015 | `stokes tag list demo.stokes`; the tree says Tags; an old project opens and its selections are tags. |
| T-019 | Face mode in the Modes island: click, Shift-click and Ctrl-click faces across bodies; Esc clears. |
| **T-020** | **Name faces in the viewport:** pick faces, Name…, type `inlet`; the faces turn the tag's colour and the tag appears in the tree; clicking the tag row highlights its faces. This is the first point the owner asked for. |
| T-016, T-018 | Import geometry… with an IGES or BREP file; the filter lists every format. |
| T-022, T-023 | The eye and the swatch on body and tag rows; a translucent body with the slider. |
| T-017 | Import an STL, OBJ or 3MF; its faces split at 30°; tag them; export STL. |
| T-024 | From the palette: `operation.create type=boolean` on two bodies; undo and redo; change a parameter and watch it re-run. |
| T-025, T-029 | Prepare ▸ Enclosure… around a body with default margins, half model by symmetry; the inlet, outlet and far-field tags come out tagged. |
| T-026 | Prepare ▸ Extract fluid…: a pipe fitting with its openings capped; pick the seed; the fluid body appears. |
| T-027, T-028 | Repair ▸ on an IGES surface model: Sew, Make solid; Close openings on a body missing a face. |
| T-032 | Box and lasso selection of faces across thousands of parts. |
| T-034 | The gate: a public STEP to fluid domain with inlet, outlet and walls in under five minutes, replayed from the journal through the CLI to the same STL. |

## Decisions and risks that need the owner

1. **OCCT issue 1564**: eight `TKGeomBase` files in OCCT 8.0.1 carry a proprietary header
   (open since 2026-09-27). Every B-rep feature here sits on them. Watch the issue or ask counsel.
2. **The term "tag"**: chosen for the reasons in ADR-0021 D2.1; the rename is one task, so a
   change of mind before T-015 starts costs nothing.
3. **Overlapping boundary tags**: a face in two `boundary` tags makes two overlapping patches. The
   plan warns in `tag.list` and lets M3's compiler refuse; say if you want tags to be exclusive.
4. **Enclosure defaults** (3 extents each way, 8 downstream, 1 % blockage warning) follow no
   standard; they are editable per operation. Say if your practice differs.
5. **Auto re-run** of operations on a parameter change is on by default (debounced, cancellable).
   Say if you prefer explicit Run.
6. **Repair tolerances** were set on generated files; expect to tune them on real CAD in the first
   weeks.
