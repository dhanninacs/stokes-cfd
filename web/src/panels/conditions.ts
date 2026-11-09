/**
 * The condition catalogue (`boundary.kinds`) in the form the assign menu works with: which of a
 * kind's parameters the values hold in use, which of those the value form asks for, and the
 * defaults it starts from.
 *
 * The rules are the server's (`stokes.physics.conditions.catalogue`): a parameter's `when` maps a
 * switch to the choices under which it is used, every entry must hold, and a switch the values do
 * not hold counts as its default, the first choice of the switch's own parameter.
 */

import type { ConditionKind, ConditionParam } from "../api/types";

/** The value a switch parameter has for these values: what they hold, else its default. */
function switchValue(kind: ConditionKind, name: string, values: Record<string, unknown>): unknown {
  const held = values[name];
  if (held !== undefined && held !== null && held !== "") return held;
  const own = kind.params.find((p) => p.name === name);
  return own?.schema.default ?? own?.schema.enum?.[0];
}

/** The parameters the kind uses with these values, in catalogue order. */
export function inUseParams(kind: ConditionKind, values: Record<string, unknown>): ConditionParam[] {
  return kind.params.filter((param) =>
    Object.entries(param.when).every(([name, choices]) => {
      const value = switchValue(kind, name, values);
      return typeof value === "string" && choices.includes(value);
    }),
  );
}

/** The switches that choose which of these parameters apply: their `when` keys. */
export function switchesFor(params: readonly ConditionParam[]): Set<string> {
  const names = new Set<string>();
  for (const param of params) for (const name of Object.keys(param.when)) names.add(name);
  return names;
}

/**
 * The parameters each kind's form leads with: the ones a person gives a value for. The rest of the kind's
 * parameters (the backflow temperature of an outlet, a velocity inlet's turbulence settings) keep their
 * defaults and sit under "More..." in the cards and the Details page. A wall, a symmetry plane and a cyclic
 * pair lead with nothing: a wall is no-slip until its options are opened.
 */
const HEADLINE: Readonly<Record<string, readonly string[]>> = {
  velocity_inlet: ["direction", "velocity", "velocity_x", "velocity_y", "velocity_z", "direction_vector"],
  mass_flow_inlet: ["mass_flow"],
  pressure_inlet: ["total_pressure"],
  pressure_outlet: ["pressure"],
  wall: [],
  symmetry: [],
};

/**
 * What the value form asks for: only the parameters the kind leads with (`HEADLINE`), as the values hold
 * them, so a pressure outlet asks for its pressure and nothing of velocity or mass flow. A kind the table
 * does not know (one a later server adds) falls back to its required parameters and the switches that choose
 * among them; a kind that needs nothing is assigned at once. Every other parameter keeps its default and is
 * reachable under "More..." (`moreFields`) and in the Details page.
 */
export function formFields(kind: ConditionKind, values: Record<string, unknown>): ConditionParam[] {
  const inUse = inUseParams(kind, values);
  const head = HEADLINE[kind.id];
  if (head !== undefined) return inUse.filter((param) => head.includes(param.name));
  const required = inUse.filter((param) => param.required);
  if (required.length === 0) return [];
  const switches = switchesFor(required);
  return inUse.filter((param) => param.required || switches.has(param.name));
}

/** The kind's other parameters in use with these values, for a collapsed "More..." section. */
export function moreFields(kind: ConditionKind, values: Record<string, unknown>): ConditionParam[] {
  const shown = new Set(formFields(kind, values).map((param) => param.name));
  return inUseParams(kind, values).filter((param) => !shown.has(param.name));
}

/** What the physics of the run decides about a boundary's fields: the solver's own settings. */
export interface PhysicsContext {
  /** The energy equation is on. */
  energy: boolean;
  /** The turbulence model (`laminar` for none). */
  turbulence: string;
}

