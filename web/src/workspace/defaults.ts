/**
 * The built-in workspace (the Stokes arrangement of web/DESIGN.md) and a defensive reader for the
 * documents the server sends.
 *
 * DEFAULT_DOCUMENT mirrors `stokes.workspace.defaults.default_document()`, which
 * docs/engineering/workspace-schema.md prints in full. The front end uses it before the first
 * `workspace.get` answers and against a server that has no `workspace.*` commands. The server
 * remains the authority: whatever it sends replaces this.
 */
import type { Corner, CustomTheme, ImportUnit, LayoutSpec, MouseScheme, PanelPlacement, SlotSpec, ThemeSpec, TopbarItemSpec, WorkspaceDocument } from "./types";

export const SLOTS = ["left", "right", "bottom", "float"] as const;
export const CORNERS: readonly Corner[] = ["bl", "br", "tl", "tr"];
export const STAGE_NAMES = ["Geometry", "Mesh", "Physics", "Solve", "Results", "Explore"] as const;
export const THEME_NAMES = ["dark", "light", "cupertino", "cupertino-dark", "ubuntu", "ubuntu-dark", "auto", "cupertino-auto", "ubuntu-auto"] as const;
export const BASE_THEMES = ["dark", "light", "cupertino", "cupertino-dark", "ubuntu", "ubuntu-dark"] as const;
export const OUTPUT_TABS = ["console", "log", "residuals", "reports"] as const;
/** The mouse schemes and import units the `preferences` section accepts (stokes/workspace/schema.py). */
export const MOUSE_SCHEMES: readonly MouseScheme[] = ["cad", "paraview", "blender", "toggle"];
export const IMPORT_UNITS: readonly ImportUnit[] = ["mm", "cm", "m", "in", "ft"];

export const DEFAULT_TOPBAR_ORDER = [
  "logo",
  "project",
  "status.connection",
  "status.units",
  "status.engine",
  "status.licence",
  "stages",
  "commands",
  "appearance",
  "run",
  "window",
] as const;

// `/monitors` was dropped from the skeleton on 2026-10-18: nothing created or read a node in
// it. The Monitor rows a user reads are virtual, drawn from a run under Results.
export const DEFAULT_GROUP_ORDER = ["geometry", "mesh", "physics", "regions", "solver", "scenes", "runs", "studies"] as const;

export const DEFAULT_STAGE_PATHS: Record<string, string[]> = {
  Geometry: ["/geometry"],
  Mesh: ["/mesh"],
  Physics: ["/physics", "/regions"],
  // `/monitors` was dropped from the skeleton on 2026-10-18: nothing created or read a node
  // in it. The Monitor rows are virtual, drawn from a run under Results.
  Solve: ["/solver", "/runs"],
  Results: ["/scenes", "/runs"],
  Explore: ["/studies"],
};

