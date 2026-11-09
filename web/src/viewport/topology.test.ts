/**
 * The topology record parser (ADR-0022 §15.2) and its projection helpers.
 *
 * The bytes come over the wire, so these tests are as much about refusing bad input as about
 * reading good input: a count that overruns the payload, a wrong version, non-SI units or a
 * truncated frame must each be an error rather than a partial or over-allocated read.
 */

import { describe, expect, it } from "vitest";

import {
  TOPOLOGY_VERSION,
  canvasCssPoint,
  TopologyFormatError,
  parseTopology,
  projectPoint,
  projectPolyline,
} from "./topology";
import { distanceToRunsSq } from "./topologypick";
import { candidatePatches } from "./scene";

/** Build a payload the way the server's codec does, so the test does not drift from the format. */
function payload(
  header: Record<string, unknown>,
  edgePoints: number[][][],
): ArrayBuffer {
  const body = new TextEncoder().encode(JSON.stringify(header));
  const padding = (4 - (body.length % 4)) % 4;
  const headerLength = body.length + padding;
  const pointCount = edgePoints.reduce((n, e) => n + e.length, 0);
  const buffer = new ArrayBuffer(4 + headerLength + pointCount * 12);
  const view = new DataView(buffer);
  view.setUint32(0, headerLength, true);
  new Uint8Array(buffer, 4, body.length).set(body);
  let offset = 4 + headerLength;
  for (const edge of edgePoints) {
    for (const p of edge) {
      view.setFloat32(offset, p[0], true);
      view.setFloat32(offset + 4, p[1], true);
      view.setFloat32(offset + 8, p[2], true);
      offset += 12;
    }
  }
  return buffer;
}

function edgeHeader(id: number, points: number) {
  return {
    id,
    length: 0.03,
    centre: [0, 0, 0.015],
    start: [0, 0, 0],
    end: [0, 0, 0.03],
    tangent: [0, 0, 1],
    curve: "GeomAbs_Line",
    points,
    pickable: true,
    // T-124a: version 2 carries each edge's bordering faces.
    faces: [0, 1],
  };
}

function goodHeader(overrides: Record<string, unknown> = {}) {
  return {
    version: TOPOLOGY_VERSION,
    part: "n_0000000a",
    basis: "abc123",
    units: "m",
    scale: 0.001,
    // T-124a: the face count, which range-checks the edges' face ids.
    faces: 2,
    // v3: per-face areas and the part's solid bodies.
    faceSizes: [
      { id: 0, area: 0.0009, centroid: [0, 0, 0.015] },
      { id: 1, area: 0.0009, centroid: [0, 0, 0.015] },
    ],
    solids: [{ volume: 0.000027, centroid: [0, 0, 0.015], area: 0.0054 }],
    edges: [edgeHeader(0, 2)],
    vertices: [
      { id: 0, position: [0, 0, 0], degree: 1, edges: [0], lengths: [0.03] },
      { id: 1, position: [0, 0, 0.03], degree: 1, edges: [0], lengths: [0.03] },
    ],
    ...overrides,
  };
}

