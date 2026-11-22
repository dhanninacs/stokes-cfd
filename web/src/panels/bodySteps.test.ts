import { describe, expect, it } from "vitest";
import type { NodeSummary } from "../api/types";
import {
  consumerOf,
  dependentsOf,
  enterRunsCard,
  nestConsumedSteps,
  operationOf,
  removalPlan,
  removalQuestion,
  roleWords,
  sayList,
  swapTarget,
  targetAndTools,
} from "./bodySteps";

const node = (id: string, type: string, name: string, path: string, params: Record<string, unknown> = {}, children?: NodeSummary[]): NodeSummary => ({
  id,
  type,
  name,
  status: "ok",
  params: params as NodeSummary["params"],
  path,
  children_count: children?.length ?? 0,
  ...(children ? { children } : {}),
});

const part = (id: string, name: string, derived = "", hidden = false) =>
  node(id, "part", name, `/geometry/parts/${name}`, { family: "implicit", derived, ...(hidden ? { visible: false, simulate: false } : {}) });
const op = (id: string, type: string, name: string, params: Record<string, unknown>) => node(id, type, name, `/geometry/operations/${name}`, params);

/** Cylinder, moved, then cut out of a plate: the review's "one part with one hole". */
function model(): NodeSummary {
  const cyl = op("o_cyl", "implicit_primitive", "implicit_primitive_1", { kind: "cylinder", inputs: [], outputs: ["p_cyl"] });
  const plate = op("o_plate", "implicit_sketch", "implicit_sketch_1", { profile: "custom", inputs: [], outputs: ["p_plate"] });
  const move = op("o_move", "implicit_transform", "implicit_transform_1", { kind: "translate", inputs: ["p_cyl"], outputs: ["p_moved"] });
  const cut = op("o_cut", "implicit_boolean", "implicit_boolean_1", { op: "difference", inputs: ["p_plate"], tools: ["p_moved"], outputs: ["p_holed"] });
  const parts = node("g_parts", "group", "parts", "/geometry/parts", {}, [
    part("p_cyl", "cylinder", "o_cyl", true),
    part("p_plate", "extrusion", "o_plate", true),
    part("p_moved", "cylinder_2", "o_move", true),
    part("p_holed", "extrusion_2", "o_cut"),
  ]);
  const ops = node("g_ops", "group", "operations", "/geometry/operations", {}, [cyl, plate, move, cut]);
  const geometry = node("g_geo", "group", "geometry", "/geometry", {}, [parts, ops]);
  return node("root", "project", "demo", "/", {}, [geometry]);
}

const find = (tree: NodeSummary, id: string): NodeSummary => {
  const stack = [tree];
  while (stack.length) {
    const at = stack.pop() as NodeSummary;
    if (at.id === id) return at;
    stack.push(...(at.children ?? []));
  }
  throw new Error(id);
};

describe("bodies and the operations that make and read them", () => {
  it("finds the operation that made a body", () => {
    const tree = model();
    expect(operationOf(tree, "p_holed")?.id).toBe("o_cut");
    expect(operationOf(tree, "p_cyl")?.id).toBe("o_cyl");
    expect(operationOf(tree, "nope")).toBeNull();
  });

  it("finds everything built from a body, transitively, upstream first", () => {
    const tree = model();
    const deps = dependentsOf(tree, "p_cyl");
    expect(deps.operations.map((o) => o.id)).toEqual(["o_move", "o_cut"]);
    expect(deps.bodies.map((b) => b.id)).toEqual(["p_moved", "p_holed"]);
    expect(dependentsOf(tree, "p_holed").operations).toEqual([]);
  });

  it("plans a delete that leaves no operation reading a gone body (review A7)", () => {
    const tree = model();
    const plan = removalPlan(tree, find(tree, "p_cyl"));
    expect(plan?.operations.map((o) => o.id)).toEqual(["o_move", "o_cut"]);
    // The body is its operation's only output: deleting the body deletes that operation.
    expect(plan?.maker?.id).toBe("o_cyl");
    const question = removalQuestion(find(tree, "p_cyl"), plan!);
    expect(question?.body).toContain("implicit_transform_1 and implicit_boolean_1 are built from cylinder");
    expect(question?.body).toContain("bodies cylinder_2 and extrusion_2");
    expect(question?.confirmLabel).toBe("Delete all 3");
    // The last body: nothing is built from it, so no question, but its operation goes with it.
    const last = removalPlan(tree, find(tree, "p_holed"));
    expect(last?.operations).toEqual([]);
    expect(last?.maker?.id).toBe("o_cut");
    expect(removalQuestion(find(tree, "p_holed"), last!)).toBeNull();
    // A node that is neither a body nor an operation goes alone.
    expect(removalPlan(tree, find(tree, "g_parts"))).toBeNull();
    // An operation takes what is built from its outputs.
    expect(removalPlan(tree, find(tree, "o_move"))?.operations.map((o) => o.id)).toEqual(["o_cut"]);
  });

  it("keeps consumed steps under the operation that consumed them (review B8)", () => {
    const tree = model();
    expect(consumerOf(tree, find(tree, "p_cyl"))?.id).toBe("o_move");
    expect(consumerOf(tree, find(tree, "p_holed"))).toBeNull();
    const geometry = nestConsumedSteps(find(tree, "g_geo"), tree);
    const parts = geometry.children?.find((c) => c.path === "/geometry/parts");
    expect(parts?.children?.map((c) => c.id)).toEqual(["p_holed"]);
    expect(parts?.children_count).toBe(1);
    const ops = geometry.children?.find((c) => c.path === "/geometry/operations");
    expect(ops?.children?.find((o) => o.id === "o_cut")?.children?.map((c) => c.id)).toEqual(["p_plate", "p_moved"]);
    expect(ops?.children?.find((o) => o.id === "o_move")?.children_count).toBe(1);
  });

  it("names the target and the tools, and swaps them", () => {
    expect(targetAndTools("implicit_boolean", ["a", "b", "c"])).toEqual({ target: "a", tools: ["b", "c"] });
    expect(targetAndTools("implicit_transform", ["a"])).toEqual({ target: "a", tools: [] });
    expect(targetAndTools("implicit_primitive", ["a"])).toBeNull();
    expect(swapTarget(["a", "b", "c"])).toEqual(["b", "a", "c"]);
    expect(swapTarget(["a"])).toEqual(["a"]);
    expect(roleWords("implicit_boolean", "difference")).toEqual(["Cut from", "Cut with"]);
    expect(roleWords("implicit_transform", undefined)).toEqual(["Body", ""]);
    expect(sayList(["a", "b", "c"])).toBe("a, b and c");
  });

  it("runs the card on an Enter in a field, not on a button or a switch", () => {
    const at = (tagName: string, inCard = true, role: string | null = null) => ({
      tagName,
      getAttribute: (name: string) => (name === "role" ? role : null),
      closest: (selector: string) => (inCard && selector === ".operation-card-body" ? {} : null),
    });
    expect(enterRunsCard(at("INPUT"))).toBe(true);
    expect(enterRunsCard(at("SELECT"))).toBe(true);
    expect(enterRunsCard(at("BUTTON"))).toBe(false);
    expect(enterRunsCard(at("BUTTON", true, "switch"))).toBe(false);
    expect(enterRunsCard(at("INPUT", false))).toBe(false);
    expect(enterRunsCard(null)).toBe(false);
  });
});
