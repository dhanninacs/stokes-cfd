/**
 * What set-up shows first and what waits under Advanced: the field lists per boundary kind and physics
 * context, the other node types, the solver context reading, and the dot on the disclosure.
 */
import { describe, expect, it } from "vitest";
import type { NodeSummary, ParamSchema } from "../api/types";
import { BASIC, DEFAULT_CONTEXT, advancedNames, changedFromDefault, hasBasicSet, namesAt, readSetupContext, setupContextOf, visibleFields, type SetupContext } from "./basicSets";
import { BOUNDARY_NAMES, KINDS } from "./kindFixtures";

const STEADY: SetupContext = DEFAULT_CONTEXT;
const HEAT: SetupContext = { ...DEFAULT_CONTEXT, energy: true };
const TURB: SetupContext = { ...DEFAULT_CONTEXT, turbulence: "kOmegaSST" };

function at(params: Record<string, unknown>, ctx: SetupContext = STEADY, names: readonly string[] = BOUNDARY_NAMES) {
  const levels = visibleFields("boundary", names, params, ctx, KINDS);
  return (level: "basic" | "advanced" | "hidden") => namesAt(levels, level, names);
}

function node(path: string, type: string, params: Record<string, unknown>, children: NodeSummary[] = []): NodeSummary {
  return { id: path, path, name: path.split("/").pop() ?? "", type, status: "ok", params, children } as unknown as NodeSummary;
}

describe("a boundary page: Basic is the kind and its lead value", () => {
  it("an inlet shows Kind and speed with Advanced closed", () => {
    expect(at({ kind: "velocity_inlet" })("basic")).toEqual(["kind", "velocity"]);
  });

  it("an outlet shows Kind and pressure", () => {
    expect(at({ kind: "pressure_outlet" })("basic")).toEqual(["kind", "pressure"]);
  });

  it("a mass-flow inlet shows its mass flow, a pressure inlet its total pressure", () => {
    expect(at({ kind: "mass_flow_inlet" })("basic")).toEqual(["kind", "mass_flow"]);
    expect(at({ kind: "pressure_inlet" })("basic")).toEqual(["kind", "total_pressure"]);
  });

  it("a wall and a symmetry plane show Kind only", () => {
    expect(at({ kind: "wall" })("basic")).toEqual(["kind"]);
    expect(at({ kind: "symmetry" })("basic")).toEqual(["kind"]);
  });

  it("the direction is Advanced at its default and joins Basic, with the components, once it is changed", () => {
    expect(at({ kind: "velocity_inlet", direction: "normal" })("advanced")).toContain("direction");
    const components = at({ kind: "velocity_inlet", direction: "components" })("basic");
    expect(components).toEqual(["kind", "direction", "velocity_x", "velocity_y", "velocity_z"]);
    expect(at({ kind: "velocity_inlet", direction: "vector" })("basic")).toEqual(["kind", "direction", "velocity", "direction_vector"]);
  });

  it("an unassigned boundary shows Kind alone", () => {
    expect(at({})("basic")).toEqual(["kind"]);
  });

  it("with no catalogue every field but Kind waits under Advanced", () => {
    const levels = visibleFields("boundary", BOUNDARY_NAMES, { kind: "velocity_inlet" }, STEADY, []);
    expect(namesAt(levels, "basic", BOUNDARY_NAMES)).toEqual(["kind"]);
  });
});

describe("a boundary page: layers belong to walls", () => {
  it("shows the layer override on a wall, under Advanced", () => {
    expect(at({ kind: "wall" })("advanced")).toContain("layers");
  });

  it("hides it on an inlet, an outlet and a symmetry plane", () => {
    for (const id of ["velocity_inlet", "pressure_outlet", "symmetry", "mass_flow_inlet"]) {
      const levels = at({ kind: id });
      expect(levels("hidden")).toContain("layers");
      expect(levels("basic")).not.toContain("layers");
      expect(levels("advanced")).not.toContain("layers");
    }
  });

  it("shows a value a non-wall already holds, so it can be seen and cleared", () => {
    expect(at({ kind: "velocity_inlet", layers: 3 })("advanced")).toContain("layers");
  });
});

