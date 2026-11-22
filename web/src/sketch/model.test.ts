import { describe, expect, it } from "vitest";
import { arcPoints, checkProfile, click, distanceTo, filletCorners, finishSpline, fromStored, gridStep, movePoint, polygonLines, rectangleLines, snap, snapTargets, toStored, type Entity, type Point } from "./model";

describe("the sketch's tools", () => {
  it("chains lines and closes the chain on its first point", () => {
    let draft = null;
    const added: Entity[] = [];
    for (const p of [[0, 0], [10, 0], [10, 5], [0, 0]] as Point[]) {
      const r = click("line", draft, p);
      added.push(...r.add);
      draft = r.draft;
    }
    expect(added).toHaveLength(3);
    expect(draft).toBeNull();
    expect(checkProfile(added)).toMatchObject({ loops: 1, open: [], message: "" });
  });

  it("makes rectangles, circles, arcs, polygons and splines", () => {
    // A rectangle stays one entity with its centre and sizes (review B5).
    expect(click("rectangle", { tool: "rectangle", points: [[0, 0]] }, [4, 2]).add).toEqual([{ kind: "rectangle", c: [2, 1], w: 4, h: 2 }]);
    const hex = click("polygon", { tool: "polygon", points: [[0, 0]] }, [5, 0], 6).add;
    expect(hex).toEqual([{ kind: "polygon", c: [0, 0], r: 5, n: 6, angle: 0 }]);
    expect(checkProfile(hex).loops).toBe(1);
    expect(click("circle", { tool: "circle", points: [[1, 1]] }, [4, 5]).add).toEqual([{ kind: "circle", c: [1, 1], r: 5 }]);
    const arc = click("arc", { tool: "arc", points: [[0, 0], [2, 0]] }, [1, 1]);
    expect(arc.add).toEqual([{ kind: "arc", a: [0, 0], m: [1, 1], b: [2, 0] }]);
    expect(click("arc", { tool: "arc", points: [[0, 0], [2, 0]] }, [1, 0]).add).toEqual([]); // collinear: no arc
    const hexagon = polygonLines([0, 0], [5, 0], 6);
    expect(hexagon).toHaveLength(6);
    expect(checkProfile(hexagon).loops).toBe(1);
    expect(finishSpline([[0, 0], [1, 1]]).add).toEqual([]);
    expect(finishSpline([[0, 0], [1, 1], [2, 0]]).add).toHaveLength(1);
  });

  it("says where a profile is open or branches", () => {
    const open = checkProfile([{ kind: "line", a: [0, 0], b: [1, 0] }]);
    expect(open.open).toHaveLength(2);
    expect(open.message).toMatch(/open at 2 points/);
    const branch = checkProfile([...rectangleLines([0, 0], [2, 2]), { kind: "line", a: [0, 0], b: [-1, -1] }]);
    expect(branch.branches).toHaveLength(1);
    const washer = checkProfile([...rectangleLines([-2, -2], [2, 2]), { kind: "circle", c: [0, 0], r: 1 }]);
    expect(washer.loops).toBe(2);
    const halfDisc = checkProfile([
      { kind: "line", a: [-1, 0], b: [1, 0] },
      { kind: "arc", a: [1, 0], m: [0, 1], b: [-1, 0] },
    ]);
    expect(halfDisc).toMatchObject({ loops: 1, message: "" });
  });
});

describe("snapping and editing", () => {
  it("prefers existing points, then the grid with horizontal and vertical alignment", () => {
    expect(snap([1.1, 0.9], [[1, 1]], 5, 0.5)).toEqual({ point: [1, 1], kind: "point" });
    expect(snap([7.4, 2.6], [], 5, 0.5)).toEqual({ point: [5, 5], kind: "grid" });
    expect(snap([7.4, 10.2], [], 1, 0.5, [0, 10])).toEqual({ point: [7, 10], kind: "horizontal" });
    expect(gridStep(10)).toBe(5);
    expect(gridStep(1)).toBe(50);
  });

  it("moves every end at a point together, so joins hold", () => {
    const moved = movePoint(rectangleLines([0, 0], [2, 2]), [2, 2], [3, 3]);
    expect(checkProfile(moved).loops).toBe(1);
    expect(moved.filter((e) => e.kind === "line" && (e.a[0] === 3 || e.b[0] === 3))).toHaveLength(2);
  });

  it("measures distances to entities for picking", () => {
    expect(distanceTo({ kind: "line", a: [0, 0], b: [10, 0] }, [5, 2])).toBeCloseTo(2);
    expect(distanceTo({ kind: "circle", c: [0, 0], r: 5 }, [8, 0])).toBeCloseTo(3);
    expect(distanceTo({ kind: "arc", a: [-5, 0], m: [0, 5], b: [5, 0] }, [0, 7])).toBeCloseTo(2, 1);
    expect(arcPoints([1, 0], [0, 1], [-1, 0], 4)[2][1]).toBeCloseTo(1);
  });
});

