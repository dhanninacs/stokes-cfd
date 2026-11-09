/**
 * The condition catalogue (`boundary.kinds`) as the tests of set-up use it: hand-built kinds that mirror
 * `stokes/physics/conditions/catalogue.py`, and the names of a boundary node's schema. Test support only.
 */
import type { ConditionKind, ConditionParam } from "../api/types";

type Row = [name: string, required?: boolean, when?: Record<string, string[]>, schema?: Record<string, unknown>];

export function kind(id: string, rows: Row[]): ConditionKind {
  const params = rows.map(
    ([name, required = false, when = {}, schema = { "x-kind": "quantity", default: 0 }]): ConditionParam => ({
      name,
      rule: "",
      required,
      when,
      schema: schema as ConditionParam["schema"],
    }),
  );
  // The family as the real catalogue gives it: what an inlet-only rule (its temperature) reads.
  const family = id.endsWith("_inlet") ? "inlet" : id.endsWith("_outlet") ? "outlet" : id;
  return { id, label: id, family, fields: [], needs: {}, color: "#fff", glyph: "", arrives: null, available: true, note: "", params };
}

const TURBULENCE: Row[] = [
  ["turbulence", false, {}, { enum: ["intensity_ratio", "intensity_length"], default: "intensity_ratio" }],
  ["intensity"],
  ["viscosity_ratio", false, { turbulence: ["intensity_ratio"] }],
  ["length_scale", false, { turbulence: ["intensity_length"] }],
];

export const VELOCITY = kind("velocity_inlet", [
  ["direction", false, {}, { enum: ["normal", "components", "vector"], default: "normal" }],
  ["velocity", true, { direction: ["normal", "vector"] }],
  ["velocity_x", true, { direction: ["components"] }],
  ["velocity_y", true, { direction: ["components"] }],
  ["velocity_z", true, { direction: ["components"] }],
  ["direction_vector", false, { direction: ["vector"] }],
  ["temperature"],
  ...TURBULENCE,
]);
export const MASS = kind("mass_flow_inlet", [["mass_flow", true], ["temperature"], ...TURBULENCE]);
export const PRESSURE_IN = kind("pressure_inlet", [["total_pressure"], ["temperature"], ...TURBULENCE]);
export const OUTLET = kind("pressure_outlet", [["pressure"], ["backflow_temperature"], ...TURBULENCE]);
export const WALL = kind("wall", [
  ["shear", false, {}, { enum: ["no_slip", "slip"], default: "no_slip" }],
  ["thermal", false, {}, { enum: ["adiabatic", "temperature", "heat_flux", "convection"], default: "adiabatic" }],
  ["temperature", false, { thermal: ["temperature"] }],
  ["heat_flux", false, { thermal: ["heat_flux"] }],
  ["heat_transfer_coefficient", false, { thermal: ["convection"] }],
  ["ambient_temperature", false, { thermal: ["convection"] }],
]);
export const SYMMETRY = kind("symmetry", []);
export const CYCLIC = kind("cyclic", [["partner", true, {}, { "x-kind": "ref" }]]);
export const KINDS: ConditionKind[] = [VELOCITY, MASS, PRESSURE_IN, OUTLET, WALL, SYMMETRY, CYCLIC];

/** The schema names of a boundary node, in the server's order (`stokes/geometry/model.py`). */
export const BOUNDARY_NAMES: string[] = [
  "kind",
  "tag",
  "direction",
  "velocity",
  "velocity_x",
  "velocity_y",
  "velocity_z",
  "direction_vector",
  "mass_flow",
  "total_pressure",
  "pressure",
  "temperature",
  "backflow_temperature",
  "turbulence",
  "intensity",
  "viscosity_ratio",
  "length_scale",
  "shear",
  "thermal",
  "heat_flux",
  "heat_transfer_coefficient",
  "ambient_temperature",
  "partner",
  "layers",
  "mesh_size",
];