describe("a boundary page: the physics decides which families show", () => {
  const TEMPERATURE = ["temperature", "backflow_temperature", "ambient_temperature", "heat_flux", "heat_transfer_coefficient", "thermal"];
  const TURBULENCE = ["turbulence", "intensity", "viscosity_ratio", "length_scale"];

  it("hides temperature with the energy equation off", () => {
    for (const id of ["velocity_inlet", "pressure_outlet", "wall"]) {
      const levels = at({ kind: id });
      for (const name of TEMPERATURE) {
        expect(levels("basic")).not.toContain(name);
        expect(levels("advanced")).not.toContain(name);
      }
    }
  });

  it("puts an inlet's temperature beside its speed with energy on; the outlet's backflow waits under Advanced", () => {
    expect(at({ kind: "velocity_inlet" }, HEAT)("basic")).toEqual(["kind", "velocity", "temperature"]);
    expect(at({ kind: "mass_flow_inlet" }, HEAT)("basic")).toContain("temperature");
    expect(at({ kind: "pressure_outlet" }, HEAT)("advanced")).toContain("backflow_temperature");
    expect(at({ kind: "pressure_outlet" }, HEAT)("basic")).toEqual(["kind", "pressure"]);
  });

  it("shows a wall's thermal choice with energy on, and the value it picks", () => {
    expect(at({ kind: "wall" }, HEAT)("advanced")).toContain("thermal");
    const flux = at({ kind: "wall", thermal: "heat_flux" }, HEAT)("advanced");
    expect(flux).toContain("heat_flux");
    expect(flux).not.toContain("heat_transfer_coefficient");
    expect(at({ kind: "wall", thermal: "convection" }, HEAT)("advanced")).toEqual(expect.arrayContaining(["heat_transfer_coefficient", "ambient_temperature"]));
  });

  it("hides turbulence for a laminar run and shows it under Advanced when a model runs", () => {
    for (const name of TURBULENCE) expect(at({ kind: "velocity_inlet" })("hidden")).toContain(name);
    expect(at({ kind: "velocity_inlet" }, TURB)("advanced")).toEqual(expect.arrayContaining(["turbulence", "intensity", "viscosity_ratio"]));
    expect(at({ kind: "pressure_outlet" }, TURB)("advanced")).toEqual(expect.arrayContaining(["turbulence", "intensity"]));
  });

  it("the turbulence choice swaps the viscosity ratio for the length scale", () => {
    const length = at({ kind: "velocity_inlet", turbulence: "intensity_length" }, TURB)("advanced");
    expect(length).toContain("length_scale");
    expect(length).not.toContain("viscosity_ratio");
  });

  it("a wall never shows turbulence", () => {
    const wall = at({ kind: "wall" }, TURB);
    for (const name of TURBULENCE) expect([...wall("basic"), ...wall("advanced")]).not.toContain(name);
  });

  it("filters hide and never clear: a value kept behind the filter is still in the parameters", () => {
    const params = { kind: "velocity_inlet", temperature: { value: 350, unit: "K" } };
    expect(at(params)("hidden")).toContain("temperature");
    expect(params.temperature.value).toBe(350);
  });

  it("the bookkeeping names stay under Advanced", () => {
    expect(at({ kind: "velocity_inlet" })("advanced")).toEqual(expect.arrayContaining(["tag", "mesh_size"]));
  });
});

describe("the other pages", () => {
  it("a region shows Material; its kind and tag wait under Advanced", () => {
    const names = ["kind", "tag", "material"];
    const levels = visibleFields("region", names, {}, STEADY);
    expect(namesAt(levels, "basic", names)).toEqual(["material"]);
    expect(namesAt(levels, "advanced", names)).toEqual(["kind", "tag"]);
  });

  it("a material and a body draw their Basic part themselves: every schema field is Advanced", () => {
    const names = ["fluid", "T_ref", "rho"];
    expect(Object.values(visibleFields("material", names, {}, STEADY))).toEqual(["advanced", "advanced", "advanced"]);
    expect(Object.values(visibleFields("part", names, {}, STEADY))).toEqual(["advanced", "advanced", "advanced"]);
    expect(BASIC.material).toEqual(["readout"]);
    expect(BASIC.part).toEqual(["role"]);
  });

  it("a type with no Basic set shows every field as it always did", () => {
    expect(hasBasicSet("tag")).toBe(false);
    expect(Object.values(visibleFields("tag", ["a", "b"], {}, STEADY))).toEqual(["basic", "basic"]);
  });
});

