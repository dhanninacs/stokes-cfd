/**
 * "Assign boundary": give a geometry patch a boundary node under /regions/fluid/boundaries.
 * Shared by the properties panel and any toolbar that offers the same action.
 */

import type { NodeSummary } from "../api/types";
import { findByPath, findNode, useStore } from "../state/store";

const REGION = "/regions";
const FLUID = "/regions/fluid";
export const BOUNDARIES_PATH = "/regions/fluid/boundaries";

export function boundaryPath(patch: string): string {
  return `${BOUNDARIES_PATH}/${patch}`;
}

async function ensureGroup(parentPath: string, name: string): Promise<void> {
  const { execute, refresh } = useStore.getState();
  if (findByPath(useStore.getState().tree, `${parentPath}/${name}`)) return;
  await execute("node.add", { parent: parentPath, type: "group", name });
  await refresh();
}

/**
 * Create `/regions/fluid/boundaries/<patch>` (a group) when it is missing, then select it. The fluid
 * region and its boundaries group are created first if the project lacks them. Resolves to the node,
 * or null when the server refused (execute has already logged it and shown a toast).
 */
export async function assignBoundary(patch: string): Promise<NodeSummary | null> {
  const store = useStore.getState();
  try {
    await store.refresh(); // decide on the server's state, not on the last event's snapshot
    let node = findByPath(useStore.getState().tree, boundaryPath(patch));
    if (!node) {
      await ensureGroup(REGION, "fluid");
      await ensureGroup(FLUID, "boundaries");
      const created = (await store.execute("node.add", { parent: BOUNDARIES_PATH, type: "group", name: patch })) as { id: string };
      await store.refresh();
      node = findNode(useStore.getState().tree, created.id);
      store.pushToast("ok", `Boundary ${patch} created`);
    }
    if (node) store.select(node.id);
    return node;
  } catch {
    return null;
  }
}
