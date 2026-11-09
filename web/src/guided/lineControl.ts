/**
 * The plot over a line, the impure half (R5): the Line button, the two clicks, the `run.probe` ask with its retry,
 * and saving the CSV. The rules (what a click does to the line, the ask's params, the files' names) are
 * `panels/lineModel.ts`, pure and tested; this reads the stores, the scene and the server.
 *
 * **The clicks ride the probe's point pick** (`scene.setProbePick`, answered by `Viewport`'s `onProbe`): while a line
 * pick is armed (`line.phase` is `pickA` or `pickB`) `Viewport` hands the click here instead of running the
 * probe's own reading, so the two cannot both answer one press.
 *
 * **One ask at a time.** Every ask, clear and restart bumps `seq`, and an answer that finds its number out of date
 * is dropped, so a slow answer never draws over a line that was cleared or re-made.
 */
import { host } from "../app/host";
import { NO_FIELD, fieldLabel, fieldUnits } from "../panels/fieldsModel";
import {
  carryToPlane,
  curveColour,
  lineAsk,
  lineCsv,
  lineErrorPhase,
  lineFileName,
  lineStale,
  pickPoint,
  pressLine,
  rayOnPlane,
  readLineReply,
  NO_LINE,
  type AxisPlane,
  type LineState,
} from "../panels/lineModel";
import { legendOf } from "../panels/legendModel";
import { useStore } from "../state/store";
import { download } from "../viewport/exportImage";
import type { PointHit } from "../viewport/scene";
import { lookContext } from "./looksControl";
import { linePlotPng } from "./linePlotImage";

/** How often a "preparing" answer is asked again, and how long for: the same bound as the single probe. */
const RETRIES = 40;
const RETRY_MS = 100;

let seq = 0;

function store() {
  return useStore.getState();
}

/** The plane the line lies in while the run's mid-plane slice is on, or null (the line is read on a patch). */
export function lineSlicePlane(): AxisPlane | null {
  const s = store();
  if (!s.results.view.slice || s.results.data?.slice !== true) return null;
  return lookContext().midPlane;
}

/** The Line button: starts a pick, or cancels the one waiting. Either way the probe's own pick is put away first. */
export function pressLineButton(): void {
  const s = store();
  seq++;
  if (s.probePick) s.setProbePick(false);
  s.setMeasureMode(false);
  s.setLine(pressLine(s.line));
}

/** Clear: the line, its plot and its markers go; the count stays. */
export function clearLine(): void {
  seq++;
  store().setLine({ ...NO_LINE, n: store().line.n });
}

/**
 * A click while a line pick is armed. `patchId` is the mesh patch the click met (null off the model). With the
 * mid-plane slice on, the click is carried to that plane along the camera ray and the line is read on the slice;
 * otherwise it is read on the patch under the FIRST click. A click that meets no meshed surface when one is needed
 * leaves the pick waiting, with a word.
 */
export function takeLinePick(hit: PointHit, patchId: string | null): void {
  const s = store();
  const line = s.line;
  if (line.phase !== "pickA" && line.phase !== "pickB") return;
  const plane = line.phase === "pickA" || line.patch === "slice" ? lineSlicePlane() : null;
  const raw = { point: [hit.point.x, hit.point.y, hit.point.z] as const, ray: hit.ray === undefined ? undefined : { origin: [hit.ray.origin.x, hit.ray.origin.y, hit.ray.origin.z] as const, direction: [hit.ray.direction.x, hit.ray.direction.y, hit.ray.direction.z] as const } };
  let point: readonly [number, number, number] = raw.point;
  let patch: string;
  if (line.phase === "pickA") {
    if (plane !== null) {
      patch = "slice";
      point = rayOnPlane(raw, plane);
    } else if (patchId !== null) {
      patch = patchId;
    } else {
      s.setLine({ ...line, message: "Click the meshed model" });
      return;
    }
  } else {
    patch = line.patch ?? "";
    if (patch === "slice" && plane !== null) point = rayOnPlane(raw, plane);
  }
  const next = pickPoint(line, point, patch);
  s.setLine(next);
  if (next.phase === "preparing") void askLine();
}

