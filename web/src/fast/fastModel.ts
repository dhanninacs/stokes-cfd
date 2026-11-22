/**
 * Fast mode's rules on the client (ADR-0047): reading `fast.status` and `fast.frame`, and the words
 * the panel shows. Kept apart from the components so they are tested without a DOM. The client
 * computes no physics (ADR-0013): every number here is the server's, only formatted (a kinematic
 * pressure times the density the server sent is a change of unit, as the results legend makes it).
 */

/** The scene source of the shown plane (`viewport/fastLayer.ts` draws it, `fitTargets.ts` knows it by name). */
export const FAST_SOURCE = "mesh:fast";

/** True while the status says a preview is starting or stepping. */
export function previewRuns(state: string | undefined): boolean {
  return state === "running" || state === "starting";
}

/** The values the shown plane can be coloured by (`/api/fast/field?field=`). */
export const FAST_FIELDS = [
  { id: "U", label: "Velocity", units: "m/s" },
  { id: "p", label: "Pressure", units: "Pa" },
  { id: "Ux", label: "Velocity x", units: "m/s" },
  { id: "Uy", label: "Velocity y", units: "m/s" },
  { id: "Uz", label: "Velocity z", units: "m/s" },
  { id: "T", label: "Temperature", units: "K" },
] as const;
export type FastField = (typeof FAST_FIELDS)[number]["id"];

/** The values a preview offers: the temperature only when it solves one. */
export function fieldsFor(thermal: boolean): readonly (typeof FAST_FIELDS)[number][] {
  return thermal ? FAST_FIELDS : FAST_FIELDS.filter((f) => f.id !== "T");
}

/** The closures of a turbulent preview (`fast.configure`'s `turbulence`). */
export const TURBULENCE = [
  { value: "auto", label: "Automatic" },
  { value: "shear_improved", label: "Shear-improved (laminar-safe)" },
  { value: "smagorinsky", label: "Smagorinsky (smoothest)" },
] as const;

/** One fluid body's figures, as the Results stage's Streams table has them for a solved case. */
export interface FastStream {
  name: string;
  /** kg/s */
  massFlow: number;
  /** Pa, or null with no inlet or outlet. */
  dp: number | null;
  /** K, or null when the temperature is not solved. */
  tIn: number | null;
  tOut: number | null;
  /** W gained by the stream, or null. */
  heat: number | null;
}

/** Fidelity 1 to 5, with the cells across the fluid's smallest side each one means. */
export const FIDELITIES = [
  { value: 1, label: "1 · quickest (24 cells across)" },
  { value: 2, label: "2 · (32 cells across)" },
  { value: 3, label: "3 · (48 cells across)" },
  { value: 4, label: "4 · (64 cells across)" },
  { value: 5, label: "5 · finest (96 cells across)" },
] as const;

/** `fast.configure`'s `axis` for the automatic plane. */
export const AUTOMATIC_PLANE = -1;

/** The shown plane's normal: automatic (the fluid box's shortest side) or one axis. */
export const PLANES = [
  { value: AUTOMATIC_PLANE, label: "Automatic" },
  { value: 0, label: "Normal to x" },
  { value: 1, label: "Normal to y" },
  { value: 2, label: "Normal to z" },
] as const;

export interface FastFrame {
  step: number;
  mlups: number;
  dp: number;
  dpPa: number;
  inletSpeed: number;
  /** Volume flow in through the inlets and out through the outlets, m³/s. */
  flowIn: number;
  flowOut: number;
  /** Outflow less inflow, as a share of the inflow. */
  imbalance: number;
  guard: number;
  unstable: boolean;
  steady: boolean;
  restrictive: boolean;
  geometryVersion: number;
  axis: number;
  plane: number;
  vertices: number;
  /** One entry per fluid body. */
  streams: FastStream[];
  /** The sum of the streams' heats, W, or null without a temperature. */
  heatBalance: number | null;
  /** The lattice steps slowly because the flow is steady. */
  holding: boolean;
  /** The flow is steady and the temperature takes several steps to each of its. */
  catchingUp: boolean;
}

