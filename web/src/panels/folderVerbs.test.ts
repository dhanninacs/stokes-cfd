/**
 * The folder Add verbs (claude-win2's 0200 item 3, the tree guide's finding 3).
 *
 * **The finding**: materials, regions, scenes, studies and monitors look like containers but offered
 * only "Add group" (now removed) and a greyed Rename and Delete — so a user could not add the thing
 * the folder is named for.
 *
 * **The test that matters most is the one about commands existing.** I wrote `scene.create` into this
 * table before checking, and **there is no scene command at all** — `Fields`, `Slice` and `Residuals`
 * are virtual rows drawn from a run, not nodes. A button calling a command that does not exist is
 * worse than no button, and nothing else in the suite would have noticed.
 */
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { VERB_PATHS, emptyHint, folderVerb } from "./folderVerbs";

/** Every `@command("name")` the server registers, read from its source. */
function registeredCommands(): Set<string> {
  // `src/panels/` sits three levels below the repository root, where `stokes/` lives.
  const here = fileURLToPath(new URL("../../../", import.meta.url));
  const names = new Set<string>();
  for (const file of readdirSync(`${here}stokes/api/commands`)) {
    if (!file.endsWith(".py")) continue;
    const text = readFileSync(`${here}stokes/api/commands/${file}`, "utf8");
    for (const match of text.matchAll(/@command\(\s*"([a-z][a-z0-9_.]*)"|@command\(\s*\n\s*"([a-z][a-z0-9_.]*)"/g)) {
      const name = match[1] ?? match[2];
      if (name) names.add(name);
    }
  }
  return names;
}

describe("folderVerbs", () => {
  it("names a command the server really registers", () => {
    // **The check that would have caught my invented command.** Read from the source rather than by
    // importing the API, which would need the whole headless stack in a browser test.
    const known = registeredCommands();
    expect(known.size, "the scan found no commands, so this check is vacuous").toBeGreaterThan(50);
    const missing = VERB_PATHS.map((p) => folderVerb(p)?.command).filter((c) => c !== undefined && !known.has(c));
    expect(missing, "a folder's Add entry calls a command that does not exist").toEqual([]);
  });

  it("offers a verb for the folders the guide names, and none where nothing can be added", () => {
    expect(folderVerb("/physics/materials")?.command).toBe("material.create");
    expect(folderVerb("/regions")?.command).toBe("region.create");
    expect(folderVerb("/studies")?.command).toBe("study.create");
    // **No verb for scenes**: no command exists, and the rows are virtual. No verb for runs either -
    // a run is made by pressing Run, and a second way would skip the settings.
    expect(folderVerb("/scenes")).toBeNull();
    expect(folderVerb("/runs")).toBeNull();
    expect(folderVerb("/monitors")).toBeNull();
    expect(folderVerb("/solver")).toBeNull();
  });

  it("explains an empty folder in one line, and says what fills the ones with no Add", () => {
    // A folder with a verb says so; the two without one say what appears there instead, which is the
    // honest answer rather than a dead button.
    expect(emptyHint("/physics/materials", "Materials")).toContain("Add one");
    expect(emptyHint("/runs", "Runs")).toContain("press Run");
    expect(emptyHint("/scenes", "Scenes")).toContain("once a run has finished");
    // **`/geometry/parts` gained a verb** (review item 2), so it now says what to add rather than
    // having no hint - the assertion here used to pin the opposite, which is how a table change
    // should show up.
    expect(emptyHint("/geometry/parts", "parts")).toContain("Add one");
    // A folder that is neither a container nor filled by something else gets no hint at all.
    expect(emptyHint("/geometry", "geometry")).toBeNull();
  });
});
