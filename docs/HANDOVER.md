# Stokes Handover

**Updated:** 18 November 2026 (Sydney)
**Working copy:** `D:\WSL\OF14_windows\stokes-windows` (branch `windows`)

Read `CLAUDE.md` first, then `docs/00-open-questions.md` and the ADR for the area you change.

## Goal

Stokes is a freemium desktop CFD workbench on OpenFOAM: CAD in, results out, every action a tool
call. The work now is to make the whole path (model or import a body, mesh, run, look at results,
optimise the shape) work from the GUI on native Windows (FoamWin, `STOKES_FOAMWIN_ENV`) and on
Linux/WSL, and to keep both branches level.

## Current status

- **Code:** the `windows` and `linux` branches of this repository.
- **Checks:** Windows: 5,222 Python tests passed, 0 failed; web: 2,482 vitest tests passed. Linux
  (WSL clone `~/stokes-linux`, Python 3.13 via uv): ruff, mypy, import, IP and content gates and the
  licence audit are clean; 5,357 passed, 7 failed for environment reasons only (4 SSH-tunnel tests
  need a test SSH key and sshd; 3 shape re-tag tests need the Rust implicit mesher, not built in
  that WSL; they fail identically without the merge).
- **Flow strip:** Geometry, Fluid, Boundaries, Mesh, Run, Results, and an optional seventh step,
  **Optimise**, whose card opens the Optimise panel (shape optimisation, with the cycle status) or a
  parametric study (New study, Design table, Plots). It never opens by itself. In Full mode its chip
  brings the Explore stage forward.
- **Shape optimisation** (ADR-0038, ADR-0040, ADR-0041): the "Equal flow split" objective runs with
  finite differences, the default (N+1 solves per cycle). The adjoint goes through the separate GPL
  program `adjointFoam` (repository `of14-adjoint`) and is experimental. Phase C of the accuracy
  study found the wall gradient on snapped cells was not the main error; an opt-in
  `sensitivityWallGradient leastSquares` was added; V2 and V3 still fail
  (`docs/adr/ADR-0041-flow-split-adjoint.md`). The adjoint stays unchecked; finite differences stay
  the default.
- **Meshing:** cfMesh (hex-dominant, tetrahedral, polyhedral, poly-hexcore) runs through the app on
  both hosts, verified through the GUI on 2026-11-18 on a 20x10x10 mm duct at 1.5 mm cells with
  identical cell counts on both: 6,336 / 134,085 / 41,158 / 8,934. Hex-dominant and poly-hexcore
  quality is ok; tet and poly are marginally over the skewness limit on that thin duct on both hosts.
  Native Windows needs FoamWin v14.0.0-win.5 or later (31 Oct 2026); an older install has no cfMesh
  programs and Stokes refuses with "cartesianMesh is not on the engine's PATH". On WSL/Linux the
  agent now sources OpenFOAM with the account's `USER`
  (`agent/stokes_agent/engine_env.py`, `_sourcing_shell_env`), so a cfMesh in `~/OpenFOAM/$USER-14` is
  found. The Mesh card and panel list a mesher the host lacks as "not available" and name the missing
  program. Gmsh is a driver since ADR-0043 (tetrahedra, prism layers on the walls).
- **Geometry:** flat faces that a curved surface crossed at a shallow angle are no longer merged
  (`stokes/geometry/faceleaks.py`). A drawn arc keeps its exact circle (ADR-0036 addendum
  2026-11-17). Splines are still polylines.
- **GUI walkthrough fixes** of 2026-11-17 and 18 are listed under Unreleased in `CHANGELOG.md`.
- **Public repository:** `dhanninacs/stokes-cfd` still carries the 2026-11-09 source snapshot (code
  branches last touched 2026-11-12). Publishing a newer one is an owner decision, not taken.
- **Packaging:** the local FoamWin installer and zip under `D:\WSL\OF14_windows\pkg\windows` are the
  20 Oct build with the 31 Oct cfMesh programs copied in by hand; not rebuilt.
- **FoamWin:** GitHub `main` is `a7bccdc5` (release v14.0.0-win.5 and the project site). Branch
  `fix/windows-libs-and-stop` (`4ee2e242`, pushed 2026-11-18) has four fixes in no release: generic
  patch fields in `decomposePar`/`reconstructPar` on Windows, bare words in `-libs` and `FOAM_LIBS`,
  the stop handler for `stopAtWriteNowSignal`, and symlinks replaced by copies. Merging it is a
  release decision, not taken.
- **M0 orbit gate** and the other milestone states are in `docs/06-roadmap.md` and `CLAUDE.md` §11.

## Immediate next steps

1. Linux agent: pull `linux`; build the Rust mesher (`native/implicit-mesh`) for the 3 re-tag
   tests; set up the SSH test key for the 4 SSH tests (see the Linux note).
2. Autosave into the project: closing relies on the recovery prompt (`recovery.json`), and after
   Restore the view is empty for about 20 s while bodies rebuild, with no message. Add a message or
   progress, and decide on saving into the project (`docs/00-open-questions.md`, U3).
3. Optimise panel: it stops well above the window bottom; inlets are drawn red; splines facet.
4. Gmsh: a pre-check of the layer fold at sharp inside corners (ADR-0043 decision 8); cfMesh quality on thin ducts (tet and poly just over the skewness limit).
5. Adjoint: separate the adjoint-field and finite-difference errors before trying again (ADR-0041;
   `of14-adjoint` `ROADMAP.md`). Do not make it the default until V2 and V3 pass.
6. `pip-audit`: opentelemetry-api 1.45.1 (fix 1.45.2, pulled in through fastapi); update the pin.
7. Rebuild the FoamWin installer and zip from a release with cfMesh instead of the hand-copied
   programs; owner decides on merging FoamWin's fix branch and on a new `stokes-cfd` snapshot.
8. The cfMesh fork (`Ultravis66/cfMesh-OpenFOAM13`) has no licence file of its own (GPL headers only);
   ask counsel before a public release (`docs/00-open-questions.md`).

## Evidence and private artifacts

Walkthrough scripts: `web/build_sketch_gui.mjs`, `web/build_elbow_gui.mjs`, `web/e2e_adjoint_gui.mjs`,
`web/e2e_manifold_gui.mjs` (Playwright, against a server on an empty project). Earlier handovers:
`docs/HANDOVER-2026-11-01.md`, `docs/HANDOVER-2026-11-04.md`; readiness: `docs/PRODUCTION-READINESS.md`,
`docs/LINUX-READINESS.md`. Logs, screenshots, local project copies and agent coordination notes are
under `.local/` and are ignored; they are not part of the public handover. Before any commit or
publication, review the diff for personal paths, credentials and ignored artifacts.
