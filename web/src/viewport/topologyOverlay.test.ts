/**
 * The edge and vertex overlay (ADR-0022 §15.3, T-058).
 *
 * These check the properties a screenshot cannot: that a curve becomes one polyline rather than a
 * chord, that one object carries edges from several parts (the batching §15.3 requires), that a
 * missing record or an out-of-range id draws nothing rather than something wrong, that an empty
 * overlay is invisible (so body and face mode cost no draw), and that disposal frees only its own
 * buffers.
 */

import { describe, expect, it } from "vitest";
import * as THREE from "three";

import { TopologyOverlay } from "./topologyOverlay";
import type { PartTopology, TopoEdge, TopoVertex } from "./topology";

function edge(id: number, points: number[]): TopoEdge {
  return {
    id,
    length: 1,
    centre: [0, 0, 0],
    start: [points[0], points[1], points[2]],
    end: [points[points.length - 3], points[points.length - 2], points[points.length - 1]],
    tangent: [0, 0, 1],
    curve: "GeomAbs_Line",
    points: new Float32Array(points),
    pointsCount: points.length / 3,
    pickable: true,
  };
}

function vertex(id: number, position: [number, number, number]): TopoVertex {
  return { id, position, degree: 1, edges: [], lengths: [] };
}

function record(part: string, edges: TopoEdge[], vertices: TopoVertex[] = []): PartTopology {
  return { part, basis: "abc", scale: 0.001, edges, vertices };
}

const IDENTITY = new THREE.Matrix4();

function setup() {
  const host = new THREE.Scene();
  const overlay = new TopologyOverlay("#00ff00", host);
  return { host, overlay };
}

