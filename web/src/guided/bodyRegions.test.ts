/** A body is what its region is (ADR-0042): two fluids and the solid between them, read from the tree. */
import { describe, expect, it } from "vitest";
import type { NodeSummary } from "../api/types";
import { bodyRegions, fluidBody, otherBodyLine, readBodies, simulatedBodies } from "./guidedTree";

function node(type: string, name: string, path: string, params: NodeSummary["params"] = {}, children: NodeSummary[] = []): NodeSummary {
  return { id: `${type}:${name}`, type, name, status: "ok" as NodeSummary["status"], params, path, children_count: children.length, children };
}

const whole = (...parts: string[]) => JSON.stringify(parts.map((part) => ({ part: `part:${part}`, faces: "all" })));

/** Three bodies: `hot` (the mesh settings' fluid) and `cold` in one water region, `wall` in an aluminium one. */
function exchanger(extra: NodeSummary[] = []): NodeSummary {
  return node("project", "hx", "/", {}, [
    node("group", "geometry", "/geometry", {}, [
      node("part", "hot", "/geometry/hot"),
      node("part", "cold", "/geometry/cold"),
      node("part", "wall", "/geometry/wall"),
      ...extra,
      node("group", "tags", "/geometry/tags", {}, [
        node("tag", "water", "/geometry/tags/water", { members: whole("hot", "cold") }),
        node("tag", "aluminium", "/geometry/tags/aluminium", { members: whole("wall") }),
      ]),
    ]),
    node("group", "materials", "/materials", {}, [
      node("material", "water", "/materials/water", { fluid: "water_liquid" }),
      node("material", "aluminium", "/materials/aluminium", { fluid: "aluminium" }),
    ]),
    node("group", "mesh", "/mesh", {}, [node("mesh_settings", "settings", "/mesh/settings", { domain_part: "part:hot" })]),
    node("group", "regions", "/regions", {}, [
      node("region", "water", "/regions/water", { kind: "fluid", tag: "tag:water", material: "material:water" }),
      node("region", "aluminium", "/regions/aluminium", { kind: "solid", tag: "tag:aluminium", material: "material:aluminium" }),
    ]),
  ]);
}

describe("a body is what its region is", () => {
  it("reads two fluids and a solid, and keeps the mesh settings' body as the first fluid", () => {
    const bodies = readBodies(exchanger());
    expect(bodies.map((b) => [b.name, b.role])).toEqual([
      ["hot", "fluid"],
      ["cold", "fluid"],
      ["wall", "solid"],
    ]);
    expect(fluidBody(bodies)?.name).toBe("hot");
    expect(simulatedBodies(bodies).map((b) => b.name)).toEqual(["hot", "cold", "wall"]);
  });

  it("names each body's material in plain words", () => {
    const regions = bodyRegions(exchanger());
    expect(regions.get("part:cold")).toMatchObject({ name: "water", kind: "fluid", materialName: "Water", libraryId: "water_liquid" });
    expect(regions.get("part:wall")).toMatchObject({ kind: "solid", materialName: "Aluminium" });
  });

  it("leaves a body in no region as it was read before: a solid that is not in the simulation", () => {
    const bodies = readBodies(exchanger([node("part", "bracket", "/geometry/bracket")]));
    const bracket = bodies.find((b) => b.name === "bracket");
    expect(bracket?.role).toBe("solid");
    expect(bracket?.region).toBeUndefined();
    expect(simulatedBodies(bodies).map((b) => b.name)).not.toContain("bracket");
    expect(otherBodyLine(bracket!)).toBe("Not in the simulation");
  });

  it("keeps a left-out body left out whatever region holds it", () => {
    const tree = exchanger();
    const cold = tree.children![0].children!.find((n) => n.name === "cold")!;
    cold.params = { simulate: false };
    expect(readBodies(tree).find((b) => b.name === "cold")?.role).toBe("left_out");
  });

  it("says what another body is under its name", () => {
    const bodies = readBodies(exchanger());
    expect(otherBodyLine(bodies[1])).toBe("Fluid · Water");
    expect(otherBodyLine(bodies[2])).toBe("Solid · Aluminium");
  });
});
