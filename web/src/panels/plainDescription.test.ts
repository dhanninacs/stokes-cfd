import { describe, expect, it } from "vitest";
import { plainDescription } from "./plainDescription";

describe("plainDescription", () => {
  it("drops Markdown emphasis and code marks, keeping the words", () => {
    expect(plainDescription("Where Stokes keeps its files. **What the About page shows** `{path}`")).toBe("Where Stokes keeps its files. What the About page shows {path}");
  });

  it("joins lines and drops an unclosed emphasis mark", () => {
    expect(plainDescription("First line\n\n**Packaged first")).toBe("First line Packaged first");
  });
});
