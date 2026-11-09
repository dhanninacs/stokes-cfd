import { describe, expect, it } from "vitest";
import { readShowAll, SHOW_ALL_KEY, writeShowAll } from "./outlineMode";

function fakeStorage(initial: Record<string, string> = {}) {
  const data = new Map(Object.entries(initial));
  return {
    data,
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, v),
    removeItem: (k: string) => void data.delete(k),
  };
}

describe("the tree's Show all rows choice", () => {
  it("is off until the user turns it on", () => {
    expect(readShowAll(fakeStorage())).toBe(false);
    expect(readShowAll(null)).toBe(false);
  });

  it("survives a reload: what was written is read back", () => {
    const s = fakeStorage();
    writeShowAll(s, true);
    expect(s.data.get(SHOW_ALL_KEY)).toBe("on");
    expect(readShowAll(s)).toBe(true);
    writeShowAll(s, false);
    expect(readShowAll(s)).toBe(false);
    expect(s.data.has(SHOW_ALL_KEY)).toBe(false);
  });

  it("shows the Basic tree when the browser refuses storage", () => {
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
    expect(readShowAll(refusing)).toBe(false);
    expect(() => writeShowAll(refusing, true)).not.toThrow();
  });
});
