/**
 * The slot layout engine (ADR-0015 §4): renders `layout` from the workspace document.
 *
 *   left, right   a side slot: one panel as it always was (the panel's glass straight in the slot
 *                 body), several panels as tabs on one glass card or as a vertical split of cards
 *                 with a handle in each 12 px gap; the width from `size`, a resize handle in the
 *                 gutter, the icon strip when folded (`collapsed`, or a narrow window).
 *   bottom        the drawer under the viewport (panels/OutputDrawer.tsx): the 36 px strip with
 *                 tabs, expanded to `size`; its open state is kept in step with `collapsed`.
 *   float         cards over the viewport, placed by `anchor`, `x`, `y` and kept inside it.
 *   hidden        not rendered.
 *
 * Sizes are written back with `workspace.set` when a gesture ends; folding is a quiet write. The
 * outputs `--tree-width` and `--props-width` (the contract's names) and their generalisation
 * (`--slot-left`, `--slot-right`, `--slot-bottom`, `--corner-<corner>-x|y`) go on <main>, where the
 * islands, the context toolbar and any plug-in chrome read them.
 */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore, type CSSProperties } from "react";
import { STAGE_FOCUS_KEY, useBoolPref } from "../app/prefs";
import { detailsPlacement } from "../panels/DetailsPanel";
import { movePanel } from "./arrange";
import { useShortcut } from "./keymap";
import { hideSlot, showSlot, slotHidden } from "./panelToggle";
import { panelsForStage } from "./stageFocus";
import { ChevronDown, ChevronUp } from "lucide-react";
import ResizeHandle from "../components/ResizeHandle";
import { SplitHandle } from "../components/SplitHandle";
import { Tooltip } from "../components/Tooltip";
import { IconButton } from "../components/ui";
import BottomDrawer, { type DrawerPanel } from "../panels/OutputDrawer";
import { useStore } from "../state/store";
import {
  DRAWER_STRIP,
  INSET,
  clamp,
  cornerOffsets,
  floatPlacement,
  heightRange,
  sideStates,
  slotPanels,
  toolbarInsets,
  type CornerOffset,
  type ResolvedPanel,
  type SideState,
} from "./geometry";
import { useWorkspace, useWorkspaceReady } from "./store";
import type { Corner, DockSlot, SideSlot } from "./types";

export function useWindowWidth(): number {
  return useSyncExternalStore(
    (notify) => {
      window.addEventListener("resize", notify);
      return () => window.removeEventListener("resize", notify);
    },
    () => window.innerWidth,
    () => 1440,
  );
}

export interface WorkspaceLayout {
  left: SideState;
  right: SideState;
  /** Writes on <main>: the layout outputs and the viewport background. */
  vars: CSSProperties;
  offsets: Record<Corner, CornerOffset>;
  insets: { left: number; right: number };
  setDraft(side: SideSlot, width: number | null): void;
}

