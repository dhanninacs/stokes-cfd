/** The Advanced disclosure's kept state: the key, the round trip, and a browser that refuses storage. */
import { describe, expect, it } from "vitest";
import { advancedKey, readAdvancedOpen, writeAdvancedOpen } from "./advancedState";

function memory(): Storage {
  const data = new Map<string, string>();
  return {
    get length() {
      return data.size;
    },
    clear: () => data.clear(),
    getItem: (k) => data.get(k) ?? null,
    key: (i) => [...data.keys()][i] ?? null,
    removeItem: (k) => void data.delete(k),
    setItem: (k, v) => void data.set(k, v),
  };
}

const refusing = {
  getItem: () => {
    throw new Error("blocked");
  },
  setItem: () => {
    throw new Error("blocked");
  },
  removeItem: () => {
    throw new Error("blocked");
  },
};

describe("advancedState", () => {
  it("keeps the choice per node type under stokes.setup.advanced.<type>", () => {
    expect(advancedKey("boundary")).toBe("stokes.setup.advanced.boundary");
  });

  it("is closed until it is left open, and survives a reload (a new read of the same storage)", () => {
    const storage = memory();
    expect(readAdvancedOpen(storage, "boundary")).toBe(false);
    writeAdvancedOpen(storage, "boundary", true);
    expect(readAdvancedOpen(storage, "boundary")).toBe(true);
    expect(readAdvancedOpen(storage, "region")).toBe(false);
    writeAdvancedOpen(storage, "boundary", false);
    expect(readAdvancedOpen(storage, "boundary")).toBe(false);
  });

  it("shows Advanced closed, and does not throw, when the browser refuses storage", () => {
    expect(readAdvancedOpen(refusing, "boundary")).toBe(false);
    expect(() => writeAdvancedOpen(refusing, "boundary", true)).not.toThrow();
    expect(readAdvancedOpen(null, "boundary")).toBe(false);
    expect(() => writeAdvancedOpen(null, "boundary", true)).not.toThrow();
  });
});
