import { create } from "zustand";
import { StokesClient, StokesApiError, type ClientConfig } from "../api/client";
import type {
  CommandSchema,
  ConditionKind,
  SetupCheck,
  EngineInfo,
  GeometryEvent,
  ImportFailedEvent,
  LicenseInfo,
  NodeSummary,
  NodeTypeSchema,
  ProjectInfo,
  TagMembers,
  TagRegionMember,
  UnitsSchema,
} from "../api/types";
import type { PatchMeta } from "../viewport/geometry";
import { stageExpanded, stageLinked, stageOfPath } from "../workspace/stageMap";
import { kindsRefreshWanted } from "./kindsRefresh";
import { toggleTopology } from "./pick";
import { defaultStageExpansion } from "./treeExpansion";
// The tag-member rules live in their own pure module so the node test runner can reach them: this
// file imports `window` through the tree sync, and the rules had no test at all until they moved.
// Re-exported here because callers (the toolbar, the panels) import them from the store.
import { idRanges as faceRanges, pickAsMembers, type PickMember, type PickMembers } from "./tagMembers";
export { SAMPLE_SOURCE, faceRanges, pickAsMembers, type PickMember, type PickMembers };
import {
  invertSection,
  moveSection,
  sectionOnProjectChange,
  sectionAt,
  toggleSection,
  type SectionAxis,
  type SectionPlane,
} from "./sectionState";
import type { ConversionTopology } from "./pick";
import { activePatch, applyMany, convertPick, NO_PICK, pickAfterBodies, pickAfterBody, pickAfterFace, pickAfterFaces, pickAfterTopology, pickInMode, pushHistory, samePicked, siblingsOf, allFacesOf, type PickHow, type PickMode, type Picked } from "./pick";
import { basename, markAllStale, noteStarted, patchForNode, readGeometryEvent, type ImportProgress, type PartEntry, type PartNode } from "./parts";
import {
  dropOperation,
  noteOperationProgress,
  readOperationEvent,
  readOperationTypes,
  type OperationEvent,
  type OperationProgress,
  type OperationTypeInfo,
} from "./operations";
import { applyMeshEvent, NO_LIVE as NO_MESH_LIVE, readMeshEvent, type MeshLive } from "../panels/meshPanelModel";
import type { TasksReply } from "../panels/meshTasks";
import { readRunEvent } from "../panels/runApi";
import { NO_RUN, reduceRunEvent, type RunLive } from "../panels/runPanelModel";
import { NO_RESULTS, type ResultsLive } from "../panels/fieldsModel";
import { NO_LINE, type LineState } from "../panels/lineModel";
import { NO_PROBE, type ProbeState } from "../panels/probeModel";
import { NO_STREAMLINES, type StreamlineState } from "../panels/streamlineModel";
import { NO_VECTOR_CLOUD, type ArrowView, type VectorCloud } from "../panels/arrowModel";
import { NO_LOADING, type LoadingCounts } from "./loadingState";
import { NO_SLICES, type SliceList } from "../panels/sliceModel";
import { NO_ISO, NO_ISO_DRAWN, type IsoDrawn, type IsoView } from "../panels/isoModel";
import { NO_SELECTION, extend, selectOne, selectSiblings as selectSiblingsOf, toggle, type Selection, type VisibleRow } from "./multiSelect";
import { indexFor, nodeAtIndexedPath } from "./treePatch";
import { selectKeepsStage, type MeshDisplay } from "./meshPatches";
import { createTreeSync, type LogLine, type TreeSync } from "./treeSync";

export type Stage = "Geometry" | "Mesh" | "Physics" | "Solve" | "Results" | "Explore";
export const STAGES: Stage[] = ["Geometry", "Mesh", "Physics", "Solve", "Results", "Explore"];

/**
 * The stage a node path belongs to, or null when no stage works in its group. Which groups a stage
 * works in is the workspace's (`tree.stages`, kept in workspace/stageMap.ts): the stage chips and
 * the tree navigate each other through it. The current stage wins when it owns the path too, so a
 * node under /runs (Solve starts a run, Results reads its output) does not move Results to Solve.
 */
export function stageForPath(path: string, current: Stage): Stage | null {
  const found = stageOfPath(path, current, STAGES);
  return STAGES.find((stage) => stage === found) ?? null;
}

export type DrawerTab = "console" | "log" | "residuals" | "reports";
export type ViewMode = "shaded" | "edges" | "wireframe";

// The section's types and rules live in a module with no browser imports (`sectionState.ts`) so they
// can be tested: this file imports `treeSync.ts`, which touches `window` at module scope, and Vitest
// runs in `node`. Re-exported here because the store is where the rest of the client looks for them.
export { type SectionAxis, type SectionPlane } from "./sectionState";
/**
 * The mouse scheme (ADR-0021 3.4): which button rotates, pans and zooms, and what a left drag means.
 * `cad` rotates on the middle button and leaves the left one for selecting and the box; `paraview`
 * rotates on the left and moves the box to Shift; `blender` is the middle button with Shift+middle
 * panning; `toggle` puts a Select/Orbit switch on the left. The list and the wording are in
 * `app/actions.ts` (`NAV_MODES`), and `viewport/scene.ts` applies them to the rig.
 */
export type NavMode = "cad" | "paraview" | "blender" | "toggle";
// The pick's types and its rules live in state/pick.ts (ADR-0022 decision 14), re-exported here so
// the other modules keep importing them from the store.
export type { PickHow, PickMode, Picked } from "./pick";
// The scale bar's shape is the viewport's (`viewport/scale.ts`); the store only carries it, so the
// overlay can render it without reaching into the scene. Imported for this file's own use and
// re-exported for the components that read it.
import type { ScaleBar } from "../viewport/scale";
export type { ScaleBar } from "../viewport/scale";
// The measurement's readout shape is the viewport's too (`viewport/measure.ts`), carried the same
// way: the scene measures and publishes, the overlay renders, the store holds neither end.
import type { MeasureDetails, MeasureReadout } from "../viewport/measure";
/** How long `setup.check` waits after the last change (ADR-0022 §11.1). */
const SETUP_CHECK_DEBOUNCE_MS = 250;
/** The pending debounce, module-level so `refreshSetup` needs no per-store field. */
let setupTimer: ReturnType<typeof setTimeout> | 0 = 0;

export type { MeasureReadout } from "../viewport/measure";
/** The source id of the sample part, as the viewport scene uses it (`SAMPLE_SOURCE` in viewport/scene.ts). */
const SAMPLE_SOURCE = "@sample";
/**
 * A theme is a style family times an appearance: the Stokes family (dark is the reference look,
 * light its counterpart), Cupertino, the macOS-inspired family, or Ubuntu, the Yaru family, each
 * in dark and light.
 */
export type Theme = "dark" | "light" | "cupertino" | "cupertino-dark" | "ubuntu" | "ubuntu-dark";
export const THEMES: Theme[] = ["dark", "light", "cupertino", "cupertino-dark", "ubuntu", "ubuntu-dark"];
export type ThemeFamily = "stokes" | "cupertino" | "ubuntu";
export type Appearance = "dark" | "light";

export function themeFamily(theme: Theme): ThemeFamily {
  if (theme.startsWith("cupertino")) return "cupertino";
  if (theme.startsWith("ubuntu")) return "ubuntu";
  return "stokes";
}
export function themeAppearance(theme: Theme): Appearance {
  return theme === "dark" || theme === "cupertino-dark" || theme === "ubuntu-dark" ? "dark" : "light";
}
export function composeTheme(family: ThemeFamily, appearance: Appearance): Theme {
  if (family === "cupertino") return appearance === "dark" ? "cupertino-dark" : "cupertino";
  if (family === "ubuntu") return appearance === "dark" ? "ubuntu-dark" : "ubuntu";
  return appearance;
}
export interface ScreenAnchor {
  /** Horizontal middle of the selection's projected bounds. */
  x: number;
  /** Top of the projected bounds: the context toolbar sits above this edge. */
  y: number;
  /** Bottom of the projected bounds: where the toolbar goes when there is no room above. */
  bottom?: number;
}

/**
 * What the pointer is over, as the viewport reports it (ADR-0022 6.1): the status strip shows
 * "2 of 3 under the pointer" once T-049 draws it. The label is the part's patch name, or the face
 * under the pointer in face mode ("face 12 of part_3"); `index` is the candidate's place in the
 * list under the pointer, 0 for the nearest, of `count` candidates (`Alt` + wheel walks them).
 */
export interface HoverInfo {
  label: string;
  index: number;
  count: number;
}

export interface ConsoleLine {
  id: number;
  kind: "command" | "result" | "error" | "event" | "info";
  text: string;
  at: number;
}

/**
 * The operation card that is open (T-029), one at a time. The card is a form of the type's schema:
 * what the pick held when it opened is kept here, so the form does not move under the user while
 * they fill it in, and the run's own state (the node it made, the message it failed with) comes
 * back here from the `geometry.op_*` events.
 */
export interface OperationDraft {
  /** Counts the cards opened: the form is rebuilt (defaults, presets) when a new one opens. */
  id: number;
  /** The operation type: the key of `operationTypes`. */
  type: string;
  /** The parameter values the menu entry presets (a boolean's `op`). */
  params: Record<string, unknown>;
  /** The parts the pick held, in pick order (of a face pick: the parts its faces are on). */
  parts: string[];
  /** The picked faces as tag members, when the pick held faces. */
  faces: Array<{ part: string; faces: "all" | Array<number | [number, number]> }> | null;
  /** The node `operation.create` made, once Run answered. */
  nodeId: string | null;
  /** The message of the run's last failure, shown in the card's error line. */
  error: string;
  /**
   * True when that failure was the user's own Cancel (`geometry.cancelled`): the card says it in
   * the quiet line, not the red one, because pressing Cancel is not a failure.
   */
  cancelled: boolean;
}

/** A button inside a toast ("Undo"); the toast closes once it has run. */
export interface ToastAction {
  label: string;
  run(): void;
}
export interface Toast {
  id: number;
  kind: "info" | "error" | "ok";
  text: string;
  /**
   * The internal code behind a failure, shown behind a disclosure rather than in the sentence.
   *
   * **A code in front of a sentence is not user text** (claude-win2's 1030 review). The toast read
   * `project.busy: Stop the run first, or use Save a copy` - a stable identifier nobody outside this
   * repository recognises, in front of the one part the user needs. The code is still wanted, for a
   * bug report and for a reader of the console, so it goes here instead of being dropped.
   */
  code?: string;
  action?: ToastAction;
}

/** Parameter names that suggest a secret; such values never reach the console or its history. */
const SENSITIVE = /(key|token|secret|password|passwd|credential)/i;
const NAMING_KEYS = /^(node|name|param|parent)$/;