/** The lattice's box along one axis, for the plane's position: where it starts, its cell size and count. */
export interface FastSpan {
  origin: number;
  cell: number;
  count: number;
}

export interface FastStatus {
  state: "off" | "starting" | "running" | "stopped" | "failed";
  /** The fidelity the grid has, which is the asked-for one or a higher one. */
  fidelity: number | null;
  /** The fidelity asked for. */
  requested: number | null;
  cells: number | null;
  fluidCells: number | null;
  /** The cell cap made the cells larger than the fidelity asks. */
  coarsened: boolean;
  les: boolean;
  cellReynolds: number | null;
  /** The fluid's density, kg/m³: what turns the plane's kinematic pressure into pascals. */
  density: number | null;
  /** True while the shown plane is the automatic one. */
  automaticPlane: boolean;
  /** The bodies previewed: the fluids, then the solids. */
  zones: { name: string; kind: string }[];
  /** The temperature is solved. */
  thermal: boolean;
  /** Each fluid's cells across its passages (its hydraulic diameter in cells). */
  resolution: { name: string; cellsAcross: number }[];
  /** The closure asked for (`auto` or one of them) and the one in use. */
  turbulence: string;
  closure: string;
  /** The lattice's box per axis (x, y, z), or null before there is a grid. */
  spans: FastSpan[] | null;
  adapter: string;
  error: string;
  frame: FastFrame | null;
}

const num = (v: unknown, fallback = 0): number => (typeof v === "number" && Number.isFinite(v) ? v : fallback);

const orNull = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

function readStreams(data: unknown): FastStream[] {
  if (!Array.isArray(data)) return [];
  return data
    .filter((s): s is Record<string, unknown> => s !== null && typeof s === "object")
    .map((s) => ({ name: typeof s.name === "string" ? s.name : "", massFlow: num(s.mass_flow), dp: orNull(s.dp), tIn: orNull(s.t_in), tOut: orNull(s.t_out), heat: orNull(s.heat) }));
}

/** A `fast.frame` event or a status's `frame`, or null when it is not one. */
export function readFrame(data: unknown): FastFrame | null {
  if (data === null || typeof data !== "object") return null;
  const d = data as Record<string, unknown>;
  if (typeof d.step !== "number") return null;
  return {
    step: num(d.step),
    mlups: num(d.mlups),
    dp: num(d.dp),
    dpPa: num(d.dp_pa),
    inletSpeed: num(d.inlet_speed),
    flowIn: num(d.flow_in),
    flowOut: num(d.flow_out),
    imbalance: num(d.imbalance),
    guard: num(d.guard),
    unstable: d.unstable === true,
    steady: d.steady === true,
    restrictive: d.restrictive === true,
    geometryVersion: num(d.geometry_version),
    axis: num(d.axis, 2),
    plane: num(d.plane),
    vertices: num(d.vertices),
    streams: readStreams(d.streams),
    heatBalance: orNull(d.heat_balance),
    holding: d.holding === true,
    catchingUp: d.catching_up === true,
  };
}

const STATES = new Set(["off", "starting", "running", "stopped", "failed"]);

function readSpans(grid: Record<string, unknown> | null): FastSpan[] | null {
  if (grid === null || !Array.isArray(grid.origin) || grid.origin.length !== 3) return null;
  const cell = num(grid.dx);
  const counts = [num(grid.nx), num(grid.ny), num(grid.nz)];
  if (cell <= 0 || counts.some((n) => n <= 0)) return null;
  return counts.map((count, axis) => ({ origin: num((grid.origin as unknown[])[axis]), cell, count }));
}

