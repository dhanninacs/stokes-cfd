/**
 * Customise mode (ADR-0015 §5; `Ctrl Shift C`, Appearance → Customise…, the palette): a layer
 * over the window with a handle on every panel, island group and top-bar item. Drag a handle (pointer
 * events, no library) and drop zones appear: the left, right and bottom slots and the viewport (a
 * float card) for panels, the four corners for islands, the places between the top-bar items; a Hide
 * target takes any of them. A drop writes the whole changed section (`layout`, `islands` or
 * `topbar.items`) with one `workspace.set`, in the scope the toolbar's toggle chooses (this project or
 * all projects). Clicking a handle, or Enter on it, opens its menu with Move to… and the slot's options,
 * so everything is also possible by keyboard. The toolbar lists what is hidden (to bring it back), the
 * values the workspace files had wrong (kind `invalid`), and Reset workspace. Escape or Done leaves.
 * Outside this mode nothing moves.
 */
import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent, type PointerEvent as ReactPointerEvent, type RefObject } from "react";
import { createPortal } from "react-dom";
import { Check, ChevronDown, ChevronUp, EyeOff, GripVertical, LayoutGrid, RotateCcw, TriangleAlert } from "lucide-react";
import { Button, Segmented } from "../components/ui";
import { Dialog } from "../components/Dialog";
import { Menu, type MenuItemSpec } from "../components/Menu";
import { Tooltip } from "../components/Tooltip";
import { useStore } from "../state/store";
import { host } from "../app/host";
import { moveIsland, movePanel, moveTopbarItem, nudgeTopbarItem, setSlotMode, type PanelTarget } from "./arrange";
import { CORNERS } from "./defaults";
import { placeOf, slotPanels } from "./geometry";
import { ISLANDS, PANELS, TOPBAR_ITEMS, type RegistryEntry } from "./registry";
import { useWorkspace } from "./store";
import type { Corner, DockSlot, Scope, TopbarItemSpec, WorkspaceDocument } from "./types";

type Kind = RegistryEntry["kind"];

interface Box {
  left: number;
  top: number;
  width: number;
  height: number;
}

interface Target {
  kind: Kind;
  id: string;
  title: string;
  box: Box;
  /** Where it is now: a slot, a corner, or the top bar. */
  place: string;
}

type Zone =
  | { kind: "slot"; slot: DockSlot; box: Box; label: string }
  | { kind: "float"; box: Box; label: string }
  | { kind: "corner"; corner: Corner; box: Box; label: string }
  | { kind: "bar"; box: Box; label: string }
  | { kind: "hide"; box: Box; label: string };

const SIDE_LABEL: Record<DockSlot | "float" | "hidden", string> = { left: "Left side", right: "Right side", bottom: "Bottom drawer", float: "Floating card", hidden: "Hidden" };
const CORNER_LABEL: Record<Corner, string> = { bl: "Bottom left", br: "Bottom right", tl: "Top left", tr: "Top right" };
const DRAG_PX = 4;

function boxOf(el: Element | null): Box | null {
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0 ? { left: r.left, top: r.top, width: r.width, height: r.height } : null;
}

function inside(box: Box, x: number, y: number): boolean {
  return x >= box.left && x <= box.left + box.width && y >= box.top && y <= box.top + box.height;
}

const shownItem = (hosted: boolean) => (i: TopbarItemSpec) => i.visible !== false && TOPBAR_ITEMS.has(i.id) && (i.id !== "window" || hosted);

