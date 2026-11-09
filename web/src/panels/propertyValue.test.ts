import { describe, expect, it } from "vitest";
import { isComputed, propertyText, propertyWhy } from "./propertyValue";

describe("a property's value on a page", () => {
  it("shows the number when the method stores one", () => {
    expect(propertyText("constant", "1 007 J/(kg·K)")).toBe("1 007 J/(kg·K)");
  });

  it("shows a dash when the method computes it, which is the bug the owner saw", () => {
    // **`density 0 kg/m³` for air**, whose method is `ideal_gas`: the stored zero is a placeholder
    // for a number that depends on pressure and temperature.
    expect(propertyText("ideal_gas", "0 kg/m³")).toBe("—");
  });

  it("treats every method except `constant` as computed", () => {
    // Derived from the schema's own enum rather than a list kept here, so a new method is computed by
    // default - the safe direction: a wrong dash is a missing number, a wrong number is a wrong model.
    for (const method of ["ideal_gas", "boussinesq", "polynomial", "sutherland", "peng_robinson"]) {
      expect(isComputed(method)).toBe(true);
    }
    expect(isComputed("constant")).toBe(false);
    // An absent method is not a method: the value stands, because nothing said it was computed.
    expect(isComputed(undefined)).toBe(false);
    expect(isComputed("")).toBe(false);
  });

  it("explains the dash rather than leaving a puzzle", () => {
    expect(propertyWhy("ideal_gas")).toBe("Computed by the ideal gas model, not stored as a value");
    expect(propertyWhy("constant")).toBe("");
  });
});
