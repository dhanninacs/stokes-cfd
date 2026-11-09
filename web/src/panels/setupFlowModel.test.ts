/** The "Set up flow" offer for a freshly imported body (T-219 C). */
import { describe, expect, it } from "vitest";
import type { ConditionKind, NodeSummary, SetupCheck } from "../api/types";
import { canCreate, defaultTemplate, defaultsParams, describeDefaults, findingFix, firstClause, flowOffer, openingsOf, partsOf } from "./setupFlowModel";

function node(id: string, path: string, type = "group", children: NodeSummary[] = [], name = path.split("/").pop() ?? ""): NodeSummary {
  return { id, type, name, status: "ok", params: {}, path, children_count: children.length, children };
}

function project(parts: string[], tags: string[], boundaries: string[] = []): NodeSummary {
  return node("root", "", "group", [
    node("g", "/geometry", "group", [
      ...parts.map((p) => node(`p_${p}`, `/geometry/${p}`, "part", [], p)),
      node("tags", "/geometry/tags", "group", tags.map((t) => node(`t_${t}`, `/geometry/tags/${t}`, "tag", [], t))),
    ]),
    node(
      "regions",
      "/regions",
      "group",
      boundaries.length === 0 ? [] : [node("r1", "/regions/fluid", "region", [node("bg", "/regions/fluid/boundaries", "group", boundaries.map((k, i) => ({ ...node(`b${i}`, `/regions/fluid/boundaries/b${i}`, "boundary"), params: { kind: k } })))])],
    ),
  ]);
}

function kind(id: string, family: string): ConditionKind {
  return { id, label: id, family, fields: [], needs: {}, color: "#fff", glyph: "", arrives: null, available: true, note: "", params: [] };
}
const KINDS = [kind("velocity_inlet", "inlet"), kind("pressure_outlet", "outlet"), kind("wall", "wall")];

const NOT_DEFINED: SetupCheck = { state: "not_defined", next: "", counts: { regions: 0, materials: 0, boundaries: 0, uncovered_faces: 0, legacy: 0 }, findings: [] };
const INCOMPLETE: SetupCheck = { ...NOT_DEFINED, state: "incomplete", counts: { ...NOT_DEFINED.counts, regions: 1 } };
/** What setup.check says after an inlet and an outlet were assigned and nothing else: a region with no body and no material. */
const REGION_WITHOUT_BODY: SetupCheck = {
  ...NOT_DEFINED,
  counts: { ...NOT_DEFINED.counts, regions: 1, boundaries: 2 },
  findings: [
    { code: "setup.no_region", severity: "error", message: "no region holds a body yet" },
    { code: "setup.no_material", severity: "error", message: "no material" },
  ],
};

describe("the offer", () => {
  it("is made for an imported part with no region", () => {
    const offer = flowOffer(project(["duct"], ["inlet", "outlet", "walls"]), NOT_DEFINED, KINDS);
    expect(offer).toEqual({ parts: [{ id: "p_duct", name: "duct" }], template: "internal", openings: { inlet: 0, outlet: 0 } });
  });
  it("is made before the first check has answered", () => {
    expect(flowOffer(project(["duct"], []), null)?.parts).toHaveLength(1);
  });
  it("is not made without a part, or once a region holds a body and a material", () => {
    expect(flowOffer(project([], []), NOT_DEFINED)).toBeNull();
    expect(flowOffer(project(["duct"], []), INCOMPLETE)).toBeNull();
    expect(flowOffer(null, NOT_DEFINED)).toBeNull();
  });
  it("is still made for a region that exists only because a boundary was assigned to it", () => {
    expect(flowOffer(project(["duct"], []), REGION_WITHOUT_BODY, KINDS)).not.toBeNull();
  });
  it("lists every part, so the person chooses which is the fluid", () => {
    expect(partsOf(project(["a", "b"], [])).map((p) => p.name)).toEqual(["a", "b"]);
  });
});

