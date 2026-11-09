import { describe, expect, it } from "vitest";
import { rankCommands } from "./fuzzy";


describe("a window item outranks a command whose identifier merely shares a word", () => {
  /**
   * **G-301's H-3, in one test.** Typing "Open Results" must put the entry CALLED "Open Results" first,
   * not `project.sample_open` - whose identifier contains "open" and whose description says "Open a
   * sample as a working copy". Measured before the fix: the row ran was `project.sample_open`, opening a
   * form and leaving its scrim over the whole window.
   *
   * The command names here are the real ones, and the labels are what `commandLabel` produces.
   */
  const items = [
    { name: "project.sample_open", label: "project sample open", description: "Open a sample as a working copy" },
    { name: "Open Results", label: "Open Results", description: "Show it on the right" },
    { name: "Open Plots", label: "Open Plots", description: "Show it on the bottom" },
    { name: "project.open", label: "project open", description: "Open a project from its folder" },
    { name: "geometry.import", label: "geometry import", description: "Import a geometry file" },
  ];
  const rank = (query: string) => rankCommands(items, query, [], (i) => i.name).map((r) => r.command.name);

  it("puts 'Open Results' first for the query 'Open Results'", () => {
    expect(rank("Open Results")[0]).toBe("Open Results");
  });

  it("ranks a LABEL match above a KEY match when the key matches only a word", () => {
    // **The discriminating case**, and the reason the first version of this test proved nothing: an item
    // called "Open Results" matches on its `name` too, so every weighting put it first. Here the command's
    // identifier contains the query word and its label does not, so ONLY the label weight can save the
    // window item. Measured on the real palette: `project.sample_open`'s name and description both say
    // "open", which is how it beat the entry actually called "Open Results".
    const mixed = [
      { name: "project.open_sample", label: "project open sample", description: "Open a sample as a working copy" },
      { name: "Open Results", label: "Open Results", description: "Show it on the right" },
    ];
    const got = rankCommands(mixed, "Open Results", [], (i) => i.name).map((r) => r.command.name);
    expect(got[0]).toBe("Open Results");
  });

  it("is not won by the description alone", () => {
    // A command whose DESCRIPTION contains the query must not outrank an item whose label IS the query.
    const only = [
      { name: "region.set", label: "region set", description: "Open Results is not this" },
      { name: "Open Results", label: "Open Results", description: "Show it on the right" },
    ];
    expect(rankCommands(only, "Open Results", [], (i) => i.name)[0]?.command.name).toBe("Open Results");
  });

  it("puts 'Open Plots' first for the query 'Open Plots'", () => {
    expect(rank("Open Plots")[0]).toBe("Open Plots");
  });

  it("still finds a command by its identifier's words", () => {
    // The label must ADD reach, not reduce it: "sample open" is how a user would say the identifier.
    const first = rank("sample open")[0];
    expect(first).toBe("project.sample_open");
  });

  it("puts every stage name first for its own name", () => {
    for (const stage of ["Open Geometry", "Open Mesh", "Open Physics", "Open Results"]) {
      const row = { name: stage, label: stage, description: "Show it somewhere" };
      const withRow = [...items, row];
      const got = rankCommands(withRow, stage, [], (i) => i.name)[0]?.command.name;
      expect(got).toBe(stage);
    }
  });
});
