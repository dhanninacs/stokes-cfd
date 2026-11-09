import { describe, expect, it } from "vitest";
import { RESIDUALS_PLACEHOLDER, RESIDUALS_UNAVAILABLE, residualsView } from "../state/residualSeries";

/**
 * The synthetic residual chart's self-description (live-review finding 2).
 *
 * **The words are the whole safeguard.** With no residual history the tab draws a placeholder, and that
 * chart's legend reads `Ux Uy p T` - the same names real data carries - beside a status bar that says
 * "converged". **Measured**: at 11 px muted grey in a corner, 2 000 px from the legend, it read as that
 * run's evidence.
 */
describe("RESIDUALS_PLACEHOLDER", () => {
  it("names the ABSENCE, not the chart", () => {
    // "demo traces" said what the chart was; this says what is missing, which is what a reader needs.
    expect(RESIDUALS_PLACEHOLDER).toContain("no residual history");
  });

  it("says it is a placeholder", () => {
    expect(RESIDUALS_PLACEHOLDER).toMatch(/^placeholder:/);
  });

  it("does not pretend to be real data in any wording", () => {
    // A regression here would be a reader mistaking a synthetic curve for a solved run.
    expect(RESIDUALS_PLACEHOLDER.toLowerCase()).not.toMatch(/converged|final residual/);
  });
});

describe("residualsView - the four-row decision", () => {
  const fin = [{ name: "Ux", value: 2.5e-6 }];

  it("draws CURVES when the run has history", () => {
    expect(residualsView({ hasRun: true, iterations: 64, final: fin })).toEqual({ kind: "curves" });
  });

  it("shows the EMPTY STATE for a real run with no history - never synthetic curves", () => {
    // **The decision** (Codex 222732 item 1). Measured before this: the demo chart's legend read
    // `Ux Uy p T` beside a "converged" status bar and was taken for that run's history.
    const view = residualsView({ hasRun: true, iterations: 0, final: fin });
    expect(view.kind).toBe("unavailable");
    if (view.kind === "unavailable") expect(view.final).toEqual(fin);
  });

  it("keeps the FINAL RESIDUALS for the empty state, separately from the curve", () => {
    // The decision asks for them "separately when present": a run that reported final values has
    // evidence even though it has no history to plot.
    const view = residualsView({ hasRun: true, iterations: 0, final: fin });
    if (view.kind === "unavailable") expect(view.final).toHaveLength(1);
  });

  it("still gives the empty state when there are no finals either", () => {
    expect(residualsView({ hasRun: true, iterations: 0, final: [] })).toEqual({ kind: "unavailable", final: [] });
  });

  it("keeps the demo ONLY when there is no run at all - the example context, not a fallback", () => {
    expect(residualsView({ hasRun: false, iterations: 0, final: [] })).toEqual({ kind: "demo" });
  });

  it("never reaches the demo because a real run's history is missing", () => {
    // The regression this test exists for: `DemoResiduals` was the DEFAULT fallback.
    for (const iterations of [0, 1, 64]) {
      expect(residualsView({ hasRun: true, iterations, final: [] }).kind).not.toBe("demo");
    }
  });
});

describe("the empty state's words", () => {
  it("says exactly what the decision specifies", () => {
    expect(RESIDUALS_UNAVAILABLE).toBe("Residual history is not available for this run");
  });
});
