import { describe, expect, it } from "vitest";
import { fieldRoute, readRunFields, refusalText, sliceEdgesRoute, sliceRoute } from "./resultsApi";

const reply = {
  run: "r_7",
  time: 400,
  fields: [
    { name: "U_mag", units: "m/s", min: 0, max: 0.157 },
    { name: "p", units: "m2/s2", min: 0, max: 0.0267 },
    { name: "broken", units: "", min: "low", max: 1 },
    { units: "m" },
  ],
  slice: true,
  surfaces: "ok",
};

describe("readRunFields", () => {
  it("reads the reply of run.fields", () => {
    const r = readRunFields(reply);
    expect(r?.run).toBe("r_7");
    expect(r?.time).toBe(400);
    expect(r?.fields).toEqual([
      { name: "U_mag", units: "m/s", min: 0, max: 0.157 },
      { name: "p", units: "m2/s2", min: 0, max: 0.0267 },
    ]);
    expect(r?.slice).toBe(true);
    expect(r?.surfaces).toBe("ok");
  });

  it("gives neutral values for what is missing and reads surfaces missing", () => {
    const r = readRunFields({ run: "r_1", surfaces: "missing" });
    expect(r?.fields).toEqual([]);
    expect(r?.slice).toBe(false);
    expect(r?.time).toBe(0);
    expect(r?.surfaces).toBe("missing");
  });

  it("reads the server's two_d when it says one, and leaves twoD absent when it does not", () => {
    expect(readRunFields({ run: "r_1", two_d: true })?.twoD).toBe(true);
    expect(readRunFields({ run: "r_1", two_d: false })?.twoD).toBe(false);
    // An older server says nothing; a mistyped answer is no answer. Absent, so the box decides.
    expect(readRunFields({ run: "r_1" })).not.toHaveProperty("twoD");
    expect(readRunFields({ run: "r_1", two_d: "yes" })).not.toHaveProperty("twoD");
  });

  it("is null for a payload that is not one", () => {
    expect(readRunFields(null)).toBeNull();
    expect(readRunFields([])).toBeNull();
    expect(readRunFields({ fields: [] })).toBeNull();
    expect(readRunFields({ run: 7 })).toBeNull();
  });
});

describe("routes", () => {
  it("builds the three routes, encoding what is data", () => {
    expect(fieldRoute("r_7", "n_12", "U_mag")).toBe("/api/results/field/r_7/n_12?field=U_mag");
    expect(fieldRoute("r_7", "slice", "p")).toBe("/api/results/field/r_7/slice?field=p");
    expect(sliceRoute("r_7")).toBe("/api/results/slice/r_7");
    expect(sliceEdgesRoute("r_7")).toBe("/api/results/slice-edges/r_7");
    expect(fieldRoute("r/../x", "a b", "p&q")).toBe("/api/results/field/r%2F..%2Fx/a%20b?field=p%26q");
  });
});

describe("refusalText", () => {
  it("says the server's words, or a plain sentence", () => {
    expect(refusalText("run.not_ready", "The run has not finished")).toBe("The run has not finished");
    expect(refusalText("run.not_ready", "")).toBe("The run has no results to show yet.");
    expect(refusalText("other", "")).toBe("The results could not be read.");
  });
});
