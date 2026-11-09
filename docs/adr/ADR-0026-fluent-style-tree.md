# ADR-0026 · Fluent-style tree: Setup, Solution and Results, with capping under Mesh and surfaces under Results

**Status:** proposed · 2026-10-22 · relies on ADR-0021 (operations, including `cap` and `extract_fluid`), ADR-0022 (analysis set-up), ADR-0023 (runs, modules, templates), ADR-0024 (mesh jobs and results) and ADR-0025 (native Windows)

## Context

The owner asked for the simulation tree and settings panels to follow the layout of the ANSYS Fluent
tutorial "Chapter 1: Fluid Flow in an Exhaust Manifold" (Release 2024 R2), so that a similar case
can be set up, solved and post-processed in the Stokes GUI. The tutorial's watertight workflow
covers the same stages as Stokes, in this order: Import Geometry, Surface Mesh, Describe Geometry,
Enclose Fluid Regions (capping), Create Regions, Update Regions, Boundary Layers and Volume Mesh.
Setup covers models, materials, cell zones and boundary conditions. Solution covers methods,
reports, initialisation and the run. Results covers graphics, surfaces (iso-clip, iso-surface),
plots and reports.

Stokes already has most of the machinery. Capping is the `cap` operation (ADR-0021 decision 5,
T-028), which closes free-edge loops with planar or filled caps, and `extract_fluid` reads its
caps. Boundaries and regions are nodes (ADR-0022). Slices, streamlines, probes and reports are run
commands (`run.*`) and results in the Results stage. What does not yet exist is a surface result
of the kind Fluent calls an iso-clip or iso-surface, and the tree does not show capping as a step
of the Mesh stage.

## Decision

1. **The stages follow the tutorial's order.** The tree shows Geometry, Mesh, Physics, Regions,
   Solve and Results, in that order. Setup is the union of Geometry, Mesh, Physics and Regions.
   Solution is Solve, and Results is Results. This ADR changes the labels and the order of the
   rows; it does not move any node, id or path.

2. **Capping lives under Mesh, as a row of the guided meshing tasks** (ADR-0021 decision 5). The row
   opens the `cap` operation, and the tree row carries its state from the operation's own status. No
   new capping command is added: the operation already is the capping step, and a second command
   for it would be a second entry point to the same machinery (ADR-0010).

3. **Surfaces live under Results.** A surface result is a derived object of a finished run, shown
   as a Results row like a slice or a streamlines set. The first surface kinds are a plane clip
   (Fluent's iso-clip on a coordinate) and an iso-surface of a field value. Both are new `run.*`
   commands. They are defined in the API before any row or panel exists (ADR-0010, CLAUDE.md §1
   rule 4), each with a schema, a test that goes through `Session.execute`, and a journal record.

4. **The Solution stage keeps reports and monitors.** Fluent's report definitions and monitors map to
   Stokes's Runs and Monitors rows; they are not moved into Results, because a monitor is produced by
   the solver, not read from a result.

5. **Panels follow the tree.** A row opens the panel that owns its node, as `openTargetOf` does now.
   The rows gain no new panel kinds in this ADR; the Results panel gains a Surfaces section when the
   first surface command lands.

## Alternatives considered

| Alternative | Why not |
|---|---|
| A new `geometry.cap` command beside `cap` | Duplicates the operation. The operation is the capping step; a second entry point needs a second test and a second journal path. |
| Put surfaces under Setup, as a geometry tool | Fluent puts them under Results, and they are derived from a finished run. |
| Rename the stages to Fluent's exact words (Setup, Solution, Results) | The tree already has Solve and Results; the change is the order and the grouping, which keeps the labels the owner has seen. |

## Consequences

- The Mesh stage shows a capping row that opens the `cap` operation, so the capping state reaches
  the tree without a new command.
- Results gains a Surfaces group after the first surface command lands. Until then the group is not
  shown, rather than shown empty.
- The Playwright GUI test (elbow case) covers the tree order and the capping row.
- Clip and iso-surface extraction need agent-side support (the VTK host extracts on the agent, as
  `extract.surfaces` does). That is the main cost of the Results change and is tracked as the next
  step after this ADR.

## Amendment 1 (2026-10-22): the plane clip is the existing slice; the iso-surface is `run.iso_surface`

Decision 3 said both surface kinds would be new `run.*` commands. Building the iso-surface showed
that the plane clip already exists, so the decision changes for the clip only:

1. **Plane clip = the existing slice.** A clip on a coordinate is a plane cut, which is what the
   slice already is: the agent's `slice.cut` cuts any plane, `RunStore.slice_artifact_for` caches it
   by `plane_key`, and `/api/results/slice/{run}?plane=ox,oy,oz,nx,ny,nz`,
   `/api/results/slice-edges/{run}` and `/api/results/field/{run}/slice` serve it. No second clip
   command is added: it would be a second entry point to the same cut (ADR-0010). There is no
   `run.slice` command today; the slice is reached through those routes and `run.fields`
   (`slice: true`). Whether the slice also gets a `run.*` command is in the "Still open" table
   (`docs/00-open-questions.md`, R1).
2. **The iso-surface is `run.iso_surface`** (`run`, `field` = `p` or `T` from the run's fields,
   `value` as a quantity such as `300 [K]`, `wait`). It answers with the surface summary: state,
   triangle and point counts, the field's range, the solved time, the cache key and the result
   reference `/api/results/iso/{run}?field=&value=`. It refuses a value outside the field's range
   (`results.iso_out_of_range`), a field that cannot be contoured (`results.iso_invalid`) and a
   surface over `MAX_ISO_TRIANGLES` = 2,000,000 (`results.iso_too_large`, checked on the agent's
   reply before the artifact is fetched). The contour runs on the agent (`iso.cut`); without
   `wait` the command answers `cutting` and the summary follows as `run.iso_ready` or
   `run.iso_failed`, so the session thread never waits on a host.
3. **One cut scheme for both kinds.** Planes and iso-surfaces share the cut slots, the queue caps,
   the in-flight map and the per-run cache folder. Their keys cannot meet: `iso_key` hashes
   `iso|field|value` (a plane's text has no `|`), the file is `iso-<key>.stkiso` against a plane's
   `<key>.stkslice`, and a cached file is served only when its header names the same field and
   value. Each kind keeps its own 16-entry LRU.
4. **The range is the run's recorded range** (every patch and the default slice, as `run.fields`
   reports it). A value only the interior reaches would be refused; for `p` and `T` the extremes
   lie on the boundary in the cases this release solves. *Superseded by amendment 2.*

## Amendment 2 (2026-10-22): the iso range is the whole volume's

Amendment 1's point 4 refused a value only the interior reaches. The range now comes from the
volume, measured where the data is:

1. **The agent measures it.** `extract_fields` calls `volume_ranges` on the internal mesh the slice
   reader already holds (no second read): each single-component field's min and max over every
   cell and every point (the points are what `vtkContourFilter` contours). `pack_fields` writes it
   as the field artifact's header table `volume: {field: {min, max}}`; non-finite or inverted
   ranges are dropped.
2. **The query returns it.** `run.fields` adds `volume: {min, max}` to a field's entry when the
   header has one; `min`/`max` stay the recorded range, which is what the legend shows.
3. **The refusal uses it.** `run.iso_surface` and `GET /api/results/iso/{run}` check the value in
   the union of the recorded and volume ranges (`stokes.run.iso.volume_range`), and the summary's
   `range` is that union. The desktop never opens the case; the read stays on the agent.
4. **A cache written before this has no `volume`** and is checked against the recorded range, as
   before; re-collecting the run's fields adds it.

