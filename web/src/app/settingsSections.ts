/**
 * What the Settings dialog shows, as data (T-178): the five sections, their options, and where each
 * option's value lives. The dialog (SettingsDialog.tsx) renders this table; nothing here touches React
 * or a store, so the rules (which option needs a project, which is hidden for a scheme, where a value
 * is read from) are tested in plain Node.
 *
 * A setting has a reader or it is not in the table. Shortcuts, viewport quality, engines, confirmations
 * and autosave arrive with their own readers (T-179 to T-184); a section that does not exist is absent,
 * not a disabled placeholder.
 */
import type { ProjectInfo } from "../api/types";
import { getIn } from "../workspace/paths";
import { IMPORT_UNITS } from "../workspace/defaults";
import type { ThemeSpec, WorkspaceDocument } from "../workspace/types";
import { REDUCED_MOTION_KEY, STAGE_FOCUS_KEY } from "./prefs";

/** Where an option's value is read from and written to. */
export type Store =
  /** The workspace document, written with `workspace.set` in the USER scope (through `useWorkspace.setValue`). */
  | { kind: "workspace"; path: readonly string[] }
  /** An API command on the open project; its result arrives as an event, and the stores follow it. */
  | { kind: "project"; command: "project.set_units" }
  /** This device only (`app/prefs.ts`, localStorage). */
  | { kind: "device"; key: string }
  /** Nothing is stored: the control is a button or a read-out. */
  | { kind: "readonly" };

export interface Choice {
  value: string;
  label: string;
  description?: string;
  /** Needs a project that has been saved to a folder (a project-scope workspace file lives in it). */
  needsFolder?: boolean;
}

export type Control = "segmented" | "select" | "switch" | "radio" | "text" | "custom";

export interface Option {
  id: string;
  label: string;
  help?: string;
  store: Store;
  control: Control;
  choices?: readonly Choice[];
  /** Disabled with "Open a project first" when no project is open. */
  needsProject?: boolean;
  /** Drawn from the workspace `theme`: the Style or the Appearance it stands for. */
  derived?: "style" | "appearance";
  /** Shown only while another workspace value equals this one. */
  shownWhen?: { path: readonly string[]; equals: string };
}

export type SectionIcon = "Palette" | "Ruler" | "Mouse" | "LayoutGrid" | "Info" | "Cpu" | "FolderTree";

export interface Section {
  id: string;
  title: string;
  icon: SectionIcon;
  options: readonly Option[];
}

/** The wire names of the API commands the table writes with; the test checks each option against this list. */
export const PROJECT_COMMANDS = ["project.set_units"] as const;

