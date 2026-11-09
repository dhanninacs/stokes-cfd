/**
 * The per-vertex face attribute of T-032 (`geometry.ts` `groupFaceIds`): the ordinals the id buffer
 * names a face with, the table that turns one back into the transport's face id, and the 16-bit
 * limit that costs the faces past it their face picking.
 */
import { describe, expect, it } from "vitest";
import { FACE_ID_ATTRIBUTE, FACE_ID_NONE, groupFaceIds, type LoadedGroup } from "./geometry";

/** A group of `triangles` triangles, one face per triangle id the caller names, vertices unshared. */
function group(faceIds: readonly number[] | null, faceBase = 0, faces = 0): LoadedGroup {
  const triangles = faceIds === null ? 1 : faceIds.length;
  const positions = new Float32Array(triangles * 9);
  const normals = new Float32Array(triangles * 9);
  const indices = new Uint32Array(triangles * 3);
  for (let i = 0; i < triangles * 3; i++) indices[i] = i;
  return {
    name: "patch",
    kind: "solid",
    color: [1, 1, 1],
    vertices: triangles * 3,
    triangles,
    offset: 0,
    faces,
    faceBase,
    quality: "fine",
    hasFaceIds: faceIds !== null,
    positions,
    normals,
    indices,
    faceIds: faceIds === null ? null : Uint32Array.from(faceIds),
  };
}

describe("the per-vertex face attribute", () => {
  it("gives the three vertices of a triangle the same ordinal, and numbers the faces it meets", () => {
    // Two triangles of face 40, one of face 41, in the transport's order.
    const { attribute, faces, truncated } = groupFaceIds(group([40, 40, 41], 40, 2));
    expect(attribute[0]).toBe(0);
    expect(attribute[1]).toBe(0);
    expect(attribute[2]).toBe(0);
    expect(attribute[3]).toBe(0);
    expect(attribute[6]).toBe(1);
    expect([...faces]).toEqual([40, 41]);
    expect(truncated).toBe(false);
  });

  it("names the attribute the schema says", () => {
    expect(FACE_ID_ATTRIBUTE).toBe("faceId");
  });

  it("gives a mesh without face ids no face at all", () => {
    const { attribute, faces, truncated } = groupFaceIds(group(null));
    expect([...attribute]).toEqual([FACE_ID_NONE, FACE_ID_NONE, FACE_ID_NONE]);
    expect(faces.length).toBe(0);
    expect(truncated).toBe(false);
  });

  it("loses the faces past 65,535 and says so", () => {
    // 65,536 single-triangle faces: the last one has no ordinal left to take.
    const ids = Array.from({ length: 65_536 }, (_, i) => i);
    const { attribute, faces, truncated } = groupFaceIds(group(ids));
    expect(faces.length).toBe(65_535);
    expect(truncated).toBe(true);
    expect(attribute[65_535 * 3]).toBe(FACE_ID_NONE);
    expect(attribute[65_534 * 3]).toBe(65_534);
  });
});
