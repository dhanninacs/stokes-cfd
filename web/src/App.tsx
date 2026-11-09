import { memo, useEffect, useLayoutEffect, useMemo, useState, useSyncExternalStore, type CSSProperties } from "react";
import { configFromLocation } from "./api/client";
import { useStore } from "./state/store";
import { host, initHost } from "./app/host";
import { REDUCED_MOTION_KEY, useBoolPref } from "./app/prefs";
import TopBar from "./app/TopBar";
import Toasts from "./app/Toasts";
import FirstRunHint from "./app/FirstRunHint";
import RecoveryCard from "./app/RecoveryCard";
import { useAutosave } from "./app/autosave";
import StartScreen from "./app/StartScreen";
import ImportDrop from "./app/ImportDrop";
import ContextToolbar from "./app/ContextToolbar";
import ContextMenu from "./app/ContextMenu";
import Viewport from "./viewport/Viewport";
import CommandPalette from "./panels/CommandPalette";
import { startWorkspace, useWorkspace } from "./workspace/store";
import { installKeymap } from "./workspace/keymap";
import { applyTokens, currentTokens, startThemeSync, subscribeTokens } from "./workspace/theme";
import { startViewDefaults } from "./workspace/viewDefaults";
import { registerAction } from "./app/actions";
import EdgeHandle from "./workspace/EdgeHandle";
import ResetPill from "./workspace/ResetPill";
import { BottomSlot, FloatLayer, SideSlotView, toggleDetails, useWorkspaceLayout } from "./workspace/Layout";
import GuidedLayer from "./guided/GuidedLayer";
import { treeShortcut } from "./guided/GuidedControls";
import { useGuidedMode } from "./guided/modeStore";
import { guidedInset } from "./guided/guidedInset";
import IslandLayer from "./workspace/IslandLayer";
import Ribbon from "./workspace/Ribbon";
import { DisplaySettings, ThemeEditor } from "./workspace/WorkspacePanel";
import SettingsDialog from "./app/SettingsDialog";
import CustomiseOverlay from "./workspace/Customise";
import { registerPanel } from "./workspace/registry";
import ConditionsPanel from "./panels/ConditionsPanel";
import DetailsPanel from "./panels/DetailsPanel";
import MeshPanel from "./panels/MeshPanel";
import GeometryCheckPage from "./panels/meshTaskPages/GeometryCheckPage";
import BodiesPage from "./panels/meshTaskPages/BodiesPage";
import BoundariesPage from "./panels/meshTaskPages/BoundariesPage";
import RunPanel from "./panels/RunPanel";
import FieldsPanel from "./panels/FieldsPanel";
import ExportImageDialog from "./panels/ExportImageDialog";
import { ChartLine, Cpu, Grid3x3, ListChecks, PanelRight, ShieldQuestion, Users, Tag as TagIcon } from "lucide-react";

// The Conditions panel (ADR-0022 §9.1, T-048) is registered here rather than imported by
// `workspace/registry.tsx`, because that file belongs to the theme session this run shares the tree
// with. `registerPanel` is the registry's documented plug-in route ("own entries with
// `registerPanel` before the first render"), so the panel needs no edit there.
//
// Consequence to remember: a registered panel only *appears* where the workspace document places it,
// and the shipped defaults in `workspace/defaults.ts` place `tree`, `properties` and `output` only.
// The panel is therefore real, rendered and reachable through the palette ("Move Conditions to …"),
// but it is not a default tab beside Properties as §9.1 asks. Adding that line to `defaults.ts` (and
// `stokes/workspace/defaults.py`, and the five presets) is the theme session's file set; the exact
// three-line diff is recorded in `.coordination/DECISION-LOG.md`.
// The Details pane (the layout decision 2026-10-18): the full page for whatever the tree selects. It
// sits BESIDE the tree in the left dock, which is what dissolves the tab strip - the tree selection
// is the tab. Registered here for the reason the stage panels are; `layout.left` places it.
registerPanel({
  kind: "panel",
  id: "details",
  title: "Details",
  icon: PanelRight,
  component: DetailsPanel,
  defaultPlace: "left",
  width: [320, 520],
  height: [160, 4096],
  grow: true,
});

