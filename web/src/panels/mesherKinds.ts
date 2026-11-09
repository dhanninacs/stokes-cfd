/**
 * Which meshers follow the part's own surfaces (ADR-0032), the one question the sizing, surface and
 * layer controls ask.
 *
 * Mirrors `SURFACE_MESHERS` and `CFMESH_MESHERS` in `stokes/mesh/model.py`. blockMesh meshes the
 * bounding box and has no surfaces to size or grow layers on; snappyHexMesh and the cfMesh family
 * (cartesian, tet, poly, poly-hexcore) all build from one surface per boundary tag.
 */

/** The cfMesh-driven volume fills: one joined surface and `system/meshDict`. */
export const CFMESH_MESHERS: readonly string[] = ["cfmesh", "cfmesh_tet", "cfmesh_poly", "polyhex"];

/** Every mesher that meshes the shape itself rather than its bounding box. */
export const SURFACE_MESHERS: readonly string[] = ["snappy", ...CFMESH_MESHERS];

/** Whether `mesher` follows the part's surfaces: local sizes, surfaces and wall layers apply. */
export function followsShape(mesher: string): boolean {
  return SURFACE_MESHERS.includes(mesher);
}

/** Whether `mesher` is one of the cfMesh volume fills. */
export function isCfMesh(mesher: string): boolean {
  return CFMESH_MESHERS.includes(mesher);
}

/**
 * The one line under the Cores field: how this mesher spends the count. `parallel` is the catalogue's
 * own word (`mpi`, `threads` or `serial`); an older server without it falls back on the mesher id.
 */
export function coresDescription(mesher: string, parallel: string): string {
  const how = parallel || (mesher === "snappy" ? "mpi" : isCfMesh(mesher) ? "threads" : "serial");
  if (how === "mpi") return "1 runs serially; larger counts use MPI.";
  if (how === "threads") return "cfMesh runs in one process with this many OpenMP threads.";
  return "blockMesh runs serially. Choose snappyHexMesh or a cfMesh type to mesh with several cores.";
}