/** `fast.status`'s answer (or `fast.stopped`'s payload). */
export function readStatus(data: unknown): FastStatus {
  const d = data !== null && typeof data === "object" ? (data as Record<string, unknown>) : {};
  const grid = d.grid !== null && typeof d.grid === "object" ? (d.grid as Record<string, unknown>) : null;
  const adapter = d.adapter !== null && typeof d.adapter === "object" ? (d.adapter as Record<string, unknown>) : null;
  const error = d.error !== null && typeof d.error === "object" ? (d.error as Record<string, unknown>) : null;
  const state = typeof d.state === "string" && STATES.has(d.state) ? (d.state as FastStatus["state"]) : "off";
  return {
    state,
    fidelity: grid !== null ? num(grid.fidelity, 0) || null : null,
    requested: num(d.fidelity, 0) || null,
    cells: grid !== null ? num(grid.cells, 0) || null : null,
    fluidCells: grid !== null ? num(grid.fluid_cells, 0) || null : null,
    coarsened: grid !== null && grid.coarsened === true,
    les: d.les === true,
    cellReynolds: typeof d.cell_reynolds === "number" ? d.cell_reynolds : null,
    density: typeof d.density === "number" && d.density > 0 ? d.density : null,
    automaticPlane: d.automatic_plane !== false,
    zones: Array.isArray(d.zones) ? d.zones.filter((z): z is { name: string; kind: string } => z !== null && typeof z === "object" && typeof (z as { name?: unknown }).name === "string").map((z) => ({ name: z.name, kind: String(z.kind) })) : [],
    thermal: d.thermal === true,
    resolution: Array.isArray(d.resolution)
      ? d.resolution.filter((r): r is { name: string; cells_across: number } => r !== null && typeof r === "object" && typeof (r as { cells_across?: unknown }).cells_across === "number").map((r) => ({ name: String(r.name), cellsAcross: r.cells_across }))
      : [],
    turbulence: typeof d.turbulence === "string" ? d.turbulence : "auto",
    closure: typeof d.closure === "string" ? d.closure : "",
    spans: readSpans(grid),
    adapter: adapter !== null && typeof adapter.name === "string" ? adapter.name : "",
    error: error !== null && typeof error.message === "string" ? error.message : "",
    frame: readFrame(d.frame),
  };
}

/** The key the routes put in `X-Fast-Key`: a mesh held under another key is fetched again. */
export function frameKey(frame: FastFrame): string {
  return `${frame.geometryVersion}:${frame.axis}:${frame.plane}`;
}

function cellsText(cells: number): string {
  return cells >= 1e6 ? `${(cells / 1e6).toFixed(1)} M cells` : `${Math.round(cells / 1e3)} k cells`;
}

/** A number in three figures with the prefix that keeps it between 1 and 1000: `9.18 mPa`, `2.08 kPa`. */
function scaled(value: number, unit: string, steps: readonly (readonly [number, string])[]): string {
  const size = Math.abs(value);
  if (size === 0 || !Number.isFinite(size)) return `0 ${unit}`;
  const [factor, prefix] = steps.find(([f]) => size >= f) ?? steps[steps.length - 1];
  return `${Number((value / factor).toPrecision(3))} ${prefix}${unit}`;
}

export function pressureText(pa: number): string {
  const size = Math.abs(pa);
  if (size >= 1e5) return `${Number((pa / 1e5).toPrecision(3))} bar`;
  return scaled(pa, "Pa", [[1e3, "k"], [1, ""], [1e-3, "m"], [1e-6, "µ"]]);
}

/** A volume flow, m³/s, in the litre unit that reads best: `1.2 L/s`, `20 mL/s`, `3.4 µL/s`. */
export function flowText(q: number): string {
  return scaled(q * 1e3, "L/s", [[1, ""], [1e-3, "m"], [1e-6, "µ"], [1e-9, "n"]]);
}

function percentText(share: number): string {
  const percent = share * 100;
  const text = Math.abs(percent) < 0.05 ? "0.0" : percent.toFixed(1);
  return `${percent > 0 && text !== "0.0" ? "+" : ""}${text} %`;
}

