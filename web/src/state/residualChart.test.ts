import { describe, expect, it } from "vitest";
import { buildChart, decadeRange, niceTicks, targetLabel } from "./residualChart";
import { EMPTY_SERIES, appendResidual } from "./residualSeries";
import type { ResidualBlock } from "../panels/runApi";

const block = (iteration: number, values: Record<string, number | null>): ResidualBlock => ({
  iteration,
  time: iteration,
  values: Object.entries(values).map(([name, value]) => ({ name, value })),
});

function falling(n: number): ReturnType<typeof appendResidual> {
  const blocks: ResidualBlock[] = [];
  for (let i = 1; i <= n; i++) blocks.push(block(i, { Ux: 10 ** (-i / 10), p: 10 ** (-1 - i / 20) }));
  return appendResidual(EMPTY_SERIES, blocks);
}

describe("decadeRange", () => {
  it("spans whole decades around the data and at least one", () => {
    expect(decadeRange([3e-2, 4e-6])).toEqual({ eMin: -6, eMax: -1 });
    expect(decadeRange([1e-3, 1e-3])).toEqual({ eMin: -3, eMax: -2 });
    expect(decadeRange([])).toEqual({ eMin: -6, eMax: 0 });
    expect(decadeRange([0, -1, Number.NaN])).toEqual({ eMin: -6, eMax: 0 });
  });
});

describe("niceTicks", () => {
  it("gives round values inside the range", () => {
    expect(niceTicks(0, 100, 5)).toEqual([0, 20, 40, 60, 80, 100]);
    expect(niceTicks(1, 52, 4).every((v) => v >= 1 && v <= 52)).toBe(true);
  });

  it("keeps iteration ticks whole numbers, even over a range of a few iterations", () => {
    expect(niceTicks(1, 2, 4, true)).toEqual([1, 2]);
    expect(niceTicks(1, 2, 4).some((v) => !Number.isInteger(v))).toBe(true);
  });
});

describe("buildChart", () => {
  it("draws one line per field and the target line on the axis", () => {
    const m = buildChart(falling(60), new Set(), 1e-4, 400, 160);
    expect(m.lines.map((l) => l.name)).toEqual(["Ux", "p"]);
    expect(m.target?.label).toBe("target 1e-4");
    expect(m.target!.y).toBeGreaterThan(m.y0);
    expect(m.target!.y).toBeLessThanOrEqual(m.y1);
    expect(m.lines[0].d.startsWith("M")).toBe(true);
  });

  it("puts the target inside the axis even when every residual is above it", () => {
    const m = buildChart(falling(5), new Set(), 1e-8, 400, 160);
    expect(m.eMin).toBeLessThanOrEqual(-8);
    expect(m.target!.y).toBeLessThanOrEqual(m.y1);
  });

  it("a hidden field is not drawn and does not stretch the axis", () => {
    const all = buildChart(falling(100), new Set(), null, 400, 160);
    const only = buildChart(falling(100), new Set(["Ux"]), null, 400, 160);
    expect(only.lines.map((l) => l.name)).toEqual(["p"]);
    expect(only.eMin).toBeGreaterThan(all.eMin);
  });

  it("breaks the line at a NaN and draws a lone number as a dot", () => {
    const s = appendResidual(EMPTY_SERIES, [block(1, { p: 1 }), block(2, { p: 0.5 }), block(3, { p: null }), block(4, { p: 0.2 }), block(5, { p: null }), block(6, { p: 0.1 }), block(7, { p: 0.05 })]);
    const line = buildChart(s, new Set(), null, 300, 120).lines[0];
    expect(line.d.match(/M/g)?.length).toBe(2); // 1-2 and 6-7
    expect(line.dots.length).toBe(1); // 4
  });

  it("a zero or negative number is a break, not a point at the bottom", () => {
    const s = appendResidual(EMPTY_SERIES, [block(1, { p: 1 }), block(2, { p: 0 }), block(3, { p: 0.1 })]);
    const line = buildChart(s, new Set(), null, 300, 120).lines[0];
    expect(line.d).toBe("");
    expect(line.dots.length).toBe(2);
  });

  it("thins a long history to a few points a pixel column and still ends at the last iteration", () => {
    const blocks: ResidualBlock[] = [];
    for (let i = 1; i <= 20000; i++) blocks.push(block(i, { Ux: 1 / i + 1e-6 * Math.abs(Math.sin(i)) }));
    const m = buildChart(appendResidual(EMPTY_SERIES, blocks), new Set(), null, 400, 160);
    const points = m.lines[0].d.split(/[ML]/).filter(Boolean).length;
    expect(points).toBeLessThan(4 * 400 * 2);
    expect(points).toBeGreaterThan(100);
    const last = m.lines[0].d.split(/[ML]/).filter(Boolean).pop()!.split(" ");
    expect(Number(last[0])).toBeCloseTo(m.x1, 0);
  });

  it("has y ticks on whole decades, top first, and x ticks inside the iteration range", () => {
    const m = buildChart(falling(52), new Set(), 1e-4, 400, 160);
    expect(m.yTicks[0].label).toMatch(/^(1|1e-?\d+)$/);
    expect(m.yTicks.every((t) => t.at >= m.y0 - 0.01 && t.at <= m.y1 + 0.01)).toBe(true);
    expect(m.xTicks.every((t) => t.value >= 1 && t.value <= 52)).toBe(true);
  });

  it("an empty series still has axes", () => {
    const m = buildChart(EMPTY_SERIES, new Set(), 1e-4, 300, 120);
    expect(m.lines).toEqual([]);
    expect(m.yTicks.length).toBeGreaterThan(0);
  });
});

describe("targetLabel", () => {
  it("prints a short exponent form", () => {
    expect(targetLabel(1e-4)).toBe("1e-4");
    expect(targetLabel(2.5e-5)).toBe("2.5e-5");
  });
});
