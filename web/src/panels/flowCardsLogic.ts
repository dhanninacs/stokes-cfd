/**
 * The two flow helpers (ADR-0022 §10.2, T-050): "External flow…" and "Internal flow…" in the Prepare
 * menu, and the rules that make them work with the existing surfaces rather than a new framework.
 *
 * §10.2 is explicit that these reuse what exists:
 *
 * * **External flow…** is the enclosure card with a velocity field and an "Add default conditions"
 *   switch (on); when the operation finishes, the front end calls `setup.defaults(external)` and opens
 *   the conditions list.
 * * **Internal flow…** is the extract-fluid card with its seed pick, then an **Openings** step: the
 *   output's `caps` faces are highlighted, the user picks the inlet cap(s) → Next → the outlet cap(s)
 *   → Finish calls `setup.defaults(internal, inlet, outlet)`.
 * * "There is no wizard framework."
 *
 * So the state machine here is small and pure: which step the openings picker is on, whether a step
 * can advance, and what the finish issues. `FlowCards.tsx` renders it and calls; the menu gets the two
 * entries as data.
 */

import type { TagRegionMember } from "../api/types";

/** The two templates `setup.defaults` accepts (from `TEMPLATES` in `stokes/api/commands/setup.py`). */
export type FlowTemplate = "external" | "internal";

/** What the External flow card asks for beyond the enclosure operation. */
export interface ExternalFlowOptions {
  /** The velocity written to the created velocity inlet; "" leaves it to `setup.check`. */
  velocity: string;
  /** Whether to call `setup.defaults` when the enclosure finishes (§10.2: on by default). */
  addDefaults: boolean;
}

/** The default the card starts from: the switch on, no velocity typed. */
export const DEFAULT_EXTERNAL: ExternalFlowOptions = { velocity: "", addDefaults: true };

/** Which cap the openings picker is asking for; null when it is not running. */
export type OpeningsStep = "inlet" | "outlet" | null;

/**
 * The openings picker's state: the caps picked so far and the step it is on.
 *
 * §10.2's sequence is inlet → Next → outlet → Finish, and the two sets must be disjoint: a face
 * cannot be both an inlet and an outlet, and the server would refuse a tag that is both. That is the
 * one rule worth enforcing in the UI rather than discovering from an error.
 */
export interface OpeningsState {
  step: OpeningsStep;
  inlet: TagRegionMember[];
  outlet: TagRegionMember[];
}

/** The picker before it starts. */
export const NO_OPENINGS: OpeningsState = { step: null, inlet: [], outlet: [] };

/** Start the openings step at the inlet. */
export function beginOpenings(): OpeningsState {
  return { step: "inlet", inlet: [], outlet: [] };
}

/**
 * Take the currently picked faces as the answer to the current step and move on.
 *
 * From `inlet` this advances to `outlet`; from `outlet` it finishes (step becomes null). A step with
 * an empty pick does nothing, so Next is inert rather than storing an empty cap and moving on.
 * Faces already taken as the inlet are removed from the outlet answer, because one face cannot be
 * both (§10.2 assumes they are disjoint; the server would refuse an overlap).
 */
export function takeOpenings(state: OpeningsState, picked: readonly TagRegionMember[]): OpeningsState {
  if (state.step === null || picked.length === 0) return state;
  if (state.step === "inlet") return { step: "outlet", inlet: [...picked], outlet: [] };
  const inletKeys = new Set(state.inlet.map((member) => member.part));
  const outlet = picked.filter((member) => !inletKeys.has(member.part) || !overlaps(state.inlet, member));
  return { step: null, inlet: state.inlet, outlet };
}

/** Whether two members of the same part name any face in common. */
function overlaps(left: readonly TagRegionMember[], right: TagRegionMember): boolean {
  const ranges = left.filter((member) => member.part === right.part).map((member) => member.faces);
  if (right.faces === "all") return ranges.length > 0;
  for (const faces of ranges) {
    if (faces === "all") return true;
    for (const [a, b] of right.faces) {
      for (const [c, d] of faces) {
        if (a <= d && c <= b) return true;
      }
    }
  }
  return false;
}

/**
 * Whether Next/Finish can be pressed: there is an inlet, and the current step has a pick to take.
 *
 * At the `outlet` step this means "the inlet is taken, so Finish will produce something"; at the
 * `inlet` step it means "the inlet itself is still being chosen", which cannot advance. A state whose
 * step is null has already finished (or never started) and is not ready for Next.
 */
export function openingsReady(state: OpeningsState): boolean {
  if (state.step === "outlet") return state.inlet.length > 0;
  return false;
}

/** Whether the picker has finished and produced two openings to hand to `setup.defaults`. */
export function openingsFinished(state: OpeningsState): boolean {
  return state.step === null && state.inlet.length > 0 && state.outlet.length > 0;
}

/** Whether the two openings are disjoint, which is what `setup.defaults` needs. */
export function openingsDisjoint(state: OpeningsState): boolean {
  const parts = new Set(state.inlet.map((member) => member.part));
  for (const member of state.outlet) {
    if (!parts.has(member.part)) continue;
    if (overlaps(state.inlet, member)) return false;
  }
  return true;
}

/**
 * The parameters `setup.defaults` takes for a finished run (§10.2).
 *
 * `target` is the prepared body's node id and `inlet`/`outlet` are the cap members; `velocity` is
 * passed only when it was typed, so the server's own default applies otherwise. Returning the
 * parameters rather than issuing the call keeps this testable and lets the caller add `node_ids`
 * from a replay.
 */
export function defaultsParams(
  template: FlowTemplate,
  target: string,
  openings: OpeningsState,
  external: ExternalFlowOptions = DEFAULT_EXTERNAL,
  material: string | null = null,
): Record<string, unknown> {
  const params: Record<string, unknown> = { target, template };
  if (template === "internal") {
    if (openings.inlet.length > 0) params.inlet = openings.inlet;
    if (openings.outlet.length > 0) params.outlet = openings.outlet;
  } else if (external.velocity.trim() !== "") {
    params.velocity = external.velocity.trim();
  }
  if (material !== null && material !== "") params.material = material;
  return params;
}

/**
 * Whether the External flow card should call `setup.defaults` when its operation finishes: only when
 * the switch is on (§10.2), so a user who wants the enclosure alone is not given conditions.
 */
export function shouldAddDefaults(template: FlowTemplate, external: ExternalFlowOptions): boolean {
  return template === "internal" ? true : external.addDefaults;
}

/** The heading the openings picker shows for its current step, or "" when it is not running. */
export function openingsPrompt(state: OpeningsState): string {
  if (state.step === "inlet") return "Pick the inlet cap, or caps";
  if (state.step === "outlet") return "Pick the outlet cap, or caps";
  return "";
}