describe("parseTopology", () => {
  it("reads a well-formed record", () => {
    const record = parseTopology(payload(goodHeader(), [[[0, 0, 0], [0, 0, 0.03]]]));
    expect(record.part).toBe("n_0000000a");
    expect(record.basis).toBe("abc123");
    expect(record.edges).toHaveLength(1);
    expect(record.edges[0].curve).toBe("GeomAbs_Line");
    expect(record.edges[0].pointsCount).toBe(2);
    // The polyline is float32, so compare at its precision rather than exactly.
    expect(record.edges[0].points[5]).toBeCloseTo(0.03, 6);
    expect(record.edges[0].points[0]).toBe(0);
    expect(record.vertices).toHaveLength(2);
    expect(record.vertices[1].degree).toBe(1);
  });

  it("reads a record with no edges or vertices", () => {
    const record = parseTopology(payload(goodHeader({ edges: [], vertices: [] }), []));
    expect(record.edges).toEqual([]);
    expect(record.vertices).toEqual([]);
  });

  it("refuses a payload shorter than its header length", () => {
    const buffer = new ArrayBuffer(4);
    new DataView(buffer).setUint32(0, 100, true);
    expect(() => parseTopology(buffer)).toThrow(TopologyFormatError);
  });

  it("refuses an unknown version, as §15.2 requires", () => {
    const bad = payload(goodHeader({ version: 99 }), [[[0, 0, 0], [0, 0, 1]]]);
    expect(() => parseTopology(bad)).toThrow(/version/);
  });

  it("refuses units that are not metres", () => {
    const bad = payload(goodHeader({ units: "mm" }), [[[0, 0, 0], [0, 0, 1]]]);
    expect(() => parseTopology(bad)).toThrow(/metres/);
  });

  it("refuses a declared point count that does not match the frames", () => {
    // The header says 3 points but the frames hold 2.
    const bad = payload(goodHeader({ edges: [edgeHeader(0, 3)] }), [[[0, 0, 0], [0, 0, 1]]]);
    expect(() => parseTopology(bad)).toThrow(/do not fill/);
  });

  it("refuses a header that is not JSON", () => {
    const buffer = new ArrayBuffer(8);
    const view = new DataView(buffer);
    view.setUint32(0, 4, true);
    new Uint8Array(buffer, 4, 4).set([0xff, 0xfe, 0xfd, 0xfc]);
    expect(() => parseTopology(buffer)).toThrow(/not JSON/);
  });

  it("refuses an edge id that is not its position", () => {
    const bad = payload(goodHeader({ edges: [edgeHeader(7, 2)] }), [[[0, 0, 0], [0, 0, 1]]]);
    expect(() => parseTopology(bad)).toThrow(/edge id/);
  });

  it("refuses a vertex whose degree disagrees with its edge list", () => {
    const bad = payload(
      goodHeader({
        vertices: [{ id: 0, position: [0, 0, 0], degree: 5, edges: [0], lengths: [0.03] }],
      }),
      [[[0, 0, 0], [0, 0, 1]]],
    );
    expect(() => parseTopology(bad)).toThrow(/degree/);
  });

  it("refuses a centre that is not three finite numbers", () => {
    const bad = payload(goodHeader({ edges: [{ ...edgeHeader(0, 2), centre: [0, 0] }] }), [
      [[0, 0, 0], [0, 0, 1]],
    ]);
    expect(() => parseTopology(bad)).toThrow(/centre/);
  });

  it("refuses a non-positive scale", () => {
    const bad = payload(goodHeader({ scale: 0 }), [[[0, 0, 0], [0, 0, 1]]]);
    expect(() => parseTopology(bad)).toThrow(/scale/);
  });

  it("refuses an unreasonable point count before allocating", () => {
    const bad = payload(goodHeader({ edges: [{ ...edgeHeader(0, 99_999) }] }), []);
    expect(() => parseTopology(bad)).toThrow(/points out of range/);
  });

  it("defaults pickable to true and curve to empty when absent", () => {
    const e = edgeHeader(0, 2);
    delete (e as Record<string, unknown>).pickable;
    delete (e as Record<string, unknown>).curve;
    const record = parseTopology(payload(goodHeader({ edges: [e] }), [[[0, 0, 0], [0, 0, 1]]]));
    expect(record.edges[0].pickable).toBe(true);
    expect(record.edges[0].curve).toBe("");
  });

  it("reads a curved edge's polyline in order", () => {
    const points = [[0, 0, 0], [0.01, 0.01, 0], [0.02, 0, 0]];
    const record = parseTopology(payload(goodHeader({ edges: [edgeHeader(0, 3)] }), [points]));
    expect(record.edges[0].pointsCount).toBe(3);
    expect(record.edges[0].points[3]).toBeCloseTo(0.01, 6);
    expect(record.edges[0].points[4]).toBeCloseTo(0.01, 6);
  });
});

