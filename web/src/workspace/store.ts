/**
 * The workspace store (ADR-0015): the effective workspace document the interface is arranged by, and
 * the writes that change it. A zustand store of its own, separate from the project store, because the
 * workspace is not project model state: it has no undo, it is never in project.json, and its file is
 * written at once.
 *
 * Reading: `workspace.get` when the connection comes up (at start and after every reconnect) and
 * again after every `workspace.changed` event (and after a project opens, closes or is saved, which
 * can add or drop the project layer). Results that arrive out of order, or while a write of ours is
 * still in flight, are dropped and fetched again afterwards, so an optimistic change never flickers
 * back. A server without `workspace.*` commands leaves the built-in document in place and the
 * arrangement lasts for the session.
 *
 * Writing: every helper goes through the project store's `execute`, so the console shows the command
 * and the journal records it (folding a panel is the one quiet write: `silent`). The document is
 * changed locally first and the refetch that follows the write confirms it, or puts the server's
 * version back when the write was refused (execute has then shown why).
 *
 * The contract is docs/engineering/workspace-schema.md.
 */
import { create } from "zustand";
import { StokesApiError, type StokesClient } from "../api/client";
import { useStore } from "../state/store";
import { defaultDocument, readDocument } from "./defaults";
import { formatPath, setIn, type Segment } from "./paths";
import { setStageMap } from "./stageMap";
import type { CustomTheme, FilesInfo, PresetSummary, Scope, SideSlot, SlotName, WorkspaceDocument, WorkspaceWarning } from "./types";

export type WorkspaceStatus = "loading" | "ready" | "fallback";

export interface WriteOptions {
  /** Where to write; by default the Customise scope, or the project when the project already owns the path. */
  scope?: Scope;
  /** Keep the command out of the console (the journal still records it): folding a panel. */
  silent?: boolean;
}

export interface ImportOutcome {
  sections: string[];
  overridden: string[];
  warnings: WorkspaceWarning[];
}

export interface WorkspaceState {
  /** `loading` until the first document arrives; `fallback` renders the built-in one meanwhile (no server yet). */
  status: WorkspaceStatus;
  /** False against a server without `workspace.*`: changes then last for the session only. */
  supported: boolean;
  document: WorkspaceDocument;
  /** Canonical leaf path to the layer it comes from: `default`, `user` or `project`. */
  sources: Record<string, string>;
  warnings: WorkspaceWarning[];
  files: FilesInfo;
  presets: PresetSummary[];

  /** Customise mode (ADR-0015 §5): handles and drop zones; outside it nothing moves. */
  customising: boolean;
  /** The Customise scope toggle: this project (`project`) or all projects (`user`). */
  scope: Scope;
  displayOpen: boolean;
  /** The Settings dialog (T-178). */
  settingsOpen: boolean;
  themeEditorOpen: boolean;
  /** The theme editor's live preview; applied instead of the document's theme while set. */
  themeDraft: CustomTheme | null;
  /** A side slot the narrow window folded and the user opened anyway (session only, as before). */
  sideOpen: Record<SideSlot, boolean>;
  /** The active tab of a slot in tabs mode, by slot; the first panel when unset. */
  activeTab: Partial<Record<SlotName, string>>;
  /**
   * The panels opened EXPLICITLY in each slot, which stage focus must not hide.
   *
   * **Session state, not a document value**: it exists so that a panel a user asked for stays
   * on screen until the stage changes, and the stage change is what clears it.
   */
  opened: Partial<Record<SlotName, readonly string[]>>;

  setValue(path: readonly Segment[], value: unknown, options?: WriteOptions): Promise<boolean>;
  resetValue(path: readonly Segment[], scope: Scope): Promise<boolean>;
  resetScope(scope: Scope): Promise<boolean>;
  applyPreset(name: string, title?: string): Promise<boolean>;
  savePreset(name: string, title?: string, description?: string): Promise<boolean>;
  exportText(scope?: "effective" | Scope, sections?: string[]): Promise<string | null>;
  importText(text: string, scope?: Scope): Promise<ImportOutcome | null>;
  loadPresets(): Promise<void>;
  setCustomising(on: boolean): void;
  setScope(scope: Scope): void;
  setDisplayOpen(open: boolean): void;
  setSettingsOpen(open: boolean): void;
  setThemeEditorOpen(open: boolean): void;
  setThemeDraft(draft: CustomTheme | null): void;
  setSideOpen(side: SideSlot, open: boolean): void;
  setActiveTab(slot: SlotName, id: string): void;
  /** Records a panel a user opened explicitly, so stage focus keeps it on screen. */
  markOpened(slot: SlotName, id: string): void;
  /** Forgets every opened panel; a stage change and a project open both do this. */
  clearOpened(): void;
  /**
   * Puts the arrangement back to the default, keeping every preference.
   *
   * **The way out when a layout will not behave** (claude-win2's 0900 report): the panels' own
   * controls must do what they say, but a user who has ended up somewhere unusable needs one
   * command that cannot itself be stuck. It clears `layout` only - a theme, a mouse scheme or
   * a shortcut is not what a stuck panel is about.
   */
  resetLayout(): Promise<boolean>;
  /** The scope a write of `path` goes to when the caller does not name one. */
  scopeFor(path: readonly Segment[]): Scope;
}

