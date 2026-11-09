/**
 * What the Explore panels and actions do: each operation is one or two `study.*` commands, run through
 * the project store so the console shows them and the journal records them, with a short toast for the
 * result. The panels, the ribbon, the palette and the shortcuts all come here, so they all do the same.
 *
 * `study.update` and `study.results` answer with every row of the table, which the console must not be
 * made to print: those calls log a one-line summary instead of their reply.
 */
import { StokesApiError } from "../../api/client";
import type { NodeSummary } from "../../api/types";
import { host } from "../../app/host";
import { basename } from "../../state/parts";
import { findNode, useStore } from "../../state/store";
import { confirmDialog } from "./confirm";
import { plural } from "./format";
import { useExplore } from "./store";

export function describeError(err: unknown): string {
  if (err instanceof StokesApiError) return err.message;
  return err instanceof Error ? err.message : String(err);
}

interface InvokeOptions {
  /** One line for the console instead of the whole reply. */
  summary?: (result: never) => string;
  /** Show a failure as a toast. Off for a form, which shows it beside its fields. */
  toast?: boolean;
}

/** Runs a command the way `execute` does for the console, with a bounded line for the reply. */
export async function invoke<T = unknown>(method: string, params: Record<string, unknown>, options: InvokeOptions = {}): Promise<T> {
  const store = useStore.getState();
  store.log("command", `${method} ${JSON.stringify(params)}`);
  try {
    const result = await store.execute(method, params, { silent: true });
    const line = options.summary ? options.summary(result as never) : JSON.stringify(result);
    store.log("result", line.length > 400 ? `${line.slice(0, 400)}…` : line);
    return result as T;
  } catch (err) {
    // `execute` has logged the failure; a toast is the user's notice unless a form is showing it.
    if (options.toast !== false) store.pushToast("error", describeError(err));
    throw err;
  }
}

/** The study node the panels show, or null. */
export function currentStudy(): NodeSummary | null {
  return findNode(useStore.getState().tree, useExplore.getState().studyId);
}

/** The study to act on, or a toast that says there is none. */
function requireStudy(): NodeSummary | null {
  const study = currentStudy();
  if (!study) useStore.getState().pushToast("info", "No study yet. Create one with New study.");
  return study;
}

function toast(kind: "info" | "ok" | "error", text: string): void {
  useStore.getState().pushToast(kind, text);
}

async function refreshTable(): Promise<void> {
  await useExplore.getState().refresh();
}

// ---------------------------------------------------------------------------------------------- operations
export interface NewStudy {
  name: string;
  type: "sweep" | "doe";
  description: string;
  sampler?: string;
  count?: number;
}

/** `study.create`, then the study is selected so its card shows. Throws when the server refuses (a form shows why). */
export async function createStudy(input: NewStudy): Promise<void> {
  const params: Record<string, unknown> = { name: input.name, type: input.type };
  if (input.description) params.description = input.description;
  if (input.type === "doe") {
    if (input.sampler) params.sampler = input.sampler;
    if (input.count) params.count = input.count;
  }
  const created = await invoke<{ id: string }>("study.create", params, { toast: false });
  const store = useStore.getState();
  await store.refresh();
  store.select(created.id);
}

/** `study.generate`: the sampler fills the table. Asks first when it would throw away results. */
export async function generateTable(): Promise<void> {
  const study = requireStudy();
  if (!study) return;
  const results = useExplore.getState().results;
  const evaluated = results ? results.counts.done + results.counts.failed : 0;
  if (evaluated > 0) {
    const ok = await confirmDialog({
      title: "Replace the design table?",
      body: `Generating again replaces the ${plural(results?.points.length ?? 0, "point")} of ${study.name}, and the results of ${plural(evaluated, "evaluated point")} are lost. This cannot be undone.`,
      confirmLabel: "Generate",
    });
    if (!ok) return;
  }
  try {
    const reply = await invoke<{ points: number; sampler: string }>("study.generate", { study: study.id });
    toast("ok", `Generated ${plural(reply.points, "point")} (${reply.sampler.replace(/_/g, " ")})`);
    await refreshTable();
  } catch {
    /* invoke has told the user */
  }
}

interface UpdateReply {
  evaluated: number;
  done: number;
  failed: number;
  pending: number;
  cancelled: boolean;
}

