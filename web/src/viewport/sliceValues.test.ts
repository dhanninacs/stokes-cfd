import { describe, expect, it } from "vitest";
import { requestKeyOf, requestedFieldOf, reducedForTint, toSliceValues } from "./sliceValues";
import { writeField } from "../state/fieldCodecSupport";

/**
 * The one place a slice's values are prepared (claude-win2's 0300 answer).
 *
 * **These are the rules that bit three rounds running**: a speed is REQUESTED as its velocity, the cache
 * is keyed on what was actually asked for, and a reduced field must be marked `components: 1` or the
 * tint refuses it - silently, until this round.
 */
const vector = (vals: number[]) => writeField({ field: "U", components: 3, values: vals, min: 0, max: 5 });

describe("requestedFieldOf", () => {
  it("asks a SLICE's speed as its velocity, so one fetch serves the colouring and the arrows", () => {
    expect(requestedFieldOf("slice", "U_mag")).toBe("U");
  });

  it("leaves a PATCH alone: a wall has no arrows, so nobody asked for its vector", () => {
    expect(requestedFieldOf("n_a0c7d2a6", "U_mag")).toBe("U_mag");
  });

  it("leaves every field that is not a speed alone", () => {
    expect(requestedFieldOf("slice", "p")).toBe("p");
  });
});

describe("requestKeyOf", () => {
  it("keys on the REQUESTED name, so the fetch lands where the read looks", () => {
    // Keying on `U_mag` while fetching `U` stores the answer under a name nobody reads.
    expect(requestKeyOf(3, "slice", "U_mag")).toBe("3|slice|U");
  });

  it("keeps the shape the cache already uses for patches", () => {
    expect(requestKeyOf(3, "n_a", "p")).toBe("3|n_a|p");
  });
});

describe("toSliceValues", () => {
  it("reduces a vector to ONE component per vertex, and SAYS so", () => {
    // `patchTint` refuses any other component count, and it used to refuse in silence.
    const values = toSliceValues(vector([3, 4, 0, 0, 0, 2]))!;
    expect(values.field.components).toBe(1);
    expect(Array.from(values.vertex)).toEqual([5, 2]);
    expect(values.field.count).toBe(2);
  });

  it("keeps the field's own name and range", () => {
    const values = toSliceValues(vector([0, 0, 0, 3, 4, 0]))!;
    expect(values.requested).toBe("U");
    expect(values.field.min).toBe(0);
    expect(values.field.max).toBe(5);
  });

  it("returns null for bytes that are not a field, rather than throwing at a caller", () => {
    expect(toSliceValues(new ArrayBuffer(3))).toBeNull();
  });
});

describe("reducedForTint", () => {
  it("passes a scalar through untouched, so nothing is copied per redraw", () => {
    const scalar = toSliceValues(writeField({ field: "p", values: [1, 2, 3] }))!.field;
    expect(reducedForTint(scalar)).toBe(scalar);
  });

  it("reduces a vector and marks it one component", () => {
    // A raw parsed vector: three floats per vertex, as the codec hands it over before reduction.
    const raw = { field: "U", units: "m/s", components: 3, count: 1, min: 0, max: 5, values: new Float32Array([3, 4, 0]) };
    const reduced = reducedForTint(raw)!;
    expect(reduced.components).toBe(1);
    expect(Array.from(reduced.values)).toEqual([5]);
  });

  it("keeps null as null", () => {
    expect(reducedForTint(null)).toBeNull();
  });
});
