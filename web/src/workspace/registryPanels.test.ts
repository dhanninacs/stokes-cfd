/**
 * Every panel the Details pane can name must be one the workspace really registers.
 *
 * **Checked by reading the SOURCE rather than by importing the registry, and that is deliberate.**
 * Importing it needs `window`, then `document`, then `devicePixelRatio`, then more - measured, one
 * stub at a time - because the registry imports every panel component, and those import the state
 * store. Chasing globals would make this test a description of what the app happens to touch at load
 * rather than a check on the map.
 *
 * Reading the two files that register panels is the same check at a fraction of the cost: `PANELS` is
 * the built-in table in `registry.tsx`, and `App.tsx` calls `registerPanel` for the four stage panels.
 *
 * **Why this matters more than it looks**: a map naming an unregistered panel renders an EMPTY pane,
 * and an empty pane reads as "this node has no settings" rather than as a mistake.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { NAMED_PANELS } from "./detailsPage";

/** Every `id: "..."` a panel definition declares, from both files that declare them. */
function registeredIds(): Set<string> {
  const here = fileURLToPath(new URL(".", import.meta.url));
  const ids = new Set<string>();
  for (const file of ["registry.tsx", "../App.tsx"]) {
    const text = readFileSync(`${here}${file}`, "utf8");
    // A panel id appears either as `id: "x"` in a PanelDef or as `id: "x"` in a `registerPanel` call;
    // both spellings are the same key, which is what the map has to match.
    for (const match of text.matchAll(/\bid:\s*"([a-z][a-z0-9-]*)"/g)) ids.add(match[1]);
  }
  return ids;
}

describe("the Details pane's panel ids", () => {
  it("names only panels something really registers", () => {
    const known = registeredIds();
    const unknown = NAMED_PANELS.filter((id) => !known.has(id));
    expect(unknown, "the map names a panel nothing registers").toEqual([]);
  });

  it("finds the panels, so the check above is not vacuous", () => {
    // **A guard that cannot fail is not a guard.** If the scan read nothing, the check above would
    // pass for the wrong reason; this pins that both files were really read.
    const known = registeredIds();
    for (const id of ["properties", "conditions", "mesh", "solve", "results"]) {
      expect(known, `the scan did not find ${id}`).toContain(id);
    }
  });
});
