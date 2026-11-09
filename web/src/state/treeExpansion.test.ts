/**
 * The launch expansion: only the current stage's subtree opens (see `treeExpansion.ts`).
 *
 * The complaint these cover is a user-visible one: "on launch the tree should look like this with
 * only geometry extended, currently everything is extended." The old rule, `expansionFor(tree, 2)`,
 * walks the whole tree, so it opened the first two levels of EVERY top-level group.
 */
import { afterEach, describe, expect, it } from "vitest";

import { setStageMap } from "../workspace/stageMap";
import { stageScopedExpansion } from "./treeExpansion";

/** A node as the tree sends it: an id, a path, and its children. */
interface N {
  id: string;
  path: string;
  children?: N[];
}
const node = (id: string, path: string, children: N[] = []): N => ({ id, path, children });

/** The stage table the workspace ships (stageMap's own default), restored after each test. */
const STAGES = {
  Geometry: ["/geometry"],
  Mesh: ["/mesh"],
  Physics: ["/physics", "/regions"],
  Solve: ["/solver", "/monitors", "/runs"],
  Results: ["/scenes", "/runs"],
  Explore: ["/studies"],
};

/** A small project shaped like the demo: one part with solids, tags, and something in each stage. */
function demoTree(): N {
  return node("root", "", [
    node("geometry", "/geometry", [
      node("parts", "/geometry/parts", [
        node("demo_pipe", "/geometry/parts/demo_pipe", [
          node("pipe", "/geometry/parts/demo_pipe/pipe"),
          node("inlet_flange", "/geometry/parts/demo_pipe/inlet_flange"),
        ]),
      ]),
      node("tags", "/geometry/tags", [
        node("inlet", "/geometry/tags/inlet"),
        node("walls", "/geometry/tags/walls"),
      ]),
    ]),
    node("mesh", "/mesh"),
    // Every group here has a child, as the real tree's groups do (`/physics/parameters` holds the
    // six parameter rows). A fixture whose `parameters` was childless made the depth rule look wrong
    // for Physics when it was the fixture that did not match the tree the server sends.
    node("physics", "/physics", [
      node("parameters", "/physics/parameters", [node("U_in", "/physics/parameters/U_in")]),
      node("materials", "/physics/materials", [node("water", "/physics/materials/water_liquid")]),
    ]),
    // As the server sends it: `/regions/fluid` holds its `boundaries` group (the dump of the live
    // tree shows children=1 there). A childless `fluid` in the fixture hid that it should open.
    node("regions", "/regions", [
      node("fluid", "/regions/fluid", [node("boundaries", "/regions/fluid/boundaries")]),
    ]),
    node("scenes", "/scenes", [node("scene_1", "/scenes/scene_1")]),
    node("studies", "/studies", [node("sweep", "/studies/velocity_sweep")]),
  ]);
}

const openIds = (expanded: Record<string, boolean>): string[] =>
  Object.entries(expanded)
    .filter(([, open]) => open)
    .map(([id]) => id)
    .sort();

afterEach(() => setStageMap(STAGES, true, 2));

