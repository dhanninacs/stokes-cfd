/**
 * The Prepare menu's entries as data (T-029): the four actions, the pick rules of every entry, and
 * the two rules T-050 and the not-built types add. No React (vitest runs in Node).
 */
import { describe, expect, it } from "vitest";
import {
  EXTERNAL_FLOW_ACTION,
  INTERNAL_FLOW_ACTION,
  NOT_BUILT,
  PICK_BODY,
  PICK_FACES,
  PICK_TWO_BODIES,
  prepareEntries,
  type PrepareEntry,
} from "./prepareEntries";

// The four T-029 actions plus the two flow helpers T-050 adds (ADR-0022 §10.2).
const ACTIONS = [
  "prepare.enclosure",
  "prepare.extractFluid",
  "prepare.boolean",
  "prepare.repair",
  EXTERNAL_FLOW_ACTION,
  INTERNAL_FLOW_ACTION,
];

function find(entries: readonly PrepareEntry[], id: string): PrepareEntry {
  const found = entries.find((entry) => entry.id === id);
  if (!found) throw new Error(`no entry ${id}`);
  return found;
}

describe("the Prepare entries", () => {
  it("leads with the two flow helpers, then the plain operations, then the submenus", () => {
    // ADR-0022 §10.2 puts External flow… and Internal flow… above Enclosure…; the manual routes stay.
    expect(prepareEntries({ bodies: 0, faces: 0 }).map((entry) => entry.label)).toEqual([
      "External flow…",
      "Internal flow…",
      "Enclosure…",
      "Extract fluid…",
      "Boolean",
      "Repair",
    ]);
  });

  it("keeps every entry on one of the six actions, and labels and explains each one", () => {
    const entries = prepareEntries({ bodies: 2, faces: 3 });
    const walk = (list: readonly PrepareEntry[]): void => {
      for (const entry of list) {
        expect(ACTIONS).toContain(entry.action);
        expect(entry.label).not.toBe("");
        expect(entry.enabled || (entry.why ?? "") !== "").toBe(true);
        if (entry.entries) walk(entry.entries);
      }
    };
    walk(entries);
  });

  it("enables the two flow helpers on a body, and refuses them for the same reason Enclosure is", () => {
    const none = prepareEntries({ bodies: 0, faces: 0 });
    expect(find(none, "external-flow").enabled).toBe(false);
    expect(find(none, "external-flow").why).toBe(PICK_BODY);
    expect(find(none, "internal-flow").enabled).toBe(false);
    const one = prepareEntries({ bodies: 1, faces: 0 });
    expect(find(one, "external-flow").enabled).toBe(true);
    expect(find(one, "internal-flow").enabled).toBe(true);
  });

  it("refuses a flow helper whose operation is not built, saying so rather than the pick rule", () => {
    const entries = prepareEntries({ bodies: 1, faces: 0, built: { enclosure: false, extract_fluid: false } });
    expect(find(entries, "external-flow").why).toBe(NOT_BUILT);
    expect(find(entries, "internal-flow").why).toBe(NOT_BUILT);
  });

  it("needs a body for the enclosing entries, two for Boolean, and faces for Remove faces", () => {
    const none = prepareEntries({ bodies: 0, faces: 0 });
    expect(none.every((entry) => !entry.enabled)).toBe(true);
    expect(find(none, "enclosure").why).toBe(PICK_BODY);

    const one = prepareEntries({ bodies: 1, faces: 0 });
    expect(find(one, "enclosure").enabled).toBe(true);
    expect(find(one, "extract-fluid").enabled).toBe(true);
    expect(find(one, "boolean").enabled).toBe(false);
    expect(find(one, "boolean").why).toBe(PICK_TWO_BODIES);
    expect(find(find(one, "repair").entries ?? [], "defeature").enabled).toBe(false);
    expect(find(find(one, "repair").entries ?? [], "defeature").why).toBe(PICK_FACES);
    expect(find(one, "repair").enabled).toBe(true);

    const two = prepareEntries({ bodies: 2, faces: 0 });
    expect(find(two, "boolean").enabled).toBe(true);
    expect((find(two, "boolean").entries ?? []).every((entry) => entry.enabled)).toBe(true);
    expect(find(find(two, "boolean").entries ?? [], "cut").params).toEqual({ op: "cut" });
  });

  it("offers Remove faces for a face pick alone", () => {
    const faces = prepareEntries({ bodies: 0, faces: 4 });
    expect(find(faces, "enclosure").enabled).toBe(false);
    expect(find(faces, "repair").enabled).toBe(true);
    expect(find(find(faces, "repair").entries ?? [], "defeature").enabled).toBe(true);
  });

  it("disables an entry whose operation type is not built yet, with the reason", () => {
    const entries = prepareEntries({ bodies: 2, faces: 3, built: { cap: false, defeature: false } });
    const repair = find(entries, "repair").entries ?? [];
    expect(find(repair, "cap")).toMatchObject({ enabled: false, why: NOT_BUILT });
    expect(find(repair, "defeature")).toMatchObject({ enabled: false, why: NOT_BUILT });
    expect(find(repair, "heal").enabled).toBe(true);
    expect(find(entries, "enclosure").enabled).toBe(true);
  });
});
