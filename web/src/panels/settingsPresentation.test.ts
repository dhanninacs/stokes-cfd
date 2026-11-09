import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import {
  BASIC_MESH,
  BASIC_SOLVER,
  INITIAL_TURBULENCE_SECTION,
  MESH_SETTINGS_SECTIONS,
  MESH_TASK_FIELDS,
  SOLVER_SETTINGS_SECTIONS,
  findingsTouch,
  groupSettingsFields,
  meshSettingIncluded,
  searchSettingsGroups,
  solverSettingIncluded,
  type SolverContext,
} from "./settingsPresentation";

/** The parameter names a node type's schema has, read from the server's own definition (the source of truth). */
function schemaNames(file: string, from: string, to?: string): string[] {
  const text = readFileSync(new URL(`../../../${file}`, import.meta.url), "utf8");
  const start = text.indexOf(from);
  const body = text.slice(start, to === undefined ? undefined : text.indexOf(to, start + from.length));
  return [...body.matchAll(/ParamSpec\(\s*"(\w+)"/g)].map((m) => m[1]);
}
const SOLVER_NAMES = schemaNames("stokes/engines/model.py", '"Solver settings"', "def settings_summary");
const MESH_NAMES = schemaNames("stokes/mesh/model.py", '"mesh_settings",', '"mesh_result"');
/** The solver fields as the schema offers them once the transient and turbulence fields exist (the grouping rules do not read the server). */
const ALL_SOLVER_FIELDS = [
  "engine", "host", "turbulence", "energy", "density_model", "molar_mass", "transient", "delta_t", "end_time", "initial_velocity",
  "initial_intensity", "initial_length_scale", "initial_viscosity_ratio", "gravity", "operating_pressure", "iterations",
  "write_interval", "residual_target", "cores", "slice_origin", "slice_normal",
];
const STEADY: SolverContext = { energy: false, turbulence: "laminar", transient: false };

const fieldsOf = (sections: typeof SOLVER_SETTINGS_SECTIONS) => sections.flatMap((section) => section.fields);

describe("settings presentation taxonomy", () => {
  it("finds collapsed settings by label, section, and multiple search terms", () => {
    const properties = { cores: { type: "integer" as const, title: "Processes" }, write_interval: { type: "integer" as const, title: "Write every" } };
    const groups = groupSettingsFields(properties, SOLVER_SETTINGS_SECTIONS);
    expect(searchSettingsGroups(groups, properties, " execution processes ").map((g) => g.fields)).toEqual([["cores"]]);
    expect(searchSettingsGroups(groups, properties, "write interval").map((g) => g.fields)).toEqual([["write_interval"]]);
    expect(searchSettingsGroups(groups, properties, "not found")).toEqual([]);
    expect(searchSettingsGroups(groups, properties, " ")).toEqual(groups);
  });

  it("keeps rejected fields reachable even when the search does not match", () => {
    const properties = { cores: { type: "integer" as const }, write_interval: { type: "integer" as const } };
    const groups = groupSettingsFields(properties, SOLVER_SETTINGS_SECTIONS);
    expect(searchSettingsGroups(groups, properties, "output", { cores: "Invalid process count" }).flatMap((g) => g.fields)).toEqual(["write_interval", "cores"]);
  });

  it("places every solver setting in its intended group", () => {
    expect(SOLVER_SETTINGS_SECTIONS.map((section) => [section.title, section.fields])).toEqual([
      ["Model", ["engine", "turbulence", "energy", "density_model", "molar_mass", "transient"]],
      ["Transient", ["delta_t", "end_time", "adjustable_time_step", "max_courant", "write_time", "time_average", "average_start"]],
      ["Initial turbulence", ["initial_velocity", "initial_intensity", "initial_length_scale", "initial_viscosity_ratio"]],
      ["Reference conditions", ["gravity", "operating_pressure"]],
      ["Run limits", ["iterations", "residual_target"]],
      ["Output", ["write_interval"]],
      ["Execution", ["host", "cores"]],
      ["Result slice", ["slice_origin", "slice_normal"]],
    ]);
    expect(fieldsOf(SOLVER_SETTINGS_SECTIONS)).toHaveLength(new Set(fieldsOf(SOLVER_SETTINGS_SECTIONS)).size);
    expect(solverSettingIncluded("extension_field", STEADY)).toBe(true);
  });

  it("puts every solver schema field in a section, none left for Other settings", () => {
    expect(SOLVER_NAMES.length).toBeGreaterThanOrEqual(10);
    for (const name of SOLVER_NAMES) expect(fieldsOf(SOLVER_SETTINGS_SECTIONS)).toContain(name);
  });

  it("every Basic name exists in the schema", () => {
    for (const name of BASIC_SOLVER) expect(SOLVER_NAMES).toContain(name);
    for (const name of BASIC_MESH) expect(MESH_NAMES).toContain(name);
    for (const names of Object.values(MESH_TASK_FIELDS)) for (const name of names) expect(MESH_NAMES).toContain(name);
    expect(INITIAL_TURBULENCE_SECTION).toBe("solver-initial");
  });

  it("shows time step and end time only for a transient run", () => {
    for (const name of ["delta_t", "end_time"]) {
      expect(solverSettingIncluded(name, STEADY)).toBe(false);
      expect(solverSettingIncluded(name, { ...STEADY, transient: true })).toBe(true);
    }
    expect(solverSettingIncluded("transient", STEADY)).toBe(true);
  });

  it("shows the initial-turbulence fields only with a turbulence model", () => {
    for (const name of ["initial_velocity", "initial_intensity", "initial_length_scale", "initial_viscosity_ratio"]) {
      expect(solverSettingIncluded(name, STEADY)).toBe(false);
      expect(solverSettingIncluded(name, { ...STEADY, turbulence: "kOmegaSST" })).toBe(true);
    }
  });

  it("shows the density model, molar mass, gravity and operating pressure only with the energy equation", () => {
    for (const name of ["density_model", "molar_mass", "gravity", "operating_pressure"]) {
      expect(solverSettingIncluded(name, STEADY)).toBe(false);
      expect(solverSettingIncluded(name, { ...STEADY, energy: true })).toBe(true);
    }
  });

  it("a steady laminar run sees no transient, turbulence-start or gas field in any section", () => {
    const properties = Object.fromEntries(ALL_SOLVER_FIELDS.map((name) => [name, { type: "number" as const }]));
    const shown = groupSettingsFields(properties, SOLVER_SETTINGS_SECTIONS, (name) => solverSettingIncluded(name, STEADY)).flatMap((g) => g.fields);
    for (const name of ["delta_t", "end_time", "initial_velocity", "initial_intensity", "initial_length_scale", "initial_viscosity_ratio", "density_model", "molar_mass"]) {
      expect(shown).not.toContain(name);
    }
    expect(shown).toEqual(expect.arrayContaining(["engine", "turbulence", "energy", "transient", "iterations"]));
  });

  it("drops the empty Transient and Initial turbulence sections with their fields", () => {
    const properties = Object.fromEntries(ALL_SOLVER_FIELDS.map((name) => [name, { type: "number" as const }]));
    const steady = groupSettingsFields(properties, SOLVER_SETTINGS_SECTIONS, (name) => solverSettingIncluded(name, STEADY)).map((g) => g.id);
    expect(steady).not.toContain("solver-transient");
    expect(steady).not.toContain(INITIAL_TURBULENCE_SECTION);
    const turbulent = groupSettingsFields(properties, SOLVER_SETTINGS_SECTIONS, (name) => solverSettingIncluded(name, { ...STEADY, turbulence: "kEpsilon", transient: true })).map((g) => g.id);
    expect(turbulent).toEqual(expect.arrayContaining(["solver-transient", INITIAL_TURBULENCE_SECTION]));
  });

  it("a finding that names the settings node opens Advanced", () => {
    expect(findingsTouch([{ nodes: ["other"] }, { nodes: ["settings-1"] }], "settings-1")).toBe(true);
    expect(findingsTouch([{ nodes: ["other"] }], "settings-1")).toBe(false);
    expect(findingsTouch([], "settings-1")).toBe(false);
  });

  it("places every mesh setting in its intended group", () => {
    const fields = fieldsOf(MESH_SETTINGS_SECTIONS);
    expect(fields).toEqual([
      "mesher", "domain", "domain_part", "box", "margin", "two_d", "plane", "thickness", "wedge_angle",
      "base_size", "surface_size", "max_cells", "feature_angle",
      "layers", "layer_mode", "expansion_ratio", "final_ratio", "first_layer_height", "min_thickness_ratio", "layer_feature_angle",
      "max_non_ortho", "max_skewness", "accept_unassigned", "host", "cores",
    ]);
    expect(fields).toHaveLength(new Set(fields).size);
    // Every mesh setting the user can edit is in a section (the mesh node's other params are not settings).
    for (const name of fields) expect(MESH_NAMES).toContain(name);
  });

  it("filters mesh controls by mesher, domain, and layer mode while retaining extensions", () => {
    const snappyBox = { mesher: "snappy", domain: "box", layerMode: "first_height" } as const;
    expect(meshSettingIncluded("box", snappyBox)).toBe(true);
    expect(meshSettingIncluded("domain_part", snappyBox)).toBe(false);
    expect(meshSettingIncluded("margin", snappyBox)).toBe(true);
    expect(meshSettingIncluded("surface_size", snappyBox)).toBe(true);
    expect(meshSettingIncluded("final_ratio", snappyBox)).toBe(false);
    expect(meshSettingIncluded("first_layer_height", snappyBox)).toBe(true);
    expect(meshSettingIncluded("extension_field", snappyBox)).toBe(true);

    const blockPart = { mesher: "blockmesh", domain: "part", layerMode: "final_ratio" } as const;
    expect(meshSettingIncluded("domain_part", blockPart)).toBe(true);
    expect(meshSettingIncluded("box", blockPart)).toBe(false);
    expect(meshSettingIncluded("margin", blockPart)).toBe(true);
    expect(meshSettingIncluded("layers", blockPart)).toBe(false);
  });
});
