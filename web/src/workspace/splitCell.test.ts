/**
 * A panel in a split dock takes the pointer and has a card (the time-varying boundaries review): the
 * Conditions panel moved to the left dock drew as bare text over the viewport, and `elementFromPoint` at its
 * Fix button returned the viewport's canvas, so a real mouse click went to the model. `.slot` is
 * `pointer-events: none`; a panel with its own `.glass` turns it back on, a stage panel (no card of its own)
 * relies on these rules. The walk (`walk_time_bc.mjs`) presses that Fix with a real click.
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("../styles.css", import.meta.url), "utf8");

/** The declarations of the first rule whose selector is exactly `selector`. */
function rule(selector: string): string {
  const at = css.indexOf(`\n${selector} {`);
  if (at < 0) return "";
  const open = css.indexOf("{", at);
  return css.slice(open + 1, css.indexOf("}", open));
}

describe("a panel in a split cell", () => {
  it("takes the pointer, as the slot it sits in does not", () => {
    expect(rule(".slot-split")).not.toBe("");
    expect(rule(".slot-cell[data-ws-panel]")).toMatch(/pointer-events:\s*auto/);
  });

  it("gets a card when it has none of its own", () => {
    const card = rule(".slot-cell[data-ws-panel]:not(:has(> .glass))");
    expect(card).toMatch(/background:\s*var\(--panel\)/);
    expect(card).toMatch(/border:\s*1px solid var\(--panel-border\)/);
    expect(card).toMatch(/border-radius:\s*var\(--radius\)/);
  });
});