/** `study.update`: evaluate "all" the points that are not done, or the given ones. */
export async function updateTable(points: "all" | readonly number[]): Promise<void> {
  const study = requireStudy();
  if (!study) return;
  const state = useExplore.getState();
  if (state.updating > 0) return;
  state.beginUpdate();
  try {
    const reply = await invoke<UpdateReply>(
      "study.update",
      { study: study.id, points: points === "all" ? "all" : [...points] },
      { summary: (r: UpdateReply) => `${r.evaluated} evaluated, ${r.done} done, ${r.failed} failed${r.cancelled ? ", cancelled" : ""}` },
    );
    if (reply.evaluated === 0) toast("info", "Nothing to update: every point is done.");
    else toast(reply.failed > 0 ? "error" : "ok", `Updated ${plural(reply.evaluated, "point")} · ${reply.done} done${reply.failed ? ` · ${reply.failed} failed` : ""}`);
  } catch {
    /* invoke has told the user */
  } finally {
    useExplore.getState().endUpdate();
    await refreshTable();
  }
}

/** `study.add_point` with a quantity text per variable; a variable left out takes the base case's value. */
export async function addPoint(inputs: Record<string, string>): Promise<void> {
  const study = currentStudy();
  if (!study) throw new Error("There is no study to add a point to.");
  await invoke("study.add_point", { study: study.id, inputs }, { toast: false });
  await refreshTable();
}

/** `study.clear`: reset the points to pending, or remove them. `null` is every point. */
export async function clearTable(points: readonly number[] | null, remove: boolean): Promise<void> {
  const study = requireStudy();
  if (!study) return;
  const count = points === null ? (useExplore.getState().results?.points.length ?? 0) : points.length;
  if (remove) {
    const ok = await confirmDialog({
      title: points === null ? "Remove every point?" : `Remove ${plural(count, "point")}?`,
      body: "The points leave the design table, with their results. This cannot be undone.",
      confirmLabel: "Remove",
    });
    if (!ok) return;
  }
  try {
    const params: Record<string, unknown> = { study: study.id, remove };
    if (points !== null) params.points = [...points];
    await invoke("study.clear", params);
    if (remove) useExplore.getState().clearSelection();
    await refreshTable();
  } catch {
    /* invoke has told the user */
  }
}

/**
 * The host's native save dialog, when the window has one (`host.canSaveFiles`); read at call time, as the host
 * fills it in after the page loads. A review build lets a script stand in for the dialog, which is modal and
 * would hold the whole window until somebody answered it: `window.__exploreSavePath` is the path it "picks",
 * null for a cancel, or false for a window that has no dialog. The test is on the build mode, so a production
 * build has none of this.
 */
function savePicker(): typeof host.pickSaveFile | null {
  if (import.meta.env.MODE === "review") {
    const stand = (window as unknown as { __exploreSavePath?: string | null | false }).__exploreSavePath;
    if (stand === false) return null;
    if (stand !== undefined) return async () => stand;
  }
  return host.canSaveFiles ? host.pickSaveFile : null;
}

/** The older, synchronous copy: it works in the desktop host, where `navigator.clipboard` can wait for a permission nobody sees. */
function copyLegacy(text: string): boolean {
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.cssText = "position:fixed;top:0;left:0;opacity:0";
  document.body.appendChild(area);
  area.select();
  try {
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    area.remove();
  }
}

async function copyText(text: string): Promise<boolean> {
  if (copyLegacy(text)) return true;
  try {
    const wait = new Promise<never>((_, reject) => window.setTimeout(() => reject(new Error("the clipboard did not answer")), 1500));
    await Promise.race([navigator.clipboard.writeText(text), wait]);
    return true;
  } catch {
    return false;
  }
}

/**
 * Export the table as CSV (SI values): to a file the host's save dialog names, through `study.export`;
 * without such a dialog the CSV text of `study.results` goes to the clipboard.
 */
export async function exportTable(): Promise<void> {
  const study = requireStudy();
  if (!study) return;
  const pick = savePicker();
  try {
    if (pick) {
      const chosen = await pick("Export design table", `${study.name}.csv`, "CSV files (*.csv);;JSON files (*.json);;All files (*)");
      if (!chosen) return;
      const path = /\.(csv|json)$/i.test(chosen) ? chosen : `${chosen}.csv`;
      const reply = await invoke<{ file: string }>("study.export", { study: study.id, path });
      toast("ok", `Exported ${study.name} to ${basename(reply.file)}`);
      return;
    }
    const reply = await invoke<{ text: string }>("study.results", { study: study.id, format: "csv" }, { summary: (r: { text: string }) => `csv, ${r.text.length.toLocaleString()} characters` });
    const rows = Math.max(0, reply.text.split("\n").filter((line) => line.trim() !== "").length - 1);
    if (await copyText(reply.text)) toast("ok", `Copied ${plural(rows, "point")} as CSV to the clipboard`);
    else toast("error", "The clipboard is not available in this window, and it has no save dialog to export to.");
  } catch {
    /* invoke has told the user */
  }
}
