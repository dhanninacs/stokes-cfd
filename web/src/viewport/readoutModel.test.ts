import { describe, expect, it } from "vitest";
import { readoutText, valueAt } from "./readoutModel";

describe("the hover readout", () => {
  it("interpolates linearly on the triangle, exactly at a vertex", () => {
    const values = new Float32Array([1, 2, 4, 8]);
    expect(valueAt(values, [0, 1, 2], [1, 0, 0])).toBe(1);
    expect(valueAt(values, [0, 1, 2], [1 / 3, 1 / 3, 1 / 3])).toBeCloseTo(7 / 3, 6);
    expect(valueAt(values, [1, 2, 3], [0.5, 0.5, 0])).toBeCloseTo(3, 6);
  });

  it("answers nothing for a vertex with no value or out of range", () => {
    expect(valueAt(new Float32Array([1, Number.NaN, 3]), [0, 1, 2], [0.2, 0.3, 0.5])).toBeNull();
    expect(valueAt(new Float32Array([1, 2]), [0, 1, 2], [0.2, 0.3, 0.5])).toBeNull();
    expect(valueAt(new Float32Array([1, 2, 3]), [-1, 1, 2], [0.2, 0.3, 0.5])).toBeNull();
  });

  it("writes the symbol, the value as the legend writes it, and the units", () => {
    expect(readoutText("|U|", 0.31234, "m/s")).toBe("|U| 0.312 m/s");
    expect(readoutText("y+", 1.5, "")).toBe("y+ 1.5");
    expect(readoutText("p", 101325, "Pa", "fixed", 0)).toBe("p 101325 Pa");
  });
});
