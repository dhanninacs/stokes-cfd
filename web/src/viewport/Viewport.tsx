import { useEffect, useMemo, useRef, useState } from "react";
import type { TagMembers } from "../api/types";
import { runAction } from "../app/actions";
import { isMeshSource, meshIdOfSource } from "../state/meshPatches";
import { pushHistory } from "../state/pick";
import { phaseOfCode, shouldRetry, type ProbeState } from "../panels/probeModel";
import { NO_FIELD } from "../panels/fieldsModel";
import { collectParts } from "../state/parts";
import { findNode, useStore } from "../state/store";
import { gizmoPlacement, subscribeGizmo } from "../workspace/gizmoSlot";
import { useWorkspace } from "../workspace/store";
import { useGuidedMode } from "../guided/modeStore";
import { guidedInset } from "../guided/guidedInset";
import { lineSlicePlane, takeLinePick } from "../guided/lineControl";
import { isLinePicking } from "../panels/lineModel";
import { freeRect } from "./freeRect";
import { effectiveDisplayQuality } from "./displayQuality";
import { THEME_TOKENS_EVENT } from "../workspace/theme";
import ImportProgressLine from "./ImportProgressLine";
import { MeasureReadout } from "./MeasureReadout";
import { MeasurePanel } from "./MeasurePanel";
import { ScaleBar } from "./ScaleBar";
import { ViewButtons } from "./ViewButtons";
import type { Vec3 } from "./measure";
import { MeshPatchesController } from "./meshPatches";
import { FieldsController } from "./fields";
import { IsoLayer } from "./isoLayer";
import { IsoStatusLine } from "./IsoStatusLine";
import { FieldLegend } from "./FieldLegend";
import { legendOf } from "../panels/legendModel";
import { volumeArrowLength, volumeArrowSeeds, volumeArrowsShown } from "../panels/arrowModel";
import { arrowMesh } from "./arrowLayer";

/** The scene key of the volume's arrows, beside the slices' own (`setSliceArrows`). */
const VOLUME_ARROWS_ID = "volume:arrows";
import { PartsController } from "./parts";
import { TopologyController } from "./topologyController";
import type { TagAnchor } from "./glyphs";
import type { ConditionKind, NodeSummary } from "../api/types";
import OperationCard from "../panels/OperationCard";
import FlowCards from "../panels/FlowCards";
import ConditionPanel from "../panels/ConditionPanel";
import { SAMPLE_SOURCE, SceneManager, setCurrentScene, type FrameTiming, type SceneStats } from "./scene";
import { hiddenNodeNames } from "./hiddenNodes";
import { collectTags, TagOverlays } from "./tags";
import { themeFor } from "./theme";
import { faceOnView, twoDOfTree } from "./twoD";
import "./viewport.css";

/**
 * The measurement hook on `window.__stokesScene`, for tools/bench_viewport.py and the review harness:
 * figures and timings only. It holds no reference a caller could use to change the scene, and the
 * app works the same without it.
 */
interface SceneProbe {
  perf(): SceneStats;
  /** Async: one forced frame per animation frame, so the page stays responsive while it runs. */
  measureRender(frames?: number): Promise<FrameTiming>;
  /** Times the frames the scene draws by itself (an orbit, an import) from `startFrames` to `stopFrames`. */
  startFrames(): void;
  stopFrames(): Promise<FrameTiming>;
  measurePick(samples?: number): number;
  /**
   * Milliseconds one topology pick costs in ``mode`` (T-046: the Edge/Point hot path).
   *
   * Separate from `measurePick`, which times the FACE pick: that path does not run the loop this
   * measures, so it could not show a regression or a fix in it (Claude's defect 6).
   */
  measureTopologyPick(mode: "edge" | "vertex", samples?: number): number;
  /** The bodies left out of the simulation, which a box or a lasso skips. */
  excludedParts(): string[];
  /** Screen rectangles of the shown patches, by name. */
  partBounds(): Record<string, { x0: number; y0: number; x1: number; y1: number }>;
  /** The store's live pick (bodies and faces). */
  pick(): { bodies: string[]; faces: Record<string, number[]> };
  /** Milliseconds the last raycast took (ADR-0021 3.2). */
  readonly pickMs: number;
  /** The part and face under a client position through the id buffer, exactly as a click sees it (T-032). */
  idPick(x: number, y: number): { part: string; patch: string; face: number } | null;
  /** Milliseconds the last id-buffer pass and read-back took (T-032). */
  readonly idReadMs: number;
  /** What the streamline object looks like now (figures only), or null when none is drawn. */
  streamlineInfo(): { segments: number; visible: boolean; width: number; resolution: [number, number]; bounds: number[] } | null;
  /**
   * Milliseconds the last pick-overlay rebuild took, and how many picks it drew. This is what a box
   * or lasso release costs on the main thread after the read-back, so T-032's 50 ms budget can be
   * attributed instead of guessed at.
   */
  readonly selectionMs: number;
  readonly selectionPicks: number;
  /**
   * How many times the pick overlay has been rebuilt. A release measures the event handler alone;
   * the overlay usually follows in the next frame, so a caller reading it right after a release
   * must check this counter to know whether the figure belongs to that release or an older one.
   */
  readonly selectionBuilds: number;
  /**
   * What the body pick's overlay has to build: how many bodies share one geometry, and the total
   * triangles. For attributing the overlay cost, not for changing the scene.
   */
  selectionShape(): {
    picks: number;
    distinctGeometries: number;
    triangles: number;
    totalFaces: number;
  };
  /** True while click picks ask the id buffer instead of the raycaster (T-032); off until measured faster. */
  readonly idPickEnabled: boolean;
  setIdPickEnabled(on: boolean): void;
  /**
   * Which mouse button does what under the current scheme (ADR-0021 3.4), by the names three uses:
   * `ROTATE`, `PAN`, `DOLLY`, or `null` for a button the app handles itself. A copy: a caller
   * reads it, and cannot reach the controls through it.
   */
  navButtons(): { LEFT: string | null; MIDDLE: string | null; RIGHT: string | null };
  /**
   * Times one part's construction `n` times over, to attribute the load's apply cost. Measurement
   * only: it builds looks and meshes into nothing and touches no scene state. Returns the per-call
   * milliseconds of each piece and whether the looks came back identical.
   */
  measureBuild(n: number): {
    lookMs: number;
    meshMs: number;
    distinctLooks: number;
    n: number;
  };
  /** Cumulative `flushContent` cost and how many times it ran, for attributing a slow load (T-096). */
  flushStats(): { count: number; ms: number };
  /**
   * The measure tool (T-098): whether it is on, the two points it has recorded in world metres, and
   * the readout it published. Figures only — a caller cannot measure into the scene through it.
   */
  measure(): { on: boolean; a: Vec3 | null; b: Vec3 | null; label: string | null; metres: number | null };
  /**
   * How many parts have a topology record loaded, and how many edges and vertices they hold, plus
   * what a screen-space topology pick at a canvas point finds.
   *
   * Added because "Edge mode selects nothing" was **not** diagnosable from outside: the store's
   * `topologyLoaded` says a record arrived but not whether the hit test can reach it, and the hovered
   * element is component state no script can read. One accessor answers both halves — the records are
   * there, and here is what a pick at this point returns.
   */
  /** The world extent along one axis, in metres: what the section slider's range needs. */
  axisRange(axis: "x" | "y" | "z"): [number, number] | null;
  /** How many clip planes the renderer currently holds: 1 while the section is on, else 0. */
  clippingCount(): number;
  /**
   * The section's cut-face state, for the review harness: how many colour groups are capped, how many
   * mark meshes are in the scene, and whether the quad is showing.
   *
   * Added because "are the caps drawn at all" cannot be judged from a screenshot alone when they may
   * be drawn in the wrong place or in the wrong colour, and the stencil pass has several silent ways
   * to do nothing.
   */
  capsInfo(): { groups: number; marks: number; quad: boolean; active: boolean; rebuilds: number };
  /** The mesh patches and cell edges the scene holds and draws, and the layers its view filter allows (T-211). */
  meshInfo(): {
    patches: number;
    patchesShown: number;
    cellEdges: number;
    cellEdgesDrawn: number;
    geometryLayer: boolean;
    meshLayer: boolean;
  };
  /** Where the section handle is on screen, so a failing colour scan can be diagnosed. */
  handleScreen(): { x: number; y: number; visible: boolean; inFront: boolean } | null;
  /**
   * What a click on an edge or vertex would escalate to (ADR-0022 §15.4).
   *
   * For the review harness: the escalation is a SELECTION, which a harness can otherwise only reach by
   * counting clicks and reading the store — and the interesting part is the wire, whose extent cannot be
   * seen in a screenshot.
   */
  topologyEscalation(
    part: string,
    kind: "edge" | "vertex",
    id: number,
    clicks: number,
  ): { kind: "edge" | "vertex"; ids: number[] };
  /** What the `Alt` + wheel topology cycle is standing on, so a check can assert it moved. */
  cycleStanding(): { part: string; kind: "edge" | "vertex"; id: number; candidates: number } | null;
  /** The world bounds of the shown parts, so a check can cut the model at its own middle. */
  modelBounds(): { min: [number, number, number]; max: [number, number, number] } | null;
  /** Frames the shown parts seen from the direction `(x, y, z)` (the camera goes out along it), so a check can look straight at a cut. */
  fitFrom(x: number, y: number, z: number): void;
  /** The clip plane the renderer holds, for the review harness. */
  planeInfo(): { count: number; normal: number[]; constant: number }[];
  topologyInfo(x?: number, y?: number): {
    parts: number;
    edges: number;
    vertices: number;
    mode: string;
    pick: string | null;
    forced: string | null;
  };
}

