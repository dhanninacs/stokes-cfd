import { describe, expect, it } from "vitest";
import { isFolderNode, typeWord } from "./typeWord";

describe("a node's type in words", () => {
  it("calls a folder a folder", () => {
    // **The two the owner's picture showed**: the `tags` subtitle and the `parts` row.
    expect(typeWord({ type: "group", path: "/geometry/tags" })).toBe("folder");
    expect(typeWord({ type: "group", path: "/geometry" })).toBe("folder");
    expect(typeWord({ type: "group", path: "/mesh/results" })).toBe("folder");
  });

  it("keeps Group for the container an import makes", () => {
    // A multi-solid import makes a group so that picking it stands for all its parts, and that IS a
    // group in the owner's sense - the one they asked to be explained rather than renamed.
    expect(typeWord({ type: "group", path: "/geometry/parts/demo_pipe" })).toBe("group");
    expect(isFolderNode({ type: "group", path: "/geometry/parts/demo_pipe" })).toBe(false);
  });

  it("spells a compound type the way the page always has", () => {
    expect(typeWord({ type: "mesh_settings", path: "/mesh/settings" })).toBe("mesh settings");
    expect(typeWord({ type: "part", path: "/geometry/parts/box" })).toBe("part");
  });
});
