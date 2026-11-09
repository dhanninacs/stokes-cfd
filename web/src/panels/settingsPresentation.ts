import type { PropertySchema } from "../api/types";
import { followsShape } from "./mesherKinds";

export interface SettingsSectionSpec {
  id: string;
  title: string;
  description?: string;
  fields: readonly string[];
  defaultOpen?: boolean;
}

export interface GroupedSettingsField {
  id: string;
  title: string;
  description?: string;
  defaultOpen: boolean;
  fields: string[];
}

/** Search includes collapsed controls; validation errors stay reachable even with a filter. */
export function searchSettingsGroups(
  groups: readonly GroupedSettingsField[],
  properties: Record<string, PropertySchema>,
  query: string,
  errors?: Readonly<Record<string, string>>,
): GroupedSettingsField[] {
  const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (!terms.length) return [...groups];
  return groups.flatMap((group) => {
    const fields = group.fields.filter((name) => {
      const schema = properties[name];
      const text = `${group.title} ${group.description ?? ""} ${name.replaceAll("_", " ")} ${schema?.title ?? ""} ${schema?.description ?? ""}`.toLowerCase();
      return Boolean(errors?.[name]) || terms.every((term) => text.includes(term));
    });
    return fields.length ? [{ ...group, fields }] : [];
  });
}

/** Assigns visible schema fields once, in section order, then leaves leftovers in Other settings. */
export function groupSettingsFields(
  properties: Record<string, PropertySchema>,
  sections: readonly SettingsSectionSpec[],
  include?: (name: string) => boolean,
): GroupedSettingsField[] {
  const eligible = new Set(
    Object.entries(properties)
      .filter(([, schema]) => schema["x-hidden"] !== true)
      .filter(([name]) => include?.(name) ?? true)
      .map(([name]) => name),
  );
  const assigned = new Set<string>();
  const groups = sections.map((section) => {
    const fields: string[] = [];
    for (const name of section.fields) {
      if (eligible.has(name) && !assigned.has(name)) {
        assigned.add(name);
        fields.push(name);
      }
    }
    return { id: section.id, title: section.title, ...(section.description === undefined ? {} : { description: section.description }), defaultOpen: section.defaultOpen ?? true, fields };
  });
  const populated = groups.filter((group) => group.fields.length > 0);
  const other = Object.keys(properties).filter((name) => eligible.has(name) && !assigned.has(name));
  if (other.length) populated.push({ id: "other-settings", title: "Other settings", defaultOpen: false, fields: other });
  return populated;
}

/**
 * The Basic names of the two settings pages (`setup/basicSets.ts` builds on these). Mesh: `base_size` is drawn
 * as Coarse / Medium / Fine and `layers` as the Boundary layers switch; the exact fields stay in Advanced.
 * Solver: the flow model (`turbulence`) and `iterations` are the real fields.
 */
export const BASIC_MESH: readonly string[] = ["base_size", "layers"];
export const BASIC_SOLVER: readonly string[] = ["turbulence", "iterations"];

/** The section the Solve tree's Initialisation row opens (it exists only for a turbulent run). */
export const INITIAL_TURBULENCE_SECTION = "solver-initial";

/**
 * The settings fields a Mesh task page draws itself. While that page is shown with the Mesh panel, Advanced
 * leaves them out, so a field is on screen once (the Sizing page and the Mesh page both showed the base size).
 */
export const MESH_TASK_FIELDS: Readonly<Record<string, readonly string[]>> = {
  sizing: ["base_size", "surface_size"],
  surface: ["feature_angle"],
  layers: ["layers", "layer_mode", "expansion_ratio", "final_ratio", "first_layer_height", "min_thickness_ratio"],
};

/** The section that is the Solve page's landing: with it the page shows Run, the residuals and the Basic settings. */
export const SOLVER_LANDING_SECTION = "solver-model";

/** A `solve.check` finding that names the settings node: the settings page opens Advanced so the cause is in sight. */
export function findingsTouch(findings: readonly { nodes: readonly string[] }[], settingsId: string): boolean {
  return findings.some((f) => f.nodes.includes(settingsId));
}

export const SOLVER_SETTINGS_SECTIONS: readonly SettingsSectionSpec[] = [
  { id: "solver-model", title: "Model", description: "Choose the solver and how to model the flow and heat transfer.", fields: ["engine", "turbulence", "energy", "density_model", "molar_mass", "transient"] },
  {
    id: "solver-transient",
    title: "Transient",
    description: "Physical time. An adaptive step keeps the Courant number under the limit; averages are written as UMean, pMean, ...",
    fields: ["delta_t", "end_time", "adjustable_time_step", "max_courant", "write_time", "time_average", "average_start"],
  },
  { id: INITIAL_TURBULENCE_SECTION, title: "Initial turbulence", fields: ["initial_velocity", "initial_intensity", "initial_length_scale", "initial_viscosity_ratio"] },
  { id: "solver-reference", title: "Reference conditions", description: "Set gravity and the operating pressure used by the flow model.", fields: ["gravity", "operating_pressure"], defaultOpen: false },
  { id: "solver-limits", title: "Run limits", description: "Set the iteration limit and residual target. Reaching the limit does not mean the solution has converged.", fields: ["iterations", "residual_target"] },
  { id: "solver-output", title: "Output", description: "Choose how often results are saved.", fields: ["write_interval"], defaultOpen: false },
  { id: "solver-execution", title: "Execution", description: "1 runs serially; larger counts use MPI.", fields: ["host", "cores"], defaultOpen: false },
  { id: "solver-result", title: "Result slice", description: "Position a cutting plane for inspecting the solution.", fields: ["slice_origin", "slice_normal"], defaultOpen: false },
];

