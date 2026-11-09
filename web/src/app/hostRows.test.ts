/**
 * Which `host.list` rows belong in Settings' Remote hosts list.
 *
 * **A WSL distribution is discovered, not added**, so it belongs in the Compute list only: the
 * Remote hosts list offers Remove, and `host.remove` has no record to remove. Measured after the
 * WSL runner landed - the same `wsl:Ubuntu-24.04` row appeared in both lists.
 */
import { describe, expect, it } from "vitest";
import { isRemoteHost } from "./hostRows";

describe("isRemoteHost", () => {
  it("keeps this computer and WSL out of the SSH list", () => {
    expect(isRemoteHost({ name: "local", runner: "local" })).toBe(false);
    expect(isRemoteHost({ name: "wsl:Ubuntu-24.04", runner: "wsl:Ubuntu-24.04" })).toBe(false);
    // The name is enough even if an older reply omits the runner.
    expect(isRemoteHost({ name: "wsl:Ubuntu-24.04" })).toBe(false);
    // And a runner that names WSL is enough even if the name does not.
    expect(isRemoteHost({ name: "Ubuntu-24.04", runner: "wsl:Ubuntu-24.04" })).toBe(false);
  });

  it("keeps a real SSH record", () => {
    expect(isRemoteHost({ name: "buildbox", host: "10.0.0.4", user: "me", runner: "ssh" })).toBe(true);
  });
});
