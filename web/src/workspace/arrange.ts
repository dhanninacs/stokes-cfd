/**
 * Arrangement edits as pure functions over the sections of the workspace document. Customise mode
 * (and the palette, later) builds the whole changed section here and writes it with one
 * `workspace.set`, so a move is atomic (docs/engineering/workspace-schema.md, "Consuming the
 * document"). The invariants the server checks hold for every result: a panel or island id appears in
 * exactly one place, every list keeps its unknown ids and entries, `order` is the display position,
 * and the float-only keys (`anchor`, `x`, `y`) exist only in the float slot.
 */
import { clone } from "./paths";
import type { Corner, DockSlot, IslandsSpec, LayoutSpec, PanelPlacement, SlotName, TopbarItemSpec } from "./types";

export type PanelTarget =
  | { kind: "slot"; slot: DockSlot; index: number }
  | { kind: "float"; anchor: Corner; x: number; y: number }
  | { kind: "hidden" };

const SLOT_NAMES: readonly SlotName[] = ["left", "right", "bottom", "float"];

function byOrder(panels: readonly PanelPlacement[]): PanelPlacement[] {
  return panels
    .map((p, i) => ({ p, i }))
    .sort((a, b) => (a.p.order ?? a.i) - (b.p.order ?? b.i) || a.i - b.i)
    .map((x) => x.p);
}

/** The slot's panels in display order, with `order` rewritten as the position. */
function renumber(panels: readonly PanelPlacement[]): PanelPlacement[] {
  return byOrder(panels).map((p, i) => ({ ...p, order: i }));
}

/** A panel entry as it may stand in a docked slot: the float-only keys go, and a size meant for another slot. */
function docked(entry: PanelPlacement, keepSize: boolean): PanelPlacement {
  const { anchor: _a, x: _x, y: _y, size, ...rest } = entry;
  void _a;
  void _x;
  void _y;
  return keepSize && size !== undefined ? { ...rest, size } : rest;
}

/** Move a panel to a slot (at a display position), to a float card, or to the hidden list. */
export function movePanel(layout: LayoutSpec, id: string, target: PanelTarget): LayoutSpec {
  const next = clone(layout);
  let entry: PanelPlacement = { id };
  let from: SlotName | "hidden" | null = null;
  for (const slot of SLOT_NAMES) {
    const found = next[slot].panels.find((p) => p.id === id);
    if (found) {
      entry = found;
      from = slot;
    }
    next[slot].panels = next[slot].panels.filter((p) => p.id !== id);
  }
  if (next.hidden.includes(id)) from ??= "hidden";
  next.hidden = next.hidden.filter((h) => h !== id);

  if (target.kind === "hidden") {
    next.hidden.push(id);
  } else if (target.kind === "float") {
    const base = docked(entry, false);
    const size = from === "float" ? entry.size : undefined;
    const card: PanelPlacement = { ...base, anchor: target.anchor, x: Math.round(Math.max(0, target.x)), y: Math.round(Math.max(0, target.y)) };
    if (size !== undefined) card.size = size;
    next.float.panels = [...byOrder(next.float.panels), card];
  } else {
    const list = byOrder(next[target.slot].panels);
    const at = Math.max(0, Math.min(target.index, list.length));
    list.splice(at, 0, docked(entry, from === target.slot));
    next[target.slot].panels = list;
  }
  for (const slot of SLOT_NAMES) next[slot].panels = renumber(next[slot].panels);
  return next;
}

export function setSlotMode(layout: LayoutSpec, slot: SlotName, mode: "tabs" | "split"): LayoutSpec {
  return { ...layout, [slot]: { ...layout[slot], mode } };
}

const CORNER_LISTS: readonly (Corner | "hidden")[] = ["bl", "br", "tl", "tr", "hidden"];

/** Move an island group to a corner (at an on-screen position, left to right) or to the hidden list. */
export function moveIsland(islands: IslandsSpec, id: string, target: Corner | "hidden", index = Number.MAX_SAFE_INTEGER): IslandsSpec {
  const next = clone(islands);
  for (const list of CORNER_LISTS) next[list] = next[list].filter((i) => i !== id);
  const list = next[target];
  list.splice(Math.max(0, Math.min(index, list.length)), 0, id);
  return next;
}

/** Move a top-bar item to a position of the full list (shown again), or hide it where it is. */
export function moveTopbarItem(items: readonly TopbarItemSpec[], id: string, target: { index: number } | "hide" | "show"): TopbarItemSpec[] {
  const next = items.map((i) => ({ ...i }));
  const from = next.findIndex((i) => i.id === id);
  if (from < 0) return next;
  if (target === "hide" || target === "show") {
    next[from].visible = target === "show";
    return next;
  }
  const [item] = next.splice(from, 1);
  item.visible = true;
  const at = target.index > from ? target.index - 1 : target.index;
  next.splice(Math.max(0, Math.min(at, next.length)), 0, item);
  return next;
}

/** One step left or right among the items shown, keeping the hidden ones where they are. */
export function nudgeTopbarItem(items: readonly TopbarItemSpec[], id: string, step: -1 | 1, shown: (item: TopbarItemSpec) => boolean): TopbarItemSpec[] {
  const visible = items.filter(shown);
  const at = visible.findIndex((i) => i.id === id);
  const neighbour = visible[at + step];
  if (at < 0 || !neighbour) return items.map((i) => ({ ...i }));
  const target = items.findIndex((i) => i.id === neighbour.id);
  return moveTopbarItem(items, id, { index: step > 0 ? target + 1 : target });
}
