/**
 * The one subscription that feeds `useOptimiseStatus`, wired the way `startExplore` wires the Explore store:
 * once for the page, following the client when the connection is replaced. A project change resets the status
 * (`onOptimiseEvent` on `project.opened` / `project.closed`). Imported by `useGuidedView`, so it is live whenever
 * the flow strip is.
 */
import type { StokesClient } from "../../api/client";
import { useStore } from "../../state/store";
import { onOptimiseEvent } from "./optimiseStatus";

let attached: StokesClient | null = null;
let off: (() => void) | null = null;

function attach(client: StokesClient | null): void {
  if (attached === client) return;
  off?.();
  attached = client;
  off = client !== null ? client.onEvent(onOptimiseEvent) : null;
}

let wired = false;

/** Follows the client for the life of the page; safe to call more than once. */
export function startOptimiseStatus(): void {
  if (wired) return;
  wired = true;
  attach(useStore.getState().client);
  useStore.subscribe((state, previous) => {
    if (state.client !== previous.client) attach(state.client);
  });
}

startOptimiseStatus();
