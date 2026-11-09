/**
 * The theme families (DESIGN.md §2): Stokes, Cupertino and Ubuntu, each in a dark and a light
 * appearance, with `auto` variants that follow the OS inside their family.
 *
 * The bug these guard against: a new family added to the union but forgotten in one of the three
 * helpers, so the Style picker, the top-bar toggle or `?theme=` maps it back to Stokes. Every
 * appearance of every family must round-trip through `composeTheme`/`themeFamily`/`themeAppearance`,
 * and the `-auto` names must resolve to the right family.
 */
import { describe, expect, it, vi } from "vitest";

// `state/store` reads `window` as it loads (treeSync's dev hook) and this test has no DOM; the hook
// only reads it, so the global itself stands in. Hoisted, so it runs before the imports.
vi.hoisted(() => {
  const scope = globalThis as { window?: unknown };
  scope.window ??= globalThis;
});

import { THEMES, composeTheme, themeAppearance, themeFamily, type Theme } from "../state/store";
import { resolveName } from "../workspace/theme";

describe("the theme families", () => {
  it("lists both appearances of all three families", () => {
    expect(THEMES).toEqual(["dark", "light", "cupertino", "cupertino-dark", "ubuntu", "ubuntu-dark"]);
  });

  it("round-trips every built-in theme through its family and appearance", () => {
    for (const theme of THEMES) {
      expect(composeTheme(themeFamily(theme), themeAppearance(theme))).toBe(theme);
    }
  });

  it("puts each family on the right base and accent appearance", () => {
    expect(themeFamily("ubuntu")).toBe("ubuntu");
    expect(themeFamily("ubuntu-dark")).toBe("ubuntu");
    expect(themeAppearance("ubuntu")).toBe("light");
    expect(themeAppearance("ubuntu-dark")).toBe("dark");
    // Stokes and Cupertino are untouched by the new family.
    expect(themeFamily("dark")).toBe("stokes");
    expect(themeFamily("cupertino-dark")).toBe("cupertino");
    expect(composeTheme("cupertino", "light")).toBe("cupertino");
    expect(composeTheme("stokes", "dark")).toBe("dark");
  });

  it("resolves each auto name inside its own family", () => {
    const cases: [Theme | "auto" | "cupertino-auto" | "ubuntu-auto", "light" | "dark", Theme][] = [
      ["auto", "light", "light"],
      ["auto", "dark", "dark"],
      ["cupertino-auto", "light", "cupertino"],
      ["cupertino-auto", "dark", "cupertino-dark"],
      ["ubuntu-auto", "light", "ubuntu"],
      ["ubuntu-auto", "dark", "ubuntu-dark"],
    ];
    for (const [name, os, expected] of cases) expect(resolveName(name, os)).toBe(expected);
  });
});