/** Development and review builds have the hook; a shipped build only with `?debug=1` in its URL. */
function wantsProbe(): boolean {
  return import.meta.env.DEV || import.meta.env.MODE === "review" || ["debug", "drive"].some((key) => new URLSearchParams(window.location.search).get(key) === "1");
}

/**
 * Tell the store which of a project's parts have their topology record loaded (ADR-0022 §15.4).
 *
 * A free function rather than inline code because **two places** need it, and they are not the same
 * moment: the effect that decides what to want, and each record's arrival. Sampling only in the effect
 * left `topologyLoaded` permanently empty — it runs immediately after `want()`, before any fetch has
 * resolved, so the set it computed was always empty and the readout could never say a conversion was
 * ready. The arrival callback is the one that actually knows.
 */
/**
 * Tell the store which sources have a topology record the pick can actually reach.
 *
 * Two things this gets right that the first version did not (Claude's review):
 *
 * 1. **It filters by what is drawn.** A record for a hidden part cannot be picked — the loops skip
 *    hidden patches — so listing it made the Edge/Point readout promise a part the user could not
 *    click. "Loaded" here means loaded AND pickable.
 * 2. **It does not republish an unchanged Set.** This is called on every topology arrival; the first
 *    version built a new `Set` every time and always called the setter, so every arrival published a
 *    new object identity and every subscriber re-rendered whether or not anything had changed. The
 *    guard is by value, since a Set's identity is new even when its contents are not.
 */
function publishTopologyLoaded(scene: SceneManager): void {
  const loaded = new Set<string>();
  for (const patch of useStore.getState().geometry) {
    if (patch.part === null) continue;
    // Through the scene, which owns the shown flags: a record for a hidden part is loaded but cannot
    // be picked, and reporting it would promise a part the user cannot click.
    if (scene.hasShownTopology(patch.part)) loaded.add(patch.part);
  }
  const current = useStore.getState().topologyLoaded;
  if (current.size === loaded.size && [...loaded].every((part) => current.has(part))) return;
  useStore.getState().setTopologyLoaded(loaded);
}

function probeOf(scene: SceneManager): SceneProbe {
  return Object.freeze({
    perf: () => scene.perf(),
    measureRender: (frames?: number) => scene.measureRender(frames),
    startFrames: () => scene.startFrames(),
    stopFrames: () => scene.stopFrames(),
    measurePick: (samples?: number) => scene.measurePick(samples),
    measureTopologyPick: (mode: "edge" | "vertex", samples?: number) =>
      scene.measureTopologyPick(mode, samples),
    partBounds: () => Object.fromEntries(scene.projectAllPartBounds()),
    excludedParts: () => scene.excludedParts(),
    streamlineInfo: () => scene.streamlineInfo(),
    pick: () => useStore.getState().picked,
    get pickMs() {
      return scene.lastPickMs;
    },
    idPick: (x: number, y: number) => scene.idPickAt(x, y),
    get idReadMs() {
      return scene.lastIdReadMs;
    },
    get selectionMs() {
      return scene.lastSelectionMs;
    },
    get selectionPicks() {
      return scene.lastSelectionPicks;
    },
    get selectionBuilds() {
      return scene.selectionBuilds;
    },
    selectionShape: () => scene.selectionShape(),
    get idPickEnabled() {
      return scene.idPickEnabled;
    },
    setIdPickEnabled: (on: boolean) => scene.setIdPickEnabled(on),
    navButtons: () => scene.navButtons(),
    measureBuild: (n: number) => scene.measureBuild(n),
    /** Cumulative `flushContent` cost and count, for attributing a slow load (T-096). */
    flushStats: () => ({ count: scene.flushCount, ms: scene.flushMs }),
    measure: () => scene.measureState(),
    topologyInfo: (x?: number, y?: number) => scene.topologyInfo(x, y),
    axisRange: (axis: "x" | "y" | "z") => scene.axisRange(axis),
    clippingCount: () => scene.renderer.clippingPlanes?.length ?? 0,
    capsInfo: () => scene.capsInfo(),
    meshInfo: () => scene.meshInfo(),
    modelBounds: () => scene.modelBounds(),
    fitFrom: (x: number, y: number, z: number) => scene.fitFrom(x, y, z),
    handleScreen: () => scene.handleScreen(),
    cycleStanding: () => scene.cycleStanding(),
    topologyEscalation: (
      part: string,
      kind: "edge" | "vertex",
      id: number,
      clicks: number,
    ): { kind: "edge" | "vertex"; ids: number[] } =>
      scene.topologyEscalation(part, kind, id, clicks),
    planeInfo: () => scene.planeInfo(),
  });
}



/** The canvas in its own pixels: the space the projection works in. */
function canvasRect(): { width: number; height: number } {
  const el = document.querySelector("canvas");
  const rect = el?.getBoundingClientRect();
  return { width: Math.max(1, Math.round(rect?.width ?? window.innerWidth)), height: Math.max(1, Math.round(rect?.height ?? window.innerHeight)) };
}

/**
 * How far into the canvas a dock reaches, or 0 when it covers nothing.
 *
 * **The dock's RIGHT EDGE for the left one**, not its width: the dock is inset 12 px from the window,
 * and the free area starts where the dock ENDS. Using the width left a 12 px strip of the model under
 * the dock, which is a small error with a large effect on a fit - the model was 12 px too wide and
 * therefore not centred in the space it had.
 */
