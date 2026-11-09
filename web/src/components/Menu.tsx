import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type FocusEvent, type KeyboardEvent, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";
import { Check, ChevronRight } from "lucide-react";
import { Tooltip } from "./Tooltip";
import { clamp, placeAt } from "./menuPlacement";

export type Placement = "top-start" | "top-end" | "bottom-start" | "bottom-end";
export type CloseReason = "escape" | "outside" | "select" | "tab";

interface PopoverProps {
  open: boolean;
  /** The element that opened it: kept out of outside-click detection and given the focus back. Not needed with `at`. */
  anchor?: RefObject<HTMLElement | null>;
  /**
   * A point in viewport pixels, for a menu that opens at the pointer. The surface's top-left sits
   * there, flipped to the other side of the point at the right and bottom edges. When both `at` and
   * `anchor` are given, `at` places it and `anchor` is still kept out of outside-click detection.
   */
  at?: { x: number; y: number } | null;
  /** Receives the focus on Escape, Tab and select when there is no anchor. Without it, the element that had the focus when the popover opened gets it back. */
  returnFocus?: RefObject<HTMLElement | null>;
  onClose: (reason: CloseReason) => void;
  placement?: Placement;
  offset?: number;
  role: "menu" | "dialog";
  /** Accessible name. */
  label: string;
  /** Selector of what to focus on open. */
  focusSelector?: string;
  className?: string;
  /** Changes when the content changes size and the surface has to be placed again (a nested list). */
  replaceKey?: string;
  onKeyDown?: (e: KeyboardEvent<HTMLDivElement>) => void;
  children: ReactNode;
}

/**
 * Where the focus goes when a popover closes by key or choice: the anchor, else `returnFocus`, else
 * the element that had it when the popover opened (captured on open, before the popover takes it).
 */
function useFocusReturn(open: boolean, anchor: RefObject<HTMLElement | null> | undefined, returnFocus: RefObject<HTMLElement | null> | undefined): () => void {
  const opener = useRef<HTMLElement | null>(null);
  useLayoutEffect(() => {
    if (!open) return;
    const active = document.activeElement;
    opener.current = active instanceof HTMLElement && active !== document.body ? active : null;
    return () => {
      opener.current = null;
    };
  }, [open]);
  return useCallback(() => {
    const target = anchor?.current ?? returnFocus?.current ?? opener.current;
    if (target?.isConnected) target.focus({ preventScroll: true });
  }, [anchor, returnFocus]);
}

/**
 * Floating surface next to an anchor, or at a point. Rendered in a portal (panels clip their overflow),
 * flips to the roomier side, stays inside the window, closes on Escape, an outside press, a scroll or
 * wheel outside it and the window losing focus (and, at a point, on a resize), and returns the focus.
 * Escape is consumed here so the global handler does not also clear the selection.
 */
