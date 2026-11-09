/**
 * The monitors of a run as the Results panel shows them (T-219 D): one row per patch the run measured, with
 * its volumetric flow rate and its mean pressure; the table as text for the clipboard and as CSV for a file;
 * and the pressure drop between the inlet and the outlet when both were measured. Pure functions over the
 * run record's `monitors` (`runApi.ts`), so every number and every refusal is tested in Node.
 *
 * Units: the flow rate is volumetric (m3/s). An incompressible solver's pressure is kinematic, p/rho
 * (m2/s2), so the drop is Delta p / rho and says so: the density is the material's, not the monitor's. The
 * energy engine's pressure is in Pa and is named `p` (`pressureLabel`).
 */
import type { MonitorValue, RunSummary } from "./runApi";
import { pressureLabel } from "./fieldsModel";

export const FLOW_UNITS = "m3/s";
export const PRESSURE_UNITS = "m2/s2";

/** A monitor value as a person reads it: four significant digits, exponent form for the very small or large. */
export function formatMonitor(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "-";
  if (value === 0) return "0";
  const magnitude = Math.abs(value);
  if (magnitude >= 1e5 || magnitude < 1e-3) {
    const [mantissa, exponent] = value.toExponential(3).split("e");
    return `${mantissa.replace(/\.?0+$/, "")}e${Number(exponent)}`;
  }
  return String(Number(value.toPrecision(4)));
}

export interface MonitorRow {
  patch: string;
  flowRate: string;
  pMean: string;
}

export function monitorRows(monitors: readonly MonitorValue[]): MonitorRow[] {
  return monitors.map((m) => ({ patch: m.patch, flowRate: formatMonitor(m.flowRate), pMean: formatMonitor(m.pMean) }));
}

/** The table as tab-separated text, header first: what "Copy table" puts on the clipboard (it pastes into a sheet). */
export function tableText(monitors: readonly MonitorValue[], pUnits = PRESSURE_UNITS): string {
  const head = ["patch", `flow rate (${FLOW_UNITS})`, `mean ${pressureLabel(pUnits)} (${pUnits})`].join("\t");
  const lines = monitors.map((m) => [m.patch, formatMonitor(m.flowRate), formatMonitor(m.pMean)].join("\t"));
  return [head, ...lines].join("\n") + "\n";
}

/** A CSV field: quoted when it holds a comma, a quote or a line break, with the quotes doubled. */
function csvField(text: string): string {
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

function exact(value: number | null): string {
  return value === null || !Number.isFinite(value) ? "" : String(value);
}

/** The monitors as CSV with the full-precision numbers (an empty cell where the solver reported none), CRLF-free, one row per patch. */
export function csvText(monitors: readonly MonitorValue[], pUnits = PRESSURE_UNITS): string {
  const head = ["patch", `flow_rate_${FLOW_UNITS}`, `p_mean_${pUnits}`].map(csvField).join(",");
  const lines = monitors.map((m) => [csvField(m.patch), exact(m.flowRate), exact(m.pMean)].join(","));
  return [head, ...lines].join("\n") + "\n";
}

/** A file name for the export: the run's name (or id) with anything that is not a letter, digit, dot, dash or underscore made safe. */
export function csvFileName(run: Pick<RunSummary, "id" | "name"> | null): string {
  const base = (run === null ? "" : run.name !== "" ? run.name : run.id).replace(/[^A-Za-z0-9._-]+/g, "_").replace(/^_+|_+$/g, "");
  return `${base !== "" ? base : "run"}-monitors.csv`;
}

export interface PressureDrop {
  inlet: string;
  outlet: string;
  /** Delta p / rho = p_mean(inlet) - p_mean(outlet), in `units`. */
  value: number;
  text: string;
  units: string;
}

function isNamed(patch: string, word: "inlet" | "outlet"): boolean {
  return patch.toLowerCase().includes(word);
}

/** The drop's symbol for its unit: `Δp/ρ` for a kinematic pressure, `Δp` for one in Pa (`pressureLabel`). */
export function dropSymbol(units: string): string {
  return pressureLabel(units) === "p" ? "Δp" : "Δp/ρ";
}

/**
 * The drop from the inlet to the outlet, or null when it cannot be said: no inlet or no outlet patch among
 * the monitors, more than one of either (which one would be a guess), or a pressure the solver did not report.
 * A patch named exactly `inlet` or `outlet` wins over names that merely contain the word.
 */
export function pressureDrop(monitors: readonly MonitorValue[], units = PRESSURE_UNITS): PressureDrop | null {
  const pick = (word: "inlet" | "outlet"): MonitorValue | null => {
    const exactly = monitors.filter((m) => m.patch.toLowerCase() === word);
    if (exactly.length === 1) return exactly[0];
    if (exactly.length > 1) return null;
    const some = monitors.filter((m) => isNamed(m.patch, word));
    return some.length === 1 ? some[0] : null;
  };
  const inlet = pick("inlet");
  const outlet = pick("outlet");
  if (inlet === null || outlet === null || inlet.pMean === null || outlet.pMean === null) return null;
  const value = inlet.pMean - outlet.pMean;
  if (!Number.isFinite(value)) return null;
  return { inlet: inlet.patch, outlet: outlet.patch, value, text: `${formatMonitor(value)} ${units}`, units };
}

/**
 * The monitors of a run, or none for no run. A record that came from somewhere that does not know `monitors`
 * (a scripted server, an older one) has none, and the tree, the table and the legend must not fall over it.
 */
export function monitorsOf(run: { monitors?: readonly MonitorValue[] } | null | undefined): readonly MonitorValue[] {
  return run?.monitors ?? [];
}

/**
 * The run whose monitors are shown: the one the fields were read from when there is one, else the newest
 * finished run, else null. A run that is still going has no monitors yet and is never the answer.
 */
export function runForMonitors<R extends Pick<RunSummary, "id" | "state">>(runs: readonly R[], resultsRun: string | null): R | null {
  const done = (r: R) => r.state === "done" || r.state === "done_unconverged";
  const held = resultsRun === null ? undefined : runs.find((r) => r.id === resultsRun);
  if (held !== undefined && done(held)) return held;
  return runs.find(done) ?? null;
}
