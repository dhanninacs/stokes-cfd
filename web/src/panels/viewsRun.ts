/**
 * The waiting and the loop of saved views (R4 review), apart from the stores, the camera and the DOM, so the timeout, the
 * restore guard and the skipped fields are tested in Node with fake clocks. `viewsControl.ts` hands in the real
 * ones (`useStore`, `currentScene`, `setTimeout`, `exportImage`).
 *
 * * **`settleWith`** waits for the layers' debounce, then for `viewSettled` up to `SETTLE_MAX_MS`, then two frames, puts
 *   the camera back **if it may** and says whether the picture settled or the wait ran out.
 * * **`ApplyGuard`** is what "may" means: the camera is restored at the end of an apply only when no newer apply has
 *   begun and the camera is still where this apply put it. Two applies close together (a second press while the first
 *   waits for a cut) and a user who has orbited since both end with the camera where the LAST apply, or the user, left it.
 * * **`exportAllWith`** applies each view in turn, settles, exports and downloads it; a view the run cannot show (its field
 *   is missing) is skipped rather than written under a name it does not deserve, and every view that came out with less than
 *   it asked for (a skipped setting, a wait that ran out) is named in `notes`, which `exportAllSummary` words for the toast.
 *
 * Import rule: no React, no store, no DOM.
 */
import type { ExportCamera } from "./exportModel";
import { SETTLE_DELAY_MS, SETTLE_MAX_MS, SETTLE_POLL_MS, viewSettled, type ParsedView, type SettleInput, type ViewApplication } from "./viewsModel";
import type { ViewSummary } from "./viewsApi";

// ---------------------------------------------------------------------------------------------- the guard
/** What is known of the camera at the moment an apply puts it back. */
export interface CameraStamp {
  /** The camera as the sidecar records it; null without a scene. */
  camera: ExportCamera | null;
  /** The rig says the user (or a restore) has taken the camera as theirs. */
  touched: boolean;
}

export interface ApplyToken extends CameraStamp {
  /** Which apply this is (the guard's counter when it began). */
  seq: number;
}

const CAMERA_TOLERANCE = 1e-5;

function near(a: readonly number[], b: readonly number[], scale: number): boolean {
  return a.length === b.length && a.every((v, i) => Math.abs(v - (b[i] ?? NaN)) <= CAMERA_TOLERANCE * scale);
}

/** Two cameras are the same when their target, direction, span, projection and up agree to a part in 100 000 of the span. */
export function sameCamera(a: ExportCamera | null, b: ExportCamera | null): boolean {
  if (a === null || b === null) return a === b;
  if (a.orthographic !== b.orthographic) return false;
  const scale = Math.max(Math.abs(a.span), Math.abs(b.span), 1e-12);
  if (Math.abs(a.span - b.span) > CAMERA_TOLERANCE * scale) return false;
  if (!near(a.target, b.target, scale) || !near(a.dir, b.dir, 1)) return false;
  return a.up === undefined || b.up === undefined ? a.up === b.up : near(a.up, b.up, 1);
}

/** The counter of applies, and the test of whether a late restore is still wanted. */
export class ApplyGuard {
  private seq = 0;

  /** An apply has put its camera back: take its stamp. Later calls make every earlier token stale. */
  begin(stamp: CameraStamp): ApplyToken {
    this.seq += 1;
    return { ...stamp, seq: this.seq };
  }

  /** The camera may be restored for `token`: it is still the newest apply and nobody has moved the camera since. */
  mayRestore(token: ApplyToken, now: CameraStamp): boolean {
    return token.seq === this.seq && token.touched === now.touched && sameCamera(token.camera, now.camera);
  }
}

// ---------------------------------------------------------------------------------------------- settle
/** What the wait needs from the outside. */
export interface SettleDeps {
  state(): SettleInput;
  sleep(ms: number): Promise<void>;
  now(): number;
  frame(): Promise<void>;
  /** Put the camera back, if it may (`ApplyGuard.mayRestore`). */
  restore(): void;
}

/**
 * Wait until the picture has what the view asked for, as far as it will: the layers' debounce, then `viewSettled`
 * every `SETTLE_POLL_MS` up to `SETTLE_MAX_MS` (a refused iso never arrives), then two frames so the last cut is drawn,
 * then the camera. **Resolves to whether the picture settled**; false is the wait running out.
 */
