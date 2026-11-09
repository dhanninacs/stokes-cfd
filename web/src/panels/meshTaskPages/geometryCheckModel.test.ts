import { describe, expect, it } from "vitest";
import { canShowClear, canStartCheck } from "./geometryCheckModel";

describe("Geometry check state", () => {
  it("shows a clean result only after a successful empty check", () => {
    expect(canShowClear("disconnected", 0)).toBe(false);
    expect(canShowClear("loading", 0)).toBe(false);
    expect(canShowClear("error", 0)).toBe(false);
    expect(canShowClear("ready", 0)).toBe(true);
    expect(canShowClear("ready", 1)).toBe(false);
  });

  it("allows a retry only while connected and idle", () => {
    expect(canStartCheck(false, "error", false)).toBe(false);
    expect(canStartCheck(true, "loading", false)).toBe(false);
    expect(canStartCheck(true, "ready", true)).toBe(false);
    expect(canStartCheck(true, "error", false)).toBe(true);
  });
});
