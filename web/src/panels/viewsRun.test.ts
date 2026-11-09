import { describe, expect, it } from "vitest";
import { NO_LOADING, type LoadingCounts } from "../state/loadingState";
import type { ExportCamera } from "./exportModel";
import { NO_RESULTS } from "./fieldsModel";
import { NO_ISO } from "./isoModel";
import { NO_LINE, type LineState } from "./lineModel";
import { NO_SLICES } from "./sliceModel";
import { NO_STREAMLINES } from "./streamlineModel";
import type { ViewSummary } from "./viewsApi";
import { SETTLE_DELAY_MS, SETTLE_MAX_MS, type ParsedView, type SettleInput, type ViewApplication } from "./viewsModel";
import { ApplyGuard, exportAllSummary, exportAllWith, sameCamera, settleWith, type CameraStamp, type ExportAllDeps, type SettleDeps } from "./viewsRun";

// ---------------------------------------------------------------------------------------------- fixtures
const CAMERA: ExportCamera = { target: [0, 0.1, 0.5], dir: [0, 1, 0], span: 0.25, orthographic: false, up: [1, 0, 0] };

function application(over: Partial<ViewApplication> = {}): ViewApplication {
  return {
    patch: { results: { ...NO_RESULTS, status: "ready" }, slices: NO_SLICES, section: null },
    forgetSection: false,
    actions: [],
    camera: CAMERA,
    line: null,
    skipped: [],
    missingField: null,
    expect: { iso: null, streamlines: false },
    ...over,
  };
}

function idle(loading: LoadingCounts = NO_LOADING, line: LineState = NO_LINE): SettleInput {
  return { results: { ...NO_RESULTS, status: "ready" }, slices: NO_SLICES, iso: NO_ISO, streamlines: NO_STREAMLINES, loading, line };
}

/** A clock that only moves when something sleeps, so a 20 s wait takes no time. */
function clock(): { now: () => number; sleep: (ms: number) => Promise<void>; time: () => number } {
  let t = 0;
  return {
    now: () => t,
    sleep: async (ms) => {
      t += ms;
    },
    time: () => t,
  };
}

function settleDeps(state: () => SettleInput, restore: () => void = () => undefined): { deps: SettleDeps; frames: () => number; time: () => number } {
  const c = clock();
  let frames = 0;
  return {
    deps: {
      state,
      sleep: c.sleep,
      now: c.now,
      frame: async () => {
        frames++;
      },
      restore,
    },
    frames: () => frames,
    time: c.time,
  };
}

// ---------------------------------------------------------------------------------------------- settle
describe("settleWith", () => {
  it("waits for the debounce, then returns true when the layers have settled, restoring the camera once", async () => {
    let polls = 0;
    let restored = 0;
    const { deps, frames, time } = settleDeps(
      () => idle({ ...NO_LOADING, sliceFields: ++polls < 4 ? 1 : 0 }),
      () => restored++,
    );
    expect(await settleWith(application(), deps)).toBe(true);
    expect(polls).toBe(4);
    expect(restored).toBe(1);
    expect(frames()).toBe(3);
    expect(time()).toBe(SETTLE_DELAY_MS + 3 * 100);
  });

  it("gives up after the limit and says so, and the camera is still put back", async () => {
    let restored = 0;
    const { deps, time } = settleDeps(
      () => idle({ ...NO_LOADING, fields: 1 }),
      () => restored++,
    );
    expect(await settleWith(application(), deps)).toBe(false);
    expect(restored).toBe(1);
    expect(time()).toBeGreaterThanOrEqual(SETTLE_DELAY_MS + SETTLE_MAX_MS);
    expect(time()).toBeLessThan(SETTLE_DELAY_MS + SETTLE_MAX_MS + 1000);
  });

  it("does not stop at the cut while the values that colour it are on the way (A on a slice, then B on the same plane)", async () => {
    let polls = 0;
    const { deps } = settleDeps(() => idle({ ...NO_LOADING, sliceFields: polls++ < 2 ? 1 : 0 }));
    expect(await settleWith(application(), deps)).toBe(true);
    expect(polls).toBe(3);
  });

  it("waits while the view's line is preparing, and is done when it is ready", async () => {
    let polls = 0;
    const { deps } = settleDeps(() => idle(NO_LOADING, { ...NO_LINE, a: [0, 0, 0], b: [1, 0, 0], patch: "slice", phase: ++polls < 4 ? "preparing" : "ready" }));
    expect(await settleWith(application(), deps)).toBe(true);
    expect(polls).toBe(4);
  });

  it("does not wait for a line that failed or is outside, and gives up on one that never finishes", async () => {
    for (const phase of ["failed", "outside"] as const) {
      const { deps } = settleDeps(() => idle(NO_LOADING, { ...NO_LINE, phase }));
      expect(await settleWith(application(), deps)).toBe(true);
    }
    const { deps } = settleDeps(() => idle(NO_LOADING, { ...NO_LINE, phase: "preparing" }));
    expect(await settleWith(application(), deps)).toBe(false);
  });
});

