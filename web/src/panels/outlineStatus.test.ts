import { describe, expect, it } from "vitest";
import type { SetupCheck } from "../api/types";
import { findingsByNode, outlineStateAdapter, stageStateOf, stateOfNode, todoNodes } from "./outlineStatus";

/**
 * The outline's state, from the app's REAL sources (G-306).
 *
 * **Representative findings, not stubs**: the shapes here are the ones `setup.check` actually answers -
 * a missing material, a boundary with no condition, a body left out of the simulation - because a stub
 * that cannot occur proves nothing about the binding.
 */
const check = (findings: SetupCheck["findings"]): SetupCheck => ({
  state: "incomplete",
  next: "materials",
  counts: { regions: 1, materials: 0, boundaries: 3, parts: 1 },
  findings,
});

/** The server's own words for a region with no material. */
const NEEDS_MATERIAL = {
  code: "setup.no_material",
  severity: "error" as const,
  message: "needs a material",
  nodes: ["n_region_fluid"],
  suggestion: "Set a material on the fluid region.",
};

describe("findingsByNode", () => {
  it("binds a finding to the nodes it names", () => {
    const bound = findingsByNode(check([NEEDS_MATERIAL]));
    expect(bound.get("n_region_fluid")?.message).toBe("needs a material");
  });

  it("ignores a finding about the PROJECT, which has nowhere to land in the tree", () => {
    const projectWide = { code: "setup.no_engine", severity: "error" as const, message: "choose an engine" };
    expect(findingsByNode(check([projectWide])).size).toBe(0);
  });

  it("keeps the FIRST finding for a node, which is the server's most severe", () => {
    const second = { code: "setup.other", severity: "error" as const, message: "second", nodes: ["n_region_fluid"] };
    expect(findingsByNode(check([NEEDS_MATERIAL, second])).get("n_region_fluid")?.message).toBe("needs a material");
  });
});

describe("stateOfNode", () => {
  const base = { setup: null, run: null, meshStale: false, hasMesh: true, resultsShown: false };

  it("marks a node with an ERROR finding as todo - the missing-material case", () => {
    const input = { ...base, setup: check([NEEDS_MATERIAL]) };
    expect(stateOfNode("n_region_fluid", input, findingsByNode(input.setup))).toBe("todo");
  });

  it("does NOT mark a node todo for a WARNING or an info", () => {
    // A body left out of the simulation is information, not something to do - the brief says so.
    const warning = { ...NEEDS_MATERIAL, severity: "warning" as const, message: "not in the simulation" };
    const input = { ...base, setup: check([warning]) };
    expect(stateOfNode("n_region_fluid", input, findingsByNode(input.setup))).toBe("idle");
  });

  it("lets the RUN's own state win for the run row - a failure is newer than a setup note", () => {
    const input = { ...base, setup: check([{ ...NEEDS_MATERIAL, nodes: ["r_1"] }]), run: { id: "r_1", state: "failed" } };
    expect(stateOfNode("r_1", input, findingsByNode(input.setup))).toBe("failed");
    const running = { ...input, run: { id: "r_1", state: "running" } };
    expect(stateOfNode("r_1", running, findingsByNode(running.setup))).toBe("running");
  });

  it("says DONE for a run that finished, converged or not", () => {
    const input = { ...base, run: { id: "r_1", state: "done_unconverged" } };
    expect(stateOfNode("r_1", input, findingsByNode(null))).toBe("done");
  });

  it("leaves an unknown node idle rather than guessing", () => {
    expect(stateOfNode("n_nothing", base, findingsByNode(null))).toBe("idle");
  });
});

describe("the adapter's value text", () => {
  it("shows the SERVER's words for a bound finding", () => {
    // "needs a material" is the server's message, not a phrase this module invents.
    const input = { setup: check([NEEDS_MATERIAL]), run: null, meshStale: false, hasMesh: true, resultsShown: false };
    const adapter = outlineStateAdapter(input);
    expect(adapter.valueOf("n_region_fluid")).toBe("needs a material");
    expect(adapter.valueOf("n_something_else")).toBe("");
  });
});

describe("stageStateOf - the states the Setup page does not report", () => {
  const base = { setup: null, run: null, meshStale: false, hasMesh: false, resultsShown: false };

  it("says Mesh is todo without a mesh, and STALE when the mesh is out of date", () => {
    expect(stageStateOf("Mesh", base)).toBe("todo");
    expect(stageStateOf("Mesh", { ...base, hasMesh: true })).toBe("ready");
    // **Stale is a different fact from "not done"** - the mesh exists and no longer matches the model.
    expect(stageStateOf("Mesh", { ...base, hasMesh: true, meshStale: true })).toBe("stale");
  });

  it("reads the Solve stage from the run", () => {
    expect(stageStateOf("Solve", base)).toBe("idle");
    expect(stageStateOf("Solve", { ...base, run: { id: "r", state: "running" } })).toBe("running");
    expect(stageStateOf("Solve", { ...base, run: { id: "r", state: "failed" } })).toBe("failed");
    expect(stageStateOf("Solve", { ...base, run: { id: "r", state: "done" } })).toBe("done");
  });

  it("says Results is ready only when they are on screen", () => {
    expect(stageStateOf("Results", base)).toBe("idle");
    expect(stageStateOf("Results", { ...base, resultsShown: true })).toBe("ready");
  });
});

describe("todoNodes", () => {
  it("lists the nodes an error is bound to, and works on REAL node objects", () => {
    const nodes = [
      { id: "n_region_fluid", name: "fluid", type: "region" },
      { id: "n_region_solid", name: "solid", type: "region" },
    ] as never;
    expect(todoNodes(nodes, check([NEEDS_MATERIAL]))).toEqual(["n_region_fluid"]);
  });
});
