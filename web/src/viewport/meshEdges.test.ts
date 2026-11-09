/** The cell-edge codec of the mesh routes (`stokes/mesh/surfaces.py patch_edges`), read by `parseMeshEdges`. */
import { describe, expect, it } from "vitest";
import { parseMeshEdges } from "./meshEdges";

/** The bytes the server writes for `segments` (each six floats), with the header text given or made. */
function buffer(segments: number[][], header?: string): ArrayBuffer {
  let text = header ?? JSON.stringify({ version: 1, units: "m", segments: segments.length });
  text += " ".repeat((4 - (text.length % 4)) % 4);
  const head = new TextEncoder().encode(text);
  const out = new ArrayBuffer(4 + head.length + segments.length * 24);
  new DataView(out).setUint32(0, head.length, true);
  new Uint8Array(out, 4, head.length).set(head);
  const floats = new Float32Array(out, 4 + head.length, segments.length * 6);
  segments.forEach((s, i) => floats.set(s, i * 6));
  return out;
}

describe("parseMeshEdges", () => {
  it("gives six floats per segment, in order", () => {
    const out = parseMeshEdges(buffer([[0, 0, 0, 1, 0, 0], [1, 0, 0, 1, 2, 0], [1, 2, 0, 1, 2, 3]]));
    expect(out).toBeInstanceOf(Float32Array);
    expect(out.length).toBe(18);
    expect([...out.slice(0, 6)]).toEqual([0, 0, 0, 1, 0, 0]);
    expect([...out.slice(12)]).toEqual([1, 2, 0, 1, 2, 3]);
  });

  it("accepts a patch with no edges", () => {
    expect(parseMeshEdges(buffer([])).length).toBe(0);
  });

  it("refuses a truncated buffer, at every cut", () => {
    const whole = buffer([[0, 0, 0, 1, 0, 0], [1, 0, 0, 1, 2, 0]]);
    expect(() => parseMeshEdges(new ArrayBuffer(2))).toThrow(/too short/);
    for (const cut of [4, 12, whole.byteLength - 1, whole.byteLength - 24]) {
      expect(() => parseMeshEdges(whole.slice(0, cut))).toThrow();
    }
  });

  it("refuses a version it does not know", () => {
    expect(() => parseMeshEdges(buffer([], JSON.stringify({ version: 2, units: "m", segments: 0 })))).toThrow(/version/);
    expect(() => parseMeshEdges(buffer([], JSON.stringify({ units: "m", segments: 0 })))).toThrow(/version/);
  });

  it("refuses a header that is not JSON, or not an object", () => {
    expect(() => parseMeshEdges(buffer([], "{not json"))).toThrow(/not JSON/);
    expect(() => parseMeshEdges(buffer([], "[1,2,3]"))).toThrow(/not an object/);
  });

  it("refuses a header that runs past the buffer", () => {
    const out = new ArrayBuffer(8);
    new DataView(out).setUint32(0, 400, true);
    expect(() => parseMeshEdges(out)).toThrow(/truncated/);
  });

  it("refuses a segment count that disagrees with the length", () => {
    const real = [[0, 0, 0, 1, 0, 0]];
    expect(() => parseMeshEdges(buffer(real, JSON.stringify({ version: 1, units: "m", segments: 2 })))).toThrow(/does not match/);
    expect(() => parseMeshEdges(buffer(real, JSON.stringify({ version: 1, units: "m", segments: 0 })))).toThrow(/does not match/);
    expect(() => parseMeshEdges(buffer(real, JSON.stringify({ version: 1, units: "m", segments: -1 })))).toThrow(/segment count/);
    expect(() => parseMeshEdges(buffer(real, JSON.stringify({ version: 1, units: "m", segments: 1.5 })))).toThrow(/segment count/);
  });
});