registerPanel({
  kind: "panel",
  id: "conditions",
  title: "Conditions",
  icon: ListChecks,
  component: ConditionsPanel,
  defaultPlace: "right",
  width: [300, 460],
  height: [160, 4096],
    stages: ["Physics"],
grow: false,
});

// The Mesh panel (ADR-0024, T-211b): settings, Generate and Cancel, progress and the result, a tab
// beside Properties and Conditions. Registered here for the reason the Conditions panel is.
registerPanel({
  kind: "panel",
  id: "mesh",
  title: "Mesh",
  icon: Grid3x3,
  component: MeshPanel,
  defaultPlace: "right",
  width: [300, 460],
  height: [160, 4096],
    stages: ["Mesh"],
grow: false,
});

// The guided-meshing task pages (brief M-502): the pane opens them for the mesh tasks that have
// no panel of their own yet. They are pane pages, not slot tabs — the Details pane reads them by
// id (`openTargetOf` for the Boundaries row, `meshTask` for the two without a tree row) — so they
// are registered here with the same plug-in route, and the workspace document never places them.
registerPanel({
  kind: "panel",
  id: "mesh-geometry",
  title: "Geometry check",
  icon: ShieldQuestion,
  component: GeometryCheckPage,
  defaultPlace: "right",
  width: [280, 420],
  height: [120, 4096],
  stages: ["Mesh"],
  grow: false,
});
registerPanel({
  kind: "panel",
  id: "mesh-bodies",
  title: "What each body is",
  icon: Users,
  component: BodiesPage,
  defaultPlace: "right",
  width: [280, 420],
  height: [120, 4096],
  stages: ["Mesh"],
  grow: false,
});
registerPanel({
  kind: "panel",
  id: "mesh-boundaries",
  title: "Boundaries",
  icon: TagIcon,
  component: BoundariesPage,
  defaultPlace: "right",
  width: [280, 420],
  height: [120, 4096],
  stages: ["Mesh"],
  grow: false,
});

// The Solve panel (T-217): Run and Stop, the state of the newest run, the residual chart, the result,
// the job log and the solver settings, a tab after Conditions in the order of the stages.
registerPanel({
  kind: "panel",
  id: "solve",
  title: "Solve",
  icon: Cpu,
  component: RunPanel,
  defaultPlace: "right",
  width: [300, 460],
  height: [160, 4096],
    stages: ["Solve"],
grow: false,
});

// The Results panel (T-218): the field that colours the mesh, its range and the mid-plane slice, a tab after
// Solve in the order of the stages.
registerPanel({
  kind: "panel",
  id: "results",
  title: "Results",
  icon: ChartLine,
  component: FieldsPanel,
  defaultPlace: "right",
  width: [300, 460],
  height: [160, 4096],
    stages: ["Results"],
grow: false,
});

// The stores are reachable from a script in development and review builds, and in any build opened
// with `?drive=1` — which only the capture harness does (`stokes.app --setup`, T-088): the host has
// no way to drive the page, so a screenshot of anything but the opening state needs this. It is
// opt-in per load, never on for a user's window.
const driven = import.meta.env.DEV || import.meta.env.MODE === "review" || new URLSearchParams(window.location.search).has("drive");
if (driven) {
  const w = window as unknown as { __stokes?: typeof useStore; __workspace?: typeof useWorkspace };
  w.__stokes = useStore;
  w.__workspace = useWorkspace;
}

// Panels re-render on their own store subscriptions, not when the layout changes under them
// (a drag handle updates the width on every pointer move). The registry memoises the panels.
const MemoTopBar = memo(TopBar);
const MemoViewport = memo(Viewport);
const MemoPalette = memo(CommandPalette);
const MemoBottom = memo(BottomSlot);
const MemoRibbon = memo(Ribbon);

const EDGE = 6;

