import { describe, expect, it } from "vitest";
import { collectParts } from "../state/parts";

/**
 * A body left out of the simulation is FAINT in the viewport (T-222).
 *
 * The scene never reads the model, so the flag has to travel in the part's style - and the style
 * reaches the scene only when its KEY changes. **That was the bug in the first attempt**: the fade
 * was implemented, the tree showed the slashed ⚡, and the body stayed solid because `simulate` was in
 * neither `styleOf` nor `styleKey`.
 */
const shape = (simulate: unknown) => ({
  id: "n_1",
  name: "housing",
  path: "/geometry/parts/housing",
  type: "part",
  status: "ok",
  params: { visible: true, opacity: 1, simulate },
  children: [],
});

describe("a part's style", () => {
  it("carries the simulate flag out of the model", () => {
    expect(collectParts(shape(false) as never)[0]?.simulate).toBe(false);
    expect(collectParts(shape(true) as never)[0]?.simulate).toBe(true);
    // **A part with no flag is IN**, which is the model's own default: `simulate` defaults to true,
    // so an older project's body must not fade for a field it never had.
    expect(collectParts(shape(undefined) as never)[0]?.simulate).toBe(true);
  });
});
