/**
 * The workspace registry (ADR-0015 §4): every panel, island and top-bar item the document can place,
 * each with an id, a title, an icon, its component, its default place and its size limits. The layout
 * engine resolves the ids of the document here; an id the registry does not know (a plug-in that is
 * not loaded) is skipped and left in the document as it is. Engine adapters and plug-ins add their
 * own entries with `registerPanel` before the first render.
 */
import { memo, type ComponentType, type Ref } from "react";
import type { Stage } from "../state/store";
import {
  Box,
  ChartScatter,
  Cpu,
  Gauge,
  LayoutGrid,
  ListTree,
  Moon,
  Play,
  Plug,
  Rotate3d,
  Search,
  SlidersHorizontal,
  SquareTerminal,
  Table2,
  Ruler,
  AppWindow,
  FolderOpen,
  ShieldCheck,
  type LucideIcon,
} from "lucide-react";
import TreePanel from "../panels/TreePanel";
import PropertiesPanel from "../panels/PropertiesPanel";
import { OutputPanel } from "../panels/OutputDrawer";
// Explore (ADR-0016). The import below that names no binding installs the study.* actions on the action bus;
// the two panels' default places come from the workspace document (stokes/workspace/defaults.py).
import DesignTable from "../panels/explore/DesignTable";
import Plots from "../panels/explore/Plots";
import "../panels/explore/actions";
import { ModeIsland, ViewIsland } from "../app/Islands";
import {
  AppearanceItem,
  CommandsItem,
  Logo,
  ProjectItem,
  RunItem,
  StageChips,
  StatusConnection,
  StatusEngine,
  StatusLicence,
  StatusUnits,
  WindowControls,
} from "../app/topbarItems";
import type { Corner, SlotName } from "./types";

/** A px range: [smallest, largest]. */
export type Range = readonly [number, number];

export interface PanelDef {
  kind: "panel";
  id: string;
  title: string;
  icon: LucideIcon;
  component: ComponentType;
  defaultPlace: SlotName;
  /** Width in a side slot or as a float card. */
  width: Range;
  /** Height in the bottom slot or in a vertical split. */
  height: Range;
  /** Fills its side slot from top to bottom (tree, output) rather than fitting its content (properties). */
  grow: boolean;
  /**
   * The stages this panel belongs to (T-220).
   *
   * **Absent means always-on**: the tree, the properties and the output are useful in every stage, so
   * a panel that does not say is never filtered. That default is deliberate - a panel that vanishes
   * because somebody forgot to annotate it is a worse failure than one extra tab, and the annotation
   * is then a positive statement about where the panel DOES belong.
   *
   * Filtering is cheap and web-only: no API change, no document change, and a user who wants the old
   * behaviour turns stage focus off in Settings.
   */
  stages?: readonly Stage[];
}

export interface IslandDef {
  kind: "island";
  id: string;
  title: string;
  icon: LucideIcon;
  /** Null for the view cube: the viewport draws it into a box of this size that the island layer reserves in its corner. */
  component: ComponentType<{ ref?: Ref<HTMLDivElement> }> | null;
  defaultPlace: Corner;
  width: Range;
  height: Range;
}

export interface TopbarItemDef {
  kind: "topbar";
  id: string;
  title: string;
  icon: LucideIcon;
  component: ComponentType | null;
  /** Position in the default order (`topbar.items`). */
  defaultPlace: number;
  /** Drawn only in the hosted window (the window controls). */
  hostedOnly?: boolean;
  width: Range;
  height: Range;
  /** The element that shows the item, for the Customise handles. */
  locate(root: ParentNode): HTMLElement | null;
}

export type RegistryEntry = PanelDef | IslandDef | TopbarItemDef;

const MemoTree = memo(TreePanel);
const MemoProperties = memo(PropertiesPanel);
const MemoOutput = memo(OutputPanel);
const MemoDesignTable = memo(DesignTable);
const MemoPlots = memo(Plots);

