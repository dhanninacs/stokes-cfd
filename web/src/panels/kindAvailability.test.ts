import { describe, expect, it } from "vitest";
import type { ConditionKind } from "../api/types";
import { kindState, unsupportedNote } from "./kindAvailability";

const kind = (id: string, extra: Partial<ConditionKind> = {}): ConditionKind => ({
  id,
  label: id,
  family: "x",
  fields: [],
  needs: {},
  color: "#fff",
  glyph: "",
  arrives: null,
  available: true,
  note: "",
  params: [],
  ...extra,
});

const MASS = kind("mass_flow_inlet", { supported: false, unsupported_reason: "Not in this solver yet" });
const LATER = kind("interface", { available: false, arrives: "a later release" });
const VELOCITY = kind("velocity_inlet", { supported: true });

describe("kindState", () => {
  it("allows a kind that is available and supported", () => {
    expect(kindState(VELOCITY)).toEqual({ enabled: true, reason: "" });
  });

  it("allows a kind from an older server that says nothing about support", () => {
    expect(kindState(kind("wall")).enabled).toBe(true);
  });

  it("disables a kind the engine cannot write, with the server's words", () => {
    expect(kindState(MASS)).toEqual({ enabled: false, reason: "Not in this solver yet" });
  });

  it("falls back to its own words when the server gives none", () => {
    expect(kindState(kind("x", { supported: false })).reason).toBe("Not in this solver yet");
  });

  it("disables a kind that arrives later and names when", () => {
    expect(kindState(LATER)).toEqual({ enabled: false, reason: "Arrives in a later release" });
  });
});

describe("unsupportedNote", () => {
  it("names a held kind the engine cannot write", () => {
    expect(unsupportedNote([VELOCITY, MASS], "mass_flow_inlet")).toBe("Not in this solver yet");
  });

  it("says nothing for a supported or unknown kind", () => {
    expect(unsupportedNote([VELOCITY, MASS], "velocity_inlet")).toBeNull();
    expect(unsupportedNote([VELOCITY], "nope")).toBeNull();
  });
});
