import { describe, expect, it } from "vitest";
import { buildStatusText, finishToastText } from "./buildStatus";
import { requestPullFocus, takePullFocus } from "./ghostState";
import * as THREE from "three";
import { planeCamera, sketchToWorld, originOf } from "./plane";
import { drawnLoops, namedLoops, nest } from "./profile";
import { defaultDepth, depthFromDrag, dragStep, niceNumber, typedDepth } from "./pull";
import { ghostGeometry, planeMatrix } from "./ghost";
import type { Entity } from "../sketch/model";

const rect: Entity = { kind: "rectangle", c: [0, 0], w: 40, h: 20 };

function worldBox(loops: ReturnType<typeof drawnLoops>, depth: number, plane: "xy" | "yz" | "zx", symmetric = false): THREE.Box3 {
  const g = ghostGeometry(loops ?? [], depth, symmetric);
  g.applyMatrix4(planeMatrix(plane, [0, 0, 0]));
  g.computeBoundingBox();
  return g.boundingBox as THREE.Box3;
}

describe("sketch planes", () => {
  it("maps sketch millimetres to world metres along the server's axes", () => {
    expect(sketchToWorld("xy", [0, 0, 0], 10, 20, 5)).toEqual([0.01, 0.02, 0.005]);
    expect(sketchToWorld("yz", [0, 0, 0], 10, 20, 5)).toEqual([0.005, 0.01, 0.02]);
    expect(sketchToWorld("zx", [1, 0, 0], 10, 20, 5)).toEqual([1.02, 0.005, 0.01]);
  });

  it("reads an origin of numbers or quantities, else the world origin", () => {
    expect(originOf([0.01, { value: 0.02, unit: "m" }, 0])).toEqual([0.01, 0.02, 0]);
    expect(originOf("nope")).toEqual([0, 0, 0]);
  });

  it("puts the drawing's centre point under the drawing's centre at the drawing's scale", () => {
    const canvas = { left: 0, top: 0, width: 1000, height: 800 };
    const drawing = { left: 300, top: 100, width: 600, height: 500 };
    const cam = planeCamera("xy", [0, 0, 0], { scale: 4, cu: 10, cv: -5 }, drawing, canvas);
    // 4 px/mm is 4000 px/m: the canvas is 0.2 m high.
    expect(cam.span).toBeCloseTo(0.2);
    expect(cam.dir).toEqual([0, 0, 1]);
    expect(cam.up).toEqual([0, 1, 0]);
    // The drawing centre (600, 350) is 100 px right of and 50 px above the canvas centre (500, 400):

    expect(cam.target[0]).toBeCloseTo(0.01 - 0.1 / 4);
    expect(cam.target[1]).toBeCloseTo(-0.005 + -50 / 4000);
    expect(cam.target[2]).toBeCloseTo(0);
  });

  it("projects the sketch through the aligned camera exactly where the drawing draws it", () => {
    const canvas = { left: 20, top: 40, width: 1200, height: 700 };
    const drawing = { left: 200, top: 90, width: 700, height: 520 };
    const view = { scale: 7.5, cu: 3, cv: 8 };
    for (const plane of ["xy", "yz", "zx"] as const) {
      const cam = planeCamera(plane, [0.1, -0.2, 0.05], view, drawing, canvas);
      const half = cam.span / 2;
      const aspect = canvas.width / canvas.height;
      const camera = new THREE.OrthographicCamera(-half * aspect, half * aspect, half, -half, 0.001, 10);
      camera.up.set(...cam.up);
      camera.position.set(cam.target[0] + cam.dir[0], cam.target[1] + cam.dir[1], cam.target[2] + cam.dir[2]);
      camera.lookAt(...cam.target);
      camera.updateMatrixWorld();
      camera.updateProjectionMatrix();
      // A sketch point and where the drawing puts it (toX/toY of the sketcher).
      const [u, v] = [17, -4];
      const p = new THREE.Vector3(...sketchToWorld(plane, [0.1, -0.2, 0.05], u, v)).project(camera);
      const x = canvas.left + ((p.x + 1) / 2) * canvas.width;
      const y = canvas.top + ((1 - p.y) / 2) * canvas.height;
      expect(x).toBeCloseTo(drawing.left + drawing.width / 2 + (u - view.cu) * view.scale, 6);
      expect(y).toBeCloseTo(drawing.top + drawing.height / 2 - (v - view.cv) * view.scale, 6);
    }
  });
});

