/**
 * Whether the tree draws every row or only the ones a first run needs.
 *
 * **Basic is the default**: rows flagged `advanced` in `outlineModel.ts` (Capping, Mesher, the empty
 * Operations and Parameters, Fidelity, the Mesh task Boundaries, the Solve rows that open the same page)
 * stay out of the tree until "Show all rows" is on. It is a view choice and not a project fact, so it lives
 * in `localStorage` under `stokes.tree.showAll` (the workspace document refuses new keys), like the guided
 * mode flag and the Advanced disclosures. Every read and write is wrapped, so a browser that blocks storage
 * just shows the Basic tree.
 */
import { create } from "zustand";

export const SHOW_ALL_KEY = "stokes.tree.showAll";

/** True only when the user turned "Show all rows" on. */
export function readShowAll(storage: Pick<Storage, "getItem"> | null): boolean {
  try {
    return storage?.getItem(SHOW_ALL_KEY) === "on";
  } catch {
    return false;
  }
}

/** Keep the choice; a refusal from the browser is ignored. */
export function writeShowAll(storage: Pick<Storage, "setItem" | "removeItem"> | null, on: boolean): void {
  try {
    if (on) storage?.setItem(SHOW_ALL_KEY, "on");
    else storage?.removeItem(SHOW_ALL_KEY);
  } catch {
    /* storage refused: the choice lasts until the page is closed */
  }
}

function storage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

interface RowMode {
  showAll: boolean;
}

export const useRowMode = create<RowMode>(() => ({ showAll: readShowAll(storage()) }));

/** Turn "Show all rows" on or off, and keep the choice. */
export function setShowAllRows(on: boolean): void {
  writeShowAll(storage(), on);
  useRowMode.setState({ showAll: on });
}
