# ADR-0031 · A 2D mode: one cell thick, `empty` (or `wedge`) front and back

**Status:** accepted · 2026-11-07 · relies on ADR-0007 (meshing), ADR-0024 (mesh jobs and results), ADR-0025 (native Windows), ADR-0029 (the agent's allow-list), ADR-0013 (front end)

## Context

Stokes models only 3D flow. OpenFOAM has no 2D solver: a 2D run is a 3D mesh exactly **one cell
thick** whose two faces normal to the thickness are `empty` patches (planar) or `wedge` patches
(axisymmetric, a thin wedge about an axis, the on-axis face collapsed and `empty`). The solvers,
`fvSchemes` and `fvSolution` are unchanged; every `0/` field gets `type empty;` (or `wedge`) on those
patches. Imported OpenFOAM 2D tutorials (pitzDaily, airFoil2D, the cavity) already run through the
case-import path (ADR-0029); this ADR is the native modelling mode. The OF14 tutorials show the two
ways the mesh is made: blockMesh with one cell across the thickness (cavity, pitzDaily, the wedge
cases such as `multiphaseEuler/bubblePipe`), and snappyHexMesh on a thin slab followed by
`extrudeMesh` from one face to a single layer and `createPatch` to make the two faces `empty`
(`incompressibleFluid/wingMotion`).

## Decision

1. **2D is a mesh setting**, because it is a property of the mesh and every other stage follows from
   the mesh's patch types. `mesh_settings` gains:
   `two_d` (`off` | `planar` | `axisymmetric`, default `off`), `plane` (`xy` | `xz` | `yz`, default
   `xy`), `thickness` (a length; 0, the default, keeps the domain's own extent across the plane) and
   `wedge_angle` (degrees, default 5, range (0, 10]). All four are set through `mesh.configure` like
   every other setting, journal and undo included. An untouched project (`two_d off`) writes
   byte-identical dictionaries and keeps its mesh basis.
2. **The plane names the two in-plane axes; the third is the normal.** For `axisymmetric` the first
   in-plane axis is the symmetry axis and the second is the radius (`xy`: axis x, radius y), and the
   radius must not be negative anywhere in the domain.
3. **Geometry is a slab.** The user gives the 2D shape as a thin solid (an imported part, a box
   primitive, or a box domain) whose two faces normal to the plane are flat. Stokes does not build a
   solid from a sketch or a single face in this ADR; that is a later geometry operation (an
   extrusion), and it produces the same slab.
4. **blockMesh** keeps its one block. In `planar` the normal axis gets exactly **one cell**, the two
   normal sides become one patch `frontAndBack` of type `empty`, and, with `thickness > 0`, the block
   spans that thickness centred on the domain's mid-plane. In `axisymmetric` the block is turned into a
   wedge of `wedge_angle` about the axis (vertices at ±angle/2, as `bubblePipe` does), the two normal
   sides become `front` and `back` of type `wedge`, and a radial side that lies on the axis
   (radius 0) collapses and is written as the patch `axis` of type `empty`.
5. **snappyHexMesh** (`planar` only, domain `part`) meshes the slab with the background block
   spanning **exactly the slab** across the plane, as `wingMotion` lays it out: the block's two faces
   there are its own patches `front` and `back`, and the slab's faces across the plane are left out
   of the staged STL. Those background faces are the 2D planes and stay exactly flat, because nothing
   snaps them (measured: when the slab's own front face was a surface, snapping left it ±0.1 mm out of
   plane and the extruded mesh had no 2D direction at all). Then `extrudeMesh`
   (`constructFrom patch`, `sourceCase "."`, `sourcePatches (front)`, `exposedPatchName back`,
   `extrudeModel plane`, `flipNormals true`, one layer of the slab's thickness or `thickness`) runs
   before checkMesh. The `plane` model makes `front` and `back` `empty` itself (measured: checkMesh
   reports 2 solution directions), so no `createPatch` is needed; `flipNormals true` keeps the layer
   inside the slab. `axisymmetric`, or a `box`/`enclosure` domain, with snappy is refused
   (`mesh.invalid`) until it is built. The agent allow-lists `extrudeMesh` with no flags
   (overwriting the mesh is OF14's default).
6. **The front/back patches belong to the mesher, not the user.** Their names (`frontAndBack`,
   `front`, `back`, `axis`) are reserved: a tag of that name is refused. Faces of a tag that lie on a
   front/back side are taken from the tag (blockMesh: the side is not mapped; snappy: the faces go to
   `front`/`back`); a tag left with no face is reported as unmapped. In the solve snapshot a patch
   whose mesh type is `empty` or `wedge` gets that condition whatever boundary names it, and every
   field writer emits `type empty;` / `type wedge;` for it, exactly as it already does for
   `symmetryPlane`.
7. **Solver settings are unchanged.** No new solver, scheme or switch: OpenFOAM solves the 2D case
   from the patch types alone. Fluxes reported by Stokes are through the mesh as built, so in
   `planar` they are per the mesh's thickness and in `axisymmetric` per the wedge.
8. **Results are shown in 2D.** When the meshed project is 2D the Results viewport locks to an
   orthographic camera looking along the plane's normal (rotation off; pan and zoom stay), so the
   field drawn on the front patch is the 2D picture. The Mesh settings and the Guided Mesh card show a 2D
   switch and the plane picker.

## Alternatives considered

| Alternative | Why not |
|---|---|
| A project-level "dimension" outside the mesh | The solver needs nothing from it; the patch types already carry it, and a second source of truth could disagree with the mesh that was actually built. |
| Write `empty` on the snappy background's front/back and skip `extrudeMesh` | Castellation splits the background cell across the thickness; the result is not one cell thick and the solver refuses `empty` on it. |
| Snap to the slab's own front face and extrude it | Measured: the snapped face is not flat (±0.1 mm on a 1 mm slab), so the extruded front/back are not parallel planes and checkMesh finds no 2D direction. |
| Let the user assign front/back like any patch | Any condition other than `empty`/`wedge` there makes the case 3D or invalid; the OpenFOAM rule is fixed, so the choice is not the user's. |
| Axisymmetric with snappy via `extrudeMesh` `wedge` model | Possible (`extrudeModel wedge`), but needs the slab to touch the axis and a second axis patch; deferred until there is a case that needs it. |

## Consequences

- 2D cases mesh and solve in seconds, which also makes them the fastest end-to-end checks.
- Four more `mesh_settings` parameters and one more agent utility (`extrudeMesh`) to keep valid.
- blockMesh names the planar pair `frontAndBack` (one patch, as the cavity tutorial does) and snappy
  names them `front` and `back` (as `extrudeMesh` leaves them); both are `empty` and both are reserved.
- A future "extrude a face/sketch" geometry operation plugs into decision 3 without changing meshing.
- `mass_flow_inlet` in `axisymmetric` is per wedge, not per full revolution; a later change may scale
  it by `360 / wedge_angle`.