/** True when a command names a secret-like parameter, so its value must not be logged. */
function touchesSecret(params: Record<string, unknown>): boolean {
  return Object.entries(params).some(([k, v]) => SENSITIVE.test(k) || (NAMING_KEYS.test(k) && typeof v === "string" && SENSITIVE.test(v)));
}

/** The params with secret-bearing values replaced, for the console line. */
export function redactParams(params: Record<string, unknown>): Record<string, unknown> {
  const secret = touchesSecret(params);
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(params)) {
    if (SENSITIVE.test(k) || (secret && (k === "value" || k === "params"))) out[k] = "[redacted]";
    else if (v && typeof v === "object" && !Array.isArray(v)) out[k] = redactParams(v as Record<string, unknown>);
    else out[k] = v;
  }
  return out;
}

export interface StokesState {
  config: ClientConfig | null;
  client: StokesClient | null;
  connected: boolean;
  theme: Theme;
  stage: Stage;
  project: ProjectInfo | null;
  tree: NodeSummary | null;
  nodeTypes: Record<string, NodeTypeSchema>;
  commands: CommandSchema[];
  /** The condition catalogue (`boundary.kinds`), empty until the connection answers. */
  conditionKinds: ConditionKind[];
  /**
   * The colours offered for a part or a tag (`palette.list`), empty until the connection answers.
   *
   * The palette is **server-owned**, so this is read rather than hardcoded: a client-side copy would
   * drift from the colours the tag commands assign in turn and from the set the condition catalogue
   * accepts. Empty means "no swatches yet" — the style card keeps its custom picker.
   */
  palette: string[];
  /**
   * The last `setup.check` answer (ADR-0022 §5, §11.1): what the drawer strip's status readout
   * shows. Null until the first answer, so the readout can stay silent rather than claim "ready".
   */
  setup: SetupCheck | null;
  /**
   * The kind the value panel is open for, or null while it is closed (ADR-0022 §7.1, T-044). A
   * transient UI choice, not model state: it is what the toolbar's Assign menu sets and the panel
   * renders, and nothing is written to the project until the panel commits.
   */
  conditionKind: ConditionKind | null;
  units: UnitsSchema | null;
  licence: LicenseInfo | null;
  engine: EngineInfo | null;
  selectedId: string | null;
  /**
   * **Every chosen row**, with `selectedId` as its PRIMARY (claude-win2's 1400 file).
   *
   * `selectedId` stays exactly as it was, because sixteen places read it and each one means "the row the
   * Details page shows" - which a multi-selection still has, exactly one. This list is the new fact: what
   * is highlighted in the tree and in 3D.
   *
   * **`primary` inside it is `selectedId`**, and the anchor is kept so a second Shift click re-ranges from
   * the same start rather than from the last click. The rules live in `multiSelect.ts` and nowhere else.
   */
  selection: Selection;
  /**
   * The tree ROW that is chosen when it is not a node (T-220's successor, the Details pane).
   *
   * **Lifted out of `TreePanel` for the reason the gui-v3 review gives**: it was local state
   * (`TreePanel.tsx:78`), and a Details pane *beside* the tree cannot read another component's
   * `useState`. The rows that are not nodes - the stage folders, the counts - are choices a user
   * makes, so the thing that shows "what is selected" has to see them.
   *
   * `markedId` (the row drawn as chosen) is `outlineRow ?? selectedId`, which is the rule as it was;
   * only the place it lives changed.
   */
  outlineRow: string | null;
  setOutlineRow: (id: string | null) => void;
  /**
   * The guided-meshing tasks' answer (brief M-502), as the stepper fetched it: the tree's Mesh rows
   * and the stepper read the SAME reply, so neither can disagree with the other. Null until the
   * first ask lands.
   */
  meshTasks: TasksReply | null;
  /**
   * The mesh task the stepper chose (brief M-502), for the tasks whose page has no tree row of its
   * own: Geometry check and What each body is. Their clicks land here AND on the row the task maps
   * to (Geometry ▸ Bodies), so the tree highlights too; any other selection clears it, because the
   * selection is the pane's normal ruler and a task page that outstayed its welcome would be a pane
   * that ignores the user. Null when the last thing chosen was not a stepper task.
   */
  meshTask:
    | "geometry"
    | "bodies"
    | "sizing"
    | "surface"
    | "layers"
    | "quality"
    | null;
  setMeshTask: (
    task: "geometry" | "bodies" | "sizing" | "surface" | "layers" | "quality" | null,
  ) => void;
  selectedPatch: string | null;
  hoverPatch: string | null;
  /** Body or face picking in the viewport; not persisted. */
  pickMode: PickMode;
  /**
   * Paint select (T-043): the canvas paints instead of orbiting while it is on. The Modes island's
   * toggle and the `selection.paint` action (`P`) set it; Esc leaves it. Not persisted.
   */
  paintMode: boolean;
  /**
   * Lasso instead of a box (T-032): a drag on empty space draws the freehand path. The Modes
   * island's toggle sets it; `Alt`-drag asks for a lasso whatever it says. Not persisted.
   */
  lassoMode: boolean;
  /** What is picked now: bodies (part node ids) and faces (face ids per part node id). */
  picked: Picked;
  /**
   * The picks before the current one, oldest first, at most ten (ADR-0022 6.7): every pick change
   * but `pickPrevious` leaves the pick it left behind. `selection.previous` pops one.
   */
  pickHistory: readonly Picked[];
  /** What the pointer is over, as the viewport reports it (ADR-0022 6.1); null when it is over nothing. */
  hoverInfo: HoverInfo | null;
  /**
   * The groups the viewport draws, one per mesh, with the unique patch name the selection works
   * with. Set by the viewport (`setGeometry`); the sample part, or the imported parts.
   */
  geometry: PatchMeta[];
  /** The `part` nodes of the tree in tree order: the viewport shows these, and the sample part only while there are none. */
  partNodes: PartNode[];
  /**
   * What the server last said about each part's mesh, by node id. The viewport fetches a mesh when
   * its revision moves; the entries follow the part nodes of the tree (see state/parts.ts).
   */
  parts: Record<string, PartEntry>;
  /** Imports under way, by the path of their top node; the viewport's progress line shows the newest. */
  importProgress: Record<string, ImportProgress>;
  /**
   * The operation types (`operation.types`), by type: the cards' schemas, the labels the menu and
   * the progress line show, and what tells an operation node from another in the tree. Empty
   * against a server without the operations.
   */
  operationTypes: Record<string, OperationTypeInfo>;
  /** Operation runs under way, by operation node id (`geometry.op_progress`). */
  operations: Record<string, OperationProgress>;
  /**
   * What the Mesh panel keeps of the mesh run that the tree does not hold (T-211b): the step, the
   * stage and the log, from the `mesh.progress` and `mesh.log` events. The state, the counts and the
   * quality are the `/mesh/results` node's own and are read from the tree.
   */
  mesh: MeshLive;
  /**
   * The run's live state (T-217): the records, the residual history, the last `solve.check`, the job
   * log and the Log tab's view, from the `run.*` events and `run.list`/`run.residuals`/`solve.check`
   * (`panels/runControl.ts`). The reducer and the shapes are `panels/runPanelModel.ts`.
   */
  run: RunLive;
  /**
   * The results on the mesh (T-218): the field chosen, its range, the slice switch and the parsed
   * artifact of the run shown. View state only: never journaled and never sent as a command. The
   * reducers are `panels/fieldsModel.ts`; `panels/resultsControl.ts` loads it and `viewport/fields.ts`
   * colours the patches from it.
   */
  results: ResultsLive;
  /**
   * The slices of the Results stage: the planes the results are cut with, how each is drawn, and what the
   * server said about each cut. View state only, like `results`; the reducers are `panels/sliceModel.ts`,
   * `viewport/sliceLayer.ts` cuts and draws what it lists.
   */
  slices: SliceList;
  /** The one operation card that is open, or null. */
  operationCard: OperationDraft | null;
  /**
   * The last operation that finished, with its first output and the type that ran it (T-050). The
   * operation card clears itself when its own run ends; a flow helper needs to know that an
   * enclosure or an extraction it started has produced a node, so it can call `setup.defaults` on it.
   * Kept as the last event rather than per-card state because only one card runs at a time.
   */
  lastOperationDone: { nodeId: string; type: string; output: string | null } | null;
  /**
   * The topology facts a finer-mode pick conversion needs (A6), supplied by the viewport because the
   * scene is what holds the decoded records. Null until the viewport mounts, and each accessor
   * returns null/empty when a part's record is not loaded — §15.4's "omit topology not yet loaded
   * with a visible pending state" rather than dropping the pick.
   */
  conversionTopology: ConversionTopology | null;
  /**
   * The parts whose topology record the controller has loaded (§15.4, A6). The viewport reports it;
   * the pending readout uses it to say which parts a conversion is waiting for, instead of leaving
   * the user with a pick that silently became empty.
   */
  topologyLoaded: ReadonlySet<string>;
  /**
   * The flow helper currently open (T-050), or null. Transient UI state: it holds the template, the
   * body the operation runs on, and the External card's fields. It is what the Prepare action sets
   * and `FlowCards` renders; the run itself is an ordinary `operation.create` through the card.
   */
  flowRun: { id: number; template: "external" | "internal"; target: string; velocity: string; addDefaults: boolean } | null;
  /**
   * True while the card is waiting for a point to be picked in the viewport (T-029): the next click
   * on a face answers it, and Escape leaves the mode (`selection.clear`, the same key as the pick).
   */
  seedPick: boolean;
  /**
   * The output `geometry.op_done` asked for, until the tree holds it: the node event that adds an
   * output is still queued behind the operation event when it arrives, so the selection waits.
   */
  pendingSelect: string | null;
  expanded: Record<string, boolean>;
  treeFilter: string;
  drawerOpen: boolean;
  drawerTab: DrawerTab;
  consoleLines: ConsoleLine[];
  toasts: Toast[];
  paletteOpen: boolean;
  fitRequest: number;
  /** A count of "fit everything" requests, so the viewport can tell the two apart. */
  fitAllRequest: number;
  /**
   * Bumped to ask the section popover to open (T-065). A counter rather than a boolean, as
   * `fitRequest` is: pressing the chord twice should reopen a control the user dismissed, and a
   * boolean set to `true` twice changes nothing, so the second press would do nothing.
   */
  sectionRequest: number;
  viewMode: ViewMode;
  /**
   * How the mesh is drawn in the Mesh stage (T-211): its boundary patches alone, or with their cell
   * edges. View state like `viewMode`: nothing is written to the project and nothing is undone.
   */
  meshDisplay: MeshDisplay;
  /** The mouse scheme (ADR-0021 3.4), from the Modes island's Navigation menu. View state only. */
  navMode: NavMode;
  /**
   * The `toggle` scheme's switch (ADR-0021 3.4): while on, a left drag rotates and the box needs
   * Shift; while off, a left drag is the box. Ignored by the other schemes, and reset when the
   * scheme changes so `toggle` always starts in Select.
   */
  orbitMode: boolean;
  orthographic: boolean;
  hiddenPatches: string[];
  isolatedPatch: string | null;
  /**
   * The parts and tags the user has hidden, **by node id, for this session only**.
   *
   * **`Hide` means one thing everywhere** (claude-win2's 2000 review, item 2): a VIEW filter. The tree
   * row's eye used to write the model's `visible` property through `geometry.style`, so the same word
   * meant "session view state" in the viewport menu and "a saved, journalled, undoable change" in the
   * tree - two behaviours behind one label and one icon. It is view state now, exactly like
   * `hiddenPatches` and `isolatedPatch` beside it, and for the same reason the section plane gives:
   * hiding something changes nothing about the geometry, the tags, the case or the run.
   *
   * **The model keeps `visible`.** A headless caller may legitimately want it, `geometry.style` still
   * accepts it, and the tests that drive it through the API are untouched. What changed is that no
   * WIDGET writes it: a user pressing the eye is filtering the view, not editing the part.
   */
  hiddenNodes: string[];
  /**
   * The section plane, or null for "no section" (T-065).
   *
   * **View state, like `viewMode` and `orthographic`, not model state.** A section is how the model is
   * being *looked at*: it changes nothing about the geometry, the tags, the regions or the case, so it
   * gets no API command, no journal record and nothing on the undo stack — the same rule
   * `toggleHidden` and `isolate` already follow, and the reason they are client-only too. A cut that
   * altered the model would be a geometry operation (the Prepare menu), which is a different feature.
   *
   * `axis` is the plane's world normal, `offset` its signed distance along that normal in metres
   * (the model's own units, as everything the viewport holds is). The plane keeps the side the normal
   * points to, so an offset of 0 cuts through the origin and +Z hides what lies behind it.
   */
  section: SectionPlane | null;
  /** Projected screen bounds of the selected patch (CSS px within the viewport); set by the viewport. */
  patchAnchor: ScreenAnchor | null;
  /**
   * The viewport's scale bar: how long the model is at the size it is drawn. Set by the viewport
   * when the camera moves and the figure changes; view state, never part of the project.
   */
  scaleBar: ScaleBar | null;
  /**
   * Measure (T-098): while it is on, a click on a model records a point and two of them become a
   * distance. `view.measure` and the Modes island toggle set it, Esc leaves it. View state, never
   * part of the project — nothing is measured into the model, as with the scale bar.
   */
  measureMode: boolean;
  /**
   * The probe: its points, the values they read, and why it has none yet (results item 2).
   *
   * **View state, no journal record** - a probe reads, it does not change the project. The rules are
   * in `panels/probeModel.ts`, so this is only where the answer lives.
   */
  probe: ProbeState;
  /** The streamlines drawn in the viewport (results item 4). View state: a read, not a project edit. */
  streamlines: StreamlineState;
  /**
   * Vector arrows on the slice (claude-win2's item 2): whether they show, and how many and how long.
   *
   * **`shown` is the switch**, and the density/scale are the caller's while hidden too - a user who sets
   * a density and then presses Show expects that density, not a reset.
   */
  arrows: ArrowView;
  setArrows: (arrows: ArrowView) => void;
  /** The arrows sampled through the volume (`run.vectors`), drawn while `arrows.where` is "volume". View state. */
  vectorCloud: VectorCloud;
  setVectorCloud: (cloud: VectorCloud) => void;
  /** The iso-surface the viewport draws (ADR-0026 amendment 1); view state, `panels/isoModel.ts` decides. */
  iso: IsoView;
  setIso: (iso: IsoView) => void;
  /** What the viewport made of `iso`, written by `viewport/isoLayer.ts` only. */
  isoDrawn: IsoDrawn;
  setStreamlines: (streamlines: StreamlineState) => void;
  /** The field and cut requests the viewport's layers have on the way; written by `viewport/fields.ts` and `sliceLayer.ts` only (`patchLoading`), read by `viewSettled`. */
  loading: LoadingCounts;
  setProbe: (probe: ProbeState) => void;
  /**
   * The plot over a line (R5): two clicked points, the field sampled between them. View state, no journal record;
   * the rules are in `panels/lineModel.ts`, the asks in `guided/lineControl.ts`.
   */
  line: LineState;
  setLine: (line: LineState) => void;
  /** Start a point pick in the viewport for the probe; the scene answers with one world point. */
  probePick: boolean;
  setProbePick: (on: boolean) => void;
  /**
   * The measurement's readout: the distance as text and the projected midpoint the label sits at,
   * in CSS pixels of the viewport. Set by the scene; null while there is no measurement.
   */
  measureReadout: MeasureReadout | null;
  /** The Measure panel's content: what is picked and every figure it supports. Null while the tool is off. */
  measureDetails: MeasureDetails | null;
  /** Asks the scene to start a new measurement without leaving the tool (the panel's Clear); a counter. */
  measureReset: number;
  /**
   * Counts the times a chip or a stage key (`1` to `6`) chose a stage. The tree reacts to a change by scrolling the
   * stage's first group into view; zero means nobody chose yet (the load), so it does nothing.
   */
  stageFocus: number;
  /**
   * Counts the selections of a node or a patch, the same one again included. The tree opens the
   * selected node's ancestors once per count, so picking a patch whose group a stage folded shows its row.
   */
  revealRequest: number;

