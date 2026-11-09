/**
 * The value panel's rules (ADR-0022 §7.1, T-044).
 *
 * These are the parts of the panel that can be wrong while looking right: the family order and
 * grouping of the kinds list, which kinds are choosable, the name a boundary defaults to and how it
 * is made unique, the target line's gesture suffix, and — the one that would corrupt a project —
 * addressing `boundary.set` by `boundary` and `boundary.assign` by `target`.
 */

import { describe, expect, it } from "vitest";

import { conditionCommit, defaultName, enterMayCommit, familyTitle, groupKinds, nameToSend, targetLine } from "./conditionPanelLogic";
import type { ConditionKind } from "../api/types";

function kind(over: Partial<ConditionKind> & { id: string; family: string }): ConditionKind {
  return {
    label: over.id,
    fields: [],
    needs: {},
    color: "#d64f4f",
    glyph: "arrow_in",
    arrives: null,
    available: true,
    note: "",
    params: [],
    ...over,
  };
}

const VELOCITY = kind({ id: "velocity_inlet", family: "inlet", label: "Velocity inlet" });
const MASS = kind({ id: "mass_flow_inlet", family: "inlet", label: "Mass-flow inlet" });
const OUTLET = kind({ id: "pressure_outlet", family: "outlet", label: "Pressure outlet", color: "#3b82c4" });
const WALL = kind({ id: "wall", family: "wall", label: "Wall", color: "#7f8c99" });
const IFACE = kind({ id: "interface", family: "interface", label: "Fluid-solid interface", available: false, arrives: "M5" });

describe("familyTitle", () => {
  it("title-cases a family id", () => {
    expect(familyTitle("inlet")).toBe("Inlet");
    expect(familyTitle("fixed_value")).toBe("Fixed Value");
    expect(familyTitle("solid-fluid")).toBe("Solid Fluid");
  });

  it("leaves an empty id alone rather than crashing", () => {
    expect(familyTitle("")).toBe("");
  });
});

