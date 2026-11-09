/**
 * Bringing a stage's panel forward from the tree (T-219 B): the Mesh, Solve, Results and Conditions panels are
 * tabs beside Properties in the right slot, and a stage chip brings its own forward. A row of the tree that
 * belongs to one (the mesh settings, the solver settings, a run, a field) does the same without changing the
 * stage, so choosing a row never refolds the tree under the pointer. `section` is the `data-ws` of a block of
 * the panel to scroll into view once the tab is in front (the Results panel's Residuals and Monitors).
 */
import { useStore } from "../state/store";
import { useWorkspace } from "../workspace/store";
import { placeOf } from "../workspace/geometry";
import type { OpenTarget } from "./stageTreeModel";

/** How long the panel needs to mount after its tab comes forward, before the section is looked for. */
const MOUNT_MS = 60;

export function openPanel(target: OpenTarget): void {
  // A row whose page is a card (Capping): select the operation node, or open the operation's card.
  if (target.select !== undefined) void useStore.getState().select(target.select);
  else if (target.operation !== undefined) useStore.getState().openOperation(target.operation);
  if (target.panel === null) return;
  const workspace = useWorkspace.getState();
  const place = placeOf(workspace.document.layout, target.panel);
  if (place === "left" || place === "right" || place === "bottom") workspace.setActiveTab(place, target.panel);
  if (target.section === undefined) return;
  const selector = `[data-ws="${target.section}"]`;
  const reveal = () => document.querySelector(selector)?.scrollIntoView({ block: "nearest" });
  reveal();
  window.setTimeout(reveal, MOUNT_MS);
}