/** Asks `run.probe` for the line now on the store, retrying while the surface is prepared. */
export async function askLine(): Promise<void> {
  const mine = ++seq;
  const s = store();
  const field = s.results.view.field;
  const resultsSeq = s.results.seq;
  const line = s.line;
  const base = { ...line, field, seq: resultsSeq, along: [], values: [] };
  const ask = field === NO_FIELD ? null : lineAsk(line, field);
  if (ask === null) {
    if (line.a !== null && line.b !== null) s.setLine({ ...base, phase: "failed", message: field === NO_FIELD ? "Pick a field first" : "No line" });
    return;
  }
  s.setLine({ ...base, phase: "preparing", message: "" });
  for (let attempt = 0; attempt < RETRIES; attempt++) {
    try {
      const reply = await store().execute("run.probe", ask);
      if (mine !== seq) return;
      const read = readLineReply(reply, ask.n);
      if (read === null) {
        store().setLine({ ...base, phase: "failed", message: "The answer was not about this line" });
        return;
      }
      store().setLine({ ...base, phase: "ready", along: read.along, values: read.values, message: "" });
      return;
    } catch (err) {
      if (mine !== seq) return;
      const raw = err as { code?: string; message?: string };
      // **The code may be nested** in the message, as the single probe finds: `post.triangulation_preparing` still means wait.
      const code = raw.code ?? (raw.message?.includes("post.triangulation_preparing") === true ? "post.triangulation_preparing" : null);
      const phase = lineErrorPhase(code);
      if (phase !== "preparing") {
        store().setLine({ ...base, phase, message: String(raw.message ?? err) });
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, RETRY_MS));
    }
  }
  if (mine === seq) store().setLine({ ...base, phase: "failed", message: "The surface did not finish preparing" });
}

/**
 * The line follows what is on screen: a new field, or new results (another run, another time), asks again along the
 * same line; and with the line on the slice, a moved slice carries both ends to the new plane first. Called when
 * those change; does nothing for a line that is waiting on an answer.
 */
export function reconcileLine(): void {
  const s = store();
  let line: LineState = s.line;
  if (line.patch === "slice") {
    const plane = lineSlicePlane();
    if (plane !== null) {
      const carried = carryToPlane(line, plane);
      if (carried !== line) {
        s.setLine(carried);
        void askLine();
        return;
      }
    }
  }
  line = s.line;
  if (lineStale(line, s.results.view.field, s.results.seq)) void askLine();
}

// ---------------------------------------------------------------------------------------------- saving
function names(): { project: string; run: string; field: string } {
  const s = store();
  const run = s.run.runs.find((r) => r.id === s.results.run);
  return { project: s.project?.name ?? "", run: run?.name ?? s.results.run ?? "", field: s.line.field ?? s.results.view.field };
}

/** The line's CSV, through the host's save dialog when it has one, else a browser download. False when cancelled or refused. */
export async function saveLineCsv(): Promise<boolean> {
  const s = store();
  const { line } = s;
  if (line.phase !== "ready" || line.field === null) return false;
  const text = lineCsv(line.field, fieldUnits(s.results.data, line.field), line.along, line.values);
  const name = lineFileName(names(), "csv");
  if (host.canSaveFiles) {
    const path = await host.pickSaveFile("Save the line's values", name, "CSV files (*.csv);;All files (*)");
    return path ? await host.writeTextFile(path, text) : false;
  }
  download(new Blob([text], { type: "text/csv;charset=utf-8" }), name);
  return true;
}

/** The plot as a PNG at 2x, a browser download (the host's writer takes text, not bytes). */
export async function saveLinePng(): Promise<boolean> {
  const s = store();
  const { line } = s;
  if (line.phase !== "ready" || line.field === null) return false;
  const units = fieldUnits(s.results.data, line.field);
  const legend = legendOf(s.results.view);
  const blob = await linePlotPng({ label: fieldLabel(line.field, units), units, along: line.along, values: line.values, colour: curveColour(legend.map, legend.reverse) }, 2);
  download(blob, lineFileName(names(), "png"));
  return true;
}
