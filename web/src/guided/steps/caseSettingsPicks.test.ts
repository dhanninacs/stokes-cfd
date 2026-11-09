import { describe, expect, it } from "vitest";
import type { SettingsRow } from "../../panels/caseCardModel";
import { CASE_PICKS, matchesPath, pickRows, rowLabel } from "./caseSettingsPicks";

function row(path: string[], over: Partial<SettingsRow> = {}): SettingsRow {
  return { kind: "entry", path, text: "1", truncated: false, overridden: false, ...over } as SettingsRow;
}

describe("the Guided cards' picks of an imported case's settings", () => {
  it("matches a path element by element, * standing for one element", () => {
    expect(matchesPath(["boundaryField", "inlet", "value"], ["boundaryField", "*", "value"])).toBe(true);
    expect(matchesPath(["boundaryField", "inlet", "type"], ["boundaryField", "*", "value"])).toBe(false);
    expect(matchesPath(["RAS", "model"], ["RAS", "model", "x"])).toBe(false);
  });

  it("keeps the file's order and skips blocks and truncated values", () => {
    const rows = [
      row(["boundaryField", "inlet", "value"]),
      row(["boundaryField", "inlet", "type"]),
      row(["boundaryField", "outlet", "value"], { truncated: true }),
      row(["boundaryField", "wall"], { kind: "block" } as Partial<SettingsRow>),
      row(["internalField"]),
    ];
    const picked = pickRows(rows, CASE_PICKS.boundaries[0]);
    expect(picked.map((r) => r.path.join("/"))).toEqual(["boundaryField/inlet/value", "boundaryField/inlet/type"]);
  });

  it("labels a row without the boundaryField wrapper", () => {
    expect(rowLabel(row(["boundaryField", "inlet", "value"]))).toBe("inlet · value");
    expect(rowLabel(row(["RAS", "model"]))).toBe("RAS · model");
  });

  it("asks for the end time and the flow model", () => {
    expect(CASE_PICKS.run[0].paths).toContainEqual(["endTime"]);
    expect(CASE_PICKS.fluid[0].paths).toContainEqual(["RAS", "model"]);
  });
});
