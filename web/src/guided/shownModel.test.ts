import { describe, expect, it } from "vitest";
import type { MeshPatchEntry } from "../state/meshPatches";
import { shownItems } from "./shownModel";

const patch = (id: string, name: string, visible = true): MeshPatchEntry => ({ id, resultId: "r", name, visible, color: "#9aa3ad", opacity: 1, faces: 1 });

describe("what the results view shows", () => {
  it("lists the boundaries, then the user's slices, each with whether it is drawn", () => {
    const items = shownItems([patch("n_1", "inlet"), patch("n_2", "walls"), patch("n_3", "outlet", false)], ["n_2"], [{ id: "s1", label: "Z = 5.1 mm", visible: true }], true, true);
    expect(items.map((i) => `${i.kind}:${i.label}:${i.shown}`)).toEqual(["patch:inlet:true", "patch:walls:false", "patch:outlet:false", "slice:Z = 5.1 mm:true"]);
  });

  it("offers the run's mid-plane only when there is no slice of the user's", () => {
    expect(shownItems([], [], [], true, false)).toEqual([{ kind: "mid", id: "mid", label: "Mid-plane", shown: false }]);
    expect(shownItems([], [], [], false, false)).toEqual([]);
  });
});
