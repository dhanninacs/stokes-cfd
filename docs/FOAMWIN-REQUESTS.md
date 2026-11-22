# FoamWin: changes Stokes needs

A running list of what Stokes has found that should change in FoamWin (the separate GPL OpenFOAM-for-Windows
program Stokes drives through files and processes). Each entry says what was seen, where, what Stokes does
about it meanwhile, and what "fixed" means. The owner updates FoamWin; Stokes updates this list and drops its
workaround when a FoamWin release carries the fix.

| # | Status | Found | Area |
|---|---|---|---|
| F1 | open | 2026-11-20 | cfMesh tetrahedral cannot add boundary layers |
| F2 | open | 2026-11-20 | cfMesh writes debug CSV/VTK files into every case |
| F3 | open (unreleased fix exists) | 2026-11-18 | decomposePar/reconstructPar with generic patch fields |
| F4 | open | 2026-11-20 | cfMesh polyhedral (pMesh): unused points and a split domain on a lattice, with or without layers |
| F5 | open | 2026-11-20 | cfMesh tetMesh crashes: "Incorrect decomposeCell size" |

## F1. cfMesh tetrahedral (`tetMesh`) cannot add boundary layers

- **Seen:** tetMesh with `boundaryLayers` leaves unused points in the mesh, and `checkMesh` fails on them.
- **Stokes meanwhile:** `mesh.configure`/`mesh.generate` refuses tetrahedral + layers before running
  ("the tetrahedral type cannot grow boundary layers on this build ... Set layers to 0, or choose the
  poly-hexcore, polyhedral or hex-dominant type"). The plate-exchanger mesher comparison runs tet with 0 layers.
- **Fixed when:** tetMesh with `nLayers 3` on a simple duct and on the gyroid exchanger's hot stream gives a mesh
  `checkMesh` passes (no unused points), and Stokes can lift the refusal.

## F2. cfMesh writes diagnostic files into every case

- **Seen:** every cfMesh job folder gets about 16 debug files, e.g. `CFMITCH_V10F_internalSkewProvenance.csv`,
  `CFMITCH_V10I_type2Birth_1.csv`, `blCapAsymmetricEdgeAtlas.csv`, `blDropoutReasonAtlas_pre_ramp.csv`,
  `suppressedClusterVertices.csv`, `twoPatchEdgePoints_call1.vtk`, `rejectedNeighbourhood_call1.vtk`,
  `validityRejectedPoints_call1.vtk` (gyroid exchanger, cartesianMesh/pMesh/polyhex, 2026-11-20).
- **Stokes meanwhile:** nothing; the files are small and Stokes does not read them.
- **Fixed when:** a release build writes none of them unless a debug switch (environment variable or meshDict
  entry) asks for them.

## F3. decomposePar / reconstructPar with generic patch fields (parallel multi-region)

- **Seen:** FoamWin branch `fix/windows-libs-and-stop` (`4ee2e242`, 2026-11-18) fixes generic patch fields in
  `decomposePar`/`reconstructPar` on Windows, bare words in `-libs`/`FOAM_LIBS`, the `stopAtWriteNowSignal`
  handler, and symlinks replaced by copies. It is in no release (`docs/HANDOVER.md`).
- **Stokes meanwhile:** multi-region (conjugate) solves run on one process, so the decompose/reconstruct path is
  not exercised; the coupled interface fields are the kind of patch field the fix covers.
- **Fixed when:** a FoamWin release carries the branch; then a two-rank conjugate run (the plate exchanger) can be
  enabled and checked in Stokes.

## F4. cfMesh polyhedral (`pMesh`): unused points and a split domain on a lattice, with or without layers

- **Seen:** the gyroid plate exchanger's hot stream (one fluid body from an STL, 0.8 mm cells, 0.4 mm on the walls,
  3 layers from 0.03 mm, growth 1.2), 2026-11-20. `checkMesh` on the 276,532-cell result: 374,461 unused points and
  2 regions not connected by any face. A coarser run of the same case (0.8 mm, no wall refinement): 58,333 cells,
  106,721 unused points and 12 negative-volume cells (aspect ratio 1.8e139); 6 checks failed.
- **Not resolution, not the layers (2026-11-21):** the same case with the walls at 0.2 mm, run by hand from the staged
  surface: with 3 layers 1,431,526 cells, 1,919,284 unused points, 2 regions (670 s); with **no layers** 881,275 cells,
  2,285,999 unused points, 2 regions (564 s). `cartesianMesh` on the same surface and sizes gives one region and
  "Mesh OK" (see the closed item below), so the domain is meshable; `pMesh`'s polyhedral conversion is what leaves
  the points and the split.
- **Stokes meanwhile:** the Mesh card shows quality "not met", and a solve is refused on cells of negative volume
  (`solve.negative_cells`, 2026-11-20); unused points and a mesh in pieces stay warnings.
- **Fixed when:** `pMesh` on the same case (0.8 mm, walls 0.2 mm; with and without layers) leaves no unused points, no
  negative volumes and one region. Case files (staged `domain.stl`, `meshDict`) can be handed over on request.

## F5. cfMesh `tetMesh` crashes on a lattice: "Incorrect decomposeCell size"

- **Seen:** same case, tetrahedral, **no layers**, 2026-11-20: `tetMesh` exits 22 with
  `FOAM FATAL ERROR: Incorrect decomposeCell size 601064, mesh has 599932 cells`.
- **Stokes meanwhile:** the mesh fails with "tetMesh exited 22"; the user can pick another mesher.
- **Fixed when:** `tetMesh` meshes the exchanger's hot stream at 0.8/0.4 mm without the error.

## Closed (not FoamWin): cfMesh cartesian dropped part of a lattice domain

- **Seen:** gyroid exchanger hot stream, 0.8 mm cells, 0.4 mm on the walls: `cartesianMesh` 15,992 cells and
  `polyHexMesh` 18,831, the outlet patch missing.
- **Answer (2026-11-21):** resolution, not the build. The same `cartesianMesh` with the walls at 0.2 mm meshes the whole
  domain: 560,231 cells, one region, both openings, `checkMesh` "Mesh OK" (113 s), on the surface as staged and on a
  copy with its self-crossings repaired alike. The channels' necks into the headers need cells of 0.2 mm or less for
  the cartesian cut to keep them open. Stokes already refuses the solve on a lost opening and says to make the cells
  smaller (`solve.boundary_not_meshed`).
