/**
 * Hiding a part or tag in the view: one meaning for "Hide", and the id/name join behind it.
 *
 * **Why this is tested rather than eyeballed.** The whole point of the change is that two things
 * which LOOKED the same stop behaving differently, and that is invisible in a screenshot: the eye
 * either writes the model or it does not. The id/name join is the part with a real failure mode - the
 * tree hides ids, the scene draws names - and a wrong join either hides nothing or hides the wrong
 * part.
 */
import { describe, expect, it } from "vitest";
import type { NodeSummary } from "../api/types";
import { hiddenNodeNames, isNodeHidden, livingIds } from "./hiddenNodes";

function node(id: string, name: string, type: string, children: NodeSummary[] = []): NodeSummary {
  return { id, name, type, children } as unknown as NodeSummary;
}

/** A small tree: two parts and a tag, one part inside a nested group. */
const TREE = node("n_root", "/", "root", [
  node("n_parts", "parts", "group", [
    node("n_box", "box", "part"),
    node("n_group", "inner", "group", [node("n_deep", "deep_part", "part")]),
  ]),
  node("n_tags", "tags", "group", [node("n_inlet", "inlet", "tag")]),
]);

describe("hiddenNodeNames", () => {
  it("resolves a part's and a tag's node id to the name the scene draws", () => {
    // **The join: the tree hides ids, the scene hides names.** `scene.ts` asks
    // `!this.hidden.has(part.name)`, so an id passed through unchanged would hide nothing at all.
    expect(hiddenNodeNames(TREE, ["n_box"])).toEqual(["box"]);
    expect(hiddenNodeNames(TREE, ["n_inlet"])).toEqual(["inlet"]);
  });

  it("finds a part nested inside a group", () => {
    // The tree is walked, not scanned at the top: a part the user can see in an open group is one
    // they can hide.
    expect(hiddenNodeNames(TREE, ["n_deep"])).toEqual(["deep_part"]);
  });

  it("ignores a node that is not a part or a tag", () => {
    // **A group's eye is not offered**, and a group has no name the scene draws - so hiding one
    // would put a name in the set that matches a part by coincidence.
    expect(hiddenNodeNames(TREE, ["n_parts", "n_group", "n_root"])).toEqual([]);
  });

  it("drops an id the tree no longer holds", () => {
    // **The case that would hide the wrong thing.** A deleted `box` must not leave the name behind,
    // where a later part called `box` would appear pre-hidden for no discoverable reason.
    expect(hiddenNodeNames(TREE, ["n_gone"])).toEqual([]);
    expect(hiddenNodeNames(TREE, ["n_box", "n_gone"])).toEqual(["box"]);
  });

  it("answers nothing for no tree or no ids", () => {
    // A viewport with no project, and a session where nothing is hidden: both are ordinary, and
    // neither is an error.
    expect(hiddenNodeNames(null, ["n_box"])).toEqual([]);
    expect(hiddenNodeNames(TREE, [])).toEqual([]);
  });
});

describe("livingIds", () => {
  it("keeps what the tree still holds and forgets the rest", () => {
    expect(livingIds(TREE, ["n_box", "n_gone", "n_inlet"])).toEqual(["n_box", "n_inlet"]);
  });

  it("forgets everything when there is no tree", () => {
    // A project that closed: nothing is hidden any more, because nothing is drawn.
    expect(livingIds(TREE, [])).toEqual([]);
    expect(livingIds(null, ["n_box"])).toEqual([]);
  });
});

describe("isNodeHidden", () => {
  it("is hidden when the SESSION says so", () => {
    expect(isNodeHidden("n_box", ["n_box"], true)).toBe(true);
  });

  it("is hidden when the MODEL says so", () => {
    // The model's flag is still honoured: `geometry.style` writes it, a headless caller may set it,
    // and a project saved with a part hidden must draw it hidden.
    expect(isNodeHidden("n_box", [], false)).toBe(true);
  });

  it("is shown when neither says so, and when the model does not say", () => {
    expect(isNodeHidden("n_box", [], true)).toBe(false);
    // `null` is "the model has no opinion", which is not the same as false.
    expect(isNodeHidden("n_box", [], null)).toBe(false);
  });
});
