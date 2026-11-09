import { describe, expect, it } from "vitest";
import type { PropertySchema } from "../api/types";
import { groupSettingsFields, type SettingsSectionSpec } from "./settingsPresentation";

const properties: Record<string, PropertySchema> = {
  first: { type: "string" },
  hidden: { type: "string", "x-hidden": true },
  excluded: { type: "string" },
  last: { type: "number" },
  unassigned: { type: "boolean" },
};

describe("groupSettingsFields", () => {
  it("groups eligible declared fields once and moves leftovers to collapsed Other settings", () => {
    const sections: SettingsSectionSpec[] = [
      { id: "solver", title: "Solver", fields: ["last", "first", "first", "hidden", "unknown"] },
      { id: "empty", title: "Empty", fields: ["excluded"] },
    ];
    const result = groupSettingsFields(properties, sections, (name) => name !== "excluded");

    expect(result).toEqual([
      { id: "solver", title: "Solver", description: undefined, defaultOpen: true, fields: ["last", "first"] },
      { id: "other-settings", title: "Other settings", defaultOpen: false, fields: ["unassigned"] },
    ]);
    const names = result.flatMap((group) => group.fields);
    expect(names).toHaveLength(new Set(names).size);
    expect(names.sort()).toEqual(["first", "last", "unassigned"]);
  });

  it("honors explicitly collapsed declared sections", () => {
    expect(groupSettingsFields({ first: { type: "string" } }, [{ id: "advanced", title: "Advanced", fields: ["first"], defaultOpen: false }])[0].defaultOpen).toBe(false);
  });
});