/** The side slots and the outputs, for App (which passes them to the islands and the context toolbar). */
export function useWorkspaceLayout(): WorkspaceLayout {
  const windowWidth = useWindowWidth();
  const layout = useWorkspace((s) => s.document.layout);
  const background = useWorkspace((s) => s.document.viewport.background);
  const open = useWorkspace((s) => s.sideOpen);
  const ready = useWorkspaceReady();
  const drawerOpen = useStore((s) => s.drawerOpen);
  const [drafts, setDrafts] = useState<Record<SideSlot, number | null>>({ left: null, right: null });
  const setDraft = useCallback((side: SideSlot, width: number | null) => setDrafts((d) => (d[side] === width ? d : { ...d, [side]: width })), []);
  const narrowLeft = windowWidth < 900;
  const narrowRight = windowWidth < 1100;
  const setSideOpen = useWorkspace((s) => s.setSideOpen);

  // Growing the window folds the manual override away, as before.
  useEffect(() => {
    if (!narrowLeft) setSideOpen("left", false);
  }, [narrowLeft, setSideOpen]);
  useEffect(() => {
    if (!narrowRight) setSideOpen("right", false);
  }, [narrowRight, setSideOpen]);

  return useMemo(() => {
    const empty = { left: { ...layout.left, panels: [] }, right: { ...layout.right, panels: [] } };
    const sides = sideStates(ready ? layout : { ...layout, ...empty }, drafts, open, windowWidth);
    const offsets = cornerOffsets(sides.left.occupied, sides.right.occupied);
    const bottomPanels = ready ? slotPanels(layout.bottom) : [];
    const bottom = bottomPanels.length === 0 ? 0 : drawerOpen ? layout.bottom.size : DRAWER_STRIP;
    const vars: Record<string, string> = {
      "--tree-width": `${sides.left.occupied}px`,
      "--props-width": `${sides.right.occupied}px`,
      "--slot-left": `${sides.left.occupied}px`,
      "--slot-right": `${sides.right.occupied}px`,
      "--slot-bottom": `${bottom}px`,
    };
    for (const corner of ["bl", "br", "tl", "tr"] as const) {
      vars[`--corner-${corner}-x`] = `${offsets[corner].x}px`;
      vars[`--corner-${corner}-y`] = `${offsets[corner].y}px`;
    }
    // A colour instead of the theme's gradient (viewport.background); the tokens stay the theme's.
    if (/^#[0-9a-fA-F]{6}$/.test(background)) {
      vars["--vp-top"] = background;
      vars["--vp-bottom"] = background;
    }
    return { left: sides.left, right: sides.right, vars: vars as CSSProperties, offsets, insets: toolbarInsets(sides.left.occupied, sides.right.occupied), setDraft };
  }, [layout, background, open, ready, drafts, windowWidth, drawerOpen, setDraft]);
}

// ---------------------------------------------------------------------------------------------- side slots
interface SideSlotProps {
  state: SideState;
  setDraft(side: SideSlot, width: number | null): void;
}

/** Writes a size back, or removes the override on a double click (the layer below shows). */

/**
 * Show or hide a dock, whichever state it is in.
 *
 * **One function for four entry points**: the header button, the chord, the View menu and the palette
 * all reach this, because a second implementation is the one that behaves differently. It is exported
 * so the action registration and the header share it rather than each calling `setValue` itself.
 */
/**
 * Show or hide the Details pane, which lives BESIDE the tree in the left dock rather than in a slot
 * of its own.
 *
 * **The two toggles are separate commands because they are separate panels** (the owner, via
 * claude-win2 1930 asks for one key each), even though both live in `layout.left`: putting the Details
 * pane away leaves the tree, and the other way round - which is what makes each chord useful on its
 * own. The tree's toggle therefore hides only `tree`; this one hides only `details`.
 */
/** Show or hide the tree alone, leaving the Details pane where it is. */
/*
  **HIDE and SHOW are two functions, not one toggle.**

  The owner: *"the tree hide feature is broken: when I tried to close the settings pane the tree
  refuses to close and is stuck in that every time I open the pipe demo"*. The layout is **remembered**,
  which is right - so a user who hid the tree last session found it hidden on open, pressed the card's
  **Hide** button, and the shared toggle **showed** it instead. From then on the button labelled Hide
  did the opposite, every time they opened the project: "stuck".

  A control must do what its label says whatever state it finds. The chord keeps toggling - "Show or
  hide" is what it is called - and the two buttons each get their own verb.
*/
export function hidePanel(id: "tree" | "details"): void {
  const workspace = useWorkspace.getState();
  const layout = workspace.document.layout;
  if (!layout.left.panels.some((p) => p.id === id)) return; // already away: pressing Hide again does nothing
  void workspace.setValue(["layout"], movePanel(layout, id, { kind: "hidden" }));
}

/** Bring a panel back to the left dock; does nothing when it is already there. */
export function showPanel(id: "tree" | "details"): void {
  const workspace = useWorkspace.getState();
  const layout = workspace.document.layout;
  if (layout.left.panels.some((p) => p.id === id)) return;
  const index = id === "tree" ? 0 : 1;
  void workspace.setValue(["layout"], movePanel(layout, id, { kind: "slot", slot: "left", index }));
}

