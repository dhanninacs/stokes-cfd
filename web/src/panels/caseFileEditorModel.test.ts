import { describe, expect, it } from "vitest";
import { byteLength, isDirty, refusalText } from "./caseFileEditorModel";

describe("refusalText", () => {
  it("names the first finding with its line", () => {
    expect(refusalText("refused", { findings: [{ line: 12, message: "code in a dictionary" }] })).toBe(
      "Line 12: code in a dictionary",
    );
  });

  it("uses the finding alone when it has no line", () => {
    expect(refusalText("refused", { findings: [{ message: "a symbolic link" }] })).toBe("a symbolic link");
  });

  it("falls back to the error's own message", () => {
    expect(refusalText("too large", undefined)).toBe("too large");
    expect(refusalText("too large", { findings: [] })).toBe("too large");
  });
});

describe("isDirty and byteLength", () => {
  it("compares the buffer with what was loaded", () => {
    expect(isDirty("a", "a")).toBe(false);
    expect(isDirty("a", "ab")).toBe(true);
  });

  it("counts UTF-8 bytes, not characters", () => {
    expect(byteLength("abc")).toBe(3);
    expect(byteLength("é")).toBe(2);
  });
});
