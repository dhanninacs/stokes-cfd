import { describe, expect, it } from "vitest";
import { entryNameRefusal, mapEntries, mapValue, vectorComponent, withComponent } from "./compoundValues";
import { fieldKindOf } from "./schemaFields";

describe("vector components", () => {
  it("reads a stored component, with its expression and display", () => {
    const values = { "position.x": { value: 0.005, unit: "m", expr: "R" }, "position.y": { value: 0, unit: "m" } };
    expect(vectorComponent(values, { "position.x": "5 mm" }, "position", "x")).toEqual({ expr: "R", si: 0.005, display: "5 mm" });
    expect(vectorComponent(values, undefined, "position", "y")).toEqual({ expr: null, si: 0, display: null });
  });

  it("reads a card's list and replaces one component in it", () => {
    const values = { position: [0.01, "R / 2", 0] };
    expect(vectorComponent(values, undefined, "position", "y")).toEqual({ expr: "R / 2", si: null, display: null });
    expect(withComponent(values, "position", "z", "3 [mm]")).toEqual([0.01, "R / 2", "3 [mm]"]);
  });
});

describe("map entries", () => {
  it("lists stored entries by name, and a card's object", () => {
    const stored = { "dimensions.W": { value: 0.02, unit: "m", expr: "R * 4" }, "dimensions.a": { value: 0.5, unit: "rad" }, other: 1 };
    const entries = mapEntries(stored, { "dimensions.a": "28.6 deg" }, "dimensions");
    expect(entries.map((e) => [e.name, e.text])).toEqual([["a", "28.6 deg"], ["W", "R * 4"]]);
    expect(mapValue(entries)).toEqual({ W: "R * 4", a: "28.6 deg" });
    expect(mapEntries({ dimensions: { H: "10 [mm]" } }, undefined, "dimensions").map((e) => e.text)).toEqual(["10 [mm]"]);
  });

  it("refuses a bad or taken name", () => {
    expect(entryNameRefusal("2W", [])).not.toBeNull();
    expect(entryNameRefusal("W", ["W"])).toBe("W is already a dimension");
    expect(entryNameRefusal("W_2", ["W"])).toBeNull();
  });

  it("gives vectors and maps their own fields", () => {
    expect(fieldKindOf({ type: "object", "x-kind": "vector", "x-dimension": "length" })).toBe("vector");
    expect(fieldKindOf({ type: "object", "x-kind": "quantity_map" })).toBe("map");
  });
});