function ResizeEdges() {
  const edges: { edge: string; style: CSSProperties; cursor: string }[] = [
    { edge: "top", style: { top: 0, left: EDGE, right: EDGE, height: EDGE }, cursor: "ns-resize" },
    { edge: "bottom", style: { bottom: 0, left: EDGE, right: EDGE, height: EDGE }, cursor: "ns-resize" },
    { edge: "left", style: { left: 0, top: EDGE, bottom: EDGE, width: EDGE }, cursor: "ew-resize" },
    { edge: "right", style: { right: 0, top: EDGE, bottom: EDGE, width: EDGE }, cursor: "ew-resize" },
    { edge: "top-left", style: { top: 0, left: 0, width: EDGE, height: EDGE }, cursor: "nwse-resize" },
    { edge: "top-right", style: { top: 0, right: 0, width: EDGE, height: EDGE }, cursor: "nesw-resize" },
    { edge: "bottom-left", style: { bottom: 0, left: 0, width: EDGE, height: EDGE }, cursor: "nesw-resize" },
    { edge: "bottom-right", style: { bottom: 0, right: 0, width: EDGE, height: EDGE }, cursor: "nwse-resize" },
  ];
  return (
    <>
      {edges.map((e) => (
        <div key={e.edge} className="absolute z-50" style={{ ...e.style, cursor: e.cursor }} onPointerDown={() => host.startResize(e.edge)} />
      ))}
    </>
  );
}

