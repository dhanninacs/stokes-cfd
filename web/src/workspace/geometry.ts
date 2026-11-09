/**
 * The slot layout as numbers: which panels each slot shows, how wide or tall it is after the panels'
 * own limits and the window's, and where the island corners and the context toolbar must stay clear
 * of it. Pure functions of the document and the window size; Layout.tsx renders them.
 *
 * The panels float over the viewport (DESIGN.md section 2): a side slot sits 12 px from the edges
 * (the right one stops 140 px above the bottom, which keeps the bottom-right corner free for the view
 * cube and the view group: styles.css), the bottom slot is the drawer under the
 * viewport, float cards sit where their entries say. Below a window width (900 px left, 1100 px
 * right) a side slot folds to its icon strip unless the user opened it, as before.
 */
import { PANELS, type PanelDef } from "./registry";
import type { Corner, LayoutSpec, PanelPlacement, SideSlot, SlotMode, SlotSpec } from "./types";

/** Gap between a panel and the viewport edge, and between neighbours, in px. */
export const INSET = 12;
/** Width of a folded side slot (the icon strip). */
export const STRIP = 40;
/** The viewport never gets narrower than this because of side panels. */
export const MIN_VIEWPORT = 360;
/** Gap between the island groups of one corner (the view cube's box is one of them). */
export const ISLAND_GAP = 8;
/** A side slot folds to its strip below this window width (unless opened). */
export const COLLAPSE_BELOW: Record<SideSlot, number> = { left: 900, right: 1100 };
/** Height of the drawer strip, and of a folded float card or split cell header. */
export const DRAWER_STRIP = 36;

export function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

/** A panel of a slot, resolved in the registry. `index` is its position in the document's list. */
export interface ResolvedPanel {
  placement: PanelPlacement;
  def: PanelDef;
  index: number;
}

/** The panels a slot shows, in display order (lowest `order` first, ties in list order); unknown ids are skipped. */
export function slotPanels(slot: SlotSpec): ResolvedPanel[] {
  return slot.panels
    .map((placement, index) => ({ placement, index, def: PANELS.get(placement.id) }))
    .filter((p): p is ResolvedPanel => p.def !== undefined)
    .sort((a, b) => (a.placement.order ?? a.index) - (b.placement.order ?? b.index) || a.index - b.index);
}

/** Where a panel is placed: a slot, `hidden`, or null when the document does not place it. */
export function placeOf(layout: LayoutSpec, id: string): "left" | "right" | "bottom" | "float" | "hidden" | null {
  for (const slot of ["left", "right", "bottom", "float"] as const) if (layout[slot].panels.some((p) => p.id === id)) return slot;
  return layout.hidden.includes(id) ? "hidden" : null;
}

/** True when the panel is rendered somewhere (placed in a slot, not hidden, known to the registry). */
export function panelShown(layout: LayoutSpec, id: string): boolean {
  const place = placeOf(layout, id);
  return place !== null && place !== "hidden" && PANELS.has(id);
}

/** The width range the panels of a slot all accept. */
export function widthRange(panels: readonly ResolvedPanel[]): [number, number] {
  const min = Math.max(...panels.map((p) => p.def.width[0]));
  const max = Math.min(...panels.map((p) => p.def.width[1]));
  return [min, Math.max(min, max)];
}

export function heightRange(panels: readonly ResolvedPanel[]): [number, number] {
  const min = Math.max(...panels.map((p) => p.def.height[0]));
  const max = Math.min(...panels.map((p) => p.def.height[1]));
  return [min, Math.max(min, max)];
}

export interface SideState {
  side: SideSlot;
  panels: ResolvedPanel[];
  mode: SlotMode;
  /** Panel width in px, after the panels' and the window's limits. */
  width: number;
  /** Lowest and highest width the handle may reach right now. */
  min: number;
  max: number;
  /** What the slot occupies: the strip when folded, the panel otherwise, nothing when empty. */
  occupied: number;
  /** Showing the icon strip. */
  collapsed: boolean;
  /** Folded in the document (`layout.<side>.collapsed`), whatever the window width. */
  docCollapsed: boolean;
  /** The window is narrow enough that the slot folds by itself: the fold button shows. */
  canCollapse: boolean;
  /** The slot reaches from the top to the bottom of its area (a growing panel is shown). */
  grow: boolean;
}

interface SideInput {
  slot: SlotSpec;
  /** The width being dragged, if any. */
  draft: number | null;
  /** Opened by the user while the narrow window folds it. */
  open: boolean;
}

function sideBase(side: SideSlot, input: SideInput, windowWidth: number) {
  const panels = slotPanels(input.slot);
  const narrow = windowWidth < COLLAPSE_BELOW[side];
  const docCollapsed = input.slot.collapsed;
  const collapsed = panels.length > 0 && (docCollapsed || (narrow && !input.open));
  const [min, max] = panels.length ? widthRange(panels) : [0, 0];
  const raw = input.draft ?? input.slot.size;
  return { panels, narrow, docCollapsed, collapsed, min, max, raw };
}

/** Both side slots: each one's room depends on what the other occupies. */
/**
 * How wide a dock must be, given its panels.
 *
 * **Only a SPLIT dock needs more than the document says.** A tabs dock shows one panel at a time, so
 * the document's size is the whole answer; a split dock shows them side by side, and the document's
 * size was written when the dock had one panel. Measured before this: `left: 300` gave the tree and
 * the Details pane 154 px each, narrower than the pane's own declared minimum of 320.
 *
 * `SPLIT_GAP` matches the 12 px `.slot-split` gap in the stylesheet - a second copy of a number is a
 * thing that drifts, so it is named here and the test pins it against the CSS.
 */