describe("ApplyGuard", () => {
  const stamp = (over: Partial<CameraStamp> = {}): CameraStamp => ({ camera: CAMERA, touched: true, ...over });

  it("lets the newest apply restore the camera it put there", () => {
    const guard = new ApplyGuard();
    const token = guard.begin(stamp());
    expect(guard.mayRestore(token, stamp())).toBe(true);
  });

  it("makes an older apply's restore stale once a newer apply has begun", () => {
    const guard = new ApplyGuard();
    const first = guard.begin(stamp());
    const second = guard.begin(stamp({ camera: { ...CAMERA, dir: [1, 0, 0] } }));
    expect(guard.mayRestore(first, stamp({ camera: { ...CAMERA, dir: [1, 0, 0] } }))).toBe(false);
    expect(guard.mayRestore(second, stamp({ camera: { ...CAMERA, dir: [1, 0, 0] } }))).toBe(true);
  });

  it("does not restore over a camera the user has moved, or taken as theirs", () => {
    const guard = new ApplyGuard();
    const token = guard.begin(stamp());
    expect(guard.mayRestore(token, stamp({ camera: { ...CAMERA, span: 0.2 } }))).toBe(false);
    expect(guard.mayRestore(token, stamp({ camera: { ...CAMERA, target: [0, 0.1, 0.6] } }))).toBe(false);
    const fresh = new ApplyGuard().begin(stamp({ touched: false }));
    expect(new ApplyGuard().mayRestore(fresh, stamp({ touched: true }))).toBe(false);
  });

  it("two applies close together: the first one's late restore is skipped, the second's is made", async () => {
    const guard = new ApplyGuard();
    const onScreen: ExportCamera[] = [];
    let now: ExportCamera = CAMERA;
    const apply = (camera: ExportCamera): { app: ViewApplication; deps: SettleDeps } => {
      now = camera;
      const token = guard.begin({ camera, touched: true });
      const app = application({ camera });
      const { deps } = settleDeps(
        () => idle(),
        () => {
          if (guard.mayRestore(token, { camera: now, touched: true })) {
            now = camera;
            onScreen.push(camera);
          }
        },
      );
      return { app, deps };
    };
    const a = apply({ ...CAMERA, dir: [1, 0, 0] });
    const waitA = settleWith(a.app, a.deps);
    const b = apply({ ...CAMERA, dir: [0, 0, 1] });
    await Promise.all([waitA, settleWith(b.app, b.deps)]);
    expect(onScreen).toEqual([{ ...CAMERA, dir: [0, 0, 1] }]);
  });

  it("sameCamera tolerates round-off and nothing more", () => {
    expect(sameCamera(CAMERA, { ...CAMERA, span: CAMERA.span * (1 + 1e-9) })).toBe(true);
    expect(sameCamera(CAMERA, { ...CAMERA, orthographic: true })).toBe(false);
    expect(sameCamera(CAMERA, { ...CAMERA, up: [0, 1, 0] })).toBe(false);
    expect(sameCamera(null, null)).toBe(true);
    expect(sameCamera(CAMERA, null)).toBe(false);
  });
});

// ---------------------------------------------------------------------------------------------- export all
const VIEWS: ViewSummary[] = [
  { id: "n_1", name: "Velocity", created: "" },
  { id: "n_2", name: "Pressure", created: "" },
  { id: "n_3", name: "Front", created: "" },
];
const parsedOf = (tag: string): ParsedView => ({ tag }) as unknown as ParsedView;
const BEFORE = parsedOf("before");

interface Harness {
  deps: ExportAllDeps<string>;
  log: string[];
}

/** Fake `exportAllWith` deps: `apps` is what applying each view gives, `settled` what its wait says. */
function harness(over: { apps?: Record<string, Partial<ViewApplication>>; settled?: Record<string, boolean>; unreadable?: string; failAt?: string } = {}): Harness {
  const log: string[] = [];
  let current = "";
  const deps: ExportAllDeps<string> = {
    views: VIEWS,
    files: ["p-r-Velocity.png", "p-r-Pressure.png", "p-r-Front.png"],
    read: async (view) => {
      log.push(`read ${view.name}`);
      return view.name === over.unreadable ? null : parsedOf(view.name);
    },
    apply: (parsed) => {
      const tag = (parsed as unknown as { tag: string }).tag;
      current = tag;
      log.push(`apply ${tag}`);
      return application(over.apps?.[tag]);
    },
    settle: async () => {
      log.push(`settle ${current}`);
      return over.settled?.[current] ?? true;
    },
    exportPicture: async () => {
      log.push(`export ${current}`);
      if (current === over.failAt) throw new Error("Out of memory");
      return { picture: `png of ${current}`, name: "fallback.png" };
    },
    download: (picture, name) => log.push(`download ${name} (${picture})`),
    sleep: async () => undefined,
    before: BEFORE,
    errorText: (err) => (err instanceof Error ? err.message : "failed"),
  };
  return { deps, log };
}

