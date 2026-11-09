# ADR-0032 · A guided watertight-geometry meshing workflow, and the volume fills behind it

**Status:** proposed · 2026-11-07 · (relies on ADR-0001, ADR-0007, ADR-0024, ADR-0025)

## Context

The owner asked for pre-processing that feels like Fluent Meshing's *Watertight Geometry*
workflow: import, local sizing, surface mesh, describe geometry, boundaries, regions, boundary
layers, volume mesh, improve and check — with the volume types a Fluent user expects (tet,
hex-dominant/cut-cell, poly, poly-hexcore) built from the open-source tools available, run in the
background with progress, cancel and the user's `cores`.

ADR-0007 already chose snappyHexMesh, cfMesh, blockMesh and Gmsh as process-driven meshers.
Until this ADR only blockMesh and snappyHexMesh were wired. An audit on the FoamWin host
(OpenFOAM 14, `foamWin-release/OF14-win`, 2026-11-07) found every cfMesh utility, `polyDualMesh`
and a `gmsh.exe` installed, but the agent's allow-list (`ALLOWED_UTILITIES`) admitted only
blockMesh, snappyHexMesh, surfaceFeatures, checkMesh, decomposePar, reconstructPar and foamRun,
so none of them could run from Stokes.

**Measured on the Flanged pipe sample** (fluid body, tags `inlet`, `outlet`, `walls`; base size
5 mm, walls 2.5 mm; one surface per tag joined into one multi-solid STL):

| Type | Utilities | Cells | Max non-ortho | Max skew | checkMesh | Time (2 threads) |
|---|---|---|---|---|---|---|
| hex-dominant (cut-cell) | blockMesh → surfaceFeatures → snappyHexMesh | 54 k | 42.7° | 1.26 | OK | 38 s |
| hex-dominant (cartesian) | cartesianMesh | 30 k | 32.8° | 1.94 | OK | 14 s |
| tetrahedral | tetMesh | 698 k | 62.5° | 0.69 | OK | 43 s |
| polyhedral | pMesh | 189 k | 64.6° | 3.27 | OK | 52 s |
| poly-hexcore | cartesianMesh → polyDualMesh 70 | 45 k | 40.4° | 2.25 | OK | 13 s |

With three wall layers: cartesian 81 k (OK), poly-hexcore 96 k (OK, 38.9°), polyhedral 265 k
(OK). **tetMesh with layers left 132,239 unused points** (checkMesh fails); `improveMeshQuality`
and `renumberMesh` did not remove them, and `generateBoundaryLayers` after a plain `tetMesh` failed
two checks. **cfMesh writes every patch as `type empty`** unless `renameBoundary` sets the type.
`generateBoundaryLayers` run *after* `polyDualMesh` left unused points; layers grown by
`cartesianMesh` *before* the dual pass every check. A tet mesh's dual (`tetMesh → polyDualMesh`)
gave 134 k polyhedra but 4 faces over the skewness limit. cfMesh is OpenMP-parallel: tetMesh
took 16.8 s on one thread and 13.2 s on four; without `OMP_NUM_THREADS` the runtime takes every
logical core (24 here).

## Decision

1. **The guided workflow is the existing nine-task stepper** (`mesh.tasks`), named after Fluent's
   steps. No new command family is introduced; each Fluent step maps to commands that exist or
   to a gap listed in the table below, and the gaps are closed in later slices.

   | Fluent watertight step | Stokes task | Commands today | Gap |
   |---|---|---|---|
   | Import Geometry | geometry | `geometry.import`, `geometry.info`, `geometry.prepare` | none |
   | Add Local Sizing | sizing | `mesh.configure` (base/surface size), body and boundary sizes, `mesh.estimate` | curvature/proximity size functions; cfMesh `objectRefinements` for boxes |
   | Generate Surface Mesh | surface | the staged per-tag STL (`mesh.precheck` reports triangles and bytes), feature angle | no remeshed surface: the CAD tessellation is the surface mesh. A surface remesh (`surfaceLambdaMuSmooth`, Gmsh `-2`) is a later slice |
   | Describe Geometry (fluid/solid, capping) | bodies | `mesh.body_role`, `region.assign`, the fluid body | capping openings to make a fluid volume from a solid-only model |
   | Update Boundaries | boundaries | `boundary.assign`, `boundary.set`, `tag.*` | none |
   | Create Regions | bodies / boundaries | `region.create`, `region.assign` | multi-region (CHT) meshes in one volume run |
   | Add Boundary Layers | layers | `mesh.configure` (`layers`, `layer_mode`, `expansion_ratio`, `first_layer_height`), per-body counts | final-ratio has no cfMesh equivalent; tet + layers refused (see 4) |
   | Generate Volume Mesh | volume | `mesh.generate` (background job: progress, `mesh.cancel`, `mesh.log`) with the chosen type | none for the types in 2 |
   | Improve / Check | quality, fidelity | `mesh.quality` (checkMesh), the fidelity measurement | an *improve* action (`improveMeshQuality`) offered when checkMesh fails |