// ---------------------------------------------------------------------------------------------- wire helpers
function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function readWarnings(raw: unknown): WorkspaceWarning[] {
  if (!Array.isArray(raw)) return [];
  return raw.filter(isRecord).map((w) => ({
    code: String(w.code ?? ""),
    kind: w.kind === "invalid" || w.kind === "unknown" ? w.kind : "notice",
    scope: String(w.scope ?? ""),
    path: String(w.path ?? ""),
    message: String(w.message ?? ""),
  }));
}

function readFile(raw: unknown): FilesInfo["user"] {
  if (!isRecord(raw) || typeof raw.path !== "string") return null;
  return { path: raw.path, exists: raw.exists === true, readable: raw.readable !== false };
}

function readFiles(raw: unknown): FilesInfo {
  return isRecord(raw) ? { user: readFile(raw.user), project: readFile(raw.project) } : { user: null, project: null };
}

function readSources(raw: unknown): Record<string, string> {
  const out: Record<string, string> = {};
  if (isRecord(raw)) for (const [k, v] of Object.entries(raw)) if (typeof v === "string") out[k] = v;
  return out;
}

function readPresets(raw: unknown): PresetSummary[] {
  if (!isRecord(raw) || !Array.isArray(raw.presets)) return [];
  return raw.presets.filter(isRecord).flatMap((p) =>
    typeof p.name === "string"
      ? [
          {
            name: p.name,
            title: typeof p.title === "string" && p.title ? p.title : p.name,
            description: typeof p.description === "string" ? p.description : "",
            source: typeof p.source === "string" ? p.source : "user",
            sections: Array.isArray(p.sections) ? p.sections.filter((s): s is string => typeof s === "string") : [],
            path: typeof p.path === "string" ? p.path : undefined,
          },
        ]
      : [],
  );
}

/** True when the project layer provides `path`, a value under it, or the list or theme that holds it. */
export function ownedByProject(sources: Record<string, string>, path: string): boolean {
  for (const [key, source] of Object.entries(sources)) {
    if (source !== "project") continue;
    if (key === path || key.startsWith(`${path}.`) || key.startsWith(`${path}[`) || path.startsWith(`${key}.`) || path.startsWith(`${key}[`)) return true;
  }
  return false;
}

// ---------------------------------------------------------------------------------------------- fetch bookkeeping
let fetchTicket = 0;
let writesInFlight = 0;
let refetchTimer: number | undefined;
/** Warnings already written to the log, so a refetch does not repeat them. */
const logged = new Set<string>();
let sessionOnlyTold = false;

function scheduleRefetch(): void {
  if (refetchTimer !== undefined) return;
  refetchTimer = window.setTimeout(() => {
    refetchTimer = undefined;
    void fetchDocument();
  }, 0);
}

async function fetchDocument(): Promise<void> {
  const client = useStore.getState().client;
  if (!client) return;
  // A write of ours is in flight: the refetch that follows it brings the settled document.
  if (writesInFlight > 0) return;
  const ticket = ++fetchTicket;
  try {
    const result = await client.call<Record<string, unknown>>("workspace.get");
    // A newer fetch is on its way, or a write began meanwhile: this answer may already be stale.
    if (ticket !== fetchTicket || writesInFlight > 0) return;
    applyResult(result);
  } catch (err) {
    if (err instanceof StokesApiError && err.code === "api.unknown_command") {
      // An older server: the built-in arrangement, changeable for this session.
      useWorkspace.setState({ status: "ready", supported: false });
    }
    // Not connected or the connection dropped: the reconnect fetches again.
  }
}

