import { describe, expect, it } from "vitest";
import { claimKeyboard, keyboardOwner } from "../workspace/keyOwner";

describe("a modal editor claims the keyboard (review A2)", () => {
  it("holds it until it gives it back, and a stale release does not free a newer claim", () => {
    const release = claimKeyboard("sketch");
    expect(keyboardOwner()).toBe("sketch");
    const other = claimKeyboard("other");
    release();
    expect(keyboardOwner()).toBe("other");
    other();
    expect(keyboardOwner()).toBeNull();
  });
});
