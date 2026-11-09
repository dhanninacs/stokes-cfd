# FoamWin / Stokes Handover

**Updated:** 22 October 2026 (Sydney)
**Workspace:** `D:\WSL\OF14_windows\stokes-windows`

## Goal

FoamWin packages the OpenFOAM-side tools needed to use the Stokes CFD workbench on Windows. The current work is an ongoing GUI/backend cleanup and Windows execution effort. OpenCode owns the in-progress implementation; review its shared notes in `.local/SUPERVISION.md` before changing overlapping source.

## Current status

This is **not yet a finished or release-verified build**. The working tree has extensive uncommitted changes across the Python backend, web GUI, tests, docs, and Windows/runtime tooling. Preserve unrelated changes and inspect `git status` before staging or committing.

- **Parallel meshing and solving:** Earlier evidence records successful two-rank WSL `snappyHexMesh`, reconstruction, `checkMesh`, and a two-rank `foamRun` smoke case. The elbow walkthrough also records an 88,201-cell mesh with zero `checkMesh` failures. Latest source edits route mesh and solve core counts through `stokes.cores.core_count`, and add parallel snapshot/driver tests, but a fresh focused verification of those latest edits is not recorded. Verify the GUI core field remains editable, generated decomposition uses the selected count, and execution logs confirm actual MPI ranks.
- **Elbow example:** Symmetry patch generation was corrected; OpenFOAM's non-planar `symmetry` patch is paired with matching U/p boundary conditions. The saved elbow solve completed and reconstructed on two ranks but ended `done_unconverged` at 500 iterations. Do not present it as a converged physical result. Plot and walkthrough evidence are in ignored `.local/elbow-solve-results.png` and `.local/elbow-walkthrough.md`.
- **Results tree / display quality:** The Results outline was reorganized into Fluent-like Surfaces, Graphics, Plots, and Reports groups. “Level of detail” currently means a display-quality/performance profile (edges, shadows, reflections), not mesh or field-data decimation. Previous frontend tests/build/typecheck were reported passing; there is no claim here of a fresh interactive GUI check.
- **Additional mesher choices:** The selector is a dropdown, but catalogue entries must remain disabled until executable availability, case generation, boundary mapping, and validation are implemented. The latest handoff says Gmsh CLI staging exists but its app driver is not wired/validated; cfMesh build failed against the current OpenFOAM target; foamyHexMesh was not found. Packaging/installer rebuild is unverified.
- **Outstanding GUI findings in the handoff:** Check whether preflight can still show “ready” for a case that compiler validation rejects, and whether the New Project dialog closes after successful creation. These were observed during the elbow walkthrough; do not assume fixed without code/test evidence.

## Immediate next steps

1. Read the latest `.local/SUPERVISION.md` and OpenCode handoff/reply; avoid concurrent edits to files it owns.
2. Once core-count edits settle, run the focused tests for core validation, mesh snapshots/decomposition, solve snapshots, and the affected drivers. Then verify a real multi-rank mesh and solve from the GUI-generated case, including reconstructed results and truthful convergence status.
3. Review cancellation/error behavior for MPI jobs and ensure invalid core counts produce clear, atomic errors. Keep existing Windows Job Object and cancellation tests intact.
4. Resolve the preflight/readiness and successful-new-project-dialog findings if still present; verify each with focused tests.
5. Keep unavailable meshers disabled until end-to-end native Windows and WSL checks pass. Rebuild and test installer/ZIP only after tool integration is complete.
6. Before any commit or publication, review the full diff and staged files for personal paths, credentials, API keys, local project data, and ignored `.local` artifacts. Do not stage the whole working tree blindly.

## Evidence and private artifacts

Detailed logs, screenshots, local project copies, and ongoing OpenCode coordination notes are under `.local/` and are ignored. They support the status above but are not part of the public handover. The most useful entry point is `.local/SUPERVISION.md`; it records test outcomes, paths, limitations, and ownership notes. Treat old entries as superseded only when a later entry or code/test evidence explicitly says so.