function dockEdge(side: "left" | "right"): number {
  const el = document.querySelector(`[data-ws-slot="${side}"]`);
  if (el === null) return 0;
  if (el.getAttribute("data-collapsed") === "true" || el.getAttribute("data-overlay") === "true") return 0;
  /*
    **An EMPTY slot covers nothing, whatever the document says about it.**

    Claude's diagnosis of the owner's first view (2026-10-19 1230) was exactly this: an old document
    holds `layout.right = {collapsed: false, panels: []}` - an empty but OPEN right slot - so this
    function returned its full 320 px and the free rectangle was 320 px narrower than what the user can
    see. The fit framed the model into that phantom space, which is the "flat blue close-up".

    `Layout` renders nothing for a slot with no panels, so checking the DOM for a panel is both the
    direct answer and the one that cannot drift from what is drawn.
  */
  if (el.querySelector("[data-ws-panel]") === null) return 0;
  const rect = el.getBoundingClientRect();
  const canvas = document.querySelector("canvas")?.getBoundingClientRect();
  if (!canvas) return side === "left" ? Math.round(rect.right) : Math.round(window.innerWidth - rect.left);
  // Both expressed against the canvas, which is what the projection measures from.
  return side === "left" ? Math.max(0, Math.round(rect.right - canvas.left)) : Math.max(0, Math.round(canvas.right - rect.left));
}


/** The docks whose size can change the free rectangle. */
const SLOT_SELECTORS = ['[data-ws-slot="left"]', '[data-ws-slot="right"]', '[data-ws-slot="bottom"]'] as const;

/**
 * Ask `run.probe` for a value and put the answer in the store - **retrying while "preparing"**.
 *
 * **The retry is the contract, not a nicety**: the command answers `post.triangulation_preparing`
 * while the surface's triangulation is built in the background, and the alternative (a less accurate
 * number, or a failure) is worse than waiting. The attempts are bounded so a surface that never
 * prepares ends as a message rather than an endless loop.
 */