describe("readSetupContext: the solver settings", () => {
  it("is steady, laminar and without energy while there are no settings", () => {
    expect(readSetupContext(null)).toEqual(DEFAULT_CONTEXT);
    expect(readSetupContext(node("/", "project", {}))).toEqual(DEFAULT_CONTEXT);
  });

  it("reads energy, turbulence and transient from /solver/settings", () => {
    const settings = node("/solver/settings", "solver_settings", { energy: true, turbulence: "kOmegaSST", transient: true });
    const tree = node("/", "project", {}, [node("/solver", "group", {}, [settings])]);
    expect(readSetupContext(tree)).toEqual({ energy: true, turbulence: "kOmegaSST", transient: true });
  });
});

describe("changedFromDefault: the dot on Advanced", () => {
  const properties: Record<string, ParamSchema> = {
    mesh_size: { "x-kind": "quantity" },
    shear: { enum: ["no_slip", "slip"], default: "no_slip" },
    intensity: { default: 0.05 },
    T: { "x-kind": "quantity", default: 300 },
  };
  const names = Object.keys(properties);

  it("is empty while everything holds its default or nothing", () => {
    expect(changedFromDefault(properties, { shear: "no_slip", intensity: 0.05, T: { value: 300, unit: "K" } }, names)).toEqual([]);
    expect(changedFromDefault(properties, {}, names)).toEqual([]);
  });

  it("names what differs, and a field with no default as soon as it holds a value", () => {
    const params = { shear: "slip", intensity: 0.1, T: { value: 310, unit: "K" }, mesh_size: { value: 0.001, unit: "m" } };
    expect(changedFromDefault(properties, params, names)).toEqual(["mesh_size", "shear", "intensity", "T"]);
  });
});

const SOLVER_NAMES = [
  "engine", "host", "turbulence", "energy", "density_model", "molar_mass", "transient", "delta_t", "end_time",
  "initial_velocity", "initial_intensity", "initial_length_scale", "initial_viscosity_ratio", "gravity", "operating_pressure",
  "iterations", "write_interval", "residual_target", "cores", "slice_origin", "slice_normal",
];
const MESH_NAMES = [
  "mesher", "host", "base_size", "surface_size", "domain", "domain_part", "box", "margin", "max_cells", "cores", "max_non_ortho",
  "max_skewness", "accept_unassigned", "layers", "layer_mode", "expansion_ratio", "final_ratio", "first_layer_height",
  "min_thickness_ratio", "layer_feature_angle", "feature_angle",
];

function solver(params: Record<string, unknown>, ctx: SetupContext = setupContextOf(params)) {
  const levels = visibleFields("solver_settings", SOLVER_NAMES, params, ctx);
  return (level: "basic" | "advanced" | "hidden") => namesAt(levels, level, SOLVER_NAMES);
}

function mesh(params: Record<string, unknown>) {
  const levels = visibleFields("mesh_settings", MESH_NAMES, params, DEFAULT_CONTEXT);
  return { at: (level: "basic" | "advanced" | "hidden") => namesAt(levels, level, MESH_NAMES), levels };
}

