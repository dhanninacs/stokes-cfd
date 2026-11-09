# ADR-0003 · CAD kernel: Open CASCADE through OCP, and only OCCT

**Status:** accepted · 2026-10-02 (decisions D1, D2, D3, D4)

## Context

Stokes imports CAD (STEP, IGES, BREP, STL, OBJ, 3MF), builds primitives and
booleans, prepares geometry (enclosure, fluid extraction, defeaturing,
splitting) and lets the user name faces. No sketch-based modelling, no native
vendor formats, no commercial kernel: you decided OCCT only, ever.

## Decision

- Open CASCADE Technology through `cadquery-ocp` (Apache-2.0 bindings; OCCT
  under LGPL-2.1 with the Open CASCADE exception) for import, healing
  (`ShapeFix`), primitives, booleans, tessellation (`BRepMesh`) and face
  topology.
- A healing pipeline runs on every import with a report; failures fall back
  to a mesh-only part with faces split by feature angle so the workflow
  continues.
- Persistent face identity across re-imports: STEP entity names, geometric
  signatures (centroid, area, normal, bounding box) and a user-confirmed
  remap dialog.
- Design size: 50 k faces and 20 M triangles.
- The OCCT notice goes in the About box and the manual.

## Alternatives considered

| Alternative | Why not |
|---|---|
| pythonocc-core (LGPL-3) | Same kernel; OCP has the Apache licence and wheels for Python 3.11–3.14 (verified 2026-10-02). |
| Siemens Parasolid, Spatial ACIS | Closed by decision D2. |
| Commercial translators for SLDPRT, PRT, CATPart | Closed by decision D3. |

## Consequences

- Import robustness is OCCT's; ten public STEP files form the M1 test set.
- OCP wheels are large; install size is accepted.
- No kernel abstraction layer is built; the geometry package uses OCCT
  directly.