describe("the template", () => {
  it("is external for an enclosure's far field and internal otherwise", () => {
    expect(defaultTemplate(project(["box"], ["inlet", "outlet", "far_field"]))).toBe("external");
    expect(defaultTemplate(project(["duct"], ["inlet", "outlet"]))).toBe("internal");
  });
});

describe("the order of the openings", () => {
  it("counts the inlets and the outlets by their family", () => {
    expect(openingsOf(project(["duct"], [], ["velocity_inlet", "pressure_outlet", "wall"]), KINDS)).toEqual({ inlet: 1, outlet: 1 });
    expect(openingsOf(project(["duct"], []), KINDS)).toEqual({ inlet: 0, outlet: 0 });
  });

  it("holds an internal flow back until both openings are assigned, and says which is missing", () => {
    const none = flowOffer(project(["duct"], []), NOT_DEFINED, KINDS);
    expect(none && canCreate(none, "internal")).toMatchObject({ enabled: false, reason: expect.stringContaining("the inlet and the outlet") });
    const inletOnly = flowOffer(project(["duct"], [], ["velocity_inlet"]), NOT_DEFINED, KINDS);
    expect(inletOnly && canCreate(inletOnly, "internal").reason).toContain("the outlet first");
    const both = flowOffer(project(["duct"], [], ["velocity_inlet", "pressure_outlet"]), REGION_WITHOUT_BODY, KINDS);
    expect(both && canCreate(both, "internal")).toEqual({ enabled: true, reason: "" });
  });

  it("does not hold an external flow back: its enclosure already named the openings", () => {
    const none = flowOffer(project(["duct"], []), NOT_DEFINED, KINDS);
    expect(none && canCreate(none, "external").enabled).toBe(true);
  });
});

describe("the command", () => {
  it("names the part and the template and nothing picked", () => {
    expect(defaultsParams("p_duct", "internal")).toEqual({ target: "p_duct", template: "internal" });
  });
  it("says what was made, whether the material comes as a name or as a node", () => {
    expect(describeDefaults({ boundaries: [1, 2, 3], material: "water" })).toBe("Fluid region with water, 3 boundaries");
    expect(describeDefaults({ boundaries: [1, 2, 3], material: { id: "m", name: "water" } })).toBe("Fluid region with water, 3 boundaries");
    expect(describeDefaults({ boundaries: [1] })).toBe("Fluid region, 1 boundary");
    expect(describeDefaults({})).toBe("Fluid region, 0 boundaries");
    expect(describeDefaults({ boundaries: [1], material: { name: "water" }, walls_faces: 4 })).toBe("Fluid region with water, 4 faces made walls");
    expect(describeDefaults({ boundaries: [1], walls_faces: 1 })).toBe("Fluid region, 1 face made walls");
  });
});

describe("a finding's Fix", () => {
  it("is the finding's own command, with its parameters", () => {
    expect(findingFix({ fix: { command: "region.set", params: { region: "r1", kind: "solid" } } })).toEqual({ command: "region.set", params: { region: "r1", kind: "solid" } });
    expect(findingFix({ fix: { command: "tag.rematch" } })).toEqual({ command: "tag.rematch", params: {} });
  });

  it("is absent for a fix that is a step on a page, rather than a button that says not wired yet", () => {
    expect(findingFix({})).toBeNull();
    expect(findingFix({ fix: { action: "condition.add" } })).toBeNull();
    expect(findingFix({ fix: { action: "condition.edit", node: "b1" } })).toBeNull();
    expect(findingFix({ fix: { action: "material.assign" } })).toBeNull();
    expect(findingFix({ fix: { command: "" } })).toBeNull();
  });
});

describe("the few words of a sentence", () => {
  it("keeps the first clause", () => {
    expect(firstClause("Assign the inlet first: pick the face, then Assign. Every face you have not assigned becomes a wall.")).toBe("Assign the inlet first");
    expect(firstClause("Assign the inlet and the outlet first. Then more.")).toBe("Assign the inlet and the outlet first");
    expect(firstClause("No colon or full stop")).toBe("No colon or full stop");
  });
});
