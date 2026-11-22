/** The modeller bodies' filled sections: the answer decoded, and when they are asked for. */
import { describe, expect, it } from "vitest";
import { outlineOf, readSolidCuts, wantSolidCut } from "./solidCut";

const b64 = (bytes: ArrayBufferView): string => btoa(String.fromCharCode(...new Uint8Array(bytes.buffer, bytes.byteOffset, bytes.byteLength)));
const cut = { part: "n_plate", positions: b64(new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0])), triangles: b64(new Uint32Array([0, 1, 2])) };

describe("solid sections", () => {
  it("decode for the plane they were asked for, and refuse another plane or a bad index", () => {
    const found = readSolidCuts({ axis: "z", offset: 0.002, cuts: [cut] }, "z", 0.002);
    expect(found?.map((c) => [c.part, c.triangles.length])).toEqual([["n_plate", 3]]);
    expect(readSolidCuts({ axis: "z", offset: 0.003, cuts: [cut] }, "z", 0.002)).toBeNull();
    expect(readSolidCuts({ axis: "z", offset: 0.002, cuts: [{ ...cut, triangles: b64(new Uint32Array([0, 1, 7])) }] }, "z", 0.002)).toEqual([]);
  });

  it("are asked for while a section cuts the bodies, not on the Mesh stage or in the Results", () => {
    const section = { axis: "z" as const, offset: 0 };
    expect(wantSolidCut({ section, stage: "Geometry", meshDisplay: "surface" })).toBe(true);
    expect(wantSolidCut({ section, stage: "Mesh", meshDisplay: "surface" })).toBe(false);
    expect(wantSolidCut({ section, stage: "Results", meshDisplay: "surface" })).toBe(false);
    expect(wantSolidCut({ section: null, stage: "Geometry", meshDisplay: "surface" })).toBe(false);
  });

  it("outline a fill by the edges only one triangle uses", () => {
    // Two triangles of a square: the diagonal is shared, the four sides are the outline.
    const rim = outlineOf(new Uint32Array([0, 1, 2, 0, 2, 3]));
    expect(rim.length).toBe(8);
    const pairs = new Set(Array.from({ length: 4 }, (_, i) => [rim[2 * i], rim[2 * i + 1]].sort().join("-")));
    expect(pairs).toEqual(new Set(["0-1", "1-2", "2-3", "0-3"]));
  });
});
