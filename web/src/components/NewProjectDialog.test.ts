/**
 * The New project dialog's validation, and the path line it shows.
 *
 * **Why the dialog has its own test.** The owner's complaint was "New project asks only for a name",
 * and the three things the spec adds - a folder, the resolved full path, and Create disabled until
 * valid - are each a rule that can be wrong in a way a screenshot will not show: a name that is
 * silently accepted and refused by the server later, or a path line that shows what the user typed
 * instead of what `project_dir` will create.
 *
 * The NAME rule is the model's own (`NAME_RE`, `stokes/model/schema.py`), duplicated here because
 * the front end cannot import it - so these tests are what keep the two in step.
 */
import { describe, expect, it } from "vitest";
import { nameProblem, pathPieces, projectName, shownNameProblem, type NewProjectValues } from "./NewProjectDialog";

describe("the New project name", () => {
  it("accepts what the model accepts", () => {
    for (const good of ["duct", "Duct_2", "_x", "a1", "pIPE_sweep2"]) {
      expect(nameProblem(good), good).toBeNull();
    }
  });

  it("refuses what the model refuses, in words a user can act on", () => {
    // The model's `NAME_RE` is `^[A-Za-z_][A-Za-z0-9_]*$`: letters, digits and underscores, no
    // leading digit.
    for (const bad of ["2duct", "my duct", "a-b", "a.b", "a/b", "a$b", "a#b", "a;b", "café"]) {
      expect(nameProblem(bad), bad).not.toBeNull();
    }
  });

  it("asks for a name when the field is empty", () => {
    // Empty is its own message: "use letters and digits" on an empty field reads as nonsense.
    expect(nameProblem("")).toBe("Enter a name.");
  });

  it("says what to do rather than only what is wrong", () => {
    const problem = nameProblem("my duct");
    expect(problem).not.toBeNull();
    expect(problem).toContain("underscores");
  });
});

describe("a typed title becomes a project name", () => {
  it("turns spaces and separators into underscores instead of refusing them", () => {
    expect(projectName("mixing elbow")).toBe("mixing_elbow");
    expect(projectName("  Elbow - run 2.1 ")).toBe("Elbow_run_2_1");
    expect(projectName("a/b")).toBe("a_b");
    expect(projectName("(draft)")).toBe("draft");
  });

  it("leaves a valid name untouched, underscores included", () => {
    for (const good of ["duct", "Duct_2", "_x", "a1", "a_"]) expect(projectName(good)).toBe(good);
  });

  it("still refuses a leading digit, saying how to fix it", () => {
    expect(nameProblem(projectName("2 elbows"))).toBe("Start the name with a letter.");
  });
});

describe("what the dialog submits", () => {
  it("carries BOTH the name and the folder", () => {
    // The defect this dialog exists to fix: `project.new {name}` alone, with the folder decided by
    // whoever happened to call it. The values shape is asserted so a refactor cannot drop the folder
    // and keep compiling.
    const values: NewProjectValues = { name: "duct", folder: "/tmp/home/projects" };
    expect(Object.keys(values).sort()).toEqual(["folder", "name"]);
  });
});

describe("the dialog as it opens (GUI issue 3)", () => {
  it("does not show 'Enter a name.' before the user has typed or tried to create", () => {
    const problem = nameProblem("");
    expect(shownNameProblem(problem, "", false)).toBeNull();
    // Typed and cleared again, or Create pressed: now it is the user's to fix.
    expect(shownNameProblem(problem, "", true)).toBe("Enter a name.");
  });

  it("shows a real problem with a typed name at once", () => {
    expect(shownNameProblem(nameProblem("2duct"), "2duct", false)).not.toBeNull();
    expect(shownNameProblem(null, "duct", false)).toBeNull();
  });
});

describe("the path line's break points", () => {
  it("cuts only after separators, so a file or folder name is never split", () => {
    expect(pathPieces("D:\\work\\Stokes\\duct.stokes")).toEqual(["D:\\", "work\\", "Stokes\\", "duct.stokes"]);
    expect(pathPieces("/srv/data/stokes-work/projects/duct.stokes")).toEqual(["/", "srv/", "data/", "stokes-work/", "projects/", "duct.stokes"]);
  });

  it("joins back to the path exactly, mixed and doubled separators included", () => {
    for (const path of ["D:/work/a\\x\\y.stokes", "//server/share/p.stokes", "name.stokes", "/trailing/", ""]) {
      expect(pathPieces(path).join(""), path).toBe(path);
    }
  });
});
