# ADR-0043 · Gmsh as a mesher: a `.geo` script, `gmshToFoam`, prism layers grown along the wall normals

**Status:** proposed · 2026-11-20 · (ADR-0001 GPL process boundary, ADR-0007 meshing strategy,
ADR-0024 mesh jobs, ADR-0025 native Windows, ADR-0032 the watertight workflow and its decision 7)

## Context

ADR-0007 chose Gmsh as one of the four meshers, driven as a process; ADR-0032 decision 7 left it
unbuilt because `gmsh.exe` is not in `FOAM_APPBIN`, where the native agent looked for every tool.
FoamWin v14.0.0-win.5 bundles Gmsh 4.15.2 in `<install>/gmsh/` and lists it in its handshake's
`tools`, and OpenFOAM's `gmshToFoam` is in `FOAM_APPBIN`. The user asked for Gmsh "as mesh driver
as well capable of boundary layer generation". Gmsh is GPL, so Stokes may only write files for it,
run it and read what it writes. A `.geo` file is a program: Gmsh's language can run shell commands
(`SystemCall`, `Shell`), start other programs and read or write any file. Everything below was
measured with the bundled Gmsh on a 20x10x10 mm duct and a 10 mm by 20 mm pipe on 2026-11-20.

## Decision

1. **Mesher id `gmsh`** ("Tetrahedral + prism layers (Gmsh)", `part` domain, `tetrahedral`,
   serial), driven by `stokes.mesh.drivers.gmsh` from the snappy record (`build_gmsh_snapshot`):
   one staged STL per tag, the per-surface sizes and the wall layer counts, so switching mesher
   keeps the user's settings. Steps: `gmsh -nopopup system/domain.geo -`, `gmshToFoam
   constant/domain.msh`, one `foamDictionary` per wall or symmetry patch (gmshToFoam writes every
   patch as `patch`, measured) and `checkMesh`.
2. **The script** merges the tag STLs, joins them at shared edges (`Coherence Mesh`; without it the
   3D mesher failed along every shared edge), and `ClassifySurfaces` + `CreateGeometry` at the
   settings' feature angle so Gmsh can remesh the surfaces at the requested sizes. Classification
   drops physical groups and renumbers surfaces, so **each tag's share is counted first** (classify
   it alone, count, `Delete Model`); measured, classification keeps tags apart (two coplanar tags
   stayed two) and numbers the new surfaces in merge order, and the script stops with an error if
   the counts do not add up. Physical surfaces are named after the tags (the patch names) and one
   physical volume `domain` becomes a cell zone. MSH 2.2 ASCII is saved for `gmshToFoam`.
3. **Sizes**: the base size is `Mesh.MeshSizeMax`; a surface asking for less gets a `Distance` +
   `Threshold` field growing back to the base size over three base cells (`MeshSize{PointsOf{}}`
   does nothing on these discrete surfaces, measured). `Mesh.OptimizeNetgen` stays on (worst
   non-orthogonality 65 to 55 degrees on the duct and on the layered pipe).
4. **Layers** use Gmsh's boundary-layer extrusion of all wall surfaces in one command (no vector:
   along the averaged normals), `Recombine`d into prisms. The heights come from the layer settings:
   first-height mode from the first height and the expansion ratio; final-ratio mode with the last
   layer that fraction of the smallest wall cell size (snappy's relative sizing). The sign follows
   the staged surface's orientation (its signed volume), because the extrusion follows the STL
   normals (measured: a flipped surface grew its layers outside). **Walls only**: each opening is
   rebuilt as a `Plane Surface` bounded by the layers' inner edge (the side surfaces' top curves,
   chained into one loop in the script), and its patch is that plane plus the layers' side faces.
5. **Refused up front** (`mesh.layers_unsupported`): walls with different layer counts (one
   extrusion grows one stack) and, under layers, an opening that is not planar. A non-`part` domain
   is refused as for cfMesh (`mesh.domain_unsupported`).
6. **The agent** admits `gmsh` and `gmshToFoam`, each with one pinned positional path
   (`system/domain.geo`, `constant/domain.msh`), `gmsh` with `-nopopup` and `-` only; widens
   `foamDictionary -set` to `symmetry|wall`; trusts `gmshToFoam`'s polyMesh; finds the native
   `gmsh.exe` from the handshake only inside the install's own folder, falling back to
   `<install>/gmsh/gmsh.exe` (on Linux `gmsh` on the captured PATH). **A staged `.geo` is linted
   by a closed grammar** (`stokes_agent.geo_lint`), not the dictionary rules: every line must be a
   statement shape the driver writes, `Merge`/`Save` name only the pinned paths, a capitalised word
   in an expression must be `Pi`, `Abs`, `Surface` or `Curve`, `#` is only the list-size operator,
   and strings appear only in the shapes that need them.
7. **A failed Gmsh step says why**: the `gmsh` log parser keeps Gmsh's errors and the layer count
   the script printed, and the mesh service replaces "gmsh exited 1" with Gmsh's first error and,
   for the layer fold of decision 8, the measured cause and what to change.
