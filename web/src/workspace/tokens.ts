/**
 * Client-side checks of theme token values, after docs/engineering/theme-tokens.md ("Kinds: what a
 * theme may set"), so the theme editor can refuse a value before it writes. The server checks again
 * (stokes.workspace.tokens) and stays the authority; the editor only checks the tokens it edits.
 *
 * Also the colour arithmetic the editor uses to derive the accent family from one accent.
 */

export type TokenKind = "colour" | "hex" | "length" | "shadow";

/** The tokens the theme editor edits or derives, with their kinds. */
export const EDITOR_TOKENS: Readonly<Record<string, TokenKind>> = {
  "--accent": "hex",
  "--accent-strong": "colour",
  "--accent-soft": "colour",
  "--accent-text": "colour",
  "--accent-ink": "colour",
  "--accent-fill": "colour",
  "--accent-glow": "colour",
  "--focus-halo": "shadow",
  "--bg": "colour",
  "--panel": "colour",
  "--panel-solid": "colour",
  "--text": "colour",
  "--text-muted": "colour",
  "--vp-top": "colour",
  "--vp-bottom": "colour",
  "--vp-grid-minor": "hex",
  "--vp-grid-major": "hex",
  "--radius": "length",
  "--radius-sm": "length",
};

const NAMED = new Set(
  `aliceblue antiquewhite aqua aquamarine azure beige bisque black blanchedalmond blue blueviolet brown burlywood cadetblue chartreuse
  chocolate coral cornflowerblue cornsilk crimson cyan darkblue darkcyan darkgoldenrod darkgray darkgreen darkgrey darkkhaki darkmagenta
  darkolivegreen darkorange darkorchid darkred darksalmon darkseagreen darkslateblue darkslategray darkslategrey darkturquoise darkviolet
  deeppink deepskyblue dimgray dimgrey dodgerblue firebrick floralwhite forestgreen fuchsia gainsboro ghostwhite gold goldenrod gray green
  greenyellow grey honeydew hotpink indianred indigo ivory khaki lavender lavenderblush lawngreen lemonchiffon lightblue lightcoral
  lightcyan lightgoldenrodyellow lightgray lightgreen lightgrey lightpink lightsalmon lightseagreen lightskyblue lightslategray
  lightslategrey lightsteelblue lightyellow lime limegreen linen magenta maroon mediumaquamarine mediumblue mediumorchid mediumpurple
  mediumseagreen mediumslateblue mediumspringgreen mediumturquoise mediumvioletred midnightblue mintcream mistyrose moccasin navajowhite
  navy oldlace olive olivedrab orange orangered orchid palegoldenrod palegreen paleturquoise palevioletred papayawhip peachpuff peru pink
  plum powderblue purple rebeccapurple red rosybrown royalblue saddlebrown salmon sandybrown seagreen seashell sienna silver skyblue
  slateblue slategray slategrey snow springgreen steelblue tan teal thistle tomato turquoise violet wheat white whitesmoke yellow
  yellowgreen transparent currentcolor`.split(/\s+/),
);
const NUM = String.raw`[+-]?(?:\d+(?:\.\d*)?|\.\d+)%?`;
const HUE = String.raw`[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:deg|grad|rad|turn)?`;
const fn = (name: string, first: string) =>
  new RegExp(String.raw`^${name}a?(?:\(\s*${first}\s*,\s*${NUM}\s*,\s*${NUM}\s*(?:,\s*${NUM}\s*)?\)|\(\s*${first}\s+${NUM}\s+${NUM}\s*(?:/\s*${NUM}\s*)?\))$`, "i");