export const SPLIT_GAP = 12;

/** The window width below which the Details pane overlays the tree: `NARROW_PX` in DetailsPanel.tsx. */
export const OVERLAY_BELOW = 1100;

function splitWidth(
    mode: SlotMode,
    panels: readonly ResolvedPanel[],
    width: number,
    overlays = false,
): number {
  // Below the narrow breakpoint the dock shows ONE panel at a time (the Details pane overlays the
  // tree), so it needs no more than the document's width. Widening it to both minima took 572 of
  // 1024 px and left the viewport a sliver.
  if (mode !== "split" || panels.length < 2 || overlays) return width;
  const needed = panels.reduce((sum, p) => sum + p.def.width[0], 0) + SPLIT_GAP * (panels.length - 1);
  return Math.max(width, needed);
}

export function sideStates(layout: LayoutSpec, drafts: Record<SideSlot, number | null>, open: Record<SideSlot, boolean>, windowWidth: number): Record<SideSlot, SideState> {
  const left = sideBase("left", { slot: layout.left, draft: drafts.left, open: open.left }, windowWidth);
  const right = sideBase("right", { slot: layout.right, draft: drafts.right, open: open.right }, windowWidth);
  const roughly = (s: typeof left) => (s.panels.length === 0 ? 0 : s.collapsed ? STRIP : s.raw);
  const room = (other: number) => windowWidth - other - 4 * INSET - MIN_VIEWPORT;
  const build = (side: SideSlot, s: typeof left, other: typeof left): SideState => {
    const activeGrow = s.panels.some((p) => p.def.grow);
    if (s.panels.length === 0) {
      return { side, panels: [], mode: layout[side].mode, width: 0, min: 0, max: 0, occupied: 0, collapsed: false, docCollapsed: s.docCollapsed, canCollapse: false, grow: false };
    }
    const maxNow = clamp(room(roughly(other)), s.min, s.max);
    const width = clamp(s.raw, s.min, maxNow);
    return {
      side,
      panels: s.panels,
      mode: layout[side].mode,
      width,
      min: s.min,
      max: maxNow,
      // **A split dock is as wide as its panels need.**
      //
      // A side dock splits ACROSS (the layout decision 2026-10-18), so two panels share the width the
      // document gives the slot - and `left: 300` produced two 154 px columns, measured, which is
      // narrower than the Details pane's own minimum of 320. The width here is therefore the larger
      // of what the document asks for and the sum of the panels' declared minima plus the gap, so a
      // split shows two usable panels without the user having to widen the dock first.
      occupied: s.collapsed ? STRIP : splitWidth(layout[side].mode, s.panels, width, windowWidth < OVERLAY_BELOW && s.panels.some((p) => p.def.id === "details")),
      collapsed: s.collapsed,
      docCollapsed: s.docCollapsed,
      // **A card can always be put away** (claude-win2's GUI review item 5: *"no visible way to
      // collapse or reopen a card… I found no edge handle when both are collapsed"*). The button was
      // gated on `narrow`, so at 1600 px there was none - hiding a card is useful at every width now
      // that it REMOVES the card rather than collapsing it to a strip.
      canCollapse: true,
      grow: activeGrow,
    };
  };
  return { left: build("left", left, right), right: build("right", right, left) };
}

export interface CornerOffset {
  /** From the corner's left or right edge of the viewport, px. */
  x: number;
  /** From its top or bottom edge, px. */
  y: number;
}

/**
 * Where each island corner starts, clear of the side slots: right of the left slot, left of the right
 * slot at the top. The bottom right starts at the viewport's inset, under the right slot, which stops
 * above it. The view cube is a member of its corner's row (IslandLayer.tsx), so the groups beside it
 * make room for it themselves.
 */
export function cornerOffsets(leftOccupied: number, rightOccupied: number): Record<Corner, CornerOffset> {
  const left = leftOccupied > 0 ? INSET + leftOccupied + INSET : INSET;
  const right = rightOccupied > 0 ? INSET + rightOccupied + INSET : INSET;
  return {
    bl: { x: left, y: INSET },
    tl: { x: left, y: INSET },
    tr: { x: right, y: INSET },
    br: { x: INSET, y: INSET },
  };
}

/** The free band between the side slots for the context toolbar. */
export function toolbarInsets(leftOccupied: number, rightOccupied: number): { left: number; right: number } {
  return {
    left: leftOccupied > 0 ? leftOccupied + 2 * INSET : INSET,
    right: rightOccupied > 0 ? rightOccupied + 2 * INSET : INSET,
  };
}

/** Float card placement: inside the viewport, measured from its anchor corner. */
export interface FloatPlacement {
  anchor: Corner;
  x: number;
  y: number;
  width: number;
}

export function floatPlacement(placement: PanelPlacement, def: PanelDef, fallbackWidth: number, position: number, viewport: { width: number; height: number }): FloatPlacement {
  const anchor = placement.anchor ?? "tl";
  const wanted = clamp(placement.size ?? fallbackWidth, def.width[0], def.width[1]);
  const width = Math.max(160, Math.min(wanted, viewport.width - 2 * INSET));
  const x = clamp(placement.x ?? 24 + 32 * position, 0, Math.max(0, viewport.width - width));
  const y = clamp(placement.y ?? 24 + 32 * position, 0, Math.max(0, viewport.height - DRAWER_STRIP));
  return { anchor, x, y, width };
}
