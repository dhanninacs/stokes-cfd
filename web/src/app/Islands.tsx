/**
 * The island groups of the workspace registry: Modes (Section, Isolate, Measure, the pick-mode
 * menu and the display-mode menu) and View (Fit, Perspective/Ortho, Appearance). The view
 * cube is the third island; the viewport draws it. Where each group sits, and when the modes drop
 * their labels, is the island layer's (workspace/IslandLayer.tsx); Appearance itself is
 * workspace/WorkspacePanel.tsx.
 */
import { useCallback, useEffect, useMemo, useRef, useState, type Ref } from "react";
import { setMouseScheme } from "./actions";
import { ChevronDown, Circle, Crosshair, Cuboid, Eye, Grid2x2, Hand, Hexagon, Lasso, LocateFixed, Maximize2, Mouse, MousePointer2, Orbit, Paintbrush, Palette, Ruler, Scissors, Spline, SquareDashed, type LucideIcon } from "lucide-react";
import { meshPatchesOf } from "../state/meshPatches";
import { useStore, type NavMode, type PickMode, type ViewMode } from "../state/store";
import { Button, IconButton } from "../components/ui";
import { Menu, type MenuItemSpec } from "../components/Menu";
import { Tooltip } from "../components/Tooltip";
import { OrthographicIcon, PerspectiveIcon } from "../components/icons";
import { useShortcut } from "../workspace/keymap";
import { useWorkspace } from "../workspace/store";
import { currentScene } from "../viewport/scene";
import { setDisplayAnchor } from "../workspace/displayAnchor";
import { NAV_MODES, VIEW_MODES, runAction, toggleIsolate, toggleProjection } from "./actions";
import { SectionPopover } from "./SectionPopover";

/**
 * What a click in the viewport picks, as the pull-up list offers it (T-053; Edge and Point enabled
 * by T-060). All four are the store's `pickMode`: Edge and Point pick B-rep topology, which needs the
 * part's topology record, so the scene shows a pending state until the record has arrived (ADR-0022
 * §15.2) and a mesh-only part simply has none to pick.
 */
const PICK_MODES: { id: string; label: string; Icon: LucideIcon; pick: PickMode }[] = [
  { id: "body", label: "Body", Icon: Cuboid, pick: "body" },
  { id: "face", label: "Face", Icon: SquareDashed, pick: "face" },
  { id: "edge", label: "Edge", Icon: Spline, pick: "edge" },
  { id: "point", label: "Point", Icon: Crosshair, pick: "point" },
];

/** One icon per mouse scheme: the button it rotates on, a hand for panning, a mode switch. */
const NAV_ICONS: Record<NavMode, LucideIcon> = {
  cad: Mouse,
  paraview: MousePointer2,
  blender: Orbit,
  toggle: Hand,
};

/** One icon per display mode: a smooth surface, a surface with its edges drawn, a grid of lines. */
const DISPLAY_ICONS: Record<ViewMode, LucideIcon> = { shaded: Circle, edges: Hexagon, wireframe: Grid2x2 };