describe("the stored form", () => {
  it("is metres, and reads back to the same entities", () => {
    const entities: Entity[] = [
      { kind: "line", a: [0, 0], b: [10, 0] },
      { kind: "arc", a: [10, 0], m: [5, 5], b: [0, 0] },
      { kind: "circle", c: [5, 1], r: 0.5 },
      { kind: "spline", points: [[0, 0], [1, 2], [3, 0]] },
    ];
    const stored = toStored(entities);
    expect(stored[0]).toEqual({ kind: "line", a: [0, 0], b: [0.01, 0] });
    const back = fromStored(JSON.stringify(stored));
    expect(back).toHaveLength(4);
    expect((back[2] as { r: number }).r).toBeCloseTo(0.5);
    expect(fromStored("not json")).toEqual([]);
    expect(fromStored([{ kind: "line", a: [0], b: [1, 1] }])).toEqual([]);
  });
});

describe("rectangles and polygons as one entity (review B5)", () => {
  const rect: Entity = { kind: "rectangle", c: [0, 0], w: 40, h: 20 };

  it("checks a shape as the lines the server receives", () => {
    expect(checkProfile([rect])).toMatchObject({ loops: 1, message: "" });
    // A line joined to a corner makes three ends meet there: a branch, as the server would say.
    expect(checkProfile([rect, { kind: "line", a: [20, 10], b: [30, 30] }]).branches).toHaveLength(1);
  });

  it("stays a rectangle when a corner is dragged, and moves by its centre", () => {
    const resized = movePoint([rect], [20, 10], [30, 20]);
    expect(resized).toEqual([{ kind: "rectangle", c: [5, 5], w: 50, h: 30 }]); // about the opposite corner (-20, -10)
    expect(movePoint([rect], [0, 0], [5, 5])).toEqual([{ kind: "rectangle", c: [5, 5], w: 40, h: 20 }]);
    expect(snapTargets([rect])).toContainEqual([20, 0]); // an edge midpoint
    expect(distanceTo(rect, [0, 12])).toBeCloseTo(2);
  });

  it("is stored as its four lines and reads back as the one rectangle", () => {
    const stored = toStored([rect]) as Array<Record<string, unknown>>;
    expect(stored).toHaveLength(4);
    expect(stored.map((e) => e.kind)).toEqual(["line", "line", "line", "line"]);
    expect(stored[0]).toMatchObject({ a: [-0.02, -0.01], b: [0.02, -0.01] });
    expect(fromStored(stored)).toEqual([rect]);
    const poly: Entity = { kind: "polygon", c: [1, 2], r: 5, n: 5, angle: 0.3 };
    const back = fromStored(toStored([poly]))[0] as Extract<Entity, { kind: "polygon" }>;
    expect(back.kind).toBe("polygon");
    expect(back.n).toBe(5);
    expect(back.r).toBeCloseTo(5);
  });

  it("reads lines edited apart from their shape as plain lines", () => {
    const stored = toStored([rect]).slice(0, 3);
    expect(fromStored(stored).map((e) => e.kind)).toEqual(["line", "line", "line"]);
    expect(fromStored(rectangleLines([0, 0], [1, 1]).map((l) => toStored([l])[0])).map((e) => e.kind)).toEqual(["line", "line", "line", "line"]);
  });
});

