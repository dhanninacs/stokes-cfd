/** State rules for the Geometry check page, kept pure so async transitions stay easy to verify. */
export type CheckStatus = "disconnected" | "loading" | "ready" | "error";

/** A green clear state is valid only after the current request has succeeded. */
export function canShowClear(status: CheckStatus, findingCount: number): boolean {
  return status === "ready" && findingCount === 0;
}

/** Rechecks require a live connection and an idle page with no repair in progress. */
export function canStartCheck(connected: boolean, status: CheckStatus, fixing: boolean): boolean {
  return connected && status !== "loading" && !fixing;
}
