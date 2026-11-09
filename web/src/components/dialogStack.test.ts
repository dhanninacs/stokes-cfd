import { describe, expect, it } from "vitest";
import { DialogStack } from "./dialogStack";

/**
 * The dialog stack's rules, tested as ARITHMETIC because that is where it broke.
 *
 * The bug (G-301's H-3) was not in `Dialog`'s behaviour but in a count: a re-run mount effect pushed
 * twice and removed once, so the stack never emptied and `isTop` was false for every dialog. A test
 * that rendered a dialog and pressed Escape would have caught it only with the exact prop change that
 * re-runs the effect; testing the operation catches the class.
 */
describe("DialogStack", () => {
  it("knows which dialog is on top", () => {
    const stack = new DialogStack();
    const outer = stack.push(Symbol("outer"));
    expect(stack.isTop(outer)).toBe(true);
    const inner = stack.push(Symbol("inner"));
    // The nested dialog takes the key, and the one beneath keeps it back.
    expect(stack.isTop(inner)).toBe(true);
    expect(stack.isTop(outer)).toBe(false);
  });

  it("returns the key to the dialog underneath when the top one closes", () => {
    const stack = new DialogStack();
    const outer = stack.push(Symbol("outer"));
    const inner = stack.push(Symbol("inner"));
    stack.remove(inner);
    expect(stack.isTop(outer)).toBe(true);
    expect(stack.depth).toBe(1);
  });

  it("survives a PUSH for a token already on the stack", () => {
    // **The failure**: `useEffect` keyed `[focus, id]` ran twice for one open dialog. Two entries, one
    // cleanup, and the stack never emptied - so Escape's `isTop` guard refused to close anything and
    // the scrim stayed up over the whole window.
    const stack = new DialogStack();
    const id = Symbol("one");
    const first = stack.push(id);
    const second = stack.push(id);
    expect(stack.depth).toBe(1);
    stack.remove(second);
    stack.remove(first);
    expect(stack.depth).toBe(0);
    // And the SAME removal twice - a cleanup can run after the dialog is already gone.
    stack.remove(first);
    expect(stack.depth).toBe(0);
  });

  it("is not on top when the stack is empty", () => {
    // A token removed twice must not be treated as top by accident: an empty stack has no top, and
    // answering `true` here would let a stale Escape close something it does not own.
    const stack = new DialogStack();
    const only = stack.push(Symbol("only"));
    stack.remove(only);
    expect(stack.isTop(only)).toBe(false);
  });

  it("keeps a lower dialog out of the way while a higher one is open", () => {
    const stack = new DialogStack();
    const a = stack.push(Symbol("a"));
    const b = stack.push(Symbol("b"));
    const c = stack.push(Symbol("c"));
    expect(stack.isTop(a)).toBe(false);
    expect(stack.isTop(b)).toBe(false);
    expect(stack.isTop(c)).toBe(true);
  });
});