describe("profiles", () => {
  it("chains lines and arcs into a loop, in either direction", () => {
    const loops = drawnLoops([
      { kind: "line", a: [0, 0], b: [10, 0] },
      { kind: "line", a: [10, 10], b: [10, 0] },
      { kind: "arc", a: [10, 10], m: [5, 15], b: [0, 10] },
      { kind: "line", a: [0, 10], b: [0, 0] },
    ]);
    expect(loops).not.toBeNull();
    expect(loops?.length).toBe(1);
  });

  it("refuses an open profile", () => {
    expect(drawnLoops([{ kind: "line", a: [0, 0], b: [10, 0] }, { kind: "line", a: [10, 0], b: [10, 10] }])).toBeNull();
  });

  it("nests a hole in its outline", () => {
    const loops = drawnLoops([rect, { kind: "circle", c: [0, 0], r: 5 }]) ?? [];
    const shapes = nest(loops);
    expect(shapes.length).toBe(1);
    expect(shapes[0].holes.length).toBe(1);
  });

  it("draws the named profiles the server builds", () => {
    const sizes = { width: 40, height: 20, radius: 5, corner_radius: 2, slot_length: 10, sides: 6 };
    for (const kind of ["rectangle", "rounded_rectangle", "circle", "ellipse", "slot", "polygon"]) expect(namedLoops(kind, sizes)).not.toBeNull();
    expect(namedLoops("nonsense", sizes)).toBeNull();
    // The polygon's flat side is at the bottom: its two lowest corners share a v.
    const hex = (namedLoops("polygon", sizes) ?? [[]])[0];
    const low = [...hex].sort((a, b) => a[1] - b[1]);
    expect(low[0][1]).toBeCloseTo(low[1][1]);
  });
});

describe("the ghost", () => {
  it("is the profile pulled along the plane's normal, in world metres", () => {
    const loops = drawnLoops([rect]);
    const xy = worldBox(loops, 10, "xy");
    expect(xy.min.toArray().map((n) => Number(n.toFixed(6)))).toEqual([-0.02, -0.01, 0]);
    expect(xy.max.toArray().map((n) => Number(n.toFixed(6)))).toEqual([0.02, 0.01, 0.01]);
    const zx = worldBox(loops, 10, "zx");
    // zx: u is z, v is x, w is y.
    expect(zx.max.toArray().map((n) => Number(n.toFixed(6)))).toEqual([0.01, 0.01, 0.02]);
    const sym = worldBox(loops, 10, "xy", true);
    expect(sym.min.z).toBeCloseTo(-0.005);
    expect(sym.max.z).toBeCloseTo(0.005);
  });

  it("keeps a hole open", () => {
    const g = ghostGeometry(drawnLoops([rect, { kind: "circle", c: [0, 0], r: 5 }]) ?? [], 10, false);
    // The hole adds its wall: more triangles than the plain box's twelve.
    const solid = ghostGeometry(drawnLoops([rect]) ?? [], 10, false);
    expect(g.getAttribute("position").count).toBeGreaterThan(solid.getAttribute("position").count);
  });
});

describe("pull rules", () => {
  it("rounds to nice numbers", () => {
    expect(niceNumber(20)).toBe(20);
    expect(niceNumber(13)).toBe(10);
    expect(niceNumber(0.031)).toBe(0.025);
    expect(niceNumber(70)).toBe(50);
  });

  it("starts a pull at half the profile's larger side", () => {
    expect(defaultDepth(drawnLoops([rect]))).toBe(20);
    expect(defaultDepth(drawnLoops([{ kind: "circle", c: [3, 3], r: 5 }]))).toBe(5);
    expect(defaultDepth(null)).toBe(10);
  });

  it("reads a drag along the pull direction only, snapped and never through the plane", () => {
    // One millimetre is 5 px straight up the screen.
    const axis: [number, number] = [0, -5];
    expect(dragStep(5)).toBe(1);
    expect(depthFromDrag(20, axis, [0, -50])).toBe(30);
    expect(depthFromDrag(20, axis, [40, -50])).toBe(30);
    expect(depthFromDrag(20, axis, [0, 51])).toBe(10);
    expect(depthFromDrag(20, axis, [0, 500])).toBe(1);
    // Symmetric: the end face moves half the depth.
    expect(depthFromDrag(20, axis, [0, -50], 2)).toBe(40);
    // Looking straight down the pull, a drag means nothing.
    expect(depthFromDrag(20, [0, 0], [0, -50])).toBe(20);
  });

  it("takes a typed depth as millimetres or as text", () => {
    expect(typedDepth("25")).toBe("25 [mm]");
    expect(typedDepth(" 2.5 ")).toBe("2.5 [mm]");
    expect(typedDepth("0")).toBeNull();
    expect(typedDepth("")).toBeNull();
    expect(typedDepth("W * 2")).toBe("W * 2");
  });
});

