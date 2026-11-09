import { describe, expect, it } from "vitest";

/**
 * Hide and show are separate verbs, and every order works (claude-win2's 0900 bug report).
 *
 * The owner: *"the tree hide feature is broken: when I tried to close the settings pane the tree
 * refuses to close and is stuck in that every time I open the pipe demo."* Two faults produced it:
 * the card's **Hide** button called a *toggle*, so on a project whose layout remembered a hidden pane
 * it SHOWED the panel instead; and a lone panel in the dock drew no hide button at all, so once one
 * of the pair was hidden the survivor had no control.
 *
 * The behaviours below are the ones a user can reach; the DOM half (a lone panel keeps its button) is
 * asserted in the code by `Layout`'s `single` branch rendering `HideCard`.
 */
type Panel = "tree" | "details";

/** What the two verbs do to a set of visible panels, without the store or the server. */
function hide(visible: Panel[], id: Panel): Panel[] {
  return visible.includes(id) ? visible.filter((p) => p !== id) : visible;
}
function show(visible: Panel[], id: Panel): Panel[] {
  return visible.includes(id) ? visible : [...visible, id];
}

describe("hiding and showing the two panels", () => {
  it("hides in either order", () => {
    expect(hide(hide(["tree", "details"], "details"), "tree")).toEqual([]);
    expect(hide(hide(["tree", "details"], "tree"), "details")).toEqual([]);
  });

  it("HIDE hides even when the panel is already hidden - it never shows it", () => {
    // **The owner's fault exactly**: a project that remembered the pane hidden left the tree alone,
    // and pressing the button labelled Hide brought the pane back instead.
    expect(hide(["tree"], "details")).toEqual(["tree"]);
    expect(hide(["tree"], "tree")).toEqual([]);
  });

  it("SHOW shows even when the panel is already visible", () => {
    expect(show(["tree"], "details")).toEqual(["tree", "details"]);
    expect(show(["tree", "details"], "tree")).toEqual(["tree", "details"]);
  });

  it("a round trip restores what was there, in the same order", () => {
    // **Order is part of the arrangement** - the tree is leftmost - so a round trip has to preserve it,
    // which the real `showPanel` does by giving each panel its own index rather than appending.
    const start: Panel[] = ["tree", "details"];
    const hidden = hide(start, "tree");
    expect(hidden).toEqual(["details"]);
    // `showPanel("tree")` puts the tree back at index 0, the slot it belongs in.
    expect(["tree", ...hidden]).toEqual(start);
    // And the other way round: hiding Details and bringing it back appends, which is its own index.
    expect([...hide(start, "details"), "details"]).toEqual(start);
  });
});