export interface MeshSettingsContext {
  mesher: string;
  domain: string;
  layerMode: string;
}

export const MESH_SETTINGS_SECTIONS: readonly SettingsSectionSpec[] = [
  { id: "mesh-mesher", title: "Mesher", description: "Choose the tool that divides your geometry into cells.", fields: ["mesher"] },
  { id: "mesh-domain", title: "Domain", description: "Select the volume in which the flow will be calculated.", fields: ["domain", "domain_part", "box", "margin"] },
  {
    id: "mesh-2d",
    title: "2D",
    description: "Mesh one cell thick for a 2D run: planar (empty front and back) or axisymmetric (a wedge).",
    fields: ["two_d", "plane", "thickness", "wedge_angle"],
    defaultOpen: false,
  },
  { id: "mesh-sizing", title: "Sizing", description: "Smaller cells capture more detail but need more memory and time.", fields: ["base_size", "surface_size", "max_cells"] },
  { id: "mesh-edges", title: "Sharp edges", description: "Control which corners and edges receive extra detail.", fields: ["feature_angle"], defaultOpen: false },
  {
    id: "mesh-layers",
    title: "Boundary layers",
    description: "Add thin cells next to walls to resolve near-wall flow.",
    fields: ["layers", "layer_mode", "expansion_ratio", "final_ratio", "first_layer_height", "min_thickness_ratio", "layer_feature_angle"],
    defaultOpen: false,
  },
  { id: "mesh-quality", title: "Quality", description: "Advanced cell-quality limits and boundary assignment checks.", fields: ["max_non_ortho", "max_skewness", "accept_unassigned"], defaultOpen: false },
  { id: "mesh-execution", title: "Execution", fields: ["host", "cores"], defaultOpen: false },
];

/** What the solver settings say that decides which of their own fields apply (`readSetupContext` gives the same three). */
export interface SolverContext {
  energy: boolean;
  turbulence: string;
  transient: boolean;
}

const TRANSIENT_FIELDS: readonly string[] = ["delta_t", "end_time", "adjustable_time_step", "max_courant", "write_time", "time_average", "average_start"];
const INITIAL_TURBULENCE_FIELDS: readonly string[] = ["initial_velocity", "initial_intensity", "initial_length_scale", "initial_viscosity_ratio"];
const ENERGY_FIELDS: readonly string[] = ["density_model", "molar_mass", "gravity", "operating_pressure"];

/**
 * Whether a solver setting applies to this run. Time step and end time need a transient run, the four
 * initial-turbulence fields a turbulence model, the density model, molar mass, gravity and operating pressure the energy equation. A name
 * the rule does not know (a schema extension) stays visible. It hides only: a stored value is never cleared.
 */
export function solverSettingIncluded(name: string, context: SolverContext): boolean {
  if (TRANSIENT_FIELDS.includes(name)) return context.transient;
  if (INITIAL_TURBULENCE_FIELDS.includes(name)) return context.turbulence !== "laminar";
  if (ENERGY_FIELDS.includes(name)) return context.energy;
  return true;
}

export function meshSettingIncluded(name: string, context: MeshSettingsContext): boolean {
  if (name === "box") return context.domain === "box";
  if (name === "domain_part") return context.domain !== "box";
  // cfMesh meshes a part's own volume (ADR-0032), so it has no margin; blockMesh and a snappy box do.
  if (name === "margin") return context.mesher === "blockmesh" || (context.mesher === "snappy" && context.domain === "box");
  if (["surface_size", "layers", "layer_mode", "expansion_ratio"].includes(name)) return followsShape(context.mesher);
  // The feature angle reaches snappy's dictionaries and the poly-hexcore dual's argument; the other cfMesh fills ignore it.
  if (name === "feature_angle") return context.mesher === "snappy" || context.mesher === "polyhex";
  // snappy-only layer controls: cfMesh sizes its layers from the local cell and has no equivalent.
  if (["min_thickness_ratio", "layer_feature_angle"].includes(name)) return context.mesher === "snappy";
  if (name === "final_ratio") return context.mesher === "snappy" && context.layerMode === "final_ratio";
  if (name === "first_layer_height") return followsShape(context.mesher) && context.layerMode === "first_height";
  return true;
}