describe("the pull of an operation", () => {
  const node = (params: Record<string, unknown>) => ({ id: "op1", type: "implicit_sketch", name: "s", status: "ok" as const, path: "/geometry/operations/s", children_count: 0, params: params as never });

  it("reads a drawn extrude's plane, origin, depth and profile", async () => {
    const { pullOf } = await import("./opPull");
    const entities = [
      { kind: "line", a: [-0.02, -0.01], b: [0.02, -0.01] },
      { kind: "line", a: [0.02, -0.01], b: [0.02, 0.01] },
      { kind: "line", a: [0.02, 0.01], b: [-0.02, 0.01] },
      { kind: "line", a: [-0.02, 0.01], b: [-0.02, -0.01] },
    ];
    const info = pullOf(node({ profile: "custom", plane: "zx", origin: [0.01, 0, 0], method: "extrude", depth: { value: 0.025, unit: "m", expr: "25 [mm]" }, entities }));
    expect(info?.plane).toBe("zx");
    expect(info?.origin).toEqual([0.01, 0, 0]);
    expect(info?.depth).toBeCloseTo(25);
    expect(info?.expression).toBeNull();
    expect(info?.loops?.length).toBe(1);
  });

  it("does not pull a revolve, and does not drag a depth bound to an expression", async () => {
    const { pullOf } = await import("./opPull");
    expect(pullOf(node({ profile: "circle", method: "revolve" }))).toBeNull();
    const bound = pullOf(node({ profile: "circle", radius: 0.005, depth: { value: 0.04, unit: "m", expr: "2 * W" } }));
    expect(bound?.expression).toBe("2 * W");
    expect(bound?.loops).not.toBeNull();
  });

  it("ghosts a sketch with bound coordinates as the body's box", async () => {
    const { pullOf, boxLoop } = await import("./opPull");
    const info = pullOf(node({ profile: "custom", entities: [{ kind: "circle", c: [0, 0], r: "R" }], depth: 0.01 }));
    expect(info?.loops).toBeNull();
    expect(boxLoop("xy", [0, 0, 0], { min: [-0.01, -0.02, 0], max: [0.01, 0.02, 0.01] })).toEqual([[[-10, -20], [10, -20], [10, 20], [-10, 20]]]);
  });
});

describe("the build status after Finish", () => {
  it("says what is built, the depth and the time, then the run's own words", () => {
    expect(buildStatusText({ progress: "", seconds: 0.3, depth: 25, known: false })).toBe("Creating the body · pull 25 mm");
    expect(buildStatusText({ progress: "", seconds: 4.9, depth: 25, known: true })).toBe("Building the body · pull 25 mm · 4 s");
    expect(buildStatusText({ progress: "Sketch: meshing 40 %", seconds: 75, depth: 12.5, known: true })).toBe("Sketch: meshing 40 % · pull 12.5 mm · 1 min 15 s");
    expect(buildStatusText({ progress: "Sketch…", seconds: 2, depth: 0, known: true })).toBe("Sketch · 2 s");
    expect(finishToastText(25)).toBe("Pulled 25 mm. Drag the arrow on the body, or type a depth.");
  });

  it("opens the Pull field of the finished sketch's body once", () => {
    requestPullFocus("op1");
    expect(takePullFocus("op2")).toBe(false);
    expect(takePullFocus("op1")).toBe(true);
    expect(takePullFocus("op1")).toBe(false);
  });
});