export function toggleTree(): void {
  const workspace = useWorkspace.getState();
  const layout = workspace.document.layout;
  const visible = layout.left.panels.some((p) => p.id === "tree");
  const next = visible
    ? movePanel(layout, "tree", { kind: "hidden" })
    : movePanel(layout, "tree", { kind: "slot", slot: "left", index: 0 });
  void workspace.setValue(["layout"], next);
}

export function toggleDetails(): void {
  const workspace = useWorkspace.getState();
  const layout = workspace.document.layout;
  const visible = layout.left.panels.some((p) => p.id === "details");
  // `movePanel` to `hidden` keeps the left slot's other panel, and back to the slot restores the
  // order it had - the same mechanism the dock toggle uses, for one panel.
  const next = visible ? movePanel(layout, "details", { kind: "hidden" }) : movePanel(layout, "details", { kind: "slot", slot: "left", index: 1 });
  void workspace.setValue(["layout"], next);
}

export function toggleDock(side: DockSlot): void {
  const workspace = useWorkspace.getState();
  const layout = workspace.document.layout;
  const next = slotHidden(layout, side) ? showSlot(layout, side) : hideSlot(layout, side);
  if (next !== layout) void workspace.setValue(["layout"], next);
}

function resetSize(path: (string | number)[]): void {
  const w = useWorkspace.getState();
  const key = path.join(".");
  const source = w.sources[key];
  if (source === "user" || source === "project") void w.resetValue(path, source);
}

/**
 * One side slot. A single panel is placed exactly as PanelSlot placed it: 12 px from the edges, a
 * user-set width with a drag handle in the gutter, and below the fold breakpoint (or when folded in
 * the document) an icon strip that opens it on click.
 */
