import { describe, expect, it } from "vitest";
import { backspace, entryLabels, isEmpty, liveValues, newEntry, nextField, resolveEntry, typeInto, type Entry } from "./entry";
import { click } from "./model";

const typed = (labels: string[], ...fields: string[]): Entry => ({ labels, fields: labels.map((_, i) => fields[i] ?? ""), active: 0 });
const none = () => undefined;

describe("typing sizes while drawing (review B4)", () => {
  it("offers the fields of each tool after its first click", () => {
    expect(entryLabels("rectangle", [[0, 0]])).toEqual(["Width", "Height"]);
    expect(entryLabels("line", [[0, 0], [1, 0]])).toEqual(["Length", "Angle"]);
    expect(entryLabels("circle", [[0, 0]])).toEqual(["Radius", "Diameter"]);
    expect(entryLabels("polygon", [[0, 0]])).toEqual(["Radius"]);
    expect(entryLabels("arc", [[0, 0]])).toBeNull();
    expect(entryLabels("rectangle", [])).toBeNull();
  });

  it("types 40 Tab 20 into the width and the height", () => {
    let e = newEntry(["Width", "Height"]);
    expect(isEmpty(e)).toBe(true);
    for (const ch of "40") e = typeInto(e, ch);
    e = nextField(e);
    for (const ch of "205") e = typeInto(e, ch);
    e = backspace(e);
    expect(e.fields).toEqual(["40", "20"]);
    expect(nextField(e, true).active).toBe(0);
    const r = resolveEntry("rectangle", [[0, 0]], [3, -2], e, none);
    // Towards the cursor's quadrant: right and down.
    expect(r).toEqual({ point: [40, -20], binds: {} });
    if ("point" in r) expect(click("rectangle", { tool: "rectangle", points: [[0, 0]] }, r.point).add).toEqual([{ kind: "rectangle", c: [20, -10], w: 40, h: 20 }]);
  });

  it("takes a line's length and angle in degrees, and the cursor's for an empty field", () => {
    const r = resolveEntry("line", [[0, 0]], null, typed(["Length", "Angle"], "10", "90"), none);
    expect("point" in r && r.point[0]).toBeCloseTo(0);
    expect("point" in r && r.point[1]).toBeCloseTo(10);
    const along = resolveEntry("line", [[0, 0]], [5, 0], typed(["Length", "Angle"], "12"), none);
    expect(along).toEqual({ point: [12, 0], binds: {} });
    expect(resolveEntry("line", [[0, 0]], [5, 0], typed(["Length", "Angle"], "W"), none)).toEqual({ error: "Length takes a number here" });
    expect(liveValues("line", [[0, 0]], [0, 3])).toEqual([3, 90]);
  });

  it("takes a circle's radius or diameter, and binds an expression", () => {
    expect(resolveEntry("circle", [[0, 0]], [1, 0], typed(["Radius", "Diameter"], "", "10"), none)).toEqual({ point: [5, 0], binds: {} });
    expect(resolveEntry("circle", [[0, 0]], [1, 0], typed(["Radius", "Diameter"], "", "D"), (t) => (t === "(D) / 2" ? 4 : undefined))).toEqual({ point: [4, 0], binds: { r: "(D) / 2" } });
    expect(resolveEntry("rectangle", [[0, 0]], [1, 1], typed(["Width", "Height"], "W", "5"), (t) => (t === "W" ? 30 : undefined))).toEqual({ point: [30, 5], binds: { w: "W" } });
    expect(resolveEntry("polygon", [[0, 0]], [1, 0], typed(["Radius"], "0"), none)).toEqual({ error: "Give it a radius above zero" });
  });
});
