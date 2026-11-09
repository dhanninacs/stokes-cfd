import { describe, expect, it } from "vitest";
import {
  DEFAULT_LINE_POINTS,
  NO_LINE,
  MAX_LINE_POINTS,
  carryToPlane,
  curveColour,
  isLinePicking,
  lengthUnitFor,
  lineAsk,
  lineChart,
  lineCsv,
  lineErrorPhase,
  lineFileName,
  lineNote,
  lineStale,
  lineTitle,
  pickPoint,
  pressLine,
  rayOnPlane,
  readLineReply,
  samplePoints,
  tickText,
  type LineState,
} from "./lineModel";

/**
 * The plot over a line (R5), pure. The rules that carry it: the two clicks make A then B and only B makes the ask,
 * the sample points are evenly spaced with both ends, a click lands on the slice's plane along the camera ray, the
 * CSV has the header and six significant digits, and a made line is asked again when the field or the results move.
 */
const ready = (over: Partial<LineState> = {}): LineState => ({
  ...NO_LINE,
  a: [0, 0, 0],
  b: [1, 0, 0],
  patch: "slice",
  phase: "ready",
  field: "p",
  seq: 3,
  along: [0, 1],
  values: [2, 4],
  ...over,
});

describe("samplePoints", () => {
  it("gives n evenly spaced points with both ends", () => {
    const pts = samplePoints([0, 0, 0], [1, 2, 4], 5);
    expect(pts).toHaveLength(5);
    expect(pts[0]).toEqual([0, 0, 0]);
    expect(pts[4]).toEqual([1, 2, 4]);
    expect(pts[2]).toEqual([0.5, 1, 2]);
  });
  it("gives the two ends for an n below 2 or not a number, and caps a huge n", () => {
    expect(samplePoints([0, 0, 0], [1, 0, 0], 1)).toHaveLength(2);
    expect(samplePoints([0, 0, 0], [1, 0, 0], Number.NaN)).toHaveLength(2);
    expect(samplePoints([0, 0, 0], [1, 0, 0], 1e9)).toHaveLength(MAX_LINE_POINTS);
  });
});

describe("the two clicks", () => {
  it("Line starts a pick, and a second press cancels it", () => {
    const picking = pressLine(NO_LINE);
    expect(picking.phase).toBe("pickA");
    expect(isLinePicking(picking.phase)).toBe(true);
    expect(pressLine(picking).phase).toBe("idle");
  });
  it("Line on a made line starts a fresh pick and keeps the count", () => {
    const next = pressLine(ready({ n: 50 }));
    expect(next).toMatchObject({ phase: "pickA", a: null, b: null, n: 50, values: [] });
  });
  it("the first click is A, the second is B and asks", () => {
    const a = pickPoint(pressLine(NO_LINE), [1, 2, 3], "slice");
    expect(a).toMatchObject({ phase: "pickB", a: [1, 2, 3], b: null, patch: "slice" });
    const b = pickPoint(a, [4, 2, 3], "ignored");
    expect(b).toMatchObject({ phase: "preparing", a: [1, 2, 3], b: [4, 2, 3], patch: "slice" });
  });
  it("a second click on A itself is not a line: it waits with a word", () => {
    const a = pickPoint(pressLine(NO_LINE), [1, 2, 3], "slice");
    const same = pickPoint(a, [1, 2, 3], "slice");
    expect(same.phase).toBe("pickB");
    expect(lineNote(same)?.text).toBe("Pick a second point");
  });
  it("a click when no pick is armed changes nothing", () => {
    const line = ready();
    expect(pickPoint(line, [9, 9, 9], "p")).toBe(line);
  });
});