export function SideSlotView({ state, setDraft }: SideSlotProps) {
  const { side, panels } = state;
  const towards = side === "left" ? "right" : "left";
  const single = panels.length === 1 ? panels[0] : null;
  const name = (single ? single.def.title : side === "left" ? "Left panels" : "Right panels").toLowerCase();
  const strip = useRef<HTMLButtonElement>(null);
  const fold = useRef<HTMLButtonElement>(null);
  // The toggle that was just used goes away; its counterpart takes the keyboard focus.
  const focusNext = useRef<"strip" | "fold" | null>(null);
  const setSideOpen = useWorkspace((s) => s.setSideOpen);
  const setActiveTab = useWorkspace((s) => s.setActiveTab);

  useEffect(() => {
    if (focusNext.current === "fold" && !state.collapsed) fold.current?.focus();
    else if (focusNext.current === "strip" && state.collapsed) strip.current?.focus();
    focusNext.current = null;
  }, [state.collapsed]);

  if (panels.length === 0) return null;

  const expand = (tab?: string) => {
    focusNext.current = "fold";
    if (tab) setActiveTab(side, tab);
    setSideOpen(side, true);
    if (state.docCollapsed) void useWorkspace.getState().setValue(["layout", side, "collapsed"], false, { silent: true });
  };
  /**
   * **Hide the dock completely** (the owner, via claude-win2 1930: *"a collapse button in their header
   * that removes the card completely (no leftover strip or rail)"*).
   *
   * It used to collapse the slot to a 40 px strip - which is the rail the owner had deleted this
   * morning, one dock over. The panels go to `layout.hidden` now and the stashed ids are remembered in
   * the document, so `Ctrl+B` (or the header button of the other dock, or `Open <panel>`) brings them
   * back where they were.
   */


  return (
    <div
      className={`slot slot-${side}`}
      data-collapsed={state.collapsed}
      data-grow={state.grow ? "true" : undefined}
      data-mode={panels.length > 1 ? state.mode : undefined}
      style={{ width: state.occupied }}
      data-ws-slot={side}
    >
      <div className="slot-frame">
        <div className="slot-body" data-ws-panel={single?.def.id}>
          {/*
            **A lone panel keeps its Hide button** - and this was the owner's bug.

            `single` rendered the panel bare, so once the OTHER panel of the pair was hidden the
            survivor had no control at all: measured, hiding Details left `tree` alone in the dock with
            no hide button and no fold button, and the chord only toggled. *"The tree refuses to close
            and is stuck in that every time I open the pipe demo"* is exactly this - the saved layout
            remembered that Details was hidden, so every open left the tree alone and un-hideable.
          */}
          {single ? (
            /*
              **A positioned wrapper, because `.cell-hide` is absolutely placed.** Measured without it:
              the button stretched to the card's full **280 x 20** and sat at the card's top instead of
              in its corner - the "malformed 320x20 strip" Claude saw. **`position: relative` on the
              wrapper is what gives the button a corner to sit in**; `display: contents` looked like it
              would also keep the wrapper out of the layout, and it *cancels* the positioning context,
              which is why the first attempt changed nothing. The wrapper is a plain flex child instead.
            */
            <div className="single-card">

              <HideCard panel={single} />
              <single.def.component />
            </div>
          ) : state.mode === "split" ? (
            <SplitBody state={state} />
          ) : (
            <TabsBody state={state} />
          )}
        </div>
        {state.collapsed ? (
          single ? (
            <Tooltip label={`Show ${name}`} side={towards} describe={false}>
              <button ref={strip} type="button" className="slot-strip glass no-drag" aria-label={`Show ${name}`} aria-expanded={false} onClick={() => expand()}>
                <single.def.icon size={16} />
              </button>
            </Tooltip>
          ) : (
            <div className="slot-strip-group glass" role="group" aria-label={name}>
              {panels.map((p, i) => (
                <Tooltip key={p.def.id} label={`Show ${p.def.title.toLowerCase()}`} side={towards} describe={false}>
                  <button ref={i === 0 ? strip : undefined} type="button" className="slot-strip no-drag" aria-label={`Show ${p.def.title.toLowerCase()}`} aria-expanded={false} onClick={() => expand(p.def.id)}>
                    <p.def.icon size={16} />
                  </button>
                </Tooltip>
              ))}
            </div>
          )
        ) : (
          <>
            <ResizeHandle
              label={`Resize the ${name}`}
              value={state.width}
              min={state.min}
              max={state.max}
              grow={side === "left" ? 1 : -1}
              onChange={(w) => setDraft(side, w)}
              onCommit={(w) => {
                setDraft(side, null);
                void useWorkspace.getState().setValue(["layout", side, "size"], Math.round(w));
              }}
              onReset={() => {
                setDraft(side, null);
                resetSize(["layout", side, "size"]);
              }}
            />
            {/*
              **No slot-level "Hide left panels" button** (measured: Claude saw it as a second 28 x 28
              control at x=592 beside the card's own). A split dock holds two panels and a user hiding
              "the dock" is not what they mean when looking at two cards - each card has its own button,
              and the chord remains for the dock as a whole.
            */}
          </>
        )}
      </div>
    </div>
  );
}

/** Several panels in tabs mode: one glass card, a tab strip on top, the panels below (inactive ones stay mounted). */
/** A stable empty list, so a slot with nothing opened does not re-render the tab strip. */
const EMPTY_OPENED: readonly string[] = [];