/** Section, Isolate, Measure and the pick- and display-mode menus. */
export function ModeIsland({ ref }: { ref?: Ref<HTMLDivElement> }) {
  const viewMode = useStore((s) => s.viewMode);
  const setViewMode = useStore((s) => s.setViewMode);
  // **The display switches live in the workspace document**, not in the viewport store: they are a
  // layout preference that follows a user from project to project, which is what the workspace is for.
  const viewportSwitches = useWorkspace((s) => s.document.viewport);
  const setViewportSwitch = (name: string, on: boolean) =>
    void useWorkspace.getState().setValue(["viewport", name], on);
  const meshDisplay = useStore((s) => s.meshDisplay);
  const setMeshDisplay = useStore((s) => s.setMeshDisplay);
  const tree = useStore((s) => s.tree);
  // The mesh entries of the menu need a mesh result that has patches (T-211).
  const hasMesh = useMemo(() => meshPatchesOf(tree).length > 0, [tree]);
  const navMode = useStore((s) => s.navMode);
  const orbitMode = useStore((s) => s.orbitMode);
  const setOrbitMode = useStore((s) => s.setOrbitMode);
  const pickMode = useStore((s) => s.pickMode);
  const setPickMode = useStore((s) => s.setPickMode);
  const paintMode = useStore((s) => s.paintMode);
  const setPaintMode = useStore((s) => s.setPaintMode);
  const lassoMode = useStore((s) => s.lassoMode);
  const setLassoMode = useStore((s) => s.setLassoMode);
  const measureMode = useStore((s) => s.measureMode);
  const probePick = useStore((s) => s.probePick);
  const selectedPatch = useStore((s) => s.selectedPatch);
  const isolatedPatch = useStore((s) => s.isolatedPatch);
  const [pickOpen, setPickOpen] = useState(false);
  const pickButton = useRef<HTMLButtonElement>(null);
  const [displayOpen, setDisplayOpen] = useState(false);
  const displayButton = useRef<HTMLButtonElement>(null);
  const [navOpen, setNavOpen] = useState(false);
  const navButton = useRef<HTMLButtonElement>(null);
  const [sectionOpen, setSectionOpen] = useState(false);
  // The chord and the command palette route through the store, as `view.fit` does: bumping
  // `sectionRequest` opens the popover, so `S` reaches the same control the button opens.
  const sectionRequest = useStore((s) => s.sectionRequest);
  const sectionButton = useRef<HTMLButtonElement>(null);
  const sectionPlane = useStore((s) => s.section);
  const isolating = isolatedPatch !== null;
  // Open when the request counter moves past 0, and again on every later bump (see the store's
  // `sectionRequest`): the first run is the mount, which must not open the card on its own.
  useEffect(() => {
    if (sectionRequest > 0) setSectionOpen(true);
  }, [sectionRequest]);
  const section = useShortcut("view.section");
  const isolate = useShortcut("selection.isolate");
  const measure = useShortcut("view.measure");
  const mode = useShortcut("selection.mode");
  const paint = useShortcut("selection.paint");
  const display = useShortcut("view.mode.cycle");
  const navKeys = useShortcut("view.nav.cycle");

  const pickItems: MenuItemSpec[] = [
    { type: "heading", id: "heading", label: "Pick mode" },
    ...PICK_MODES.map<MenuItemSpec>((m) => ({
      id: m.id,
      label: m.label,
      radio: true,
      checked: pickMode === m.pick,
      onSelect: () => setPickMode(m.pick),
    })),
  ];
  const displayItems: MenuItemSpec[] = [
    { type: "heading", id: "heading", label: "Display" },
    ...VIEW_MODES.map<MenuItemSpec>((m) => ({ id: m.id, label: m.label, radio: true, checked: viewMode === m.id, onSelect: () => setViewMode(m.id) })),
    ...(hasMesh
      ? ([
          { type: "separator", id: "mesh-sep" },
          { type: "heading", id: "mesh-heading", label: "Mesh" },
          { id: "mesh-surface", label: "Mesh surface", radio: true, checked: meshDisplay === "surface", description: "Draw the boundary patches of the mesh", onSelect: () => setMeshDisplay("surface") },
          { id: "mesh-cells", label: "Mesh with cells", radio: true, checked: meshDisplay === "cells", description: "Draw the boundary patches with the mesh's cell edges", onSelect: () => setMeshDisplay("cells") },
        ] as MenuItemSpec[])
      : []),
  ];
  /*
    **The display switches, beside the view mode** (the owner, via claude-win2 2000: *"put them where
    display options already live (the viewport's display/palette popover at the bottom right, next to
    Shaded/CAD)"*).

    `checked` with `onSelect` that flips it, rather than a `switch` element: this is a menu, and the
    menu's own idiom for a boolean is a check mark. Each writes `viewport.<name>` through the
    workspace, which is the same path every other view default takes - "API first" in rule 4's sense,
    and the reason a second monitor of the same user sees the same look.
  */
  const shown: readonly [string, string, string][] = [
    ["grid", "Ground grid", "The ground plane's lines"],
    ["axisLines", "Axis lines", "The red and blue origin lines"],
    ["shadow", "Shadows", "The soft shadow under the model"],
    ["reflections", "Reflections", "The environment's light on the surfaces"],
    ["edges", "Edges", "The dark outline on each part"],
    ["gizmo", "Orientation gizmo", "The axis marker in the corner"],
    ["scaleBar", "Scale bar", "The length reference at the top"],
  ];
  for (const [key, label, description] of shown) {
    displayItems.push({ type: "separator", id: `sep-${key}` } as MenuItemSpec);
    displayItems.push({
      id: `show-${key}`,
      label,
      description,
      radio: true,
      checked: viewportSwitches[key] !== false,
      // Stays open, so three switches are three clicks rather than six (review item 3).
      keepOpen: true,
      onSelect: () => setViewportSwitch(key, viewportSwitches[key] === false),
    });
  }
  // **Reset to defaults** (the owner's display request): every switch back on, in one action, because
  // a user who has turned three of them off to see something should not have to remember which three.
  displayItems.push(
    { type: "separator", id: "sep-reset" } as MenuItemSpec,
    {
      id: "show-reset",
      label: "Show everything again",
      description: "Turn every display switch back on",
      onSelect: () => {
        const set = useWorkspace.getState().setValue;
        for (const [key] of shown) void set(["viewport", key], true);
      },
    } as MenuItemSpec,
  );
  // The mouse schemes: every one rotates, pans and zooms from anywhere, so the choice is about
  // which button your hands already know (ADR-0021 3.4). The label names the tool and the
  // description — a tooltip, not a row chip, because it is a sentence — says which button does
  // what. The Select/Orbit switch appears under the scheme that has one.
  const navItems: MenuItemSpec[] = [
    { type: "heading", id: "heading", label: "Mouse" },
    ...NAV_MODES.map<MenuItemSpec>((m) => ({
      id: m.id,
      label: m.label,
      radio: true,
      checked: navMode === m.id,
      description: m.summary,
      onSelect: () => setMouseScheme(m.id),
    })),
    ...(navMode === "toggle"
      ? ([
          { type: "separator", id: "sep" },
          {
            id: "orbit",
            label: "Orbit with left drag",
            radio: true,
            checked: orbitMode,
            description: "While on, a left drag rotates and the selection box needs Shift.",
            onSelect: () => setOrbitMode(!orbitMode),
          },
        ] as MenuItemSpec[])
      : []),
  ];
  const picked = PICK_MODES.find((m) => m.pick === pickMode) ?? PICK_MODES[0];
  const PickIcon = picked.Icon;
  const DisplayIcon = DISPLAY_ICONS[viewMode];
  const current = VIEW_MODES.find((m) => m.id === viewMode)?.label ?? "Shaded";
  const nav: { id: NavMode; label: string; summary: string } =
    NAV_MODES.find((m) => m.id === navMode) ?? { id: "paraview", label: "Orbit", summary: "" };
  const NavIcon = NAV_ICONS[nav.id];

  return (
    <div ref={ref} className="island island-modes glass pill-group" role="toolbar" aria-label="Modes" data-ws-island="modes">
      <Tooltip label={`Pick mode: ${picked.label}`} hint={mode.hint} disabled={pickOpen} describe={false}>
        <Button ref={pickButton} variant="ghost" aria-label={`Pick mode: ${picked.label}`} aria-haspopup="menu" aria-expanded={pickOpen} aria-keyshortcuts={mode.keys} onClick={() => setPickOpen((was) => !was)}>
          <PickIcon size={14} /> <span className="island-label">{picked.label}</span> <ChevronDown size={12} className="-mr-0.5 opacity-70" />
        </Button>
      </Tooltip>
      <Tooltip label="Paint select" hint={paint.hint} describe={false}>
        <Button variant="ghost" active={paintMode} aria-pressed={paintMode} aria-label="Paint select" aria-keyshortcuts={paint.keys} onClick={() => setPaintMode(!paintMode)}>
          <Paintbrush size={14} /> <span className="island-label">Paint</span>
        </Button>
      </Tooltip>
      <Tooltip label="Lasso select: drag on empty space to draw a freehand path, or hold Alt" describe={false}>
        <Button variant="ghost" active={lassoMode} aria-pressed={lassoMode} aria-label="Lasso select" onClick={() => setLassoMode(!lassoMode)}>
          <Lasso size={14} /> <span className="island-label">Lasso</span>
        </Button>
      </Tooltip>
      <div className="divider" />
      <Tooltip label="Section view" hint={section.hint} describe={false} disabled={sectionOpen}>
        <Button
          ref={sectionButton}
          variant="ghost"
          active={sectionPlane !== null}
          aria-pressed={sectionPlane !== null}
          aria-haspopup="dialog"
          aria-expanded={sectionOpen}
          aria-label="Section"
          aria-keyshortcuts={section.keys}
          onClick={() => setSectionOpen((was) => !was)}
        >
          <Scissors size={14} /> <span className="island-label">Section</span>
        </Button>
      </Tooltip>
      <Tooltip label={isolating ? "Show all patches again" : selectedPatch ? `Isolate ${selectedPatch}` : "Isolate the selected patch"} hint={isolate.hint} describe={false}>
        <Button variant="ghost" active={isolating} aria-pressed={isolating} aria-disabled={!isolating && !selectedPatch} aria-label="Isolate" aria-keyshortcuts={isolate.keys} onClick={toggleIsolate}>
          <Eye size={14} /> <span className="island-label">Isolate</span>
        </Button>
      </Tooltip>
      <Tooltip label="Measure" hint={measure.hint} describe={false}>
        <Button variant="ghost" active={measureMode} aria-pressed={measureMode} aria-label="Measure" aria-keyshortcuts={measure.keys} onClick={() => runAction("view.measure")}>
          <Ruler size={14} /> <span className="island-label">Measure</span>
        </Button>
      </Tooltip>
      {/*
        **Probe** (results item 2): read the field at a point. Beside Measure because it is the same
        gesture - a click on the model answers with a world point - and the two cannot both be waiting
        for one press, which the actions enforce.
      */}
      <Tooltip label="Probe: read a field at a point" describe={false}>
        <Button variant="ghost" active={probePick} aria-pressed={probePick} aria-label="Probe" onClick={() => runAction("view.probe")}>
          <LocateFixed size={14} /> <span className="island-label">Probe</span>
        </Button>
      </Tooltip>
      <Tooltip label={`Display mode: ${current}`} hint={display.hint} disabled={displayOpen} describe={false}>
        <Button ref={displayButton} variant="ghost" aria-label={`Display mode: ${current}`} aria-haspopup="menu" aria-expanded={displayOpen} aria-keyshortcuts={display.keys} onClick={() => setDisplayOpen((was) => !was)}>
          <DisplayIcon size={14} /> <span className="island-label">{current}</span> <ChevronDown size={12} className="-mr-0.5 opacity-70" />
        </Button>
      </Tooltip>
      <Tooltip label={`Mouse: ${nav.label} — ${nav.summary}`} hint={navKeys.hint} disabled={navOpen} describe={false}>
        <Button ref={navButton} variant="ghost" aria-label={`Mouse scheme: ${nav.label}`} aria-haspopup="menu" aria-expanded={navOpen} aria-keyshortcuts={navKeys.keys} onClick={() => setNavOpen((was) => !was)}>
          <NavIcon size={14} /> <span className="island-label">{nav.label}</span> <ChevronDown size={12} className="-mr-0.5 opacity-70" />
        </Button>
      </Tooltip>
      <Menu open={pickOpen} anchor={pickButton} onClose={() => setPickOpen(false)} items={pickItems} label="Pick mode" placement="top-start" />
      <Menu open={displayOpen} anchor={displayButton} onClose={() => setDisplayOpen(false)} items={displayItems} label="Display" placement="top-start" />
      <Menu open={navOpen} anchor={navButton} onClose={() => setNavOpen(false)} items={navItems} label="Mouse" placement="top-start" />
      <SectionPopover
        open={sectionOpen}
        anchor={sectionButton}
        // Read from the live scene on every render while the popover is open: the model can be
        // re-imported or a part hidden while the card is up, and each axis has its own extent.
        range={(axis) => currentScene()?.axisRange(axis) ?? null}
        onClose={() => setSectionOpen(false)}
      />
    </div>
  );
}

