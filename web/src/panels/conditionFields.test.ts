/**
 * Which parameters a boundary's form leads with, which sit under "More...", and which the Details page
 * shows: only the kind's own, never another kind's. Fixtures mirror stokes/physics/conditions/catalogue.py.
 */
import { describe, expect, it } from "vitest";
import type { ConditionKind, ConditionParam } from "../api/types";
import { boundaryFieldSplit, defaultValues, formFields, moreFields } from "./conditions";

type Row = [name: string, required?: boolean, when?: Record<string, string[]>, schema?: Record<string, unknown>];

function kind(id: string, rows: Row[]): ConditionKind {
  const params = rows.map(
    ([name, required = false, when = {}, schema = { "x-kind": "quantity", default: 0 }]): ConditionParam => ({
      name,
      rule: "",
      required,
      when,
      schema: schema as ConditionParam["schema"],
    }),
  );
  return { id, label: id, family: "x", fields: [], needs: {}, color: "#fff", glyph: "", arrives: null, available: true, note: "", params };
}

const TURBULENCE: Row[] = [
  ["turbulence", false, {}, { enum: ["intensity_ratio", "intensity_length"], default: "intensity_ratio" }],
  ["intensity"],
  ["viscosity_ratio", false, { turbulence: ["intensity_ratio"] }],
  ["length_scale", false, { turbulence: ["intensity_length"] }],
];

const VELOCITY = kind("velocity_inlet", [
  ["direction", false, {}, { enum: ["normal", "components", "vector"], default: "normal" }],
  ["velocity", true, { direction: ["normal", "vector"] }],
  ["velocity_x", true, { direction: ["components"] }],
  ["velocity_y", true, { direction: ["components"] }],
  ["velocity_z", true, { direction: ["components"] }],
  ["direction_vector", false, { direction: ["vector"] }],
  ["temperature"],
  ...TURBULENCE,
]);
const MASS = kind("mass_flow_inlet", [["mass_flow", true], ["temperature"], ...TURBULENCE]);
const PRESSURE_IN = kind("pressure_inlet", [["total_pressure"], ["temperature"], ...TURBULENCE]);
const OUTLET = kind("pressure_outlet", [["pressure"], ["backflow_temperature"], ...TURBULENCE]);
const WALL = kind("wall", [
  ["shear", false, {}, { enum: ["no_slip", "slip"], default: "no_slip" }],
  ["thermal", false, {}, { enum: ["adiabatic", "temperature", "heat_flux", "convection"], default: "adiabatic" }],
  ["temperature", false, { thermal: ["temperature"] }],
  ["heat_flux", false, { thermal: ["heat_flux"] }],
]);
const SYMMETRY = kind("symmetry", []);
const CYCLIC = kind("cyclic", [["partner", true, {}, { "x-kind": "ref" }]]);
const KINDS = [VELOCITY, MASS, PRESSURE_IN, OUTLET, WALL, SYMMETRY, CYCLIC];

const names = (params: ConditionParam[]): string[] => params.map((p) => p.name);
const lead = (k: ConditionKind, values: Record<string, unknown> = defaultValues(k)): string[] => names(formFields(k, values));

describe("formFields: only the kind's own leading parameters", () => {
  it("a velocity inlet asks for its direction and velocity, nothing of pressure or mass flow", () => {
    expect(lead(VELOCITY)).toEqual(["direction", "velocity"]);
  });

  it("the components appear when the direction becomes components, the vector when it becomes vector", () => {
    expect(lead(VELOCITY, { direction: "components" })).toEqual(["direction", "velocity_x", "velocity_y", "velocity_z"]);
    expect(lead(VELOCITY, { direction: "vector" })).toEqual(["direction", "velocity", "direction_vector"]);
  });

  it("a mass-flow inlet asks for its mass flow alone", () => {
    expect(lead(MASS)).toEqual(["mass_flow"]);
  });

  it("a pressure inlet asks for its total pressure alone", () => {
    expect(lead(PRESSURE_IN)).toEqual(["total_pressure"]);
  });

  it("a pressure outlet asks for its pressure and none of the velocity, mass flow or direction", () => {
    expect(lead(OUTLET)).toEqual(["pressure"]);
  });

  it("a wall, a symmetry plane and a cyclic pair lead with no value fields, except the partner the pair needs", () => {
    expect(lead(WALL)).toEqual([]);
    expect(lead(SYMMETRY)).toEqual([]);
    expect(lead(CYCLIC)).toEqual(["partner"]);
  });

  it("an unknown kind falls back to its required parameters and the switches over them", () => {
    const other = kind("future", [["mode", false, {}, { enum: ["a", "b"], default: "a" }], ["gain", true, { mode: ["a"] }], ["spare"]]);
    expect(lead(other)).toEqual(["mode", "gain"]);
  });
});