export function Popover({ open, anchor, at = null, returnFocus, onClose, placement = "bottom-start", offset = 6, role, label, focusSelector, className = "", replaceKey, onKeyDown, children }: PopoverProps) {
  const ref = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  useLayoutEffect(() => {
    closeRef.current = onClose;
  });
  const restoreFocus = useFocusReturn(open, anchor, returnFocus);
  const atX = at?.x;
  const atY = at?.y;
  const pointed = atX !== undefined && atY !== undefined;

  useLayoutEffect(() => {
    if (!open) return;
    const el = ref.current;
    const a = anchor?.current ?? null;
    if (!el || (!pointed && !a)) return;
    const place = () => {
      const w = el.offsetWidth;
      const h = el.offsetHeight;
      const vw = document.documentElement.clientWidth;
      const vh = document.documentElement.clientHeight;
      const pad = 8;
      if (atX !== undefined && atY !== undefined) {
        const p = placeAt({ x: atX, y: atY }, { w, h }, { w: vw, h: vh }, pad);
        el.style.left = `${Math.round(p.x)}px`;
        el.style.top = `${Math.round(p.y)}px`;
        el.dataset.side = p.y < atY ? "top" : "bottom";
        el.dataset.placed = "true";
        return;
      }
      if (!a) return;
      const r = a.getBoundingClientRect();
      let side: "top" | "bottom" = placement.startsWith("top") ? "top" : "bottom";
      const above = r.top - pad;
      const below = vh - r.bottom - pad;
      if (side === "top" && h + offset > above && below > above) side = "bottom";
      else if (side === "bottom" && h + offset > below && above > below) side = "top";
      const x = placement.endsWith("end") ? r.right - w : r.left;
      const y = side === "top" ? r.top - offset - h : r.bottom + offset;
      el.style.left = `${Math.round(clamp(x, pad, Math.max(pad, vw - w - pad)))}px`;
      el.style.top = `${Math.round(clamp(y, pad, Math.max(pad, vh - h - pad)))}px`;
      el.dataset.side = side;
      el.dataset.placed = "true";
    };
    place();
    // A point menu closes on a resize (below); an anchored one follows its anchor.
    if (pointed) return;
    window.addEventListener("resize", place);
    return () => window.removeEventListener("resize", place);
  }, [open, anchor, pointed, atX, atY, placement, offset, replaceKey]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      const target = e.target as Node | null;
      if (!target || ref.current?.contains(target) || anchor?.current?.contains(target)) return;
      closeRef.current("outside");
    };
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.preventDefault();
      e.stopPropagation();
      restoreFocus();
      closeRef.current("escape");
    };
    // Anything that moves the content under the surface, or the window under the user, dismisses it. A
    // scroll does not bubble (hence the capture) and a wheel zoom over the canvas fires no scroll at all.
    // A scroll in the first moments is the page settling after the opening gesture (a right-click selects,
    // and the tree scrolls the selection into view), not the user moving away: only a wheel counts then.
    const openedAt = performance.now();
    const onMoved = (e: Event) => {
      const target = e.target;
      if (target instanceof Node && ref.current?.contains(target)) return;
      if (e.type === "scroll" && performance.now() - openedAt < 300) return;
      closeRef.current("outside");
    };
    const onWindowBlur = () => closeRef.current("outside");
    const onResize = () => closeRef.current("outside");
    document.addEventListener("pointerdown", onDown, true);
    document.addEventListener("keydown", onKey, true);
    document.addEventListener("scroll", onMoved, true);
    document.addEventListener("wheel", onMoved, { capture: true, passive: true });
    window.addEventListener("blur", onWindowBlur);
    if (pointed) window.addEventListener("resize", onResize);
    return () => {
      document.removeEventListener("pointerdown", onDown, true);
      document.removeEventListener("keydown", onKey, true);
      document.removeEventListener("scroll", onMoved, true);
      document.removeEventListener("wheel", onMoved, true);
      window.removeEventListener("blur", onWindowBlur);
      window.removeEventListener("resize", onResize);
    };
  }, [open, anchor, pointed, restoreFocus]);

  useEffect(() => {
    if (!open || !focusSelector) return;
    const target = ref.current?.querySelector<HTMLElement>(focusSelector);
    if (!target) return;
    target.focus({ preventScroll: true });
    if (document.activeElement === target) return;
    // The surface starts `visibility: hidden` and fades in, so on its first frame it cannot take the
    // focus yet (a hidden element refuses it). Try again for a few frames, until it can.
    let frame = 0;
    let tries = 0;
    const retry = () => {
      target.focus({ preventScroll: true });
      if (document.activeElement !== target && ++tries < 8) frame = requestAnimationFrame(retry);
    };
    frame = requestAnimationFrame(retry);
    return () => cancelAnimationFrame(frame);
  }, [open, focusSelector]);

  if (!open) return null;

  const onBlur = (e: FocusEvent<HTMLDivElement>) => {
    const next = e.relatedTarget as Node | null;
    // focus moved to another control: close. (null means the window lost focus: the window blur above decides)
    if (next && !e.currentTarget.contains(next) && !anchor?.current?.contains(next)) closeRef.current("outside");
  };

  return createPortal(
    <div ref={ref} role={role} aria-label={label} className={`popover ${className}`} onKeyDown={onKeyDown} onBlur={onBlur}>
      {children}
    </div>,
    document.body,
  );
}