function applyResult(result: Record<string, unknown>): void {
  const document = readDocument(result.document);
  const warnings = readWarnings(result.warnings);
  const files = readFiles(result.files);
  const state = useWorkspace.getState();
  useWorkspace.setState({
    status: "ready",
    supported: true,
    document,
    sources: readSources(result.sources),
    warnings,
    files,
    // A closed project takes "this project" with it.
    scope: state.scope === "project" && !files.project ? "user" : state.scope,
  });
  setStageMap(document.tree.stages, document.tree.linkStage, document.tree.defaultExpanded);
  // Information about the files belongs in the log (the Log tab); invalid values are shown in Customise mode.
  const log = useStore.getState().log;
  for (const w of warnings) {
    if (w.kind === "invalid") continue;
    const key = `${w.code}|${w.scope}|${w.path}|${w.message}`;
    if (logged.has(key)) continue;
    logged.add(key);
    log("event", `workspace ${w.code} (${w.scope}${w.path ? ` ${w.path}` : ""}): ${w.message}`);
  }
}

/**
 * Runs a workspace command through the project store, so the console and the journal see it. The
 * document is changed locally first (`optimistic`); the refetch after the write settles it.
 */
async function write(method: string, params: Record<string, unknown>, optimistic: ((d: WorkspaceDocument) => WorkspaceDocument) | null, silent = false): Promise<unknown> {
  const state = useWorkspace.getState();
  if (optimistic) {
    const document = optimistic(state.document);
    useWorkspace.setState({ document });
    setStageMap(document.tree.stages, document.tree.linkStage, document.tree.defaultExpanded);
  }
  if (!state.supported) {
    if (!sessionOnlyTold) {
      sessionOnlyTold = true;
      useStore.getState().pushToast("info", "This server cannot store the workspace; the change lasts until the window closes.");
    }
    return null;
  }
  writesInFlight++;
  try {
    return await useStore.getState().execute(method, params, { silent });
  } catch {
    return null; // execute has logged it (and, unless silent, shown a toast); the refetch reverts
  } finally {
    writesInFlight--;
    if (writesInFlight === 0) scheduleRefetch();
  }
}

function warningsOf(result: unknown): WorkspaceWarning[] {
  return isRecord(result) ? readWarnings(result.warnings) : [];
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
}

