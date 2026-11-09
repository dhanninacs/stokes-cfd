/** Human-friendly unit strings from pint's compact form: "m**3/s" → "m³/s", "kg/m**3" → "kg/m³". */

const SUPERSCRIPT: Record<string, string> = { "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴", "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹", "-": "⁻" };

export function prettyUnit(unit: string): string {
  if (!unit) return "";
  return unit
    .replace(/\*\*(-?\d+)/g, (_, exp: string) => exp.split("").map((c) => SUPERSCRIPT[c] ?? c).join(""))
    .replace(/\*/g, "·")
    .replace(/degC/g, "°C")
    .replace(/degF/g, "°F")
    .replace(/delta_/g, "Δ");
}

export function formatNumber(value: number, digits = 5): string {
  if (!Number.isFinite(value)) return String(value);
  const magnitude = Math.abs(value);
  if (magnitude !== 0 && (magnitude >= 1e6 || magnitude < 1e-4)) return value.toExponential(Math.max(1, digits - 2));
  return Number(value.toPrecision(digits)).toString();
}

/** "2 m/s" → "2 m/s"; "1996 Pa" stays; "0.0056549 m**3/s" → "0.0056549 m³/s". */
export function prettyDisplay(display: string): string {
  const match = /^(\S+)\s+(.+)$/.exec(display.trim());
  if (!match) return display;
  return `${match[1]} ${prettyUnit(match[2])}`;
}
