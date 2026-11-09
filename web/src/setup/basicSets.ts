/**
 * The one model of what set-up shows first and what waits under Advanced, for both modes.
 *
 * Guided mode's cards and the Full-mode Details pane ask for the same things on a first run: a body's role,
 * a region's material, a boundary's kind and its one lead value. This module owns that list per node type
 * (`BASIC`), says for every field of a node whether it is Basic, Advanced or hidden (`visibleFields`), and
 * holds the two small tables the cards and the pane share (`KIND_WORDS`, `INLINE_VALUE`). A parity test
 * (`guidedParity.test.ts`) asserts every card control is a Basic field of the same node type, so the two
 * modes cannot drift.
 *
 * A boundary's lead values are the guided cards' own form rules (`formFields` in `panels/conditions.ts`);
 * nothing here copies that table. The physics filter (`PhysicsContext`) only hides: a value kept behind it
 * stays stored and the snapshot still reads it.
 *
 * Import rule: no React, no store, no DOM.
 */
import type { ConditionKind, NodeSummary, ParamSchema } from "../api/types";
import { boundaryFieldSplit, formFields, switchesFor, type PhysicsContext } from "../panels/conditions";
import type { Role } from "../panels/meshTaskPages/bodiesModel";
import { BASIC_MESH, BASIC_SOLVER, meshSettingIncluded, solverSettingIncluded } from "../panels/settingsPresentation";

/** The boundary kinds the guided card offers and the Details pane names, by the catalogue's id. */
export const KIND_WORDS: Readonly<Record<string, string>> = {
  velocity_inlet: "Inlet",
  mass_flow_inlet: "Inlet (mass flow)",
  pressure_inlet: "Inlet (pressure)",
  pressure_outlet: "Outlet",
  wall: "Wall",
  symmetry: "Symmetry",
  cyclic: "Cyclic",
  interface: "Interface",
};

/** The one parameter a kind's row edits in place, with its unit. */
export interface InlineValue {
  param: "velocity" | "pressure";
  unit: string;
}

/** The kinds that carry one inline value: an inlet's speed, an outlet's pressure. */
export const INLINE_VALUE: Readonly<Record<string, InlineValue>> = {
  velocity_inlet: { param: "velocity", unit: "m/s" },
  pressure_outlet: { param: "pressure", unit: "Pa" },
};

/** The roles a body can take, in the words both modes use. The Geometry card offers the first, second and last. */
export const BODY_ROLES: readonly { value: Role; label: string }[] = [
  { value: "fluid", label: "Fluid" },
  { value: "solid", label: "Solid" },
  { value: "hole", label: "Hole" },
  { value: "left_out", label: "Leave out" },
];

/** What the run's own settings say, as far as set-up pages care: the solver settings node. */
export interface SetupContext extends PhysicsContext {
  /** A transient run (time step and end time matter). */
  transient: boolean;
}

/** The solver settings' defaults: steady, laminar, no energy equation. */
export const DEFAULT_CONTEXT: SetupContext = { energy: false, turbulence: "laminar", transient: false };

function child(node: NodeSummary | null | undefined, path: string): NodeSummary | null {
  return node?.children?.find((c) => c.path === path) ?? null;
}

/** A solver settings node's own params as a context; the defaults for what they leave out. */
export function setupContextOf(params: Record<string, unknown>): SetupContext {
  return {
    energy: params.energy === true,
    turbulence: typeof params.turbulence === "string" && params.turbulence !== "" ? params.turbulence : DEFAULT_CONTEXT.turbulence,
    transient: params.transient === true,
  };
}

/** The solver settings of the project (`/solver/settings`) as a context; the defaults while there are none. */
export function readSetupContext(tree: NodeSummary | null): SetupContext {
  const params = child(child(tree, "/solver"), "/solver/settings")?.params;
  return params === undefined ? DEFAULT_CONTEXT : setupContextOf(params);
}

export type FieldLevel = "basic" | "advanced" | "hidden";

/**
 * The names Basic shows per node type. Most are schema parameters; `role` and `readout` are the two a page
 * draws itself (a body's role chips, a material's density and viscosity). A boundary's lead values depend on
 * its kind, so `visibleFields` adds them to `kind`.
 */
export const BASIC: Readonly<Record<string, readonly string[]>> = {
  boundary: ["kind"],
  region: ["material"],
  material: ["readout"],
  part: ["role"],
  // The two settings pages. Mesh: `base_size` and `layers` are drawn as Coarse / Medium / Fine and the layers
  // switch. Solver: the flow model and the iteration limit are the fields themselves.
  mesh_settings: BASIC_MESH,
  solver_settings: BASIC_SOLVER,
};

/** Basic names whose exact field also stays in Advanced, because the Basic control is a preset of it. */
export const ALSO_ADVANCED: Readonly<Record<string, readonly string[]>> = {
  mesh_settings: BASIC_MESH,
};

/** The node types whose page is split into Basic and Advanced. */
export function hasBasicSet(type: string): boolean {
  return type in BASIC;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** A switch at its default: the value is unset, empty or the schema's own default (else the catalogue's first choice). */
function atDefault(kind: ConditionKind, name: string, values: Record<string, unknown>): boolean {
  const held = values[name];
  if (held === undefined || held === null || held === "") return true;
  const own = kind.params.find((p) => p.name === name);
  return held === (own?.schema.default ?? own?.schema.enum?.[0]);
}