export const DEFAULT_SHORTCUTS: Record<string, string | string[]> = {
  "stage.1": "1",
  "stage.2": "2",
  "stage.3": "3",
  "stage.4": "4",
  "stage.5": "5",
  "stage.6": "6",
  "view.fit": "F",
  "view.projection": "O",
  "view.mode.cycle": "V",
  "view.nav.cycle": "Shift+N",
  "view.section": "S",
  "view.measure": "M",
  // **The package-two chords** (claude-win2 approved 2026-10-17). Letters fire only while the
  // viewport has focus - the keymap refuses a plain key in a text field, which is the rule.
  "selection.name": "Ctrl+Shift+T",
  "mesh.generate": "Ctrl+G",
  // The guided-meshing stepper (brief M-502): between tasks, in the Mesh stage. `Alt+↑/↓` is
  // free - no default action claims it - and the arrows cannot type into a text field.
  "mesh.task.next": "Alt+ArrowDown",
  "mesh.task.previous": "Alt+ArrowUp",
  "pick.mode.cycle": "B",
  "selection.grow": "G",
  "selection.lasso": "L",
  "selection.isolate": "I",
  "selection.hide": "H",
  "selection.clear": "Escape",
  "selection.mode": [],
  "selection.previous": [],
  "selection.paint": "P",
  // The geometry operations (T-029): no default chords; app/PrepareMenu.tsx has the handlers.
  "prepare.enclosure": [],
  "prepare.extractFluid": [],
  "prepare.boolean": [],
  "prepare.repair": [],
  "palette.open": "Ctrl+K",
  "history.undo": "Ctrl+Z",
  "history.redo": ["Ctrl+Y", "Ctrl+Shift+Z"],
  "project.save": "Ctrl+S",
  "project.saveAs": "Ctrl+Shift+S",
  "project.importStep": "Ctrl+I",
  "project.open": "Ctrl+O",
  // **`Ctrl+N` and `Ctrl+W` are the menu spec's numbers.** `Ctrl+W` is the browser's close-tab
  // chord, which the desktop host owns anyway; in a browser tab it still reaches the keymap because
  // the socket page does not let the default through while a project is open.
  "project.new": "Ctrl+N",
  "project.close": "Ctrl+W",
  "project.saveCopy": [],
  "project.startScreen": [],
  "display.settings": "Ctrl+,",
  "workspace.customise": "Ctrl+Shift+C",
  // **The two panel toggles** (the owner, via claude-win2 1930): the chords, kept beside the other
  // layout actions so a test can check the browser and the server agree.
  "layout.toggleTree": "Ctrl+B",
  "layout.toggleDetails": "Ctrl+Shift+B",
  "layout.reset": [],
  "view.fitAll": "Alt+F",
  // T-176: the actions the context menu and Settings add. `Ctrl+,` stays on `display.settings`
  // (owner decision 2026-10-14), so Settings gets `Ctrl+Shift+,`. Those without a chord are
  // reached from a menu.
  "app.settings": "Ctrl+Shift+,",
  "selection.clearPick": [],
  "context.menu": ["Shift+F10", "ContextMenu"],
  "view.fitSelection": [],
  "view.showAll": "Shift+F",
  "view.from.front": "Alt+1",
  "view.from.back": "Alt+2",
  "view.from.left": "Alt+3",
  "view.from.right": "Alt+4",
  "view.from.top": "Alt+5",
  "view.from.bottom": "Alt+6",
  "view.from.iso": "Alt+0",
  "selection.siblings": [],
  "selection.allFaces": [],
  "edit.rename": [],
  "edit.delete": [],
  "edit.copyName": [],
  "edit.copyPath": [],
  "tree.rename": "F2",
  "tree.delete": "Delete",
  "tree.menu": ["Shift+F10", "ContextMenu"],
};

function slot(size: number, collapsed: boolean, panels: PanelPlacement[]): SlotSpec {
  return { mode: "tabs", size, collapsed, panels };
}

export function defaultLayout(): LayoutSpec {
  return {
    // **The Details pane beside the tree** (the layout decision 2026-10-18): the tree selection is
    // the tab, so the left dock holds the outline and the page for what is selected in it.
    // **Split mode, not tabs**: the decision puts the tree and the pane SIDE BY SIDE, which is what
    // makes the selection-as-tab idea work - a tab strip would hide one behind the other.
    left: { ...slot(300, false, [{ id: "tree", order: 0 }, { id: "details", order: 1 }]), mode: "split" },
    // The Conditions panel (ADR-0022 §9.1, T-048) is a tab in the right slot beside Properties: the
    // set-up stage's flat view, which §9.2 keeps *beside* the one tree rather than replacing it.
    // The Mesh panel (ADR-0024, T-211b) is the tab before Conditions, in the order of the stages, the Solve panel (T-217) the tab after it, and the Results panel (T-218) the one after that.
    // **Collapsed by default** (claude-win2's 0230 ruling, review item 6): the stage panels are
    // pages for a selected node and live in the Details pane beside the tree, so leaving them here
    // drew Properties twice and cost the viewport 320 px. Nothing is deleted - a user can open the
    // slot or move the panels, and `classic` keeps its own right column.
    /*
      **The right slot has NO PANELS in the default arrangement** (the owner, via claude-win2:
      *"the collapsed right-hand icon strip is not needed. Remove it."*).

      It held `properties | mesh | conditions | solve | results | plots` collapsed to a 40 px rail of
      six icons. Three of those panels are **pages for a selected node** and live in the Details pane
      beside the tree; the rest are reachable from the Commands palette and the View menu. Keeping
      them placed is what drew the rail, and **nothing placed means nothing drawn**: `slotGeometry`
      gives an empty slot an `occupied` of 0, so the right side takes no width at all.

      **`classic` keeps its right column** - it is a deliberate arrangement somebody asked for - and
      a user who moves a panel to the right still gets a real dock with a close button.
    */
    right: slot(320, true, []),
    bottom: slot(240, true, [{ id: "output", order: 0, tabs: [...OUTPUT_TABS] }, { id: "design-table", order: 1 }]),
    float: slot(320, false, []),
    // **The stage panels are HIDDEN, not placed** (the owner's "remove the right strip"). `hidden` is
    // where a panel that exists and is not drawn is listed, so every built-in is accounted for once -
    // which is what the preset test checks - while nothing is drawn on the right.
    hidden: ["properties", "mesh", "conditions", "solve", "results", "plots"],
  };
}