/** The boundary parameters that only mean something when the energy equation is solved. */
export const TEMPERATURE_PARAMS: ReadonlySet<string> = new Set([
  "temperature",
  "backflow_temperature",
  "ambient_temperature",
  "heat_flux",
  "heat_transfer_coefficient",
  "thermal",
]);

/** The boundary parameters that only mean something when a turbulence model runs. */
export const TURBULENCE_PARAMS: ReadonlySet<string> = new Set(["turbulence", "intensity", "viscosity_ratio", "length_scale"]);

/** True when the run's physics makes this parameter meaningless: temperature with energy off, turbulence for laminar. */
export function hiddenByPhysics(name: string, physics: PhysicsContext | undefined): boolean {
  if (physics === undefined) return false;
  if (!physics.energy && TEMPERATURE_PARAMS.has(name)) return true;
  if (physics.turbulence === "laminar" && TURBULENCE_PARAMS.has(name)) return true;
  return false;
}

/**
 * The layer override is a wall's: the snapshot refuses it on any other kind ("layers need a wall"). It shows
 * for a wall, and for a boundary that already holds one, so a stale value can still be seen and cleared.
 */
function layersShown(kindId: string | null, values: Record<string, unknown>): boolean {
  if (kindId === "wall") return true;
  const held = values.layers;
  return held !== undefined && held !== null && held !== "";
}

/**
 * Which of a boundary node's schema fields the Details page shows, and which it folds under "More...".
 * `names` are the node type's parameters. A parameter that some kind of the catalogue uses is hidden unless
 * this boundary's own kind uses it with its current values (a pressure outlet shows no velocity, mass flow
 * or direction); a parameter no kind names (the node's own bookkeeping) stays in the main part, except the
 * walls-only `layers`. With `physics`, the temperature family is dropped while the energy equation is off and
 * the turbulence family on a laminar run: hidden, never cleared, so the stored values stand. With no
 * catalogue or an unknown kind nothing else is hidden or folded.
 */
export function boundaryFieldSplit(
  kinds: readonly ConditionKind[],
  kindId: string | null,
  values: Record<string, unknown>,
  names: readonly string[],
  physics?: PhysicsContext,
): { shown: Set<string>; more: Set<string> } {
  const own = kindId === null ? undefined : kinds.find((k) => k.id === kindId);
  const keep = (name: string): boolean => !hiddenByPhysics(name, physics) && (name !== "layers" || layersShown(kindId, values));
  if (own === undefined) return { shown: new Set(names.filter(keep)), more: new Set() };
  const conditional = new Set<string>();
  for (const k of kinds) for (const p of k.params) conditional.add(p.name);
  const lead = new Set(formFields(own, values).map((p) => p.name));
  const rest = new Set(moreFields(own, values).map((p) => p.name));
  const shown = new Set<string>();
  const more = new Set<string>();
  for (const name of names) {
    if (!keep(name)) continue;
    if (!conditional.has(name) || lead.has(name)) shown.add(name);
    else if (rest.has(name)) more.add(name);
  }
  return { shown, more };
}

/**
 * The values an assign sends: those the kind uses with these values, and nothing else. The form starts from
 * every parameter's default, and the server refuses a value it does not use ("velocity_x is used only with
 * direction components"), so a velocity inlet with the default normal direction must not send the three
 * components that default to zero. Empty values are left out too: the server's own default stands.
 */
export function submittedValues(kind: ConditionKind, values: Record<string, unknown>): Record<string, unknown> {
  const used = new Set(inUseParams(kind, values).map((param) => param.name));
  const out: Record<string, unknown> = {};
  for (const [name, value] of Object.entries(values)) {
    if (!used.has(name) || value === undefined || value === null || value === "") continue;
    out[name] = value;
  }
  return out;
}

/** Every parameter's default: the values the form starts from. */
export function defaultValues(kind: ConditionKind): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const param of kind.params) if (param.schema.default !== undefined) out[param.name] = param.schema.default;
  return out;
}