describe("the ask", () => {
  it("is run.probe's polyline with the count, once both ends are set", () => {
    expect(lineAsk(NO_LINE, "p")).toBeNull();
    expect(lineAsk(ready({ n: DEFAULT_LINE_POINTS }), "U_mag")).toEqual({ patch: "slice", field: "U_mag", polyline: [[0, 0, 0], [1, 0, 0]], n: 100 });
  });
  it("clamps n to 2..1000", () => {
    expect(lineAsk(ready({ n: 0 }), "p")?.n).toBe(2);
    expect(lineAsk(ready({ n: 1e6 }), "p")?.n).toBe(MAX_LINE_POINTS);
  });
  it("reads an answer only when along and values agree and are numbers", () => {
    expect(readLineReply({ along: [0, 1, 2], values: [1, 2, 3] }, 3)).toEqual({ along: [0, 1, 2], values: [1, 2, 3] });
    expect(readLineReply({ along: [0, 1], values: [1] }, 3)).toBeNull();
    expect(readLineReply({ along: [0, 1], values: [1, "x"] }, 3)).toBeNull();
    expect(readLineReply({ values: [1, 2] }, 3)).toBeNull();
    expect(readLineReply(null, 3)).toBeNull();
    expect(readLineReply({ along: [0, 1, 2, 3], values: [1, 2, 3, 4] }, 3)).toBeNull();
  });
  it("maps the server's codes to phases: preparing retries, outside and failed end", () => {
    expect(lineErrorPhase("post.triangulation_preparing")).toBe("preparing");
    expect(lineErrorPhase("post.probe_invalid")).toBe("outside");
    expect(lineErrorPhase("post.probe_outside")).toBe("outside");
    expect(lineErrorPhase("run.not_found")).toBe("failed");
  });
});

describe("where a click lands on the slice", () => {
  it("is the camera ray's crossing of the plane", () => {
    const p = rayOnPlane({ point: [5, 5, 5], ray: { origin: [0, 0, 10], direction: [0, 0.6, -0.8] } }, { axis: "z", offset: 2 });
    // t = (2 - 10) / -0.8 = 10; y = 0.6 * 10
    expect(p[0]).toBeCloseTo(0);
    expect(p[1]).toBeCloseTo(6);
    expect(p[2]).toBe(2);
  });
  it("carries the picked point onto the plane when the ray is parallel to it or behind", () => {
    expect(rayOnPlane({ point: [1, 2, 3], ray: { origin: [0, 0, 9], direction: [1, 0, 0] } }, { axis: "z", offset: 0.5 })).toEqual([1, 2, 0.5]);
    expect(rayOnPlane({ point: [1, 2, 3], ray: { origin: [0, 0, 9], direction: [0, 0, 1] } }, { axis: "z", offset: 0.5 })).toEqual([1, 2, 0.5]);
    expect(rayOnPlane({ point: [1, 2, 3] }, { axis: "y", offset: 7 })).toEqual([1, 7, 3]);
  });
  it("moves both ends of a line to a moved slice, and leaves one that is already there", () => {
    const line = ready({ a: [0, 0, 1], b: [3, 4, 1] });
    expect(carryToPlane(line, { axis: "z", offset: 1 })).toBe(line);
    const moved = carryToPlane(line, { axis: "z", offset: 2 });
    expect(moved.a).toEqual([0, 0, 2]);
    expect(moved.b).toEqual([3, 4, 2]);
    expect(carryToPlane(NO_LINE, { axis: "z", offset: 2 })).toBe(NO_LINE);
  });
});

describe("a made line follows the screen", () => {
  it("is stale for another field or other results, and for nothing else", () => {
    expect(lineStale(ready(), "p", 3)).toBe(false);
    expect(lineStale(ready(), "U_mag", 3)).toBe(true);
    expect(lineStale(ready(), "p", 4)).toBe(true);
    expect(lineStale(ready({ phase: "preparing" }), "U_mag", 9)).toBe(false);
    expect(lineStale(NO_LINE, "p", 1)).toBe(false);
    expect(lineStale(ready({ phase: "outside" }), "U_mag", 3)).toBe(true);
  });
});

