import { describe, expect, it } from "vitest";
import { NO_PROBE, phaseOfCode, probeChart, probeCurve, probeRows, shouldRetry } from "./probeModel";

/**
 * The probe panel's rules (results item 2), pure so they can be checked without a browser.
 *
 * **The one that matters most is the "preparing" phase**: the command answers
 * `post.triangulation_preparing` while the surface is triangulated in the background, and a panel that
 * showed that as a failure would teach a user to distrust a value that arrives a moment later.
 */
describe("phaseOfCode", () => {
  it("is PREPARING for the busy code, so the panel waits rather than failing", () => {
    expect(phaseOfCode("post.triangulation_preparing")).toBe("preparing");
  });

  it("separates 'outside the surface' from a failure", () => {
    // The probe worked and the point is not on anything: a user fixes that by picking again.
    expect(phaseOfCode("post.probe_outside")).toBe("outside");
    expect(phaseOfCode("post.probe_invalid")).toBe("outside");
  });

  it("is a FAILURE for any other error", () => {
    expect(phaseOfCode("solve.no_material")).toBe("failed");
  });

  it("is idle when there was no error at all", () => {
    expect(phaseOfCode(null)).toBe("idle");
    expect(phaseOfCode("")).toBe("idle");
  });
});

describe("shouldRetry", () => {
  it("retries ONLY while preparing", () => {
    // Retrying an outside-the-surface answer would hammer the server for the same refusal.
    expect(shouldRetry("preparing")).toBe(true);
    expect(shouldRetry("outside")).toBe(false);
    expect(shouldRetry("failed")).toBe(false);
    expect(shouldRetry("ready")).toBe(false);
  });
});

describe("probeCurve", () => {
  it("puts the readings where they were TAKEN, by distance along the line", () => {
    // Three picks on a 1 m line, the middle one a quarter of the way along: the plot's x-axis is the
    // chord length, so the unevenly spaced pick stays uneven.
    const points = [
      [0, 0, 0],
      [0.25, 0, 0],
      [1, 0, 0],
    ] as const;
    expect(probeCurve(points, [1, 2, 3])).toEqual([
      { at: 0, value: 1 },
      { at: 0.25, value: 2 },
      { at: 1, value: 3 },
    ]);
  });

  it("has one marker at zero for a single point", () => {
    expect(probeCurve([[0.5, 0.5, 0.5]], [7])).toEqual([{ at: 0, value: 7 }]);
  });

  it("draws nothing when the answer and the question disagree in length", () => {
    // A stale answer under a new field must not be drawn at all rather than stretched to fit.
    expect(probeCurve([[0, 0, 0], [1, 0, 0]], [1])).toEqual([]);
    expect(probeCurve([], [1])).toEqual([]);
  });
});

describe("probeRows", () => {
  it("shows each reading to four significant digits", () => {
    expect(probeRows([0.157801, 0, 1234.5678]).map((r) => r.value)).toEqual(["0.1578", "0", "1235"]);
  });
});

describe("NO_PROBE", () => {
  it("is idle with nothing drawn", () => {
    expect(NO_PROBE.phase).toBe("idle");
    expect(NO_PROBE.values).toEqual([]);
    expect(NO_PROBE.points).toEqual([]);
  });
});

describe("probeChart", () => {
  it("joins the readings in the order they were taken", () => {
    const chart = probeChart([{ at: 0, value: 0 }, { at: 1, value: 0.2 }], 200, 100, 10);
    expect(chart.path.startsWith("M10.00,")).toBe(true);
    expect(chart.path).toContain("L190.00,");
    // A rising value goes UP the screen, so its SVG y is smaller.
    expect(chart.points[0]!.y).toBeGreaterThan(chart.points[1]!.y);
  });

  it("puts a FLAT value across the middle instead of dividing by zero", () => {
    // "Every reading the same" is a result, not a reason to draw nothing - and the span is widened so
    // the line has somewhere to be.
    const chart = probeChart([{ at: 0, value: 3 }, { at: 1, value: 3 }], 200, 100, 10);
    expect(chart.points[0]!.y).toBeCloseTo(chart.points[1]!.y, 6);
    expect(Number.isFinite(chart.points[0]!.y)).toBe(true);
  });

  it("draws one marker for one reading", () => {
    const chart = probeChart([{ at: 0, value: 7 }], 200, 100, 10);
    expect(chart.points).toHaveLength(1);
    expect(Number.isFinite(chart.points[0]!.x)).toBe(true);
  });

  it("has nothing to draw for no readings, or a box too small to hold them", () => {
    expect(probeChart([], 200, 100).path).toBe("");
    expect(probeChart([{ at: 0, value: 1 }], 20, 100, 12).path).toBe("");
  });
});