describe("projectPoint", () => {
  // An identity view-projection: NDC equals the point, so the pixel mapping is easy to check.
  const identity = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];

  it("maps NDC (-1,-1) to the bottom-left corner in pixel terms", () => {
    const p = projectPoint({ x: -1, y: -1, z: 0 }, identity, 800, 600);
    expect(p).toMatchObject({ x: 0, y: 600, visible: true });
  });

  it("maps the origin to the centre", () => {
    const p = projectPoint({ x: 0, y: 0, z: 0 }, identity, 800, 600);
    expect(p.x).toBeCloseTo(400, 9);
    expect(p.y).toBeCloseTo(300, 9);
  });

  it("carries depth through as NDC z", () => {
    expect(projectPoint({ x: 0, y: 0, z: 0.5 }, identity, 100, 100).depth).toBeCloseTo(0.5, 9);
  });

  it("marks a point behind the camera invisible rather than projecting it", () => {
    // w = -1 (the fourth row of an identity is [0,0,0,1], so use a matrix with w = -1).
    const behind = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, -1];
    const p = projectPoint({ x: 0, y: 0, z: 0 }, behind, 800, 600);
    expect(p.visible).toBe(false);
  });

  it("projects a polyline and reports whether any point was in front", () => {
    const flat = new Float32Array([-1, -1, 0, 1, 1, 0]);
    const { runs, visible } = projectPolyline(flat, 2, identity, 100, 100);
    expect(visible).toBe(true);
    // One run: every point is in front.
    expect(runs).toHaveLength(1);
    expect(runs[0]).toHaveLength(2);
    expect(runs[0][0]).toMatchObject({ x: 0, y: 100 });
    expect(runs[0][1]).toMatchObject({ x: 100, y: 0 });
  });
});


describe("projectPolyline: points behind the camera (A1)", () => {
  // Column-major (three.js `Matrix4.elements`): `w = m[3]x + m[7]y + m[11]z + m[15]`. With
  // `m[11] = -1`, any point with z > 0 has w < 0 — behind the camera — and z < 0 is in front.
  const behindIfPositiveZ = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, -1, 0, 0, 0, 0];

  it("never emits the (0,0) corner for a point behind the camera", () => {
    // The first vertex is behind, the second in front. The original bug was a phantom segment from
    // the viewport's top-left corner to the visible end.
    const pts = new Float32Array([0, 0, 1, 5, 5, -1]);
    const { runs, visible } = projectPolyline(pts, 2, behindIfPositiveZ, 100, 100);
    expect(visible).toBe(true);
    for (const run of runs) {
      for (const point of run) {
        expect(point.x === 0 && point.y === 0).toBe(false);
      }
    }
  });

  it("reports nothing visible for a polyline entirely behind the camera", () => {
    const pts = new Float32Array([0, 0, 1, 5, 5, 2]);
    const { runs, visible } = projectPolyline(pts, 2, behindIfPositiveZ, 100, 100);
    expect(visible).toBe(false);
    expect(runs).toEqual([]);
  });

  it("still projects a polyline entirely in front", () => {
    const pts = new Float32Array([-1, -1, -1, 1, 1, -1]);
    const { runs, visible } = projectPolyline(pts, 2, behindIfPositiveZ, 100, 100);
    expect(visible).toBe(true);
    expect(runs).toHaveLength(1);
    expect(runs[0][0]).toMatchObject({ x: 0, y: 100 });
    expect(runs[0][1]).toMatchObject({ x: 100, y: 0 });
  });

  it("returns TWO runs when an edge leaves the frustum and re-enters, joined by nothing", () => {
    // The defect the first fix left: pushing the crossing outward from the screen centre invented a
    // segment joining the two visible pieces, so the hit test accepted a pointer in the gap. The
    // crossing is now solved in WORLD space and projected, so a point on the near plane (w = 0)
    // projects to a very large coordinate in the correct direction — which is what an infinite
    // projection means, and is not a made-up position.
    const pts = new Float32Array([
      -1, 0, -1, // in front, screen x 0
      0, 0, 2, // behind
      1, 0, -1, // in front again, screen x 100
    ]);
    const { runs, visible } = projectPolyline(pts, 3, behindIfPositiveZ, 100, 100);
    expect(visible).toBe(true);
    expect(runs).toHaveLength(2);
    expect(runs[0].length).toBeGreaterThanOrEqual(2);
    expect(runs[1].length).toBeGreaterThanOrEqual(2);

    // The first run leaves toward the left (its crossing projects far negative); the second comes
    // back from the right. Neither is at the screen-centre-derived position the old code invented.
    const leaves = runs[0][runs[0].length - 1];
    const reenters = runs[1][0];
    expect(leaves.x).toBeLessThan(-1000);
    expect(reenters.x).toBeGreaterThan(1000);

    // The point that matters: the two runs are NOT joined. A pointer in the gap between them — where
    // the fabricated segment used to be — is far from the edge.
    const inGap = { x: 50, y: 50, depth: 0 };
    const gapDistance = Math.sqrt(distanceToRunsSq(inGap, runs));
    expect(gapDistance).toBeGreaterThan(40);
  });
});


