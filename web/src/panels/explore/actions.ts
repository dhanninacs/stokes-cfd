/**
 * The Explore actions on the action bus (app/actions.ts): `study.new`, `study.generate`, `study.update`,
 * `study.addPoint` and `study.export`. Their handlers are installed when this module loads, which the
 * registry does at start, so the palette, the ribbon, a rebound chord and the toolbar buttons all reach
 * the same code whether or not an Explore panel is on screen. Their ids and titles are in `ACTIONS`.
 */
import { registerAction } from "../../app/actions";
import { exportTable, generateTable, updateTable } from "./commands";
import { openAddPoint, openNewStudy } from "./forms";

/** Install the handlers; returns the call that removes them. */
export function registerExploreActions(): () => void {
  const removers = [
    registerAction("study.new", openNewStudy),
    registerAction("study.generate", () => void generateTable()),
    registerAction("study.update", () => void updateTable("all")),
    registerAction("study.addPoint", openAddPoint),
    registerAction("study.export", () => void exportTable()),
  ];
  return () => removers.forEach((remove) => remove());
}

registerExploreActions();
