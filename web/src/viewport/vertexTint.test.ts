/** Which tint a source draws, and which bytes a group reads (`vertexTint.ts`, T-211 for T-218). */
import { describe, expect, it } from "vitest";
import { chooseTint, groupSlice, tintedTriangles, tintLayout } from "./vertexTint";

describe("chooseTint", () => {
  it("lets the vertex array win over the face tint when it fits", () => {
    expect(chooseTint(new Uint8Array(12 * 4), 12)).toBe("vertex");
  });

  it("uses the face tint when the source has no vertex array", () => {
    expect(chooseTint(undefined, 12)).toBe("face");
  });

  it("reports a length that is not four bytes per vertex, so the caller clears it", () => {
    expect(chooseTint(new Uint8Array(12 * 4 - 1), 12)).toBe("mismatch");
    expect(chooseTint(new Uint8Array(13 * 4), 12)).toBe("mismatch");
    expect(chooseTint(new Uint8Array(0), 12)).toBe("mismatch");
  });

  it("accepts an empty array for an empty source", () => {
    expect(chooseTint(new Uint8Array(0), 0)).toBe("vertex");
  });
});

describe("the layout of a source's groups", () => {
  it("starts each group where the one before it ends", () => {
    expect(tintLayout([4, 3, 5])).toEqual({ offsets: [0, 4, 7], total: 12 });
    expect(tintLayout([])).toEqual({ offsets: [], total: 0 });
  });

  it("slices the array by group, four bytes a vertex", () => {
    const rgba = Uint8Array.from({ length: 12 * 4 }, (_, i) => i);
    const layout = tintLayout([4, 3, 5]);
    const second = groupSlice(rgba, layout.offsets[1], 3);
    expect(second.length).toBe(12);
    expect(second[0]).toBe(16);
    expect(second[11]).toBe(27);
    // A view, not a copy: writing the geometry's attribute from it reads the caller's bytes.
    expect(second.buffer).toBe(rgba.buffer);
  });
});

describe("tintedTriangles", () => {
  it("counts the triangles with any vertex of alpha above zero", () => {
    const rgba = new Uint8Array(5 * 4);
    rgba[3 * 4 + 3] = 255; // vertex 3 is tinted
    const indices = Uint32Array.from([0, 1, 2, 1, 2, 3, 2, 3, 4]);
    expect(tintedTriangles(rgba, indices)).toBe(2);
    expect(tintedTriangles(new Uint8Array(5 * 4), indices)).toBe(0);
  });
});
