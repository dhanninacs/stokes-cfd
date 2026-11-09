# ADR-0034 · One mesh result per mesher, and the user picks the one in use

**Status:** accepted · 2026-11-09 · amends ADR-0024 §4.2 ("at most one child") and §4.3 ("Latest only"); relies on ADR-0032 (the volume meshers)

## Context

ADR-0024 kept one mesh result per project: `mesh.generate` removed the previous result node, its
patches and its cached surfaces before adding the new one (the owner's Q4, "one result per project
keeps the node cheap"). ADR-0032 then gave the project six volume meshers, and comparing them is now
the point. In the mixing-elbow walkthrough (2026-11-08), the tetrahedral, polyhedral, hex-dominant
and snappyHexMesh meshes of one part were made one after another. Each generate deleted the one
before it, so only the last could be seen, and comparing two meant meshing again. The owner asked
for every mesh to stay viewable, and chose this design over keeping them for viewing only
(2026-11-09).

## Decision

1. `/mesh/results` keeps **one `mesh_result` per mesher**. `mesh.generate` removes only the previous
   result of the **same** mesher, with its patches and cached surfaces, as ADR-0024 §4.3 did for
   every result. The other meshers' results stay.
2. A `mesh_result` gains the bool parameter `active`. **The result in use** is the one marked
   `active`; with none marked, it is the latest (`stokes.mesh.model.active_result`, mirrored by
   `activeResultOf` in the web app). A project saved before this holds one result and no mark, so it
   reads as before.
3. **The result in use is "the mesh" everywhere a single mesh is meant**: `mesh.status`,
   `mesh.tasks`, `mesh.quality`, the staleness pass, the run snapshot (through `mesh.status`'s
   `result`), and the viewport, which draws only its patches.
4. A new generate makes its result the one in use. A generate whose inputs match a kept result of
   that mesher (the existing reuse) takes that result and makes it the one in use, without meshing.
5. **`mesh.use result=<id>`** makes a kept result the one in use. It is journaled and is not an undo
   step, like the results themselves. It is refused with `mesh.busy` while a different mesh is being
   made, and with `mesh.no_result` for an id that is not a kept result. It returns `mesh.status`.
6. A job stopped by a new generate may belong to another mesher's result, which now stays. Its
   result is marked `cancelled` at once, because no event will ever end it.
7. In Guided mode, the Mesh card shows a **Mesh to use** picker when more than one finished mesh is
   kept. Choosing one calls `mesh.use` and sets the settings' mesher to that result's. The card,
   the staleness check and Regenerate then describe the mesh in use. With several meshes in the
   tree, the row of the one in use says "in use".

## Alternatives considered

| Alternative | Why not |
|---|---|
| Keep the results for viewing only; Run always uses the newest | Two notions of "the mesh" (shown, and solved on) that can disagree, with nothing on screen saying which one a run will use. |
| Keep every generate, several per mesher | The tree and the disk grow with every Generate. A second mesh of the same mesher is a settings change the user can repeat; comparing meshers is what was asked for. |
| A separate "comparison" view that loads results from old jobs | A second way of finding a mesh. The result nodes already carry everything a mesh needs (patches, quality, surfaces cache). |

## Consequences

- A project can hold up to one result per mesher (seven today), each with its cached surfaces. The
  disk cost is the surfaces cache per kept result. The agent's jobs were never deleted on replace,
  so that does not change.
- Results of the meshers not in use keep their own status. Only the result in use is re-checked for
  staleness, and choosing another one re-runs the check.
- `test_the_commands_do_not_include_the_ones_the_briefs_exclude` lists `mesh.use`. ADR-0024's
  "the group never gains a second child" no longer holds; protection of everything under
  `/mesh/results` from user edits does.
- Full mode has the tree rows and their "in use" mark, but not yet a picker of its own. `mesh.use`
  is the API for it.
