/** The volume mesh at the section: the command's answer decoded, each region's fill, and when it is asked for. */
import { describe, expect, it } from "vitest";
import { readMeshCut, regionFill, SINGLE_FILL, SOLID_FILL, wantedCut } from "./meshCut";

const b64 = (bytes: ArrayBufferView): string => btoa(String.fromCharCode(...new Uint8Array(bytes.buffer, bytes.byteOffset, bytes.byteLength)));

const ready = {
  state: "ready",
  axis: "z",
  offset: 0.002,
  cells: 1,
  names: ["hot", "cold", "plate"],
  kinds: ["fluid", "fluid", "solid"],
  positions: b64(new Float32Array([0, 0, 0.002, 1, 0, 0.002, 1, 1, 0.002, 0, 1, 0.002])),
  triangles: b64(new Uint32Array([0, 1, 2, 0, 2, 3])),
  edges: b64(new Uint32Array([0, 1, 1, 2, 2, 3, 0, 3])),
  regions: b64(new Uint8Array([2, 2])),
};

describe("the mesh section", () => {
  it("decodes a ready answer into typed arrays", () => {
    const cut = readMeshCut(ready);
    expect(cut).not.toBeNull();
    expect(cut!.positions.length).toBe(12);
    expect(Array.from(cut!.triangles)).toEqual([0, 1, 2, 0, 2, 3]);
    expect(cut!.edges.length).toBe(8);
    expect(Array.from(cut!.regions)).toEqual([2, 2]);
  });

  it("refuses an answer that is not ready or whose arrays disagree", () => {
    expect(readMeshCut({ state: "cutting" })).toBeNull();
    expect(readMeshCut({ ...ready, regions: b64(new Uint8Array([2])) })).toBeNull();
    expect(readMeshCut({ ...ready, triangles: b64(new Uint32Array([0, 1, 9, 0, 2, 3])) })).toBeNull();
  });

  it("fills solids grey, hot warm, cold cool, and a one-region mesh plainly", () => {
    const names = ["hot", "cold", "plate"];
    const kinds = ["fluid", "fluid", "solid"];
    expect(regionFill(2, names, kinds)).toBe(SOLID_FILL);
    expect(regionFill(0, names, kinds)).not.toBe(regionFill(1, names, kinds));
    expect(regionFill(0, [], [])).toBe(SINGLE_FILL);
  });

  it("is asked for only while a section is on and the mesh is what the view shows", () => {
    const section = { axis: "y" as const, offset: 0.01, invert: false };
    expect(wantedCut({ section, stage: "Mesh", meshDisplay: "surface" })).toEqual({ axis: "y", offset: 0.01 });
    expect(wantedCut({ section, stage: "Geometry", meshDisplay: "cells" })).toEqual({ axis: "y", offset: 0.01 });
    expect(wantedCut({ section, stage: "Geometry", meshDisplay: "surface" })).toBeNull();
    expect(wantedCut({ section: null, stage: "Mesh", meshDisplay: "surface" })).toBeNull();
  });
});
