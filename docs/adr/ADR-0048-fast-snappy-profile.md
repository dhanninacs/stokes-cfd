# ADR-0048 · A fast snappyHexMesh profile, in the app rather than in a GPL fork

**Status:** accepted · 2026-11-10 · (relies on ADR-0001, ADR-0007, ADR-0024, ADR-0032)

## Context

The owner asked for a **Fast** meshing option in the GUI and API, taking the ideas
from `fastSnappyHexMesh` (OpenFOAM-13/14) but redoing them from the ground up.
That project is GPL-3.0-or-later, and ADR-0001 forbids copying, linking or
porting its code into the closed-source application. Stokes already drives the
stock OpenFOAM `snappyHexMesh` as a separate process, so the safe place to add
speed is the case the app asks it to build.

The 2026-11-10 Linux measurements on the Ahmed body tutorial (238,131 cells,
3 layers, 2 physical cores / 4 threads) show where the available time is:

| change | wall time | cells |
|---|---:|---:|
| stock snappyHexMesh, serial | 48.5 s | 238,131 |
| stock snappyHexMesh, 2 MPI ranks | 40.4 s | 238,131 |
| stock + the fast profile, 2 ranks | **34.6 s** | **198,268** |

The first two are app-independent; the third is the profile this ADR adds.

## Decision

1. **A speed profile on the existing mesher**, not a new mesher id. The
   `mesh_settings` node gains `speed: "balanced" | "fast"`, default
   `"balanced"`. It is shown for snappyHexMesh only and is ignored by every
   other mesher. The API is the existing command:
   `mesh.configure { values: { speed: "fast" } }`; the Guided Mesh card has a
   **Fast meshing** switch and Full mode shows **Speed** under Execution.

2. **The profile is a quality trade, stated as one.** It writes:
   - `nCellsBetweenLevels 2` instead of 3 (the largest saving; one fewer buffer
     layer, so fewer cells around every surface);
   - `nSmoothPatch 2`, `nRelaxIter 4`, `nFeatureSnapIter 8` (the solve loop
     stays at 50 — the measured gain was in these loops, not in cutting the
     solve short);
   - layer `nSmoothNormals 2`, `nLayerIter 30`, `nRelaxedIter 5`.
   The patch names, boundary types, levels per surface and layer counts are
   unchanged.

3. **The app still runs stock `snappyHexMesh`**, as a process, with the same
   dictionary writer and the same `checkMesh` gate. No OpenFOAM or
   fastSnappyHexMesh source is copied, linked or vendored. The speed comes from
   the inputs the app generates, which is the only GPL-clean place it can come
   from without writing a new mesher.

4. **The profile is in the basis.** `snappy/6` adds `speed` and the selected
   snap controls to the `snap` part; switching Fast on marks an existing mesh
   out of date rather than reusing it. `cells_between_levels` already belongs to
   the `sizing` part.

5. **No automatic core change.** The profile does not silently raise `cores`:
   MPI ranks are the user's setting and a remote host may have fewer cores than
   the desktop. The card says "more cores mesh faster" beside Cores and the
   profile is measured at the user's count.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Bundle or call the GPL `fastSnappyHexMesh` binary | A real 1.3–1.5x, but it is a GPL program to distribute and support, and the owner asked for a ground-up option. External use remains possible later as an opt-in host capability, not as app code. |
| Port its C++ changes into a new native mesher | A multi-month project (castellation, snapping, layers, MPI balance), and a clean-room reimplementation would not be "the same ideas with a flag" — it would be a new mesher with its own validation matrix. |
| Make Fast a new mesher id | A second entry in the Mesher list implies a different algorithm and splits the result cache; Fast is the same mesher with different controls. |
| Lower the base size instead of the refinement gap | It changes the cells the user asked for everywhere, not the gap between levels. The user already has Fineness for that. |
| Cut `nSolveIter` from 50 to 30 | Measured no meaningful gain on the Ahmed body; the profile keeps 50 and does not risk snapping quality for nothing. |

## Consequences

- Fast produces about **17 % fewer cells** on the Ahmed body and **1.17x**
  against stock at the same 2 ranks, **1.40x** against stock serial. It is a
  coarser mesh: the Ahmed body's max aspect ratio moved from 11.5 to 19.3 and
  max non-orthogonality from 44.1 to 55.8 degrees on the pipe, while `checkMesh`
  still reported `Mesh OK` and only the tutorial's known concave-cell finding.
- The Guided card and Full mode expose one setting, no new mesher. A project
  saved before this reads `speed` as its default and produces byte-identical
  dictionaries.
- Further speed, in measured order of value: auto-selecting ranks from the
  host when Cores is left at 1; caching the `surfaceFeatures` `.eMesh` across
  meshes of the same surfaces; and running `blockMesh` and `surfaceFeatures`
  concurrently in the agent. None is assumed here.
- The GPL boundary stays as ADR-0001 states: the engine is a separate process,
  and this ADR adds no engine source to the application.
