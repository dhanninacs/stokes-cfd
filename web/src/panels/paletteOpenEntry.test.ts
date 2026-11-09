import { describe, expect, it } from "vitest";
import { rankCommands } from "./fuzzy";

/**
 * **The same words must find the same row, every time** (G-301's H-3, Claude's 0630 point 2).
 *
 * The defect: the "Open <panel>" entry was offered only while the panel was hidden or folded away. Measured
 * in a real browser, the same keystrokes gave:
 *
 *     use 1 -> Open Results        (runs it, panel opens, no scrim)
 *     use 2 -> project.sample_open (opens a FORM, and its scrim blocked the whole window)
 *
 * The entry had simply vanished, because the first use placed the panel and `visibleNow()` became true.
 * A user typing "Open Results" a second time got a list with nothing called that.
 *
 * The rule now: the entry is ALWAYS offered, reading "<title> (open)" once it is on screen, so the same
 * query finds the same row and choosing it is harmless. This pins the ordering contract; the browser
 * measurement lives in `docs/audit/G-301-post-processing.md`.
 */
describe("the Open <panel> entry", () => {
  const items = [
    { name: "project.sample_open", label: "project sample open", description: "Open a sample as a working copy" },
    { name: "operation.delete", label: "operation delete", description: "Delete an operation" },
  ];
  const rank = (rows: { name: string; label: string; description: string }[], q: string) =>
    rankCommands(rows, q, [], (i) => i.name).map((r) => r.command.name);

  it("is found for the panel's own words whether it reads 'Open X' or 'X (open)'", () => {
    // Both wordings are what a user might have seen last time, so both must keep pointing at the entry.
    const hidden = [...items, { name: "Open Results", label: "Open Results", description: "Show it on the right" }];
    const open = [...items, { name: "Results (open)", label: "Results (open)", description: "Bring it to the front" }];
    expect(rank(hidden, "Open Results")[0]).toBe("Open Results");
    expect(rank(open, "Results open")[0]).toBe("Results (open)");
  });

  it("still outranks a command whose identifier shares the word 'open'", () => {
    // The item that took the first row when the entry vanished. It must lose in BOTH wordings.
    const open = [...items, { name: "Results (open)", label: "Results (open)", description: "Bring it to the front" }];
    expect(rank(open, "Results")[0]).toBe("Results (open)");
    expect(rank(items, "Results")).not.toContain("Results (open)");
  });
});
