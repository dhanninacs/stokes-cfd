import { useEffect, useId, useRef, type KeyboardEvent, type ReactNode } from "react";
import { dialogStack } from "./dialogStack";
import { createPortal } from "react-dom";

interface DialogProps {
  open: boolean;
  title: string;
  onClose: () => void;
  /** Wider than the prompt (440 px), for text areas. Same as `size="wide"`. */
  wide?: boolean;
  /** `settings` is the two-column Settings dialog (760 px, as tall as the window allows). */
  size?: "default" | "wide" | "settings";
  /** Selector of the element that takes the focus on open; the first field otherwise. */
  focus?: string;
  children: ReactNode;
  /** The buttons at the bottom right. */
  actions: ReactNode;
}

/**
 * The open dialogs, oldest first. A dialog can open over another (Export over Settings), and every
 * dialog listens for Escape on `document` in the capture phase: `stopPropagation` does not stop a
 * second listener on the same node, so without this the Escape that closes the top dialog would close
 * the one under it too. Only the top frame reacts to Escape and to a press on its scrim.
 */

const FOCUSABLE = "input, textarea, select, button:not([disabled]), [tabindex]:not([tabindex='-1'])";

/**
 * Modal dialog in the style of PromptDialog: a scrim, Escape and an outside press close it, Tab stays
 * inside, and the focus goes back to where it was. For forms with more than one field (save a layout,
 * import or export JSON).
 */
export function Dialog(props: DialogProps) {
  return props.open ? <Frame {...props} /> : null;
}

function Frame({ title, onClose, wide, size, focus, children, actions }: DialogProps) {
  const ref = useRef<HTMLDivElement>(null);
  const id = useRef(Symbol("dialog")).current;
  /** Pushed once per mounted frame; `useRef` because the effect may run twice for one dialog. */
  const tokenRef = useRef<ReturnType<typeof dialogStack.push> | null>(null);
  const isTop = () => tokenRef.current !== null && dialogStack.isTop(tokenRef.current);
  const kind = size ?? (wide ? "wide" : "default");
  const closeRef = useRef(onClose);
  useEffect(() => {
    closeRef.current = onClose;
  });
  const titleId = useId();

  useEffect(() => {
    /*
      **`dialogStack`, not a bare array** (G-301's H-3). This effect is keyed `[focus, id]`, so a dialog
      whose focus target changes - a form filling in - runs the cleanup and the effect again. With a
      plain array that pushed a SECOND entry for a dialog that never closed, `isTop()` then disagreed
      with the array's end, and **Escape stopped closing any dialog while its scrim kept the window
      unclickable**. `push` is idempotent per token and `remove` takes the token back, so the balance
      cannot leak; `dialogStack.test.ts` fails if the guard is removed.
    */
    const token = dialogStack.push(id);
    tokenRef.current = token;
    const previous = document.activeElement as HTMLElement | null;
    const first = (focus ? ref.current?.querySelector<HTMLElement>(focus) : null) ?? ref.current?.querySelector<HTMLElement>(FOCUSABLE);
    first?.focus();
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (!dialogStack.isTop(token)) return; // a dialog opened over this one takes the key
      e.preventDefault(); // the global handler must not also clear the selection
      e.stopPropagation();
      closeRef.current();
    };
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      if (tokenRef.current !== null) dialogStack.remove(tokenRef.current);
      tokenRef.current = null;
      if (previous?.isConnected) previous.focus({ preventScroll: true });
    };
  }, [focus, id]);

  const trap = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== "Tab") return;
    const nodes = Array.from(e.currentTarget.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((n) => !n.hasAttribute("disabled") && n.offsetParent !== null);
    const first = nodes[0];
    const last = nodes[nodes.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last?.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first?.focus();
    }
  };

  return createPortal(
    <div
      className={kind === "settings" ? "scrim scrim-settings" : "scrim"}
      onPointerDown={(e) => {
        if (e.target === e.currentTarget && isTop()) onClose();
      }}
    >
      <div ref={ref} className={`dialog glass ${kind === "wide" ? "dialog-wide" : kind === "settings" ? "dialog-settings" : ""}`} role="dialog" aria-modal="true" aria-labelledby={titleId} onKeyDown={trap}>
        <h2 id={titleId} className="mb-3 text-[15px] font-semibold">
          {title}
        </h2>
        {children}
        <div className="mt-3 flex justify-end gap-2">{actions}</div>
      </div>
    </div>,
    document.body,
  );
}
