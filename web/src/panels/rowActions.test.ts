/**
 * Which rows get a menu button (claude-win2's 0200 item 2, the tree guide's finding 2).
 *
 * **The complaint**: right-click and the "…" button on a row with nothing to offer looked the row up
 * in the raw tree, failed, and closed itself - so the Flow model, the "not set yet" placeholders, run
 * rows and result rows showed a button that did nothing. A row either has a menu with a real entry or
 * none.
 *
 * The rule is deliberately small: **"no actions" is a named set, not a judgement**. A general
 * predicate would become a second opinion about the menu's contents, and the first time it disagreed
 * with the menu builder a row would hide a menu that had something in it.
 */
import { describe, expect, it } from "vitest";
import { rowHasActions } from "./nodeRules";

describe("rowHasActions", () => {
  it("gives every NODE a menu", () => {
    // A node can always be renamed, deleted, or opened in its own panel; a fixed folder is refused
    // the first two WITH A REASON, which is an action, not an absence.
    expect(rowHasActions("node", false)).toBe(true);
    expect(rowHasActions("node", true)).toBe(true);
  });

  it("gives a virtual row a menu only when it OPENS something", () => {
    // **The case the finding names.** A run, a field and a stage folder open a panel - they keep
    // their menu. A "not set yet" placeholder does nothing at all, so it gets neither the menu nor
    // the button.
    expect(rowHasActions("virtual", true)).toBe(true);
    expect(rowHasActions("virtual", false)).toBe(false);
  });

  it("gives a count row nothing", () => {
    // `3 items` is a label on a folder, not a thing that can be acted on.
    expect(rowHasActions("count", true)).toBe(false);
    expect(rowHasActions("count", false)).toBe(false);
  });
});
