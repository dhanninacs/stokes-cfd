import { describe, expect, it } from "vitest";
import { mmExpression, withoutMm } from "./mmText";

describe("mmExpression", () => {
  it("gives millimetres to the numbers that are added or subtracted", () => {
    expect(mmExpression("10 + c")).toBe("10 [mm] + c");
    expect(mmExpression("30 - c")).toBe("30 [mm] - c");
    expect(mmExpression("(W + 10) / 2")).toBe("(W + 10 [mm]) / 2");
    expect(mmExpression("2 * c + 10")).toBe("2 * c + 10 [mm]");
    expect(mmExpression("-c + 2.5")).toBe("-c + 2.5 [mm]");
  });

  it("leaves factors, divisors and exponents plain", () => {
    expect(mmExpression("W / 2")).toBe("W / 2");
    expect(mmExpression("2 * c")).toBe("2 * c");
    expect(mmExpression("c * 1.5")).toBe("c * 1.5");
    expect(mmExpression("c ^ 2")).toBe("c ^ 2");
  });

  it("does not touch a number that has a unit, a unit's own digits, or a name's digits", () => {
    expect(mmExpression("30 [deg]")).toBe("30 [deg]");
    expect(mmExpression("5 [mm] + c")).toBe("5 [mm] + c");
    expect(mmExpression("1 [m^2] / W")).toBe("1 [m^2] / W");
    expect(mmExpression("h2 + 3")).toBe("h2 + 3 [mm]");
    expect(mmExpression("sketch_1.W + 4")).toBe("sketch_1.W + 4 [mm]");
  });

  it("is undone for display", () => {
    expect(withoutMm(mmExpression("10 + c"))).toBe("10 + c");
    expect(withoutMm("30 [deg]")).toBe("30 [deg]");
  });
});
