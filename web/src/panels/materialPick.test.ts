/**
 * The Material ▾ pick (ADR-0022 §4.4, T-045).
 *
 * The rules under test are the ones that quietly misbehave: the fluid/solid sections, the recents
 * being a *convenience* that survives a refused `localStorage`, the two-command library path (so the
 * toast's "two undo steps" is true), and that the command parameters match the server's.
 */

import { describe, expect, it, vi } from "vitest";

import {
  RECENTS_KEY,
  RECENTS_LIMIT,
  addedMessage,
  libraryCommands,
  librarySearchParams,
  providerChips,
  readRecents,
  recordMatches,
  splitByKind,
  withRecent,
  writeRecents,
} from "./materialPick";
import type { LibraryRecord, MaterialChoice, MaterialProvider } from "../api/types";

const choice = (id: string, name: string, kind = "fluid"): MaterialChoice => ({ id, name, kind });

function record(over: Partial<LibraryRecord> = {}): LibraryRecord {
  return {
    provider: "builtin",
    fluid: "water_liquid",
    name: "Water (liquid)",
    formula: "H2O",
    cas: "7732-18-5",
    kind: "fluid",
    aliases: ["water", "H2O", "R718"],
    T_min: 275,
    T_max: 370,
    p_max: null,
    T_critical: null,
    p_critical: null,
    molar_mass: 0.018,
    properties: { density: true, viscosity: true, specific_heat: true, thermal_conductivity: true },
    composition: null,
    basis: null,
    reference: { source: "CoolProp 8.0.0 HEOS::Water" },
    ...over,
  };
}

const provider = (name: string, available: boolean): MaterialProvider => ({
  name,
  available,
  version: "1",
  reason: available ? "" : "not installed",
  fluids: 20,
  note: "",
});

describe("splitByKind", () => {
  it("puts fluids first and solids second", () => {
    const { fluids, solids } = splitByKind([choice("s", "Steel", "solid"), choice("w", "Water")]);
    expect(fluids.map((m) => m.name)).toEqual(["Water"]);
    expect(solids.map((m) => m.name)).toEqual(["Steel"]);
  });

  it("treats an unlabelled material as a fluid, which is what most set-ups need", () => {
    expect(splitByKind([choice("x", "Mystery", "")]).fluids).toHaveLength(1);
  });

  it("keeps the order it was given", () => {
    const { fluids } = splitByKind([choice("a", "Air"), choice("w", "Water")]);
    expect(fluids.map((m) => m.name)).toEqual(["Air", "Water"]);
  });
});

describe("readRecents", () => {
  it("reads the stored names", () => {
    const storage = { getItem: (k: string) => (k === RECENTS_KEY ? '["Water","Air"]' : null) };
    expect(readRecents(storage)).toEqual(["Water", "Air"]);
  });

  it("is empty without storage, without a key, and for a non-list value", () => {
    expect(readRecents(null)).toEqual([]);
    expect(readRecents({ getItem: () => null })).toEqual([]);
    expect(readRecents({ getItem: () => '"Water"' })).toEqual([]);
  });

  it("survives a storage that throws, because it is only a convenience (§4.4)", () => {
    const angry = {
      getItem: () => {
        throw new Error("SecurityError");
      },
    };
    expect(readRecents(angry)).toEqual([]);
  });

  it("ignores a hand-edited list: non-strings, blanks, duplicates and overflow", () => {
    const messy = { getItem: () => JSON.stringify(["Water", 7, "", "Water", "Air", "Steel", "Oil", "Glycol"]) };
    expect(readRecents(messy)).toEqual(["Water", "Air", "Steel", "Oil", "Glycol"]);
    expect(readRecents(messy)).toHaveLength(RECENTS_LIMIT);
  });
});

describe("withRecent", () => {
  it("puts the name at the front", () => {
    expect(withRecent(["Air"], "Water")).toEqual(["Water", "Air"]);
  });

  it("moves an existing name to the front rather than duplicating it", () => {
    expect(withRecent(["Air", "Water"], "Water")).toEqual(["Water", "Air"]);
  });

  it("caps the list at the limit", () => {
    expect(withRecent(["a", "b", "c", "d", "e"], "f")).toHaveLength(RECENTS_LIMIT);
  });

  it("ignores an empty name", () => {
    expect(withRecent(["Air"], "")).toEqual(["Air"]);
  });
});

describe("writeRecents", () => {
  it("writes the capped list as JSON", () => {
    const setItem = vi.fn();
    writeRecents({ setItem }, ["Water", "Air"]);
    expect(setItem).toHaveBeenCalledWith(RECENTS_KEY, JSON.stringify(["Water", "Air"]));
  });

  it("never throws when the store refuses, so the pick still works", () => {
    const angry = {
      setItem: () => {
        throw new Error("QuotaExceededError");
      },
    };
    expect(() => writeRecents(angry, ["Water"])).not.toThrow();
    expect(() => writeRecents(null, ["Water"])).not.toThrow();
  });
});

describe("libraryCommands", () => {
  it("issues the two commands of §4.4 against a pick", () => {
    const { add, assign } = libraryCommands(record(), [{ part: "n_1", faces: "all" }]);
    expect(add.method).toBe("material.add_from_library");
    expect(add.params).toEqual({ provider: "builtin", fluid: "water_liquid" });
    expect(assign.method).toBe("region.assign");
    expect(assign.params).not.toHaveProperty("material");
    expect(assign.params.target).toEqual([{ part: "n_1", faces: "all" }]);
  });

  it("takes the provider from the record, so the two commands cannot disagree", () => {
    const { add } = libraryCommands(record({ provider: "coolprop" }), []);
    expect(add.params.provider).toBe("coolprop");
  });
});

describe("addedMessage", () => {
  it("says what was added and to how many bodies, in §4.4's wording", () => {
    expect(addedMessage({ name: "water" }, "water", 3)).toBe("Added water · assigned to 3 bodies");
    expect(addedMessage({ name: "Water (liquid)" }, "water_liquid", 1)).toBe(
      "Added Water (liquid) as water_liquid · assigned to 1 body",
    );
  });
});

describe("providerChips", () => {
  it("offers only the available providers", () => {
    expect(providerChips([provider("builtin", true), provider("refprop", false)]).map((p) => p.name)).toEqual([
      "builtin",
    ]);
  });

  it("keeps the server's order", () => {
    const chips = providerChips([provider("builtin", true), provider("coolprop", true)]);
    expect(chips.map((p) => p.name)).toEqual(["builtin", "coolprop"]);
  });
});

describe("librarySearchParams", () => {
  it("uses the API's providers array and an empty string for an unfiltered search", () => {
    expect(librarySearchParams("", null)).toEqual({ query: "" });
  });

  it("sends a selected provider under the plural providers parameter", () => {
    expect(librarySearchParams("water", "coolprop")).toEqual({ query: "water", providers: ["coolprop"] });
  });
});

describe("recordMatches", () => {
  it("matches the name, the fluid id, the formula and an alias, case-insensitively", () => {
    expect(recordMatches(record(), "water")).toBe(true);
    expect(recordMatches(record(), "WATER_LIQUID")).toBe(true);
    expect(recordMatches(record(), "h2o")).toBe(true);
    expect(recordMatches(record(), "r718")).toBe(true);
  });

  it("does not match an unrelated query", () => {
    expect(recordMatches(record(), "steel")).toBe(false);
  });

  it("matches everything for an empty or blank query", () => {
    expect(recordMatches(record(), "")).toBe(true);
    expect(recordMatches(record(), "   ")).toBe(true);
  });
});
