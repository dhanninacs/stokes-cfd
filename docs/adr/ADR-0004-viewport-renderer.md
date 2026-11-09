# ADR-0004 · Viewport renderer: VTK with C++ picking helpers

**Status:** superseded by ADR-0013 (web front end, three.js viewport) · 2026-10-02 · kept for the reasoning that still applies to the Qt host window

## Context

The viewport must show tessellated CAD (up to ~20 M triangles), mesh
boundaries of 20 M-cell meshes, and results (surfaces, slices, streamlines),
with face picking under 16 ms and 60 fps interaction. The same toolkit should
serve the agent for extraction.

## Options

| Option | Licence | Fit | Verdict |
|---|---|---|---|
| VTK 9.7 (OpenGL2 backend) in a Qt widget | BSD-3 | native OpenFOAM reader, every filter needed for post, hardware selection, LOD actors, Qt integration | **chosen** |
| Custom renderer (OpenGL via moderngl or wgpu) | MIT | full control and the smoothest possible feel; months of work before parity for post-processing | deferred; revisit only if VTK's interaction feel blocks goal 2 |
| three.js in a webview | MIT | fingen's path; browser memory limits on large data | rejected |
| pyvista | MIT | a convenient layer on VTK | allowed for prototypes and the agent; the core uses VTK directly |

## Decision

- VTK 9.7 for rendering in the desktop and for extraction in the agent.
- Picking through an id-buffer: each face (CAD) or patch triangle group gets
  an id colour; one GPU read-back per click, implemented in `stokes.native`
  (nanobind) with VTK's hardware selector as the fallback.
- Level-of-detail actors swapped in during interaction, full-resolution at
  rest; one actor per part to keep draw calls low.
- Selection, hover and boundary tints rendered as per-face scalars, not as
  duplicated geometry.
- Orientation cube, section plane and gizmos are VTK widgets styled to the
  design system.
- A reduced-quality mode (decision B6): LOD always on, no edge rendering,
  one light, a lower-resolution picking buffer and surface-only mesh display;
  chosen automatically on integrated GPUs and switchable in the view island.

## Consequences

- VTK's Qt widget needs care on HiDPI and multi-monitor setups; this is
  tested in M0.
- The viewport feel depends on our interactor style and the LOD logic, both
  measured against the budgets in docs/02 §6.
- The same VTK code path on the agent means the desktop never parses polyMesh
  itself.