export function defaultDocument(): WorkspaceDocument {
  return {
    version: 1,
    theme: { name: "auto" },
    layout: defaultLayout(),
    islands: { bl: ["modes"], br: ["view", "gizmo"], tl: [], tr: [], hidden: [] },
    topbar: {
      items: DEFAULT_TOPBAR_ORDER.map<TopbarItemSpec>((id) => ({ id, visible: true })),
      stages: { style: "chips" },
    },
    tree: {
      groupOrder: [...DEFAULT_GROUP_ORDER],
      hidden: [],
      stages: Object.fromEntries(Object.entries(DEFAULT_STAGE_PATHS).map(([k, v]) => [k, [...v]])),
      linkStage: true,
      defaultExpanded: 2,
      layout: "outline",
    },
    shortcuts: {
      ...DEFAULT_SHORTCUTS,
      "history.redo": ["Ctrl+Y", "Ctrl+Shift+Z"],
      "context.menu": ["Shift+F10", "ContextMenu"],
      "tree.menu": ["Shift+F10", "ContextMenu"],
    },
    // **Every display switch starts on** (the owner's display toggles: "defaults stay as today"), so
    // turning one off is a change the user made. Each corresponds to something `SceneManager` draws -
    // see the note in `stokes/workspace/schema.py`, which is where the list is justified.
    viewport: {
      projection: "perspective",
      viewMode: "shaded",
      grid: true,
      axisLines: true,
      shadow: true,
      reflections: true,
      edges: true,
      gizmo: true,
      scaleBar: true,
      background: "theme",
      lowDetail: false,
    },
    engines: { default: null, runPresets: {} },
    preferences: {
      mouse: { scheme: "paraview", orbitLeft: false },
      import: { units: "mm" },
      // **Empty means Auto** (the Settings spec §2): pick the best host that answers and
      // its newest supported install, so a user who never opens Compute still meshes.
      compute: { host: "", install: "", auto: true },
      // The guided-meshing stepper (ASK Q2): shown by default, collapsible to one line.
      mesh: { stepper: "shown" },
    },
  };
}

/**
 * Removes saved copies of the selection editor when Details is beside the tree.
 * Older layouts can still place the stage editors in the right dock; those pages
 * are already rendered by Details for the selected node. Other arrangements keep
 * their panels, so layouts with a separate editor location remain user-controlled.
 */
export function migrateDuplicateStageEditors(document: WorkspaceDocument): WorkspaceDocument {
  const leftIds = new Set(document.layout.left.panels.map((panel) => panel.id));
  if (!leftIds.has("tree") || !leftIds.has("details")) return document;

  const duplicateIds = new Set(["properties", "mesh", "conditions", "solve", "results"]);
  const removed = document.layout.right.panels.filter((panel) => duplicateIds.has(panel.id));
  if (removed.length === 0) return document;

  const removedIds = new Set(removed.map((panel) => panel.id));
  return {
    ...document,
    layout: {
      ...document.layout,
      right: {
        ...document.layout.right,
        panels: document.layout.right.panels.filter((panel) => !removedIds.has(panel.id)),
      },
      hidden: [...new Set([...document.layout.hidden, ...removed.map((panel) => panel.id)])],
    },
  };
}