function TabsBody({ state }: { state: SideState }) {
  const activeId = useWorkspace((s) => s.activeTab[state.side]);
  const setActiveTab = useWorkspace((s) => s.setActiveTab);
  const stage = useStore((s) => s.stage);
  const [stageFocus] = useBoolPref(STAGE_FOCUS_KEY, true);
  /** The panels opened explicitly in this slot, from the palette or a tree row. */
  const opened = useWorkspace((s) => s.opened[state.side] ?? EMPTY_OPENED);
  /*
    **A stage CHANGE clears what was opened** - and only a change, never the first render.

    The rule is the one the single pin already had: the panel a user asked for stays until the stage
    changes, and no longer. But an effect with `[stage]` also runs on MOUNT, so the first open after a
    reload was cleared a moment after the user made it - measured: `opened` read `{right: ["mesh"]}`
    and the tab strip stayed empty. Comparing against the previous stage is what distinguishes the two.
  */
  const lastStage = useRef(stage);
  useEffect(() => {
    if (lastStage.current === stage) return;
    lastStage.current = stage;
    useWorkspace.getState().clearOpened();
  }, [stage]);
  /**
   * **Stage focus, with a pin** (T-220).
   *
   * The pin is the panel the current tab names when the stage excludes it - so a tree row that opens
   * the Mesh panel while in Geometry keeps it on screen until the stage changes. It is derived from
   * the active tab rather than stored, because a stored pin would need clearing on every stage
   * change; deriving it means the pin CANNOT outlive the stage, which is the rule.
   */
  /*
    **Every panel the user has OPENED is pinned, not only the active tab.**

    The owner's report: *"hiding the tree and settings tabs is broken"*. Opening Mesh, then
    Conditions, then Properties from the palette in the Geometry stage placed all three in the
    document and showed **one** - because a single pin lets the newest hide the previous. A panel the
    user asked for stays until the stage changes, which is the rule the tree-row open already had.
  */
  const pinned = stageFocus ? (opened.length > 0 ? opened : activeId) : null;
  const { shown } = useMemo(
    () => (stageFocus ? panelsForStage(state.panels, stage, pinned) : { shown: state.panels, hidden: [] }),
    [stageFocus, state.panels, stage, pinned],
  );
  const active = shown.some((p) => p.def.id === activeId) ? activeId : shown[0]?.def.id;
  // A stage that brings a tab forward (Mesh, Solve) must not leave it clipped at the end of a strip
  // that scrolls: with five tabs in a 320 px slot the last one is out of view until this moves it in.
  const strip = useRef<HTMLDivElement>(null);
  useEffect(() => {
    strip.current?.querySelector<HTMLElement>("[aria-selected='true']")?.scrollIntoView?.({ inline: "nearest", block: "nearest" });
  }, [active]);
  return (
    <div className="slot-tabs glass">
      <div ref={strip} role="tablist" aria-label={state.side === "left" ? "Left panels" : "Right panels"} className="slot-tablist">
        {shown.map((p) => (
          <button
            key={p.def.id}
            type="button"
            role="tab"
            aria-selected={p.def.id === active}
            className={`tab ${p.def.id === active ? "active" : ""}`}
            onClick={() => setActiveTab(state.side, p.def.id)}
            data-ws-panel-tab={p.def.id}
          >
            {p.def.title}
          </button>
        ))}
      </div>
      {shown.map((p) => (
        <div key={p.def.id} role="tabpanel" aria-label={p.def.title} className="slot-tabpanel" hidden={p.def.id !== active} data-ws-panel={p.def.id}>
          <p.def.component />
        </div>
      ))}
    </div>
  );
}

/** Several panels in split mode: stacked glass cards with a handle in each gap; `size` is a panel's height. */
/**
 * Whether a side dock overlays rather than sits beside its neighbour.
 *
 * **The decision's rule in one place** (the layout decision 2026-10-18): below `NARROW_PX` the Details
 * pane "overlays the tree (a back arrow returns to the tree) instead of side by side". It applies to a
 * dock holding the tree and the pane, which is what tells a small window from a large one - a width
 * test alone would also catch the right dock, where there is nothing to overlay.
 *
 * `useWindowWidth` is the app's existing subscription, so this follows a resize without a media query
 * and the boundary is a number a test can pin rather than a CSS value nothing checks.
 */
function useDetailsOverlay(side: SideSlot): boolean {
  const width = useWindowWidth();
  const panels = useWorkspace((s) => s.document.layout[side].panels);
  const hasTreeAndPane = panels.some((p) => p.id === "tree") && panels.some((p) => p.id === "details");
  return hasTreeAndPane && detailsPlacement(width, false) === "overlay";
}

