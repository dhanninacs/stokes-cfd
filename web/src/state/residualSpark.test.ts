import { describe, expect, it } from "vitest";
import { residualSpark } from "./residualSpark";

describe("residualSpark", () => {
  it("is empty until there are two iterations", () => {
    expect(residualSpark(null, 40, 14)).toBe("");
    expect(residualSpark({ iterations: [1], fields: [{ name: "p", values: [1] }] }, 40, 14)).toBe("");
  });

  it("follows the worst field on a log scale, falling left to right", () => {
    const d = residualSpark(
      {
        iterations: [1, 2, 3],
        fields: [
          { name: "Ux", values: [1, 1e-2, 1e-4] },
          { name: "p", values: [1e-1, 1e-1, 1e-3] },
        ],
      },
      40,
      14,
    );
    // worst: 1, 1e-1, 1e-3 -> logs 0, -1, -3: top-left to bottom-right, the middle a third of the way down
    expect(d).toBe("M0.0 1.0L20.0 5.0L40.0 13.0");
  });

  it("breaks the line where no field reported", () => {
    const d = residualSpark({ iterations: [1, 2, 3, 4], fields: [{ name: "p", values: [1, Number.NaN, 1e-1, 1e-2] }] }, 30, 12);
    expect(d.match(/M/g)?.length).toBe(2);
  });

  it("keeps only the last window of iterations", () => {
    const n = 500;
    const series = { iterations: Array.from({ length: n }, (_, i) => i + 1), fields: [{ name: "p", values: Array.from({ length: n }, (_, i) => 10 ** (-i / 100)) }] };
    expect(residualSpark(series, 40, 14, 50).match(/[ML]/g)?.length).toBe(50);
  });
});
