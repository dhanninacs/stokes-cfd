/**
 * Each fluid stream's flow, pressure drop and heat, as `results.streams` answers them, in the words and
 * numbers the Results card shows. Pure, so the rows and the balance line are tested without a browser.
 */

export interface Stream {
  region: string;
  inlets: string[];
  outlets: string[];
  massFlow: number | null;
  pressureDrop: number | null;
  tIn: number | null;
  tOut: number | null;
  heat: number | null;
}

export interface StreamsAnswer {
  streams: Stream[];
  /** The heat the heated streams gained against what the cooled ones lost, when both exist. */
  balance: { gained: number; lost: number; imbalance: number } | null;
  units: { massFlow: string; pressure: string };
}

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const names = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : []);

/** The command's answer, read defensively: anything malformed reads as no streams. */
export function readStreams(raw: unknown): StreamsAnswer {
  const out: StreamsAnswer = { streams: [], balance: null, units: { massFlow: "kg/s", pressure: "Pa" } };
  if (!isRecord(raw)) return out;
  if (Array.isArray(raw.streams)) {
    for (const s of raw.streams) {
      if (!isRecord(s) || typeof s.region !== "string") continue;
      out.streams.push({
        region: s.region,
        inlets: names(s.inlets),
        outlets: names(s.outlets),
        massFlow: num(s.mass_flow),
        pressureDrop: num(s.pressure_drop),
        tIn: num(s.T_in),
        tOut: num(s.T_out),
        heat: num(s.heat),
      });
    }
  }
  const b = raw.balance;
  if (isRecord(b) && num(b.gained) !== null && num(b.lost) !== null && num(b.imbalance) !== null) {
    out.balance = { gained: b.gained as number, lost: b.lost as number, imbalance: b.imbalance as number };
  }
  if (isRecord(raw.units)) {
    if (typeof raw.units.mass_flow === "string") out.units.massFlow = raw.units.mass_flow;
    if (typeof raw.units.pressure === "string") out.units.pressure = raw.units.pressure;
  }
  return out;
}

/** Four significant digits, no trailing zeros. */
function sig(v: number, digits = 4): string {
  return String(Number(v.toPrecision(digits)));
}

/** One stream as the card's row: name, flow, pressure drop, temperatures and heat, each "-" when unknown. */
export function streamRow(s: Stream, units: StreamsAnswer["units"]): { name: string; flow: string; dp: string; temps: string; heat: string } {
  const flow = s.massFlow === null ? "-" : units.massFlow === "kg/s" ? `${sig(s.massFlow * 1000)} g/s` : `${sig(s.massFlow)} ${units.massFlow}`;
  const dp = s.pressureDrop === null ? "-" : `${sig(s.pressureDrop)} ${units.pressure}`;
  const temps = s.tIn === null || s.tOut === null ? "-" : `${sig(s.tIn, 5)} → ${sig(s.tOut, 5)} K`;
  const heat = s.heat === null ? "-" : `${s.heat > 0 ? "+" : ""}${sig(s.heat)} W`;
  return { name: s.region, flow, dp, temps, heat };
}

/** The sentence under the table: the heat exchanged and how well the streams agree, or null. */
export function balanceLine(b: StreamsAnswer["balance"]): string | null {
  if (b === null) return null;
  return `Heat exchanged ${sig((b.gained + b.lost) / 2)} W · the streams agree to ${sig(b.imbalance * 100, 2)} %`;
}
