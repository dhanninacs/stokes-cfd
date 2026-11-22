# ADR-0042 · Multi-region conjugate heat transfer: one mesh, zones, split, merge back

**Status:** proposed · 2026-11-20 · (ADR-0008, ADR-0018, ADR-0022, ADR-0024, ADR-0027)

## Context

The model already has fluid and solid regions, each with its bodies, material and boundaries
(ADR-0022), and the engine table has named `of14.multiRegion` since M0. Everything after the model
assumed one fluid: the mesh snapshot meshed `domain_part` alone, the case writer wrote one region at
the case root, the agent read `constant/polyMesh`, and the results read one set of fields. A
counter-flow heat exchanger needs two fluids that never mix and the wall between them: three regions.

Measured on FoamWin 14 (2026-11-20) with a 24 x 12 x 12 mm gyroid core, 1.2 mm aluminium wall, water
at 350 K and 300 K entering at opposite ends at 0.05 m/s, 0.3 mm cells (128 k cells): snappyHexMesh
with the two channel surfaces as face and cell zones, `splitMeshRegions -cellZones all
-defaultRegion solid`, then `foamMultiRun` with `coupledTemperature` on the `mappedWall` interfaces.
500 iterations in 160 s; the hot stream lost 104.647 W and the cold stream gained 104.648 W. Two
failures on the way: a 0.8 mm wall at 0.3 mm cells let the two fluids touch (a `hot_to_cold`
interface of 243 faces), and fluid pockets cut off from every opening made `p_rgh` singular (NaN).

## Decision

1. **A mesh is multi-region when the regions hold more than one simulated body, and each body is
   a mesh region.** No new model parameter: the regions, their kinds, materials and boundaries are
   the input. A region node holds the bodies of one material, and two channels of the same water
   are two streams with their own inlets, so a body takes its material, kind and boundaries from
   the node that holds it and is meshed and solved as its own region. Its name in the case is the
   node's when the node holds one body and the body's when it holds several. Only snappyHexMesh
   with a `part` domain meshes it in this slice; the other meshers still mesh the domain part
   alone. A body that shares no wall with another is refused by name. A new boundary goes to the
   region that holds the body its faces are on.
2. **One mesh, then a split.** Every body is meshed in one snappyHexMesh run. The faces a body
   shares with no other body are the boundary surfaces (tag-named patches, `unassigned` walls) and
   bound the mesh; the faces shared with another region's body are staged as open zone surfaces
   (`faceZone`, `cellZone <region>`, `mode insidePoint`) for every region but the last solid, which
   is the default region. Which is which is decided per triangle: a point a quarter of a base cell
   outside the triangle is inside another body or it is not. The test is
   `stokes.geometry.inside.Columns`: each body as the heights it crosses on lines a sixth of a cell
   apart, so a million triangle centres against a million triangles is a search and not a loop
   (measured on the gyroid core: 205 s point by point, 14 s for the whole snapshot this way). Zone
   surfaces get no feature edges: they are open, and every edge of their rim would be one
   (measured: 2.8 M cells and climbing).
2b. **The outer skin takes a collar of the interface along every rim.** The thin triangles a
   body's mesh has along the edge where a mouth meets the wall test as interface from both sides,
   so neither body gave them to the skin and it had hairline gaps along every rim; snappyHexMesh
   reached the whole background box through them (measured: a `background` patch of 6,136 faces
   on a 530 k-cell mesh, where a 89 k-cell mesh had been lucky). Each body's skin now runs two
   triangles down the shared wall, so the skins of two bodies lie on each other
   (`stokes.mesh.conjugate.collar`). A multi-region mesh that still keeps faces of the background
   block fails with `mesh.leaked`.
2a. **A flat face on a body's bounding box is a face of its own** (`stokes.geometry.faceleaks`):
   a lattice channel cut by a plane meets its mouth at a shallow angle over more than half the rim,
   and the feature-angle split merged the mouths into the lattice wall, so an inlet could not be
   picked. Measured on the gyroid channel: 0 of 27.6 mm² of an end plane was pickable before, 25.5
   after.
3. **A fluid region is the cells connected to its openings.** After meshing, `topoSet` rebuilds each
   fluid cell zone as the cells reachable from points just inside its inlets and outlets
   (`regionToCell`); what is cut off stays with the default solid. A fluid with no opening is refused.
