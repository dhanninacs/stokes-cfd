/**
 * The workspace document (schema version 1) as the front end sees it. The contract is
 * docs/engineering/workspace-schema.md; the server (stokes.workspace) sanitises every layer and sends
 * the effective, complete document. Unknown keys (a newer Stokes, a plug-in) are kept as they came:
 * every object type below allows them, and the front end writes them back untouched.
 */

export type SlotName = "left" | "right" | "bottom" | "float";
export type SideSlot = "left" | "right";
export type DockSlot = "left" | "right" | "bottom";
export type Corner = "bl" | "br" | "tl" | "tr";
export type SlotMode = "tabs" | "split";
export type StageStyle = "chips" | "ribbon" | "hidden";
export type Scope = "user" | "project";
export type BaseThemeName = "dark" | "light" | "cupertino" | "cupertino-dark" | "ubuntu" | "ubuntu-dark";
export type ThemeName = BaseThemeName | "auto" | "cupertino-auto" | "ubuntu-auto";

/** One panel in a slot. `anchor`, `x` and `y` exist only in the float slot. */
export interface PanelPlacement {
  id: string;
  order?: number;
  size?: number;
  collapsed?: boolean;
  tabs?: string[];
  anchor?: Corner;
  x?: number;
  y?: number;
  [key: string]: unknown;
}

export interface SlotSpec {
  mode: SlotMode;
  size: number;
  collapsed: boolean;
  panels: PanelPlacement[];
  [key: string]: unknown;
}

export interface LayoutSpec {
  left: SlotSpec;
  right: SlotSpec;
  bottom: SlotSpec;
  float: SlotSpec;
  hidden: string[];
  [key: string]: unknown;
}

export interface IslandsSpec {
  bl: string[];
  br: string[];
  tl: string[];
  tr: string[];
  hidden: string[];
  [key: string]: unknown;
}

export interface TopbarItemSpec {
  id: string;
  visible?: boolean;
  [key: string]: unknown;
}

export interface TopbarSpec {
  items: TopbarItemSpec[];
  stages: { style: StageStyle; [key: string]: unknown };
  [key: string]: unknown;
}

export interface TreeSpec {
  groupOrder: string[];
  hidden: string[];
  stages: Record<string, string[]>;
  linkStage: boolean;
  defaultExpanded: number;
  /**
   * The outline or the groups (G-305). **Absent reads as "outline"**, which is what the target tree is;
   * "groups" is exactly the tree the app drew before the outline existed, kept for a user who arranged
   * their own.
   */
  layout: "outline" | "groups";
  [key: string]: unknown;
}

/** Action id to one chord, a list of chords (the first is the one tooltips show), or "" / [] for none. */
export type ShortcutsSpec = Record<string, string | string[]>;

export interface ViewportSpec {
  projection: "perspective" | "orthographic";
  viewMode: "shaded" | "edges" | "wireframe";
  grid: boolean;
  axisLines?: boolean;
  shadow?: boolean;
  reflections?: boolean;
  edges?: boolean;
  /** Reduce viewport rendering cost without changing full-resolution geometry or field data. */
  lowDetail?: boolean;
  background: string;
  [key: string]: unknown;
}

export interface EnginesSpec {
  default: string | null;
  runPresets: Record<string, unknown>;
  [key: string]: unknown;
}

/** The mouse schemes the viewport can apply; the ids are `NAV_MODES` in app/actions.ts. */
export type MouseScheme = "cad" | "paraview" | "blender" | "toggle";
/** The unit a CAD file is assumed to be drawn in when it declares none. */
export type ImportUnit = "mm" | "cm" | "m" | "in" | "ft";

/**
 * The user's own settings (T-176). Per user only: the server refuses them in the project scope and
 * a preset never carries them.
 */
export interface PreferencesSpec {
  mouse: { scheme: MouseScheme; orbitLeft: boolean; [key: string]: unknown };
  import: { units: ImportUnit; [key: string]: unknown };
  /** The guided-meshing stepper's visibility (ASK Q2, brief M-502). */
  mesh: { stepper: "shown" | "hidden"; [key: string]: unknown };
  [key: string]: unknown;
}

export interface BuiltinTheme {
  name: ThemeName;
  [key: string]: unknown;
}

export interface CustomTheme {
  base: BaseThemeName;
  title?: string;
  tokens: Record<string, string>;
  [key: string]: unknown;
}

export type ThemeSpec = BuiltinTheme | CustomTheme;

export interface WorkspaceDocument {
  version: number;
  theme: ThemeSpec;
  layout: LayoutSpec;
  islands: IslandsSpec;
  topbar: TopbarSpec;
  tree: TreeSpec;
  shortcuts: ShortcutsSpec;
  viewport: ViewportSpec;
  engines: EnginesSpec;
  preferences: PreferencesSpec;
  /**
   * Guided or Full (the guided-mode brief): the flow strip and step cards with the tree put away, or
   * the arrangement the app had before. Absent reads as `guided` (`guided/modeModel.ts`). **Web only
   * for now**: the server's schema does not list it, and `defaultDocument()` is pinned to the server's
   * default, so a document that carries `mode` keeps it (unknown keys are kept as they came) and the
   * user's own choice is kept in the browser's storage (`guided/modeStore.ts`).
   */
  mode?: "guided" | "full";
  [key: string]: unknown;
}

export type WarningKind = "invalid" | "unknown" | "notice";

/** A finding of the server about a workspace file or the merged document. */
export interface WorkspaceWarning {
  code: string;
  kind: WarningKind;
  scope: string;
  path: string;
  message: string;
}

export interface FileInfo {
  path: string;
  exists: boolean;
  readable: boolean;
}

export interface FilesInfo {
  user: FileInfo | null;
  project: FileInfo | null;
}

/** `workspace.get` without a path. */
export interface GetResult {
  version: number;
  document: unknown;
  sources: Record<string, string>;
  warnings: unknown[];
  files: unknown;
}

/** One entry of `workspace.presets`. */
export interface PresetSummary {
  name: string;
  title: string;
  description: string;
  source: "shipped" | "user" | string;
  sections: string[];
  path?: string;
}

export function isCustomTheme(theme: ThemeSpec): theme is CustomTheme {
  return typeof (theme as CustomTheme).base === "string";
}