/** What the overlay puts handles on, measured from the DOM (layout, islands and top bar as rendered). */
function measure(doc: WorkspaceDocument): Target[] {
  const out: Target[] = [];
  for (const slot of ["left", "right", "bottom", "float"] as const) {
    for (const p of slotPanels(doc.layout[slot])) {
      const box =
        boxOf(document.querySelector(`[data-ws-panel="${p.def.id}"]`)) ??
        boxOf(document.querySelector(`[data-ws-panel-tab="${p.def.id}"]`)) ??
        boxOf(document.querySelector(`[data-ws-slot="${slot}"]`));
      if (box) out.push({ kind: "panel", id: p.def.id, title: p.def.title, box, place: slot });
    }
  }
  for (const corner of CORNERS) {
    for (const id of doc.islands[corner]) {
      const def = ISLANDS.get(id);
      if (!def) continue;
      // The view cube has no component: the viewport draws it into the box the island layer reserves for it.
      const box = boxOf(document.querySelector(`[data-ws-island="${id}"]`));
      if (box) out.push({ kind: "island", id, title: def.title, box, place: corner });
    }
  }
  for (const item of doc.topbar.items) {
    const def = TOPBAR_ITEMS.get(item.id);
    if (!def || item.visible === false) continue;
    const box = boxOf(def.locate(document));
    if (box) out.push({ kind: "topbar", id: item.id, title: def.title, box, place: "bar" });
  }
  return out;
}

function sameTargets(a: readonly Target[], b: readonly Target[]): boolean {
  const k = (t: Target) => `${t.kind}:${t.id}:${t.place}:${Math.round(t.box.left)},${Math.round(t.box.top)},${Math.round(t.box.width)},${Math.round(t.box.height)}`;
  return a.length === b.length && a.every((t, i) => k(t) === k(b[i]));
}

/** The drop zones for what is being dragged, from the window's current layout. */
function zonesFor(kind: Kind, hide: Box | null): Zone[] {
  const main = boxOf(document.querySelector("main"));
  const bar = boxOf(document.querySelector(".topbar"));
  const drawer = boxOf(document.querySelector('[data-ws-slot="bottom"]'));
  const zones: Zone[] = [];
  if (hide) zones.push({ kind: "hide", box: hide, label: "Hide" });
  if (!main) return zones;
  if (kind === "panel") {
    const band = Math.min(Math.max(200, main.width * 0.22), 360);
    const bottomTop = main.top + main.height - 96;
    const bottomBox: Box = { left: main.left + band, top: bottomTop, width: main.width - 2 * band, height: (drawer ? drawer.top + drawer.height : main.top + main.height) - bottomTop };
    zones.push(
      { kind: "slot", slot: "left", box: { left: main.left, top: main.top, width: band, height: main.height + (drawer?.height ?? 0) }, label: SIDE_LABEL.left },
      { kind: "slot", slot: "right", box: { left: main.left + main.width - band, top: main.top, width: band, height: main.height + (drawer?.height ?? 0) }, label: SIDE_LABEL.right },
      { kind: "slot", slot: "bottom", box: bottomBox, label: SIDE_LABEL.bottom },
      { kind: "float", box: { left: main.left + band, top: main.top, width: main.width - 2 * band, height: bottomTop - main.top }, label: "Float over the viewport" },
    );
  } else if (kind === "island") {
    // The quadrants of the viewport: a drop anywhere in one picks its corner.
    const w = main.width / 2;
    const h = main.height / 2;
    const at: Record<Corner, Box> = {
      tl: { left: main.left, top: main.top, width: w, height: h },
      tr: { left: main.left + main.width - w, top: main.top, width: w, height: h },
      bl: { left: main.left, top: main.top + main.height - h, width: w, height: h },
      br: { left: main.left + main.width - w, top: main.top + main.height - h, width: w, height: h },
    };
    for (const corner of CORNERS) zones.push({ kind: "corner", corner, box: at[corner], label: CORNER_LABEL[corner] });
  } else if (bar) {
    zones.push({ kind: "bar", box: bar, label: "Top bar" });
  }
  return zones;
}

interface DragState {
  target: Target;
  x: number;
  y: number;
  zone: Zone | null;
  zones: Zone[];
}

export default function CustomiseOverlay() {
  const on = useWorkspace((s) => s.customising);
  return on ? <Overlay /> : null;
}

