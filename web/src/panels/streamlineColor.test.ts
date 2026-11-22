/** Streamlines coloured by a field they carried (T, p) instead of the speed, without tracing again. */
import { describe, expect, it } from "vitest";
import { carriedFields, coloredBy, speedRangeOf, type TracedLine } from "./streamlineModel";

const lines: TracedLine[] = [
  { points: [[0, 0, 0], [1, 0, 0]], speeds: [0.1, 0.2], values: { T: [350, 340], p: [10, 5] } },
  { points: [[0, 1, 0], [1, 1, 0], [2, 1, 0]], speeds: [0.3, 0.2, 0.1], values: { T: [330, 320, 310], p: [8, 4, 1] } },
];

describe("colouring streamlines by a carried field", () => {
  it("lists the fields every line carries", () => {
    expect(carriedFields(lines)).toEqual(["T", "p"]);
    expect(carriedFields([{ points: [[0, 0, 0]], speeds: [1] }])).toEqual([]);
  });

  it("puts the field's values where the speeds were, so the range follows the field", () => {
    const hot = coloredBy(lines, "T")!;
    expect(hot[1].speeds).toEqual([330, 320, 310]);
    expect(speedRangeOf(hot)).toEqual({ min: 310, max: 350 });
    expect(coloredBy(hot.map((l, i) => ({ ...l, speeds: lines[i].speeds })), "")![0].speeds).toEqual([0.1, 0.2]);
  });

  it("refuses a field a line does not carry", () => {
    expect(coloredBy(lines, "k")).toBeNull();
  });
});

describe("lines follow the field the view shows", () => {
  it("take the shown field's values when they carry it, and keep a colouring chosen on purpose", async () => {
    const { linesForField } = await import("./streamlineModel");
    const line = { points: [[0, 0, 0], [1, 0, 0]] as [number, number, number][], speeds: [1, 2], values: { T: [350, 342] } };
    const state = { phase: "ready" as const, lines: [line], traced: [line], range: { min: 1, max: 2 }, message: "" };
    expect(linesForField(state, "T").lines[0].speeds).toEqual([350, 342]);
    expect(linesForField(state, "T").range).toEqual({ min: 342, max: 350 });
    expect(linesForField(state, "U_mag").lines[0].speeds).toEqual([1, 2]);
    expect(linesForField(state, "k").lines[0].speeds).toEqual([1, 2]);
    expect(linesForField({ ...state, colorBy: "" }, "T").lines[0].speeds).toEqual([1, 2]);
  });
});