8. **The measured limit of the layers.** At a sharp inside corner between two walls (the duct's
   90 degree edges) the layer fronts fold into each other once the stack is thicker than about 0.6
   of the wall cell (passed at 0.6, failed at 0.7 with 1 mm cells; Gmsh stops with "overlapping
   facets" or "a segment and a facet intersect"). On the duct at the defaults (three layers, final
   ratio 0.3, 0.76 of the 0.75 mm wall cell) the job fails with that explanation. Below the limit
   the layered duct passes checkMesh but reaches 66 to 86 degrees of non-orthogonality beside the
   corners, over the 65 degree bar, so the result is a warning. Smooth walls have room: the pipe
   at the defaults gave 21,798 prisms and 55 degrees, and stacks of 1.5 wall cells passed.
   `ScaleLastLayer` avoided the fold but squashed the last layer to aspect ratio 3,000, so it is
   not used.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Import the Gmsh API (Python or C) | GPL linking (ADR-0001). The script and the process are the boundary. |
| Mesh straight from the STL triangles (no classification) | The CAD tessellation becomes the surface mesh: two triangles per face of the duct, sizes ignored. |
| Name the classified surfaces by a bounding-box query | Not robust beyond boxes; the per-tag count is exact and checked. |
| Grow layers on every surface, openings included | Simplest script, but layers on inlets and outlets break Stokes's walls-only rule. |
| A tetrahedral mesh, then snappyHexMesh's layer pass on its walls | Possible later; it is snappy's layer quality on tetrahedra, not Gmsh's prisms, and was not what was asked. |
| `ScaleLastLayer` for the corners | Measured: aspect ratio 3,000 in the last layer, checkMesh failed. |
| A word blocklist for the `.geo` lint | A blocklist is the commands somebody thought of; the grammar is closed like `flags.py`. |

## Consequences

- The Mesh card offers seven built meshers. Gmsh is "not available" on a host whose install has no
  `gmsh` or `gmshToFoam`, naming the program (`mesher_needs`).
- The engine test `tests/engine/test_mesh_gmsh_engine.py` meshes the duct without and with layers,
  the pipe with layers, and pins the failure message of the corner limit.
- Not done: external (`enclosure`) domains; per-wall layer counts; non-planar openings under
  layers; layers on walls next to an unlayered wall; Gmsh over several threads (HXT); a pre-check
  of the corner limit from the surface (today the job fails and says why).

## Addendum 2026-11-21 · lattice bodies: the tessellation kept, the surface repaired

The gyroid plate exchanger's hot stream (one implicit body: gyroid channel lattice, box headers,
cylinder ports; 241,930 display triangles, median edge 0.2 mm; base 0.8 mm, surface 0.4 mm) failed
both ways: without layers the classified surface (10,932 patches) took 7 minutes to remesh and
crossed itself; with layers the per-tag counts disagreed (10,932 against 10,935). Measured causes and
decisions:

9. **Tags are kept apart by `CreateTopology` before classifying.** After `Coherence Mesh` joined
   the tags, classification merged tags that meet on a smooth surface (two coplanar tags became
   one), which is what broke the counts. A curve on every tag boundary first keeps them apart.
10. **The staged surface is repaired before Gmsh sees it** (`stokes.geometry.surface_repair`). The
    implicit body's tessellation had 440 pairs of triangles crossing each other (nearly parallel,
    0.05 mm apart) and faces lying back to back; Gmsh's boundary recovery stops on each. The repair
    finds every crossing and back-to-back fold, smooths the vertices around them (each step capped
    at half the vertex's shortest edge; tag outlines never move) and checks again: two rounds,
    8,470 vertices moved (the largest by 1.06 mm, on a coarse flat face), 145 s on this surface. It also drops a closed piece apart from the body
    that faces outwards (a 2.6 mm pocket of fluid the lattice cut off; snappyHexMesh drops it too,
    keeping only the region round its inside point).
11. **A tessellation as fine as the sizes asked for is kept as the wall** (`uses_tessellation`:
    median edge at most the smallest requested size): no classification or remesh, only
    `CreateTopology` to split each tag into its pieces and give them boundary curves. Gmsh's
    reparametrisation of the lattice fails ("Wrong topology of boundary mesh for parametrization",
    measured again after the repair), so the remesh is not an option there. The wall cells are then
    the display triangles: finer than asked where the tessellation is fine (0.2 mm against 0.4 mm),
    coarse where it is coarse (flat header faces, edges to 8 mm), and of their quality.
12. **Layers on a kept tessellation are refused when it has knife folds** (edges folding back
    sharper than 25 degrees): the exchanger has 8,152 and its layers crossed at once ("overlapping
    facets"). A smooth fine surface layers well (a 96-segment pipe: 34,560 prisms, checkMesh OK).

**Measured on the exchanger (no layers):** 504,582 tetrahedra in 347 s of Gmsh, one region, but
checkMesh failed two checks (max skewness 12, an aspect ratio of 1,350) with non-orthogonality
averaging 46 degrees (98,306 faces over 70). The flow solve went to NaN at the 10th iteration, so
there is no pressure drop to set against snappyHexMesh's 2,065 Pa. The display tessellation is the
limit: 7 % of its triangles have a shape quality under 0.1 and 20 % under 0.3. Making Gmsh useful on
lattices needs a CFD-quality surface (a surface remesh that copes with high genus, or a cleaner
tessellation from the implicit mesher); Gmsh's MMG3D adaptation was tried and had not finished
after an hour. **Recommendation: snappyHexMesh or cfMesh for lattice bodies; Gmsh for CAD bodies.**