export async function settleWith(application: ViewApplication, deps: SettleDeps): Promise<boolean> {
  await deps.sleep(SETTLE_DELAY_MS);
  const started = deps.now();
  let settled = false;
  while (deps.now() - started < SETTLE_MAX_MS) {
    if (viewSettled(deps.state(), application.expect)) {
      settled = true;
      break;
    }
    await deps.sleep(SETTLE_POLL_MS);
  }
  await deps.frame();
  await deps.frame();
  // A cut that landed meanwhile may have moved the rig's content; the camera is the view's, unless a newer apply or the user has it.
  deps.restore();
  await deps.frame();
  return settled;
}

// ---------------------------------------------------------------------------------------------- export all
/** What `exportAllWith` needs from the outside; `Picture` is whatever the export hands to the download. */
export interface ExportAllDeps<Picture> {
  views: readonly ViewSummary[];
  /** The file for each view, in order (`exportAllNames`). */
  files: readonly string[];
  /** The view's record read, or null when it cannot be (missing, not a view). */
  read(view: ViewSummary): Promise<ParsedView | null>;
  apply(parsed: ParsedView): ViewApplication;
  settle(application: ViewApplication): Promise<boolean>;
  exportPicture(): Promise<{ picture: Picture; name: string }>;
  download(picture: Picture, name: string): void;
  sleep(ms: number): Promise<void>;
  /** The figure that was on screen, to put back at the end. */
  before: ParsedView | null;
  onProgress?(p: ExportAllProgress): void;
  /** An error in words. */
  errorText(err: unknown): string;
}

export interface ExportAllProgress {
  done: number;
  total: number;
  name: string;
}

export interface ExportAllResult {
  /** The files written, in order. */
  names: string[];
  /** Why it stopped before the end, in words, or "". */
  stopped: string;
  /** What came out with less than the view asked for, or not at all, one line per view (`Pressure: No p in this run`). */
  notes: string[];
}

/**
 * Apply each view in turn, export it and download the PNG named after the view. Sequential: one view is applied,
 * settled, drawn and handed to the browser before the next is touched. A view whose field the run lacks is not
 * exported (the picture would show the field that was on screen); a view that settled late or skipped a setting is
 * exported and named in `notes`. The figure that was on screen is applied again at the end, even after a failure.
 */
export async function exportAllWith<Picture>(deps: ExportAllDeps<Picture>): Promise<ExportAllResult> {
  const names: string[] = [];
  const notes: string[] = [];
  let stopped = "";
  try {
    for (const [index, view] of deps.views.entries()) {
      deps.onProgress?.({ done: index, total: deps.views.length, name: view.name });
      const parsed = await deps.read(view);
      if (parsed === null) {
        stopped = `${view.name} could not be read`;
        break;
      }
      const application = deps.apply(parsed);
      if (application.missingField !== null) {
        notes.push(`${view.name}: ${application.skipped.join(", ") || `No ${application.missingField} in this run`}`);
        continue;
      }
      const settled = await deps.settle(application);
      const problems = [...application.skipped, ...(settled ? [] : ["timed out"])];
      if (problems.length > 0) notes.push(`${view.name}: ${problems.join(", ")}`);
      const result = await deps.exportPicture();
      const file = deps.files[index] ?? result.name;
      deps.download(result.picture, file);
      names.push(file);
      // Browsers hold back several downloads started at once; a short gap lets each one begin.
      await deps.sleep(250);
    }
  } catch (err) {
    stopped = deps.errorText(err);
  } finally {
    if (deps.before !== null) await deps.settle(deps.apply(deps.before));
  }
  return { names, stopped, notes };
}

/** How many notes the toast names before it says "+N more"; the console log has them all. */
const TOAST_NOTES = 2;

/**
 * What the toast says of a finished "Export all": the words, how loud (`ok` when nothing is to report), and `details`,
 * every line in full, for the console. The toast is short: the first notes, then how many more there are.
 */
export function exportAllSummary(result: ExportAllResult, total: number): { kind: "ok" | "info" | "error"; text: string; details: string[] } {
  const n = result.names.length;
  if (result.stopped === "" && result.notes.length === 0) return { kind: "ok", text: `Exported ${n} view${n === 1 ? "" : "s"}`, details: [] };
  const head = `${n} of ${total} exported`;
  const lines = [...(result.stopped === "" ? [] : [result.stopped]), ...result.notes];
  const shown = lines.slice(0, TOAST_NOTES);
  const more = lines.length - shown.length;
  return { kind: result.stopped === "" ? "info" : "error", text: [head, ...shown, ...(more > 0 ? [`+${more} more`] : [])].join(" · "), details: [head, ...lines] };
}