export interface MenuEntry {
  type?: "item";
  id: string;
  label: string;
  icon?: ReactNode;
  /** Shortcut shown at the right: for chords only, never a sentence. */
  hint?: string;
  /**
   * What the entry does, in a tooltip on hover. For an entry whose meaning does not fit one word,
   * such as the mouse schemes, where the label names the tool and only the description says which
   * button rotates. On a disabled entry it is the reason, and a screen reader gets it too.
   */
  description?: string;
  /** Present on radio and check items; drives aria-checked and the check mark. */
  checked?: boolean;
  /** One of a group of exclusive choices (menuitemradio). */
  radio?: boolean;
  /** Greyed and inert, but still reachable by the arrow keys so its reason can be read. */
  disabled?: boolean;
  /** A nested list. Enter, Space, click or ArrowRight open it in place; `onSelect` is then ignored. */
  items?: MenuItemSpec[];
  /**
   * Choosing the row closes this menu and opens another one (a chained menu): the row says so with the
   * chevron and `aria-haspopup`, and `onSelect` runs as for any row. Not needed with `items`.
   */
  popup?: boolean;
  /**
   * Choosing the row leaves this menu OPEN.
   *
   * **Claude's GUI review item 3**: the Display menu closed after every switch, so turning off three
   * things meant reopening it three times. A row that changes a setting beside it - a check mark that
   * stays visible - is a row whose effect a reader wants to see, and re-summoning the menu to see it is
   * the opposite of that. Escape and an outside click still close it, and a menu whose rows all keep it
   * open is the only kind that should ask for this.
   */
  keepOpen?: boolean;
  onSelect?: () => void;
}
export type MenuItemSpec = MenuEntry | { type: "separator"; id: string } | { type: "heading"; id: string; label: string };

function isEntry(item: MenuItemSpec): item is MenuEntry {
  return item.type === undefined || item.type === "item";
}

interface MenuProps {
  open: boolean;
  anchor?: RefObject<HTMLElement | null>;
  /** Open at a point instead of under `anchor` (see `Popover`). */
  at?: { x: number; y: number } | null;
  returnFocus?: RefObject<HTMLElement | null>;
  onClose: (reason: CloseReason) => void;
  items: MenuItemSpec[];
  label: string;
  placement?: Placement;
}

const FOCUS_FIRST = "[role^='menuitem'][aria-checked='true'], [role^='menuitem']:not([aria-disabled='true'])";

/** The row a list opens on: a checked one, else the first enabled one, else the first. */
function firstFocusId(level: MenuItemSpec[]): string | undefined {
  const entries = level.filter(isEntry);
  return (entries.find((e) => e.checked && !e.disabled) ?? entries.find((e) => !e.disabled) ?? entries[0])?.id;
}

