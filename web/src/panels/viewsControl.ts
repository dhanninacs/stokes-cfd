/**
 * The saved views' hands (R4): read the figure on screen into a record, apply a record to the stores and the camera,
 * and run "Export all". The rules are `viewsModel.ts` (pure, tested); this file only reads the stores and the scene,
 * writes what the model decided, and waits for the viewport.
 *
 * **Apply writes in the order the Looks card does**: the saved section is forgotten first, the results view, the slice
 * list and the section go in ONE `setState` (so `viewport/fields.ts` engages the slice's cut on a whole state), then the
 * Looks controller's own actions (iso and streamlines asks, arrows, the projection) through `runLooks`, then the camera.
 * The camera is restored with `CameraRig.restore`, which takes it as the user's, so a patch or a cut that arrives later
 * does not re-frame the picture; it is restored once at once and once more when the layers have settled, **unless a
 * newer apply has begun or the user has moved the camera since** (`ApplyGuard`, `viewsRun.ts`).
 *
 * **Export all** applies each view, waits for the layers (`viewSettled`, after the slice debounce), runs the R3 export
 * with the view's name in the file name, downloads it, and goes on to the next, one at a time. The figure that was on
 * screen is put back at the end. A view whose field the run lacks is not exported; one that came out with less than it
 * asked for is named in the result's `notes` (`viewsRun.ts`).
 */
import { Vector3 } from "three";
import { askLine, clearLine } from "../guided/lineControl";
import { cancelSettledFit, runLooks } from "../guided/looksControl";
import { useStore } from "../state/store";
import { forgetSavedSection } from "../viewport/fields";
import { ExportMemoryError, cameraOf, currentExportView, download, exportImage } from "../viewport/exportImage";
import { currentScene, type SceneManager } from "../viewport/scene";
import { readStoredExport } from "./exportModel";
import { getView, saveView, type Execute, type SaveReply, type ViewSummary } from "./viewsApi";
import { applyView, cameraUp, exportAllNames, exportAllSettings, readRecord, recordOf, type ParsedView, type StreamExtras, type ViewApplication, type ViewRecord } from "./viewsModel";
import { ApplyGuard, exportAllWith, settleWith, type ApplyToken, type ExportAllProgress, type ExportAllResult } from "./viewsRun";

export type { ExportAllProgress, ExportAllResult };

const sleep = (ms: number): Promise<void> => new Promise((resolve) => window.setTimeout(resolve, ms));
const frame = (): Promise<void> => new Promise((resolve) => requestAnimationFrame(() => resolve()));

/** The applies in this window: a late restore of the camera checks it. */
const guard = new ApplyGuard();
/** The stamp each apply took when it put its camera back. */
const tokens = new WeakMap<ViewApplication, ApplyToken>();

function cameraStamp(): { camera: ReturnType<typeof cameraOf> | null; touched: boolean } {
  const scene = currentScene();
  return { camera: scene === null ? null : cameraOf(scene), touched: scene?.rig.touched === true };
}

function storage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

/** The figure on screen as a record: null without a scene or with no results to show. */
export function currentViewRecord(stream: StreamExtras | null): ViewRecord | null {
  const scene = currentScene();
  const s = useStore.getState();
  if (scene === null || s.results.data === null) return null;
  const live = scene.liveSize;
  return recordOf({ view: currentExportView(), camera: cameraOf(scene), pixels: { w: Math.round(live.width), h: Math.round(live.height) }, slices: s.slices, section: s.section, stream, line: s.line });
}

/** Save the figure on screen under `name`. Rejects with the server's refusal, or when there is nothing to save. */
export async function saveCurrentView(name: string, stream: StreamExtras | null): Promise<SaveReply> {
  const record = currentViewRecord(stream);
  if (record === null) throw new Error("Open the results first");
  return saveView(useStore.getState().execute as Execute, name, record);
}

const vec = (v: readonly [number, number, number]): Vector3 => new Vector3(v[0], v[1], v[2]);

