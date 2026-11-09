import {
  cloneElement,
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type FocusEvent,
  type MouseEvent,
  type PointerEvent,
  type ReactElement,
  type RefObject,
  type Ref,
} from "react";
import { createPortal } from "react-dom";

export type TooltipSide = "top" | "bottom" | "left" | "right";

/** What the wrapped element has to accept. Native elements and `Button` all do. */
interface TriggerProps {
  ref?: Ref<HTMLElement>;
  onMouseEnter?: (e: MouseEvent<HTMLElement>) => void;
  onMouseLeave?: (e: MouseEvent<HTMLElement>) => void;
  onFocus?: (e: FocusEvent<HTMLElement>) => void;
  onBlur?: (e: FocusEvent<HTMLElement>) => void;
  onPointerDown?: (e: PointerEvent<HTMLElement>) => void;
  "aria-describedby"?: string;
}

const SHOW_DELAY = 450;
/** After a tooltip closed, the next one opens at once while the pointer is still travelling. */
const WARM_WINDOW = 350;
let lastClosedAt = 0;

interface TooltipProps {
  label: string;
  /** Keyboard shortcut, shown as a key cap. */
  hint?: string;
  side?: TooltipSide;
  /** Point `aria-describedby` at the bubble. Off for buttons whose aria-label already says it. */
  describe?: boolean;
  disabled?: boolean;
  children: ReactElement<TriggerProps>;
}

/**
 * Small hover and keyboard-focus label with an optional shortcut. No wrapper element: the
 * child gets the listeners and a merged ref. The bubble renders in a portal, so panel
 * `overflow: hidden` cannot clip it, and flips to the other side when it would leave the window.
 */
export function Tooltip({ label, hint, side = "top", describe = true, disabled = false, children }: TooltipProps) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const openRef = useRef(false);
  const anchor = useRef<HTMLElement | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const childProps = children.props;
  const childRef = childProps.ref;

  const setRef = useCallback(
    (node: HTMLElement | null) => {
      anchor.current = node;
      if (typeof childRef === "function") childRef(node);
      else if (childRef) (childRef as RefObject<HTMLElement | null>).current = node;
    },
    [childRef],
  );

  const show = useCallback(() => {
    window.clearTimeout(timer.current);
    const warm = performance.now() - lastClosedAt < WARM_WINDOW;
    timer.current = window.setTimeout(() => setOpen(true), warm ? 0 : SHOW_DELAY);
  }, []);
  const hide = useCallback(() => {
    window.clearTimeout(timer.current);
    if (openRef.current) lastClosedAt = performance.now();
    setOpen(false);
  }, []);

  useEffect(() => {
    openRef.current = open;
  }, [open]);
  useEffect(() => () => window.clearTimeout(timer.current), []);

  // Dismissible without moving the pointer (Escape), and gone as soon as anything else happens.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") hide();
    };
    window.addEventListener("keydown", onKey, true);
    window.addEventListener("resize", hide);
    window.addEventListener("blur", hide);
    return () => {
      window.removeEventListener("keydown", onKey, true);
      window.removeEventListener("resize", hide);
      window.removeEventListener("blur", hide);
    };
  }, [open, hide]);

  const wired = {
    ref: setRef,
    onMouseEnter: (e: MouseEvent<HTMLElement>) => {
      childProps.onMouseEnter?.(e);
      show();
    },
    onMouseLeave: (e: MouseEvent<HTMLElement>) => {
      childProps.onMouseLeave?.(e);
      hide();
    },
    onFocus: (e: FocusEvent<HTMLElement>) => {
      childProps.onFocus?.(e);
      // keyboard focus only: a mouse click already shows the label on hover. `e.target` is the element that took
      // focus, so a label on a row (a div around a slider or chips) is reached from the keyboard too; for a
      // trigger that is itself focusable it is the same element as `currentTarget`.
      if ((e.target as HTMLElement).matches(":focus-visible")) show();
    },
    onBlur: (e: FocusEvent<HTMLElement>) => {
      childProps.onBlur?.(e);
      hide();
    },
    onPointerDown: (e: PointerEvent<HTMLElement>) => {
      childProps.onPointerDown?.(e);
      hide();
    },
    "aria-describedby": open && describe ? id : childProps["aria-describedby"],
  };

  // The same element tree whether or not the tooltip is disabled, so toggling it never remounts the child.
  return (
    <>
      {disabled ? children : cloneElement(children, wired)}
      {!disabled && open && anchor.current && <Bubble id={id} anchor={anchor.current} label={label} hint={hint} side={side} />}
    </>
  );
}

function Bubble({ id, anchor, label, hint, side }: { id: string; anchor: HTMLElement; label: string; hint?: string; side: TooltipSide }) {
  const ref = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const a = anchor.getBoundingClientRect();
    const w = el.offsetWidth;
    const h = el.offsetHeight;
    const gap = 8;
    const pad = 6;
    const vw = document.documentElement.clientWidth;
    const vh = document.documentElement.clientHeight;
    let s = side;
    if (s === "top" && a.top - gap - h < pad) s = "bottom";
    else if (s === "bottom" && a.bottom + gap + h > vh - pad) s = "top";
    else if (s === "left" && a.left - gap - w < pad) s = "right";
    else if (s === "right" && a.right + gap + w > vw - pad) s = "left";
    let x: number;
    let y: number;
    if (s === "top" || s === "bottom") {
      x = a.left + a.width / 2 - w / 2;
      y = s === "top" ? a.top - gap - h : a.bottom + gap;
    } else {
      y = a.top + a.height / 2 - h / 2;
      x = s === "left" ? a.left - gap - w : a.right + gap;
    }
    x = Math.min(Math.max(x, pad), Math.max(pad, vw - w - pad));
    y = Math.min(Math.max(y, pad), Math.max(pad, vh - h - pad));
    el.style.transform = `translate(${Math.round(x)}px, ${Math.round(y)}px)`;
    el.dataset.side = s;
    el.dataset.placed = "true";
  }, [anchor, label, hint, side]);
  return createPortal(
    <div ref={ref} id={id} role="tooltip" className="tooltip">
      <span>{label}</span>
      {hint && <span className="kbd">{hint}</span>}
    </div>,
    document.body,
  );
}
