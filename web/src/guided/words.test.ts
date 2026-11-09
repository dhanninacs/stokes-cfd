import { describe, expect, it } from "vitest";
import { plainSentence } from "./words";

describe("plainSentence", () => {
  it("drops the identifier in front of a server sentence", () => {
    expect(plainSentence("mesh.no_host: the host has no OpenFOAM install")).toBe("the host has no OpenFOAM install");
    expect(plainSentence("run.no_host: no host was available")).toBe("no host was available");
  });

  it("leaves a sentence that has none, and a colon that is not a code", () => {
    expect(plainSentence("The mesh was not built")).toBe("The mesh was not built");
    expect(plainSentence("Failed: the solver stopped")).toBe("Failed: the solver stopped");
  });
});