function restoreCamera(scene: SceneManager | null, application: ViewApplication): void {
  const cam = application.camera;
  if (scene === null || cam === null) return;
  scene.setOrthographic(cam.orthographic);
  // A record with no up vector is the default up: an older record applied after a face-on view must not keep the turned one.
  scene.rig.restore({ target: vec(cam.target), dir: vec(cam.dir).normalize(), span: cam.span, up: vec(cameraUp(cam)).normalize() });
}

/**
 * Write a parsed view into the stores, make its asks and put the camera back. Returns what was applied (so the caller
 * can wait for it to settle and say what was left out). **A new apply drops a first-show fit still waiting**
 * (`cancelSettledFit`), which would otherwise frame the model over the view's camera, and makes every earlier apply's
 * late camera restore stale.
 */
export function applyParsed(parsed: ParsedView): ViewApplication {
  cancelSettledFit();
  const s = useStore.getState();
  const application = applyView(parsed, { results: s.results, slices: s.slices, section: s.section });
  if (application.forgetSection) forgetSavedSection();
  useStore.setState({ results: application.patch.results, slices: application.patch.slices, section: application.patch.section });
  runLooks(application.actions);
  // The view's line, or none: asked through `askLine` (its own retry while the surface is prepared); `preparing` holds the settle wait.
  if (application.line === null) clearLine();
  else {
    useStore.getState().setLine(application.line);
    void askLine();
  }
  restoreCamera(currentScene(), application);
  tokens.set(application, guard.begin(cameraStamp()));
  return application;
}

/** Apply a record (the answer of `view.get`, or a sidecar file); null when it is not a view. */
export function applyRecord(raw: unknown): ViewApplication | null {
  const parsed = readRecord(raw);
  return parsed === null ? null : applyParsed(parsed);
}

/**
 * Wait until the picture has what the view asked for (`settleWith`): the layers' debounce, `viewSettled` every
 * `SETTLE_POLL_MS` up to `SETTLE_MAX_MS`, two frames, then the camera, **unless a newer apply has begun or the user has
 * moved the camera since this one** (the restore is then not theirs to make). Resolves to whether the picture settled
 * (false: the wait ran out, a refused iso or a slow cut).
 */
export function settleView(application: ViewApplication): Promise<boolean> {
  return settleWith(application, {
    state: () => {
      const s = useStore.getState();
      return { results: s.results, slices: s.slices, iso: s.iso, streamlines: s.streamlines, loading: s.loading, line: s.line };
    },
    sleep,
    now: () => Date.now(),
    frame,
    restore: () => {
      const token = tokens.get(application);
      if (token === undefined || guard.mayRestore(token, cameraStamp())) restoreCamera(currentScene(), application);
    },
  });
}

/**
 * Apply each view in turn, export it and download the PNG named after the view (`exportAllWith`). Sequential. The
 * figure that was on screen is applied again at the end, even after a failure. `onProgress` is told before each view starts.
 */
export async function exportAllViews(views: readonly ViewSummary[], stream: StreamExtras | null, onProgress: (p: ExportAllProgress) => void = () => undefined): Promise<ExportAllResult> {
  const scene = currentScene();
  if (scene === null) return { names: [], stopped: "Open the results first", notes: [] };
  const execute = useStore.getState().execute as Execute;
  const settings = exportAllSettings(readStoredExport(storage()).settings);
  const before = currentViewRecord(stream);
  const current = currentExportView();
  const files = exportAllNames({ project: current.project, run: current.run }, views.map((v) => v.name));
  return exportAllWith({
    views,
    files,
    read: async (view) => {
      const record = await getView(execute, view.id);
      return record === null ? null : readRecord(record);
    },
    apply: applyParsed,
    settle: settleView,
    exportPicture: async () => {
      const result = await exportImage(scene, settings);
      return { picture: result.blob, name: result.name };
    },
    download,
    sleep,
    before: before === null ? null : readRecord(before),
    onProgress,
    errorText: (err) => (err instanceof ExportMemoryError ? err.message : err instanceof Error ? err.message : "The picture could not be made"),
  });
}