describe("closing a chain (review A9)", () => {
  it("ends the chain when it lands on any open end", () => {
    const openEnds: Point[] = [[0, 0], [10, 10]];
    const r = click("line", { tool: "line", points: [[10, 0]] }, [10, 10], 6, openEnds);
    expect(r.add).toHaveLength(1);
    expect(r.draft).toBeNull();
    const on = click("line", { tool: "line", points: [[10, 0]] }, [20, 0], 6, openEnds);
    expect(on.draft).not.toBeNull();
  });

  it("polygonLines is still a closed chain", () => {
    expect(checkProfile(polygonLines([0, 0], [5, 0], 6)).loops).toBe(1);
  });
});

describe("filletCorners", () => {
  const square: Entity[] = [
    { kind: "line", a: [0, 0], b: [10, 0] },
    { kind: "line", a: [10, 0], b: [10, 10] },
    { kind: "line", a: [10, 10], b: [0, 10] },
    { kind: "line", a: [0, 10], b: [0, 0] },
  ];

  it("rounds every corner of a square with tangent arcs and keeps the loop closed", () => {
    const { entities, corners } = filletCorners(square, 2);
    expect(corners).toHaveLength(4);
    expect(entities).toHaveLength(8);
    // The first line is cut back by the radius at both ends (a right angle: cut = r).
    const first = entities[0] as Extract<Entity, { kind: "line" }>;
    for (const [got, want] of [[first.a, [2, 0]], [first.b, [8, 0]]] as const) {
      expect(got[0]).toBeCloseTo(want[0], 9);
      expect(got[1]).toBeCloseTo(want[1], 9);
    }
    const arcs = entities.filter((e) => e.kind === "arc") as Extract<Entity, { kind: "arc" }>[];
    for (const arc of arcs) {
      // Every arc point is the radius from a centre 2 mm inside the corner.
      const cx = Math.min(arc.a[0], arc.b[0]) < 5 ? 2 : 8;
      const cy = Math.min(arc.a[1], arc.b[1]) < 5 ? 2 : 8;
      for (const p of [arc.a, arc.m, arc.b]) expect(Math.hypot(p[0] - cx, p[1] - cy)).toBeCloseTo(2, 9);
    }
    const check = checkProfile(entities);
    expect([check.loops, check.open.length, check.branches.length]).toEqual([1, 0, 0]);
  });

  it("rounds only the corners between selected lines", () => {
    const { entities, corners } = filletCorners(square, 2, new Set([0, 1]));
    expect(corners).toEqual([[10, 0]]);
    expect(entities).toHaveLength(5);
    expect(entities[2]).toEqual(square[2]);
  });

  it("leaves a corner whose lines are too short for the cut, and a straight run", () => {
    // 11 mm cannot be cut from a 10 mm side; 6 mm can once per side, so two opposite corners take it.
    expect(filletCorners(square, 11).corners).toHaveLength(0);
    expect(filletCorners(square, 6).corners).toHaveLength(2);
    const run: Entity[] = [
      { kind: "line", a: [0, 0], b: [5, 0] },
      { kind: "line", a: [5, 0], b: [10, 0] },
    ];
    expect(filletCorners(run, 1).corners).toHaveLength(0);
    expect(filletCorners(square, 0).entities).toEqual(square);
  });

  it("cuts by r / tan(angle / 2) at a corner that is not square", () => {
    const wedge: Entity[] = [
      { kind: "line", a: [10, 0], b: [0, 0] },
      { kind: "line", a: [0, 0], b: [10, 10] },
    ];
    const { entities } = filletCorners(wedge, 1);
    const cut = 1 / Math.tan(Math.PI / 8);
    expect((entities[0] as Extract<Entity, { kind: "line" }>).b[0]).toBeCloseTo(cut, 9);
    const arc = entities[2] as Extract<Entity, { kind: "arc" }>;
    // Tangent: the arc's ends are the radius from the centre on the bisector.
    const d = 1 / Math.sin(Math.PI / 8);
    const centre: Point = [d * Math.cos(Math.PI / 8), d * Math.sin(Math.PI / 8)];
    for (const p of [arc.a, arc.m, arc.b]) expect(Math.hypot(p[0] - centre[0], p[1] - centre[1])).toBeCloseTo(1, 9);
  });
});