function Overlay() {
  const doc = useWorkspace((s) => s.document);
  const scope = useWorkspace((s) => s.scope);
  const files = useWorkspace((s) => s.files);
  const warnings = useWorkspace((s) => s.warnings);
  const setScope = useWorkspace((s) => s.setScope);
  const setCustomising = useWorkspace((s) => s.setCustomising);
  const hosted = host.available;
  const [targets, setTargets] = useState<Target[]>([]);
  // The toolbar sits 8 px into the viewport, under the top bar and the ribbon if there is one.
  const [top, setTop] = useState(56);
  const [drag, setDragState] = useState<DragState | null>(null);
  // The latest drag, for the drop on pointer up (a state updater must not write).
  const dragRef = useRef<DragState | null>(null);
  const setDrag = (next: DragState | null) => {
    dragRef.current = next;
    setDragState(next);
  };
  const [menu, setMenu] = useState<{ target: Target | HiddenEntry; anchor: HTMLElement } | null>(null);
  const [confirmReset, setConfirmReset] = useState(false);
  const [problemsOpen, setProblemsOpen] = useState(false);
  const hideRef = useRef<HTMLDivElement>(null);
  const menuAnchor = useRef<HTMLElement | null>(null);
  const press = useRef<{ target: Target; x: number; y: number; moved: boolean; id: number } | null>(null);
  const swallowClick = useRef(false);

  // Handles follow the layout: measured every frame while the mode is on, set only when something moved.
  useEffect(() => {
    let frame = 0;
    let last: Target[] = [];
    const tick = () => {
      const next = measure(useWorkspace.getState().document);
      if (!sameTargets(next, last)) {
        last = next;
        setTargets(next);
      }
      const main = document.querySelector("main");
      if (main) setTop(Math.round(main.getBoundingClientRect().top) + 8);
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, []);

  // Escape leaves Customise mode (or ends a drag), before the global handler clears the selection.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || menu || confirmReset || document.querySelector("[aria-modal='true']")) return;
      e.preventDefault();
      e.stopPropagation();
      if (press.current) {
        press.current = null;
        setDrag(null);
        return;
      }
      setCustomising(false);
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [menu, confirmReset, setCustomising]);

  const write = useCallback(
    (path: (string | number)[], value: unknown) => {
      void useWorkspace.getState().setValue(path, value, { scope: useWorkspace.getState().scope === "project" && files.project ? "project" : "user" });
    },
    [files.project],
  );

  // ------------------------------------------------------------------ moves
  const toPanel = useCallback((id: string, target: PanelTarget) => write(["layout"], movePanel(useWorkspace.getState().document.layout, id, target)), [write]);
  const toCorner = useCallback((id: string, corner: Corner | "hidden", index?: number) => write(["islands"], moveIsland(useWorkspace.getState().document.islands, id, corner, index)), [write]);
  const toBar = useCallback(
    (id: string, target: { index: number } | "hide" | "show") => write(["topbar", "items"], moveTopbarItem(useWorkspace.getState().document.topbar.items, id, target)),
    [write],
  );

  const drop = (state: DragState) => {
    const { target, zone, x, y } = state;
    if (!zone) return;
    if (zone.kind === "hide") {
      if (target.kind === "panel") toPanel(target.id, { kind: "hidden" });
      else if (target.kind === "island") toCorner(target.id, "hidden");
      else toBar(target.id, "hide");
      return;
    }
    if (target.kind === "panel") {
      if (zone.kind === "slot") {
        const peers = targets.filter((t) => t.kind === "panel" && t.place === zone.slot && t.id !== target.id);
        const index = peers.filter((t) => (zone.slot === "bottom" ? t.box.left + t.box.width / 2 < x : t.box.top + t.box.height / 2 < y)).length;
        toPanel(target.id, { kind: "slot", slot: zone.slot, index });
      } else if (zone.kind === "float") {
        const main = boxOf(document.querySelector("main"));
        if (!main) return;
        const width = target.place === "float" ? target.box.width : 320;
        const left = x - main.left - width / 2;
        const top = y - main.top - 16;
        const right = main.width - (left + width);
        const anchor: Corner = `${top + 120 < main.height / 2 ? "t" : "b"}${left + width / 2 < main.width / 2 ? "l" : "r"}` as Corner;
        const ax = anchor[1] === "l" ? left : right;
        const ay = anchor[0] === "t" ? top : main.height - (y - main.top) - 16;
        toPanel(target.id, { kind: "float", anchor, x: Math.max(0, ax), y: Math.max(0, ay) });
      }
    } else if (target.kind === "island" && zone.kind === "corner") {
      const peers = targets.filter((t) => t.kind === "island" && t.place === zone.corner && t.id !== target.id);
      toCorner(target.id, zone.corner, peers.filter((t) => t.box.left + t.box.width / 2 < x).length);
    } else if (target.kind === "topbar" && zone.kind === "bar") {
      toBar(target.id, { index: barIndex(useWorkspace.getState().document.topbar.items, targets, x, hosted) });
    }
  };

  // ------------------------------------------------------------------ pointer
  const onPointerDown = (e: ReactPointerEvent<HTMLButtonElement>, target: Target) => {
    if (e.button !== 0) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    press.current = { target, x: e.clientX, y: e.clientY, moved: false, id: e.pointerId };
  };
  const onPointerMove = (e: ReactPointerEvent<HTMLButtonElement>) => {
    const p = press.current;
    if (!p || p.id !== e.pointerId) return;
    if (!p.moved && Math.hypot(e.clientX - p.x, e.clientY - p.y) < DRAG_PX) return;
    if (!p.moved) {
      p.moved = true;
      setMenu(null);
    }
    const zones = dragRef.current?.zones ?? zonesFor(p.target.kind, boxOf(hideRef.current));
    const zone = zones.find((z) => z.kind === "hide" && inside(z.box, e.clientX, e.clientY)) ?? zones.find((z) => z.kind !== "hide" && inside(z.box, e.clientX, e.clientY)) ?? null;
    setDrag({ target: p.target, x: e.clientX, y: e.clientY, zone, zones });
  };
  const onPointerUp = (e: ReactPointerEvent<HTMLButtonElement>) => {
    const p = press.current;
    if (!p || p.id !== e.pointerId) return;
    press.current = null;
    if (p.moved) {
      swallowClick.current = true;
      const last = dragRef.current;
      setDrag(null);
      if (last) drop(last);
    }
  };
  const onClickHandle = (e: ReactMouseEvent<HTMLButtonElement>, target: Target) => {
    if (swallowClick.current) {
      swallowClick.current = false;
      return;
    }
    menuAnchor.current = e.currentTarget;
    setMenu((m) => (m && "box" in m.target && m.target.id === target.id ? null : { target, anchor: e.currentTarget }));
  };

  // ------------------------------------------------------------------ menus
  const menuItems = useMemo((): MenuItemSpec[] => (menu ? itemsFor(menu.target, doc, hosted, { toPanel, toCorner, toBar, write }) : []), [menu, doc, hosted, toPanel, toCorner, toBar, write]);

  const hidden = useMemo(() => hiddenEntries(doc, hosted), [doc, hosted]);
  const invalid = warnings.filter((w) => w.kind === "invalid");

  return createPortal(
    <div className="cz-layer" data-dragging={drag ? "true" : undefined}>
      <div className="cz-toolbar glass" role="toolbar" aria-label="Customise the workspace" style={{ top }}>
        <div className="cz-row">
          <Tooltip label="Drag a handle to move it; click it, or press Enter on it, for its menu" side="bottom" describe={false}>
            <span className="cz-title" tabIndex={0}>
              <LayoutGrid size={15} aria-hidden="true" style={{ color: "var(--accent-ink)" }} />
              Customise
            </span>
          </Tooltip>
          <Tooltip label={files.project ? "Where the changes go" : "Save the project to a folder to give it its own arrangement"} side="bottom" describe={false}>
            <span className="cz-scope">
              <Segmented<Scope>
                label="Apply changes to"
                value={scope === "project" && files.project ? "project" : "user"}
                onChange={(next) => (next === "project" && !files.project ? useStore.getState().pushToast("info", "Save the project to a folder first: its arrangement lives beside project.json.") : setScope(next))}
                options={[
                  { value: "project", label: "This project" },
                  { value: "user", label: "All projects" },
                ]}
              />
            </span>
          </Tooltip>
          <div ref={hideRef} className="cz-hide" data-active={drag?.zone?.kind === "hide" ? "true" : undefined} aria-hidden="true">
            <EyeOff size={13} /> Drop here to hide
          </div>
          <Button variant="ghost" className="ws-small" onClick={() => setConfirmReset(true)}>
            <RotateCcw size={13} /> Reset workspace…
          </Button>
          <Button variant="primary" className="!h-7 text-[12.5px]" onClick={() => setCustomising(false)}>
            <Check size={13} /> Done
          </Button>
        </div>
        {(hidden.length > 0 || invalid.length > 0) && (
          <div className="cz-row cz-row-2">
            {hidden.length > 0 && (
              <div className="cz-hidden" role="group" aria-label="Hidden items">
                <span className="muted">Hidden:</span>
                {hidden.map((h) => (
                  <button
                    key={`${h.kind}:${h.id}`}
                    type="button"
                    className="cz-chip no-drag"
                    aria-haspopup="menu"
                    onClick={(e) => {
                      menuAnchor.current = e.currentTarget;
                      setMenu({ target: h, anchor: e.currentTarget });
                    }}
                  >
                    {h.title}
                    <ChevronDown size={12} aria-hidden="true" />
                  </button>
                ))}
              </div>
            )}
            {invalid.length > 0 && (
              <button type="button" className="cz-problems no-drag" aria-expanded={problemsOpen} onClick={() => setProblemsOpen((o) => !o)}>
                <TriangleAlert size={13} aria-hidden="true" />
                {invalid.length} value{invalid.length === 1 ? "" : "s"} in the workspace files left out
                {problemsOpen ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
              </button>
            )}
          </div>
        )}
        {problemsOpen && invalid.length > 0 && (
          <ul className="cz-problem-list" aria-label="Workspace problems">
            {invalid.map((w, i) => (
              <li key={`${w.path}-${i}`}>
                <span className="mono">{w.path || "(document)"}</span> <span className="muted">({w.scope})</span> {w.message}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="cz-handles" role="group" aria-label="Arrangement handles">
        {targets.map((t) => (
          <Handle
            key={`${t.kind}:${t.id}`}
            target={t}
            dragging={drag?.target.id === t.id && drag.target.kind === t.kind}
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={onPointerUp}
            onClick={onClickHandle}
          />
        ))}
      </div>

      {drag && (
        <>
          {drag.zones
            .filter((z) => z.kind !== "hide")
            .map((z, i) => (
              <div key={i} className="cz-zone" data-active={drag.zone === z ? "true" : undefined} style={{ left: z.box.left, top: z.box.top, width: z.box.width, height: z.box.height }}>
                <span className="cz-zone-label">{z.label}</span>
              </div>
            ))}
          {drag.zone?.kind === "bar" && <BarCaret items={doc.topbar.items} targets={targets} x={drag.x} hosted={hosted} dragged={drag.target.id} />}
          <div className="cz-ghost glass" style={{ transform: `translate(${Math.round(drag.x + 12)}px, ${Math.round(drag.y + 12)}px)` }}>
            <GripVertical size={13} aria-hidden="true" />
            {drag.target.title}
            {drag.zone && <span className="muted">→ {drag.zone.label}</span>}
          </div>
        </>
      )}

      <Menu open={menu !== null && menuItems.length > 0} anchor={menuAnchor as RefObject<HTMLElement | null>} onClose={() => setMenu(null)} items={menuItems} label={menu ? `${menu.target.title} arrangement` : "Arrangement"} />
      <ResetDialog open={confirmReset} hasProject={files.project?.exists === true} onClose={() => setConfirmReset(false)} />
    </div>,
    document.body,
  );
}

interface HandleProps {
  target: Target;
  dragging: boolean;
  onPointerDown: (e: ReactPointerEvent<HTMLButtonElement>, t: Target) => void;
  onPointerMove: (e: ReactPointerEvent<HTMLButtonElement>) => void;
  onPointerUp: (e: ReactPointerEvent<HTMLButtonElement>) => void;
  onClick: (e: ReactMouseEvent<HTMLButtonElement>, t: Target) => void;
}

/** An outline around the target and a grip chip on it: drag to move, click or Enter for the menu. */
function Handle({ target, dragging, onPointerDown, onPointerMove, onPointerUp, onClick }: HandleProps) {
  const { box } = target;
  const chipOnTop = target.kind !== "topbar";
  return (
    <div className="cz-target" data-kind={target.kind} data-dragging={dragging ? "true" : undefined} style={{ left: box.left, top: box.top, width: box.width, height: box.height }}>
      <button
        type="button"
        className={`cz-handle no-drag ${chipOnTop ? "chip-top" : "cover"}`}
        aria-label={`Move ${target.title}`}
        aria-haspopup="menu"
        title={chipOnTop ? undefined : target.title}
        onPointerDown={(e) => onPointerDown(e, target)}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onClick={(e) => onClick(e, target)}
        onContextMenu={(e) => {
          e.preventDefault();
          onClick(e, target);
        }}
      >
        {chipOnTop && (
          <>
            <GripVertical size={13} aria-hidden="true" />
            <span>{target.title}</span>
            <ChevronDown size={12} aria-hidden="true" />
          </>
        )}
      </button>
    </div>
  );
}

/** Where a top-bar drop lands: before the first shown item (or the stage split) right of the pointer. */
function barIndex(items: readonly TopbarItemSpec[], targets: readonly Target[], x: number, hosted: boolean): number {
  const bar = boxOf(document.querySelector(".topbar"));
  const centre = bar ? bar.left + bar.width / 2 : x;
  const shown = shownItem(hosted);
  for (let i = 0; i < items.length; i++) {
    const item = items[i];
    if (item.id === "stages") {
      const t = targets.find((tt) => tt.kind === "topbar" && tt.id === "stages");
      const mid = t ? t.box.left + t.box.width / 2 : centre;
      if (mid > x) return i;
      continue;
    }
    if (!shown(item)) continue;
    const t = targets.find((tt) => tt.kind === "topbar" && tt.id === item.id);
    if (t && t.box.left + t.box.width / 2 > x) return i;
  }
  return items.length;
}

/** The accent caret where a top-bar item would land. */
function BarCaret({ items, targets, x, hosted, dragged }: { items: readonly TopbarItemSpec[]; targets: readonly Target[]; x: number; hosted: boolean; dragged: string }) {
  const index = barIndex(items, targets, x, hosted);
  const bar = boxOf(document.querySelector(".topbar"));
  if (!bar) return null;
  const after = items.slice(index).map((i) => targets.find((t) => t.kind === "topbar" && t.id === i.id && i.id !== dragged)).find(Boolean);
  const before = items
    .slice(0, index)
    .map((i) => targets.find((t) => t.kind === "topbar" && t.id === i.id && i.id !== dragged))
    .filter(Boolean)
    .pop();
  const left = after ? after.box.left - 3 : before ? before.box.left + before.box.width + 1 : x;
  return <div className="cz-caret" style={{ left, top: bar.top + 8, height: bar.height - 16 }} />;
}

// ---------------------------------------------------------------------------------------------- menus
interface HiddenEntry {
  kind: Kind;
  id: string;
  title: string;
}

/**
 * What the toolbar offers to bring back: hidden panels, islands and top-bar items, and registered
 * panels and islands the document does not place at all (one a plug-in or a newer version added).
 */
function hiddenEntries(doc: WorkspaceDocument, hosted: boolean): HiddenEntry[] {
  const out: HiddenEntry[] = [];
  for (const def of PANELS.values()) {
    const place = placeOf(doc.layout, def.id);
    if (place === "hidden" || place === null) out.push({ kind: "panel", id: def.id, title: def.title });
  }
  for (const def of ISLANDS.values()) {
    const placed = CORNERS.some((c) => doc.islands[c].includes(def.id));
    if (!placed) out.push({ kind: "island", id: def.id, title: def.title });
  }
  for (const item of doc.topbar.items) {
    const def = TOPBAR_ITEMS.get(item.id);
    // The stage control with the style "hidden" shows nowhere either; its chip offers the styles.
    const unseen = item.visible === false || (item.id === "stages" && doc.topbar.stages.style === "hidden");
    if (def && unseen && (item.id !== "window" || hosted)) out.push({ kind: "topbar", id: item.id, title: def.title });
  }
  return out;
}

interface Moves {
  toPanel(id: string, target: PanelTarget): void;
  toCorner(id: string, corner: Corner | "hidden", index?: number): void;
  toBar(id: string, target: { index: number } | "hide" | "show"): void;
  write(path: (string | number)[], value: unknown): void;
}

/** The menu of a handle or a hidden chip: Move to…, then the options of the place it is in. */
function itemsFor(target: Target | HiddenEntry, doc: WorkspaceDocument, hosted: boolean, moves: Moves): MenuItemSpec[] {
  const items: MenuItemSpec[] = [];
  if (target.kind === "panel") {
    const place = placeOf(doc.layout, target.id) ?? "hidden";
    items.push({ type: "heading", id: "h", label: `Move ${target.title} to` });
    for (const slot of ["left", "right", "bottom"] as const) {
      const end = doc.layout[slot].panels.filter((p) => p.id !== target.id).length;
      items.push({ id: slot, label: SIDE_LABEL[slot], radio: true, checked: place === slot, onSelect: () => moves.toPanel(target.id, { kind: "slot", slot, index: end }) });
    }
    items.push({
      id: "float",
      label: SIDE_LABEL.float,
      radio: true,
      checked: place === "float",
      onSelect: () => moves.toPanel(target.id, { kind: "float", anchor: "tr", x: 16, y: 16 + 32 * doc.layout.float.panels.filter((p) => p.id !== target.id).length }),
    });
    items.push({ id: "hidden", label: SIDE_LABEL.hidden, radio: true, checked: place === "hidden", onSelect: () => moves.toPanel(target.id, { kind: "hidden" }) });
    if (place === "left" || place === "right" || place === "bottom") {
      const slot = doc.layout[place];
      const many = slotPanels(slot).length > 1;
      items.push({ type: "separator", id: "s1" });
      if (many) {
        items.push(
          { type: "heading", id: "mode", label: `${SIDE_LABEL[place]} shows its panels` },
          { id: "tabs", label: "As tabs", radio: true, checked: slot.mode === "tabs", onSelect: () => moves.write(["layout"], setSlotMode(doc.layout, place, "tabs")) },
          { id: "split", label: place === "bottom" ? "Side by side" : "Stacked", radio: true, checked: slot.mode === "split", onSelect: () => moves.write(["layout"], setSlotMode(doc.layout, place, "split")) },
        );
      }
      if (place !== "bottom") {
        items.push({ id: "fold", label: slot.collapsed ? `Unfold the ${place} side` : `Fold the ${place} side to icons`, onSelect: () => moves.write(["layout", place, "collapsed"], !slot.collapsed) });
        const index = slot.panels.findIndex((p) => p.id === target.id);
        if (slot.mode === "split" && many && index >= 0) {
          const folded = slot.panels[index].collapsed === true;
          items.push({ id: "fold-cell", label: folded ? `Unfold ${target.title}` : `Fold ${target.title} to its header`, onSelect: () => moves.write(["layout", place, "panels", index, "collapsed"], !folded) });
        }
      }
    }
    if (place === "float") {
      const index = doc.layout.float.panels.findIndex((p) => p.id === target.id);
      const folded = doc.layout.float.panels[index]?.collapsed === true;
      if (index >= 0) items.push({ type: "separator", id: "s2" }, { id: "fold-card", label: folded ? "Unfold the card" : "Fold the card to its header", onSelect: () => moves.write(["layout", "float", "panels", index, "collapsed"], !folded) });
    }
  } else if (target.kind === "island") {
    const corner = (CORNERS.find((c) => doc.islands[c].includes(target.id)) ?? "hidden") as Corner | "hidden";
    items.push({ type: "heading", id: "h", label: `Move ${target.title} to` });
    for (const c of CORNERS) items.push({ id: c, label: CORNER_LABEL[c], radio: true, checked: corner === c, onSelect: () => moves.toCorner(target.id, c) });
    items.push({ id: "hidden", label: "Hidden", radio: true, checked: corner === "hidden", onSelect: () => moves.toCorner(target.id, "hidden") });
  } else {
    const list = doc.topbar.items;
    const item = list.find((i) => i.id === target.id);
    const shown = shownItem(hosted);
    const visible = list.filter(shown);
    const at = visible.findIndex((i) => i.id === target.id);
    items.push({ type: "heading", id: "h", label: target.title });
    if (item?.visible === false) {
      items.push({ id: "show", label: "Show in the top bar", onSelect: () => moves.toBar(target.id, "show") });
    } else {
      items.push(
        { id: "left", label: "Move left", disabled: at <= 0, onSelect: () => moves.write(["topbar", "items"], nudgeTopbarItem(list, target.id, -1, shown)) },
        { id: "right", label: "Move right", disabled: at < 0 || at >= visible.length - 1, onSelect: () => moves.write(["topbar", "items"], nudgeTopbarItem(list, target.id, 1, shown)) },
        { id: "hide", label: "Hide", onSelect: () => moves.toBar(target.id, "hide") },
      );
    }
    if (target.id === "stages") {
      const style = doc.topbar.stages.style;
      items.push(
        { type: "separator", id: "s" },
        { type: "heading", id: "style", label: "Stage control" },
        { id: "chips", label: "Chips in the top bar", radio: true, checked: style === "chips", onSelect: () => moves.write(["topbar", "stages", "style"], "chips") },
        { id: "ribbon", label: "Ribbon under the top bar", radio: true, checked: style === "ribbon", onSelect: () => moves.write(["topbar", "stages", "style"], "ribbon") },
        { id: "none", label: "None (keys 1–6 only)", radio: true, checked: style === "hidden", onSelect: () => moves.write(["topbar", "stages", "style"], "hidden") },
      );
    }
  }
  return items;
}

/** The confirmation of "Reset workspace": here in Customise mode's toolbar, and in the command palette. */
export function ResetDialog({ open, hasProject, onClose }: { open: boolean; hasProject: boolean; onClose: () => void }) {
  const resetScope = useWorkspace((s) => s.resetScope);
  const run = async (scope: Scope) => {
    onClose();
    if (await resetScope(scope)) useStore.getState().pushToast("ok", scope === "user" ? "The workspace is back to the Stokes defaults." : "This project's arrangement was removed.");
  };
  return (
    <Dialog
      open={open}
      title="Reset the workspace?"
      onClose={onClose}
      actions={
        <>
          <button type="button" className="btn no-drag" onClick={onClose}>
            Cancel
          </button>
          {hasProject && (
            <button type="button" className="btn no-drag" onClick={() => void run("project")}>
              Reset this project
            </button>
          )}
          <button type="button" className="btn primary no-drag" onClick={() => void run("user")}>
            Reset
          </button>
        </>
      }
    >
      <p className="text-[12.5px]">
        Your arrangement, theme, shortcuts, viewport defaults, mouse scheme and default import unit go back to the Stokes defaults for all projects. Your saved layout presets stay. A project with its own arrangement keeps it until you reset that project too.
      </p>
    </Dialog>
  );
}