export default function App() {
  const config = useMemo(configFromLocation, []);
  const init = useStore((s) => s.init);
  const theme = useStore((s) => s.theme);
  /**
   * **The Start screen shows when nothing is open** (the menu spec). A client-side decision, because
   * the server's `project` being null IS the state - there is no second flag that could go stale and
   * disagree with it.
   *
   * The Settings dialog draws above it (it is a modal in the layer stack), so Settings opened from a
   * menu with nothing else on screen still works.
   */
  const startScreen = useStore((s) => s.project === null);
  useAutosave();
  const tokens = useSyncExternalStore(subscribeTokens, currentTokens);
  const [hosted, setHosted] = useState(false);
  const [reducedMotion] = useBoolPref(REDUCED_MOTION_KEY, false);
  const layout = useWorkspaceLayout();
  const guided = useGuidedMode() === "guided";
  // Which of the two panels in the left dock are not on screen, for the edge handles.
  /*
    **Two BOOLEAN selectors, not one object** (measured: a selector that builds a fresh object on every
    call never compares equal, so React re-renders forever - "Maximum update depth exceeded", error
    #185, and the whole app rendered nothing). `useSyncExternalStore` compares the selector's result by
    reference; a primitive is the only shape it can settle on.
  */
  const treeHidden = useWorkspace((s) => !s.document.layout.left.panels.some((p) => p.id === "tree"));
  const detailsHidden = useWorkspace((s) => !s.document.layout.left.panels.some((p) => p.id === "details"));
  const ribbon = useWorkspace((s) => s.status !== "loading" && s.document.topbar.stages.style === "ribbon" && s.document.topbar.items.find((i) => i.id === "stages")?.visible !== false);

  /**
   * **`Ctrl+B` and `Ctrl+Shift+B`**, and the View menu, and the palette — one command each.
   *
   * Registered here rather than in a slot, because the slots come and go (a hidden dock renders
   * nothing) while the chord has to keep working: with the tree hidden there is no header button to
   * press, so the way back must not depend on the thing it restores.
   */
  useEffect(() => {
    const stops = [
      // Guided mode puts the whole left dock away or back (tree and Details); Full mode keeps the tree's own toggle.
      registerAction("layout.toggleTree", () => treeShortcut()),
      registerAction("layout.toggleDetails", () => toggleDetails()),
      // **The way out when an arrangement will not behave** (claude-win2's 0900/1330). The palette
      // entry existed without a handler, so it did nothing - found by checking rather than by a user.
      registerAction("layout.reset", () => void useWorkspace.getState().resetLayout()),
    ];
    return () => {
      for (const stop of stops) stop();
    };
  }, []);

  useEffect(() => {
    void init(config);
    // The workspace follows the project store's connection; the keymap, the theme and the view defaults
    // follow the workspace.
    const stops = [startWorkspace(), startThemeSync(config.theme), startViewDefaults(), installKeymap()];
    void initHost(config.apiBase).then(setHosted); // idempotent: safe to run again in strict mode
    return () => stops.forEach((stop) => stop());
  }, [config, init]);

  // The theme and a custom theme's tokens land together, before the frame is painted.
  useLayoutEffect(() => {
    const root = document.documentElement;
    const changed = root.dataset.theme !== theme;
    root.dataset.theme = theme;
    applyTokens(tokens, changed);
  }, [theme, tokens]);

  useEffect(() => {
    if (reducedMotion) document.documentElement.dataset.motion = "reduced";
    else delete document.documentElement.dataset.motion;
  }, [reducedMotion]);

  // A browser context menu (Reload, View source) has no place in the app; text fields keep theirs.
  useEffect(() => {
    const onContextMenu = (e: MouseEvent) => {
      const t = e.target;
      const editable = t instanceof HTMLElement && (t.isContentEditable || t.tagName === "INPUT" || t.tagName === "TEXTAREA");
      if (!editable) e.preventDefault();
    };
    window.addEventListener("contextmenu", onContextMenu);
    return () => window.removeEventListener("contextmenu", onContextMenu);
  }, []);

  return (
    <div className="app-shell flex h-full flex-col" data-start-screen={startScreen}>
      <MemoTopBar hosted={hosted} />
      {ribbon && <MemoRibbon />}
      <main className="relative min-h-0 flex-1" style={layout.vars}>
        {/*
          **The Start screen, when no project is open** (the menu spec). It is a sibling of the
          workspace. Workspace preferences persist in the workspace store, while the viewport
          mounts only for an open project: the start screen does not load or render geometry.
        */}
        {startScreen && <StartScreen />}
        {!startScreen && <>
        <MemoViewport />
        <SideSlotView state={layout.left} setDraft={layout.setDraft} />
        {/*
          **A handle for each hidden panel** (claude-win2's GUI review item 5): with both collapsed the
          window is empty and the chords are not discoverable from it. The handles appear only while
          their panel is hidden, so they cost the viewport nothing when everything is open.
        */}
        {/* Guided mode has the Tree button in the top bar instead of the edge handles and the reset pill. */}
        {treeHidden && !guided && <EdgeHandle panel="tree" />}
        {detailsHidden && !guided && <EdgeHandle panel="details" />}
        {/*
          **"Reset layout" appears only when BOTH panels are hidden** (the owner, via Claude 1330), which
          is the state where a user is stuck rather than merely tidy: an empty left dock with two
          handles and nothing else. It is a pill at the top of the dock area rather than a permanent
          toolbar button, because a reset is a way out and not a daily action.
        */}
        {treeHidden && detailsHidden && !guided && <ResetPill />}
        <SideSlotView state={layout.right} setDraft={layout.setDraft} />
        <FloatLayer />
        {/* Drop a CAD file on the window, the hint of an empty project and the upload card (T-219 A). */}
        {/* In Guided mode the toolbar stays clear of the step card (312 px) at the left; the flow strip is in the top bar. */}
        <ContextToolbar leftInset={layout.insets.left + guidedInset(guided ? "guided" : "full").left} rightInset={layout.insets.right} topInset={guidedInset(guided ? "guided" : "full").top} />
        <ContextMenu />
        <IslandLayer offsets={layout.offsets} />
        {/* Guided mode: the flow strip across the top of the viewport and the open step's card (guided/). */}
        <GuidedLayer />
        </>}
        <ImportDrop />
        <div className="bottom-stack">
          <Toasts />
          {!startScreen && <RecoveryCard />}
          {!startScreen && <FirstRunHint />}
        </div>
        <MemoPalette />
      </main>
      {!startScreen && <MemoBottom />}
      {hosted && <ResizeEdges />}
      <DisplaySettings />
      <SettingsDialog />
      <ExportImageDialog />
      <ThemeEditor />
      <CustomiseOverlay />
    </div>
  );
}