/** The panel's lines while a preview runs: what it is, how fast, and what it says. */
export function statsLines(status: FastStatus): string[] {
  const frame = status.frame;
  const lines: string[] = [];
  const cells = status.fluidCells ?? status.cells;
  const head = [status.fidelity !== null ? `Fidelity ${status.fidelity}` : "", cells !== null ? cellsText(cells) : "", status.les ? "turbulent preview (LES)" : "laminar"];
  lines.push(head.filter((s) => s !== "").join(" · "));
  if (frame === null || frame.step === 0) {
    lines.push("Building the lattice…");
    return lines;
  }
  lines.push(`${Math.round(frame.mlups).toLocaleString("en")} M cell updates/s · step ${frame.step.toLocaleString("en")}`);
  lines.push(`Pressure drop ${pressureText(frame.dpPa)}`);
  lines.push(`Flow in ${flowText(frame.flowIn)} · inlet ${Number(frame.inletSpeed.toPrecision(3))} m/s`);
  lines.push(`Flow out ${flowText(frame.flowOut)} (${percentText(frame.imbalance)})`);
  lines.push(frame.holding ? "Steady · holding (edit to resume)" : frame.steady ? "Steady" : frame.catchingUp ? "Flow steady · temperature settling…" : "Settling…");
  return lines;
}

/** What a preview of several bodies is previewing, in a line: `hot, cold (fluids) · plate (solid)`; "" for one fluid. */
export function zonesLine(status: FastStatus): string {
  if (status.zones.length < 2) return "";
  const fluids = status.zones.filter((z) => z.kind !== "solid").map((z) => z.name);
  const solids = status.zones.filter((z) => z.kind === "solid").map((z) => z.name);
  const parts = [fluids.length > 0 ? `${fluids.join(", ")} (${fluids.length > 1 ? "fluids" : "fluid"})` : "", solids.length > 0 ? `${solids.join(", ")} (${solids.length > 1 ? "solids" : "solid"})` : ""];
  return parts.filter((p) => p !== "").join(" · ");
}

/** A mass flow, kg/s, as `3.81 g/s` or `1.2 kg/s`. */
export function massFlowText(kgPerSecond: number): string {
  return scaled(kgPerSecond * 1e3, "g/s", [[1e3, "k"], [1, ""], [1e-3, "m"], [1e-6, "µ"]]);
}

/** A heat rate, W, signed: what the stream gained. */
export function heatText(watts: number): string {
  const text = scaled(watts, "W", [[1e3, "k"], [1, ""], [1e-3, "m"]]);
  return watts > 0 ? `+${text}` : text;
}

/** The streams table's rows: name, flow, pressure drop, temperatures in to out, heat. */
export function streamRows(frame: FastFrame | null): string[][] {
  if (frame === null) return [];
  return frame.streams.map((s) => [
    s.name,
    massFlowText(s.massFlow),
    s.dp !== null ? pressureText(s.dp) : "–",
    s.tIn !== null && s.tOut !== null ? `${s.tIn.toFixed(1)} → ${s.tOut.toFixed(1)} K` : "–",
    s.heat !== null ? heatText(s.heat) : "–",
  ]);
}

/** What the grid is that the user did not ask for, in a few words each (the reason is the tooltip's). */
export function gridNotes(status: FastStatus): { text: string; tip: string }[] {
  const notes: { text: string; tip: string }[] = [];
  if (status.fidelity !== null && status.requested !== null && status.fidelity > status.requested) {
    notes.push({
      text: `Fidelity raised to ${status.fidelity}`,
      tip: `The flow is too fast for the cells of fidelity ${status.requested}, so the preview runs on finer ones. Lower the inlet speed to use the fidelity you chose.`,
    });
  }
  if (status.coarsened) {
    notes.push({
      text: "Coarser cells than asked",
      tip: "The grid is at the most cells the preview takes (8 million, or what the GPU's memory allows), so its cells are larger than this fidelity asks for.",
    });
  }
  return notes;
}