describe("groupKinds", () => {
  it("groups by family in §7.1's order", () => {
    const groups = groupKinds([OUTLET, WALL, VELOCITY, MASS, IFACE]);
    expect(groups.map((g) => g.id)).toEqual(["inlet", "outlet", "wall", "interface"]);
    expect(groups[0].title).toBe("Inlet");
  });

  it("keeps the catalogue order inside a family", () => {
    const groups = groupKinds([VELOCITY, MASS]);
    expect(groups[0].kinds.map((k) => k.id)).toEqual(["velocity_inlet", "mass_flow_inlet"]);
  });

  it("drops an empty family", () => {
    expect(groupKinds([VELOCITY]).map((g) => g.id)).toEqual(["inlet"]);
  });

  it("keeps an unknown family instead of losing it, sorted after the known ones", () => {
    const odd = kind({ id: "porous", family: "porous_zone" });
    const groups = groupKinds([odd, VELOCITY]);
    expect(groups.map((g) => g.id)).toEqual(["inlet", "porous_zone"]);
  });

  it("still shows an unavailable kind, so its `arrives` can be read", () => {
    const groups = groupKinds([IFACE]);
    expect(groups[0].kinds[0].available).toBe(false);
    expect(groups[0].kinds[0].arrives).toBe("M5");
  });

  it("gives every kind a colour for the 12 px dot", () => {
    for (const group of groupKinds([VELOCITY, OUTLET, WALL])) {
      for (const k of group.kinds) expect(k.color).toMatch(/^#[0-9a-f]{6}$/i);
    }
  });

  it("has nothing to show for an empty catalogue", () => {
    expect(groupKinds([])).toEqual([]);
  });
});

describe("defaultName", () => {
  it("defaults from the kind's label", () => {
    expect(defaultName(VELOCITY, [])).toBe("Velocity inlet");
  });

  it("makes the name unique, as the tree does", () => {
    expect(defaultName(VELOCITY, ["Velocity inlet"])).toBe("Velocity inlet_2");
    expect(defaultName(VELOCITY, ["Velocity inlet", "Velocity inlet_2"])).toBe("Velocity inlet_3");
  });

  it("falls back to the id when the kind has no label", () => {
    expect(defaultName(kind({ id: "symmetry", family: "constraint", label: "" }), [])).toBe("symmetry");
  });

  it("ignores unrelated names", () => {
    expect(defaultName(OUTLET, ["Velocity inlet", "walls"])).toBe("Pressure outlet");
  });
});

describe("targetLine", () => {
  it("reads 'what · gesture', as §7.1 specifies", () => {
    expect(targetLine("12 faces of 3 bodies", "replace")).toBe("12 faces of 3 bodies · Replace");
    expect(targetLine("3 faces on 2 parts", "add")).toBe("3 faces on 2 parts · Add");
    expect(targetLine("1 face on 1 part", "toggle")).toBe("1 face on 1 part · Toggle");
  });

  it("has no gesture for a boundary chosen from the tree", () => {
    expect(targetLine("inlet", null)).toBe("inlet");
  });

  it("says 'nothing picked' when there is nothing to apply to", () => {
    expect(targetLine("", null)).toBe("nothing picked");
  });
});

describe("conditionCommit", () => {
  it("addresses boundary.set by `boundary`, not by `target`", () => {
    // The server's `boundary.set` takes `boundary` (a path) while `boundary.assign` takes `target`
    // (a pick). Using the wrong key silently targets nothing, so this is pinned.
    const call = conditionCommit({
      boundary: "/regions/fluid/boundaries/inlet",
      kind: VELOCITY,
      name: "inlet",
      values: { velocity: "2 [m/s]" },
    });
    expect(call.method).toBe("boundary.set");
    expect(call.params.boundary).toBe("/regions/fluid/boundaries/inlet");
    expect(call.params).not.toHaveProperty("target");
    expect(call.params.kind).toBe("velocity_inlet");
    expect(call.params.name).toBe("inlet");
    expect(call.params.values).toEqual({ velocity: "2 [m/s]" });
  });

  it("omits an empty name and empty values rather than sending blanks", () => {
    const call = conditionCommit({ boundary: "/b/x", kind: WALL, name: "", values: {} });
    expect(call.params).not.toHaveProperty("name");
    expect(call.params).not.toHaveProperty("values");
  });

  it("still sends the kind, so the panel can change a boundary's kind", () => {
    expect(conditionCommit({ boundary: "/b/x", kind: OUTLET, name: "", values: {} }).params.kind).toBe(
      "pressure_outlet",
    );
  });
});

describe("nameToSend", () => {
  it("leaves a name at its default to the server, which names the boundary from its kind", () => {
    expect(nameToSend("Velocity inlet", "Velocity inlet")).toBeNull();
    expect(nameToSend("  ", "Velocity inlet")).toBeNull();
  });

  it("keeps a name the person typed, made into a valid node name", () => {
    expect(nameToSend("main inlet", "Velocity inlet")).toBe("main_inlet");
    expect(nameToSend("inlet-2", "Velocity inlet")).toBe("inlet_2");
    expect(nameToSend("inlet", "Velocity inlet")).toBe("inlet");
    expect(nameToSend("2nd", "Velocity inlet")).toBe("b_2nd");
    expect(nameToSend("a/b!", "Velocity inlet")).toBe("ab");
  });

  it("sends nothing when no valid character is left", () => {
    expect(nameToSend("!!!", "Velocity inlet")).toBeNull();
  });
});

describe("enterMayCommit", () => {
  const target = (attrs: Record<string, string>) => ({ getAttribute: (name: string) => attrs[name] ?? null });
  it("waits while the field under the keypress refused its own text", () => {
    expect(enterMayCommit(target({ "data-refused": "true" }))).toBe(false);
  });
  it("commits from a field with no local refusal, including one the server refused (a retry), and from no field", () => {
    expect(enterMayCommit(target({}))).toBe(true);
    expect(enterMayCommit(target({ "aria-invalid": "true" }))).toBe(true);
    expect(enterMayCommit(null)).toBe(true);
  });
});