export const SECTIONS: readonly Section[] = [
  {
    // **Compute first** (the Settings spec §2): "tell Stokes WHERE OpenFOAM runs and WHICH install to
    // use". It is the section that decides whether anything else in the product can work, so it is
    // the one a user opens Settings for - the old first section was a theme picker.
    id: "compute",
    title: "Compute",
    icon: "Cpu",
    options: [
      // **One row, drawn by the section itself.** The options table describes simple controls (a
      // switch, a select); this one is a live report from a host - a list of found installs, a
      // status dot per host and a Rescan button - so it is a `custom` control whose component owns
      // its own layout. The table still holds the row so the section appears, is ordered and is
      // counted like the others.
      { id: "compute.hosts", label: "Hosts and OpenFOAM installs", store: { kind: "readonly" }, control: "custom" },
      // **The host LIST, drawn by its own component too**: adding a host is a form and trusting one
      // is a fingerprint check, neither of which fits a switch or a select.
      { id: "compute.remoteHosts", label: "Remote hosts", store: { kind: "readonly" }, control: "custom" },
    ],
  },
  {
    // **Folders** (the Settings spec §3), after Compute: where the files go. One editable path - the
    // projects folder - and the rest read-only, because samples, cache and logs are consequences of
    // that choice and of the open project rather than independent decisions.
    id: "folders",
    title: "Folders",
    icon: "FolderTree",
    options: [
      { id: "folders.paths", label: "Project and app folders", store: { kind: "readonly" }, control: "custom" },
    ],
  },
  {
    // **`Appearance` is GONE from Settings** (the Settings spec §1: "an option appears in exactly one
    // place"). Style, Appearance and the custom-theme editor all live in the **Appearance popover**
    // (the View island's button, `Ctrl+,`), which is where a user meets them while looking at the
    // thing they are changing - and the top bar's sun/moon toggle is the quick path to the one they
    // change most. Verified before deleting: the popover holds Style, `Appearance` and Layout, so
    // nothing was lost.
    //
    // What stays here is what has no other home: reduced motion (a device preference nobody else
    // offers) and the ground grid's switch. The popover's "Grid, minor/major" are theme TOKENS - the
    // grid's colours - not a second switch, so the two are not duplicates.
    id: "display",
    title: "Display",
    icon: "Palette",
    options: [
      {
        id: "appearance.reducedMotion",
        label: "Reduced motion",
        help: "Fewer animations. The system setting always applies. Remembered on this device.",
        store: { kind: "device", key: REDUCED_MOTION_KEY },
        control: "switch",
      },
      {
        id: "appearance.stageFocus",
        label: "Show only this stage's panels",
        help: "The panel tabs follow the stage you are in. Off shows every panel in a slot. Remembered on this device.",
        store: { kind: "device", key: STAGE_FOCUS_KEY },
        control: "switch",
      },
      {
        id: "appearance.grid",
        label: "Ground grid",
        help: "The grid, its axes and the faint floor under the part.",
        store: { kind: "workspace", path: ["viewport", "grid"] },
        control: "switch",
      },
      /*
        **The tree's arrangement** (Codex's 063500): the outline that groups rows by stage, or the plain
        groups. **A presentation switch only** - the selection, the model and every other preference are
        untouched, and the value is written through the workspace command, so it is saved and shared the
        way every other preference is.

        **The labels are the user's words**, not the document's keys: "Workflow outline" and "Groups". The
        stored values stay `outline` and `groups`, which never appear on screen.
      */
      {
        id: "appearance.treeLayout",
        label: "Tree layout",
        help: "How the simulation tree arranges its rows. Both show the same model. A custom group order applies to Groups, because the outline follows the workflow stages.",
        store: { kind: "workspace", path: ["tree", "layout"] },
        control: "select",
        choices: [
          { value: "outline", label: "Workflow outline" },
          { value: "groups", label: "Groups" },
        ],
      },
    ],
  },
  {
    id: "units",
    title: "Units",
    icon: "Ruler",
    options: [
      {
        id: "units.system",
        label: "Display units (this project)",
        help: "How values are shown; they are stored in SI. This is a step in the open project's undo history, not a preference.",
        store: { kind: "project", command: "project.set_units" },
        control: "select",
        needsProject: true,
        choices: [
          { value: "si", label: "SI (m, kg, s)" },
          { value: "mm", label: "Millimetres" },
          { value: "imperial", label: "Imperial" },
        ],
      },
      {
        id: "units.importUnit",
        label: "Default import unit",
        help: "The unit a geometry file is assumed to be drawn in when it does not say. It is the starting answer of the import question.",
        store: { kind: "workspace", path: ["preferences", "import", "units"] },
        control: "select",
        choices: IMPORT_UNITS.map((u) => ({ value: u, label: u })),
      },
    ],
  },
  // **`Mouse` is GONE from Settings** (the Settings spec §1). The viewport's own menu offers the
  // scheme (`contextMenuItems.ts`, the `Mouse` submenu) where the user meets it while looking at the
  // thing it changes.
  //
  // **A real inconsistency, found while checking this and NOT fixed here**: the menu's choice is
  // session-only (`setNavMode` writes the store) while the Settings row wrote the workspace document,
  // so a scheme picked in the menu was forgotten on reload and one picked in Settings was not.
  // `viewDefaults.applyMouse` syncs document -> store in one direction only. Recording it rather than
  // fixing it silently, because the fix is a decision: should the menu remember, or should neither?
  // My reading is that a mouse scheme is a preference and both should remember - the menu should call
  // the workspace write as the Settings row did.
  {
    id: "workspace",
    title: "Workspace",
    icon: "LayoutGrid",
    options: [
      {
        id: "workspace.scope",
        label: "Customise scope",
        help: "Where a change you make in Customise mode is saved.",
        store: { kind: "readonly" },
        control: "segmented",
        choices: [
          { value: "user", label: "All projects" },
          { value: "project", label: "This project", needsFolder: true },
        ],
      },
      { id: "workspace.presets", label: "Layout presets", store: { kind: "readonly" }, control: "custom" },
      { id: "workspace.manage", label: "Save, export and import", store: { kind: "readonly" }, control: "custom" },
      { id: "workspace.reset", label: "Customise and reset", store: { kind: "readonly" }, control: "custom" },
    ],
  },
  {
    id: "about",
    title: "About",
    icon: "Info",
    options: [
      { id: "about.info", label: "This installation", store: { kind: "readonly" }, control: "custom" },
      { id: "about.notices", label: "Third-party notices", store: { kind: "readonly" }, control: "custom" },
    ],
  },
];

/** Everything the table reads, passed in so the functions below are pure. */
export interface OptionState {
  workspace: WorkspaceDocument;
  project: Pick<ProjectInfo, "display_units"> | null;
  /** A device preference as stored (`readPref`). */
  device(key: string): string | null;
  /** The Style and Appearance a theme stands for (`choiceOf` in WorkspacePanel.tsx). */
  theme(spec: ThemeSpec): { family: string; appearance: string };
  /** The open project has been saved to a folder, so it can hold its own workspace file. */
  projectFolder: boolean;
}

/** The current value of an option, read from its store and never kept elsewhere: a change made by the console shows. */
export function optionValue(option: Option, state: OptionState): string | boolean {
  const { store } = option;
  if (store.kind === "workspace") {
    if (option.derived) {
      const choice = state.theme(state.workspace.theme);
      return option.derived === "style" ? choice.family : choice.appearance;
    }
    const value = getIn(state.workspace, store.path);
    return typeof value === "boolean" || typeof value === "string" ? value : "";
  }
  if (store.kind === "project") return state.project?.display_units ?? "";
  if (store.kind === "device") {
    const stored = state.device(store.key);
    return option.control === "switch" ? stored === "1" : (stored ?? "");
  }
  return "";
}

/** Why the option cannot be used now (the text for the control's tooltip), or null. */
export function optionDisabled(option: Option, state: Pick<OptionState, "project">): string | null {
  if (option.needsProject && state.project === null) return "Open a project first";
  return null;
}

/** Why one choice of the option cannot be used now, or null. */
export function choiceDisabled(choice: Choice, state: Pick<OptionState, "projectFolder">): string | null {
  if (choice.needsFolder && !state.projectFolder) return "Save the project to a folder first";
  return null;
}

/** False for an option that only makes sense for another setting's value (the orbit switch outside Select / Orbit). */
export function optionShown(option: Option, state: Pick<OptionState, "workspace">): boolean {
  const when = option.shownWhen;
  return when === undefined || getIn(state.workspace, when.path) === when.equals;
}

/** The section with this id, or the first one. */
export function sectionById(id: string | null): Section {
  return SECTIONS.find((s) => s.id === id) ?? SECTIONS[0];
}
