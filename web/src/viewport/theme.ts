/**
 * Palette for the three.js viewport, one per theme.
 *
 * The accent follows the `--accent` token of `styles.css`, so the selection colour of the 3D
 * view always matches the chrome, and the view helper's Y axis follows `--ok`. The ground, shadow
 * and edge colours are viewport-specific:
 * they come from the optional tokens `--vp-grid-minor`, `--vp-grid-major`, `--vp-edge`,
 * `--vp-shadow` and `--vp-floor` when `styles.css` defines them, and from the defaults below
 * otherwise. Only hex values are read, because the colour parser ignores an alpha channel.
 */

export interface LineStyle {
  /** CSS colour, hex. */
  color: string;
  /** Opacity at full strength, 0..1. */
  alpha: number;
}

export interface SceneTheme {
  isDark: boolean;
  /** Selection and hover tint. */
  accent: string;
  gridMinor: LineStyle;
  gridMajor: LineStyle;
  /** World X axis on the ground. Red, as on the view helper. */
  axisX: LineStyle;
  /** World Z axis on the ground. Blue, as on the view helper. */
  axisZ: LineStyle;
  /** World Y axis (up), hex. Only the view helper draws it, in the green of the `--ok` token. */
  axisY: string;
  /** Faint disc under the part that reads as a floor. */
  floor: LineStyle;
  /** Contact shadow; `alpha` is the darkest value. */
  shadow: LineStyle;
  /** Feature-edge lines in the "edges" view mode. */
  edge: string;
  /** Renderer tone-mapping exposure. */
  exposure: number;
  /** `Scene.environmentIntensity`: strength of the image-based light. */
  environment: number;
}

const DARK: SceneTheme = {
  isDark: true,
  accent: "#19c0a8",
  gridMinor: { color: "#9db2c9", alpha: 0.1 },
  gridMajor: { color: "#b9cce2", alpha: 0.2 },
  axisX: { color: "#ff6b6b", alpha: 0.42 },
  axisZ: { color: "#5b9bff", alpha: 0.42 },
  axisY: "#3ddc84",
  floor: { color: "#8aa3bf", alpha: 0.1 },
  shadow: { color: "#000000", alpha: 0.78 },
  edge: "#0b1016",
  exposure: 1.0,
  environment: 0.9,
};

const LIGHT: SceneTheme = {
  isDark: false,
  accent: "#0f9d8a",
  gridMinor: { color: "#27384d", alpha: 0.1 },
  gridMajor: { color: "#1c2b3f", alpha: 0.2 },
  axisX: { color: "#d8433d", alpha: 0.5 },
  axisZ: { color: "#2f6fe0", alpha: 0.5 },
  axisY: "#2a9d5c",
  floor: { color: "#1c2b3f", alpha: 0.05 },
  shadow: { color: "#0d1b2e", alpha: 0.34 },
  edge: "#1a2230",
  exposure: 1.0,
  environment: 1.25,
};

const HEX = /^#[0-9a-fA-F]{6}$/;

/** A hex colour token from the document root, or null when it is absent or not a plain hex value. */
function readToken(name: string): string | null {
  if (typeof document === "undefined") return null;
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return HEX.test(value) ? value : null;
}

function withToken(base: LineStyle, name: string): LineStyle {
  const color = readToken(name);
  return color ? { color, alpha: base.alpha } : base;
}

/**
 * Palette for a theme. Call it after `data-theme` has been applied to the document root,
 * otherwise the tokens of the previous theme are read.
 */
export function themeFor(theme: "dark" | "light" | "cupertino" | "cupertino-dark" | "ubuntu" | "ubuntu-dark"): SceneTheme {
  // The Cupertino and Ubuntu families reuse the dark or light base; their accent and grid tokens
  // arrive via CSS.
  const base = theme === "dark" || theme === "cupertino-dark" || theme === "ubuntu-dark" ? DARK : LIGHT;
  return {
    ...base,
    accent: readToken("--accent") ?? base.accent,
    axisY: readToken("--ok") ?? base.axisY,
    gridMinor: withToken(base.gridMinor, "--vp-grid-minor"),
    gridMajor: withToken(base.gridMajor, "--vp-grid-major"),
    floor: withToken(base.floor, "--vp-floor"),
    shadow: withToken(base.shadow, "--vp-shadow"),
    edge: readToken("--vp-edge") ?? base.edge,
  };
}