const RGB = fn("rgb", NUM);
const HSL = fn("hsl", HUE);
const HEX_ANY = /^#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$/;
const HEX_SHORT_OR_LONG = /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/;
const VAR = /^var\(\s*(--[a-z0-9-]+)\s*\)$/;
const LENGTH = /^(?:0|(\d+(?:\.\d+)?)(px|rem|em))$/;
const SHADOW_LENGTH = /^(?:0|[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:px|rem|em))$/;
const FORBIDDEN = /url\(|image-set\(|expression\(|[;{}"']/i;

export function isColour(text: string): boolean {
  const value = text.trim();
  if (HEX_ANY.test(value) || RGB.test(value) || HSL.test(value) || NAMED.has(value.toLowerCase())) return true;
  const ref = VAR.exec(value);
  return ref !== null && (EDITOR_TOKENS[ref[1]] === "colour" || EDITOR_TOKENS[ref[1]] === "hex");
}

/** `#abc` and `#AABBCC` as `#aabbcc`. */
export function hex6(text: string): string {
  const digits = text.trim().slice(1).toLowerCase();
  return `#${digits.length === 3 ? [...digits].map((c) => c + c).join("") : digits}`;
}

function splitOutside(text: string, separator: "," | " "): string[] {
  const parts: string[] = [];
  let depth = 0;
  let current = "";
  for (const char of text) {
    if (char === "(") depth++;
    if (char === ")") depth--;
    const at = separator === " " ? /\s/.test(char) : char === separator;
    if (at && depth === 0) {
      parts.push(current);
      current = "";
    } else current += char;
  }
  parts.push(current);
  return parts.map((p) => p.trim()).filter(Boolean);
}

function isShadow(text: string): boolean {
  if (text.toLowerCase() === "none") return true;
  const layers = splitOutside(text, ",");
  if (layers.length === 0 || layers.length > 8) return false;
  return layers.every((layer) => {
    let lengths = 0;
    let colours = 0;
    let insets = 0;
    for (const part of splitOutside(layer, " ")) {
      if (part.toLowerCase() === "inset") insets++;
      else if (SHADOW_LENGTH.test(part)) lengths++;
      else if (isColour(part)) colours++;
      else return false;
    }
    return lengths >= 2 && lengths <= 4 && colours <= 1 && insets <= 1;
  });
}

/** Why `value` cannot be the value of `name`, or null when it can. Unknown names pass (the server checks them). */
export function tokenProblem(name: string, value: string): string | null {
  const text = value.trim();
  if (!text) return "Enter a value.";
  if (text.length > 256) return "A token value has at most 256 characters.";
  if (FORBIDDEN.test(text)) return "url(), quotes, braces and semicolons are not allowed.";
  switch (EDITOR_TOKENS[name]) {
    case "hex":
      return HEX_SHORT_OR_LONG.test(text) ? null : "The 3D view reads this one: use #rrggbb.";
    case "colour":
      return isColour(text) ? null : "Use a colour: #rrggbb, rgb(), hsl() or a CSS name.";
    case "length": {
      const m = LENGTH.exec(text);
      if (!m) return "Use a length such as 12px or 0.75rem.";
      if (m[1] !== undefined && Number(m[1]) > (m[2] === "px" ? 100 : 6)) return m[2] === "px" ? "At most 100px." : `At most 6${m[2]}.`;
      return null;
    }
    case "shadow":
      return isShadow(text) ? null : "Use one to eight box shadows.";
    default:
      return null;
  }
}

// ---------------------------------------------------------------------------------------------- colour arithmetic
export interface Rgba {
  r: number;
  g: number;
  b: number;
  a: number;
}

/** A colour the editor can show in a swatch and a colour picker: hex and rgb()/rgba() forms; null otherwise. */
export function parseColour(text: string): Rgba | null {
  const value = text.trim();
  if (HEX_ANY.test(value)) {
    let d = value.slice(1);
    if (d.length <= 4) d = [...d].map((c) => c + c).join("");
    const n = (i: number) => parseInt(d.slice(i, i + 2), 16);
    return { r: n(0), g: n(2), b: n(4), a: d.length === 8 ? n(6) / 255 : 1 };
  }
  const m = /^rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)(?:\s*[,/]\s*([\d.]+%?))?\s*\)$/i.exec(value);
  if (!m) return null;
  const alpha = m[4] === undefined ? 1 : m[4].endsWith("%") ? parseFloat(m[4]) / 100 : parseFloat(m[4]);
  return { r: Number(m[1]), g: Number(m[2]), b: Number(m[3]), a: Math.max(0, Math.min(1, alpha)) };
}

const hex2 = (n: number) => Math.round(Math.max(0, Math.min(255, n))).toString(16).padStart(2, "0");

export function toHex({ r, g, b }: Rgba): string {
  return `#${hex2(r)}${hex2(g)}${hex2(b)}`;
}

export function toRgba({ r, g, b, a }: Rgba): string {
  return a >= 1 ? toHex({ r, g, b, a }) : `rgba(${Math.round(r)}, ${Math.round(g)}, ${Math.round(b)}, ${Math.round(a * 100) / 100})`;
}

/** Towards white (amount > 0) or black (amount < 0), 0..1. */
function shade(c: Rgba, amount: number): Rgba {
  const t = amount > 0 ? 255 : 0;
  const k = Math.abs(amount);
  return { r: c.r + (t - c.r) * k, g: c.g + (t - c.g) * k, b: c.b + (t - c.b) * k, a: c.a };
}

function luminance({ r, g, b }: Rgba): number {
  const lin = (v: number) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}

/**
 * The accent family from one accent, so the primary button's gradient, the selected rows, the focus
 * ring and the accent text all follow it (the shapes of the built-in themes: a stronger tone under the
 * pointer, a translucent soft tone, an ink tone readable on panels).
 */
export function accentFamily(accent: string, light: boolean, cupertino: boolean, ubuntu = false): Record<string, string> {
  const c = parseColour(accent);
  if (!c) return { "--accent": accent };
  const solid = { ...c, a: 1 };
  const fill = light ? shade(solid, -0.12) : solid;
  const family: Record<string, string> = {
    "--accent": toHex(solid),
    "--accent-strong": toHex(light ? shade(solid, -0.08) : shade(solid, 0.1)),
    "--accent-soft": toRgba({ ...solid, a: light ? 0.14 : 0.16 }),
    "--accent-ink": toHex(light ? shade(solid, -0.24) : shade(solid, 0.18)),
    "--accent-fill": toHex(fill),
    "--accent-glow": toRgba({ ...solid, a: light ? 0.32 : 0.28 }),
    "--accent-text": luminance(fill) > 0.42 ? "rgba(0, 0, 0, 0.82)" : "#ffffff",
  };
  // Cupertino and Ubuntu both want a soft accent focus halo rather than the Stokes glow ring.
  if (ubuntu) family["--focus-halo"] = `0 0 0 4px ${toRgba({ ...solid, a: light ? 0.32 : 0.38 })}`;
  else if (cupertino) family["--focus-halo"] = `0 0 0 4px ${toRgba({ ...solid, a: light ? 0.35 : 0.4 })}`;
  return family;
}