describe("the solver page: Basic is the flow model and the iterations", () => {
  it("has a Basic set, and a steady laminar run shows exactly those two fields", () => {
    expect(hasBasicSet("solver_settings")).toBe(true);
    expect(BASIC.solver_settings).toEqual(["turbulence", "iterations"]);
    expect(solver({})("basic")).toEqual(["turbulence", "iterations"]);
  });

  it("hides time step and end time until the run is transient", () => {
    expect(solver({})("hidden")).toEqual(expect.arrayContaining(["delta_t", "end_time"]));
    expect(solver({ transient: true })("advanced")).toEqual(expect.arrayContaining(["transient", "delta_t", "end_time"]));
  });

  it("hides the initial-turbulence fields for a laminar run and offers them under Advanced otherwise", () => {
    const names = ["initial_velocity", "initial_intensity", "initial_length_scale", "initial_viscosity_ratio"];
    expect(solver({ turbulence: "laminar" })("hidden")).toEqual(expect.arrayContaining(names));
    expect(solver({ turbulence: "kOmegaSST" })("advanced")).toEqual(expect.arrayContaining(names));
    expect(solver({ turbulence: "kOmegaSST" })("basic")).toEqual(["turbulence", "iterations"]);
  });

  it("hides the density model and molar mass until the energy equation is on", () => {
    expect(solver({ energy: false })("hidden")).toEqual(expect.arrayContaining(["density_model", "molar_mass"]));
    expect(solver({ energy: true })("advanced")).toEqual(expect.arrayContaining(["energy", "density_model", "molar_mass"]));
  });

  it("every other field is Advanced, and a hidden field is only hidden (nothing is dropped from the list)", () => {
    const levels = visibleFields("solver_settings", SOLVER_NAMES, {}, DEFAULT_CONTEXT);
    expect(Object.keys(levels).sort()).toEqual([...SOLVER_NAMES].sort());
    expect(advancedNames("solver_settings", levels, SOLVER_NAMES)).toEqual(
      expect.arrayContaining(["engine", "energy", "transient", "residual_target", "write_interval", "host", "slice_origin"]),
    );
    expect(advancedNames("solver_settings", levels, SOLVER_NAMES)).not.toContain("iterations");
    // Gravity and the operating pressure mean something only with the energy equation on.
    expect(levels.gravity).toBe("hidden");
    const withEnergy = visibleFields("solver_settings", SOLVER_NAMES, { energy: true }, { ...DEFAULT_CONTEXT, energy: true });
    expect(advancedNames("solver_settings", withEnergy, SOLVER_NAMES)).toEqual(expect.arrayContaining(["gravity", "operating_pressure"]));
  });

  it("reads the context from the node's own params, with the defaults for what they leave out", () => {
    expect(setupContextOf({})).toEqual(DEFAULT_CONTEXT);
    expect(setupContextOf({ energy: true, turbulence: "kEpsilon", transient: true })).toEqual({ energy: true, turbulence: "kEpsilon", transient: true });
  });
});

describe("the mesh page: Basic is the fineness and the layers switch", () => {
  it("has a Basic set: the base size (as Coarse / Medium / Fine) and the layers (as a switch)", () => {
    expect(hasBasicSet("mesh_settings")).toBe(true);
    expect(BASIC.mesh_settings).toEqual(["base_size", "layers"]);
  });

  it("with snappyHexMesh both are Basic, and the exact fields stay in Advanced too", () => {
    const m = mesh({ mesher: "snappy" });
    expect(m.at("basic")).toEqual(["base_size", "layers"]);
    const advanced = advancedNames("mesh_settings", m.levels, MESH_NAMES);
    expect(advanced).toEqual(expect.arrayContaining(["base_size", "layers", "mesher", "surface_size", "max_cells", "feature_angle", "max_non_ortho", "cores"]));
  });

  it("with blockMesh the layers switch is hidden (the mesher has no layers)", () => {
    const m = mesh({ mesher: "blockmesh" });
    expect(m.at("basic")).toEqual(["base_size"]);
    expect(m.at("hidden")).toEqual(expect.arrayContaining(["layers", "surface_size", "feature_angle"]));
    expect(mesh({}).at("hidden")).toContain("layers");
  });

  it("the layer fields follow the layer mode, as the old page did", () => {
    expect(mesh({ mesher: "snappy", layer_mode: "final_ratio" }).at("hidden")).toContain("first_layer_height");
    expect(mesh({ mesher: "snappy", layer_mode: "first_height" }).at("hidden")).toContain("final_ratio");
  });
});