/** Fit, projection and the Appearance button. */
export function ViewIsland({ ref }: { ref?: Ref<HTMLDivElement> }) {
  const requestFit = useStore((s) => s.requestFit);
  const orthographic = useStore((s) => s.orthographic);
  const displayOpen = useWorkspace((s) => s.displayOpen);
  const setDisplayOpen = useWorkspace((s) => s.setDisplayOpen);
  const fit = useShortcut("view.fit");
  const projection = useShortcut("view.projection");
  const display = useShortcut("display.settings");
  // The popover hangs from this button while the view island is shown (setDisplayAnchor).
  const anchor = useCallback((el: HTMLButtonElement | null) => setDisplayAnchor(el), []);

  return (
    <div ref={ref} className="island island-view glass pill-group" role="toolbar" aria-label="View" data-ws-island="view">
      <IconButton label="Fit to view" hint={fit.hint} keys={fit.keys} onClick={() => requestFit()}>
        <Maximize2 size={14} />
      </IconButton>
      <IconButton
        label={orthographic ? "Switch to perspective" : "Switch to orthographic"}
        hint={projection.hint}
        keys={projection.keys}
        active={orthographic}
        aria-pressed={orthographic}
        onClick={toggleProjection}
      >
        {orthographic ? <OrthographicIcon size={15} /> : <PerspectiveIcon size={15} />}
      </IconButton>
      <IconButton
        ref={anchor}
        // **`Appearance`, with a palette** (claude-win2's 2000 item 4). "Display" meant three things:
        // this popover (theme and layout), the view mode (Shaded/Wireframe), and the display units.
        // It holds looks and layout, so it is Appearance; the view mode keeps "Display mode"; units
        // stay "Units".
        //
        // **And the GEAR belongs only to Settings** - a second gear on a different popover is how a
        // user learns that the icon does not tell them where they are going.
        label="Appearance"
        hint={display.hint}
        keys={display.keys}
        aria-haspopup="dialog"
        aria-expanded={displayOpen}
        onClick={() => setDisplayOpen(!displayOpen)}
        data-display-anchor="true"
      >
        <Palette size={14} />
      </IconButton>
    </div>
  );
}
