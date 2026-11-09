/**
 * The pure rules of a source's per-vertex tint (T-211, for the fields T-218 draws on mesh patches).
 *
 * A part's `tint` attribute (materials.ts) is RGBA8, one word per vertex: a tag's colour with its
 * strength in alpha. The scene writes it from the source's face tint (tags). A field or a quality
 * measure has one value per vertex instead, and `SceneManager.setSourceVertexTint` keeps such an array
 * for the source. The scene re-applies tints whenever a part is made or replaced, so the array has to
 * live with the source and not only in the geometry; these functions decide which tint then wins and
 * which slice of the array a group reads. They are outside `SceneManager` so they run in Node.
 *
 * The array covers the source's groups in order, `4 * (vertices of all groups)` bytes: a mesh patch
 * has one group, so it is simply its vertex count times four.
 */

/** Where each group's vertices start in a source's array, and the vertex count of the whole source. */
export interface TintLayout {
  offsets: number[];
  total: number;
}

export function tintLayout(vertexCounts: readonly number[]): TintLayout {
  const offsets: number[] = [];
  let total = 0;
  for (const count of vertexCounts) {
    offsets.push(total);
    total += count;
  }
  return { offsets, total };
}

/**
 * Which tint a source draws: its vertex array when it has one that fits (`vertex`), the tag colours when
 * it has none (`face`), and `mismatch` when the array does not hold four bytes for every vertex, which
 * the caller answers by dropping the array, warning, and falling back to the face tint.
 */
export type TintChoice = "vertex" | "face" | "mismatch";

export function chooseTint(vertex: Uint8Array | undefined, totalVertices: number): TintChoice {
  if (vertex === undefined) return "face";
  return vertex.length === totalVertices * 4 ? "vertex" : "mismatch";
}

/** The bytes of one group: a view of its vertices' share of the source's array. */
export function groupSlice(vertex: Uint8Array, offset: number, vertexCount: number): Uint8Array {
  return vertex.subarray(offset * 4, (offset + vertexCount) * 4);
}

/** Triangles that have a tinted vertex (alpha above zero), for the scene's statistics. */
export function tintedTriangles(rgba: Uint8Array, indices: Uint32Array): number {
  let tinted = 0;
  for (let t = 0; t + 2 < indices.length; t += 3) {
    if (rgba[indices[t] * 4 + 3] !== 0 || rgba[indices[t + 1] * 4 + 3] !== 0 || rgba[indices[t + 2] * 4 + 3] !== 0) tinted++;
  }
  return tinted;
}
