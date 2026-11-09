import { describe, expect, it } from "vitest";
import { MODE_KEY, effectiveMode, readMode, readStoredMode, writeStoredMode } from "./modeModel";

describe("guided mode choice", () => {
  it("reads only the two known modes", () => {
    expect(readMode("guided")).toBe("guided");
    expect(readMode("full")).toBe("full");
    expect(readMode("expert")).toBeNull();
    expect(readMode(undefined)).toBeNull();
  });

  it("defaults to guided when nothing says otherwise", () => {
    expect(effectiveMode(null, undefined)).toBe("guided");
    expect(effectiveMode(null, "nonsense")).toBe("guided");
  });

  it("lets the user's choice outrank the document", () => {
    expect(effectiveMode("full", "guided")).toBe("full");
    expect(effectiveMode(null, "full")).toBe("full");
  });

  it("keeps the choice in storage and survives a storage that throws", () => {
    const values = new Map<string, string>();
    const storage = { getItem: (k: string) => values.get(k) ?? null, setItem: (k: string, v: string) => void values.set(k, v) };
    expect(readStoredMode(storage)).toBeNull();
    writeStoredMode(storage, "full");
    expect(values.get(MODE_KEY)).toBe("full");
    expect(readStoredMode(storage)).toBe("full");

    const refusing = {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
    };
    expect(readStoredMode(refusing)).toBeNull();
    expect(() => writeStoredMode(refusing, "guided")).not.toThrow();
    expect(readStoredMode(null)).toBeNull();
  });
});
