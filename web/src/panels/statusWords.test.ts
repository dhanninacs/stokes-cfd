/**
 * What a dot and a count badge MEAN (claude-win2's 0200 item 6, the guide's finding 7).
 *
 * **The guide's words**: *"Badges and dots explain nothing. Dots show a native tooltip with the raw
 * word 'ok', 'new'; every folder is a green 'ok'."* The dot's tooltip used to be the internal status
 * string, so a user learned nothing and - because every folder is green - learned to ignore the dot
 * entirely, which is how a warning stops working.
 */
import { describe, expect, it } from "vitest";
import { countWords, statusWord } from "./statusWords";

describe("statusWord", () => {
  it("says what the state means, not the internal word", () => {
    expect(statusWord("ok").text).toBe("Ready");
    expect(statusWord("stale").text).toContain("Out of date");
    expect(statusWord("failed").text).toContain("Failed");
    // **"new" is not a warning.** A freshly created settings node has not been used yet, which is
    // ordinary - calling it "needs attention" would make every new project amber.
    expect(statusWord("new").text).toBe("Not used yet");
    expect(statusWord("new").ready).toBe(false);
  });

  it("marks only 'ok' as nothing-to-do", () => {
    // **Green is not news**, so `ready` is what lets a caller hide the dot rather than show a
    // second green one. Every other state needs a person.
    expect(statusWord("ok").ready).toBe(true);
    for (const s of ["new", "warning", "stale", "failed"]) expect(statusWord(s).ready).toBe(false);
  });

  it("says so rather than pretending, for a status it does not know", () => {
    // A new status must not render as ready - that would hide the one case worth showing.
    const unknown = statusWord("sideways");
    expect(unknown.ready).toBe(false);
    expect(unknown.text).toContain("sideways");
  });
});

describe("countWords", () => {
  it("says the number counts the items inside", () => {
    // The guide's finding 7: `/scenes` says 4 before any run, and nothing said 4 OF WHAT.
    expect(countWords(4)).toBe("4 items inside");
    // And one is not "1 items".
    expect(countWords(1)).toBe("1 item inside");
    expect(countWords(0)).toBe("0 items inside");
  });
});
