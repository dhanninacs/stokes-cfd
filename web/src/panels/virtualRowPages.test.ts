/**
 * A virtual row's page, which the Details pane reads (claude-win2's pictures, the gui-v3 warning 2).
 *
 * **The gap this closes was live**: the pane looked its selection up with `findNode`, which walks the
 * raw tree, so a virtual row (a run, a field, a slice, a placeholder) returned null and the pane fell
 * back to the stage checklist. Measured: selecting a run showed "NEXT STEP" instead of the run.
 *
 * A virtual row already knows its panel - `openTargetOf` is the same answer a double-click gives - so
 * the pane asks the row. These tests pin that every row kind the tree can build maps to a real panel,
 * because a row with no target is a row that would silently show the checklist.
 */
import { describe, expect, it } from "vitest";
import type { NodeSummary } from "../api/types";
import { openTargetOf, type RowKind } from "./stageTreeModel";

/** A row as the tree builds it: a node whose `meta.row` names what kind of row it is. */
function row(kind: RowKind): NodeSummary {
  return {
    id: `virtual:${kind}`,
    type: kind,
    name: kind,
    path: `/x/${kind}`,
    status: "ok",
    params: {},
    meta: { row: kind, placeholder: false, subtitle: "" },
  } as unknown as NodeSummary;
}

describe("a virtual row's page", () => {
  it("maps the row kinds the tree builds to the panel they open", () => {
    // Every kind, so a new one added without a target fails here rather than showing a checklist.
    expect(openTargetOf(row("run"))?.panel).toBe("solve");
    expect(openTargetOf(row("mesh-settings"))?.panel).toBe("mesh");
    expect(openTargetOf(row("mesh-result"))?.panel).toBe("mesh");
    expect(openTargetOf(row("solver-settings"))?.panel).toBe("solve");
    expect(openTargetOf(row("flow-model"))?.panel).toBe("solve");
    expect(openTargetOf(row("fields"))?.panel).toBe("results");
    expect(openTargetOf(row("field"))?.panel).toBe("results");
    expect(openTargetOf(row("slice"))?.panel).toBe("results");
    expect(openTargetOf(row("residuals"))?.panel).toBe("results");
    // **`monitors` opens Results**, not a folder: the rows are virtual and the folder is gone
    // (2026-10-18), so this is where a user reads them.
    expect(openTargetOf(row("monitors"))?.panel).toBe("results");
    // **The mesh tasks** (brief M-502): Boundaries has its own page; Surface, Layers, Quality and
    // Fidelity show the Mesh panel's existing sections until their packages land — the brief's own
    // fallback for them, recorded at `openTargetOf`.
    expect(openTargetOf(row("mesh-boundaries"))?.panel).toBe("mesh-boundaries");
    expect(openTargetOf(row("mesh-surface"))?.panel).toBe("mesh");
    expect(openTargetOf(row("mesh-layers"))?.panel).toBe("mesh");
    expect(openTargetOf(row("mesh-quality"))?.panel).toBe("mesh");
    expect(openTargetOf(row("mesh-fidelity"))?.panel).toBe("mesh");
    // ADR-0026: Capping under Mesh, Surfaces under Results.
    // Capping's page is the cap's CARD, not a panel: the Close openings card with no cap yet, the
    // first cap node's own page when there is one.
    expect(openTargetOf(row("mesh-capping"))).toEqual({ panel: null, operation: "cap" });
    expect(openTargetOf({ ...row("mesh-capping"), meta: { row: "mesh-capping", placeholder: false, subtitle: "", ref: "c1" } } as unknown as NodeSummary)).toEqual({ panel: null, select: "c1" });
    expect(openTargetOf(row("surfaces"))?.panel).toBe("results");
  });

  it("gives a row that carries no kind no target", () => {
    // A real node is not a virtual row, and the pane must not read a page out of nothing.
    const plain = { id: "n_1", type: "part", name: "box", params: {} } as unknown as NodeSummary;
    expect(openTargetOf(plain)).toBeNull();
  });

  it("names a panel for every kind, so none falls through", () => {
    // **The guard that cannot go vacuous**: list the kinds explicitly and assert each resolves. A
    // kind added to `RowKind` without a case here fails this rather than showing the checklist.
    const kinds: RowKind[] = [
      "mesh-settings", "mesh-result", "mesh-boundaries", "mesh-surface", "mesh-layers",
      "mesh-quality", "mesh-fidelity", "solver-settings", "flow-model", "run",
      "fields", "field", "slice", "residuals", "monitors", "mesh-capping", "surfaces",
    ];
    const missing = kinds.filter((k) => openTargetOf(row(k)) === null);
    expect(missing, "a row kind opens nothing, so the pane would show the checklist").toEqual([]);
  });
});