/** Menu with arrow-key navigation, radio and check items, separators, headings and nested lists. */
export function Menu({ open, anchor, at, returnFocus, onClose, items, label, placement = "bottom-start" }: MenuProps) {
  const uid = useId();
  const restoreFocus = useFocusReturn(open, anchor, returnFocus);
  const rows = useRef(new Map<string, HTMLElement>());
  const pendingFocus = useRef<string | null>(null);
  // The ids of the rows opened to get here: the nested list shown is the one at the end.
  const [path, setPath] = useState<string[]>([]);
  useEffect(() => {
    if (!open) setPath([]);
  }, [open]);

  // Follow the path down; a row that is gone ends it there.
  const trail: MenuEntry[] = [];
  let level = items;
  for (const id of path) {
    const next = level.find((i): i is MenuEntry => isEntry(i) && i.id === id && i.items !== undefined);
    if (!next?.items) break;
    trail.push(next);
    level = next.items;
  }
  const trailKey = trail.map((t) => t.id).join("/");
  const parent = trail[trail.length - 1];

  // After a list changes, put the focus where the user expects it: the first row going in, the row that opened the list coming out.
  useLayoutEffect(() => {
    if (!open) return;
    const id = pendingFocus.current;
    if (id === null) return;
    pendingFocus.current = null;
    rows.current.get(id)?.focus({ preventScroll: true });
  }, [open, trailKey]);

  const enter = (entry: MenuEntry) => {
    pendingFocus.current = entry.items ? (firstFocusId(entry.items) ?? null) : null;
    setPath([...trail.map((t) => t.id), entry.id]);
  };
  const leave = () => {
    const last = trail[trail.length - 1];
    if (!last) return;
    pendingFocus.current = last.id;
    setPath(trail.slice(0, -1).map((t) => t.id));
  };

  const select = (item: MenuEntry) => {
    if (item.disabled) return;
    if (item.items) {
      enter(item);
      return;
    }
    if (item.keepOpen !== true) {
      restoreFocus();
      onClose("select");
    }
    item.onSelect?.();
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    // every row, disabled ones too (the menu pattern): the reason of a disabled row is read by landing on it
    const nodes = Array.from(e.currentTarget.querySelectorAll<HTMLElement>("[role^='menuitem']"));
    if (nodes.length === 0) return;
    const index = nodes.indexOf(document.activeElement as HTMLElement);
    let target: HTMLElement | undefined;
    switch (e.key) {
      case "ArrowDown":
        target = nodes[index < 0 ? 0 : (index + 1) % nodes.length];
        break;
      case "ArrowUp":
        target = nodes[index < 0 ? nodes.length - 1 : (index - 1 + nodes.length) % nodes.length];
        break;
      case "Home":
        target = nodes[0];
        break;
      case "End":
        target = nodes[nodes.length - 1];
        break;
      case "ArrowRight": {
        const entry = level.find((i): i is MenuEntry => isEntry(i) && rows.current.get(i.id) === document.activeElement);
        if (!entry?.items || entry.disabled) return;
        e.preventDefault();
        enter(entry);
        return;
      }
      case "ArrowLeft":
      case "Backspace":
        if (trail.length === 0) return;
        e.preventDefault();
        leave();
        return;
      case "Tab":
        e.preventDefault();
        restoreFocus();
        onClose("tab");
        return;
      default:
        return;
    }
    e.preventDefault();
    target?.focus({ preventScroll: true });
  };

  return (
    <Popover
      open={open}
      anchor={anchor}
      at={at}
      returnFocus={returnFocus}
      onClose={onClose}
      placement={placement}
      role="menu"
      label={parent?.label ?? label}
      className="menu"
      focusSelector={FOCUS_FIRST}
      replaceKey={trailKey}
      onKeyDown={onKeyDown}
    >
      {parent && (
        // visual caption only (the list is named by the popover's label)
        <div key={`parent-${parent.id}`} className="menu-heading" title={parent.label} aria-hidden="true">
          {parent.label}
        </div>
      )}
      {level.map((item) => {
        if (item.type === "separator") return <div key={item.id} role="separator" className="menu-sep" />;
        if (item.type === "heading") {
          return (
            // visual caption only: a menu may own items and separators, not loose text
            <div key={item.id} className="menu-heading" title={item.label} aria-hidden="true">
              {item.label}
            </div>
          );
        }
        const choice = item.radio || item.checked !== undefined;
        // A disabled row's reason has to reach a screen reader without the tooltip, which opens on a delay and not at all on a bare focus.
        const reasonId = item.disabled && item.description !== undefined ? `${uid}-${item.id}-reason` : undefined;
        const row = (
          <button
            key={item.id}
            ref={(el) => {
              if (el) rows.current.set(item.id, el);
              else rows.current.delete(item.id);
            }}
            type="button"
            role={item.radio ? "menuitemradio" : item.checked !== undefined ? "menuitemcheckbox" : "menuitem"}
            aria-checked={choice ? Boolean(item.checked) : undefined}
            aria-disabled={item.disabled || undefined}
            aria-haspopup={item.items || item.popup ? "menu" : undefined}
            aria-label={reasonId ? item.label : undefined}
            aria-describedby={reasonId}
            tabIndex={-1}
            className="menu-item no-drag"
            onClick={() => select(item)}
            onMouseMove={(e) => {
              if (document.activeElement !== e.currentTarget && !item.disabled) e.currentTarget.focus({ preventScroll: true });
            }}
          >
            <span className="menu-icon">{choice ? item.checked && <Check size={14} /> : item.icon}</span>
            <span className="menu-text">{item.label}</span>
            {item.items || item.popup ? (
              <span className="kbd">
                <ChevronRight size={13} />
              </span>
            ) : (
              item.hint && <span className="kbd">{item.hint}</span>
            )}
            {reasonId && (
              <span id={reasonId} className="sr-only">
                {item.description}
              </span>
            )}
          </button>
        );
        // A description that says what the entry does becomes a tooltip: on a long one, such as a
        // mouse scheme's button map, showing it in the row itself would push the label off.
        return item.description === undefined ? (
          row
        ) : (
          <Tooltip key={item.id} label={item.description} side="right" describe={false}>
            {row}
          </Tooltip>
        );
      })}
    </Popover>
  );
}