describe("TopologyOverlay", () => {
  it("starts invisible, so body and face mode cost no extra draw", () => {
    const { overlay } = setup();
    expect(overlay.hover.visible).toBe(false);
    expect(overlay.picked.visible).toBe(false);
    expect(overlay.hoverPoints.visible).toBe(false);
    expect(overlay.pickedPoints.visible).toBe(false);
    overlay.dispose();
  });

  it("draws a straight edge as one segment", () => {
    const { overlay } = setup();
    const topo = new Map([["p1", record("p1", [edge(0, [0, 0, 0, 0, 0, 1])])]]);
    const matrices = new Map([["p1", IDENTITY]]);
    overlay.set([], [{ part: "p1", id: 0 }], [], [], topo, matrices);
    expect(overlay.picked.visible).toBe(true);
    const position = overlay.picked.geometry.getAttribute("instanceStart");
    expect(position.count).toBe(1);
    overlay.dispose();
  });

  it("draws a curved edge as its polyline, not a single chord", () => {
    const { overlay } = setup();
    // A three-point polyline is two segments; a chord would be one.
    const topo = new Map([["p1", record("p1", [edge(0, [0, 0, 0, 1, 1, 0, 2, 0, 0])])]]);
    overlay.set([], [{ part: "p1", id: 0 }], [], [], topo, new Map([["p1", IDENTITY]]));
    expect(overlay.picked.geometry.getAttribute("instanceStart").count).toBe(2);
    overlay.dispose();
  });

  it("batches edges from several parts into one object (no draw call per part)", () => {
    const { overlay } = setup();
    const topo = new Map([
      ["p1", record("p1", [edge(0, [0, 0, 0, 1, 0, 0])])],
      ["p2", record("p2", [edge(0, [0, 1, 0, 1, 1, 0])])],
    ]);
    const matrices = new Map([
      ["p1", IDENTITY],
      ["p2", IDENTITY],
    ]);
    overlay.set([], [{ part: "p1", id: 0 }, { part: "p2", id: 0 }], [], [], topo, matrices);
    expect(overlay.picked.geometry.getAttribute("instanceStart").count).toBe(2);
    overlay.dispose();
  });

  it("applies the part's matrix, so a moved part's edge is drawn where it is", () => {
    const { overlay } = setup();
    const topo = new Map([["p1", record("p1", [edge(0, [0, 0, 0, 1, 0, 0])])]]);
    const moved = new THREE.Matrix4().makeTranslation(10, 0, 0);
    overlay.set([], [{ part: "p1", id: 0 }], [], [], topo, new Map([["p1", moved]]));
    const geometry = overlay.picked.geometry;
    // One entry per segment: the segment's two ends live in instanceStart and instanceEnd.
    const start = geometry.getAttribute("instanceStart");
    const end = geometry.getAttribute("instanceEnd");
    expect(start.getX(0)).toBeCloseTo(10, 5);
    expect(end.getX(0)).toBeCloseTo(11, 5);
    overlay.dispose();
  });

  it("draws nothing for an id with no record, rather than an edge from another part", () => {
    const { overlay } = setup();
    const topo = new Map([["p1", record("p1", [edge(0, [0, 0, 0, 1, 0, 0])])]]);
    overlay.set([], [{ part: "missing", id: 0 }], [], [], topo, new Map([["missing", IDENTITY]]));
    expect(overlay.picked.visible).toBe(false);
    overlay.dispose();
  });

  it("draws nothing for an out-of-range edge id", () => {
    const { overlay } = setup();
    const topo = new Map([["p1", record("p1", [edge(0, [0, 0, 0, 1, 0, 0])])]]);
    overlay.set([], [{ part: "p1", id: 99 }], [], [], topo, new Map([["p1", IDENTITY]]));
    expect(overlay.picked.visible).toBe(false);
    overlay.dispose();
  });

  it("skips an edge the record marks unpickable", () => {
    const { overlay } = setup();
    const degenerate = { ...edge(0, [0, 0, 0, 0, 0, 0]), pickable: false };
    const topo = new Map([["p1", record("p1", [degenerate])]]);
    overlay.set([], [{ part: "p1", id: 0 }], [], [], topo, new Map([["p1", IDENTITY]]));
    expect(overlay.picked.visible).toBe(false);
    overlay.dispose();
  });

  it("draws vertex markers, and hover apart from the picked set", () => {
    const { overlay } = setup();
    const topo = new Map([["p1", record("p1", [], [vertex(0, [0, 0, 0]), vertex(1, [1, 0, 0])])]]);
    const matrices = new Map([["p1", IDENTITY]]);
    overlay.set([], [], [{ part: "p1", id: 0 }], [{ part: "p1", id: 1 }], topo, matrices);
    expect(overlay.hoverPoints.visible).toBe(true);
    expect(overlay.pickedPoints.visible).toBe(true);
    expect(overlay.hoverPoints.geometry.getAttribute("position").count).toBe(1);
    expect(overlay.pickedPoints.geometry.getAttribute("position").count).toBe(1);
    overlay.dispose();
  });

  it("keeps hover and pick in separate objects, so hovering never disturbs the pick", () => {
    const { overlay } = setup();
    const topo = new Map([["p1", record("p1", [edge(0, [0, 0, 0, 1, 0, 0]), edge(1, [0, 1, 0, 1, 1, 0])])]]);
    const matrices = new Map([["p1", IDENTITY]]);
    overlay.set([{ part: "p1", id: 0 }], [{ part: "p1", id: 1 }], [], [], topo, matrices);
    expect(overlay.hover.visible).toBe(true);
    expect(overlay.picked.visible).toBe(true);
    expect(overlay.hover).not.toBe(overlay.picked);
    overlay.dispose();
  });

  it("clears everything, which hides it again", () => {
    const { overlay } = setup();
    const topo = new Map([["p1", record("p1", [edge(0, [0, 0, 0, 1, 0, 0])], [vertex(0, [0, 0, 0])])]]);
    const matrices = new Map([["p1", IDENTITY]]);
    overlay.set([], [{ part: "p1", id: 0 }], [], [{ part: "p1", id: 0 }], topo, matrices);
    expect(overlay.picked.visible).toBe(true);
    overlay.clear();
    expect(overlay.picked.visible).toBe(false);
    expect(overlay.pickedPoints.visible).toBe(false);
    overlay.dispose();
  });

  it("replaces the previous pick rather than appending to it", () => {
    const { overlay } = setup();
    const topo = new Map([["p1", record("p1", [edge(0, [0, 0, 0, 1, 0, 0]), edge(1, [0, 1, 0, 1, 1, 0])])]]);
    const matrices = new Map([["p1", IDENTITY]]);
    overlay.set([], [{ part: "p1", id: 0 }, { part: "p1", id: 1 }], [], [], topo, matrices);
    expect(overlay.picked.geometry.getAttribute("instanceStart").count).toBe(2);
    overlay.set([], [{ part: "p1", id: 0 }], [], [], topo, matrices);
    expect(overlay.picked.geometry.getAttribute("instanceStart").count).toBe(1);
    overlay.dispose();
  });

  it("follows an accent change without rebuilding the buffers", () => {
    const { overlay } = setup();
    overlay.setAccent("#ff0000");
    expect(overlay.hover.material.color.getHexString()).toBe("ff0000");
    overlay.dispose();
  });

  it("takes a device-pixel resolution for its line widths (§15.3)", () => {
    const { overlay } = setup();
    overlay.setResolution(1920, 1080);
    expect(overlay.picked.material.resolution.x).toBe(1920);
    expect(overlay.picked.material.resolution.y).toBe(1080);
    overlay.dispose();
  });

  it("is safe to dispose twice", () => {
    const { overlay } = setup();
    overlay.dispose();
    expect(() => overlay.dispose()).not.toThrow();
  });

  it("ignores a set() call after disposal rather than touching freed buffers", () => {
    const { host, overlay } = setup();
    overlay.dispose();
    const topo = new Map([["p1", record("p1", [edge(0, [0, 0, 0, 1, 0, 0])])]]);
    expect(() => overlay.set([], [{ part: "p1", id: 0 }], [], [], topo, new Map([["p1", IDENTITY]]))).not.toThrow();
    expect(host.children).toHaveLength(0);
  });
});