  setViewMode(mode: ViewMode): void;
  setMeshDisplay(display: MeshDisplay): void;
  setNavMode(mode: NavMode): void;
  setOrbitMode(on: boolean): void;
  setOrthographic(on: boolean): void;
  /** Turn the section on or off; `setSectionPlane` moves one that is on. */
  setSection(on: boolean): void;
  /** Move the section plane. A no-op while the section is off, so a slider cannot enable it. */
  setSectionPlane(axis: SectionAxis, offset: number): void;
  /**
   * Turn the section ON at a plane, in ONE write.
   *
   * **`setSection(true)` then `setSectionPlane(...)` is not equivalent** (G-301's D6): the first writes
   * the default plane - axis z, offset 0, the WORLD ORIGIN - and the slice layer fetches it before the
   * second lands, so opening the Section control produced two `404 plane=0,0,0,0,0,1` every time.
   * Measured, and it is why this exists rather than a caller doing both.
   */
  putSectionAt(axis: SectionAxis, offset: number): void;
  /** Keep the other half of the model. A no-op while the section is off, as the move is. */
  setSectionInvert(invert: boolean): void;
  toggleHidden(name: string): void;
  /** Hide or show a part or tag by node id, for this session. */
  toggleHiddenNode(id: string): void;
  isolate(name: string | null): void;
  setPatchAnchor(anchor: ScreenAnchor | null): void;
  setScaleBar(bar: ScaleBar | null): void;
  /** Turn measure mode on or off (T-098); the scene clears the measurement when it goes off. */
  setMeasureMode(on: boolean): void;
  /** The readout the viewport measured; the scene is the only caller. */
  setMeasureReadout(readout: MeasureReadout | null): void;
  /** The panel's content; the scene is the only caller. */
  setMeasureDetails(details: MeasureDetails | null): void;
  /** Clear the current measurement and wait for a first element again. */
  resetMeasure(): void;

  init(config: ClientConfig): Promise<void>;
  refresh(): Promise<void>;
  /**
   * Re-run `setup.check`, debounced (ADR-0022 §11.1: "the readout refreshes with the debounced
   * `setup.check`"). Every model change calls it; the 250 ms window coalesces a burst of edits into
   * one call, because the check walks the whole tree.
   */
  refreshSetup(): void;
  execute(method: string, params?: Record<string, unknown>, options?: { silent?: boolean }): Promise<unknown>;
  /**
   * Select a tree node (null: none). The pick follows the node (a part is a body pick, a tag picks its
   * members, anything else empties it) unless `keepPick` asks to leave the pick as it is.
   */
  select(id: string | null, options?: { keepPick?: boolean }): void;
  /**
   * Choose a row the way a MODIFIED click does (claude-win2's 1400 file): `ctrl` toggles, `shift` extends
   * from the anchor, both together add a range. `visible` is the rows as the user sees them, because a
   * Shift range must not reach into a collapsed folder.
   *
   * **`select` above is unchanged and still means "the one row"**: it is what every non-modified path
   * calls, and what its sixteen readers assume. This is the gesture path.
   */
  selectRow(id: string, gesture: { ctrl?: boolean; shift?: boolean }, visible: readonly string[]): void;
  /** Ctrl+A in the tree: every sibling of the primary's type in its folder. */
  selectSiblings: () => void;
  /** Escape, or a press on empty tree. */
  clearSelection: () => void;
  selectPatch(name: string | null): void;
  hover(name: string | null): void;
  /** Switch the picking mode; the pick converts to the new mode instead of dropping (ADR-0022 6.3). */
  setPickMode(mode: PickMode): void;
  /** Hand over the scene-backed accessors a finer-mode pick conversion needs (A6). */
  setConversionTopology(topology: ConversionTopology | null): void;
  /** Record which parts' topology records are loaded, for the §15.4 pending readout. */
  setTopologyLoaded(parts: ReadonlySet<string>): void;
  /** Turn paint select on or off (T-043); the mode is not persisted and survives a switch of Body/Face. */
  setPaintMode(on: boolean): void;
  /** Draw a lasso instead of a box on the next drag on empty space (T-032); Alt asks for one regardless. */
  setLassoMode(on: boolean): void;
  /**
   * Apply a whole set of bodies and faces in one update (T-043, ADR-0022 6.5): the paint stroke as
   * it goes, and the box and the lasso T-032 adds. Bodies when `faces` is absent, faces otherwise;
   * the active member is the last item's part and `pickHistory` gets one entry for the call.
   */
  pickMany(items: { part: string; faces?: number[] }[], how: "add" | "remove" | "replace"): void;
  /** Pick a body (a part node id) with the gesture `how`; `patch` names the clicked patch of a source with several (the sample part). */
  pickBody(part: string, how: PickHow, patch?: string): void;
  /** Pick every part beside `part` under the same parent group, `part` first: the body double-click (ADR-0022 6.2). */
  pickSiblings(part: string, how: PickHow, patch?: string): void;
  /** Pick a face of a part node id with the gesture `how`; the pick selects faces only (T-061). */
  pickFace(part: string, face: number, how: PickHow): void;
  /** Pick one B-rep edge or vertex (ADR-0022 §15.4, T-058); the rules are `pickAfterTopology`'s. */
  pickTopology(part: string, kind: "edge" | "vertex", id: number, how: PickHow): void;
  /** A whole set of edges or vertices: the double-click's wire, or a triple-click's body. */
  pickTopologyMany(
    part: string,
    kind: "edge" | "vertex",
    ids: readonly number[],
    how: PickHow,
  ): void;
  /** Pick every face of a part with the gesture `how`: the face triple-click (ADR-0022 6.2). */
  pickAllFaces(part: string, how: PickHow): void;
  /** Bring back the pick before the last change (ADR-0022 6.7); it converts to the current mode when needed. */
  pickPrevious(): void;
  /** The viewport reports what the pointer is over, for the status strip (T-049). */
  setHoverInfo(info: HoverInfo | null): void;
  /** Empty the pick (Esc, or a switch of mode). */
  clearPick(): void;
  /** The viewport reports what it draws. Selection state that names a patch no longer there is dropped. */
  setGeometry(patches: PatchMeta[]): void;
  /** Patches were renamed (their node was, or a name clash resolved differently): the selection follows. */
  renamePatches(renames: ReadonlyMap<string, string>): void;
  toggleExpanded(id: string): void;
  setTreeFilter(text: string): void;
  /** A chip or a stage key chose a stage: the tree opens its groups and folds the others (unless `tree.linkStage` is off). */
  setStage(stage: Stage): void;
  setTheme(theme: Theme): void;
  toggleDrawer(open?: boolean): void;
  setDrawerTab(tab: DrawerTab): void;
  setPalette(open: boolean): void;
  pushToast(kind: Toast["kind"], text: string, action?: ToastAction, code?: string): void;
  dismissToast(id: number): void;
  log(kind: ConsoleLine["kind"], text: string): void;
  /** Drop console lines, all of them or only the given kinds (the Log tab clears events alone). */
  clearConsole(kinds?: ConsoleLine["kind"][]): void;
  /** Fit the shape; with `all`, every body including a region's domain. */
  requestFit(all?: boolean): void;
  /** Ask the section popover to open, from the chord or the command palette (`view.section`). */
  requestSection(): void;
  /**
   * Open the operation card for a type (T-029), pre-filled with the live pick: the parts it reads,
   * and a face pick's faces as members. One card at a time; a new one replaces the old.
   */
  openOperation(type: string, params?: Record<string, unknown>): void;
  /** Close the card, and leave the seed-pick mode it may have been waiting in. */
  closeOperationCard(): void;
  /** Turn the seed-pick mode on or off; the card drives it, Escape leaves it. */
  setSeedPick(on: boolean): void;
  /** The node the card's run made: set when Run answers, cleared by a new card. */
  /**
   * The node the open card's Run made. `cardId` is the card that asked: a reply that arrives after
   * the card was closed and another opened must not attach to the new one (T-029).
   */
  setOperationNode(cardId: number, id: string | null): void;
  /** The message of a failed run, or "" when it is cleared; `cancelled` marks the user's own Cancel. */
  setOperationError(message: string, cancelled?: boolean): void;
}