/**
 * The boundary's Basic names: the kind's lead values (`formFields`), with a switch left at its default (the
 * velocity inlet's "normal" direction) moved to Advanced. Off its default the switch and what it chooses all
 * show, so a value in use is never out of sight.
 */
function boundaryBasic(kind: ConditionKind, values: Record<string, unknown>, ctx: SetupContext): Set<string> {
  const lead = formFields(kind, values);
  const switches = switchesFor(lead);
  const out = new Set<string>();
  for (const param of lead) if (!switches.has(param.name) || !atDefault(kind, param.name, values)) out.add(param.name);
  // **With energy on, an inlet's temperature is a lead value.** It sat under Advanced, so the
  // walkthrough read "set the temperature" in the status bar while the field that sets it was folded
  // away; the outlet's backflow temperature only matters if fluid flows back in, and stays Advanced.
  if (ctx.energy && kind.family === "inlet" && kind.params.some((p) => p.name === "temperature")) out.add("temperature");
  return out;
}

/**
 * For each schema field of a node: shown with Advanced closed (`basic`), shown under Advanced (`advanced`),
 * or not shown at all (`hidden`).
 *
 * * boundary: `kind` and the kind's lead values are Basic. Advanced holds the kind's other values, the tag,
 *   the mesh size, and `layers` (walls only). The temperature family waits for the energy equation and the
 *   turbulence family for a turbulence model.
 * * region: `material` is Basic; the kind and tag are Advanced.
 * * material, body (`part`): every schema field is Advanced; the page's own readout or role chips are Basic.
 * * mesh settings: `base_size` and `layers` are Basic (the fineness chips and the layers switch); what the mesher,
 *   the domain and the layer mode make meaningless is hidden (`meshSettingIncluded`).
 * * solver settings: `turbulence` and `iterations` are Basic; time step and end time need a transient run, the
 *   initial-turbulence fields a turbulence model, density model and molar mass the energy equation
 *   (`solverSettingIncluded`).
 * * any other type has no Basic set: every field stays `basic` (it is shown as it always was).
 */
export function visibleFields(
  type: string,
  names: readonly string[],
  params: Record<string, unknown>,
  ctx: SetupContext,
  kinds: readonly ConditionKind[] = [],
): Record<string, FieldLevel> {
  const out: Record<string, FieldLevel> = {};
  if (!hasBasicSet(type)) {
    for (const name of names) out[name] = "basic";
    return out;
  }
  if (type === "boundary") {
    const kindId = typeof params.kind === "string" && params.kind !== "" ? params.kind : null;
    const own = kindId === null ? undefined : kinds.find((k) => k.id === kindId);
    const { shown, more } = boundaryFieldSplit(kinds, kindId, params, names, ctx);
    const basic = own === undefined ? new Set<string>() : boundaryBasic(own, params, ctx);
    for (const name of names) {
      if (name === "kind" || basic.has(name)) out[name] = "basic";
      else if (shown.has(name) || more.has(name)) out[name] = "advanced";
      else out[name] = "hidden";
    }
    return out;
  }
  const basic = new Set(BASIC[type] ?? []);
  const applies = appliesFor(type, params, ctx);
  for (const name of names) out[name] = !applies(name) ? "hidden" : basic.has(name) ? "basic" : "advanced";
  return out;
}

/** The "does this field mean anything now" rule of a settings page; every other type applies all its fields. */
function appliesFor(type: string, params: Record<string, unknown>, ctx: SetupContext): (name: string) => boolean {
  if (type === "solver_settings") return (name) => solverSettingIncluded(name, ctx);
  if (type === "mesh_settings") {
    const context = {
      mesher: typeof params.mesher === "string" ? params.mesher : "blockmesh",
      domain: typeof params.domain === "string" ? params.domain : "part",
      layerMode: typeof params.layer_mode === "string" ? params.layer_mode : "final_ratio",
    };
    return (name) => meshSettingIncluded(name, context);
  }
  return () => true;
}

/** The names Advanced holds: the Advanced level, plus the exact fields behind a Basic preset (`ALSO_ADVANCED`). */
export function advancedNames(type: string, levels: Record<string, FieldLevel>, names: readonly string[]): string[] {
  const also = new Set(ALSO_ADVANCED[type] ?? []);
  return names.filter((name) => levels[name] === "advanced" || (levels[name] === "basic" && also.has(name)));
}

/** The names at one level, in the order `names` gives them. */
export function namesAt(levels: Record<string, FieldLevel>, level: FieldLevel, names: readonly string[]): string[] {
  return names.filter((name) => levels[name] === level);
}

function plain(value: unknown): unknown {
  return isRecord(value) && "value" in value ? value.value : value;
}

/**
 * Which of these fields hold something other than the schema's default: the dot on the Advanced disclosure.
 * A field with no default (the layer override, a boundary's mesh size) differs as soon as it holds a value.
 */
export function changedFromDefault(properties: Record<string, ParamSchema>, params: Record<string, unknown>, names: readonly string[]): string[] {
  return names.filter((name) => {
    const held = plain(params[name]);
    const schema = properties[name];
    if (held === undefined || held === null || held === "") return false;
    const wanted = plain(schema?.default);
    if (wanted === undefined || wanted === null) return true;
    return JSON.stringify(held) !== JSON.stringify(wanted);
  });
}