const panels = new Map<string, PanelDef>([
  [
    "tree",
    { kind: "panel", id: "tree", title: "Simulation tree", icon: ListTree, component: MemoTree, defaultPlace: "left", width: [240, 420], height: [160, 4096], grow: true },
  ],
  [
    "properties",
    { kind: "panel", id: "properties", title: "Properties", icon: SlidersHorizontal, component: MemoProperties, defaultPlace: "right", width: [280, 420], height: [120, 4096], grow: false },
  ],
  [
    "output",
    { kind: "panel", id: "output", title: "Output", icon: SquareTerminal, component: MemoOutput, defaultPlace: "bottom", width: [300, 720], height: [120, 480], grow: true },
  ],
  // Explore: a tab beside the output in the bottom slot, a tab beside the properties on the right. The plots
  // fit their content like the properties do (a growing panel would stretch the properties card too).
  [
    "design-table",
    { kind: "panel", id: "design-table", title: "Design table", icon: Table2, component: MemoDesignTable, defaultPlace: "bottom", width: [300, 1200], height: [160, 480], stages: ["Explore"],
    grow: true },
  ],
  [
    "plots",
    { kind: "panel", id: "plots", title: "Plots", icon: ChartScatter, component: MemoPlots, defaultPlace: "right", width: [240, 720], height: [240, 4096], stages: ["Explore"],
    grow: false },
  ],
]);

const islands = new Map<string, IslandDef>([
  ["modes", { kind: "island", id: "modes", title: "Modes", icon: Ruler, component: ModeIsland, defaultPlace: "bl", width: [132, 420], height: [34, 34] }],
  ["view", { kind: "island", id: "view", title: "View", icon: Rotate3d, component: ViewIsland, defaultPlace: "br", width: [100, 160], height: [34, 34] }],
  ["gizmo", { kind: "island", id: "gizmo", title: "View cube", icon: Box, component: null, defaultPlace: "br", width: [128, 128], height: [128, 128] }],
]);

const byClass = (selector: string) => (root: ParentNode) => root.querySelector<HTMLElement>(selector);
const byItem = (id: string) => byClass(`[data-ws-item="${id}"]`);

const item = (id: string, title: string, icon: LucideIcon, component: ComponentType | null, defaultPlace: number, width: Range, extra: Partial<TopbarItemDef> = {}): [string, TopbarItemDef] => [
  id,
  { kind: "topbar", id, title, icon, component, defaultPlace, width, height: [22, 32], locate: byItem(id), ...extra },
];

const topbarItems = new Map<string, TopbarItemDef>([
  item("logo", "Logo", Box, Logo, 0, [40, 140]),
  item("project", "Project menu", FolderOpen, ProjectItem, 1, [80, 260], { locate: byClass(".topbar .project-btn") }),
  item("status.connection", "Connection", Plug, StatusConnection, 2, [22, 22]),
  item("status.units", "Display units", Gauge, StatusUnits, 3, [28, 90]),
  item("status.engine", "Engine", Cpu, StatusEngine, 4, [60, 200]),
  item("status.licence", "Licence", ShieldCheck, StatusLicence, 5, [36, 90]),
  item("stages", "Stage control", LayoutGrid, StageChips, 6, [180, 620]),
  item("commands", "Commands", Search, CommandsItem, 7, [30, 180]),
  item("appearance", "Appearance", Moon, AppearanceItem, 8, [30, 30]),
  item("run", "Run", Play, RunItem, 9, [64, 90]),
  item("window", "Window controls", AppWindow, WindowControls, 10, [84, 100], { hostedOnly: true }),
]);

export const PANELS: ReadonlyMap<string, PanelDef> = panels;
export const ISLANDS: ReadonlyMap<string, IslandDef> = islands;
export const TOPBAR_ITEMS: ReadonlyMap<string, TopbarItemDef> = topbarItems;

/** Add a panel (an engine's Solve panel, a plug-in's); returns the call that removes it. */
export function registerPanel(def: PanelDef): () => void {
  panels.set(def.id, def);
  return () => {
    if (panels.get(def.id) === def) panels.delete(def.id);
  };
}