let lineId = 1;
let toastId = 1;
/** Counts the operation cards opened, so a new one gets a new form (T-029). */
let cardId = 0;
/** How the tree reaches the store (state/treeSync.ts); made when the client is. */
let sync: TreeSync | null = null;

/** The OS appearance, which the `auto` requests follow. */
function osAppearance(): Appearance {
  return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

function resolveTheme(theme: ClientConfig["theme"]): Theme {
  if (theme === "auto") return osAppearance();
  if (theme === "cupertino-auto") return composeTheme("cupertino", osAppearance());
  if (theme === "ubuntu-auto") return composeTheme("ubuntu", osAppearance());
  return theme;
}

export function findNode(root: NodeSummary | null, id: string | null): NodeSummary | null {
  if (!root || !id) return null;
  const index = indexFor(root); // the index of the store's tree: a lookup, not a walk
  if (index !== null) return index.byId.get(id) ?? null;
  const stack: NodeSummary[] = [root];
  while (stack.length) {
    const n = stack.pop()!;
    if (n.id === id) return n;
    if (n.children) stack.push(...n.children);
  }
  return null;
}

export function findByPath(root: NodeSummary | null, path: string): NodeSummary | null {
  if (!root) return null;
  const index = indexFor(root);
  if (index !== null) return nodeAtIndexedPath(index, path);
  const stack: NodeSummary[] = [root];
  while (stack.length) {
    const n = stack.pop()!;
    if (n.path === path) return n;
    if (n.children) stack.push(...n.children);
  }
  return null;
}

/** The one patch a source id stands for: a part node's single patch, the sample part's patch, or null. */
export function patchForSource(source: string, geometry: readonly PatchMeta[]): string | null {
  const own = geometry.filter((g) => g.part === source || (source === SAMPLE_SOURCE && g.part === null));
  return own.length === 1 ? own[0].name : null;
}

/**
 * A tag row selected in the tree: the pick becomes the tag's members, so the accent highlight shows
 * them (ADR-0021 3.5). A tag with faces is a face pick, its whole-part members included face by face;
 * a tag of whole parts is a body pick. There is no active member: a tag has no single patch
 * (`patchForNode`). The faces come from `tag.members`; a selection that moved on while the answer
 * was on its way keeps its own pick.
 */
async function pickTag(id: string): Promise<void> {
  const state = useStore.getState();
  if (!state.client) return;
  let members: TagMembers;
  try {
    members = (await state.client.call<{ members: TagMembers }>("tag.members", { tag: id })).members;
  } catch (err) {
    useStore.getState().log("error", `tag ${id}: ${(err as Error).message}`);
    return;
  }
  if (useStore.getState().selectedId !== id) return;
  const present = members.filter((m) => m.exists);
  const byFace = present.some((m) => !m.whole && m.faces.length > 0);
  const faces: Record<string, number[]> = {};
  const bodies: string[] = [];
  for (const m of present) {
    if (!byFace) bodies.push(m.part);
    else if (m.faces.length > 0) faces[m.part] = [...new Set([...(faces[m.part] ?? []), ...m.faces])].sort((a, b) => a - b);
  }
  const before = useStore.getState();
  useStore.setState({ ...changedPick(before, { bodies, faces, edges: {}, vertices: {} }, false), pickMode: byFace ? "face" : "body", selectedPatch: null });
}

/**
 * What a tag made from the pick holds: its `members` parameter and the kind it takes.
 *
 * **`edges` carries a picked edge loop** (ADR-0022 §15.4, and `cap.py`'s own docstring: "store
 * explicit cap loops as edge members of a boundary or plain tag; the cap operation consumes the
 * loop"). The server side has been complete all along — `OpContext.edges` reads a member's `edges`
 * key as ids or ranges, and `cap`'s `_tagged_loops` ties those edges to a closed opening by identity.
 * Only the UI was missing, so a loop could not be tagged and `cap loop=` was unreachable.
 */


/**
 * The pick as one line for the toolbar and the card: "3 faces on 2 parts", "1 face on 1 part",
 * "2 bodies"; null while the pick holds nothing. The sample part counts as a part: it has faces
 * to assign even though it has no node (`pickAsMembers` leaves it out of tags and members).
 */
export function describePick(picked: Picked, mode: PickMode): string | null {
  if (mode === "edge" || mode === "point") {
    const ids = Object.values(mode === "edge" ? picked.edges : picked.vertices).filter((held) => held.length > 0);
    const count = ids.reduce((n, held) => n + held.length, 0);
    if (count === 0) return null;
    const noun = mode === "edge" ? "edge" : "vertex";
    // "vertex" pluralises to "vertices", not "vertexs".
    const plural = count === 1 ? noun : mode === "edge" ? "edges" : "vertices";
    return `${count} ${plural} on ${ids.length} part${ids.length === 1 ? "" : "s"}`;
  }
  if (mode === "face") {
    const held = Object.values(picked.faces).filter((ids) => ids.length > 0);
    const faces = held.reduce((n, ids) => n + ids.length, 0);
    if (faces === 0) return null;
    return `${faces} face${faces === 1 ? "" : "s"} on ${held.length} part${held.length === 1 ? "" : "s"}`;
  }
  const bodies = picked.bodies.length;
  return bodies === 0 ? null : `${bodies} ${bodies === 1 ? "body" : "bodies"}`;
}

/**
 * What the pick holds that an operation can read (T-029): the picked parts, and the picked faces
 * as the number of face ids. The sample part has no node and no face ids, so it is left out — the
 * Prepare menu's entries count only what a run could take.
 */
export function readablePick(picked: Picked, mode: PickMode): { bodies: number; faces: number } {
  const bodies = pickAsMembers(picked, "body")?.members.length ?? 0;
  const held = pickAsMembers(picked, "face");
  let faces = 0;
  if (mode === "face" && held !== null) {
    for (const member of held.members) {
      // A face pick makes face members, but the type allows an edge member: a member with no `faces`
      // key counts no faces, which is what "readable" should say about it.
      if (!("faces" in member)) continue;
      for (const range of member.faces) faces += Array.isArray(range) ? range[1] - range[0] + 1 : 1;
    }
  }
  return { bodies, faces };
}

/**
 * The part a face pick acts on: the one picked from last, or after a toggle-off the last part
 * still holding faces. A face pick sets no `selectedPatch` (T-061), so this is what Isolate and
 * Hide and Grow's active member work from; null while the pick holds no faces.
 */
export function activeFacePart(picked: Picked): string | null {
  const held = Object.keys(picked.faces).filter((part) => picked.faces[part].length > 0);
  return held.length > 0 ? held[held.length - 1] : null;
}

/**
 * The pick after a `tag.region` answer: the grown faces (and any whole parts) in place of the old
 * pick, the active member kept where the answer still has it, else the first part of it.
 */
export function pickFromRegion(members: readonly TagRegionMember[], keepActive: string | null): { picked: Picked; active: string | null } {
  const bodies: string[] = [];
  const faces: Record<string, number[]> = {};
  for (const m of members) {
    if (m.faces === "all") {
      bodies.push(m.part);
      continue;
    }
    const ids: number[] = [];
    for (const [first, last] of m.faces) for (let i = first; i <= last; i++) ids.push(i);
    if (ids.length > 0) faces[m.part] = ids;
  }
  const parts = [...Object.keys(faces), ...bodies];
  const active = keepActive !== null && parts.includes(keepActive) ? keepActive : (parts[0] ?? null);
  return { picked: { bodies, faces, edges: {}, vertices: {} }, active };
}

/**
 * The pick after a change, with the pick it is leaving kept for `selection.previous` (ADR-0022 6.7).
 * Nothing is kept when nothing moved, or when the caller itself is `pickPrevious` (`keep`), so the
 * history never holds two of the same pick in a row.
 */
function changedPick(state: StokesState, picked: Picked, keep: boolean): { picked: Picked; pickHistory: readonly Picked[] } {
  if (keep || samePicked(state.picked, picked)) return { picked, pickHistory: state.pickHistory };
  return { picked, pickHistory: pushHistory(state.pickHistory, state.picked) };
}

/**
 * The face count of a part in the drawn geometry (`convertPick` body → face): a part's face ids run
 * from 0 (stokes.geometry.mesh numbers them from the part's own base), so the count is the furthest
 * `faceBase + faces` over its groups. 0 when the part is not drawn or carries no face ids at all
 * (the sample part), which the conversion reads as "no face to name" and drops.
 */
function faceCountFor(geometry: readonly PatchMeta[], part: string): number {
  let count = 0;
  for (const meta of geometry) if (meta.part === part) count = Math.max(count, meta.faceBase + meta.faces);
  return count;
}

/** The part a drawn patch belongs to; null for a patch of the sample part, which has no node. */
function partOfPatch(geometry: readonly PatchMeta[], patch: string): string | null {
  return geometry.find((meta) => meta.name === patch)?.part ?? null;
}

/** The parent of the node `id` in the tree, or null (the root, or a node the tree does not hold). */
function parentNode(root: NodeSummary | null, id: string): NodeSummary | null {
  const stack: NodeSummary[] = root ? [root] : [];
  while (stack.length > 0) {
    const node = stack.pop() as NodeSummary;
    for (const child of node.children ?? []) {
      if (child.id === id) return node;
      stack.push(child);
    }
  }
  return null;
}

/**
 * The ids of the `part` nodes beside `part` under the same parent group — the import file's group,
 * one per file (ADR-0022 6.2) — in tree order. A part with no such group (the sample part, which has
 * no node) stands alone, which leaves a body double-click on it what a plain click does. This walks
 * the tree, which a gesture may: it runs on a click, never per frame.
 */
function partSiblings(tree: NodeSummary | null, part: string): string[] {
  const group = parentNode(tree, part);
  if (group === null) return [part];
  return (group.children ?? []).filter((child) => child.type === "part").map((child) => child.id);
}

export const useStore = create<StokesState>((set, get) => ({
  config: null,
  client: null,
  connected: false,
  theme: "dark",
  stage: "Geometry",
  project: null,
  tree: null,
  nodeTypes: {},
  commands: [],
  conditionKinds: [],
  palette: [],
  setup: null,
  conditionKind: null,
  units: null,
  licence: null,
  engine: null,
  selectedId: null,
  selection: NO_SELECTION,
  outlineRow: null,
  meshTasks: null,
  meshTask: null,
  selectedPatch: null,
  hoverPatch: null,
  pickMode: "body",
  paintMode: false,
  lassoMode: false,
  picked: NO_PICK,
  pickHistory: [],
  hoverInfo: null,
  geometry: [],
  expanded: {},
  treeFilter: "",
  drawerOpen: false,
  drawerTab: "console",
  consoleLines: [],
  toasts: [],
  paletteOpen: false,
  fitRequest: 0,
  /** A count of "fit everything" requests, so the viewport can tell the two apart. */
  fitAllRequest: 0,
  sectionRequest: 0,
  viewMode: "shaded",
  meshDisplay: "surface",
  navMode: "paraview",
  orbitMode: false,
  orthographic: false,
  partNodes: [],
  parts: {},
  importProgress: {},
  operationTypes: {},
  operations: {},
  mesh: NO_MESH_LIVE,
  run: NO_RUN,
  results: NO_RESULTS,
  slices: NO_SLICES,
  operationCard: null,
  lastOperationDone: null,
  flowRun: null,
  conversionTopology: null,
  topologyLoaded: new Set<string>(),
  seedPick: false,
  pendingSelect: null,
  hiddenPatches: [],
  hiddenNodes: [],
  section: null,
  isolatedPatch: null,
  patchAnchor: null,
  scaleBar: null,
  measureMode: false,
  probe: NO_PROBE,
  streamlines: NO_STREAMLINES,
  arrows: { shown: false, density: 48, scale: 1 },
  setArrows: (arrows) => set({ arrows }),
  vectorCloud: NO_VECTOR_CLOUD,
  setVectorCloud: (vectorCloud) => set({ vectorCloud }),
  iso: NO_ISO,
  setIso: (iso) => set({ iso }),
  isoDrawn: NO_ISO_DRAWN,
  setStreamlines: (streamlines) => set({ streamlines }),
  loading: NO_LOADING,
  setProbe: (probe) => set({ probe }),
  line: NO_LINE,
  setLine: (line) => set({ line }),
  probePick: false,
  setProbePick: (probePick) => set({ probePick }),
  measureReadout: null,
  measureDetails: null,
  measureReset: 0,
  stageFocus: 0,
  revealRequest: 0,

  setViewMode: (viewMode) => set({ viewMode }),
  setMeshDisplay: (meshDisplay) => set({ meshDisplay }),
  setNavMode: (navMode) => set({ navMode, orbitMode: false }),
  setOrbitMode: (orbitMode) => set({ orbitMode }),
  setOrthographic: (orthographic) => set({ orthographic }),
  setSection: (on) => set((s) => ({ section: toggleSection(s.section, on) })),
  setSectionPlane: (axis, offset) => set((s) => ({ section: moveSection(s.section, axis, offset) })),
  putSectionAt: (axis, offset) => set({ section: sectionAt(axis, offset) }),
  setSectionInvert: (invert) => set((s) => ({ section: invertSection(s.section, invert) })),
  toggleHidden: (name) =>
    set((s) => ({ hiddenPatches: s.hiddenPatches.includes(name) ? s.hiddenPatches.filter((n) => n !== name) : [...s.hiddenPatches, name] })),
  toggleHiddenNode: (id) =>
    set((s) => ({ hiddenNodes: s.hiddenNodes.includes(id) ? s.hiddenNodes.filter((n) => n !== id) : [...s.hiddenNodes, id] })),
  isolate: (isolatedPatch) => set({ isolatedPatch }),
  setPatchAnchor: (patchAnchor) => set({ patchAnchor }),
  setScaleBar: (scaleBar) => set({ scaleBar }),
  setMeasureMode: (measureMode) => set({ measureMode }),
  setMeasureReadout: (measureReadout) => set({ measureReadout }),
  setMeasureDetails: (measureDetails) => set({ measureDetails }),
  resetMeasure: () => set((s) => ({ measureReset: s.measureReset + 1 })),

  async init(config) {
    const client = new StokesClient(config.apiBase, config.token);
    const theme = resolveTheme(config.theme);
    set({ config, client, theme });
    if (config.theme === "auto" || config.theme === "cupertino-auto" || config.theme === "ubuntu-auto") {
      // Follow the OS appearance inside whichever family is current; the user may have switched.
      window
        .matchMedia("(prefers-color-scheme: light)")
        .addEventListener("change", (e) => set({ theme: composeTheme(themeFamily(get().theme), e.matches ? "light" : "dark") }));
    }
    // The tree reaches the store through this: fetched whole when it must be, patched from the node events otherwise.
    sync = createTreeSync({
      get,
      set: (partial) => set(partial),
      consoleWith: (lines) => ({ consoleLines: withLines(get().consoleLines, lines) }),
      logError: (text) => get().log("error", text),
      // The stage the window is in, so a new project opens on the part of the tree the user is
      // looking at. `get()` rather than a captured `stage`: by the time a tree arrives the user may
      // have moved stages, and the expansion should follow where they are.
      defaultExpansion: (tree) => defaultStageExpansion(get().stage, tree),
      importFailed: reportFailure,
    });
    let connectedBefore = false;
    client.onStatus((connected) => {
      set({ connected });
      if (!connected) return;
      void get().refresh();
      // Announcements made while the connection was down are lost: every mesh is asked for again.
      if (connectedBefore) set((s) => ({ parts: markAllStale(s.parts), importProgress: {} }));
      connectedBefore = true;
    });
    client.onEvent((event, data) => {
      const geometry = readGeometryEvent(event, data);
      // The operations (T-029) are applied here, before the sync's queue: a run's progress and its
      // end are the card's news, and `geometry.op_done` closes it before the tree catches up.
      const operation = readOperationEvent(event, data);
      if (operation !== null) applyOperationEvent(operation);
      else if (event === "project.opened" || event === "project.closed") dropMeshes();
      // Which kinds the engine can write depends on the engine: ask again when it can have changed.
      if (kindsRefreshWanted(event, data)) {
        void client
          .call<ConditionKind[]>("boundary.kinds")
          .then((conditionKinds) => set({ conditionKinds }))
          .catch((err) => get().log("error", `boundary.kinds: ${(err as Error).message}`));
      }
      // The mesh run's step and log (T-211b); its state and result arrive as node events, in the tree.
      const meshEvent = readMeshEvent(event, data);
      if (meshEvent !== null) useStore.setState((s) => ({ mesh: applyMeshEvent(s.mesh, meshEvent, Date.now()) }));
      // Still a console line (through the sync, which keeps the order things happened in), but a
      // run reports up to ten progress steps a second: only a few of them reach the console.
      // The run's events (T-217): the job log, the residual blocks and the progress are 10 Hz and have
      // their own homes (`run`), so they never reach the console; the start and the end still do.
      const runEvent = readRunEvent(event, data);
      if (runEvent !== null) useStore.setState((s) => ({ run: reduceRunEvent(s.run, runEvent) }));
      const quiet =
        (operation?.kind === "progress" && !progressLogAllowed()) ||
        meshEvent?.kind === "progress" ||
        meshEvent?.kind === "log" ||
        runEvent?.kind === "progress" ||
        runEvent?.kind === "residual" ||
        runEvent?.kind === "log";
      const shown = geometry === null ? true : geometryLogAllowed(geometry);
      sync?.receive(event, data, geometry, quiet || !shown ? null : `${event} ${loggedData(event, data)}`);
    });
    try {
      await client.connect();
    } catch (err) {
      get().pushToast("error", (err as Error).message);
      return;
    }
    const [types, commands, units, licence, engine, conditionKinds, palette, operationTypes] =
      await Promise.all([
      client.call<Record<string, NodeTypeSchema>>("node.types"),
      client.call<CommandSchema[]>("schema.commands"),
      client.call<UnitsSchema>("schema.units"),
      client.call<LicenseInfo>("license.info").catch(() => null), // older servers lack these two
      client.call<EngineInfo>("engine.info").catch(() => null),
      // The assign menu is built from the condition catalogue, which needs no project. A server
      // that does not answer leaves the menu empty rather than the window unstarted (T-061).
      client.call<ConditionKind[]>("boundary.kinds").catch((err) => {
        get().log("error", `boundary.kinds: ${(err as Error).message}`);
        return [] as ConditionKind[];
      }),
      // The colour palette for the style card's swatches. It needs no project, and a server that
      // cannot answer leaves the swatches out rather than the window unstarted.
      client.call<{ colors: string[] }>("palette.list").catch((err) => {
        get().log("error", `palette.list: ${(err as Error).message}`);
        return { colors: [] as string[] };
      }),
      // The operation types (T-029): asked once per connection, as the node types are; a server
      // without the operations leaves the Prepare menu shut rather than the window unstarted.
      client.call<unknown>("operation.types").catch((err) => {
        get().log("error", `operation.types: ${(err as Error).message}`);
        return null;
      }),
    ]);
    set({
      nodeTypes: types,
      commands,
      units,
      licence,
      engine,
      conditionKinds,
      palette: palette.colors,
      operationTypes: readOperationTypes(operationTypes),
    });
    await get().refresh();
    get().refreshSetup();
    // The tree opens to the workspace's `tree.defaultExpanded` levels **within the current stage**:
    // the stage's subtree opens and every other top-level group folds. The document was asked for as
    // soon as the connection came up, ahead of the refresh above, and the server answers in the order
    // it is asked, so the depth is known by now (two levels, as ever, from a server without a
    // workspace). See `treeExpansion.ts` for why this is neither `expansionFor` nor `stageExpanded`.
    const tree = get().tree;
    if (tree) set({ expanded: defaultStageExpansion(get().stage, tree) });
  },

  refresh: () => (sync ? sync.refresh() : Promise.resolve()),

  refreshSetup() {
    const s = get();
    if (s.client === null || s.project === null) {
      set({ setup: null });
      return;
    }
    if (setupTimer !== 0) clearTimeout(setupTimer);
    setupTimer = setTimeout(() => {
      setupTimer = 0;
      const now = get();
      if (now.client === null || now.project === null) return;
      void now.client
        .call<SetupCheck>("setup.check")
        .then((answer) => set({ setup: answer }))
        .catch((err: unknown) => {
          // A failed check must not blank a good readout or raise a toast on every keystroke: the
          // readout keeps its last answer and the reason goes to the console.
          now.log("error", `setup.check: ${(err as Error).message}`);
        });
    }, SETUP_CHECK_DEBOUNCE_MS);
  },

  async execute(method, params = {}, options = {}) {
    const client = get().client;
    if (!client) throw new Error("not connected");
    const secret = touchesSecret(params);
    if (!options.silent) get().log("command", `${method} ${JSON.stringify(secret ? redactParams(params) : params)}`);
    try {
      const result = await client.call(method, params);
      if (!options.silent) get().log("result", secret ? "(withheld: the parameter name suggests a secret)" : JSON.stringify(result));
      if (method === "geometry.import" || method === "geometry.import_step") noteImportStarted(result, params);
      // Every command may have changed the set-up (ADR-0022 §11.1): ask for a fresh readiness report,
      // debounced, so a burst of edits costs one check. `setup.check` itself is excluded, or the
      // readout would drive itself.
      if (method !== "setup.check") get().refreshSetup();
      return result;
    } catch (err) {
      // **The console keeps the code; the toast shows the sentence.**
      //
      // One place, so every failure is the same shape. The console line is unchanged (`code: message`)
      // because a reader of the log wants the identifier; the toast carries the message and offers the
      // code behind a disclosure, because a user needs to know what happened, not what we call it.
      const coded = err instanceof StokesApiError;
      const text = coded ? `${err.code}: ${err.message}` : (err as Error).message;
      get().log("error", text);
      if (!options.silent) {
        get().pushToast("error", coded ? err.message : text, undefined, coded ? err.code : undefined);
      }
      throw err;
    }
  },

  // Selecting a node moves the stage chips to the stage that works in the node's group (a patch
  // under /geometry selects Geometry), unless the workspace unlinked them (`tree.linkStage`). The tree
  // itself is left as it is: only a chip or a stage key (setStage) opens and folds its groups.
  setOutlineRow(id) {
    set({ outlineRow: id });
  },

  setMeshTask(task) {
    set({ meshTask: task });
  },

  selectRow(id, gesture, visible) {
    const state = get();
    const shift = gesture.shift === true;
    const ctrl = gesture.ctrl === true;
    const next = shift ? extend(state.selection, id, visible, ctrl) : ctrl ? toggle(state.selection, id) : selectOne(id);
    // **The primary is the row the rest of the UI follows**, so `selectedId` is always it - which is what
    // keeps the sixteen readers of `selectedId` correct without touching any of them.
    /*
      **The 3D view follows too** (claude-win2's 1400 file: *"selecting bodies/tags/boundaries in the
      tree highlights all of them in 3D ... one selection store, two views"*).

      `select` above already picks ONE body; this picks the whole set, and it is the same `Picked` the
      viewport drawer reads - so there is no second copy of "what is highlighted" to disagree.
    */
    const partIds = next.ids.filter((id) => findNode(state.tree, id)?.type === "part");
    const picked: Picked = partIds.length === 0 ? NO_PICK : { bodies: partIds, faces: {}, edges: {}, vertices: {} };
    /*
      **The stage follows the PRIMARY row here too.** Measured: clicking a Physics region in the outline set
      `selectedId` and left the stage chip on Geometry, while `select(id)` - the other entry point - moved
      it to Physics. **Every row click goes through this function**, so `stageForPath`'s rule had never run
      for a click in either tree; the two entry points now apply the same rule.
    */
    const primaryNode = next.primary === null ? null : findNode(state.tree, next.primary);
    const stage =
      primaryNode !== null && stageLinked() && !selectKeepsStage(state.stage, primaryNode.type)
        ? stageForPath(primaryNode.path, state.stage)
        : null;
    set({
      selection: next,
      selectedId: next.primary,
      ...(stage !== null && stage !== state.stage ? { stage } : {}),
      ...changedPick(state, picked, false),
      selectedPatch: next.primary === null || partIds.length === 0 ? null : patchForNode(findNode(state.tree, next.primary), state.geometry),
      // **A tree choice outranks a stepper task page** (brief M-502): the task state exists so the
      // Geometry check and Bodies pages can open from the stepper, and any other selection ends it.
      meshTask: null,
    });
    // A tag's faces arrive from `tag.members`; choosing one still means picking it.
    if (next.primary !== null && findNode(state.tree, next.primary)?.type === "tag") void pickTag(next.primary);
  },

  selectSiblings() {
    const state = get();
    const rows: VisibleRow[] = [];
    const visit = (node: NodeSummary, parent: string | null): void => {
      rows.push({ id: node.id, parent, type: node.type });
      for (const child of node.children ?? []) visit(child, node.id);
    };
    if (state.tree !== null) visit(state.tree, null);
    set({ selection: selectSiblingsOf(state.selection, rows) });
  },

  clearSelection() {
    set({ selection: NO_SELECTION, selectedId: null });
  },

  select(id, options) {
    const state = get();
    const node = findNode(state.tree, id);
    const stage = node && stageLinked() && !selectKeepsStage(state.stage, node.type) ? stageForPath(node.path, state.stage) : null;
    const revealRequest = state.revealRequest + (id === null ? 0 : 1);
    if (options?.keepPick) {
      // Naming the pick selects the new tag and leaves the pick, its mode and active member alone.
      const kept = { selectedId: id, revealRequest };
      set(stage && stage !== state.stage ? { ...kept, stage } : kept);
      return;
    }
    const selectedPatch = patchForNode(node, state.geometry);
    // A part node is a body pick; a tag node picks its members (they arrive from tag.members);
    // anything else, and null, empties the pick (ADR-0021 3.1, 3.5).
    const picked: Picked = node?.type === "part" ? { bodies: [id as string], faces: {}, edges: {}, vertices: {} } : NO_PICK;
    const next = { selectedId: id, selectedPatch, ...changedPick(state, picked, false), revealRequest, meshTask: null };
    set(stage && stage !== state.stage ? { ...next, stage } : next);
    if (node?.type === "tag" && id !== null) void pickTag(id);
  },

  selectPatch(name) {
    set({ selectedPatch: name });
    if (!name) return;
    const { tree, stage, revealRequest, geometry } = get();
    // A patch of an imported part belongs to that part's node; a patch of the sample part to the
    // tag or part that carries its name, if the project has one.
    const owner = geometry.find((g) => g.name === name)?.part ?? null;
    const node =
      owner !== null
        ? findNode(tree, owner)
        : (findByPath(tree, `/geometry/tags/${name}`) ?? findByPath(tree, `/geometry/parts/${name}`));
    // Picking in the viewport is cross-stage work (assigning a boundary happens in Physics), so a
    // patch pick reveals its tree row but never moves the stage chip; tree selection does that.
    void stage;
    set({ selectedId: node?.id ?? null, revealRequest: revealRequest + 1 });
  },

  hover: (name) => set({ hoverPatch: name }),

  // The pick (ADR-0021 3.1). In body mode `selectedPatch` stays the active member: the patch of the
  // last body picked, so the properties card, the context toolbar anchor and Isolate and Hide keep
  // working. A face pick does not set it (T-061): it would outline the whole body. Not persisted.
  /**
   * Switching the mode keeps the pick (ADR-0022 6.3) instead of dropping it: the faces become the
   * bodies that own them, and the bodies become all their faces. The active member is kept while the
   * converted pick still holds its part, otherwise a body pick takes the patch of the pick's own last
   * body — what a click on it would leave — and a face pick takes none (T-061). `clearPick` and
   * `Escape` are what empties the pick, unchanged.
   */
  /** The viewport hands its scene-backed accessors over so mode changes can expand a pick (A6). */
  setConversionTopology(topology) {
    set({ conversionTopology: topology });
  },
  setTopologyLoaded(parts) {
    set({ topologyLoaded: parts });
  },
  setPickMode(pickMode) {
    const s = get();
    if (pickMode === s.pickMode) return;
    const picked = convertPick(s.picked, pickMode, (part) => faceCountFor(s.geometry, part), s.conversionTopology ?? undefined);
    const held = pickMode === "body" ? picked.bodies : Object.keys(picked.faces);
    const owner = s.selectedPatch === null ? null : partOfPatch(s.geometry, s.selectedPatch);
    const active = pickMode === "body" ? (picked.bodies[picked.bodies.length - 1] ?? null) : null;
    const own = owner !== null && held.includes(owner) ? s.selectedPatch : active === null ? null : patchForSource(active, s.geometry);
    const node = active === null || active === SAMPLE_SOURCE ? null : findNode(s.tree, active);
    set({
      ...changedPick(s, picked, false),
      pickMode,
      selectedPatch: own,
      selectedId: node?.type === "part" ? node.id : null,
    });
  },

  // Paint select (T-043). The mode is view state alone: the stroke runs in the viewport, which
  // reports every new face (or body) it crosses to `pickMany`. Switching Body/Face keeps it.
  setPaintMode: (paintMode) => set({ paintMode }),

  // Lasso or box (T-032). View state alone: the drag itself lives in the viewport, and what it
  // selects reaches the store through `pickMany`, the one bulk path (ADR-0022 6.5).
  setLassoMode: (lassoMode) => set({ lassoMode }),

  /**
   * One update for the whole set (T-043): the stroke reports each picked face or body as it goes,
   * and T-032's box and lasso report their whole result at once. The pick's own rules are in
   * state/pick.ts; one call leaves the pick it replaced for `selection.previous`, never one entry
   * per item. Nothing is revealed or scrolled here: a stroke paints many items and the tree must
   * not jump under it.
   */
  pickMany(items, how) {
    const s = get();
    const { picked, active } = applyMany(s.picked, items, how);
    // A face pick selects the faces and nothing else (T-061), as `pickFace` leaves it; a body pick
    // keeps the active member's patch, as `pickBody` does with the patch it was clicked on.
    const byFace = Object.values(picked.faces).some((ids) => ids.length > 0);
    const own = byFace || active === null ? null : patchForSource(active, s.geometry);
    const node = byFace || active === null || active === SAMPLE_SOURCE ? null : findNode(s.tree, active);
    /*
      **The TREE SELECTION follows the viewport** (claude-win2's 1400 file: *"selecting in the viewport
      with Ctrl/Shift (click, box, lasso) selects the same rows in the tree (one selection store, two
      views)"*). It used to set `selectedId` alone - ONE node, the active member - so a box over three
      bodies highlighted three in 3D and one row in the tree.

      **A face pick chooses no rows**: the brief's selection is of bodies, and a face is not a row.
      Taken from the PICKED BODIES rather than the items, so "add" and "remove" both come out right.
    */
    const bodyNodes = byFace
      ? []
      : picked.bodies.map((id) => findNode(s.tree, id)).filter((n): n is NonNullable<typeof n> => n?.type === "part");
    set({
      ...changedPick(s, picked, false),
      selectedPatch: own,
      selectedId: node?.type === "part" ? node.id : null,
      selection: bodyNodes.length === 0 ? NO_SELECTION : { ids: bodyNodes.map((n) => n.id), primary: node?.id ?? bodyNodes[0]!.id, anchor: node?.id ?? bodyNodes[0]!.id },
    });
  },

  pickBody(part, how, patch) {
    const s = get();
    const { picked, active } = pickAfterBody(s.picked, part, how);
    // The active member is the body just picked, or after a toggle-off the last one left: that body's
    // single patch, as a tree selection of the part node would give, or the clicked patch when the
    // source has several (the sample part).
    const own = activePatch(active, part, patch, (source) => patchForSource(source, s.geometry));
    const node = active === null || active === SAMPLE_SOURCE ? null : findNode(s.tree, active);
    set({
      ...changedPick(s, picked, false),
      selectedPatch: own,
      selectedId: node?.type === "part" ? node.id : null,
      revealRequest: s.revealRequest + 1,
    });
    // The sample part has no part node: its patch selects the tag or part that carries its name, as before.
    if (active === SAMPLE_SOURCE && own) get().selectPatch(own);
  },

  // The body double-click (ADR-0022 6.2): every part under the clicked part's parent group — the
  // import file's group (one per file) — the clicked part first, with the gesture `how` of the click.
  pickSiblings(part, how, patch) {
    const s = get();
    const parts = siblingsOf(part, partSiblings(s.tree, part));
    const { picked, active } = pickAfterBodies(s.picked, parts, how);
    const own = activePatch(active, part, patch, (source) => patchForSource(source, s.geometry));
    const node = active === null || active === SAMPLE_SOURCE ? null : findNode(s.tree, active);
    set({
      ...changedPick(s, picked, false),
      selectedPatch: own,
      selectedId: node?.type === "part" ? node.id : null,
      revealRequest: s.revealRequest + 1,
    });
    // The sample part has no part node and no sibling: its patch is selected as a click would.
    if (active === SAMPLE_SOURCE && own) get().selectPatch(own);
  },

  pickFace(part, face, how) {
    if (face < 0) return;
    const s = get();
    // The pick's own rules live in state/pick.ts (T-040); the active member they return is what the
    // context toolbar works from, through `activeFacePart` above.
    const { picked } = pickAfterFace(s.picked, part, face, how);
    // A face pick selects the faces and nothing else (T-061). `selectedPatch` is what the viewport
    // outlines and lifts, and what the part's card and row follow, so keeping it here made the whole
    // body look and act selected; `selectedId` would select the part's row in the tree.
    set({ ...changedPick(s, picked, false), selectedPatch: null, selectedId: null });
  },

  /**
   * A whole SET of edges or vertices on one part (ADR-0022 §15.4): a double-clicked wire, a point's
   * incident edges, or a triple-click's whole body.
   *
   * A set rather than one id because all three escalations ARE sets, and going through one bulk path
   * keeps the all-or-nothing toggle rule in one place (`toggleTopology`) instead of three.
   */
  pickTopologyMany(part, kind, ids, how) {
    if (ids.length === 0) return;
    const s = get();
    const held = (kind === "edge" ? s.picked.edges : s.picked.vertices)[part] ?? [];
    const all: Record<string, number[]> =
      how === "replace"
        ? {}
        : { ...(kind === "edge" ? s.picked.edges : s.picked.vertices) };
    const next = toggleTopology(held, ids, how);
    if (next.length) all[part] = next;
    else delete all[part];
    const owners = Object.keys(all);
    const picked: Picked = {
      bodies: [],
      faces: {},
      edges: kind === "edge" ? all : {},
      vertices: kind === "vertex" ? all : {},
    };
    // As a single topology pick leaves it (T-061): no patch is selected, so the whole body is not
    // outlined and lifted while a wire is picked, and the pick keeps its history entry exactly as
    // `pickTopology` does.
    void owners;
    set({ ...changedPick(s, picked, false), selectedPatch: null, selectedId: null });
  },

  pickTopology(part, kind, id, how) {
    const s = get();
    const { picked } = pickAfterTopology(s.picked, part, kind, id, how);
    // As a face pick leaves it (T-061): the pick holds topology and selects no patch, so the whole
    // body is not outlined and lifted while one edge is picked.
    set({ ...changedPick(s, picked, false), selectedPatch: null, selectedId: null });
  },

  // The face triple-click (ADR-0022 6.2): every face of the clicked body. A part the transport gives
  // no face ids for (the sample part) has none to pick, and is left as it is.
  pickAllFaces(part, how) {
    const s = get();
    const faces = allFacesOf(faceCountFor(s.geometry, part));
    if (faces.length === 0) return;
    const { picked } = pickAfterFaces(s.picked, part, faces, how);
    // Picking faces selects faces alone, as a face click leaves it (T-061).
    set({ ...changedPick(s, picked, false), selectedPatch: null, selectedId: null });
  },

  /**
   * Bring back the pick before the last change (ADR-0022 6.7): the newest entry of the history is
   * applied and dropped, and it is converted when it is the other mode's pick. A change of its own,
   * so it keeps nothing. A body pick gets its active member back, as `pickBody` leaves it; a face
   * pick selects no patch (T-061).
   */
  pickPrevious() {
    const s = get();
    const stored = s.pickHistory[s.pickHistory.length - 1];
    if (stored === undefined) return;
    const picked = pickInMode(stored, s.pickMode, (part) => faceCountFor(s.geometry, part), s.conversionTopology ?? undefined);
    const active = s.pickMode === "body" ? (picked.bodies[picked.bodies.length - 1] ?? null) : null;
    const own = active === null ? null : patchForSource(active, s.geometry);
    const node = active === null || active === SAMPLE_SOURCE ? null : findNode(s.tree, active);
    set({
      picked,
      pickHistory: s.pickHistory.slice(0, -1),
      selectedPatch: own,
      selectedId: node?.type === "part" ? node.id : null,
      revealRequest: s.revealRequest + (active === null ? 0 : 1),
    });
    if (active === SAMPLE_SOURCE && own) get().selectPatch(own);
  },

  setHoverInfo: (hoverInfo) => set({ hoverInfo }),

  clearPick() {
    const s = get();
    set({ ...changedPick(s, NO_PICK, false), selectedPatch: null, selectedId: null });
  },

  setGeometry(patches) {
    const s = get();
    if (sameMetas(s.geometry, patches)) return;
    const names = new Set(patches.map((p) => p.name));
    const next: Partial<StokesState> = { geometry: patches };
    // Selection state that names a patch which is no longer drawn (a part was removed, or the first
    // part replaced the sample) goes with it.
    if (s.selectedPatch !== null && !names.has(s.selectedPatch)) next.selectedPatch = null;
    if (s.hoverPatch !== null && !names.has(s.hoverPatch)) next.hoverPatch = null;
    if (s.isolatedPatch !== null && !names.has(s.isolatedPatch)) next.isolatedPatch = null;
    if (s.hiddenPatches.some((n) => !names.has(n))) next.hiddenPatches = s.hiddenPatches.filter((n) => names.has(n));
    // Picks whose parts left the scene go with them (ADR-0021 3.1). The parts are indexed once, not
    // searched once per picked body: this runs on EVERY mesh that arrives, and during a large load
    // `patches` grows to thousands while the previous pick is still held, so a `some` per body was
    // quadratic in the part count — a measured part of the 14 s a 4,500-part load spent applying
    // meshes (T-096). A body id in this set is drawn; the sample part is the one with no part id.
    const drawn = new Set<string>();
    for (const patch of patches) drawn.add(patch.part ?? SAMPLE_SOURCE);
    if (s.picked.bodies.length > 0) {
      const bodies = s.picked.bodies.filter((id) => drawn.has(id));
      if (bodies.length !== s.picked.bodies.length) next.picked = { ...s.picked, bodies };
    }
    if (Object.keys(s.picked.faces).length > 0) {
      const faces: Record<string, number[]> = {};
      for (const [part, ids] of Object.entries(s.picked.faces)) if (drawn.has(part)) faces[part] = ids;
      if (Object.keys(faces).length !== Object.keys(s.picked.faces).length) {
        next.picked = { ...(next.picked ?? s.picked), faces };
      }
    }
    // The selected node may be a part whose mesh has only now arrived.
    if (s.selectedPatch === null && s.selectedId !== null) {
      const own = patchForNode(findNode(s.tree, s.selectedId), patches);
      if (own !== null) next.selectedPatch = own;
    }
    set(next);
  },

  renamePatches(renames) {
    if (renames.size === 0) return;
    const follow = (name: string | null): string | null => (name === null ? null : (renames.get(name) ?? name));
    set((s) => ({
      // The list is renamed here too, not left for the viewport's next report: a refresh of the tree
      // between the two would derive the selected patch from the old names and undo the rename.
      geometry: s.geometry.map((g) => (renames.has(g.name) ? { ...g, name: renames.get(g.name) ?? g.name } : g)),
      selectedPatch: follow(s.selectedPatch),
      hoverPatch: follow(s.hoverPatch),
      isolatedPatch: follow(s.isolatedPatch),
      hiddenPatches: s.hiddenPatches.map((n) => follow(n) ?? n),
    }));
  },
  toggleExpanded: (id) => set((s) => ({ expanded: { ...s.expanded, [id]: !s.expanded[id] } })),
  setTreeFilter: (treeFilter) => set({ treeFilter }),

  // Called by the stage chips and the stage keys, and by nothing else: the tree opens the stage's
  // groups (the workspace's `tree.stages`), folds the other top-level groups and (TreePanel, on
  // stageFocus) scrolls to the first one. What the user opened or folded below the top level is left
  // alone, so a stage comes back the way it was left. With the link off (`tree.linkStage`) the stage
  // changes and the tree stays as it is. The load does not come through here: it opens the tree to
  // `tree.defaultExpanded` levels.
  setStage(stage) {
    const { tree, expanded, stageFocus } = get();
    if (!stageLinked()) set({ stage, stageFocus: stageFocus + 1 });
    else set({ stage, expanded: stageExpanded(stage, tree, expanded), stageFocus: stageFocus + 1 });
  },
  setTheme: (theme) => set({ theme }),
  toggleDrawer: (open) => set((s) => ({ drawerOpen: open ?? !s.drawerOpen })),
  setDrawerTab: (drawerTab) => set({ drawerTab, drawerOpen: true }),
  setPalette: (paletteOpen) => set({ paletteOpen }),

  pushToast(kind, text, action, code) {
    const id = toastId++;
    set((s) => ({ toasts: [...s.toasts.slice(-3), { id, kind, text, action, code }] }));
    window.setTimeout(() => get().dismissToast(id), kind === "error" || action ? 6000 : 3000);
  },
  dismissToast: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
  log(kind, text) {
    // Lines the events queued for the next frame come first: the console keeps the order things happened in.
    const lines = [...(sync?.takeLog() ?? []), { kind, text }];
    set((s) => ({ consoleLines: withLines(s.consoleLines, lines) }));
  },
  clearConsole: (kinds) => set((s) => ({ consoleLines: kinds ? s.consoleLines.filter((l) => !kinds.includes(l.kind)) : [] })),
  requestFit: (all = false) =>
    set((s) =>
      all
        ? { fitRequest: s.fitRequest + 1, fitAllRequest: s.fitAllRequest + 1 }
        : { fitRequest: s.fitRequest + 1 },
    ),
  requestSection: () => set((s) => ({ sectionRequest: s.sectionRequest + 1 })),

  // The operation card (T-029). The pick is read once, when the card opens: the form is filled in
  // and must not change under the user because the selection moved on behind it.
  openOperation(type, params = {}) {
    const s = get();
    const faces = pickAsMembers(s.picked, "face");
    const bodies = pickAsMembers(s.picked, "body");
    const parts = faces !== null ? faces.members.map((m) => m.part) : (bodies?.members.map((m) => m.part) ?? []);
    set({
      operationCard: {
        id: ++cardId,
        type,
        params,
        parts,
        // A face pick's faces go to the entries that take them (Remove faces); null otherwise.
        // A face pick's faces go to the entries that take them; an edge pick's members are not
        // faces, so the card takes none of them.
        faces:
          faces !== null && s.pickMode === "face"
            ? faces.members.filter(
                (m): m is { part: string; faces: Array<number | [number, number]> | "all" } =>
                  "faces" in m,
              )
            : null,
        nodeId: null,
        error: "",
        cancelled: false,
      },
      seedPick: false,
    });
  },
  closeOperationCard: () => set({ operationCard: null, seedPick: false }),
  setSeedPick: (on) => set({ seedPick: on }),
  setOperationNode(cardId, id) {
    const card = get().operationCard;
    if (card === null || card.id !== cardId || card.nodeId === id) return;
    set({ operationCard: { ...card, nodeId: id } });
  },
  setOperationError(message, cancelled = false) {
    const card = get().operationCard;
    if (card === null || (card.error === message && card.cancelled === cancelled)) return;
    set({ operationCard: { ...card, error: message, cancelled } });
  },
}));

// ---------------------------------------------------------------------------------------------- the event layer for parts
// These read and write the store through `useStore`, which the module has by the time any event arrives.

/** True when two patch lists hold the very same meta objects: the viewport replaces a meta when it changes. */
function sameMetas(a: readonly PatchMeta[], b: readonly PatchMeta[]): boolean {
  return a.length === b.length && a.every((meta, i) => meta === b[i]);
}

/** The console after `lines` were written: the last 500 of them are kept. */
function withLines(current: readonly ConsoleLine[], lines: readonly LogLine[]): ConsoleLine[] {
  const at = Date.now();
  return [...current, ...lines.map((line) => ({ id: lineId++, kind: line.kind, text: line.text, at }))].slice(-500);
}

/** The fields a node event carries for patching the tree: the console shows the event as it was before them. */
const PATCH_FIELDS: Record<string, readonly string[]> = {
  node: ["node", "also", "parent", "index", "resync"],
  geometry: ["node", "also", "resync"],
};

/** An event's data as the console shows it: without the summaries a node event carries for the tree. */
function loggedData(event: string, data: Record<string, unknown>): string {
  const fields = PATCH_FIELDS[event.slice(0, event.indexOf("."))];
  if (!fields || !fields.some((field) => field in data)) return JSON.stringify(data);
  const shown = { ...data };
  for (const field of fields) delete shown[field];
  return JSON.stringify(shown);
}

let logWindowStart = 0;
let logWindowLines = 0;
/** Lines of geometry events allowed per second in the console; a file of thousands of solids sends thousands of events. */
const GEOMETRY_LOG_LINES_PER_SECOND = 12;

function geometryLogAllowed(event: GeometryEvent): boolean {
  if (event.kind === "failed") return true;
  return progressLogAllowed();
}

/** The same budget for a run's progress: an operation reports up to ten steps a second. */
function progressLogAllowed(): boolean {
  const now = Date.now();
  if (now - logWindowStart > 1000) {
    logWindowStart = now;
    logWindowLines = 0;
  }
  return ++logWindowLines <= GEOMETRY_LOG_LINES_PER_SECOND;
}

/** A project was opened or closed: every mesh and import of the old one is gone. The tree that follows re-creates what the new one has. */
function dropMeshes(): void {
  for (const burst of failures.values()) window.clearTimeout(burst.timer);
  failures.clear();
  useStore.setState({
    parts: {},
    importProgress: {},
    operations: {},
    mesh: NO_MESH_LIVE,
    operationCard: null,
    seedPick: false,
    pendingSelect: null,
    // **The section is per project and goes with the parts** (Claude's defect 4). The rule is in
    // `sectionState.ts`, which Vitest can import (this module touches `window`).
    section: sectionOnProjectChange(),
    // And the popover is not left open over a project it was opened for.
    sectionRequest: 0,
    // The slices are planes of the old project's model.
    slices: NO_SLICES,
    // A line is two points of the old project's model.
    line: NO_LINE,
  });
}

// ---------------------------------------------------------------------------------------------- the operation events
/**
 * Applies one `geometry.op_*` event (T-029): the progress of a run, its end, its failure. Handled
 * here rather than in the tree sync, because a failure and a progress step change no node: the
 * sync's queue carries node patches and geometry events only.
 *
 * `geometry.op_done` closes the card that ran it and asks for the first output to be selected;
 * the `node.added` event that brings that output is still queued behind this one, so the selection
 * waits for the tree (`pendingSelect`, applied by the subscription below). A failure shows its
 * message in the card's red error line, as a failed node does in the properties panel.
 */
function applyOperationEvent(op: OperationEvent): void {
  const state = useStore.getState();
  switch (op.kind) {
    case "progress": {
      const node = findNode(state.tree, op.nodeId);
      const label = node ? (state.operationTypes[node.type]?.label ?? "") : "";
      useStore.setState({ operations: noteOperationProgress(state.operations, op, label, Date.now()) });
      return;
    }
    case "done": {
      useStore.setState({ operations: dropOperation(state.operations, op.nodeId) });
      const card = state.operationCard;
      // Recorded before the card's early return: a flow helper watches this to run the default
      // conditions on the node its operation just made (T-050).
      const finished = findNode(state.tree, op.nodeId);
      useStore.setState({
        lastOperationDone: {
          nodeId: op.nodeId,
          type: finished?.type ?? "",
          output: op.outputs[0] ?? null,
        },
      });
      if (card === null || card.nodeId !== op.nodeId) return;
      useStore.setState({ operationCard: null, seedPick: false, pendingSelect: op.outputs[0] ?? null });
      return;
    }
    case "failed": {
      useStore.setState({ operations: dropOperation(state.operations, op.nodeId) });
      const card = state.operationCard;
      // A cancel arrives as an `op_failed` too (`geometry.cancelled`): the card says it quietly.
      if (card !== null && card.nodeId === op.nodeId) useStore.getState().setOperationError(op.message, op.code === "geometry.cancelled");
      return;
    }
  }
}

/**
 * A finished operation's first output is selected as soon as the tree holds it. The check is a
 * subscription on the tree alone, so nothing walks the tree on any other update.
 */
useStore.subscribe((state, previous) => {
  const id = state.pendingSelect;
  if (id === null || state.tree === previous.tree) return;
  if (findNode(state.tree, id) === null) return;
  useStore.setState({ pendingSelect: null });
  useStore.getState().select(id);
});

/** A cancel or a crash fails every unfinished part of an import at once; they are told as one toast. */
interface FailureBurst {
  code: string;
  message: string;
  name: string;
  count: number;
  timer: number;
}
const failures = new Map<string, FailureBurst>();
const FAILURE_BURST_MS = 250;

function reportFailure(event: ImportFailedEvent): void {
  const key = `${event.code}\n${event.message}`;
  const burst = failures.get(key);
  if (burst) {
    burst.count++;
    return;
  }
  const entry: FailureBurst = { code: event.code, message: event.message, name: event.path ? basename(event.path) : "", count: 1, timer: 0 };
  entry.timer = window.setTimeout(() => {
    failures.delete(key);
    announceFailure(entry);
  }, FAILURE_BURST_MS);
  failures.set(key, entry);
}

function announceFailure(burst: FailureBurst): void {
  const { pushToast, log } = useStore.getState();
  const parts = burst.count > 1 ? ` (${burst.count} parts)` : "";
  // The user asked for a cancel: it is news, not an error.
  if (burst.code === "geometry.cancelled") {
    pushToast("info", `Import cancelled${parts}`);
    return;
  }
  const subject = burst.count === 1 && burst.name && !burst.message.includes(burst.name) ? ` (${burst.name})` : "";
  const text = `Import failed: ${burst.message}${parts}${subject}`;
  log("error", text);
  pushToast("error", text);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * `geometry.import` answers as soon as the placeholder part exists, long before the server's
 * first progress event (the file is read first, and a cold worker takes seconds to start). The
 * answer says the import is running, so the progress line starts there.
 */
function noteImportStarted(result: unknown, params: Record<string, unknown>): void {
  if (!isRecord(result) || typeof result.path !== "string" || !isRecord(result.import) || result.import.status !== "importing") return;
  const path = result.path;
  const file = typeof params.path === "string" ? basename(params.path) : "";
  useStore.setState((s) => ({ importProgress: noteStarted(s.importProgress, path, file, Date.now()) }));
}