describe("stageScopedExpansion", () => {
  it("opens only the current stage's subtree, and folds every other group", () => {
    setStageMap(STAGES, true, 2);

    const open = openIds(stageScopedExpansion("Geometry", demoTree(), 2));

    // Exactly the owner's target tree: `/geometry`, `/geometry/parts`, `/geometry/tags` AND
    // `/geometry/parts/demo_pipe` open, with the parts and tags visible and every other group
    // folded. Depth 2 reaches `demo_pipe` because the depth counts levels BELOW `/geometry`.
    expect(open).toEqual(["demo_pipe", "geometry", "parts", "tags"]);
  });

  it("does not open the groups of the other stages, nor anything inside them", () => {
    setStageMap(STAGES, true, 2);

    const open = openIds(stageScopedExpansion("Geometry", demoTree(), 2));

    // The old rule (`expansionFor(tree, 2)`) opened every one of these: the complaint was
    // "everything is extended", and these are the rows that made it look so.
    for (const id of ["mesh", "physics", "parameters", "regions", "fluid", "scenes", "scene_1", "studies", "sweep"]) {
      expect(open).not.toContain(id);
    }
  });

  it("opens the same subtree in every stage, chosen by the stage it is given", () => {
    setStageMap(STAGES, true, 2);

    // /mesh is childless on this project. The GROUP still opens — it is the Mesh stage's group and
    // has a row to show — there is simply nothing below it to mark.
    expect(openIds(stageScopedExpansion("Mesh", demoTree(), 2))).toEqual(["mesh"]);

    // Physics owns /physics and /regions, so BOTH open with their children (`parameters`, `fluid`);
    // Geometry's whole subtree folds, including the group rows it had open a moment ago.
    const physics = openIds(stageScopedExpansion("Physics", demoTree(), 2));
    // Physics owns /physics and /regions: both open, with `parameters` and `materials` below /physics
    // and `fluid` below /regions. `/physics/parameters/U_in` is a childless leaf, so it is not marked.
    expect(physics).toEqual(["fluid", "materials", "parameters", "physics", "regions"]);
    expect(physics).not.toContain("geometry");
    expect(physics).not.toContain("parts");
  });

  it("opens the groups of a stage that lists a sub-group, with the group on its way", () => {
    // The Results stage lists /scenes and /runs; a project with no runs still opens /scenes.
    setStageMap(STAGES, true, 2);

    const open = openIds(stageScopedExpansion("Results", demoTree(), 2));
    // `/scenes` opens. `scene_1` is childless, so it draws no chevron and is not marked open — the
    // `stageOpens` half is what put `/scenes` on the list, not anything inside it.
    expect(open).toEqual(["scenes"]);
    expect(open).not.toContain("geometry");
  });

  it("folds everything at depth 0, including the stage's own groups", () => {
    setStageMap(STAGES, true, 0);

    // "0 all folded" is the workspace schema's promise for `tree.defaultExpanded`, and it has to
    // fold the stage's groups too — not only the ones this function leaves alone.
    const open = openIds(stageScopedExpansion("Geometry", demoTree(), 0));
    expect(open).toEqual([]);
  });

  it("opens only the top-level groups at depth 1", () => {
    setStageMap(STAGES, true, 1);

    // Depth 1 opens the stage group and its immediate children: `/geometry`, `/geometry/parts`,
    // `/geometry/tags`. `/geometry/parts/demo_pipe` stays folded.
    const open = openIds(stageScopedExpansion("Geometry", demoTree(), 1));
    expect(open).toEqual(["geometry", "parts", "tags"]);
  });

  it("opens a deeper tree to the depth asked, and no further", () => {
    setStageMap(STAGES, true, 2);

    // A part whose solids are themselves grouped one level deeper than the demo's.
    const deep = node("root", "", [
      node("geometry", "/geometry", [
        node("parts", "/geometry/parts", [
          node("asm", "/geometry/parts/assembly", [
            node("sub", "/geometry/parts/assembly/sub", [node("leaf", "/geometry/parts/assembly/sub/leaf")]),
          ]),
        ]),
      ]),
      node("mesh", "/mesh"),
    ]);
    // Depth 2 reaches `asm` but not `sub`, so the tree stops there rather than opening everything.
    expect(openIds(stageScopedExpansion("Geometry", deep, 2))).toEqual(["asm", "geometry", "parts"]);
    expect(openIds(stageScopedExpansion("Geometry", deep, 3))).toEqual(["asm", "geometry", "parts", "sub"]);
  });

  it("does not mark childless nodes open", () => {
    setStageMap(STAGES, true, 2);

    // A leaf draws no chevron, so `expanded[leafId] = true` would be an entry that means nothing.
    const open = stageScopedExpansion("Geometry", demoTree(), 8);
    expect(open.pipe).toBeUndefined();
    expect(open.inlet_flange).toBeUndefined();
    expect(open.walls).toBeUndefined();
  });

  it("has nothing to open with no tree, and does not throw", () => {
    setStageMap(STAGES, true, 2);

    expect(stageScopedExpansion("Geometry", null, 2)).toEqual({});
  });

  it("keeps an expansion it was given for nodes it does not own", () => {
    // The caller passes what the store already holds. Nodes below the stage's groups are the user's
    // to have opened, so a value they set is preserved rather than reset — the rule the chip's
    // `stageExpanded` also follows, so switching stages does not undo the user's work.
    setStageMap(STAGES, true, 2);

    const next = stageScopedExpansion("Geometry", demoTree(), 2, { some_deep_node: true });
    expect(next.some_deep_node).toBe(true);
  });

  it("folds a group that was open when the tree loads", () => {
    // A tree arriving after a previous project was open must not inherit that project's expansion:
    // `expanded` maps ids, and the new project's ids differ, but the top-level groups' ids can
    // repeat, so the stage rule must be authoritative for them.
    setStageMap(STAGES, true, 2);

    const next = stageScopedExpansion("Geometry", demoTree(), 2, { mesh: true, physics: true });
    expect(next.mesh).toBe(false);
    expect(next.physics).toBe(false);
  });
});