// ---------------------------------------------------------------------------------------------- reading the wire
// The server sends a sanitised, complete document. It is still data from a socket: each section is
// checked for its shape and replaced by the default when it does not have it, so a renderer never
// meets a value of the wrong type.

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function strings(value: unknown): string[] | null {
  return Array.isArray(value) && value.every((v) => typeof v === "string") ? (value as string[]) : null;
}

function finite(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function readPanel(value: unknown): PanelPlacement | null {
  if (!isRecord(value) || typeof value.id !== "string") return null;
  const out: PanelPlacement = { ...value, id: value.id };
  if (out.order !== undefined && !finite(out.order)) delete out.order;
  if (out.size !== undefined && !finite(out.size)) delete out.size;
  if (out.collapsed !== undefined && typeof out.collapsed !== "boolean") delete out.collapsed;
  if (out.tabs !== undefined && !strings(out.tabs)) delete out.tabs;
  if (out.anchor !== undefined && !CORNERS.includes(out.anchor as Corner)) delete out.anchor;
  if (out.x !== undefined && !finite(out.x)) delete out.x;
  if (out.y !== undefined && !finite(out.y)) delete out.y;
  return out;
}

function readSlot(value: unknown, fallback: SlotSpec, side: string): SlotSpec {
  if (!isRecord(value)) return fallback;
  const panels = Array.isArray(value.panels) ? value.panels.map(readPanel).filter((p): p is PanelPlacement => p !== null) : fallback.panels;
  return {
    ...value,
    mode: value.mode === "split" ? "split" : "tabs",
    size: finite(value.size) ? value.size : fallback.size,
    /*
      **A stored `collapsed` is honoured, always.** An earlier version ignored `false` for the right
      slot to stop a document written before the collapse brought it back (claude-win2's leftover 1),
      and that rule was too broad: it also ignored the `false` written a second earlier by **opening**
      a panel there, so `Open Properties` put the panel in a 40 px strip. Measured.

      The problem it solved is solved where it belongs - the DEFAULT now places nothing in the right
      slot, so a document that opens the slot gets an open strip with no rail, and a document that
      does not mention it gets the collapsed default. A rule that overrides what the user just did is
      worse than the bug it fixes.
    */
    collapsed: typeof value.collapsed === "boolean" ? value.collapsed : fallback.collapsed,
    panels,
  };
}

function readTheme(value: unknown): ThemeSpec {
  if (!isRecord(value)) return { name: "auto" };
  if (typeof value.base === "string" && (BASE_THEMES as readonly string[]).includes(value.base)) {
    const tokens: Record<string, string> = {};
    if (isRecord(value.tokens)) for (const [k, v] of Object.entries(value.tokens)) if (typeof v === "string") tokens[k] = v;
    const theme: CustomTheme = { ...value, base: value.base as (typeof BASE_THEMES)[number], tokens };
    if (theme.title !== undefined && typeof theme.title !== "string") delete theme.title;
    return theme;
  }
  if (typeof value.name === "string" && (THEME_NAMES as readonly string[]).includes(value.name)) return { ...value, name: value.name as (typeof THEME_NAMES)[number] };
  return { name: "auto" };
}

/** The document the server sent, each section checked; what does not fit falls back to the default. */
export function readDocument(raw: unknown): WorkspaceDocument {
  const d = defaultDocument();
  if (!isRecord(raw)) return d;
  const layout = isRecord(raw.layout) ? raw.layout : {};
  const islands = isRecord(raw.islands) ? raw.islands : {};
  const topbar = isRecord(raw.topbar) ? raw.topbar : {};
  const tree = isRecord(raw.tree) ? raw.tree : {};
  const viewport = isRecord(raw.viewport) ? raw.viewport : {};
  const engines = isRecord(raw.engines) ? raw.engines : {};
  const preferences = isRecord(raw.preferences) ? raw.preferences : {};
  const mouse = isRecord(preferences.mouse) ? preferences.mouse : {};
  const imports = isRecord(preferences.import) ? preferences.import : {};
  const compute = isRecord(preferences.compute) ? preferences.compute : {};
  const mesh = isRecord(preferences.mesh) ? preferences.mesh : {};
  const items = Array.isArray(topbar.items)
    ? topbar.items.filter((i): i is TopbarItemSpec => isRecord(i) && typeof i.id === "string").map((i) => ({ ...i, visible: i.visible !== false }))
    : d.topbar.items;
  const stagesStyle = isRecord(topbar.stages) && (topbar.stages.style === "ribbon" || topbar.stages.style === "hidden") ? topbar.stages.style : "chips";
  const stageMap: Record<string, string[]> = {};
  if (isRecord(tree.stages)) for (const [k, v] of Object.entries(tree.stages)) if (strings(v)) stageMap[k] = v as string[];
  const shortcuts: Record<string, string | string[]> = {};
  if (isRecord(raw.shortcuts)) {
    for (const [k, v] of Object.entries(raw.shortcuts)) if (typeof v === "string" || strings(v)) shortcuts[k] = v as string | string[];
  }
  const document: WorkspaceDocument = {
    ...raw,
    version: 1,
    theme: readTheme(raw.theme),
    layout: {
      ...layout,
      left: readSlot(layout.left, d.layout.left, "left"),
      right: readSlot(layout.right, d.layout.right, "right"),
      bottom: readSlot(layout.bottom, d.layout.bottom, "bottom"),
      float: readSlot(layout.float, d.layout.float, "float"),
      hidden: strings(layout.hidden) ?? [],
    },
    islands: {
      ...islands,
      bl: strings(islands.bl) ?? d.islands.bl,
      br: strings(islands.br) ?? d.islands.br,
      tl: strings(islands.tl) ?? d.islands.tl,
      tr: strings(islands.tr) ?? d.islands.tr,
      hidden: strings(islands.hidden) ?? [],
    },
    topbar: { ...topbar, items, stages: { ...(isRecord(topbar.stages) ? topbar.stages : {}), style: stagesStyle } },
    tree: {
      ...tree,
      groupOrder: strings(tree.groupOrder) ?? d.tree.groupOrder,
      hidden: strings(tree.hidden) ?? [],
      stages: Object.keys(stageMap).length ? stageMap : d.tree.stages,
      linkStage: typeof tree.linkStage === "boolean" ? tree.linkStage : true,
      defaultExpanded: finite(tree.defaultExpanded) ? Math.max(0, Math.min(8, Math.round(tree.defaultExpanded))) : 2,
      // **A document without the key reads as the outline**: it is a new key, not a change of meaning.
      layout: tree.layout === "groups" ? "groups" : "outline",
    },
    shortcuts: Object.keys(shortcuts).length ? shortcuts : d.shortcuts,
    viewport: {
      ...viewport,
      projection: viewport.projection === "orthographic" ? "orthographic" : "perspective",
      viewMode: viewport.viewMode === "edges" || viewport.viewMode === "wireframe" ? viewport.viewMode : "shaded",
      grid: viewport.grid !== false,
      background: typeof viewport.background === "string" ? viewport.background : "theme",
      lowDetail: viewport.lowDetail === true,
    },
    engines: { ...engines, default: typeof engines.default === "string" ? engines.default : null, runPresets: isRecord(engines.runPresets) ? engines.runPresets : {} },
    preferences: {
      ...preferences,
      mouse: {
        ...mouse,
        scheme: MOUSE_SCHEMES.includes(mouse.scheme as MouseScheme) ? (mouse.scheme as MouseScheme) : "paraview",
        orbitLeft: mouse.orbitLeft === true,
      },
      import: { ...imports, units: IMPORT_UNITS.includes(imports.units as ImportUnit) ? (imports.units as ImportUnit) : "mm" },
      // **`compute` is normalised like the others, not merely spread.** `...preferences` keeps an
      // unknown key, but a document from before this existed has none - and a missing `auto` would
      // read as false, which means "use the host named by an empty string" rather than "choose for
      // me". Recorded here so the SHAPE is always complete, which is what the server's schema does
      // too.
      compute: {
        host: typeof compute.host === "string" ? compute.host : "",
        install: typeof compute.install === "string" ? compute.install : "",
        auto: compute.auto !== false,
      },
      mesh: {
        stepper: mesh.stepper === "hidden" ? "hidden" : "shown",
      },
    },
  };
  return migrateDuplicateStageEditors(document);
}