describe("exportAllWith", () => {
  it("applies, settles, exports and downloads each view in turn, then puts the figure back", async () => {
    const { deps, log } = harness();
    const result = await exportAllWith(deps);
    expect(result).toEqual({ names: ["p-r-Velocity.png", "p-r-Pressure.png", "p-r-Front.png"], stopped: "", notes: [] });
    expect(log).toEqual([
      "read Velocity", "apply Velocity", "settle Velocity", "export Velocity", "download p-r-Velocity.png (png of Velocity)",
      "read Pressure", "apply Pressure", "settle Pressure", "export Pressure", "download p-r-Pressure.png (png of Pressure)",
      "read Front", "apply Front", "settle Front", "export Front", "download p-r-Front.png (png of Front)",
      "apply before", "settle before",
    ]);
  });

  it("names a view whose wait ran out, and still exports it", async () => {
    const { deps } = harness({ settled: { Pressure: false } });
    const result = await exportAllWith(deps);
    expect(result.names).toHaveLength(3);
    expect(result.notes).toEqual(["Pressure: timed out"]);
  });

  it("does not write a view whose field the run lacks, and names it; the others come out", async () => {
    const { deps, log } = harness({ apps: { Pressure: { skipped: ["No p in this run"], missingField: "p" } } });
    const result = await exportAllWith(deps);
    expect(result.names).toEqual(["p-r-Velocity.png", "p-r-Front.png"]);
    expect(result.notes).toEqual(["Pressure: No p in this run"]);
    expect(log).not.toContain("export Pressure");
    // The file of the view after it is its own, not the skipped view's.
    expect(log).toContain("download p-r-Front.png (png of Front)");
  });

  it("names the other skipped settings of a view it exported, with a timeout beside them", async () => {
    const { deps } = harness({ apps: { Front: { skipped: ["No T for the iso", "1 slice over the limit"] } }, settled: { Front: false } });
    const result = await exportAllWith(deps);
    expect(result.names).toHaveLength(3);
    expect(result.notes).toEqual(["Front: No T for the iso, 1 slice over the limit, timed out"]);
  });

  it("stops at a view that cannot be read, and the figure that was on screen is still put back (finally)", async () => {
    const { deps, log } = harness({ unreadable: "Pressure" });
    const result = await exportAllWith(deps);
    expect(result.names).toEqual(["p-r-Velocity.png"]);
    expect(result.stopped).toBe("Pressure could not be read");
    expect(log.slice(-2)).toEqual(["apply before", "settle before"]);
  });

  it("stops when the picture cannot be made, with the reason, and puts the figure back", async () => {
    const { deps, log } = harness({ failAt: "Pressure" });
    const result = await exportAllWith(deps);
    expect(result.names).toEqual(["p-r-Velocity.png"]);
    expect(result.stopped).toBe("Out of memory");
    expect(log.slice(-2)).toEqual(["apply before", "settle before"]);
  });

  it("puts nothing back when no figure was on screen", async () => {
    const { deps, log } = harness();
    await exportAllWith({ ...deps, before: null });
    expect(log.filter((l) => l.includes("before"))).toEqual([]);
  });

  it("tells the caller before each view starts", async () => {
    const { deps } = harness();
    const seen: string[] = [];
    await exportAllWith({ ...deps, onProgress: (p) => seen.push(`${p.done}/${p.total} ${p.name}`) });
    expect(seen).toEqual(["0/3 Velocity", "1/3 Pressure", "2/3 Front"]);
  });
});

describe("exportAllSummary", () => {
  it("is plain when everything came out", () => {
    expect(exportAllSummary({ names: ["a", "b"], stopped: "", notes: [] }, 2)).toEqual({ kind: "ok", text: "Exported 2 views", details: [] });
    expect(exportAllSummary({ names: ["a"], stopped: "", notes: [] }, 1).text).toBe("Exported 1 view");
  });

  it("counts the files and lists what was left out", () => {
    const s = exportAllSummary({ names: ["a", "b"], stopped: "", notes: ["Pressure: No p in this run"] }, 3);
    expect(s).toEqual({ kind: "info", text: "2 of 3 exported · Pressure: No p in this run", details: ["2 of 3 exported", "Pressure: No p in this run"] });
  });

  it("is an error when it stopped, and keeps the toast short with the rest in the details", () => {
    const s = exportAllSummary({ names: ["a"], stopped: "Out of memory", notes: ["B: timed out", "C: timed out"] }, 5);
    expect(s.kind).toBe("error");
    expect(s.text).toBe("1 of 5 exported · Out of memory · B: timed out · +1 more");
    expect(s.details).toEqual(["1 of 5 exported", "Out of memory", "B: timed out", "C: timed out"]);
  });
});