function SplitBody({ state }: { state: SideState }) {
  const [drafts, setDrafts] = useState<Record<string, number>>({});
  const body = useRef<HTMLDivElement>(null);
  const [available, setAvailable] = useState(0);
  useLayoutEffect(() => {
    const el = body.current;
    if (!el) return;
    const measure = () => setAvailable(el.clientHeight);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  const last = state.panels.length - 1;
  /**
   * Which panel the overlay shows, and whether the dock is in overlay mode at all.
   *
   * `openOverlay` starts at the FIRST panel (the tree) and a click moves it, so the dock arrives as
   * the tree - which is what a user expects of a left dock - and the pane is one click away.
   */
  const [openOverlay, setOpenOverlay] = useState<string | null>(null);
  const overlay = useDetailsOverlay(state.side);
  const picked = state.panels.find((p) => p.def.id === openOverlay) ?? state.panels[0];
  return (
    /*
      **A side dock splits ACROSS.** `SplitBody` handles `SideSlot`, which is left or right only - the
      bottom dock has its own container - so panels in it are always side by side. That is what the
      layout decision 2026-10-18 needs for the tree and the Details pane: measured before this, the
      pane landed UNDER the tree, because the split was a column.

      The axis is a property of the slot rather than of a panel: a panel does not know which dock it
      is in, and a document value would let a user set an axis that makes no sense where it sits.
    */
    <div ref={body} className="slot-split" data-axis={overlay ? "stack" : "across"} data-overlay={overlay || undefined}>
      {/*
        **Narrow: one panel at a time, with a way between them** (the layout decision 2026-10-18:
        *"the Details pane overlays the tree (a back arrow returns to the tree) instead of side by
        side"*).

        The rule is `detailsPlacement`, which pins the boundary in one tested place rather than in a
        media query. **Only the split's rendering changes** - every panel stays mounted, so the tree
        keeps its scroll position and the pane keeps its state, which is the same reason the tabs body
        keeps inactive panels mounted.
      */}
      {/*
        **The way between the panels while overlaying.** A back arrow alone would only toggle, and a
        dock may hold more than two panels; a small strip of the panels' own titles is what the tabs
        body already teaches a user, so the same idea appears here rather than a new affordance.
      */}
      {overlay && state.panels.length > 1 && (
        <div className="slot-overlay-tabs" role="tablist" aria-label="Panels">
          {state.panels.map((p) => (
            <button
              key={p.def.id}
              type="button"
              role="tab"
              aria-selected={p.def.id === picked?.def.id}
              className={`tab ${p.def.id === picked?.def.id ? "active" : ""}`}
              onClick={() => setOpenOverlay(p.def.id)}
            >
              {p.def.title}
            </button>
          ))}
        </div>
      )}
      {(overlay ? [picked] : state.panels).filter((p): p is ResolvedPanel => p !== undefined).map((p, i) => {
        const folded = p.placement.collapsed === true;
        const size = drafts[p.def.id] ?? p.placement.size;
        const fixed = !folded && i < last && size !== undefined;
        return (
          <div key={p.def.id} className="slot-cell" data-folded={folded || undefined} data-grow={p.def.grow || undefined} style={fixed ? { flex: `0 0 ${size}px` } : undefined} data-ws-panel={p.def.id}>
            {i > 0 && <CellHandle state={state} index={i - 1} drafts={drafts} setDrafts={setDrafts} available={available} />}
            {folded && <CellHeader panel={p} side={state.side} />}
            {/*
              **A hide button on every card in a split** (claude-win2's GUI review item 5: *"neither
              card has a header collapse button"*). The slot's own fold button lives in the TABS
              branch, so the default left dock - which is a split - had none at all, at any window
              width. One button per card, because a split holds two panels and hiding "the dock" is
              not what a user means when they are looking at two.
            */}
            <HideCard panel={p} />
            {/* Mounted while folded (the CSS hides it), so the panel keeps its state and listeners. */}
            <p.def.component />
          </div>
        );
      })}
    </div>
  );
}


/**
 * The hide control on one card in a split dock.
 *
 * **In the card's own corner, not a header row**: these panels draw their own section titles (the
 * tree's "SIMULATION", the pane's "NEXT STEP"), and a header added above them would be a second title
 * for the same thing. A small button in the top-right of the card sits where a card's controls belong
 * and costs no height.
 */
function HideCard({ panel }: { panel: ResolvedPanel }) {
  const keys = useShortcut(panel.def.id === "details" ? "layout.toggleDetails" : "layout.toggleTree");
  return (
    <Tooltip label={`Hide the ${panel.def.title}`} hint={keys.hint} side="bottom" describe={false}>
      <button
        type="button"
        className="cell-hide no-drag"
        aria-label={`Hide the ${panel.def.title}`}
        aria-keyshortcuts={keys.keys}
        data-cell-hide={panel.def.id}
        onClick={() => hidePanel(panel.def.id === "details" ? "details" : "tree")}
      >
        <svg width={13} height={13} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" aria-hidden="true">
          <path d="M18 6 6 18M6 6l12 12" />
        </svg>
      </button>
    </Tooltip>
  );
}

function CellHandle({
  state,
  index,
  drafts,
  setDrafts,
  available,
}: {
  state: SideState;
  index: number;
  drafts: Record<string, number>;
  setDrafts: (update: (d: Record<string, number>) => Record<string, number>) => void;
  available: number;
}) {
  const above = state.panels[index];
  const [min] = heightRange([above]);
  const below = state.panels.slice(index + 1).reduce((sum, p) => sum + heightRange([p])[0], 0);
  const max = Math.max(min, available - below - INSET * (state.panels.length - 1));
  const measured = () => {
    const cell = document.querySelector<HTMLElement>(`[data-ws-slot="${state.side}"] .slot-cell[data-ws-panel="${above.def.id}"]`);
    return Math.round(cell?.getBoundingClientRect().height ?? min);
  };
  const value = drafts[above.def.id] ?? above.placement.size ?? measured();
  return (
    <SplitHandle
      orientation="horizontal"
      label={`Resize ${above.def.title.toLowerCase()}`}
      value={clamp(value, min, max)}
      min={min}
      max={max}
      onChange={(h) => setDrafts((d) => ({ ...d, [above.def.id]: h }))}
      onCommit={(h) => {
        setDrafts((d) => {
          const { [above.def.id]: _gone, ...rest } = d;
          void _gone;
          return rest;
        });
        void useWorkspace.getState().setValue(["layout", state.side, "panels", above.index, "size"], Math.round(h));
      }}
    />
  );
}

/** A folded split cell shows its header only: icon, title and the unfold button (folding is in the panel menu of Customise mode). */
function CellHeader({ panel, side }: { panel: ResolvedPanel; side: SideSlot }) {
  const Icon = panel.def.icon;
  const unfold = () => void useWorkspace.getState().setValue(["layout", side, "panels", panel.index, "collapsed"], false, { silent: true });
  return (
    <div className="cell-head glass">
      <Icon size={14} className="muted" aria-hidden="true" />
      <span className="section-title cell-title">{panel.def.title}</span>
      <IconButton label={`Unfold ${panel.def.title.toLowerCase()}`} aria-expanded={false} className="!h-6 !w-6" onClick={unfold}>
        <ChevronDown size={14} />
      </IconButton>
    </div>
  );
}

// ---------------------------------------------------------------------------------------------- float cards
function useMainSize(): { width: number; height: number } {
  const [size, setSize] = useState({ width: 1200, height: 800 });
  useLayoutEffect(() => {
    const main = document.querySelector("main");
    if (!main) return;
    const measure = () => setSize((s) => (s.width === main.clientWidth && s.height === main.clientHeight ? s : { width: main.clientWidth, height: main.clientHeight }));
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(main);
    return () => observer.disconnect();
  }, []);
  return size;
}

/** The float slot: cards over the viewport, each kept inside it. */
export function FloatLayer() {
  const slot = useWorkspace((s) => s.document.layout.float);
  const ready = useWorkspaceReady();
  const viewport = useMainSize();
  if (!ready) return null;
  const panels = slotPanels(slot);
  return (
    <>
      {panels.map((p, position) => {
        const place = floatPlacement(p.placement, p.def, slot.size, position, viewport);
        const folded = p.placement.collapsed === true;
        const style: CSSProperties = { width: place.width, maxHeight: Math.max(DRAWER_STRIP, viewport.height - place.y - INSET) };
        if (place.anchor[0] === "t") style.top = place.y;
        else style.bottom = place.y;
        if (place.anchor[1] === "l") style.left = place.x;
        else style.right = place.x;
        if (p.def.grow && !folded) style.height = Math.min(520, viewport.height - place.y - INSET);
        return (
          <div key={p.def.id} className="float-card glass" data-collapsed={folded} data-anchor={place.anchor} style={style} data-ws-panel={p.def.id} data-ws-float={p.index}>
            <FloatHeader panel={p} folded={folded} />
            <div className="float-body" hidden={folded}>
              <p.def.component />
            </div>
          </div>
        );
      })}
    </>
  );
}

/**
 * A float card's header: while open, a slim grab bar with the fold button (the panel below carries
 * its own title); folded, the icon, the title and the unfold button.
 */
function FloatHeader({ panel, folded }: { panel: ResolvedPanel; folded: boolean }) {
  const Icon = panel.def.icon;
  const name = panel.def.title.toLowerCase();
  const toggle = () => void useWorkspace.getState().setValue(["layout", "float", "panels", panel.index, "collapsed"], !folded, { silent: true });
  if (!folded) {
    return (
      <div className="float-head float-bar">
        <span className="float-grip" aria-hidden="true" />
        <IconButton label={`Fold ${name}`} aria-expanded className="float-fold !h-5 !w-5" onClick={toggle}>
          <ChevronUp size={13} />
        </IconButton>
      </div>
    );
  }
  return (
    <div className="float-head">
      <Icon size={14} className="muted" aria-hidden="true" />
      <span className="section-title float-title">{panel.def.title}</span>
      <IconButton label={`Unfold ${name}`} aria-expanded={false} className="!h-6 !w-6" onClick={toggle}>
        <ChevronDown size={14} />
      </IconButton>
    </div>
  );
}

// ---------------------------------------------------------------------------------------------- bottom slot
let seeding = false;

/**
 * The bottom slot under the viewport. Its open state is the project store's `drawerOpen` (the toasts
 * and the palette open the console through it): it is set from `layout.bottom.collapsed` when the
 * document loads or that value changes, and a fold or unfold by the user is written back quietly.
 */
export function BottomSlot() {
  const slot = useWorkspace((s) => s.document.layout.bottom);
  const ready = useWorkspaceReady();
  const docCollapsed = slot.collapsed;
  const seen = useRef<boolean | null>(null);

  useEffect(() => {
    if (!ready || seen.current === docCollapsed) return;
    seen.current = docCollapsed;
    const store = useStore.getState();
    if (store.drawerOpen === !docCollapsed) return;
    seeding = true;
    try {
      store.toggleDrawer(!docCollapsed);
    } finally {
      seeding = false;
    }
  }, [docCollapsed, ready]);

  useEffect(
    () =>
      useStore.subscribe((state, previous) => {
        if (state.drawerOpen === previous.drawerOpen || seeding) return;
        const w = useWorkspace.getState();
        if (w.status !== "ready" || slotPanels(w.document.layout.bottom).length === 0) return;
        if (w.document.layout.bottom.collapsed === !state.drawerOpen) return;
        seen.current = !state.drawerOpen;
        void w.setValue(["layout", "bottom", "collapsed"], !state.drawerOpen, { silent: true });
      }),
    [],
  );

  const panels = useMemo(() => (ready ? slotPanels(slot) : []), [slot, ready]);
  const drawerPanels = useMemo<DrawerPanel[]>(() => panels.map((p) => ({ id: p.def.id, title: p.def.title, component: p.def.component, size: p.placement.size })), [panels]);
  if (panels.length === 0) return null;
  const [min, max] = heightRange(panels);
  const height = clamp(slot.size, min, max);
  return (
    <BottomDrawer
      panels={drawerPanels}
      mode={slot.mode}
      height={height}
      min={min}
      max={max}
      onCommit={(h) => void useWorkspace.getState().setValue(["layout", "bottom", "size"], Math.round(h))}
      onReset={() => resetSize(["layout", "bottom", "size"])}
      onCommitPanel={(i, w) => {
        const p = panels[i];
        if (p) void useWorkspace.getState().setValue(["layout", "bottom", "panels", p.index, "size"], Math.round(w));
      }}
    />
  );
}
