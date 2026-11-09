import { describe, expect, it } from "vitest";
import { freeRect, MIN_SIDE, type Obstruction } from "./freeRect";

const win = { width: 1600, height: 1000 };
const base: Obstruction = { left: 0, right: 0, top: 48, bottom: 40, window: win };

describe("the free rectangle", () => {
  it("is the whole window minus the furniture when no dock is open", () => {
    expect(freeRect(base)).toEqual({ x: 0, y: 48, width: 1600, height: 912 });
  });

  it("moves right by the tree's width when only the tree is open", () => {
    const r = freeRect({ ...base, left: 300 });
    expect(r).toEqual({ x: 300, y: 48, width: 1300, height: 912 });
  });

  it("narrows by both docks when both are open", () => {
    // **The case the owner sees first**: the model sat under the tree and the pane.
    expect(freeRect({ ...base, left: 300, right: 320 })).toEqual({
      x: 300,
      y: 48,
      width: 980,
      height: 912,
    });
  });

  it("is unchanged by a dock that is collapsed", () => {
    // **A collapsed panel contributes 0**, which is the dock's job to report - this function trusts
    // the number rather than knowing about collapse.
    expect(freeRect({ ...base, left: 0, right: 0 })).toEqual(freeRect(base));
  });

  it("keeps a floor rather than going empty when the docks meet", () => {
    // **A zero-width rectangle renders nothing at all**, so the floor turns a black screen into a
    // small, wrong one - the failure a user can still work around.
    const r = freeRect({ ...base, left: 900, right: 800 });
    expect(r.width).toBe(MIN_SIDE);
    expect(r.height).toBe(912);
  });

  it("counts the narrow-window overlay as nothing on the right", () => {
    // Below the overlay width the Details pane floats over the viewport rather than narrowing it, so
    // the dock reports 0 and the model keeps the full width behind it - which is what "overlay" means
    // and what the owner's narrow-window rule already does.
    expect(freeRect({ ...base, left: 300, right: 0 }).width).toBe(1300);
  });
});