/** Cells across a fluid's passages below which the panel says its figures are not to be relied on
 * (`stokes.fast.zones.RESOLVED_CELLS`). */
export const RESOLVED_CELLS = 10;

/** What to say when a fluid's passages are only a few cells across, or "": the picture is a guide, the numbers are not. */
export function resolutionWarning(status: FastStatus): string {
  const coarse = status.resolution.filter((r) => r.cellsAcross > 0 && r.cellsAcross < RESOLVED_CELLS);
  if (coarse.length === 0) return "";
  const worst = coarse.reduce((a, b) => (a.cellsAcross <= b.cellsAcross ? a : b));
  const who = status.resolution.length > 1 ? `${worst.name}'s passages` : "The passages";
  return `Under-resolved: ${who} are about ${Math.max(1, Math.round(worst.cellsAcross))} cells across. Read the picture, not the numbers; raise the fidelity.`;
}

/** The most the outflow may differ from the inflow, once steady, before the panel says so. */
export const IMBALANCE_LIMIT = 0.05;

/** What the user should know about the last frame, or "". */
export function frameWarning(frame: FastFrame | null): string {
  if (frame === null) return "";
  if (frame.unstable) return "The lattice became unstable and was restarted. Raise the fidelity or lower the inlet speed.";
  if (frame.guard > 0) return "The stability guard is holding parts of the flow: the numbers are not reliable. Raise the fidelity.";
  if (frame.steady && Math.abs(frame.imbalance) > IMBALANCE_LIMIT) return "The flow out differs from the flow in: the cells are too coarse for an opening. Raise the fidelity.";
  if (frame.restrictive) return "A restrictive core: the lattice runs slower to stay accurate.";
  return "";
}

/** The colours' range as the server's values gave it, for the legend. */
export interface FastRange {
  field: FastField;
  min: number;
  max: number;
}

/** The legend's title and its two ends, in the unit shown (pascals for the pressure, with the density). */
export function legendOf(range: FastRange, density: number | null): { title: string; low: string; high: string } {
  const def = FAST_FIELDS.find((f) => f.id === range.field) ?? FAST_FIELDS[0];
  const tick = (v: number) => (v === 0 ? "0" : Math.abs(v) >= 1e4 || Math.abs(v) < 1e-3 ? v.toExponential(2) : String(Number(v.toPrecision(3))));
  if (range.field === "p") {
    // The server's pressure is kinematic (m²/s²); without a density it is shown as it is.
    if (density === null) return { title: "Pressure, m²/s²", low: tick(range.min), high: tick(range.max) };
    return { title: "Pressure, Pa", low: tick(range.min * density), high: tick(range.max * density) };
  }
  // A temperature to a tenth of a kelvin: three figures would round 342.6 K to 343.
  if (range.field === "T") return { title: "Temperature, K", low: range.min.toFixed(1), high: range.max.toFixed(1) };
  return { title: `${def.label}, ${def.units}`, low: tick(range.min), high: tick(range.max) };
}

/** Where the plane sits along its axis as a share (0 to 1) of the lattice's box, from the frame's cell index. */
export function planeShare(frame: FastFrame | null, spans: FastSpan[] | null): number {
  const span = frame !== null && spans !== null ? spans[frame.axis] : undefined;
  if (frame === null || span === undefined || span.count <= 1) return 0.5;
  return Math.min(1, Math.max(0, (frame.plane + 0.5) / span.count));
}

/** The offset (m) `fast.configure` takes for a share of the lattice's box along `axis`. */
export function planeOffset(share: number, axis: number, spans: FastSpan[] | null): number | null {
  const span = spans !== null ? spans[axis] : undefined;
  if (span === undefined) return null;
  return span.origin + Math.min(1, Math.max(0, share)) * span.count * span.cell;
}
