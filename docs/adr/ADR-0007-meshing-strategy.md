# ADR-0007 · Meshing strategy

**Status:** accepted · 2026-10-02 (decisions E1, E2, E3)

## Context

General-purpose CFD on arbitrary CAD needs automatic meshers. The
permissive-licence options are thin (Gmsh GPL, TetGen AGPL, CGAL GPL, Netgen
LGPL tet-only). snappyHexMesh, cfMesh and Gmsh are GPL but, as separate
executables the user already has or builds, are allowed by ADR-0001. You
chose all four meshers for v1.

## Decision

1. **One mesh model, four drivers.** The tree holds base size, surface and
   region refinements, feature edges, layers and location in mesh; each driver
   maps what it supports and greys out the rest.
2. **snappyHexMesh**: castellation, snapping, layers; per-selection STL with
   `solid` blocks maps to patches.
3. **cfMesh**: `cartesianMesh`, `pMesh` (polyhedral), `tetMesh`; detected or
   built by the helper; its Foundation-14 compatibility is verified in M2,
   and if it needs the ESI line it ships with that dialect.
4. **blockMesh**: background boxes and structured ducts.
5. **Gmsh**: driven as a process with a generated `.geo` (physical groups
   named after selections) and `gmshToFoam`; never linked.
6. **Quality** is a tree object fed by checkMesh: non-orthogonality,
   skewness, aspect ratio, negative volumes, layer coverage per selection,
   with "show the cells" views.
7. **Pre-flight** before launch: watertightness, `locationInMesh` inside the
   fluid, levels against base size, estimated cells against memory.
8. **Sizes**: interactive to 20 M cells, batch to 100 M; the desktop never
   loads volume data.
9. **finmesh**: your code may be ported later as a parametric-part generator
   (v2) with no employer design data.

## Alternatives considered

| Alternative | Why not |
|---|---|
| An own hex-dominant cut-cell mesher | Years of work; revisit only with evidence after the beta. |
| Netgen (LGPL) linked for tets | Tet-only; Gmsh as a process covers it. |

## Consequences

- Four drivers each need a writer, a progress parser, a quality mapper and a
  tutorial; M2 grows by about 1.5 weeks and is the first deferral lever.
