import { describe, expect, it } from "vitest";
import { defaultDocument } from "./defaults";
import { hideSlot, showSlot, slotHidden } from "./panelToggle";
import type { LayoutSpec } from "./types";

/** The default arrangement, with the tree and the Details pane where the left dock puts them. */
const base = (): LayoutSpec => defaultDocument().layout;

describe("putting a dock away and bringing it back", () => {
  it("removes the left dock's panels entirely, so nothing is drawn", () => {
    // **Nothing is drawn means no panels**, not `collapsed: true`: a collapsed slot still draws its
    // 40 px strip of icons, which is what the owner had deleted this morning.
    const after = hideSlot(base(), "left");
    expect(after.left.panels).toEqual([]);
    expect(after.hidden).toEqual(expect.arrayContaining(["tree", "details"]));
    expect(slotHidden(after, "left")).toBe(true);
  });

  it("remembers the order, so opening it again is a round trip", () => {
    // **The round trip the owner asked to be tested**: hide then show gives back the same layout.
    const before = base();
    const shown = showSlot(hideSlot(before, "left"), "left");
    expect(shown.left.panels.map((p) => p.id)).toEqual(before.left.panels.map((p) => p.id));
    expect(shown.hidden.filter((id) => id === "tree" || id === "details")).toEqual([]);
    expect(slotHidden(shown, "left")).toBe(false);
  });

  it("is a no-op the second time, so a chord pressed twice cannot lose the layout", () => {
    // The failure that makes people stop using a collapse control: the second press empties the
    // stash, and the third brings nothing back.
    const once = hideSlot(base(), "left");
    const twice = hideSlot(once, "left");
    expect(twice.left.panels).toEqual([]);
    expect(showSlot(twice, "left").left.panels.map((p) => p.id)).toEqual(["tree", "details"]);
  });

  it("showing a dock with nothing stashed does nothing", () => {
    const layout = base();
    expect(showSlot(layout, "left")).toBe(layout);
  });

  it("works for the right dock, which starts empty", () => {
    // The right dock has no panels by default (the owner's "remove the right strip"), so hiding it
    // must not claim to have stashed anything.
    const layout = base();
    expect(hideSlot(layout, "right")).toBe(layout);
  });
});