// ---------------------------------------------------------------------------------------------- the store
export const useWorkspace = create<WorkspaceState>((set, get) => ({
  status: "loading",
  supported: true,
  document: defaultDocument(),
  sources: {},
  warnings: [],
  files: { user: null, project: null },
  presets: [],
  customising: false,
  scope: "user",
  displayOpen: false,
  settingsOpen: false,
  themeEditorOpen: false,
  themeDraft: null,
  sideOpen: { left: false, right: false },
  activeTab: {},
  opened: {},

  scopeFor(path) {
    const s = get();
    if (!s.files.project) return "user";
    if (s.scope === "project") return "project";
    // Outside Customise mode a change goes where the shown value comes from; a user-scope write under
    // a project override would change nothing on screen.
    if (!s.customising && ownedByProject(s.sources, formatPath(path))) return "project";
    return "user";
  },

  async setValue(path, value, options = {}) {
    const scope = options.scope ?? get().scopeFor(path);
    const result = await write("workspace.set", { path: formatPath(path), value, scope }, (d) => setIn(d, path, value), options.silent);
    if (isRecord(result) && typeof result.note === "string") useStore.getState().pushToast("info", `${result.note}.`);
    return result !== null;
  },

  async resetValue(path, scope) {
    const result = await write("workspace.reset", { scope, path: formatPath(path) }, null);
    return result !== null;
  },

  async resetScope(scope) {
    const result = await write("workspace.reset", { scope }, null);
    return result !== null;
  },

  async applyPreset(name, title) {
    const scope = get().scope === "project" && get().files.project ? "project" : "user";
    const client = useStore.getState().client;
    // What the scope held before, for the Undo in the toast (a quiet read, not a user action).
    let before: Record<string, unknown> | null = null;
    try {
      const exported = client ? await client.call<{ text?: string }>("workspace.export", { scope }) : null;
      before = exported?.text ? (JSON.parse(exported.text) as Record<string, unknown>) : null;
    } catch {
      before = null;
    }
    const result = await write("workspace.apply_preset", { name, scope }, null);
    if (!isRecord(result)) return false;
    const sections = strings(result.sections);
    const overridden = strings(result.overridden);
    const shown = title ?? (typeof result.title === "string" ? result.title : name);
    const undo =
      before && sections.length
        ? {
            label: "Undo",
            run: () => {
              const previous = before ?? {};
              const kept = sections.filter((s) => s in previous);
              const dropped = sections.filter((s) => !(s in previous));
              void (async () => {
                if (kept.length) await write("workspace.import", { text: JSON.stringify({ version: 1, ...Object.fromEntries(kept.map((s) => [s, previous[s]])) }), scope }, null);
                for (const section of dropped) await write("workspace.reset", { scope, path: section }, null);
              })();
            },
          }
        : undefined;
    const note = overridden.length ? ` This project still overrides ${overridden.join(", ")}.` : "";
    useStore.getState().pushToast("ok", `Applied the ${shown} layout.${note}`, undo);
    return true;
  },

  async savePreset(name, title, description) {
    const params: Record<string, unknown> = { name };
    if (title) params.title = title;
    if (description) params.description = description;
    const result = await write("workspace.save_preset", params, null);
    if (result !== null) {
      useStore.getState().pushToast("ok", `Saved the layout as ${title || name}`);
      void get().loadPresets();
    }
    return result !== null;
  },

  async exportText(scope = "effective", sections) {
    if (!get().supported) return `${JSON.stringify(get().document, null, 2)}\n`;
    const params: Record<string, unknown> = { scope };
    if (sections?.length) params.sections = sections;
    try {
      const result = await useStore.getState().execute("workspace.export", params);
      return isRecord(result) && typeof result.text === "string" ? result.text : null;
    } catch {
      return null;
    }
  },

  async importText(text, scope) {
    const where = scope ?? (get().scope === "project" && get().files.project ? "project" : "user");
    const result = await write("workspace.import", { text, scope: where }, null);
    if (!isRecord(result)) return null;
    return { sections: strings(result.sections), overridden: strings(result.overridden), warnings: warningsOf(result) };
  },

  async loadPresets() {
    const client = useStore.getState().client;
    if (!client || !get().supported) return;
    try {
      set({ presets: readPresets(await client.call("workspace.presets")) });
    } catch {
      /* an older server, or not connected: the list stays as it was */
    }
  },

  setCustomising: (customising) => set(customising ? { customising, displayOpen: false, settingsOpen: false } : { customising }),
  setScope: (scope) => set({ scope: scope === "project" && !get().files.project ? "user" : scope }),
  setDisplayOpen: (displayOpen) => set({ displayOpen }),
  setSettingsOpen: (settingsOpen) => set(settingsOpen ? { settingsOpen, displayOpen: false } : { settingsOpen }),
  setThemeEditorOpen: (themeEditorOpen) => set(themeEditorOpen ? { themeEditorOpen } : { themeEditorOpen, themeDraft: null }),
  setThemeDraft: (themeDraft) => set({ themeDraft }),
  setSideOpen: (side, open) => set((s) => ({ sideOpen: { ...s.sideOpen, [side]: open } })),
  setActiveTab: (slot, id) => set((s) => ({ activeTab: { ...s.activeTab, [slot]: id } })),
  /** Records a panel a user opened; `null` clears the slot's list, which a stage change does. */
  markOpened: (slot, id) =>
    set((s) => {
      const before = s.opened[slot] ?? [];
      return before.includes(id) ? s : { opened: { ...s.opened, [slot]: [...before, id] } };
    }),
  clearOpened: () => set({ opened: {} }),
  resetLayout: async () => {
    const result = await write("workspace.reset", { scope: get().scopeFor(["layout"]), path: "layout" }, null);
    set({ opened: {} });
    return result !== null;
  },
}));

// ---------------------------------------------------------------------------------------------- wiring
/** How long the chrome waits for the first document before it renders the built-in one. */
const FIRST_LOAD_WAIT = 1500;

/**
 * Follows the project store's client: the first document when the connection comes up, a refetch
 * after every reconnect and every event that may change the effective workspace. Returns a cleanup.
 */
export function startWorkspace(): () => void {
  let detach: (() => void) | null = null;
  const attach = (client: StokesClient) => {
    detach?.();
    const offEvent = client.onEvent((event, data) => {
      if (event === "workspace.changed") {
        if (data.scope === "presets") void useWorkspace.getState().loadPresets();
        else scheduleRefetch();
      } else if (event === "project.opened" || event === "project.closed" || event === "project.saved") {
        scheduleRefetch();
      }
    });
    const offStatus = client.onStatus((connected) => {
      if (!connected) return;
      scheduleRefetch();
      void useWorkspace.getState().loadPresets();
    });
    detach = () => {
      offEvent();
      offStatus();
    };
    if (useStore.getState().connected) scheduleRefetch();
  };
  const initial = useStore.getState().client;
  if (initial) attach(initial);
  const unsubscribe = useStore.subscribe((state, previous) => {
    if (state.client && state.client !== previous.client) attach(state.client);
  });
  const wait = window.setTimeout(() => {
    if (useWorkspace.getState().status === "loading") useWorkspace.setState({ status: "fallback" });
  }, FIRST_LOAD_WAIT);
  return () => {
    window.clearTimeout(wait);
    unsubscribe();
    detach?.();
  };
}

/** True once a document (the server's, or the built-in one after the wait) can be rendered. */
export function useWorkspaceReady(): boolean {
  return useWorkspace((s) => s.status !== "loading");
}