async function runProbe(
  store: { execute: (name: string, params: Record<string, unknown>) => Promise<unknown>; setProbe: (p: ProbeState) => void },
  patch: string,
  field: string,
  points: [number, number, number][],
): Promise<void> {
  store.setProbe({ phase: "preparing", points, values: [], field, message: "" });
  for (let attempt = 0; attempt < 40; attempt++) {
    try {
      const reply = (await store.execute("run.probe", { patch, field, point: points[0] })) as { values?: number[] };
      store.setProbe({ phase: "ready", points, values: reply.values ?? [], field, message: "" });
      return;
    } catch (err) {
      // **The code may be nested**: the client keeps `code` on `StokesApiError`, but a bare
      // `message` carrying it is still recognisable - which is what the browser showed: the
      // phase came out "failed" WITH the preparing message, so the code never matched.
      const raw = err as { code?: string; message?: string };
      const code = raw.code ?? (raw.message && raw.message.includes("post.triangulation_preparing") ? "post.triangulation_preparing" : null);
      const phase = phaseOfCode(code);
      if (!shouldRetry(phase)) {
        store.setProbe({ phase, points, values: [], field, message: String((err as Error).message ?? err) });
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
  }
  store.setProbe({ phase: "failed", points, values: [], field, message: "the surface did not finish preparing" });
}


/** The `mesh_patch` node named `name` anywhere in the tree, or null. */
function findMeshPatch(tree: NodeSummary | null, drawnName: string): { id: string } | null {
  if (tree === null) return null;
  const stack: NodeSummary[] = [tree];
  while (stack.length > 0) {
    const node = stack.pop()!;
    // **The scene appends " (mesh)"** (`meshPatchName`), so the comparison allows it. Checked as a
    // suffix rather than by building a fake `MeshPatchEntry`, which would need fields this lookup has
    // no business inventing.
    if (node.type === "mesh_patch" && (drawnName === node.name || drawnName === `${node.name} (mesh)`)) {
      return node;
    }
    for (const child of node.children ?? []) stack.push(child);
  }
  return null;
}


export default function Viewport() {
  // Guided mode draws the field legend under its step card (guided/GuidedLayer.tsx), not in its own corner.
  const guided = useGuidedMode() === "guided";
  const guidedRef = useRef(guided);
  guidedRef.current = guided;
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const hostRef = useRef<HTMLDivElement>(null);
  const sceneRef = useRef<SceneManager | null>(null);
  /**
   * The live scene, as **React state** and not only as a ref, so an effect that needs it re-runs when
   * it appears.
   *
   * `sceneRef.current` is set inside an effect, and an effect whose dependency list does not include
   * the scene runs *once* when `client` arrives — at which point the ref is still null, so it returns
   * early and never runs again. That is what stopped Edge mode loading any topology record: the
   * controller was never created (measured: `topologyLoaded` stayed `[]` with all four parts drawn),
   * so a click in Edge mode had nothing to hit and selected nothing. `setCurrentScene` is a plain
   * module variable and causes no re-render, which is why the ref alone was not enough.
   */
  const [scene, setScene] = useState<SceneManager | null>(null);
  /**
   * Why the 3D view could not start, or null. **A blank canvas is not an explanation**: a Linux VM
   * on llvmpipe (or a driver on Chromium's block list) fails WebGL 2 while everything else works,
   * so this puts the reason in the viewport instead of only in the console and a toast.
   */
  const [webglError, setWebglError] = useState<string | null>(null);
  /** The lazy topology fetcher (T-058); null until the client effect has run. */
  const topologyRef = useRef<TopologyController | null>(null);
  /**
   * Each part's mesh signature at the last effect run, so a re-import can be told from an ordinary
   * hover change (A3). The client carries no basis string, so the signature is the transport figures
   * that identify a mesh; a change means the ids may have moved and the record must be refetched.
   */
  const topologyMarks = useRef<Map<string, string>>(new Map());
  const tagsRef = useRef<TagOverlays | null>(null);
  const client = useStore((s) => s.client);
  const theme = useStore((s) => s.theme);
  const selectedPatch = useStore((s) => s.selectedPatch);
  const hoverPatch = useStore((s) => s.hoverPatch);
  const fitRequest = useStore((s) => s.fitRequest);
  const stage = useStore((s) => s.stage);
  const fitAllRequest = useStore((s) => s.fitAllRequest);
  const viewMode = useStore((s) => s.viewMode);
  const section = useStore((s) => s.section);
  const orthographic = useStore((s) => s.orthographic);
  const grid = useWorkspace((s) => s.document.viewport.grid);
  const hiddenPatches = useStore((s) => s.hiddenPatches);
  const hiddenNodes = useStore((s) => s.hiddenNodes);
  const isolatedPatch = useStore((s) => s.isolatedPatch);
  const pickMode = useStore((s) => s.pickMode);
  // The condition kind the value panel is open for (T-044); null while it is closed.
  const conditionKind = useStore((s) => s.conditionKind);
  // The flow helper in progress (T-050), if any.
  const flowRun = useStore((s) => s.flowRun);
  // A stable key for the condition catalogue, so the glyph effect re-runs when it arrives without
  // depending on the array identity (the store hands back the same kinds on every unrelated change).
  const conditionKindsKey = useStore((s) => s.conditionKinds.map((k) => k.id).join(","));
  const paintMode = useStore((s) => s.paintMode);
  const lassoMode = useStore((s) => s.lassoMode);
  const navMode = useStore((s) => s.navMode);
  const orbitMode = useStore((s) => s.orbitMode);
  const picked = useStore((s) => s.picked);
  const geometry = useStore((s) => s.geometry);
  const hover = useStore((s) => s.hover);
  // A5: the edge or vertex under the pointer, drawn as a hover highlight by the overlay.
  const [topologyHover, setTopologyHover] = useState<{ part: string; kind: "edge" | "vertex"; id: number } | null>(null);
  const tree = useStore((s) => s.tree);
  /*
    **Which bodies are a region's domain** (Claude's fit decision (a), 2026-10-19): Fit frames the
    shapes a user recognises, and `region.list` already says which bodies a region holds. Read when the
    tree changes rather than in the render loop, and handed to the scene, where framing happens.
  */
  const execute = useStore((s) => s.execute);
  useEffect(() => {
    let live = true;
    void execute("region.list", {}, { silent: true })
      .then((reply) => {
        const rows = (Array.isArray(reply) ? reply : ((reply as { regions?: { bodies?: string[] }[] }).regions ?? [])) as { bodies?: string[] }[];
        // **Names, not ids**: the scene keys parts by the name a patch carries, so the ids
        // `region.list` reports are resolved through the tree here, where both are known.
        const byId = new Map<string, string>();
        const walk = (n: { id?: string; name?: string; children?: unknown[] }) => {
          if (n.id && n.name) byId.set(n.id, n.name);
          for (const child of (n.children ?? []) as { id?: string; name?: string; children?: unknown[] }[]) walk(child);
        };
        if (tree) walk(tree as never);
        const names = new Set(rows.flatMap((r) => r.bodies ?? []).map((id) => byId.get(id) ?? "").filter((n) => n !== ""));
        if (live) sceneRef.current?.setRegionBodies(names);
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [execute, tree]);
  const setGeometry = useStore((s) => s.setGeometry);
  const setPatchAnchor = useStore((s) => s.setPatchAnchor);
  const setScaleBar = useStore((s) => s.setScaleBar);
  const measureMode = useStore((s) => s.measureMode);
  const probePick = useStore((s) => s.probePick);
  const linePicking = useStore((s) => isLinePicking(s.line.phase));
  const lineA = useStore((s) => s.line.a);
  const lineB = useStore((s) => s.line.b);
  const streamlines = useStore((s) => s.streamlines);
  const arrows = useStore((s) => s.arrows);
  const vectorCloud = useStore((s) => s.vectorCloud);
  // The lines wear the results view's colour map, as the slices and the legend do (R1's open item).
  const streamMap = useStore((s) => legendOf(s.results.view).map);
  const streamReverse = useStore((s) => legendOf(s.results.view).reverse);
  const setMeasureReadout = useStore((s) => s.setMeasureReadout);
  const setHoverInfo = useStore((s) => s.setHoverInfo);

  useEffect(() => {
    const canvas = canvasRef.current;
    const host = hostRef.current;
    if (!canvas || !host) return;
    let scene: SceneManager;
    try {
      scene = new SceneManager(canvas, themeFor(useStore.getState().theme));
    } catch (err) {
      // No WebGL 2 (blocked, or a driver on the block list): the rest of the window stays usable.
      const message = `3D view unavailable: ${(err as Error).message}`;
      setWebglError((err as Error).message);
      useStore.getState().log("error", message);
      useStore.getState().pushToast("error", message);
      return;
    }
    setWebglError(null);
    sceneRef.current = scene;
    // The scene is reachable while it lives: the operation card asks it for a point in the view
    // (`requestPoint`, T-029). Cleared again on the way out, before the scene is disposed.
    setCurrentScene(scene);
    setScene(scene);
    // Tags draw on the scene's parts (ADR-0021 3.5); their faces come from tag.members, through
    // whichever client is connected when a tag appears or its members change.
    const tags = new TagOverlays(scene, async (id) => {
      const state = useStore.getState();
      if (!state.client) return null;
      try {
        return (await state.client.call<{ members: TagMembers }>("tag.members", { tag: id })).members;
      } catch (err) {
        state.log("error", `tag ${id}: ${(err as Error).message}`);
        return null;
      }
    });
    tagsRef.current = tags;
    // The double-click in face mode is the tangent chain of `tag.region`: the very call Grow makes
    // (ContextToolbar's `selection.grow`), so the two can never drift apart. It replaces the pick,
    // and the pick it leaves is kept for `selection.previous`; Grow is older than that rule and
    // knows nothing of it, so the entry is written here. Grow asks the server and answers later, so
    // the entry goes in first: a command that fails costs `selection.previous` one no-op step.
    const growPick = (): void => {
      const s = useStore.getState();
      if (runAction("selection.grow")) useStore.setState({ pickHistory: pushHistory(s.pickHistory, s.picked) });
    };
    scene.onPick = (hit, modifiers) => {
      const s = useStore.getState();
      const how = modifiers.shift ? "add" : modifiers.ctrl ? "toggle" : "replace";
      const clicks = modifiers.clicks;
      if (!hit) {
        if (how === "replace") s.clearPick();
        return;
      }
      // A mesh patch is not a part: a click on one selects its tree row (T-211) and picks nothing.
      const meshId = meshIdOfSource(hit.part);
      if (meshId !== null) {
        s.select(meshId);
        return;
      }
      // The click count escalates what the click picked (ADR-0022 6.2). The first pointerup of the
      // run has already applied `how` to what is under the pointer. A mesh without face ids (the
      // sample part) has no face to grow, and nothing to pick either: it is left as it is.
      if (s.pickMode === "face") {
        if (clicks >= 3) s.pickAllFaces(hit.part, how);
        else if (clicks === 2 && hit.face >= 0) growPick();
        else s.pickFace(hit.part, hit.face, how);
        return;
      }
      if (clicks === 2) s.pickSiblings(hit.part, how, hit.patch);
      else s.pickBody(hit.part, how, hit.patch);
    };
    scene.onHover = hover;
    // The paint stroke (T-043): every face (or body) the pointer crosses during the press is applied
    // at once, Ctrl removing instead of adding. The scene has already reduced the pick, so this adds
    // one store update per new face, never one per face of a mesh.
    scene.onPaint = (hit, ctrl) => {
      if (!hit || isMeshSource(hit.part)) return;
      const s = useStore.getState();
      const how = ctrl ? "remove" : "add";
      if (s.pickMode === "face") s.pickMany([{ part: hit.part, faces: [hit.face] }], how);
      else s.pickMany([{ part: hit.part }], how);
    };
    // Edge and Point mode resolve a click over the topology records in the scene and hand back a
    // part and an id (ADR-0022 §15.3, T-058); the store holds it as the pick.
    scene.onTopologyPick = (part, kind, id, modifiers) => {
      const how = modifiers.shift ? "add" : modifiers.ctrl ? "toggle" : "replace";
      // **The click count escalates** (ADR-0022 §15.4): one is the id, two is the wire through an edge
      // (or a point's incident edges), three is every edge and vertex of the body. The scene resolves
      // it because it holds the record; the bulk pick keeps the all-or-nothing toggle rule in one
      // place. A single click stays on the one-id path, so nothing about it changes.
      // **The escalation names its own kind.** A double-click on a POINT selects the EDGES that meet
      // there, not vertices — so `kind` cannot be carried over from the click: doing that put edge ids
      // into the vertex map and the pick came out empty. Measured before the fix: a double-click on
      // vertex 11 (incident edges `[13, 14, 15]`) left `edges={}`.
      const escalated = scene.topologyEscalation(part, kind, id, modifiers.clicks);
      if (escalated.ids.length > 1) {
        useStore.getState().pickTopologyMany(part, escalated.kind, escalated.ids, how);
      } else {
        useStore.getState().pickTopology(part, kind, id, how);
      }
      if (modifiers.clicks >= 2) {
        // Say what was selected: a wire the user cannot see the extent of is a wire they cannot trust,
        // and the branch count is the part §15.4 says must be reported rather than guessed at.
        const noun = escalated.kind === "edge" ? "edges" : "vertices";
        useStore
          .getState()
          .pushToast(
            "info",
            modifiers.clicks >= 3
              ? `Picked all ${escalated.ids.length} ${noun} of the body`
              : `Picked ${escalated.ids.length} ${noun}`,
          );
      }
    };
    scene.onHoverInfo = (info) => setHoverInfo(info);
    scene.onTopologyHover = (hit) => setTopologyHover(hit);
    // The box and the lasso (T-032) arrive here as one whole result and go through `pickMany`, the
    // same bulk path the paint stroke uses — one store update and one history entry per gesture.
    scene.onPickMany = (items, how) => {
      // Mesh patches are not parts (T-211): a box or a lasso over them picks the parts under it and no patch.
      const notMesh = items.filter((item) => !isMeshSource(item.part));
      /*
        **A box or a lasso skips a body left out of the simulation, and SAYS SO** (Claude's pick rule,
        2026-10-19). Clicking one still picks it, because the way to put it back is its own menu - so
        the two gestures differ on purpose: a click is aimed, a box catches whatever it covers.

        **Saying so is the part that matters**: a body that silently vanishes from a selection is the
        failure a user cannot see, which is why this reports the count rather than filtering quietly.
      */
      /*
        **From the STORE's part nodes, not from the scene** - measured: `setSource` restyles every part
        of an import with the SOURCE's style, so a per-part `simulate` never reaches the scene's parts
        and the scene reported no excluded body on a grouped import at all. The store holds one node
        per part, which is what the rule needs.
      */
      const excluded = new Set(
        collectParts(useStore.getState().tree)
          .filter((part) => !part.simulate)
          .map((part) => part.id),
      );
      const parts = notMesh.filter((item) => !excluded.has(item.part));
      const skipped = notMesh.length - parts.length;
      if (skipped > 0) {
        useStore
          .getState()
          .pushToast("info", `${skipped} left-out ${skipped === 1 ? "body was" : "bodies were"} skipped`);
      }
      if (parts.length > 0 || items.length === 0) useStore.getState().pickMany(parts, how);
    };
    scene.onAnchor = (anchor) => setPatchAnchor(anchor);
    // The scale bar: the scene measures the view at the orbit target and calls back only when the
    // figure changes, so a drag does not re-render the overlay once a frame.
    scene.onScale = (bar) => setScaleBar(bar);
    // The measurement (T-098): the scene owns the world points and publishes where the label goes.
    scene.onMeasure = (readout) => setMeasureReadout(readout);
    scene.onMeasureDetails = (details) => useStore.getState().setMeasureDetails(details);
    /*
      **The probe's point pick** (results item 2): one click, and the value of the current field at
      that point is read with `run.probe`.

      **The patch, not the part, is what the command needs** - `PointHit` carries the patch name, and
      the command addresses a `mesh_patch` node. It also carries the world point, which is the probe's
      own coordinate system (SI metres), so nothing has to be converted here.

      **A "preparing" answer retries by itself** (claude-win2's contract): the surface's
      triangulation is built in the background, and showing "preparing" then failing would teach a
      user to distrust a value that arrives a moment later.
    */
    scene.onProbe = (hit) => {
      const store = useStore.getState();
      /*
        **A line pick takes the click instead of the probe's reading** (R5): the two clicks of the plot over a
        line ride this same one-shot pick, so while one is armed the probe's own readout does not run. The pick
        is spent by the click, so it is armed again for the second point.
      */
      if (isLinePicking(store.line.phase)) {
        takeLinePick(hit, findMeshPatch(store.tree, hit.patch)?.id ?? null);
        scene.setProbePick(isLinePicking(useStore.getState().line.phase));
        return;
      }
      // The pick is spent: the button is not lit for a click that will not come (it needed two presses to re-arm).
      store.setProbePick(false);
      const field = store.results.view.field;
      /*
        **`mesh_patch` nodes, not parts** - measured on the audit's duct: its geometry part is `box`,
        while the surfaces the colours sit on are the patches `inlet`, `outlet` and `walls`. Searching
        `collectParts` therefore found nothing, and the probe answered "outside" for a click squarely
        on the model. `run.probe` addresses a `mesh_patch`, so that is what must be matched.
      */
      /*
        **The scene draws a mesh patch under `meshPatchName`, which appends " (mesh)"** - measured:
        `hit.patch` came back as `"walls (mesh)"` while the tree holds `walls`, which is why every
        lookup matched nothing and the probe said "outside" for a click squarely on the model.

        The suffix is deliberate (`meshPatches.ts`: *"it cannot be a part's, so the selection never
        confuses the two"*), so the lookup uses the SAME function rather than stripping it - one rule
        for the name, in one place.
      */
      const patch = findMeshPatch(store.tree, hit.patch);
      const point: [number, number, number] = [hit.point.x, hit.point.y, hit.point.z];
      if (patch === null || field === NO_FIELD) {
        store.setProbe({ phase: "outside", points: [point], values: [], field, message: "Pick a point on the meshed model" });
        return;
      }
      void runProbe(store, String(patch.id), field, [point]);
    };
    // A drag on the section's handle moves the cut. It goes through the SAME store action the
    // popover's slider uses, so a drag and a slider move are one kind of edit and neither can drift
    // from the other: the scene reports an offset, the store decides what that means.
    scene.onSectionDrag = (offset) => {
      const now = useStore.getState().section;
      if (now === null) return;
      useStore.getState().setSectionPlane(now.axis, offset);
    };
    // A scene made while measure mode is already on has to pick it up: the effect below only runs
    // when the store's flag changes, and this one only when the scene is remade.
    scene.setMeasureMode(useStore.getState().measureMode);
    scene.setProbePick(useStore.getState().probePick || isLinePicking(useStore.getState().line.phase));
    scene.onContent = (patches) => setGeometry(patches);
    const globals = window as unknown as { __stokesScene?: SceneProbe };
    const probe = wantsProbe() ? probeOf(scene) : null;
    if (probe) globals.__stokesScene = probe;
    const observer = new ResizeObserver(() => {
      const rect = host.getBoundingClientRect();
      scene.setSize(Math.max(1, Math.floor(rect.width)), Math.max(1, Math.floor(rect.height)));
    });
    observer.observe(host);
    return () => {
      observer.disconnect();
      tags.dispose();
      tagsRef.current = null;
      if (sceneRef.current === scene) setCurrentScene(null);
      setScene((current) => (current === scene ? null : current));
      scene.dispose();
      sceneRef.current = null;
      if (probe && globals.__stokesScene === probe) delete globals.__stokesScene;
      setPatchAnchor(null);
    };
  }, [hover, setPatchAnchor, setGeometry, setHoverInfo]);

  // Show only project geometry, by part id. Empty projects and imports keep an empty scene.
  useEffect(() => {
    if (!client || !scene) return;
    const controller = new PartsController(client, scene);
    // The boundary patches of the mesh (T-211) are sources of the same scene, drawn in the Mesh stage.
    const meshes = new MeshPatchesController(client, scene);
    // The results colour those patches (T-218): the tint is set once the scene holds a patch.
    const fields = new FieldsController(client, scene);
    // The iso-surface the Results panel made ready (ADR-0026 amendment 1), one at a time.
    const iso = new IsoLayer(client, scene);
    return () => {
      iso.dispose();
      fields.dispose();
      meshes.dispose();
      controller.dispose();
    };
  }, [client, scene]);

  // The lazy edge/vertex topology (ADR-0022 §15.2, T-058): records are fetched one part at a time,
  // only while Edge or Point mode is on, and released when the mode is left. The scene keeps the
  // decoded records; this controller owns the fetch lifecycle.
  useEffect(() => {
    if (scene === null || client === null) return;
    const api = client;
    // Bound locally for the same reason `api` is: the callbacks below capture it, and TypeScript
    // cannot keep the null-narrowing of a `const` that is also a React dependency across a closure.
    const live = scene;
    const topology = new TopologyController({
      fetch: (part, signal) => api.fetchTopology(part, signal),
      // A null state is a removal (A3): the scene must drop the record, or a re-import would keep
      // serving the old edges because nothing ever told it they were gone.
      onChange: (part, state) => {
        live.setTopology(part, state !== null && state.status === "ready" ? state.topology : null);
        // §15.4's pending state: a record ARRIVING is what makes a part loaded, and the effect below
        // only samples the scene synchronously — right after `want()`, before any fetch has resolved.
        // Without this call `topologyLoaded` stayed empty forever (measured), so the readout could
        // never say a conversion was ready.
        publishTopologyLoaded(live);
      },
    });
    topologyRef.current = topology;

    // A6: the pick's mode conversions need the decoded records, which the scene owns. These
    // accessors return null when a part's record is not loaded, so `convertPick` omits that part and
    // the UI can say it is pending rather than silently emptying the pick (§15.4).
    useStore.getState().setConversionTopology({
      verticesOfEdge: (part, edge) => {
        const record = scene.topologyOf(part);
        if (record === undefined) return null;
        if (!record.edges.some((item) => item.id === edge)) return null;
        // The record states its own incidence: every vertex lists the edges that meet it. Matching
        // the edge's start and end against vertex positions would be the proximity guess the review
        // had me remove from the server (A9) — reintroduced on the client, and it would silently
        // lose the endpoint wherever a STEP vertex tolerance moved the sampled position.
        const out: number[] = [];
        for (const vertex of record.vertices) {
          if (vertex.edges.includes(edge)) out.push(vertex.id);
        }
        return out;
      },
      edgesOfVertex: (part, vertex) => {
        const record = scene.topologyOf(part);
        if (record === undefined) return null;
        const found = record.vertices.find((item) => item.id === vertex);
        return found === undefined ? null : [...found.edges];
      },
      allEdges: (part) => {
        const record = scene.topologyOf(part);
        return record === undefined ? null : record.edges.map((item) => item.id);
      },
      // T-124a: the record now carries each edge's faces, so an edge converts to the faces it
      // borders (one or two). A vertex converts to the union of its incident edges' faces.
      facesOfEdges: (part, edges) => {
        const record = scene.topologyOf(part);
        if (record === undefined) return null;
        const out: number[] = [];
        for (const edge of edges) {
          const found = record.edges.find((item) => item.id === edge);
          if (found !== undefined) out.push(...found.faces);
        }
        return [...new Set(out)].sort((a, b) => a - b);
      },
      facesOfVertices: (part, vertices) => {
        const record = scene.topologyOf(part);
        if (record === undefined) return [];
        const out: number[] = [];
        for (const id of vertices) {
          const vertex = record.vertices.find((item) => item.id === id);
          if (vertex === undefined) continue;
          for (const edge of vertex.edges) {
            const found = record.edges.find((item) => item.id === edge);
            if (found !== undefined) out.push(...found.faces);
          }
        }
        return [...new Set(out)].sort((a, b) => a - b);
      },
    });

    return () => {
      topology.clear();
      topologyRef.current = null;
      useStore.getState().setConversionTopology(null);
    };
  }, [client, scene]);

  // Enter and leave the mode with the store's pick mode, and ask for the visible parts there.
  useEffect(() => {
    const topology = topologyRef.current;
    if (topology === null || scene === null) return;
    const live = scene;
    // **Measure mode needs the record too, whatever the pick mode.** A face's true area lives in the
    // record, so asking for it in Face mode with no record loaded found nothing and the click fell
    // back to a distance — measured on the demo as `topologyInfo: {"parts":0}` while the Measure
    // toggle was on. The record is what makes an edge's length, a face's area and a body's volume
    // available at all, so it is wanted while measuring as well as while picking topology.
    const wantsTopology = pickMode === "edge" || pickMode === "point" || measureMode;
    topology.setActive(wantsTopology);
    if (!wantsTopology) return;

    const visible = geometry.map((patch) => patch.part).filter((id): id is string => typeof id === "string" && !isMeshSource(id));

    // A3: a part whose MESH changed may have had its face and edge ids renumbered, so its record is
    // no longer valid and would be served stale. The client has no basis string, so the change is
    // detected by the transport figures that identify a mesh — its triangle and face counts and the
    // offsets they live at. A part that disappeared is forgotten for the same reason; nothing else
    // tells the controller, and without this a re-import kept serving the old edges.
    const signature = new Map<string, string>();
    for (const patch of geometry) {
      if (patch.part === null || isMeshSource(patch.part)) continue;
      const mark = `${patch.offset}:${patch.triangles}:${patch.faces}:${patch.faceBase}`;
      signature.set(patch.part, `${signature.get(patch.part) ?? ""}|${patch.name}:${mark}`);
    }
    const previous = topologyMarks.current;
    for (const part of previous.keys()) {
      if (!signature.has(part)) topology.forget(part);
      else if (previous.get(part) !== signature.get(part)) topology.forget(part);
    }
    topologyMarks.current = signature;

    topology.want(visible, hoverPatch ?? selectedPatch);

    // §15.4's pending state (A6): tell the store which parts' records are in, so the readout can say
    // which parts a conversion is still waiting for rather than leaving a pick that quietly emptied.
    publishTopologyLoaded(live);
  }, [pickMode, measureMode, geometry, hoverPatch, selectedPatch, scene]);

  // The document root receives data-theme from App in an effect that runs after this one. The
  // palette reads the CSS tokens, so it waits one frame for the attribute to be in place.
  useEffect(() => {
    const id = requestAnimationFrame(() => sceneRef.current?.setTheme(themeFor(theme)));
    return () => cancelAnimationFrame(id);
  }, [theme]);

  // A custom theme changes tokens without changing `data-theme`: the colours are read again when the
  // tokens are (the event goes out after they are on <html>, so they can be read at once).
  useEffect(() => {
    const onTokens = () => sceneRef.current?.setTheme(themeFor(useStore.getState().theme));
    window.addEventListener(THEME_TOKENS_EVENT, onTokens);
    return () => window.removeEventListener(THEME_TOKENS_EVENT, onTokens);
  }, []);

  /*
    **The model centres in the FREE area, not the whole window** (claude-win2's 1930, the owner's third
    layout request): the canvas is full-window behind the cards, so a model centred on the window sat
    under the tree and the pane.

    **Measured from the docks themselves rather than from the layout document.** The document holds a
    width, but the dock may be collapsed (0), overlaid because the window is narrow (0), or mid-drag at
    a size the document has not been told yet - and the DOM is the one place all four answers already
    exist. A `ResizeObserver` on each dock reports all of them, including a divider drag.

    **The measurement is of what COVERS the canvas**, so a collapsed dock reports 0 and an overlaid one
    reports 0 too - the overlay floats over the model rather than narrowing the space it may use, which
    is what "overlay" means.
  */
  useEffect(() => {
    // **Depends on the SCENE**, not just on mount: a scene is remade when the theme or the WebGL
    // context changes, and a rectangle applied to the old rig would leave the new camera centred on the
    // window again.
    if (scene === null) return;
    let frame = 0;
    let previous = "";
    const observer = new ResizeObserver(() => schedule());
    const observed = new Set<Element>();
    const apply = () => {
      frame = 0;
      const live = sceneRef.current;
      if (live === null) return;
      // Docks can mount after the scene; observe new elements as the layout arrives.
      for (const selector of [...SLOT_SELECTORS, "canvas"]) {
        const el = document.querySelector(selector);
        if (el && !observed.has(el)) { observer.observe(el); observed.add(el); }
      }
      // Guided mode's step card (left column) and strip (top row) cover the canvas too: the fit stays clear of them.
      const inset = guidedInset(guidedRef.current ? "guided" : "full");
      const rect = freeRect({ left: dockEdge("left") + inset.left, right: dockEdge("right"),
        top: inset.top, bottom: 0, window: canvasRect() });
      const key = JSON.stringify(rect);
      if (key === previous) return;
      previous = key;
      live.rig.setFreeRect(rect);
    };
    const schedule = () => { if (!frame) frame = requestAnimationFrame(apply); };
    const mounts = new MutationObserver(schedule);
    mounts.observe(document.querySelector("main") ?? document.body, { childList: true, subtree: true });
    apply();
    window.addEventListener("resize", schedule);
    return () => {
      observer.disconnect();
      mounts.disconnect();
      cancelAnimationFrame(frame);
      window.removeEventListener("resize", schedule);
    };
  }, [scene, guided]);

  // The workspace's `viewport.grid`, and the corner it gives the view cube (or its hiding).
  useEffect(() => {
    sceneRef.current?.setGrid(grid);
  }, [grid]);

  /*
    **The display switches** (the owner, via claude-win2 2000). One effect for all six, because they
    are one decision - "what is drawn besides the model" - and six effects would each be a separate
    reason for the scene to be told something.

    `gizmo` and `scaleBar` are DOM rather than three.js, so they are applied where those live; the
    four that belong to the scene go through its own setters.
  */
  const display = useWorkspace((s) => s.document.viewport);
  useEffect(() => {
    const scene = sceneRef.current;
    if (scene === null) return;
    const quality = effectiveDisplayQuality(display);
    scene.setAxes(quality.axes);
    scene.setShadow(quality.shadow);
    scene.setReflections(quality.reflections);
    scene.setEdges(quality.edges);
  }, [display, scene]);

  useEffect(() => {
    // **Switched off is a PLACEMENT**, not a separate mechanism: `setGizmo` already decides whether
    // and where the gizmo draws, so "hidden" is one more answer to the question it asks - which
    // keeps one route to the gizmo rather than a flag beside it.
    const place = () => sceneRef.current?.setGizmo(display.gizmo === false ? null : gizmoPlacement());
    place();
    return subscribeGizmo(place);
  }, [display.gizmo]);

  useEffect(() => {
    sceneRef.current?.setSelected(selectedPatch);
  }, [selectedPatch]);

  // The candidates `Alt` + wheel cycles and the hover text are reduced by the mode (ADR-0022 6.1).
  useEffect(() => {
    sceneRef.current?.setPickMode(pickMode);
  }, [pickMode]);

  // Measure (T-098): while it is on, a click on the model records a point and two of them become a
  // distance. Leaving the mode — the island's toggle, `M` again, or Esc — clears the measurement.
  useEffect(() => {
    sceneRef.current?.setMeasureMode(measureMode);
  }, [measureMode]);

  // The Measure panel's Clear: a new measurement in the same tool.
  const measureReset = useStore((s) => s.measureReset);
  useEffect(() => {
    if (measureReset > 0) sceneRef.current?.resetMeasure();
  }, [measureReset]);

  /*
    **The streamlines follow the store** (results item 4). The panel computes them; this draws them.
    The effect depends on the `streamlines` slice of the store, which is REPLACED when the panel sets
    a new answer - so a redraw is one object swap, and `setStreamlines` disposes the old one.
  */
  useEffect(() => {
    sceneRef.current?.setStreamlines(streamlines.lines, streamlines.range, streamMap, streamReverse, streamlines.tubes === true);
  }, [streamlines, streamMap, streamReverse]);

  /*
    **The volume's arrows follow the store** (`run.vectors`, 2026-11-04): an even lattice through the fluid, one
    instanced mesh keyed beside the slices' arrows. The length is the lattice step's, so neighbours do not overlap.
  */
  useEffect(() => {
    const scene = sceneRef.current;
    if (scene === null) return;
    if (!volumeArrowsShown(arrows) || vectorCloud.phase !== "ready") {
      scene.setSliceArrows(VOLUME_ARROWS_ID, null);
      return;
    }
    const seeds = volumeArrowSeeds(vectorCloud.points, vectorCloud.vectors);
    const speeds = seeds.map((s) => s.speed);
    const range = speeds.length === 0 ? { min: 0, max: 0 } : { min: Math.min(...speeds), max: Math.max(...speeds) };
    scene.setSliceArrows(VOLUME_ARROWS_ID, arrowMesh(seeds, volumeArrowLength(vectorCloud.spacing, arrows.scale), range, streamMap, streamReverse));
  }, [arrows, vectorCloud, streamMap, streamReverse]);

  // The probe (results item 2): one click, one value, through the same one-shot point pick.
  useEffect(() => {
    sceneRef.current?.setProbePick(probePick || linePicking);
    // A line pick on the slice lands on the slice's plane even where no part is under the cursor
    // (looking along a pipe at its mid-plane disc, say); the probe's own pick keeps needing a face.
    sceneRef.current?.setPointPlane(linePicking ? lineSlicePlane() : null);
  }, [probePick, linePicking]);

  // The plot over a line (R5): its segment and end markers, from the store's two points.
  useEffect(() => {
    sceneRef.current?.setPlotLine(lineA, lineB);
  }, [lineA, lineB, scene]);

  // Paint select (T-043): the crosshair, and the stroke that holds the camera still instead of orbiting.
  useEffect(() => {
    sceneRef.current?.setPaintMode(paintMode);
  }, [paintMode]);

  // Lasso instead of a box on the next drag on empty space (T-032).
  useEffect(() => {
    sceneRef.current?.setLassoMode(lassoMode);
  }, [lassoMode]);

  // The mouse scheme (ADR-0021 3.4): the rig's buttons and what a left drag means. Both the scheme
  // and the Select/Orbit switch go together, because `toggle` reads the switch.
  useEffect(() => {
    sceneRef.current?.setNavMode(navMode, orbitMode);
  }, [navMode, orbitMode]);

  // Nothing is selectable in Results (the surface under the pointer is a slice or a cut), so under the
  // CAD and Blender schemes a left drag there drew a box and the camera never moved.
  useEffect(() => {
    sceneRef.current?.setOrbitOnly(stage === "Results");
  }, [stage]);

  useEffect(() => {
    sceneRef.current?.setHover(hoverPatch);
  }, [hoverPatch]);

  // The boundary glyphs (ADR-0022 §8.1, T-046): fetched from `tag.anchor` when the boundaries or
  // the tree change, and rescaled as the camera moves so each keeps a constant size on screen.
  useEffect(() => {
    const scene = sceneRef.current;
    if (!scene || client === null) return;
    const api = client;
    const kinds = useStore.getState().conditionKinds;
    if (kinds.length === 0) return;
    // **No project, no anchors.** `tag.anchor` reads the project's tags, so with nothing open it
    // refuses with `api.no_project` - and logging that as an error puts a red dot on the Console
    // for a state the user is deliberately in (the Start screen). The kinds are empty anyway when
    // there is no project, so this is a guard against a race rather than a behaviour change: the
    // kinds and the project arrive in separate messages.
    if (useStore.getState().project === null) return;
    let cancelled = false;
    void api
      .call<{ anchors: TagAnchor[] }>("tag.anchor")
      .then((answer) => {
        if (cancelled) return;
        // Each boundary's tag and kind, so a glyph knows its direction and colour.
        const owners = new Map<string, ConditionKind>();
        const byTag = new Map(kinds.map((k) => [k.id, k]));
        const walk = (node: NodeSummary | null): void => {
          if (node === null) return;
          if (node.type === "boundary") {
            const tagId = node.params.tag;
            const kindId = node.params.kind;
            const kind = typeof kindId === "string" ? byTag.get(kindId) : undefined;
            const tag = typeof tagId === "string" ? findNode(useStore.getState().tree, tagId) : null;
            if (kind !== undefined && tag !== null) owners.set(tag.path, kind);
          }
          for (const child of node.children ?? []) walk(child);
        };
        walk(useStore.getState().tree);
        scene.setBoundaryGlyphs(answer.anchors ?? [], owners);
      })
      .catch((err: unknown) => {
        useStore.getState().log("error", `tag.anchor: ${(err as Error).message}`);
      });
    return () => {
      cancelled = true;
    };
  }, [client, tree, conditionKindsKey]);

  // Rescale on a camera move, so the glyphs keep their on-screen size (the only per-frame work).
  useEffect(() => {
    const scene = sceneRef.current;
    if (!scene) return;
    const rescale = () => scene.rescaleGlyphs();
    scene.onCameraMove = rescale;
    return () => {
      scene.onCameraMove = undefined;
    };
  }, []);

  // The edge/vertex overlay (ADR-0022 §15.3, T-058). In Edge and Point mode the scene draws what the
  // pick holds; leaving the mode clears it, so body and face mode draw nothing extra. The hovered
  // item is taken from the store's hover patch only when that patch is the part the pick is on, so a
  // hover over another body never lights an edge on this one.
  useEffect(() => {
    if (!scene) return;
    if (pickMode !== "edge" && pickMode !== "point") {
      scene.setTopologyMode(null);
      scene.clearTopologyPick();
      return;
    }
    scene.setTopologyMode(pickMode === "point" ? "vertex" : "edge");
    const edges = Object.entries(picked.edges).flatMap(([part, ids]) => ids.map((id) => ({ part, id })));
    const vertices = Object.entries(picked.vertices).flatMap(([part, ids]) => ids.map((id) => ({ part, id })));
    // A5: the hovered element is drawn as well as the picked ones. Before this the hover set was
    // always empty, so §15.3's hover feedback never appeared and only a click showed anything.
    const hovered =
      topologyHover === null || topologyHover.part === undefined
        ? {}
        : topologyHover.kind === "edge"
          ? { edges: [{ part: topologyHover.part, id: topologyHover.id }] }
          : { vertices: [{ part: topologyHover.part, id: topologyHover.id }] };
    scene.setTopologyPick(
      hovered,
      pickMode === "edge" ? { edges } : { vertices },
    );
  }, [pickMode, picked.edges, picked.vertices, topologyHover, scene]);

  // The scene draws the store's pick: the picked faces in face mode, the picked bodies (all their
  // faces) other than the active one in body mode (ADR-0021 3.5).
  useEffect(() => {
    const scene = sceneRef.current;
    if (!scene) return;
    if (pickMode === "face") {
      const faces = new Map<string, Set<number>>();
      for (const [part, ids] of Object.entries(picked.faces)) if (ids.length) faces.set(part, new Set(ids));
      scene.setFaceSelection(faces);
      scene.setBodySelection(null, []);
      return;
    }
    scene.setFaceSelection(new Map<string, ReadonlySet<number>>());
    // One pass over the patches to index them by part, then one lookup per picked body. The nested
    // loop this replaces scanned all 4,500 patches for each of 3,625 picked bodies — about 16
    // million iterations, which is where a wide box select spent most of its release (2026-10-06).
    const byPart = new Map<string | null, string[]>();
    for (const meta of geometry) {
      const key = meta.part ?? null;
      const names = byPart.get(key);
      if (names === undefined) byPart.set(key, [meta.name]);
      else names.push(meta.name);
    }
    const candidates = byPart.get(SAMPLE_SOURCE) ?? [];
    const others: string[] = [];
    for (const partId of picked.bodies) {
      for (const name of byPart.get(partId) ?? candidates) {
        if (name !== selectedPatch) others.push(name);
      }
    }
    scene.setBodySelection(selectedPatch, others);
  }, [pickMode, picked, selectedPatch, geometry]);

  // The tags the tree shows: the visible ones colour their faces, and a tag whose eye is off hides
  // its faces on the parts that hold them (ADR-0021 3.5).
  useEffect(() => {
    tagsRef.current?.setTags(collectTags(tree));
  }, [tree]);

  useEffect(() => {
    sceneRef.current?.setViewMode(viewMode);
  }, [viewMode]);

  // The section (T-065): `null` clears the cut, a plane sets or moves it. View state like the view
  // mode above, so it follows the store rather than an API command.
  useEffect(() => {
    sceneRef.current?.setSection(section);
  }, [section]);

  useEffect(() => {
    sceneRef.current?.setOrthographic(orthographic);
  }, [orthographic]);

  // **2D results** (ADR-0031): in Results a 2D project is seen face-on, orthographic, with rotation off; leaving
  // Results (or turning 2D off) gives rotation back. Keyed on the plane, not the tree, so an edit elsewhere does
  // not re-frame the picture.
  const plane2d = useMemo(() => twoDOfTree(tree)?.plane ?? null, [tree]);
  const locked2d = stage === "Results" && plane2d !== null;
  useEffect(() => {
    const scene = sceneRef.current;
    if (scene === null) return;
    if (!locked2d || plane2d === null) {
      scene.lockToPlane(null);
      return;
    }
    if (!useStore.getState().orthographic) useStore.getState().setOrthographic(true);
    scene.lockToPlane(faceOnView(plane2d));
  }, [locked2d, plane2d]);

  useEffect(() => {
    // **The tree's eye and the viewport menu drive the same thing** (claude-win2's 2000 item 2):
    // `hiddenPatches` holds the patch names hidden from the viewport's own menus, and `hiddenNodes`
    // the part and tag NODE ids hidden from the tree's eye. `setVisibility` matches on the drawn
    // part's NAME, so the ids are resolved here - the tree knows nodes, the scene knows names.
    const names = hiddenNodeNames(tree, hiddenNodes);
    sceneRef.current?.setVisibility([...hiddenPatches, ...names], isolatedPatch);
  }, [hiddenPatches, isolatedPatch, hiddenNodes, tree]);

  useEffect(() => {
    // **`F` fits the shape; `Shift+F` fits every body** (Claude's fit decision (a)): the fluid is the
    // CFD domain, so a user who wants it framed asks for it.
    if (fitRequest > 0) sceneRef.current?.fit();
  }, [fitRequest]);

  useEffect(() => {
    if (fitAllRequest > 0) sceneRef.current?.fitAll();
  }, [fitAllRequest]);

  return (
    <div ref={hostRef} className="viewport-bg absolute inset-0">
      <canvas ref={canvasRef} className="block h-full w-full outline-none" tabIndex={0} />
      {webglError !== null && (
        <div className="viewport-webgl-error" role="status" aria-live="polite">
          <strong>3D view unavailable on this display</strong>
          <p>
            The window and its commands still work. Stokes needs WebGL 2, which this graphics
            driver did not provide.
          </p>
          <code>{webglError}</code>
          <p className="viewport-webgl-error-hint">
            On a Linux machine, check the GPU driver. On a virtual machine or a blocklisted
            driver, start Stokes with <code>--software-webgl</code> to run WebGL on SwiftShader.
          </p>
        </div>
      )}
      <ImportProgressLine />
      {/* The scale (T-097): a round length at the size it is drawn, so the model's size is readable
          without opening the tree. */}
      {/* The owner's Scale bar switch; the component draws nothing when it is off. */}
      {display.scaleBar !== false && <ScaleBar />}
      {/* The standard views as named buttons against the view cube (hidden with it). */}
      <ViewButtons shown={display.gizmo !== false} />
      {/* The legend of the field that colours the mesh in the Results stage (T-218). In Guided mode the
          guided layer draws it under the step card instead, so the two never sit in the same corner. */}
      {!guided && <FieldLegend />}
      {/* The iso-surface in the scene: its field, value and triangle count, while it is drawn. */}
      <IsoStatusLine />
      {/* The measurement (T-098): the distance at the middle of the line it belongs to. */}
      <MeasureReadout />
      <MeasurePanel />
      {/* The operation card (T-029) floats at the right of the viewport, clear of the panels. */}
      <OperationCard />
      {/* The two flow helpers (ADR-0022 §10.2, T-050): a small panel above the operation card they
          drive, so the operation itself still runs through the one card implementation. */}
      <FlowCards
        run={
          flowRun === null
            ? null
            : {
                id: flowRun.id,
                template: flowRun.template,
                target: flowRun.target,
                external: { velocity: flowRun.velocity, addDefaults: flowRun.addDefaults },
              }
        }
        onClose={() => useStore.setState({ flowRun: null })}
      />
      {/* The value panel (ADR-0022 §7.1, T-044): the one form for assigning a condition, opened by
          the Assign menu. Without an anchor it docks at the right, where the operation card sits. */}
      <ConditionPanel kind={conditionKind} anchor={null} onClose={() => useStore.setState({ conditionKind: null })} />
    </div>
  );
}
