/**
 * The Quality page's pure model (brief M-507): the verdict line, the histogram bars with the
 * limit's position, the three distribution states, and the cell-type rows with zeros hidden.
 */
import { describe, expect, it } from "vitest";
import {
  PENDING_LINE,
  axisLabel,
  barsFor,
  cellTypeRows,
  distributionState,
  isLogAxis,
  verdictLine,
  worstCentre,
  worstFaces,
  worstValue,
  type MetricHistogram,
} from "./qualityModel";

const LIMITS = { max_non_ortho: 65, max_skewness: 4 };

describe("verdictLine", () => {
  it("the ok verdict keeps the existing words", () => {
    expect(verdictLine(true, LIMITS, 5.95)).toBe("Mesh OK · within this project's limits");
  });

  it("a non-ortho over the limit names it and both numbers", () => {
    expect(verdictLine(true, LIMITS, 72)).toBe(
      "Quality not met: non-orthogonality 72° over the 65° limit",
    );
  });

  it("a failed mesh says so plainly", () => {
    expect(verdictLine(false, LIMITS, null)).toBe("Quality not met");
  });
});

describe("barsFor", () => {
  const metric: MetricHistogram = {
    unit: "deg",
    edges: [0, 10, 20, 30, 40],
    counts: [100, 50, 20, 3],
    max: 38,
    mean: 8,
  };

  it("one bar per bin, with the limit flag on the bins past it", () => {
    const bars = barsFor(metric, 25);
    expect(bars).toHaveLength(4);
    expect(bars[0]).toEqual({ from: 0, to: 10, count: 100, overLimit: false });
    // **A bin whose RIGHT edge crosses the limit is flagged**: it holds cells beyond it.
    expect(bars[2]).toEqual({ from: 20, to: 30, count: 20, overLimit: true });
    expect(bars[3].overLimit).toBe(true);
  });

  it("no limit means nothing is flagged", () => {
    expect(barsFor(metric, null).every((b) => !b.overLimit)).toBe(true);
  });

  it("an invalid metric has NO bars — the page shows the maximum and the reason", () => {
    const invalid: MetricHistogram = { ...metric, valid: false, reason: "the max disagrees with checkMesh" };
    expect(barsFor(invalid, 25)).toEqual([]);
  });
});

describe("the axis", () => {
  it("aspect ratio is a log axis; degrees are linear with the unit's label", () => {
    expect(isLogAxis("")).toBe(true);
    expect(isLogAxis("deg")).toBe(false);
    expect(axisLabel({ unit: "deg" } as MetricHistogram)).toBe("deg");
    expect(axisLabel({ unit: "" } as MetricHistogram)).toBe("ratio");
  });
});

describe("distributionState", () => {
  it("pending, ready, error — and absent before the marker exists", () => {
    expect(distributionState(undefined)).toBe("absent");
    expect(distributionState({ pending: true })).toBe("pending");
    expect(distributionState({ error: "the reader refused" })).toBe("error");
    expect(
      distributionState({ metrics: { non_ortho: { unit: "deg", edges: [0, 1], counts: [1], max: 1, mean: 1 } } }),
    ).toBe("ready");
  });

  it("the pending line's words: the verdict is ALREADY shown from checkMesh", () => {
    expect(PENDING_LINE).toBe("Working out the distributions…");
  });
});

describe("cellTypeRows", () => {
  it("the seven checkMesh types, zero rows hidden, in checkMesh's order", () => {
    const rows = cellTypeRows({
      hex: 3032, prism: 464, wedge: 0, pyramid: 0, tet_wedge: 0, tet: 0, poly: 79,
    });
    expect(rows).toEqual([
      { name: "hexahedra", count: 3032 },
      { name: "prisms", count: 464 },
      { name: "polyhedra", count: 79 },
    ]);
  });

  it("an absent table gives no rows", () => {
    expect(cellTypeRows(undefined)).toEqual([]);
  });
});

describe("worstFaces", () => {
  const reply = {
    worst: [
      { metric: "non_ortho", face: 12, value: 56.3, centre: [0.1, 0.2, 0.3] as [number, number, number], owner: 4, neighbour: 5 },
      { metric: "non_ortho", face: 13, value: 55.1, centre: [0.4, 0.2, 0.3] as [number, number, number], owner: 6, neighbour: 7 },
      { metric: "skewness", face: 99, value: 3.0, centre: [0, 0, 0] as [number, number, number], owner: 1, neighbour: null },
    ],
  };

  it("one metric's rows, worst first, capped at 10", () => {
    expect(worstFaces(reply, "non_ortho")).toHaveLength(2);
    expect(worstFaces(reply, "non_ortho")[0].face).toBe(12);
    expect(worstFaces(reply, "skewness")).toHaveLength(1);
    expect(worstFaces(reply, "aspect_ratio")).toEqual([]);
  });

  it("an absent reply gives no rows", () => {
    expect(worstFaces(undefined, "non_ortho")).toEqual([]);
  });

  it("the value and the position, trimmed for the table", () => {
    expect(worstValue(56.3099, "deg")).toBe("56.31°");
    expect(worstValue(3.0, "")).toBe("3");
    expect(worstCentre([0.12345, 1, 2])).toBe("0.1235, 1, 2");
  });
});