2. **The volume types are mesher choices** in `mesh_settings.mesher`, each a catalogue entry with
   `output_structures` in Fluent's vocabulary and a `parallel` word saying how `cores` is spent:

   | `mesher` | Fluent type | Tool chain | `cores` |
   |---|---|---|---|
   | `blockmesh` | structured hex (bounding box) | blockMesh | serial |
   | `snappy` | hex-dominant, cut-cell | blockMesh → surfaceFeatures → snappyHexMesh | MPI ranks |
   | `cfmesh` | hex-dominant, cartesian core | cartesianMesh | OpenMP threads |
   | `cfmesh_tet` | tetrahedral | tetMesh | OpenMP threads |
   | `cfmesh_poly` | polyhedral | pMesh | OpenMP threads |
   | `polyhex` | poly-hexcore | cartesianMesh → polyDualMesh | OpenMP threads |
   | `gmsh` | tetrahedral (alternative) | gmsh → gmshToFoam | not built |

   New ids are appended to the enum so stored projects keep their meaning.
3. **One cfMesh driver** (`stokes.mesh.drivers.cfmesh`) serves the four cfMesh types from the
   snappy snapshot: the per-tag STLs joined into one `constant/geometry/domain.stl`, sizes from
   the same precedence snappy's levels use (written as cfMesh `localRefinement` lengths),
   `renameBoundary` setting every patch's name and type, and walls-only `patchBoundaryLayers`
   with `thicknessRatio` = the expansion ratio and, in first-height mode,
   `maxFirstLayerThickness`. The cfMesh types mesh a `part` domain only in this slice; `box` and
   `enclosure` are refused by name (`mesh.domain_unsupported`).
4. **The tet type refuses layers** (`mesh.layers_unsupported`) with a message naming the types
   that can grow them, until a cfMesh build that does not leave unused points is measured.
5. **The agent admits `cartesianMesh`, `tetMesh`, `pMesh` and `polyDualMesh`** as utilities with
   no flags, trusts their `constant/polyMesh` output like blockMesh's, and admits exactly one
   positional argument for `polyDualMesh`: a feature angle matching 0–180 with up to three
   decimals. The dual's angle is the settings' feature angle, never below 70°.
6. **cfMesh steps carry `threads`** (1–256, cfMesh utilities only); the agent sets
   `OMP_NUM_THREADS` for that process, always explicitly, so the user's `cores` bounds the run.
   MPI cfMesh (decomposed surface, `-parallel`) is not used.
7. **Gmsh stays unbuilt** in this slice: `gmsh.exe` sits outside `FOAM_APPBIN`, which is the only
   place the native agent resolves tools, and tetMesh already covers tetrahedra. Wiring it means
   a `.geo` writer (surface loop from the joined STL, physical groups per tag), a native tool path
   for `gmsh`, and `gmshToFoam` with its patch-type fix-up.

## Alternatives considered

| Alternative | Why not |
|---|---|
| A separate `volume_fill` parameter beside `mesher` | Two enums that constrain each other; the catalogue already carries one entry per choice with its reason, and the front end already draws it. |
| Poly via `tetMesh → polyDualMesh` (Fluent's tet-to-poly) | Measured 4 faces over the skewness limit on the pipe; pMesh gives polyhedra that pass. Kept as a future option. |
| Layers with `generateBoundaryLayers` after the dual | Measured: unused points and two failed checks. |
| cfMesh over MPI | Needs a decomposed surface (`preparePar`) and an MS-MPI run per utility; OpenMP in one process already uses the cores and is cancelled by the same Job Object. |
| Port or link cfMesh | ADR-0001: GPL tools are processes, never linked. |

## Consequences

- The Mesh panel's mesher list offers six built types; sizing, surface and layer pages apply to
  every shape-following mesher (`SURFACE_MESHERS`), and the Cores line says MPI, threads or
  serial from the catalogue.
- The basis of a cfMesh result hashes the same parts as snappy's, so editing a size or a layer
  count marks it stale and Generate reruns it.
- To do next: external (`enclosure`) domains for cfMesh (a box surface around the part), an
  *Improve* action, curvature/proximity sizing, the Guided Mesh card's type picker, and Gmsh.