describe("canvasCssPoint (A5)", () => {
  it("converts to canvas-relative CSS pixels and does NOT multiply by the device pixel ratio", () => {
    // The defect: the hover path passed `(clientX - left) * dpr` while `topologyAt` multiplies by the
    // ratio itself, so on a 1.25x display the hover sampled 1.25x too far from the origin.
    const rect = { left: 100, top: 50 };
    expect(canvasCssPoint(300, 200, rect)).toEqual({ x: 200, y: 150 });
    // The scale is deliberately absent: doubling the inputs doubles the output, i.e. it is a pure
    // translation, with no ratio folded in.
    expect(canvasCssPoint(400, 300, rect)).toEqual({ x: 300, y: 250 });
  });

  it("handles a rect at the origin and a negative offset", () => {
    expect(canvasCssPoint(10, 20, { left: 0, top: 0 })).toEqual({ x: 10, y: 20 });
    expect(canvasCssPoint(10, 20, { left: 30, top: 40 })).toEqual({ x: -20, y: -20 });
  });
});

describe("candidatePatches: which patches the topology pick may consider", () => {
  /**
   * **These tests are about the LOOP's decision, not about `Map.get`.**
   *
   * The previous version of this suite built a `Map` and asserted `sourcePatches` returned the right
   * entries — a test of the standard library. It would have passed with the loop calling the function
   * wrongly, or never calling it at all, which is exactly the bug it was written for: the loop looked a
   * record up by patch NAME while records are keyed by SOURCE id, so `entry === undefined` skipped
   * every patch and Edge mode selected nothing (Claude's defect 7). The loop's real decision is "this
   * source's patches, minus the hidden ones", so that is what `candidatePatches` is and what is tested.
   */
  const patches = [
    { name: "pipe", source: "n_4a749c45", shown: true },
    { name: "inlet_flange", source: "n_7d525911", shown: true },
    { name: "fluid", source: "n_1a411339", shown: true },
  ];
  const bySource = new Map<string, typeof patches>();
  for (const patch of patches) {
    bySource.set(patch.source, [...(bySource.get(patch.source) ?? []), patch]);
  }

  // **What these tests do and do not cover.** They cover the DECISION this function makes: given the
  // source-keyed map and a record's key, which patches are candidates. They do NOT cover the one-line
  // wiring in `partsOfSource` that chooses WHICH map to pass — mutating that line to pass the
  // name-keyed map still leaves these tests green, which I verified rather than assumed. That wiring is
  // covered end to end instead: `tools/test_interactions.py` enters Edge mode on the real window and
  // asserts edges are found at all, which fails outright if no patch is ever a candidate. A unit test
  // cannot see that line without a WebGL context, and claiming otherwise would be the same mistake
  // this suite was written to fix.
  it("returns a source's patches when they are found by SOURCE id", () => {
    expect(candidatePatches(bySource, "n_4a749c45").map((p) => p.name)).toEqual(["pipe"]);
  });

  it("returns NOTHING for a patch NAME, which is the bug this covers", () => {
    // **The point, stated exactly.** The defect's lookup was against a DIFFERENT map — the scene's
    // patches keyed by *name* (which is what `this.parts` is). So the honest contrast is two maps: the
    // source-keyed one this function needs, and the name-keyed one the bug used. Built here so the
    // difference is visible in the test rather than described in a comment.
    // This is the assertion that fails if the loop ever looks a record up by `patch.name` again: a
    // name is not a key in the source-keyed map, so the loop would see no candidates at all.
    expect(candidatePatches(bySource, "pipe")).toEqual([]);
    expect(candidatePatches(bySource, "fluid")).toEqual([]);
    // The name-keyed map, as `this.parts` was: looking the same record key up in it finds NOTHING,
    // which is why the loop skipped every patch. One map, two keyings, opposite answers.
    const byName = new Map(patches.map((patch) => [patch.name, [patch]]));
    expect(byName.get("n_4a749c45")).toBeUndefined();
    expect(byName.get("pipe")?.map((p) => p.name)).toEqual(["pipe"]);
    // So a lookup that used the record's key against the NAME-keyed map returns undefined for every
    // real source — the defect, reproduced in two lines.
    expect(byName.get("n_4a749c45") ?? []).toEqual([]);
  });

  it("drops a hidden patch, which is what the loop's `shown` check did", () => {
    // The filter the loop applied inline. Tested here so removing it from the loop cannot silently
    // make a hidden part pickable.
    const hidden = new Map([["n_a", [{ name: "a", source: "n_a", shown: false }]]]);
    expect(candidatePatches(hidden, "n_a")).toEqual([]);
    const mixed = new Map([
      [
        "n_a",
        [
          { name: "a1", source: "n_a", shown: true },
          { name: "a2", source: "n_a", shown: false },
        ],
      ],
    ]);
    expect(candidatePatches(mixed, "n_a").map((p) => p.name)).toEqual(["a1"]);
  });

  it("returns every DRAWN patch of a source that draws several solids", () => {
    // A multi-solid part is one source with a patch per solid; a hidden one among them must not stop
    // the others from being candidates.
    const multi = new Map([
      [
        "n_abc",
        [
          { name: "housing_1", source: "n_abc", shown: true },
          { name: "housing_2", source: "n_abc", shown: false },
          { name: "housing_3", source: "n_abc", shown: true },
        ],
      ],
    ]);
    expect(candidatePatches(multi, "n_abc").map((p) => p.name)).toEqual([
      "housing_1",
      "housing_3",
    ]);
  });

  it("returns nothing for an unknown source rather than throwing", () => {
    // A stale record whose source has left the scene is skipped, not fatal.
    expect(candidatePatches(bySource, "n_gone")).toEqual([]);
  });
});

