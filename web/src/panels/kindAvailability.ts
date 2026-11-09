import type { ConditionKind } from "../api/types";

/**
 * Whether a condition kind can be chosen, and the few words that say why not when it cannot.
 *
 * Two different reasons share one greyed-out look: the catalogue says the kind **arrives later**
 * (`available: false`), or the project's engine **cannot write it** (`supported: false`, from
 * `boundary.kinds`). Either way the kind stays listed, so the plan is visible and a user who wants
 * mass flow learns it is not in this solver yet instead of finding the kind missing.
 */
export interface KindState {
  enabled: boolean;
  /** Empty when enabled. */
  reason: string;
}

const NOT_IN_SOLVER = "Not in this solver yet";

export function kindState(kind: Pick<ConditionKind, "available" | "arrives" | "supported" | "unsupported_reason">): KindState {
  if (!kind.available) return { enabled: false, reason: kind.arrives ? `Arrives in ${kind.arrives}` : "Not available yet" };
  if (kind.supported === false) return { enabled: false, reason: kind.unsupported_reason || NOT_IN_SOLVER };
  return { enabled: true, reason: "" };
}

/** A boundary whose kind the engine cannot write: the words for its badge, else null. */
export function unsupportedNote(
  kinds: readonly (Pick<ConditionKind, "id" | "available" | "supported" | "unsupported_reason">)[],
  current: unknown,
): string | null {
  const found = kinds.find((kind) => kind.id === current);
  return found !== undefined && found.available && found.supported === false
    ? found.unsupported_reason || NOT_IN_SOLVER
    : null;
}
