import { describe, expect, it } from "vitest";
import { COLORMAPS } from "../state/colormap";
import { LineMaterial } from "three/addons/lines/LineMaterial.js";
import { streamlineLines, streamlineSegments } from "./streamlineLayer";

/**
 * The drawn form of a streamline, pure so it can be checked without a renderer.
 *
 * **Two rules matter and both are easy to get wrong**: a segment's colour is the speed ACROSS it (not
 * one end), and every line in a picture uses ONE range - otherwise two lines at the same speed come out
 * different colours.
 */
const line = (points: [number, number, number][], speeds: number[]) => ({ points, speeds });

describe("streamlineSegments", () => {
  it("makes one segment per gap, with six floats of position and six of colour", () => {
    const { positions, colors } = streamlineSegments(
      [line([[0, 0, 0], [1, 0, 0], [2, 0, 0]], [1, 2, 3])],
      { min: 1, max: 3 },
    );
    expect(positions).toHaveLength(12); // two segments x 6
    expect(colors).toHaveLength(12);
    expect(Array.from(positions.slice(0, 6))).toEqual([0, 0, 0, 1, 0, 0]);
  });

  it("colours a segment by the MEAN of its ends, not by one of them", () => {
    // **Discriminating on purpose**: the first version compared a speed-0 segment with a speed-1 one,
    // which is true whether the colour comes from the mean, the start or the end - so the mutation
    // check passed when it should have failed. Here the segment's ends are 0 and 1 on a 0..1 range, so
    // the mean (0.5) is a colour neither end has.
    const { colors } = streamlineSegments([line([[0, 0, 0], [1, 0, 0]], [0, 1])], { min: 0, max: 1 });
    const mid = streamlineSegments([line([[0, 0, 0], [1, 0, 0]], [0.5, 0.5])], { min: 0, max: 1 });
    expect(Array.from(colors.slice(0, 3))).toEqual(Array.from(mid.colors.slice(0, 3)));
  });

  it("gives BOTH ends of a segment the same colour", () => {
    // A gradient along a few millimetres would be a claim the data cannot support.
    const { colors } = streamlineSegments([line([[0, 0, 0], [1, 0, 0]], [0.2, 0.8])], { min: 0, max: 1 });
    expect(Array.from(colors.slice(0, 3))).toEqual(Array.from(colors.slice(3, 6)));
  });

  it("uses ONE range for every line, so equal speeds look equal", () => {
    const a = streamlineSegments([line([[0, 0, 0], [1, 0, 0]], [0.5, 0.5])], { min: 0, max: 1 });
    const b = streamlineSegments([line([[0, 0, 0], [1, 0, 0]], [0.5, 0.5])], { min: 0, max: 1 });
    expect(Array.from(a.colors)).toEqual(Array.from(b.colors));
  });

  it("skips a line that cannot be drawn, rather than guessing", () => {
    // One point has no segment; a length mismatch means the speeds are not about these points.
    expect(streamlineSegments([line([[0, 0, 0]], [1])], { min: 0, max: 1 }).positions).toHaveLength(0);
    expect(streamlineSegments([line([[0, 0, 0], [1, 0, 0]], [1])], { min: 0, max: 1 }).positions).toHaveLength(0);
  });

  it("colours with the colour map it is given, reversed or not, and viridis when none is", () => {
    const l = [line([[0, 0, 0], [1, 0, 0]], [1, 1])]; // the top of a 0..1 range
    const range = { min: 0, max: 1 };
    const rgb = (c: Float32Array) => Array.from(c.slice(0, 3)).map((v) => Math.round(v * 255));
    expect(rgb(streamlineSegments(l, range).colors)).toEqual(rgb(streamlineSegments(l, range, "viridis").colors));
    // The top of a map is its last stop; reverse reads it from the other end, so the top becomes the first stop.
    const top = rgb(streamlineSegments(l, range, "magma").colors);
    expect(top).toEqual(COLORMAPS.magma.stops[COLORMAPS.magma.stops.length - 1].map((v) => Math.round(v)));
    expect(rgb(streamlineSegments(l, range, "magma", true).colors)).toEqual(COLORMAPS.magma.stops[0].map((v) => Math.round(v)));
    expect(top).not.toEqual(rgb(streamlineSegments(l, range).colors));
  });

  it("has nothing to draw for no lines", () => {
    expect(streamlineSegments([], { min: 0, max: 1 }).positions).toHaveLength(0);
  });
});

describe("streamlineLines", () => {
  const line = { points: [[0, 0, 0], [0, 0, 0.1], [0, 0, 0.2]] as [number, number, number][], speeds: [0.1, 0.2, 0.3] };

  it("draws with a LineMaterial, the only material that stretches LineSegments2's instanced quads", () => {
    // A LineBasicMaterial cast to the type drew one ~1 m quad per segment over the whole screen: on pipe3 that hung
    // the Intel GPU (D3D11 DEVICE_HUNG) and froze the Windows app (2026-11-03).
    const object = streamlineLines([line], { min: 0.1, max: 0.3 }, 2, "viridis", false, { width: 800, height: 600 });
    expect(object).not.toBeNull();
    const material = object!.material as LineMaterial;
    expect(material).toBeInstanceOf(LineMaterial);
    expect(material.vertexColors).toBe(true);
    expect(material.linewidth).toBe(2);
    expect([material.resolution.x, material.resolution.y]).toEqual([800, 600]);
    // Depth-tested: volume lines have a front and a back (2026-11-04, "not volumetric").
    expect(material.depthTest).toBe(true);
    expect(material.depthWrite).toBe(true);
  });
  it("draws nothing for no segments", () => {
    expect(streamlineLines([{ points: [[0, 0, 0]], speeds: [0] }], { min: 0, max: 1 })).toBeNull();
  });
});