describe("v3: face areas and solid volumes", () => {
  it("reads the per-face areas and the solids", () => {
    const record = parseTopology(payload(goodHeader(), [[[0, 0, 0], [0, 0, 0.03]]]));
    expect(record.faceSizes).toHaveLength(2);
    expect(record.faceSizes[0].area).toBeCloseTo(0.0009, 12);
    expect(record.faceSizes[0].centroid).toEqual([0, 0, 0.015]);
    expect(record.solids).toHaveLength(1);
    expect(record.solids[0].volume).toBeCloseTo(0.000027, 12);
    expect(record.solids[0].area).toBeCloseTo(0.0054, 12);
  });

  it("refuses a NaN or negative area rather than carrying it to the readout", () => {
    // A NaN would poison any sum without ever comparing unequal, and would reach the label as
    // "NaN mm²"; a negative area is not a measurement at all.
    for (const area of [Number.NaN, -0.5]) {
      const header = goodHeader({
        faceSizes: [{ id: 0, area, centroid: [0, 0, 0] }],
      });
      expect(() => parseTopology(payload(header, [[[0, 0, 0], [0, 0, 0.03]]]))).toThrow(
        TopologyFormatError,
      );
    }
  });

  it("refuses a face id past the face count", () => {
    const header = goodHeader({ faceSizes: [{ id: 5, area: 0.001, centroid: [0, 0, 0] }] });
    expect(() => parseTopology(payload(header, [[[0, 0, 0], [0, 0, 0.03]]]))).toThrow(
      TopologyFormatError,
    );
  });

  it("refuses a face-size list longer than the face count", () => {
    const header = goodHeader({
      faceSizes: [
        { id: 0, area: 0.001, centroid: [0, 0, 0] },
        { id: 1, area: 0.001, centroid: [0, 0, 0] },
        { id: 0, area: 0.001, centroid: [0, 0, 0] },
      ],
    });
    expect(() => parseTopology(payload(header, [[[0, 0, 0], [0, 0, 0.03]]]))).toThrow(
      TopologyFormatError,
    );
  });

  it("refuses a body whose volume is not finite", () => {
    const header = goodHeader({ solids: [{ volume: Number.NaN, centroid: [0, 0, 0], area: 0.1 }] });
    expect(() => parseTopology(payload(header, [[[0, 0, 0], [0, 0, 0.03]]]))).toThrow(
      TopologyFormatError,
    );
  });
});