4. **`splitMeshRegions -cellZones all -defaultRegion <solid>`** writes `constant/<region>/polyMesh`
   and leaves the whole mesh in `constant/polyMesh`. The whole mesh stays the mesh the agent
   measures, displays and extracts from. A split that reports an interface between two fluid regions
   fails the mesh with `mesh.fluids_touch`: the wall is thinner than the mesh can hold.
5. **The case is written per region by the writers that exist.** `stokes.compile.of14.multi_region`
   builds one `SolveSnapshot` per fluid region, lets `of14.fluid` write it, and moves the files under
   the region's folders; it adds the interface conditions (`coupledTemperature`, `noSlip`,
   `fixedFluxPressure`), the solid's `T` and properties (`heSolidThermo`, ADR-0018), and one
   `controlDict` with `regionSolvers`. The engine is `of14.multiRegion`; it runs `foamMultiRun`.
6. **Results are merged back onto the whole mesh by the agent.** When the solver ends, the agent
   writes `T` (every region), `U`, `p` and `p_rgh` for the whole mesh (`stokes_agent.regions`, the
   run job's `merge_regions` action), placing each region's cells by `constant/cellToRegion`; the
   split keeps a region's cells in their order (checked on the gyroid core: the temperature jump
   between neighbouring cells of one region is 0.34 K merged and 6.2 K with the order scrambled).
   A solid has no velocity (zero) and takes the lowest fluid pressure, so the colour range stays
   the fluids'. Every reader that exists (planes, iso-surfaces, probes, patches) then works
   unchanged: the VTK reader is told to read the whole mesh only (`extract.case_root`). Region
   meshes, and the interface surface itself, are not shown separately in this slice.
7. **Residuals and monitors carry the region.** A residual is keyed `<region>.<field>`
   (`foamMultiRun` puts the region's name first on each line). Flow rate and mean pressure and
   temperature are written per opening under `postProcessing/<region>/`, which the agent's result
   path rule now admits; the heat a stream gains or loses is `mdot cp (T_out - T_in)`.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Mesh each region separately and stitch | Non-conformal interfaces need `mappedWall` interpolation across unmatched faces; a single mesh gives matching faces for free. |
| Closed surface per body with `mode inside`, void as its own region | The outer patches lose the user's tag names (they become `<region>_to_void`), and the void has to be deleted and its interfaces retyped by hand. |
| Show each region's mesh and fields separately | Every agent reader would need a region argument at once; merging back gets the temperature through the wall on one cut plane, which is the picture a heat exchanger needs. |
| A `conjugate` switch on the mesh settings | The regions already say it; a switch could disagree with them. |

## Consequences

Two fluids and a wall solve end to end through the app's commands (measured 2026-11-20: the
gyroid core at 89 k cells, mesh 105 s, 300 iterations in 63 s; the hot stream lost 17.2 W and the
cold stream gained 16.6 W, not yet converged). In Guided mode the Fluid card lists every other
body with a material at one press (water, air, aluminium, copper); a body's role is read from its
region. The mesh job gains two programs
on the agent's allow-list (`topoSet`, `splitMeshRegions`) and the run one (`foamMultiRun`), each with
pinned arguments. Still to do: several bodies per region, cfMesh, layers on interface walls, radiation,
per-region turbulence choice, parallel runs (`decomposePar -allRegions`), and showing the interface
surface itself with its heat flux.

Measured again 2026-11-20 on a 525,724-cell mesh of the same core (0.6 mm cells, 1.2 mm wall), each fluid started at its inlet temperature and the wall at their mean: after 1,000 iterations the outlets moved 0.005 K (hot) and 0.013 K (cold) over the last 250, the hot stream gave 101.9 W and the cold stream took 97.3 W, a gap of 4.5 %. The same mesh started at 300 K everywhere was 41 % apart after 400 iterations, both outlets still moving: the wall was still warming. The multi-region snapshot is cached by its inputs (`build_conjugate_snapshot`): the staleness check rebuilt it on the session thread after every edit, 14 to 27 s, and the Mesh card's fields stayed disabled meanwhile.

**Decision 3 withdrawn (2026-11-20).** The topoSet rebuild of each fluid from its openings stopped the mesh twice in the walkthrough: `regionToCell` refuses a seed whose cell snapping removed, and at a 1 mm cell in a gyroid mouth one always was. The fluid is the cells snappyHexMesh keeps from the zone's inside point; the meshes and solves measured here (the prototype, the 89 k and 526 k-cell cores) solved with that.
