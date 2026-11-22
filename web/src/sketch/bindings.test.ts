import { describe, expect, it } from "vitest";
import { afterDelete, applyValues, bindPoint, bindRadius, bindScalar, boundStored, expressionsOf, freeEntity, freePoint, parseStored } from "./bindings";
import type { Entity } from "./model";

const square: Entity[] = [
  { kind: "line", a: [0, 0], b: [20, 0] },
  { kind: "line", a: [20, 0], b: [20, 10] },
  { kind: "line", a: [20, 10], b: [0, 10] },
  { kind: "line", a: [0, 10], b: [0, 0] },
];

describe("sketch bindings", () => {
  it("binds every coordinate at a point, so joined ends move together", () => {
    const bound = bindPoint(square, {}, [20, 0], 0, "W");
    expect(bound).toEqual({ "0.b.0": "W", "1.a.0": "W" });
    const both = bindPoint(square, bound, [20, 10], 0, "W");
    const moved = applyValues(square, both, { W: 30 });
    expect(moved[0]).toEqual({ kind: "line", a: [0, 0], b: [30, 0] });
    expect(moved[1]).toEqual({ kind: "line", a: [30, 0], b: [30, 10] });
    expect(moved[2].kind === "line" && moved[2].a).toEqual([30, 10]);
    expect(expressionsOf(both)).toEqual(["W"]);
    expect(freePoint(square, both, [20, 0])).toEqual({ "1.b.0": "W", "2.a.0": "W" });
  });

  it("stores bound coordinates as their text and reads them back", () => {
    const bindings = bindRadius(bindPoint(square, {}, [20, 10], 1, "H"), 0, null);
    const stored = boundStored(square, bindings) as Array<Record<string, unknown>>;
    expect(stored[1]).toEqual({ kind: "line", a: [0.02, 0], b: [0.02, "H"] });
    const back = parseStored(stored);
    expect(back.bindings).toEqual(bindings);
    expect(back.entities[0]).toEqual(square[0]);
    expect(back.entities[1].kind === "line" && back.entities[1].b).toEqual([20, 0]); // waits for H's value
    const circle = parseStored([{ kind: "circle", c: [0, 0], r: "R" }], 5);
    expect(circle.entities).toEqual([{ kind: "circle", c: [0, 0], r: 5 }]);
    expect(circle.bindings).toEqual({ "0.r": "R" });
  });

  it("drops a deleted entity's bindings and renumbers the later ones", () => {
    expect(afterDelete({ "0.b.0": "W", "1.a.0": "W", "2.a.1": "H" }, 1)).toEqual({ "0.b.0": "W", "1.a.1": "H" });
  });
});

describe("bindings on rectangles and polygons", () => {
  const rect: Entity = { kind: "rectangle", c: [0, 0], w: 40, h: 20 };

  it("stores a bound width as corner expressions and reads the rectangle back bound", () => {
    const bindings = bindScalar({}, 0, "w", "W");
    const stored = boundStored([rect], bindings) as Array<Record<string, unknown>>;
    expect(stored).toHaveLength(4);
    expect(stored[0].a).toEqual(["-(W) / 2", -0.01]);
    expect(stored[1].a).toEqual(["(W) / 2", -0.01]);
    const back = parseStored(stored, 3);
    expect(back.entities).toEqual([{ kind: "rectangle", c: [0, 0], w: 3, h: 20 }]);
    expect(back.bindings).toEqual({ "0.w": "W" });
  });

  it("offsets a bound centre with its size", () => {
    const stored = boundStored([rect], { "0.c.0": "X0" }) as Array<Record<string, unknown>>;
    expect(stored[0].a).toEqual(["(X0) - (40 [mm]) / 2", -0.01]);
    expect(parseStored(stored).bindings).toEqual({ "0.c.0": "X0" });
  });

  it("applies values to a shape's sizes and refuses a size that is not positive", () => {
    const b = { "0.w": "W", "0.h": "H" };
    expect(applyValues([rect], b, { W: 60, H: -1 })).toEqual([{ kind: "rectangle", c: [0, 0], w: 60, h: 20 }]);
  });

  it("frees every binding of one entity", () => {
    expect(freeEntity({ "0.w": "W", "0.c.0": "X", "1.r": "R" }, 0)).toEqual({ "1.r": "R" });
  });
});