describe("words and files", () => {
  it("has a short note per phase and none for a plot", () => {
    expect(lineNote(pressLine(NO_LINE))?.text).toBe("Click point A");
    expect(lineNote(ready())).toBeNull();
    expect(lineNote(ready({ phase: "outside", message: "off" }))).toEqual({ text: "Outside the model", tip: "off" });
  });
  it("titles the plot with the field, its units and the line's length", () => {
    expect(lineTitle("|U|", "m/s", 0.12)).toBe("|U| (m/s) along 120 mm");
    expect(lineTitle("T", "", 2)).toBe("T along 2 m");
    expect(lineTitle("T", "K", 0)).toBe("T (K)");
  });
  it("writes the CSV with the header and six significant digits", () => {
    expect(lineCsv("p", "Pa", [0, 0.123456789, 1], [1.23456789, 100000.123, Number.NaN])).toBe("distance_m,p[Pa]\n0,1.23457\n0.123457,100000\n1,\n");
    expect(lineCsv("alpha", "", [0], [1]).split("\n")[0]).toBe("distance_m,alpha");
  });
  it("names the files <project>-<run>-<field>-line", () => {
    expect(lineFileName({ project: "pipe 4", run: "run_1", field: "U_mag" }, "csv")).toBe("pipe-4-run_1-U_mag-line.csv");
    expect(lineFileName({ project: "", run: "", field: "p" }, "png")).toBe("stokes-p-line.png");
  });
  it("reads the colour of the middle of the map", () => {
    expect(curveColour("viridis", false)).toMatch(/^rgb\(\d+, \d+, \d+\)$/);
    expect(curveColour("coolwarm", false)).not.toBe(curveColour("viridis", false));
  });
});

describe("the plot's geometry", () => {
  it("picks one unit for the whole axis", () => {
    expect(lengthUnitFor(0.12)).toEqual({ suffix: "mm", scale: 1e-3 });
    expect(lengthUnitFor(2)).toEqual({ suffix: "m", scale: 1 });
    expect(lengthUnitFor(0)).toEqual({ suffix: "m", scale: 1 });
  });
  it("draws a curve inside the box, in the display unit", () => {
    const c = lineChart([0, 0.05, 0.1], [1, 3, 2], 280, 132);
    expect(c.unit).toBe("mm");
    expect(c.d.startsWith("M")).toBe(true);
    expect(c.d.match(/L/g)).toHaveLength(2);
    expect(c.xTicks[c.xTicks.length - 1]!.value).toBeLessThanOrEqual(100);
    expect(c.yMin).toBeLessThan(1);
    expect(c.yMax).toBeGreaterThan(3);
    for (const t of c.yTicks) expect(t.at).toBeGreaterThanOrEqual(c.y0 - 1e-9);
  });
  it("still draws a flat line, across the middle", () => {
    const c = lineChart([0, 1], [5, 5], 280, 132);
    expect(c.d).not.toBe("");
    const ys = [...c.d.matchAll(/ ([\d.]+)/g)].map((m) => Number(m[1]));
    expect(ys[0]).toBeCloseTo((c.y0 + c.y1) / 2, 0);
  });
  it("breaks the path at a value that is not a number", () => {
    const c = lineChart([0, 1, 2, 3], [1, Number.NaN, 2, 3], 280, 132);
    expect(c.d.match(/M/g)).toHaveLength(2);
  });
  it("draws nothing for no samples, a zero length or no number", () => {
    expect(lineChart([], [], 280, 132).d).toBe("");
    expect(lineChart([0, 0], [1, 2], 280, 132).d).toBe("");
    expect(lineChart([0, 1], [Number.NaN, Number.NaN], 280, 132).d).toBe("");
  });
  it("prints ticks to three digits, in exponent form at the extremes", () => {
    expect(tickText(0)).toBe("0");
    expect(tickText(1.23456)).toBe("1.23");
    expect(tickText(250000)).toBe("2.5e5");
    expect(tickText(0.0004)).toBe("4e-4");
  });
});
