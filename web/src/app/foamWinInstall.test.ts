import { describe, expect, it } from "vitest";
import { megabytes, offerFoamWin, progressFraction, progressLine } from "./foamWinInstall";

describe("offerFoamWin", () => {
  it("offers the download only on this Windows computer with no supported install", () => {
    expect(offerFoamWin({ host: "local", system: "Windows", installs: [] })).toBe(true);
    expect(offerFoamWin({ host: "local", system: "Windows", installs: [{ supported: false }] })).toBe(true);
    expect(offerFoamWin({ host: "local", system: "Windows", installs: [{ supported: true }] })).toBe(false);
    expect(offerFoamWin({ host: "local", system: "Linux", installs: [] })).toBe(false);
    expect(offerFoamWin({ host: "wsl:Ubuntu-24.04", system: "Windows", installs: [] })).toBe(false);
    expect(offerFoamWin(null)).toBe(false);
  });
});

describe("progress", () => {
  it("names each step and measures the download", () => {
    expect(megabytes(137_505_636)).toBe("137.5 MB");
    expect(progressLine({ stage: "download", done: 68_752_818, total: 137_505_636 })).toBe(
      "Downloading 68.8 MB of 137.5 MB",
    );
    expect(progressFraction({ stage: "download", done: 50, total: 200 })).toBe(0.25);
    expect(progressFraction({ stage: "unpack", done: 0, total: 0 })).toBeNull();
    expect(progressLine({ stage: "unpack", done: 0, total: 0 })).toContain("SHA-256");
    expect(progressLine(null)).toBe("");
  });
});