describe("moreFields: the rest of the kind, behind More...", () => {
  it("lists the kind's other parameters in use, and only those", () => {
    expect(names(moreFields(OUTLET, defaultValues(OUTLET)))).toEqual(["backflow_temperature", "turbulence", "intensity", "viscosity_ratio"]);
    expect(names(moreFields(MASS, defaultValues(MASS)))).toEqual(["temperature", "turbulence", "intensity", "viscosity_ratio"]);
  });

  it("follows the switches: the length scale replaces the viscosity ratio", () => {
    const more = names(moreFields(OUTLET, { ...defaultValues(OUTLET), turbulence: "intensity_length" }));
    expect(more).toContain("length_scale");
    expect(more).not.toContain("viscosity_ratio");
  });

  it("gives a wall its options, following the thermal choice", () => {
    expect(names(moreFields(WALL, defaultValues(WALL)))).toEqual(["shear", "thermal"]);
    expect(names(moreFields(WALL, { thermal: "heat_flux" }))).toEqual(["shear", "thermal", "heat_flux"]);
  });

  it("is empty for a kind with nothing else", () => {
    expect(moreFields(SYMMETRY, {})).toEqual([]);
    expect(moreFields(CYCLIC, {})).toEqual([]);
  });
});

describe("boundaryFieldSplit: the Details page of a boundary", () => {
  const all = ["kind", "direction", "velocity", "velocity_x", "mass_flow", "total_pressure", "pressure", "backflow_temperature", "temperature", "intensity", "region_note"];

  it("a pressure outlet shows its pressure; its other parameters fold; another kind's vanish", () => {
    const { shown, more } = boundaryFieldSplit(KINDS, "pressure_outlet", { kind: "pressure_outlet" }, all);
    expect([...shown].sort()).toEqual(["kind", "pressure", "region_note"]);
    expect([...more].sort()).toEqual(["backflow_temperature", "intensity"]);
    for (const gone of ["direction", "velocity", "velocity_x", "mass_flow", "total_pressure", "temperature"]) {
      expect(shown.has(gone) || more.has(gone)).toBe(false);
    }
  });

  it("a velocity inlet shows direction and velocity, with the components hidden until chosen", () => {
    const normal = boundaryFieldSplit(KINDS, "velocity_inlet", { direction: "normal" }, all);
    expect([...normal.shown].sort()).toEqual(["direction", "kind", "region_note", "velocity"]);
    expect([...normal.more].sort()).toEqual(["intensity", "temperature"]);
    const components = boundaryFieldSplit(KINDS, "velocity_inlet", { direction: "components" }, all);
    expect(components.shown.has("velocity_x")).toBe(true);
    expect(components.shown.has("velocity")).toBe(false);
  });

  it("shows everything when the catalogue is not loaded or the kind is unknown", () => {
    expect([...boundaryFieldSplit([], "pressure_outlet", {}, all).shown]).toEqual(all);
    expect(boundaryFieldSplit(KINDS, null, {}, all).more.size).toBe(0);
    expect(boundaryFieldSplit(KINDS, "nonsense", {}, all).shown.size).toBe(all.length);
  });
});

describe("boundaryFieldSplit: layers belong to walls, and the physics filters the families", () => {
  const names = ["kind", "tag", "velocity", "pressure", "temperature", "backflow_temperature", "turbulence", "intensity", "viscosity_ratio", "shear", "thermal", "layers", "mesh_size"];
  const everywhere = (s: { shown: Set<string>; more: Set<string> }, name: string): boolean => s.shown.has(name) || s.more.has(name);

  it("shows no layers field on an inlet, an outlet or a symmetry plane, and shows it on a wall", () => {
    for (const id of ["velocity_inlet", "mass_flow_inlet", "pressure_inlet", "pressure_outlet", "symmetry"]) {
      expect(everywhere(boundaryFieldSplit(KINDS, id, { kind: id }, names), "layers")).toBe(false);
    }
    expect(boundaryFieldSplit(KINDS, "wall", { kind: "wall" }, names).shown.has("layers")).toBe(true);
  });

  it("keeps a layers value a non-wall already holds in view, so it can be cleared", () => {
    expect(boundaryFieldSplit(KINDS, "velocity_inlet", { kind: "velocity_inlet", layers: 2 }, names).shown.has("layers")).toBe(true);
  });

  it("drops the temperature family with the energy equation off and keeps it with it on", () => {
    const off = boundaryFieldSplit(KINDS, "velocity_inlet", {}, names, { energy: false, turbulence: "kOmegaSST" });
    expect(everywhere(off, "temperature")).toBe(false);
    const on = boundaryFieldSplit(KINDS, "velocity_inlet", {}, names, { energy: true, turbulence: "kOmegaSST" });
    expect(on.more.has("temperature")).toBe(true);
    const wall = boundaryFieldSplit(KINDS, "wall", {}, names, { energy: false, turbulence: "laminar" });
    expect(everywhere(wall, "thermal")).toBe(false);
  });

  it("drops the turbulence family on a laminar run and keeps it when a model runs", () => {
    const laminar = boundaryFieldSplit(KINDS, "velocity_inlet", {}, names, { energy: true, turbulence: "laminar" });
    for (const name of ["turbulence", "intensity", "viscosity_ratio"]) expect(everywhere(laminar, name)).toBe(false);
    const model = boundaryFieldSplit(KINDS, "velocity_inlet", {}, names, { energy: true, turbulence: "kEpsilon" });
    for (const name of ["turbulence", "intensity", "viscosity_ratio"]) expect(model.more.has(name)).toBe(true);
  });

  it("without a physics context it filters nothing but the walls-only layers", () => {
    const split = boundaryFieldSplit(KINDS, "velocity_inlet", {}, names);
    expect(split.more.has("temperature")).toBe(true);
    expect(split.more.has("intensity")).toBe(true);
  });
});
