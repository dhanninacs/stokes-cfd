/**
 * The New project dialog's outcome after `project.new`.
 *
 * **Why this test exists.** A successful create must close the dialog; a failed one must leave it
 * open so the user can fix the name or folder and try again. A regression in either direction is
 * silent in a screenshot, so the decision is pinned here.
 */
import { describe, expect, it } from "vitest";
import { afterCreate } from "./newProjectOutcome";

describe("the New project dialog after project.new", () => {
  it("closes the dialog when the project was created", () => {
    expect(afterCreate(true).closeDialog).toBe(true);
  });

  it("keeps the dialog open when nothing was created", () => {
    expect(afterCreate(false).closeDialog).toBe(false);
  });
});
