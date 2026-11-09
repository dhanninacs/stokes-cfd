/**
 * The pure half of the id buffer (T-032): the pixel encoding the id material writes and the
 * read-back decodes, the box and lasso masks a gesture applies to the pixels it read, and the
 * point-in-polygon test behind the lasso. The pass itself needs a GPU and is exercised by hand
 * through the probe (`idPick`) and `tools/bench_viewport.py`.
 */
import * as THREE from "three";
import { describe, expect, it } from "vitest";
import {
  FACE_ID_NONE,
  ID_FRAGMENT,
  ID_VERTEX,
  PART_INDEX_NONE,
  packIdBytes,
  polygonMask,
  rectMask,
  rowCrossings,
  scanIdPixels,
  unpackIdBytes,
  type IdPixels,
} from "./idbuffer";

/** A read-back of the given pixels: `null` is a pixel no part covers. */
function idPixels(rows: readonly (readonly (readonly number[] | null)[])[], x = 0, y = 0): IdPixels {
  const height = rows.length;
  const width = rows[0]?.length ?? 0;
  const bytes = new Uint8Array(width * height * 4);
  rows.forEach((row, r) => {
    row.forEach((cell, c) => {
      if (cell !== null) packIdBytes(cell[0], cell[1], bytes, (r * width + c) * 4);
    });
  });
  return { x, y, width, height, bytes };
}

describe("the id pixel encoding", () => {
  it("round-trips a part index and a face id, low byte first", () => {
    for (const [part, face] of [
      [1, 0],
      [255, 1],
      [256, 255],
      [PART_INDEX_NONE, FACE_ID_NONE],
      [65_535, 65_535],
    ] as const) {
      const bytes = new Uint8Array(4);
      packIdBytes(part, face, bytes, 0);
      expect(unpackIdBytes(bytes, 0)).toEqual({ part, face });
    }
  });

  it("keeps the part in R,G and the face in B,A", () => {
    const bytes = new Uint8Array(4);
    packIdBytes(0x1234, 0xabcd, bytes, 0);
    expect([...bytes]).toEqual([0x34, 0x12, 0xcd, 0xab]);
  });
});

describe("the box and lasso mask", () => {
  const rows = [
    [
      [1, 4],
      [1, 4],
      null,
    ],
    [
      [2, 0],
      [2, 7],
      [3, 1],
    ],
  ] as const;

  it("names every part a rectangle covers, each once", () => {
    const mask = scanIdPixels(idPixels(rows), rectMask(0, 0, 3, 2));
    expect(mask.parts).toEqual([1, 2, 3]);
    expect(mask.faces.get(1)).toEqual([4]);
    expect(mask.faces.get(2)).toEqual([0, 7]);
    expect(mask.faces.get(3)).toEqual([1]);
  });

  it("leaves out the pixels the mask rejects", () => {
    const mask = scanIdPixels(idPixels(rows), rectMask(0, 0, 1, 2));
    expect(mask.parts).toEqual([1, 2]);
    expect(mask.faces.has(3)).toBe(false);
  });

  it("names no part for a pixel of the clear value", () => {
    const mask = scanIdPixels(idPixels([[null, [7, 3]]]), rectMask(0, 0, 2, 1));
    expect(mask.parts).toEqual([7]);
  });

  it("leaves a face out where the patch has none to name", () => {
    const mask = scanIdPixels(idPixels([[[9, FACE_ID_NONE]]]), rectMask(0, 0, 1, 1));
    expect(mask.parts).toEqual([9]);
    expect(mask.faces.has(9)).toBe(false);
  });

  it("takes the read-back's own origin into account", () => {
    // The same pixels, read starting a hundred pixels to the right and up.
    const mask = scanIdPixels(idPixels(rows, 100, 50), rectMask(100, 50, 103, 52));
    expect(mask.parts).toEqual([1, 2, 3]);
    expect(scanIdPixels(idPixels(rows, 100, 50), rectMask(0, 0, 10, 10)).parts).toEqual([]);
  });

  it("follows a lasso path rather than its bounds", () => {
    // A square path around the top-left pixel alone.
    const path = [0, 0, 1, 0, 1, 1, 0, 1];
    const mask = scanIdPixels(idPixels(rows), polygonMask(path));
    expect(mask.parts).toEqual([1]);
  });

  it("keeps a concave path out of the notch it cuts", () => {
    // A 3x3 square with its lower-right corner cut away.
    const path = [0, 3, 3, 3, 3, 2, 2, 2, 2, 0, 0, 0];
    const inside = polygonMask(path);
    expect(inside(0.5, 0.5)).toBe(true);
    expect(inside(2.5, 1.0)).toBe(false);
    expect(inside(1.5, 1.0)).toBe(true);
    expect(inside(2.5, 2.5)).toBe(true);
  });
});

describe("the row crossings of a polygon", () => {
  it("spans a square between its two edges", () => {
    expect(rowCrossings([0, 0, 4, 0, 4, 4, 0, 4], 2)).toEqual([0, 4]);
  });

  it("crosses only the rows the polygon covers", () => {
    expect(rowCrossings([0, 0, 4, 0, 4, 4, 0, 4], 5)).toEqual([]);
  });

  it("counts a vertex's row once", () => {
    // The apex row is where the two edges meet: an even-odd fill must still see two crossings.
    const xs = rowCrossings([0, 0, 2, 4, 4, 0], 0);
    expect(xs.length % 2).toBe(0);
  });
});

describe("the id material's clipping support", () => {
  it("its shaders ask three for the clip planes, in the chunks that exist", () => {
    // **Claude's defect 7c.** The id material is a hand-written `ShaderMaterial`, so
    // `renderer.clippingPlanes` reaches it ONLY through the clipping chunks — three injects those
    // into the materials it compiles itself, not into a raw ShaderMaterial. Without them the id pass
    // ignored the section and a box or lasso named faces the drawn frame had removed.
    //
    // Read through the material's own source so this fails if the includes are dropped, and check the
    // names against three's chunk table so a typo cannot pass as a working include.
    const vertex = ID_VERTEX;
    const fragment = ID_FRAGMENT;

    const chunks = THREE.ShaderChunk as unknown as Record<string, string>;
    for (const [name, source] of [
      ["clipping_planes_pars_vertex", vertex],
      ["clipping_planes_vertex", vertex],
      ["clipping_planes_pars_fragment", fragment],
      ["clipping_planes_fragment", fragment],
    ] as const) {
      expect(source).toContain(`#include <${name}>`);
      // A name that is not in the table leaves the `#include` line in the GLSL, which is a compile
      // error at runtime rather than a missing feature — the failure mode this asked-for check catches.
      expect(Object.prototype.hasOwnProperty.call(chunks, name)).toBe(true);
    }

    // The fragment must actually discard, and the vertex must feed it — otherwise the includes are
    // decoration. `vClipPosition` is the varying the chunks agree on.
    expect(chunks["clipping_planes_fragment"]).toContain("discard");
    expect(chunks["clipping_planes_pars_vertex"]).toContain("vClipPosition");
    // The fragment's pars chunk must come before its use, or the uniforms are undeclared. Both are
    // matched as full `#include <...>` lines: a bare `indexOf("clipping_planes_fragment")` also matches
    // inside `clipping_planes_pars_fragment`, so the first version of this compared 403 < 157 and
    // failed for the wrong reason.
    expect(fragment.indexOf("#include <clipping_planes_pars_fragment>")).toBeLessThan(
      fragment.indexOf("#include <clipping_planes_fragment>"),
    );
  });
});
